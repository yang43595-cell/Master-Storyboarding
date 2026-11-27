"""Negative replays: identity boards, stale PASS and matching graph edges are insufficient."""
import copy
import json
import re
import subprocess
import shutil
from pathlib import Path

import pytest
from PIL import Image

from scripts.canvas_readback import compare, digest, validate_submission
from scripts.core import artifacts, revision_control as rc
from scripts.core.io import write_json, read_json
from scripts.core.project_state import new_project_state, write_project_state
from scripts.tests.test_doc_reference_integrity import _slug
from scripts.tests.test_review_receipt_binding import pending


def delivery(root):
    # Tiny synthetic local fixtures; no customer content or model execution.
    Image.new('RGB', (8, 8), 'blue').save(root / 'frame.png')
    (root / 'director.json').write_text('{"fixture":"director"}')
    refs = {}
    entries = []
    for ident, name in [('FRAME', 'frame.png'), ('DIRECTOR', 'director.json')]:
        refs[ident] = {'artifact_id': ident, 'revision': 'R01', 'sha256': artifacts.digest(root / name)}
        entries.append({**refs[ident], 'logical_id': ident, 'status': 'current',
                        'user_approved': True, 'path_or_url': name, 'checksum': refs[ident]['sha256'],
                        'upload_bindings': [{'target_url': 'https://fixture.invalid/exact',
                            'node_id': 'frame-node', 'media_id': 'frame-media',
                            'revision': 'R01', 'sha256': refs[ident]['sha256']}] if ident == 'FRAME' else []})
    write_json(root / artifacts.MANIFEST_PATH, {'artifacts': entries})
    state = new_project_state()
    state.update(current_artifacts=list(refs), user_confirmations=[{'artifact_ids': list(refs)}])
    write_project_state(root, state)
    def row(ident, previous=None):
        return {'node_id': ident, 'prompt': 'Synthetic actor holds the item.',
                'parameters': {'duration': 4}, 'duration_bounds': [2, 8], 'shot_durations': [1, 3],
                'native_entity_type': 'fixture-chip', 'required_entities': ['ACTOR'],
                'required_reference_roles': {'ACTOR': ['start_frame' if previous else 'end_frame']},
                'ordered_media': [{'node_id': 'frame-node', 'media_id': 'frame-media'}],
                'reference_checks': [{**refs['FRAME'], 'kind': 'image',
                                      'role': 'start_frame' if previous else 'end_frame',
                                      'state_id': 'handoff', 'entity_ids': ['ACTOR']}],
                'continuity': {'event_id': 'one-event', **({'previous_node_id': previous} if previous else {})}}
    frozen = {'target_url': 'https://fixture.invalid/exact', 'revision': 'R02',
              'source_artifacts': [refs['DIRECTOR']], 'drafts': [row('one'), row('two', 'one')]}
    bind_synthetic_quality(root, frozen)
    observed = copy.deepcopy(frozen)
    observed.update(phase='after_refresh', frozen_sha256=digest(frozen))
    for row in observed['drafts']:
        row['native_mentions'] = [{**m, 'entity_type': 'fixture-chip', 'native_noneditable_entity': True}
                                  for m in row['ordered_media']]
    return frozen, observed



@pytest.mark.parametrize('prompt,expected_ok', [
    ('从 @视频1 转到 @图片2，再听 @音频1。', True),
    ('从 @视频1 转到 @图片1，再听 @音频1。', False),
    ('从 @图片2 转到 @视频1，再听 @音频1。', False),
])
def test_planned_redframe_labels_keep_media_kind_numbering_and_order(prompt, expected_ok):
    from scripts.canvas_readback import validate_canvas_inputs

    refs = [
        dict(kind=kind, sha256=str(slot) * 64, canvas_binding=dict(
            mode='direct', purpose=f'synthetic slot {slot}', prompt_reference=mentioned,
            **({} if mentioned else {'unmentioned_reason': 'layout support'})))
        for slot, (kind, mentioned) in enumerate(
            [('image', False), ('video', True), ('image', True), ('audio', True)], 1)
    ]
    row = dict(node_id='fixture', prompt=prompt, reference_checks=refs, realistic_face_slots=[],
               edges=[], source_nodes=[], shot_durations=[4], parameters={'duration': 4})
    if expected_ok:
        validate_canvas_inputs(row, redframe=True, before_upload=True)
    else:
        with pytest.raises(ValueError, match='正文引用须按有序素材槽逐项对应'):
            validate_canvas_inputs(row, redframe=True, before_upload=True)


