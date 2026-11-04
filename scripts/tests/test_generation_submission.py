"""Complete validate_submission replays using synthetic bytes and declarations."""
import copy
import json

import pytest

from scripts.canvas_readback import validate_submission
from scripts.core import artifacts, candidate_test
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state, write_project_state
from scripts.tests.test_delivery_preflight import delivery, bind_synthetic_quality
from scripts.tests.test_generation_boundaries import fixture
from scripts.tests.test_private_canvas_preview import preview, seal


def submission(root, private=False):
    frozen = preview(root) if private else delivery(root)[0]
    payload, board = fixture(root)
    payload.pop('source_storyboard')
    payload['generation_segments'] = payload['generation_segments'][:2]
    payload['scenes'] = board['scenes']
    sha = frozen['drafts'][0]['reference_checks'][0]['sha256']
    for i, segment in enumerate(payload['generation_segments']):
        segment['materials'] = [dict(sha256=sha, state_id='handoff',
                                     role='end_frame' if i == 0 else 'start_frame')]
    payload['generation_segments'][1]['continuity']['boundary'].update(
        state_id='handoff', previous_sha256s=[sha], current_sha256s=[sha])
    for i, row in enumerate(frozen['drafts']):
        row.update(source_shot_ids=[f'S{i+1}'], source_segment_id=f'G{i+1}',
                   duration_bounds=[1, 18], shot_durations=[9])
        row['parameters'].update(model='synthetic-model', reference_mode='synthetic-multi-reference', duration=9)
    write_json(root / 'director.json', payload)
    manifest = artifacts.load_manifest(root)
    item = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'DIRECTOR')
    item['checksum'] = item['sha256'] = artifacts.digest(root / 'director.json')
    frozen['source_artifacts'][0]['sha256'] = item['checksum']
    if private:
        frozen['source_artifacts'][0]['record_sha256'] = item['checksum']
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    bind_synthetic_quality(root, frozen)
    if private: seal(frozen)
    return frozen


def register_prior(root, current, prior, private):
    """Synthetic fixture registration, never grants real project authority."""
    path = root / 'prior.json'
    if private: seal(prior)
    write_json(path, prior)
    sha = artifacts.digest(path)
    ref = dict(artifact_id='PRIOR', revision=prior['revision'], sha256=sha)
    item = dict(**ref, logical_id='PRIOR', path_or_url=path.name, checksum=sha,
                status='candidate' if private else 'current', user_approved=not private)
    manifest = artifacts.load_manifest(root)
    manifest['artifacts'] = [a for a in manifest['artifacts'] if a['artifact_id'] != 'PRIOR'] + [item]
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    if private:
        candidate_test.configure(root, 'TEST', 'synthetic-only', ['FRAME', 'DIRECTOR', 'PRIOR'])
        ref.update(project_path=str(root), path=str(path), record_sha256=sha)
    else:
        state = load_project_state(root)
        if 'PRIOR' not in state['current_artifacts']:
            state['current_artifacts'].append('PRIOR')
            state['user_confirmations'].append({'artifact_ids': ['PRIOR']})
        write_project_state(root, state)
    current['source_artifacts'] = [r for r in current['source_artifacts'] if r['artifact_id'] != 'PRIOR'] + [ref]
    current['drafts'][0]['continuity']['previous_delivery'] = {k: ref[k] for k in ('artifact_id','revision','sha256')}
    from scripts.core.quality_evidence import fingerprint, draft_binding
    for row in current['drafts']:
        row['quality_evidence']['input_sha256'] = fingerprint(draft_binding(row))
    if private: seal(current)


def partial(root, private=False):
    frozen = submission(root, private)
    prior = copy.deepcopy(frozen)
    prior['drafts'] = prior['drafts'][:1]
    frozen['drafts'] = frozen['drafts'][1:]
    frozen['revision'] = 'R03'
    register_prior(root, frozen, prior, private)
    # Preserve quality observations for both needed nodes, not just the new one.
    bind_synthetic_quality(root, {'drafts': prior['drafts'] + frozen['drafts']})
    register_prior(root, frozen, prior, private)
    return frozen, prior


