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
    (root / 'director.json').write_text('{"fixture":"director"}')
    refs = {}
    entries = []
    for ident, name in [('FRAME', 'frame.png'), ('DIRECTOR', 'director.json')]:
        refs[ident] = {'artifact_id': ident, 'revision': 'R01', 'sha256': artifacts.digest(root / name)}
        entries.append({**refs[ident], 'logical_id': ident, 'status': 'current',
                        'user_approved': True, 'path_or_url': name, 'checksum': refs[ident]['sha256']})
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
            'status': 'current', 'user_approved': True, 'path_or_url': video.name, 'checksum': artifacts.digest(video)}
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
