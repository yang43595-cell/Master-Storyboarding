"""Stage routing uses existing, exact production records, with no platform calls."""
import json
from pathlib import Path

import pytest

from scripts import canvas_readback, stage_context_cli
from scripts.core import artifacts, production_handoff
from scripts.core.agent_acceptance import register_workflow_authorization
from scripts.core.io import read_json, write_json
from scripts.core.asset_batch_receipt import BATCH_CARD_PREFIX
from scripts.core.project_kickoff import REQUEST_PATH
from test_formal_package_submission import confirmed_narrative, narrative, register, video_project
from test_visual_readiness import write_request
from test_workflow_repair_cli import redframe_fixture


def steps(project, action):
    return [row for row in production_handoff.resolve_production_handoff(project)['steps']
            if row['action'] == action]


def kickoff(project):
    write_json(project / REQUEST_PATH, {'kickoff': {
        'decision_ref': 'synthetic kickoff authorization', 'product': {'mode': 'none'},
        'canvas': {'platform': 'redframe', 'model': 'Seedance 2.5', 'aspect_ratio': '9:16',
                   'resolution': '480p', 'target_url': 'https://fixture.invalid/canvas'},
        'image_generation': {'provider': 'redframe', 'model': 'synthetic image model',
                             'fee_scope': 'existing_quota', 'reference_upload_authorized': True,
                             'authorization_ref': 'synthetic exact scope', 'scope': 'required_asset_outputs'},
    }})


def authorize(project, *skills):
    register_workflow_authorization(project, authorization_id='AUTH-1',
                                    authorization_ref='synthetic explicit authorization', skills=list(skills))


def test_candidate_waits_then_adoption_unlocks_independent_actions(tmp_path):
    kickoff(tmp_path)
    assert steps(tmp_path, 'creative')[0]['status'] == 'ready'
    entry = register(tmp_path, 'N', 'narrative.package', narrative(tmp_path))
    assert steps(tmp_path, 'creative-review')[0]['status'] == 'waiting'
    assert not steps(tmp_path, 'director')
    assert not steps(tmp_path, 'assets')

    decision = tmp_path / 'decision.json'
    write_json(decision, {'scope': 'artifact', 'quote': '编导稿 R01 整稿通过',
                          'at': '2026-11-22T10:00:00+08:00', 'review_ref': 'synthetic exact approval'})
    assert stage_context_cli.main([
        'confirm-script-in-chat', '--project', str(tmp_path), '--artifact-id', 'N', '--path', 'N.json',
        '--revision', entry['revision'], '--sha256', artifacts.digest(tmp_path / 'N.json'),
        '--confirmation-json', str(decision),
    ]) == 0
    authorize(tmp_path, 'storyboard-planner')
    director = production_handoff.require_production_handoff(tmp_path, 'director')
    assert director['artifact_ids'] == ['N']
    assert steps(tmp_path, 'assets')[0]['status'] == 'ready'
    assert not steps(tmp_path, 'video')


