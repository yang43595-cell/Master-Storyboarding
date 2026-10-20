"""Negative replays: identity boards, stale PASS and matching graph edges are insufficient."""
import copy
import json
import subprocess
import shutil

import pytest
from PIL import Image

from scripts.canvas_readback import compare, digest, validate_submission
from scripts.core import artifacts, revision_control as rc
from scripts.core.io import write_json, read_json
from scripts.core.project_state import new_project_state, write_project_state
from scripts.tests.test_review_receipt_binding import pending


def delivery(root):
    # Tiny synthetic local fixtures; no customer content or model execution.
    Image.new('RGB', (8, 8), 'blue').save(root / 'frame.png')
    from scripts.tests.test_story_paragraph_sources import narrative
    from scripts.tests.test_director_compact import compact_package
    n = narrative(root); n.pop('human_readable_path')
    b = compact_package(); b.pop('human_readable_path')
    b['revision'] = 'R01'
    b['source_narrative'] = {'artifact_id': 'NARRATIVE', 'logical_id': 'narrative.package', 'revision': 'R01', 'path': 'narrative.json'}
    write_json(root / 'narrative.json', n); write_json(root / 'director.json', b)
    refs = {}
    entries = []
    for ident, name in [('FRAME', 'frame.png'), ('DIRECTOR', 'director.json'), ('NARRATIVE', 'narrative.json')]:
        refs[ident] = {'artifact_id': ident, 'revision': 'R01', 'sha256': artifacts.digest(root / name)}
        logical = {'DIRECTOR': 'storyboard.package', 'NARRATIVE': 'narrative.package'}.get(ident, ident)
        entries.append({**refs[ident], 'logical_id': logical, 'type': logical, 'status': 'current',
                        'user_approved': True, 'path_or_url': name, 'checksum': refs[ident]['sha256'],
                        'upload_bindings': [{'target_url': 'https://fixture.invalid/exact',
                            'node_id': 'frame-node', 'media_id': 'frame-media',
                            'revision': 'R01', 'sha256': refs[ident]['sha256']}] if ident == 'FRAME' else []})
    write_json(root / artifacts.MANIFEST_PATH, {'artifacts': entries})
    state = new_project_state()
    state.update(current_artifacts=list(refs), user_confirmations=[{'artifact_ids': list(refs)}])
    write_project_state(root, state)
    def row(ident, previous=None):
        return {'node_id': ident, 'prompt': 'Synthetic actor holds the item.', 'source_shot_ids': ['S01-010'],
                'parameters': {'duration': 4}, 'duration_bounds': [2, 8], 'shot_durations': [1, 3],
                'native_entity_type': 'fixture-chip', 'required_entities': ['ACTOR'],
                'required_reference_roles': {'ACTOR': ['start_frame' if previous else 'end_frame']},
                'ordered_media': [{'node_id': 'frame-node', 'media_id': 'frame-media'}],
                'reference_checks': [{**refs['FRAME'], 'kind': 'image',
                                      'role': 'start_frame' if previous else 'end_frame',
                                      'state_id': 'handoff', 'entity_ids': ['ACTOR']}],
                'continuity': {'event_id': 'one-event', **({'previous_node_id': previous} if previous else {})}}
    frozen = {'target_url': 'https://fixture.invalid/exact', 'revision': 'R02',
              'schema_version': 'video-prompt-1.0', 'document_type': 'video.prompt',
              'stage': 'voiceover-to-video-script', 'project_id': 'MSB-TEST',
              'source_storyboard': {**refs['DIRECTOR'], 'logical_id': 'storyboard.package', 'path': 'director.json'},
              'source_narrative': {**refs['NARRATIVE'], 'logical_id': 'narrative.package', 'path': 'narrative.json'},
              'source_artifacts': [refs['DIRECTOR'], refs['NARRATIVE']], 'drafts': [row('one'), row('two', 'one')]}
    observed = copy.deepcopy(frozen)
    observed.update(phase='after_refresh', frozen_sha256=digest(frozen))
    for row in observed['drafts']:
        row['native_mentions'] = [{**m, 'entity_type': 'fixture-chip', 'native_noneditable_entity': True}
                                  for m in row['ordered_media']]
    return frozen, observed


def test_full_reference_and_native_readback_then_mutation_fails(tmp_path):
    frozen, observed = delivery(tmp_path)
    assert compare(frozen, observed, tmp_path)['ok']
    observed['drafts'][1]['native_mentions'][0]['native_noneditable_entity'] = False
    assert not compare(frozen, observed, tmp_path)['ok']
    with pytest.raises(ValueError, match='--project'):
        compare(frozen, observed)


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
    video = tmp_path / 'action.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=16x16:r=10',
                    '-t', '1', '-pix_fmt', 'yuv420p', str(video)], check=True, capture_output=True)
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
    assert validate_submission(frozen, tmp_path)['ok']
    ref['duration'] = 2
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
    from scripts.tests.test_canvas_readback import sample
    frozen, observed = sample()
    write_json(tmp_path / 'frozen.json', frozen); write_json(tmp_path / 'observed.json', observed)
    result = delivery_cli(tmp_path, '--legacy-read-only', '--readback', str(tmp_path / 'observed.json'))
    assert result.returncode == 0
    assert json.loads(result.stdout)['submission_checks'] == 'not_checked'


def test_workflow_links_action_selfcheck_and_explicit_delivery():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    skill = (root / 'skills/voiceover-to-video-script/SKILL.md').read_text()
    method = (root / 'skills/voiceover-to-video-script/modules/video-method.md').read_text()
    contract = (root / 'skills/voiceover-to-video-script/references/seedance-delivery-package.md').read_text()
    docs = (root / 'docs/development/delivery-snapshot-checks.md').read_text()
    # Configuration reachability only, never an automated semantic judgment.
    assert '动作链自查' in skill and '动作链自查' in method
    assert all(s in method for s in ['未检查', '发现问题', '已人工看图', '放回', '相邻两段'])
    assert '--delivery' in contract and docs.count('--delivery') >= 2


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
