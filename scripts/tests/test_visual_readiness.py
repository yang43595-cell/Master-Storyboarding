"""The video entry point consumes the existing shot-scoped supplement decisions."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from scripts import canvas_readback
from scripts.core import artifacts, generation_boundaries, quality_evidence
from scripts.core.io import read_json, write_json
from scripts.core.project_state import load_project_state, write_project_state
from scripts.core.visual_readiness import require_visual_readiness
from test_formal_package_submission import register, video_project
from test_quality_evidence_consumers import agent_fixture


def write_request(root, board, shots, *, request_id='VS-LAYOUT', decision='request_supplement',
                  references=None, relationship=None):
    path = root / '05_分镜' / f'视觉补充请求_{request_id}.json'
    request = {
        'schema_version': '1.0',
        'project_id': 'MSB-TEST',
        'request_id': request_id,
        'status': 'ready',
        'source_storyboard': {
            'artifact_id': board['artifact_id'], 'revision': board['revision'],
            'path': board['path_or_url'], 'logical_id': 'storyboard.package',
            'status': 'confirmed' if board['user_approved'] else 'agent_accepted',
        },
        'source_shot_ids': shots,
        'decision': decision,
        'return_upstream': {'required': False, 'target': 'none', 'reason': ''},
    }
    if decision != 'no_supplement':
        request.update(target_state='女二靠椅背，靠窗桌在左侧', must_appear=['女二、座椅、桌位'],
                       must_not_appear=[], preserve=['窗墙与桌位'], existing_references=references or [])
    if decision == 'request_supplement':
        request['max_images'] = 1
        request['gap'] = {'type': 'space', 'observed_evidence': '现有空景看不到座位人物',
                          'why_existing_is_insufficient': '没有画面同时观察到人与窗下座椅',
                          'downstream_consequence': '换机位后人物座位可能漂移'}
    else:
        request.update(status='not_needed', max_images=0, visual_review={
            'storyboard_sha256': board['checksum'],
            'observation': '合成测试记录：本镜的场景和位置已核对',
        })
    if relationship is not None:
        request['relationship_reference'] = relationship
    write_json(path, request)
    return path


def test_write_entry_checks_required_visuals_only_for_selected_shots(tmp_path, capsys):
    video_project(tmp_path, current_prompt=False)
    manifest = artifacts.load_manifest(tmp_path)
    board = next(entry for entry in manifest['artifacts'] if entry['logical_id'] == 'storyboard.package')
    path = tmp_path / board['path_or_url']
    payload = read_json(path)
    payload['scenes'][0]['shots'][0]['required_visuals'] = [
        {'prompt_clause': '她松开放在桌上的手。', 'min_seconds': 1},
        {'prompt_clause': '物品留在桌面。', 'min_seconds': 2},
    ]
    write_json(path, payload)
    board['checksum'] = artifacts.digest(path)
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    write_request(tmp_path, board, ['S02-010'], decision='no_supplement')
    args = ['--project', str(tmp_path), '--storyboard', board['path_or_url'], '--shot-id']
    assert generation_boundaries.main([*args, 'S01-010']) == 1
    assert 'required_visuals: S01-010' in json.loads(capsys.readouterr().out)['error']
    assert generation_boundaries.main([*args, 'S02-010']) == 0
    assert json.loads(capsys.readouterr().out)['reviewed_shot_ids'] == ['S02-010']


def test_write_entry_and_formal_adoption_block_pending_request_by_shot(tmp_path, capsys):
    payload = video_project(tmp_path, current_prompt=False)
    manifest = artifacts.load_manifest(tmp_path)
    board = next(entry for entry in manifest['artifacts'] if entry['logical_id'] == 'storyboard.package')
    path = write_request(tmp_path, board, ['S01-010'])
    args = ['--project', str(tmp_path), '--storyboard', board['path_or_url'], '--shot-id']
    assert generation_boundaries.main([*args, 'S01-010']) == 1
    assert 'VS-LAYOUT（S01-010）' in json.loads(capsys.readouterr().out)['error']
    assert generation_boundaries.main([*args, 'S02-010']) == 1
    assert '缺本版视觉检查结论：S02-010' in json.loads(capsys.readouterr().out)['error']

    ordinary = write_request(tmp_path, board, ['S02-010'], request_id='VS-ORDINARY',
                             decision='no_supplement')
    assert generation_boundaries.main([*args, 'S02-010']) == 0
    assert json.loads(capsys.readouterr().out)['reviewed_shot_ids'] == ['S02-010']
    assert generation_boundaries.main([*args, 'S02-010', '--shot-id', 'S01-010']) == 1
    error = json.loads(capsys.readouterr().out)['error']
    assert '缺本版视觉检查结论：S01-010；' in error
    assert 'S02-010' not in error

    receipt = read_json(ordinary)
    receipt['visual_review']['storyboard_sha256'] = '0' * 64
    write_json(ordinary, receipt)
    assert generation_boundaries.main([*args, 'S02-010']) == 1
    assert '视觉检查记录对应的导演稿字节已变' in json.loads(capsys.readouterr().out)['error']
    receipt['visual_review']['storyboard_sha256'] = board['checksum']
    write_json(ordinary, receipt)

    candidate = register(tmp_path, 'VIDEO', 'video.prompt', payload, ['storyboard.package'])
    with pytest.raises(ValueError, match='VS-LAYOUT.*缺已采用'):
        artifacts.validate_candidate_submission(tmp_path, candidate)
    assert artifacts.validate_candidate_submission(tmp_path, candidate, reading_existing=True)

    request = json.loads(path.read_text())
    request['status'] = 'superseded'
    write_json(path, request)
    assert artifacts.validate_candidate_submission(tmp_path, candidate)


def test_accepted_supplement_must_be_the_bound_reference(tmp_path):
    def use_request(_frozen, image):
        board = next(entry for entry in artifacts.load_manifest(tmp_path)['artifacts']
                     if entry['logical_id'] == 'storyboard.package')
        request_path = write_request(tmp_path, board, ['S01-010'])
        image['quality_evidence']['source_files'].append(
            {'path': str(request_path), 'sha256': artifacts.digest(request_path)})

    frozen, _ = agent_fixture(tmp_path, image_setup=use_request)
    assert canvas_readback.validate_submission(frozen, tmp_path)['ok']
    frozen['drafts'][0]['reference_checks'] = []
    frozen['drafts'][0]['ordered_media'] = []
    with pytest.raises(ValueError, match='VS-LAYOUT.*缺已采用'):
        canvas_readback.validate_submission(frozen, tmp_path)


def test_reuse_existing_relationship_image_does_not_require_new_supplement(tmp_path):
    def observe_layout(frozen, image):
        row = frozen['drafts'][0]
        ref = row['reference_checks'][0]
        ref['roles'] = ['layout']
        image['quality_evidence']['uses'] = [quality_evidence.image_use(ref, ['S01-010'])]
        row['required_reference_roles'] = {'E1': ['layout']}
        row['quality_evidence']['checks']['persistent_objects'].update(
            scene_state_assessment='unchanged', source_shot_ids=['S01-010'])

    frozen, image = agent_fixture(tmp_path, image_setup=observe_layout)
    board = next(entry for entry in artifacts.load_manifest(tmp_path)['artifacts']
                 if entry['logical_id'] == 'storyboard.package')
    storyboard = read_json(tmp_path / board['path_or_url'])
    request = write_request(
        tmp_path, board, ['S01-010'], decision='reuse_existing',
        references=[
            {'kind': 'other', 'ref_id': 'PORTRAIT', 'path': 'portrait.png', 'coverage': '人物身份'},
            {'kind': 'confirmed_supplement', 'ref_id': 'IMG', 'path': image['path_or_url'],
             'coverage': '固定窗下座椅与人物'},
        ],
        relationship={'ref_id': 'IMG', 'sha256': image['checksum']},
    )
    assert require_visual_readiness(tmp_path, storyboard, board, ['S01-010'],
                                    require_review=True)['request_ids'] == ['VS-LAYOUT']
    assert canvas_readback.validate_submission(frozen, tmp_path)['ok']

    for field, value in [('artifact_id', 'PORTRAIT'), ('roles', ['identity']),
                         ('canvas_binding', {'prompt_reference': False})]:
        changed_rows = deepcopy(frozen['drafts'])
        changed_rows[0]['reference_checks'][0][field] = value
        with pytest.raises(ValueError, match='VS-LAYOUT.*既有关系图 IMG 或空间用途'):
            require_visual_readiness(tmp_path, storyboard, board, ['S01-010'],
                                     rows=changed_rows, require_review=True)

    stale = read_json(request)
    stale['relationship_reference']['sha256'] = '0' * 64
    write_json(request, stale)
    with pytest.raises(ValueError, match='关系图路径或图片字节与所选版本不一致'):
        require_visual_readiness(tmp_path, storyboard, board, ['S01-010'], require_review=True)


@pytest.mark.parametrize(('scenario', 'error'), [
    ('local_revision', None),
    ('changed_segment_pending', 'VS-LAYOUT.*缺已采用'),
    ('new_global_scope', 'VS-LAYOUT.*缺已采用'),
    ('invalid_baseline_confirmation', 'VS-LAYOUT.*缺已采用'),
    ('changed_baseline_bytes', 'VS-LAYOUT.*缺已采用'),
    ('unrelated_missing_input', '视频稿输入不可用.*missing.png'),
])
def test_adopting_video_revision_rechecks_only_changed_visual_scope(tmp_path, scenario, error):
    baseline = video_project(tmp_path)
    manifest = artifacts.load_manifest(tmp_path)
    board = next(entry for entry in manifest['artifacts'] if entry['logical_id'] == 'storyboard.package')
    existing = next(entry for entry in manifest['artifacts'] if entry['artifact_id'] == 'P-OLD')
    baseline['source_storyboard']['sha256'] = board['checksum']
    baseline['scope'] = {'type': 'full_local_authored'}
    if scenario == 'unrelated_missing_input':
        baseline['generation_segments'][0]['materials'] = [{'path': 'missing.png'}]
    old_path = tmp_path / existing['path_or_url']
    write_json(old_path, baseline)
    existing['checksum'] = artifacts.digest(old_path)
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)

    changed = deepcopy(baseline)
    changed['revision'] = 'R02'
    revised = changed['generation_segments'][1]
    before_prompt = revised['prompt']
    movement = revised['director_coverage'][0]['movement']
    revised_movement = '机位保持眼高，固定中近景'
    revised['prompt'] = before_prompt.replace(movement['prompt_quote'], revised_movement, 1)
    movement['prompt_quote'] = revised_movement
    changed['segment_reconciliation'] = {
        'baseline': {'artifact_id': existing['artifact_id'], 'sha256': existing['checksum']},
        'changes': [{'action': 'revise', 'segment_id': revised['segment_id'], 'reason': 'Rephrase the fixed camera instruction.',
                     'before_sha256': sha256(before_prompt.encode()).hexdigest(),
                     'after_sha256': sha256(revised['prompt'].encode()).hexdigest()}],
    }
    if scenario == 'new_global_scope':
        changed['scope']['note'] = 'Different overall scope.'
    if scenario == 'invalid_baseline_confirmation':
        state = load_project_state(tmp_path)
        state['user_confirmations'][0]['artifact_ids'].remove(existing['artifact_id'])
        write_project_state(tmp_path, state)
    if scenario == 'changed_baseline_bytes':
        baseline['document'] = 'Drifted source baseline.'
        write_json(old_path, baseline)
    write_request(tmp_path, board, ['S02-010' if scenario == 'changed_segment_pending' else 'S01-010'])
    candidate = register(tmp_path, 'P-NEW', 'video.prompt', changed, ['storyboard.package'])

    if error:
        with pytest.raises(ValueError, match=error):
            artifacts.validate_candidate_submission(tmp_path, candidate)
    else:
        assert artifacts.validate_candidate_submission(tmp_path, candidate) == artifacts.digest(tmp_path / 'P-NEW.json')