def test_planned_redframe_labels_accept_text_only_and_explicitly_unmentioned_slots():
    from scripts.canvas_readback import validate_canvas_inputs

    row = dict(node_id='fixture', prompt='镜头拉远。', reference_checks=[], realistic_face_slots=[],
               edges=[], source_nodes=[], shot_durations=[4], parameters={'duration': 4})
    validate_canvas_inputs(row, redframe=True, before_upload=True)
    row['reference_checks'] = [dict(kind='image', sha256='0' * 64, canvas_binding=dict(
        mode='direct', purpose='layout support', prompt_reference=False, unmentioned_reason='not a prompt chip'))]
    validate_canvas_inputs(row, redframe=True, before_upload=True)


def bind_synthetic_quality(root, frozen):
    """Complete byte/use-bound synthetic observations, never real approval."""
    from scripts.core import quality_evidence as q
    from scripts.tests.test_quality_evidence_consumers import observations
    manifest = artifacts.load_manifest(root)
    images = {}
    for row in frozen['drafts']:
        row.setdefault('source_shot_ids', ['SYNTHETIC-SHOT'])
        for ref in row['reference_checks']:
            if ref.get('kind') != 'image' or 'product_reference_id' in ref:
                continue
            item = next(a for a in manifest['artifacts'] if a['artifact_id'] == ref['artifact_id'])
            if item['artifact_id'] not in images:
                evidence = observations(q.IMAGE_ASPECTS)
                evidence.update(image_sha256=item['checksum'],
                    source_files=[{'path': 'director.json', 'sha256': artifacts.digest(root / 'director.json')}],
                    source_shot_ids=[], view={'resolution': 'original', 'locator': 'synthetic pixels'}, uses=[])
                images[item['artifact_id']] = evidence
                item['quality_evidence'] = evidence
            evidence = images[item['artifact_id']]
            evidence['source_shot_ids'] = sorted(set(evidence['source_shot_ids'] + row['source_shot_ids']))
            evidence['uses'].append(q.image_use(ref, row['source_shot_ids']))
        row['quality_evidence'] = observations(q.DRAFT_ASPECTS)
        row['quality_evidence']['checks']['packaging_identity'].update(scope='none', items=[], result='not_applicable')
        row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))
    write_json(root / artifacts.MANIFEST_PATH, manifest)


def bind_synthetic_canvas(root, frozen, observed):
    """Add declared graph fixtures, never a real platform receipt."""
    for row in frozen['drafts']:
        row['edges'], row['source_nodes'] = [], []
        for slot, (media, ref) in enumerate(zip(row['ordered_media'], row['reference_checks']), 1):
            ref['canvas_binding'] = dict(mode='edge', purpose='synthetic boundary', prompt_reference=True,
                                         source_port='image', target_port=f'input-{slot}')
            row['edges'].append(dict(source_node_id=media['node_id'], source_port='image',
                                     target_node_id=row['node_id'], target_port=f'input-{slot}', slot=slot))
            source = dict(node_id=media['node_id'], port='image', sha256=ref['sha256'])
            if source not in row['source_nodes']:
                row['source_nodes'].append(source)
    bind_synthetic_quality(root, frozen)
    observed['drafts'] = copy.deepcopy(frozen['drafts'])
    for row in observed['drafts']:
        row['native_mentions'] = [{**media, 'entity_type': row.get('native_entity_type'),
                                    'native_noneditable_entity': True} for media in row['ordered_media']]
    observed['frozen_sha256'] = digest(frozen)