def test_benchmark_workflow_is_selected_after_narrative_adoption(tmp_path):
    kickoff(tmp_path)
    path = tmp_path / REQUEST_PATH
    request = read_json(path)
    creation = request['kickoff']['creation'] = {'workflow': 'pending'}
    write_json(path, request)
    args = ['handoff', '--project', str(tmp_path)]
    first = stage_context_cli.handoff(stage_context_cli.parser().parse_args(args))
    assert first['workflow'] == 'pending'
    creative, = first['next_actions']
    assert creative['skill'] == 'creative-director'
    assert '还原原片剧情、对白' in creative['reason']
    assert 'workflow_entry' not in creative

    confirmed_narrative(tmp_path)
    authorize(tmp_path, 'storyboard-planner')
    pending = stage_context_cli.handoff(stage_context_cli.parser().parse_args(args))
    assert pending['next_actions'] == []
    assert steps(tmp_path, 'director')[0]['status'] == 'waiting'

    creation['workflow'] = 'depth-guided-recreation'
    write_json(path, request)
    flow = stage_context_cli.handoff(stage_context_cli.parser().parse_args(args))
    director = next(row for row in flow['next_actions'] if row['action'] == 'director')
    assert flow['workflow'] == director['workflow'] == 'depth-guided-recreation'
    assert director['artifact_ids'] == ['N']
    assert director['workflow_entry'] in Path(director['handoff_path']).read_text(encoding='utf-8')
    method = stage_context_cli.ROOT / 'skills/depth-guided-recreation/modules/recreation-method.md'
    documents = stage_context_cli._local_standard_documents(
        stage_context_cli.ROOT / 'skills/storyboard-planner', request,
    )
    assert method in documents
    prepare = ['prepare', '--project', str(tmp_path), '--skill', 'storyboard-planner',
               '--objective', '对齐原片拍法', '--context-id', director['context_id']]
    result = stage_context_cli.prepare(stage_context_cli.parser().parse_args(prepare))
    assert result['valid']
    startup = Path(result['path'])
    assert str(method) in startup.read_text(encoding='utf-8')
    basis = read_json(startup.with_name('task_basis.json'))
    assert {'path': str(method), 'sha256': artifacts.digest(method)} in basis['standards']['local_documents']

    creation['workflow'] = 'creative'
    write_json(path, request)
    creative_director = production_handoff.require_production_handoff(tmp_path, 'director')
    assert creative_director['artifact_ids'] == director['artifact_ids']
    assert 'workflow_entry' not in creative_director
    assert method not in stage_context_cli._local_standard_documents(
        stage_context_cli.ROOT / 'skills/storyboard-planner', request,
    )
    with pytest.raises(ValueError, match='制作路线已变化'):
        stage_context_cli.prepare(stage_context_cli.parser().parse_args(prepare))


def test_no_supplement_allows_only_the_shot_with_confirmed_dependencies(tmp_path):
    video_project(tmp_path, current_prompt=False)
    kickoff(tmp_path)
    authorize(tmp_path, 'voiceover-to-video-script')
    path = tmp_path / 'B.json'
    board = read_json(path)
    board['scenes'][1]['shots'][0]['source_asset_requirement_ids'] = []
    write_json(path, board)
    manifest = artifacts.load_manifest(tmp_path)
    entry = next(row for row in manifest['artifacts'] if row['artifact_id'] == 'B')
    entry['checksum'] = artifacts.digest(path)
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)

    # S02 has no asset blocker. The missing stage alone must stop it.
    args = ['handoff', '--project', str(tmp_path)]
    handoff = stage_context_cli.handoff(stage_context_cli.parser().parse_args(args))
    assert any(row['skill'] == 'storyboard-director' and row['shot_ids'] == ['S02-010']
               for row in handoff['next_actions'])
    assert all(row['skill'] != 'voiceover-to-video-script' for row in handoff['next_actions'])
    with pytest.raises(ValueError, match='视频或画布交接当前未就绪'):
        stage_context_cli.handoff(stage_context_cli.parser().parse_args([
            *args, '--to-skill', 'voiceover-to-video-script',
        ]))
    with pytest.raises(ValueError, match='缺本版视觉检查结论'):
        production_handoff.require_handoff_visual_inputs(tmp_path, board, entry, ['S02-010'])

    write_request(tmp_path, entry, ['S01-010', 'S02-010'], decision='no_supplement')

    video = steps(tmp_path, 'video')
    assert [(row['status'], row['shot_ids']) for row in video] == [
        ('ready', ['S02-010']), ('waiting', ['S01-010']),
    ]
    assert 'AST-CHAR-A' in video[1]['blockers']['S01-010'][0]
    with pytest.raises(ValueError, match='超出已就绪'):
        production_handoff.require_production_handoff(tmp_path, 'video', shot_ids=['S01-010'])
    assert production_handoff.require_handoff_visual_inputs(
        tmp_path, board, entry, ['S02-010'])['reviewed_shot_ids'] == ['S02-010']