@pytest.mark.parametrize('private', [False, True])
def test_full_submission_exact_parameters_and_legitimate_aliases(tmp_path, private):
    frozen = submission(tmp_path, private)
    assert validate_submission(frozen, tmp_path, private_preview=private)['ok']
    for row in frozen['drafts']:
        row['parameters']['mode'] = row['parameters'].pop('reference_mode')
        row['parameters']['duration_seconds'] = row['parameters']['duration']
    bind_synthetic_quality(tmp_path, frozen)
    if private: seal(frozen)
    assert validate_submission(frozen, tmp_path, private_preview=private)['ok']


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('mutation', ['model', 'mode', 'mode_conflict', 'too_long', 'shorter_than_source', 'duration_conflict'])
def test_full_submission_cannot_replace_source_parameters(tmp_path, private, mutation):
    frozen = submission(tmp_path, private)
    row = frozen['drafts'][1]
    if mutation == 'model': row['parameters']['model'] = 'other-model'
    elif mutation == 'mode': row['parameters']['reference_mode'] = 'other-mode'
    elif mutation == 'mode_conflict': row['parameters']['mode'] = 'other-mode'
    elif mutation == 'duration_conflict': row['parameters']['duration_seconds'] = 4
    else:
        duration = 30 if mutation == 'too_long' else 4
        row['parameters']['duration'] = duration
        row.update(duration_bounds=[1, 60], shot_durations=[duration])
    bind_synthetic_quality(tmp_path, frozen)
    if private: seal(frozen)
    with pytest.raises(ValueError, match='实际交付'):
        validate_submission(frozen, tmp_path, private_preview=private)


@pytest.mark.parametrize('private', [False, True])
def test_partial_uses_existing_exact_predecessor_read_only(tmp_path, private):
    frozen, prior = partial(tmp_path, private)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    payload = copy.deepcopy(frozen)
    result = validate_submission(frozen, tmp_path, private_preview=private)
    assert result['ok'] and result['predecessor_context_nodes'] == ['one']
    assert frozen == payload and [r['node_id'] for r in frozen['drafts']] == ['two']
    assert {p: p.read_bytes() for p in before} == before
    assert result['production_ready'] is False
    if private:
        assert result['production_blocked'] and result['review_status'] == 'unreviewed'


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('mutation', ['erase_predecessor', 'missing_snapshot', 'wrong_event', 'wrong_node',
    'stale_bytes', 'wrong_revision', 'wrong_target', 'wrong_project', 'mixed_mode', 'source_revision',
    'wrong_segment', 'cycle', 'self_cycle', 'reference_state', 'prior_model'])
def test_partial_full_entry_rejects_laundered_or_stale_predecessor(tmp_path, private, mutation):
    frozen, prior = partial(tmp_path, private)
    row = frozen['drafts'][0]
    rewrite_prior = False
    if mutation == 'erase_predecessor': row['continuity'] = dict(event_id='new-event')
    elif mutation == 'missing_snapshot': row['continuity'].pop('previous_delivery')
    elif mutation == 'wrong_event': row['continuity']['event_id'] = 'new-event'
    elif mutation == 'wrong_node': row['continuity']['previous_node_id'] = 'unknown'
    elif mutation == 'stale_bytes': (tmp_path / 'prior.json').write_text('{}')
    elif mutation == 'wrong_revision': row['continuity']['previous_delivery']['revision'] = 'R00'
    else:
        rewrite_prior = True
        if mutation == 'wrong_target': prior['target_url'] += '-other'
        elif mutation == 'wrong_project': prior['target_project_id'] = 'other'
        elif mutation == 'mixed_mode':
            if private: prior.pop('preview_authorization')
            else: prior['preview_authorization'] = {}
        elif mutation == 'source_revision': prior['source_artifacts'][0]['sha256'] = 'f' * 64
        elif mutation == 'wrong_segment': prior['drafts'][0]['source_segment_id'] = 'OTHER'
        elif mutation == 'cycle': prior['drafts'][0]['continuity']['previous_node_id'] = 'two'
        elif mutation == 'self_cycle': prior['drafts'][0]['continuity']['previous_node_id'] = 'one'
        elif mutation == 'reference_state': prior['drafts'][0]['reference_checks'][0]['state_id'] = 'unrelated'
        elif mutation == 'prior_model': prior['drafts'][0]['parameters']['model'] = 'other-model'
    if rewrite_prior:
        # Avoid re-sealing intentionally absent mode declaration in this mutation.
        if mutation == 'mixed_mode':
            path = tmp_path / 'prior.json'; write_json(path, prior)
            sha = artifacts.digest(path)
            manifest = artifacts.load_manifest(tmp_path)
            item = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'PRIOR')
            item.update(checksum=sha, sha256=sha); write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
            frozen['source_artifacts'][-1].update(sha256=sha)
            if private: frozen['source_artifacts'][-1]['record_sha256'] = sha
            row['continuity']['previous_delivery']['sha256'] = sha
        else: register_prior(tmp_path, frozen, prior, private)
    if private: seal(frozen)
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path, private_preview=private)