@pytest.mark.parametrize('declare_on_source', [False, True])
def test_delivery_external_scope_comes_from_exact_adopted_source(tmp_path, declare_on_source):
    frozen, _ = delivery(tmp_path)
    external = tmp_path.parent / (tmp_path.name + '-selected-inputs')
    external.mkdir()
    evidence = external / 'original.txt'; evidence.write_text('selected original')
    source = {'fixture': 'director', 'evidence_path': str(evidence)}
    if declare_on_source:
        source['input_roots'] = [str(external)]
    else:
        frozen['input_roots'] = [str(external)]  # A frozen claim cannot widen scope.
    write_json(tmp_path / 'director.json', source)
    manifest = artifacts.load_manifest(tmp_path)
    entry = next(item for item in manifest['artifacts'] if item['artifact_id'] == 'DIRECTOR')
    entry['checksum'] = artifacts.digest(tmp_path / 'director.json')
    frozen['source_artifacts'][0]['sha256'] = entry['checksum']
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    bind_synthetic_quality(tmp_path, frozen)
    if declare_on_source:
        validate_submission(frozen, tmp_path, preflight=True)
        frozen['source_artifacts'][0]['sha256'] = '0' * 64
        with pytest.raises(ValueError, match='file/hash'):
            validate_submission(frozen, tmp_path, preflight=True)
    else:
        with pytest.raises(ValueError, match='Dependency readiness failed'):
            validate_submission(frozen, tmp_path, preflight=True)


def test_full_reference_and_native_readback_then_mutation_fails(tmp_path):
    frozen, observed = delivery(tmp_path)
    assert compare(frozen, observed, tmp_path)['ok']
    observed['drafts'][1]['native_mentions'][0]['native_noneditable_entity'] = False
    assert not compare(frozen, observed, tmp_path)['ok']
    with pytest.raises(ValueError, match='--project'):
        compare(frozen, observed)


def test_repeated_source_digest_is_scoped_to_one_delivery(tmp_path, monkeypatch):
    frozen, observed = delivery(tmp_path)
    frame = tmp_path / 'frame.png'
    reads = []
    read_digest = artifacts.digest

    def count_digest(path):
        if path == frame:
            reads.append(path)
        return read_digest(path)

    monkeypatch.setattr(artifacts, 'digest', count_digest)
    assert compare(frozen, observed, tmp_path)['ok']
    assert reads == [frame]

    wrong = copy.deepcopy(frozen)
    wrong['drafts'][1]['reference_checks'][0]['sha256'] = '0' * 64
    bind_synthetic_quality(tmp_path, wrong)
    with pytest.raises(ValueError, match='reference file/hash'):
        validate_submission(wrong, tmp_path, preflight=True)

    frame.write_bytes(b'changed after a successful validation')
    with pytest.raises(ValueError, match='reference file/hash'):
        compare(frozen, observed, tmp_path)


@pytest.mark.parametrize('mutation', ['prompt', 'native_media', 'role', 'action_still', 'entity', 'missing_entity', 'missing_ref',
    'missing_role', 'duration', 'nan', 'bool_duration', 'timeline', 'boundary', 'predecessor', 'stale_source',
    'stale_reference', 'missing_file', 'unconfirmed'])