def test_finished_unapproved_image_batch_waits_for_review_without_redispatch(tmp_path):
    _, narrative = confirmed_narrative(tmp_path)
    kickoff(tmp_path)
    request = tmp_path / 'asset-request.json'
    target = 'AST-CHAR-A@main'
    write_json(request, {'image_targets': [target]})
    receipt = tmp_path / 'asset-receipt.json'
    batch = {'context_id': 'asset-a', 'request': request.name, 'request_sha256': artifacts.digest(request),
             'receipt': receipt.name, 'targets': [target]}
    path = tmp_path / '00_项目管理/执行上下文/批次' / batch['context_id'] / 'next_stage_handoff.md'
    path.parent.mkdir(parents=True)
    path.write_text('\n'.join([
        '- 目标 Skill：`asset-image-assistant`', '- artifact_id：`N`',
        '  - logical_id：`narrative.package`', f'  - revision：`{narrative["revision"]}`',
        f'  - sha256：`{narrative["checksum"]}`',
        f'{BATCH_CARD_PREFIX}{json.dumps(batch, ensure_ascii=False)}`',
    ]), encoding='utf-8')
    assert [row['status'] for row in steps(tmp_path, 'assets')] == ['waiting']

    image = tmp_path / 'synthetic.png'
    image.write_bytes(b'synthetic content used only for custody/hash validation')
    write_json(receipt, {'context_id': batch['context_id'], 'request_sha256': batch['request_sha256'], 'items': [{
        'target': target, 'candidate_id': 'image-a', 'observer': 'fixture', 'visual_status': 'passed',
        'observations': 'Synthetic observation', 'path': image.name, 'sha256': artifacts.digest(image),
    }]})
    assert not steps(tmp_path, 'assets')
    assert steps(tmp_path, 'asset-review') == [{
        'action': 'asset-review', 'status': 'waiting',
        'reason': '候选已完成实看，沿原目标等待准确图片确认及登记',
        'output_ids': [target], 'receipt_paths': [str(receipt)],
    }]
    image.write_bytes(b'changed bytes')
    assert not steps(tmp_path, 'asset-review')
    assert [row['status'] for row in steps(tmp_path, 'assets')] == ['waiting']


def test_canvas_resume_counts_only_exact_formal_readback(tmp_path):
    frozen, observed = redframe_fixture(tmp_path)
    entry = next(entry for entry in artifacts.load_manifest(tmp_path)['artifacts']
                 if entry['artifact_id'] == 'VIDEO')
    canvas = read_json(tmp_path / REQUEST_PATH)['kickoff']['canvas']
    from scripts.core.draft_delivery import read_draft_delivery
    segment_ids = {row['source_segment_id'] for row in frozen['drafts']}
    record = tmp_path / '06_视频/回读'
    write_json(record / 'frozen.json', frozen)
    write_json(record / 'observed.json', observed)
    result = read_draft_delivery(tmp_path, canvas, entry, segment_ids)
    assert set(result) == segment_ids and all(row['status'] == 'resume' for row in result.values())

    report = canvas_readback.compare(frozen, observed, tmp_path, formal_delivery=True)
    assert report['ok']
    write_json(record / 'formal_readback.json', report)
    result = read_draft_delivery(tmp_path, canvas, entry, segment_ids)
    assert set(result) == segment_ids and all(row['status'] == 'complete' for row in result.values())
    changed = {**entry, 'checksum': '0' * 64}
    assert read_draft_delivery(tmp_path, canvas, changed, segment_ids) == {}
    observed['drafts'][0]['prompt'] += ' drift'
    write_json(record / 'observed.json', observed)
    result = read_draft_delivery(tmp_path, canvas, entry, segment_ids)
    assert set(result) == segment_ids and all(row['status'] == 'resume' for row in result.values())