@pytest.mark.parametrize('private', [False, True])
def test_partial_does_not_replay_unrelated_old_nodes_or_predecessor_history(tmp_path, private):
    frozen, prior = partial(tmp_path, private)
    prior['drafts'][0]['continuity'].update(previous_node_id='earlier-node',
        previous_delivery=dict(artifact_id='UNRELATED-OLD-SNAPSHOT', revision='R00', sha256='a' * 64))
    bind_synthetic_quality(tmp_path, {'drafts': prior['drafts'] + frozen['drafts']})
    prior['source_artifacts'].append(dict(artifact_id='UNRELATED-OLD-SNAPSHOT', revision='R00', sha256='a' * 64,
        project_path=str(tmp_path), path=str(tmp_path / 'missing-old.json'), record_sha256='a' * 64))
    unrelated = copy.deepcopy(prior['drafts'][0])
    unrelated.update(node_id='unrelated', parameters={'duration': 1, 'obsolete_unrelated_field': True}, prompt='')
    unrelated['reference_checks'][0]['artifact_id'] = 'UNRELATED-MISSING-FILE'
    prior['drafts'].append(unrelated)
    register_prior(tmp_path, frozen, prior, private)
    result = validate_submission(frozen, tmp_path, private_preview=private)
    assert result['ok'] and result['predecessor_context_nodes'] == ['one']


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('cycle', [False, True])
def test_partial_selected_historical_contexts_are_acyclic(tmp_path, private, cycle):
    frozen = submission(tmp_path, private)
    payload = json.loads((tmp_path / 'director.json').read_text())
    for i, original in enumerate(copy.deepcopy(payload['generation_segments']), 3):
        original.update(segment_id=f'G{i}', source_shot_ids=[f'S{i}'])
        original['same_scene_split'] = dict(from_segment_id=f'G{i-1}', reason='event_complete' if i == 3 else 'model_duration_exceeded',
            evidence='Synthetic event boundary.', estimated_scene_duration_seconds=27, timing_basis='Synthetic estimate.',
            source_boundary=dict(shot_id=f'S{i-1}', field='cut.exit', quote=f'Synthetic boundary {i-1}'))
        if i == 4: original['continuity']['previous_segment_id'] = 'G3'
        payload['generation_segments'].append(original)
        if i == 4:
            payload['scenes'][0]['shots'].append(dict(shot_id=f'S{i}', cut={'exit':f'Synthetic boundary {i}'}))
    write_json(tmp_path / 'director.json', payload)
    manifest = artifacts.load_manifest(tmp_path)
    source = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'DIRECTOR')
    source['checksum'] = source['sha256'] = artifacts.digest(tmp_path / 'director.json')
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    frozen['source_artifacts'][0]['sha256'] = source['checksum']
    if private: frozen['source_artifacts'][0]['record_sha256'] = source['checksum']
    extra = copy.deepcopy(frozen['drafts'])
    for i, row in enumerate(extra, 3):
        row.update(node_id=f'node{i}', source_segment_id=f'G{i}', source_shot_ids=[f'S{i}'])
    all_rows = frozen['drafts'] + extra
    for row, previous in zip(all_rows, ['node3', 'one', 'one', 'node3']):
        row['continuity']['previous_node_id'] = previous
        if private: row['reference_checks'][0]['limited_use']['draft_node_ids'] += ['node3','node4']
    if not cycle: all_rows[0]['continuity'].pop('previous_node_id')
    bind_synthetic_quality(tmp_path, {'drafts': all_rows})
    prior = copy.deepcopy(frozen); prior['drafts'] = [all_rows[0], all_rows[2]]
    frozen['drafts'] = [all_rows[1], all_rows[3]]
    register_prior(tmp_path, frozen, prior, private)
    frozen['drafts'][1]['continuity']['previous_delivery'] = copy.deepcopy(frozen['drafts'][0]['continuity']['previous_delivery'])
    from scripts.core.quality_evidence import fingerprint, draft_binding
    for row in frozen['drafts']:
        row['quality_evidence']['input_sha256'] = fingerprint(draft_binding(row))
    if private: seal(frozen)
    if cycle:
        with pytest.raises(ValueError, match='contexts contain a cycle'):
            validate_submission(frozen, tmp_path, private_preview=private)
    else:
        assert validate_submission(frozen, tmp_path, private_preview=private)['ok']


@pytest.mark.parametrize('private', [False, True])
def test_node_id_source_alias_cannot_bypass_parameter_binding(tmp_path, private):
    frozen = submission(tmp_path, private)
    for row in frozen['drafts']:
        row['node_id'] = row.pop('source_segment_id')
    frozen['drafts'][1]['continuity']['previous_node_id'] = 'G1'
    frozen['drafts'][1]['parameters']['model'] = 'other-model'
    bind_synthetic_quality(tmp_path, frozen)
    if private: seal(frozen)
    with pytest.raises(ValueError, match='实际交付模型'):
        validate_submission(frozen, tmp_path, private_preview=private)