def test_reference_duration_and_boundary_negatives(tmp_path, mutation):
    frozen, _ = delivery(tmp_path)
    row = frozen['drafts'][1]
    ref = row['reference_checks'][0]
    if mutation == 'prompt': row['prompt'] = ''
    elif mutation == 'native_media': row['ordered_media'][0].pop('media_id')
    elif mutation == 'role': ref['role'] = 'unassigned'
    elif mutation == 'action_still': ref['role'] = 'action'
    elif mutation == 'entity': ref['entity_ids'] = ['OTHER']
    elif mutation == 'missing_role': row['required_reference_roles']['ACTOR'].append('action')
    elif mutation == 'missing_entity': row['required_entities'].append('MISSING')
    elif mutation == 'missing_ref': row['reference_checks'] = []
    elif mutation == 'duration': row['parameters']['duration'] = 100
    elif mutation == 'nan': row['parameters']['duration'] = float('nan')
    elif mutation == 'bool_duration': row['parameters']['duration'] = True
    elif mutation == 'timeline': row['shot_durations'] = [1, 1]
    elif mutation == 'boundary': ref['state_id'] = 'new-state'
    elif mutation == 'predecessor': row['continuity']['previous_node_id'] = 'unrelated'
    elif mutation == 'stale_source': frozen['source_artifacts'][0]['revision'] = 'R00'
    elif mutation == 'stale_reference': ref['sha256'] = '0' * 64
    elif mutation == 'missing_file': (tmp_path / 'frame.png').unlink()
    elif mutation == 'unconfirmed':
        state = new_project_state(); write_project_state(tmp_path, state)
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local video tools unavailable')
def test_action_reference_checks_actual_video_duration(tmp_path):
    frozen, _ = delivery(tmp_path)
    video = tmp_path / 'action.mov'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=16x16:r=10',
                    '-t', '1', '-c:v', 'png', '-pix_fmt', 'rgb24', str(video)], check=True, capture_output=True)
    manifest = artifacts.load_manifest(tmp_path)
    item = {'artifact_id': 'ACTION', 'logical_id': 'ACTION', 'revision': 'R01',
            'status': 'current', 'user_approved': True, 'path_or_url': video.name, 'checksum': artifacts.digest(video),
            'upload_bindings': [{'target_url': frozen['target_url'], 'node_id': 'video-node',
                                'media_id': 'video-media', 'revision': 'R01', 'sha256': artifacts.digest(video)}]}
    manifest['artifacts'].append(item); write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    state = read_json(tmp_path / '00_项目管理/project_state.json')
    state['current_artifacts'].append('ACTION'); state['user_confirmations'].append({'artifact_ids': ['ACTION']})
    write_project_state(tmp_path, state)
    row = frozen['drafts'][0]
    row['ordered_media'].append({'node_id': 'video-node', 'media_id': 'video-media'})
    ref = {'artifact_id': 'ACTION', 'revision': 'R01', 'sha256': item['checksum'],
           'role': 'action', 'kind': 'video', 'entity_ids': ['ACTOR'], 'duration': 1}
    row['reference_checks'].append(ref)
    bind_synthetic_quality(tmp_path, frozen)
    assert validate_submission(frozen, tmp_path)['ok']
    ref['duration'] = 2
    # Recheck this synthetic revision so actual media duration remains the target gate.
    bind_synthetic_quality(tmp_path, frozen)
    with pytest.raises(ValueError, match='actual video'):
        validate_submission(frozen, tmp_path)


def test_delivery_pass_must_match_snapshot_and_current_registered_inputs(tmp_path):
    candidate = pending(tmp_path)
    # Preserve the active change manifest; register synthetic sources before opening another change.
    rc.cancel_change(tmp_path, change_id='FIX', reason='fixture setup')
    frozen, observed = delivery(tmp_path)
    state = read_json(tmp_path / '00_项目管理/project_state.json')
    manifest = artifacts.load_manifest(tmp_path)
    old = tmp_path / 'old.json'; old.write_text('{}')
    manifest['artifacts'].append({'artifact_id': 'OLD', 'logical_id': 'video.prompt', 'revision': 'R01',
        'status': 'current', 'user_approved': True, 'path_or_url': old.name, 'checksum': artifacts.digest(old)})
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    state['current_artifacts'].append('OLD'); state['user_confirmations'].append({'artifact_ids': ['OLD']})
    write_project_state(tmp_path, state)
    rc.open_change(tmp_path, change_id='DELIVERY', tier='T1', logical_ids=['video.prompt'], reason='fixture')
    rc.confirm_impact(tmp_path, change_id='DELIVERY', approval_ref='fixture:impact')
    write_json(candidate, frozen)
    rc.record_candidate(tmp_path, change_id='DELIVERY', artifact_id='NEW', logical_id='video.prompt',
                        artifact_type='json', candidate_path=candidate.name, revision='R02')
    report = compare(frozen, observed, tmp_path)
    write_json(tmp_path / 'report.json', {**report, 'revision': 'R01'})
    with pytest.raises(rc.RevisionControlError, match='旧 PASS'):
        rc.record_review(tmp_path, change_id='DELIVERY', verdict='approved', report_ref='report.json')
    write_json(tmp_path / 'report.json', report)
    rc.record_review(tmp_path, change_id='DELIVERY', verdict='approved', report_ref='report.json')
    # Candidate bytes unchanged, but an upstream reference is changed after PASS.
    (tmp_path / 'frame.png').write_bytes(b'changed-reference')
    with pytest.raises(rc.RevisionControlError, match='参考检查失败'):
        rc.confirm_candidate(tmp_path, change_id='DELIVERY', approval_ref='fixture:user')


def test_delivery_revision_requires_report_with_observed_project_id(tmp_path):
    frozen, observed = delivery(tmp_path)
    frozen['target_project_id'] = observed['target_project_id'] = 'exact-platform-project'
    observed['frozen_sha256'] = digest(frozen)
    logical = 'canvas.delivery'
    old = tmp_path / 'old-delivery.json'
    old.write_text('{}')
    manifest = artifacts.load_manifest(tmp_path)
    manifest['artifacts'].append({'artifact_id': 'OLD', 'logical_id': logical, 'revision': 'R01',
        'status': 'current', 'user_approved': True, 'path_or_url': old.name, 'checksum': artifacts.digest(old)})
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    rc.open_change(tmp_path, change_id='DELIVERY', tier='T1', logical_ids=[logical], reason='fixture')
    rc.confirm_impact(tmp_path, change_id='DELIVERY', approval_ref='fixture:impact')
    candidate = tmp_path / 'delivery.json'
    write_json(candidate, frozen)
    rc.record_candidate(tmp_path, change_id='DELIVERY', artifact_id='NEW', logical_id=logical,
                        artifact_type='json', candidate_path=candidate.name, revision=frozen['revision'])

    report = compare(frozen, observed, tmp_path)
    assert report['ok'] and report['observed_target_project_id'] == 'exact-platform-project'
    report_path = tmp_path / 'report.json'
    old_pass = {key: value for key, value in report.items() if key != 'observed_target_project_id'}
    for invalid in [old_pass, {**report, 'observed_target_project_id': 'other-project'}]:
        write_json(report_path, invalid)
        with pytest.raises(rc.RevisionControlError, match='项目 ID'):
            rc.record_review(tmp_path, change_id='DELIVERY', verdict='approved', report_ref=report_path.name)
    write_json(report_path, report)
    rc.record_review(tmp_path, change_id='DELIVERY', verdict='approved', report_ref=report_path.name)

    def restore_pre_upgrade_receipt():
        # Simulate the persisted receipt and exact bytes produced before this guard.
        old_report_path = tmp_path / 'old-report.json'
        write_json(old_report_path, old_pass)
        control_path = tmp_path / rc.CONTROL_RELATIVE
        control = read_json(control_path)
        control['active_change']['review'].update(
            report_ref=old_report_path.name, report_sha256=artifacts.digest(old_report_path))
        write_json(control_path, control)

    restore_pre_upgrade_receipt()
    with pytest.raises(rc.RevisionControlError, match='项目 ID'):
        rc.confirm_candidate(tmp_path, change_id='DELIVERY', approval_ref='fixture:user')
    rc.record_review(tmp_path, change_id='DELIVERY', verdict='approved', report_ref=report_path.name)
    rc.confirm_candidate(tmp_path, change_id='DELIVERY', approval_ref='fixture:user')
    confirmation = read_json(tmp_path / rc.CONTROL_RELATIVE)['active_change']['candidate_confirmation']
    restore_pre_upgrade_receipt()
    with pytest.raises(rc.RevisionControlError, match='项目 ID'):
        rc.commit_change(tmp_path, change_id='DELIVERY')
    rc.record_review(tmp_path, change_id='DELIVERY', verdict='approved', report_ref=report_path.name)
    change = read_json(tmp_path / rc.CONTROL_RELATIVE)['active_change']
    assert change['status'] == 'commit_ready' and change['candidate_confirmation'] == confirmation
    assert read_json(tmp_path / 'old-report.json') == old_pass
    assert rc.commit_change(tmp_path, change_id='DELIVERY')['state'] == 'committed'


def delivery_cli(root, *extra):
    import sys
    return subprocess.run([sys.executable, '-m', 'scripts.canvas_readback',
        '--frozen', str(root / 'frozen.json'), *extra], capture_output=True, text=True)


def test_explicit_delivery_cli_rechecks_after_interrupt_repeat_and_source_change(tmp_path):
    frozen, observed = delivery(tmp_path)
    write_json(tmp_path / 'frozen.json', frozen)
    write_json(tmp_path / 'observed.json', observed)
    args = ['--delivery', '--project', str(tmp_path)]
    assert delivery_cli(tmp_path, *args, '--preflight').returncode == 0
    # A completed preflight cannot turn an interrupted write into readback proof.
    assert delivery_cli(tmp_path, *args).returncode != 0
    args += ['--readback', str(tmp_path / 'observed.json')]
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    first, repeated = delivery_cli(tmp_path, *args), delivery_cli(tmp_path, *args)
    assert first.returncode == repeated.returncode == 0
    assert first.stdout == repeated.stdout
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    # Unchanged candidate/readback and prior PASS do not freeze authority.
    (tmp_path / 'director.json').write_text('{"fixture":"new revision bytes"}')
    assert delivery_cli(tmp_path, *args).returncode != 0


@pytest.mark.parametrize('missing', ['project', 'revision', 'manifest'])
def test_explicit_delivery_never_falls_back_to_legacy(tmp_path, missing):
    frozen, observed = delivery(tmp_path)
    args = ['--delivery', '--readback', str(tmp_path / 'observed.json')]
    if missing != 'project': args += ['--project', str(tmp_path)]
    if missing == 'revision':
        frozen.pop('revision'); observed['frozen_sha256'] = digest(frozen)
    if missing == 'manifest': (tmp_path / artifacts.MANIFEST_PATH).unlink()
    write_json(tmp_path / 'frozen.json', frozen); write_json(tmp_path / 'observed.json', observed)
    result = delivery_cli(tmp_path, *args)
    assert result.returncode != 0
    assert 'unrecognized arguments' not in result.stderr


def test_legacy_comparison_stays_read_only_and_unchecked(tmp_path):
    from scripts.tests._canvas_sample_fixture import sample
    frozen, observed = sample()
    write_json(tmp_path / 'frozen.json', frozen); write_json(tmp_path / 'observed.json', observed)
    result = delivery_cli(tmp_path, '--readback', str(tmp_path / 'observed.json'))
    assert result.returncode == 0
    assert json.loads(result.stdout)['submission_checks'] == 'not_checked'


def test_workflow_links_action_selfcheck():
    root = Path(__file__).resolve().parents[2]
    skill = (root / 'skills/voiceover-to-video-script/SKILL.md').read_text()
    method = root / 'skills/voiceover-to-video-script/modules/video-method.md'
    # Configuration reachability only, never an automated semantic judgment.
    assert 'video-method.md#自查与修改' in skill
    assert '### 动作链自查' in _doc_section(method, '自查与修改')
    action = _doc_section(method, '动作链自查')
    assert all(s in action for s in ['未检查', '发现问题', '已人工看图', '放回', '相邻两段'])


def _doc_section(path, anchor):
    body = path.read_text(encoding='utf-8')
    headings = list(re.finditer(r'^(#{1,6}) ([^\n]+)$', body, re.MULTILINE))
    heading = next((item for item in headings if _slug(item[2]) == anchor), None)
    assert heading is not None, f'{path.name}#{anchor}'
    boundary = next((item.start() for item in headings
                     if item.start() > heading.start() and len(item[1]) <= len(heading[1])), len(body))
    return body[heading.start():boundary]


def test_video_authoring_routes_read_only_their_scope():
    video = Path(__file__).resolve().parents[2] / 'skills/voiceover-to-video-script'
    skill = (video / 'SKILL.md').read_text(encoding='utf-8')
    method = video / 'modules/video-method.md'
    full_row = next(line for line in skill.splitlines() if line.startswith('| 全片写稿 |'))
    repair_row = next(line for line in skill.splitlines() if line.startswith('| 局部返修 |'))
    assert 'modules/video-method.md)' not in skill
    assert 'modules/video-method.md#全片写稿' in full_row and '#局部返修' not in full_row
    assert 'modules/video-method.md#局部返修' in repair_row and '#全片写稿' not in repair_row

    full = _doc_section(method, '全片写稿')
    repair = _doc_section(method, '局部返修')
    assert 'generation_boundaries' in full and '--discover-dependencies' in full
    assert '--segment-id' not in full
    assert all(term in repair for term in ('generation_boundaries', '--segment-id', '--require-file'))
    assert all('省略' in line for line in repair.splitlines() if '--discover-dependencies' in line)
    assert all(f'#{anchor}' in repair for anchor in ('跨镜对白及通话', '产品连续动作', '动作链自查'))
    assert '平台草稿的目标与持久化证据' not in method.read_text(encoding='utf-8')


def test_normal_draft_reading_links_exact_checks_and_keeps_optional_modes_separate():
    root = Path(__file__).resolve().parents[2]
    video = root / 'skills/voiceover-to-video-script'
    contract = video / 'references/seedance-delivery-package.md'
    snapshot = root / 'docs/development/delivery-snapshot-checks.md'
    redframe = root / 'skills/_shared/redframe-operations.md'
    experiments = root / 'docs/rules/experiments.md'
    # Ordinary saved-draft work starts with precise links, not a full-file read.
    skill = (video / 'SKILL.md').read_text(encoding='utf-8')
    save_row = next(line for line in skill.splitlines() if line.startswith('| 保存／更新画布草稿 |'))
    save_step = next(line for line in skill.splitlines() if line.startswith('4. 仅本轮获准平台操作时'))
    assert 'seedance-delivery-package.md#草稿参考与交付' in save_row
    assert 'seedance-delivery-package.md#正式草稿写前检查' in save_row
    assert 'redframe-operations.md#成批写入和整节点回读' in save_row
    assert 'seedance-delivery-package.md#画布紧凑排版' in save_row
    assert 'video-method.md' not in save_row
    assert 'seedance-delivery-package.md)' not in save_step

    persistence = _doc_section(contract, '草稿身份与回读')
    # Check the current persistence contract, not a synonym for refresh.
    # A user decision to skip refresh must retain the unverified scope.
    assert all(term in persistence for term in ('保存后', '用户决定', '刷新', '未实际刷新', '未核范围'))
    assert all(term in persistence for term in ('ordered_media', 'native_mentions'))
    assert '不足以证明绑定或持久化' in persistence
    assert 'RHTV' not in persistence

    checklist = _doc_section(contract, '正式草稿写前检查')
    required = ('同一份-json', '操作与采用', '来源映射', '正式交付的文件依赖',
                '本轮观察证据与输入版本', '分段参数')
    assert all(f'delivery-snapshot-checks.md#{anchor}' in checklist for anchor in required)
    assert 'delivery-snapshot-checks.md)' not in checklist
    schema = _doc_section(snapshot, '同一份-json')
    assert all(term in schema for term in ('node_id', 'media_id', 'native_noneditable_entity'))
    operation = _doc_section(snapshot, '操作与采用')
    assert '--delivery' in operation and '--preflight' in operation and '--readback' in operation
    assert all(term in operation for term in ('after_refresh', 'frozen_sha256', 'canvas_readback.digest'))
    batch = _doc_section(redframe, '成批写入和整节点回读')
    assert '--delivery' in batch and '--preflight' in batch
    assert '#草稿身份与回读' in '\n'.join((save_row, save_step, checklist, batch))
    assert '#新建直传计划和回读' in batch
    assert '#更新前核已有编辑' in batch
    direct = _doc_section(redframe, '新建直传计划和回读')
    assert 'finalize-new' in direct and 'after_refresh' in direct
    node_anchor = '红帧整节点的源连线用途与时间'
    assert f'#{node_anchor}' in batch
    node = _doc_section(redframe, node_anchor)
    assert all(term in node for term in ('unmentioned_reason', 'shot_durations', 'source_nodes'))
    update = _doc_section(redframe, '更新前核已有编辑')
    assert '--baseline' in update and '--current' in update

    normal = '\n'.join((save_row, save_step, checklist, persistence, batch, direct, node, update,
                        *(_doc_section(snapshot, anchor) for anchor in required)))
    assert all(term not in normal for term in ('--private-preview', 'read-rich-editor', 'node_port_download', 'rhtv_cdp.js'))
    assert '--private-preview' in _doc_section(experiments, '明确授权的私有候选画布预览')
    capture = _doc_section(redframe, '平台采集与离线合同的接缝')
    assert 'read-rich-editor' in capture and 'node_port_download' in capture
    assert 'RHTV_CANVAS_URL' in capture and 'RHTV_TARGET_ID' in capture


def test_delivery_source_revision_changes_after_preflight(tmp_path):
    frozen, observed = delivery(tmp_path)
    write_json(tmp_path / 'frozen.json', frozen); write_json(tmp_path / 'observed.json', observed)
    args = ['--delivery', '--project', str(tmp_path)]
    assert delivery_cli(tmp_path, *args, '--preflight').returncode == 0
    manifest = artifacts.load_manifest(tmp_path)
    next(a for a in manifest['artifacts'] if a['artifact_id'] == 'DIRECTOR')['revision'] = 'R02'
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    result = delivery_cli(tmp_path, *args, '--readback', str(tmp_path / 'observed.json'))
    assert result.returncode != 0
    assert 'revision' in result.stdout
