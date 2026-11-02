"""Synthetic endpoint/authority/source-strategy integration, no live QA claims."""
import copy
import json

import pytest
from PIL import Image

from scripts.canvas_readback import compare, digest, validate_submission, _endpoint_upload_expected
from scripts.core import artifacts, candidate_test, quality_evidence as q
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state, write_project_state
from scripts.tests.test_delivery_preflight import delivery, bind_synthetic_quality
from scripts.tests.test_private_canvas_preview import preview, seal as seal_preview
from scripts.tests.test_endpoint_control import synthetic, seal as seal_control, descriptor
from scripts.tests.test_endpoint_states import fixture


def bind_endpoint_quality(root, frozen):
    bind_synthetic_quality(root, frozen)
    for row in frozen['drafts']:
        row['quality_evidence']['checks']['persistent_objects'].update(
            scene_state_assessment='changed', source_shot_ids=list(row['source_shot_ids']),
            observation='Synthetic source chain changes from floor to lifted to exited; no real pixel claim.',
            locator='Synthetic director scene S / shots S1, S2, S3')
        row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))


def integrated(root, private):
    frozen = preview(root) if private else delivery(root)[0]
    row = frozen['drafts'][0]
    old_ref = copy.deepcopy(row['reference_checks'][0])
    state_row, sources, strategy = fixture()
    strategy['end_facts'].append(dict(fact_id='BAG-END', entity_id='BAG', expected_presence='present',
        evidence_requirement='visible', source_quote='Bag remains visible.'))
    state_row['reference_checks'][1]['endpoint_observations']['facts'].append(dict(
        fact_id='BAG-END', entity_id='BAG', visibility='visible', observation='Synthetic visible bag at end'))
    state_row['prompt'] += ' Bag remains visible.'
    row.update(state_row)
    row.update(node_id='draft-node', native_identity_mode='first_last_frame_download',
               parameters=dict(duration=17, model='Synthetic model', aspect_ratio='9:16', resolution='480p'),
               duration_bounds=[2, 20], shot_durations=[17], continuity=dict(event_id='one-event'))
    row.pop('native_entity_type', None)
    row['required_reference_roles'] = {'ROOM': ['start_frame', 'end_frame'], 'BAG': ['end_frame'], 'WIFE': ['end_frame']}
    frozen['drafts'] = [row]
    Image.new('RGB', (8, 8), 'green').save(root / 'end.png')
    manifest = artifacts.load_manifest(root)
    start = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'FRAME')
    end = copy.deepcopy(start)
    end.update(artifact_id='FRAME-END', logical_id='FRAME-END', path_or_url='end.png',
               checksum=artifacts.digest(root / 'end.png'), sha256=artifacts.digest(root / 'end.png'))
    manifest['artifacts'].append(end)
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    if private:
        candidate_test.configure(root, 'TEST', 'synthetic-only', ['FRAME', 'FRAME-END', 'DIRECTOR'])
    else:
        state = load_project_state(root)
        state['current_artifacts'].append('FRAME-END')
        state['user_confirmations'].append({'artifact_ids': ['FRAME-END']})
        write_project_state(root, state)
    for i, (ref, item) in enumerate(zip(row['reference_checks'], [start, end])):
        scoped = copy.deepcopy(ref)
        ref.update(copy.deepcopy(old_ref)); ref.update(scoped); ref.pop('role', None)
        ref.update(artifact_id=item['artifact_id'], revision=item['revision'], sha256=item['checksum'],
                   entity_ids=['ROOM', 'BAG'] if i == 0 else ['ROOM', 'WIFE', 'BAG'])
        if private:
            ref.update(path=str(root / item['path_or_url']), record_sha256=item['checksum'])
            ref['limited_use'].update(draft_node_ids=['draft-node'], roles=ref['roles'], entity_ids=ref['entity_ids'])
    source = sources[0][2]
    source['generation_segments'] = [dict(segment_id='V1', source_shot_ids=['S1', 'S2', 'S3'])]
    (root / 'director.json').write_text(json.dumps(source))
    director = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'DIRECTOR')
    director['checksum'] = director['sha256'] = artifacts.digest(root / 'director.json')
    frozen['source_artifacts'][0]['sha256'] = director['checksum']
    if private: frozen['source_artifacts'][0]['record_sha256'] = director['checksum']
    row['state_control'].update(source_revision=director['revision'], source_sha256=director['checksum'])
    folder = root / 'endpoint-evidence'; folder.mkdir()
    control, expected, reports = synthetic(folder)
    frozen.update(target_url=expected['target_url'], target_project_id=expected['target_project_id'])
    expected.update(draft_revision=frozen['revision'], source_segment_id='V1')
    reports['scope']['V1'] = reports['scope'].pop('SYNTH-6')
    reports['post_refresh']['V1'] = reports['post_refresh'].pop('SYNTH-6')
    reports['slot_downloads']['checks']['V1'] = reports['slot_downloads']['checks'].pop('SYNTH-6')
    for index, item in enumerate([start, end]):
        expected['ordered_media'][index]['sha256'] = item['checksum']
        data = (root / item['path_or_url']).read_bytes()
        for label in ('source', 'input'):
            control['downloads'][index][label] = descriptor(folder, f'{label}-{index+1}.bin', data)
        for receipt in (reports['source_downloads']['sources'][index], reports['slot_downloads']['checks']['V1']['bound_media'][index]):
            receipt['sha256'] = item['checksum']
            receipt['path'] = str(folder / f'source-{index+1}.bin')
    control['scope'] = copy.deepcopy(expected)
    seal_control(folder, control, reports)
    for desc in [*control['evidence'].values(), *[d[k] for d in control['downloads'] for k in ('source', 'input')]]:
        desc['path'] = str(folder.relative_to(root) / desc['path'])
    row['ordered_media'] = expected['ordered_media']
    row['state_control']['platform'] = control
    for slot, (ref, media, item) in enumerate(zip(row['reference_checks'], row['ordered_media'], [start, end]), 1):
        binding = _endpoint_upload_expected(frozen, row, ref, media, slot)
        if private: ref['upload_binding'] = binding
        else: item['upload_bindings'] = [binding]
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    bind_endpoint_quality(root, frozen)
    if private:
        frozen['preview_authorization'].update(target_url=frozen['target_url'], target_project_id=frozen['target_project_id'])
        seal_preview(frozen)
    observed = copy.deepcopy(frozen)
    observed.update(phase='after_refresh', frozen_sha256=digest(frozen))
    observed['drafts'][0]['frame_slots'] = [dict(role=role, **m) for role, m in zip(('start_frame', 'end_frame'), row['ordered_media'])]
    return frozen, observed


@pytest.mark.parametrize('private', [False, True])
def test_synthetic_full_endpoint_path(tmp_path, private):
    frozen, observed = integrated(tmp_path, private)
    result = compare(frozen, observed, tmp_path, private_preview=private)
    assert result['ok'] and result['production_ready'] is False
    assert result['native_reference_checks'] == {'draft-node': 'not_applicable_endpoint_slots'}
    assert result['endpoint_control_checks'] == {'draft-node': 'passed'}
    proof = result['submission_checks']['endpoint_control_checks']['draft-node']
    assert proof['endpoint_constraints_verified'] and not proof['motion_verified']
    assert proof['source_strategy']['per_shot_control'] is False
    with pytest.raises(ValueError, match='--project'):
        compare(frozen, observed, private_preview=private)


@pytest.mark.parametrize('mutation', ['mode_only', 'source_strategy_missing', 'source_hash', 'wrong_role', 'missing_qa',
    'reverse_media', 'third_reference', 'legacy_mode', 'missing_mapping', 'mapping_slot', 'mapping_hash', 'claimed_proof'])
def test_endpoint_mode_never_bypasses_existing_guards(tmp_path, mutation):
    frozen, _ = integrated(tmp_path, True)
    row = frozen['drafts'][0]
    if mutation == 'mode_only': row.pop('state_control')
    elif mutation == 'source_strategy_missing': row['state_control']['strategy_id'] = 'absent'
    elif mutation == 'source_hash': row['state_control']['source_sha256'] = '0' * 64
    elif mutation == 'wrong_role': row['reference_checks'][0]['roles'] = ['state_reference']
    elif mutation == 'missing_qa': row['reference_checks'][0].pop('endpoint_observations')
    elif mutation == 'reverse_media': row['ordered_media'].reverse()
    elif mutation == 'third_reference':
        row['ordered_media'].append(copy.deepcopy(row['ordered_media'][0]))
        row['reference_checks'].append(copy.deepcopy(row['reference_checks'][0]))
    elif mutation == 'legacy_mode': row.pop('native_identity_mode')
    elif mutation == 'missing_mapping': row['reference_checks'][0].pop('upload_binding')
    elif mutation == 'mapping_slot': row['reference_checks'][0]['upload_binding']['slot'] = 2
    elif mutation == 'mapping_hash': row['reference_checks'][0]['upload_binding']['control_evidence_sha256'] = '0' * 64
    elif mutation == 'claimed_proof': row['state_control']['platform'] = dict(endpoint_constraints_verified=True)
    seal_preview(frozen)
    with pytest.raises(ValueError): validate_submission(frozen, tmp_path, private_preview=True)


@pytest.mark.parametrize('mutation', ['reverse_slots', 'slot_hash', 'inline_mentions', 'prompt', 'parameters', 'missing_slots'])
def test_endpoint_readback_remains_exact(tmp_path, mutation):
    frozen, observed = integrated(tmp_path, True)
    row = observed['drafts'][0]
    if mutation == 'reverse_slots': row['frame_slots'].reverse()
    elif mutation == 'slot_hash': row['frame_slots'][0]['sha256'] = '0' * 64
    elif mutation == 'inline_mentions': row['native_mentions'] = [{'node_id': 'invented'}]
    elif mutation == 'prompt': row['prompt'] += 'changed'
    elif mutation == 'parameters': row['parameters']['duration'] = 18
    elif mutation == 'missing_slots': row.pop('frame_slots')
    assert not compare(frozen, observed, tmp_path, private_preview=True)['ok']


def test_missing_source_strategy_is_not_satisfied_by_valid_platform(tmp_path):
    frozen, _ = integrated(tmp_path, True)
    row = frozen['drafts'][0]
    source = json.loads((tmp_path / 'director.json').read_text())
    source['scenes'][0].pop('endpoint_strategies')
    (tmp_path / 'director.json').write_text(json.dumps(source))
    sha = artifacts.digest(tmp_path / 'director.json')
    manifest = artifacts.load_manifest(tmp_path)
    next(a for a in manifest['artifacts'] if a['artifact_id'] == 'DIRECTOR').update(checksum=sha, sha256=sha)
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    frozen['source_artifacts'][0].update(sha256=sha, record_sha256=sha)
    row['state_control']['source_sha256'] = sha
    bind_endpoint_quality(tmp_path, frozen)
    seal_preview(frozen)
    with pytest.raises(ValueError, match='source must explicitly select'):
        validate_submission(frozen, tmp_path, private_preview=True)


def test_default_multistate_contract_is_not_downgraded(tmp_path):
    frozen, _ = integrated(tmp_path, True)
    row = frozen['drafts'][0]
    row.pop('native_identity_mode'); row.pop('state_control')
    seal_preview(frozen)
    with pytest.raises(ValueError, match='scene_state'):
        validate_submission(frozen, tmp_path, private_preview=True)


@pytest.mark.parametrize('private', [False, True])
def test_endpoint_mapping_boolean_slot_cannot_alias_integer(tmp_path, private):
    frozen, _ = integrated(tmp_path, private)
    if private:
        frozen['drafts'][0]['reference_checks'][0]['upload_binding']['slot'] = True
        seal_preview(frozen)
    else:
        manifest = artifacts.load_manifest(tmp_path)
        next(a for a in manifest['artifacts'] if a['artifact_id'] == 'FRAME')['upload_bindings'][0]['slot'] = True
        write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    with pytest.raises(ValueError): validate_submission(frozen, tmp_path, private_preview=private)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('mutation', ['unchanged', 'wrong_shots'])
def test_endpoint_requires_consistent_persistent_state_assessment(tmp_path, private, mutation):
    frozen, _ = integrated(tmp_path, private)
    row = frozen['drafts'][0]
    assessment = row['quality_evidence']['checks']['persistent_objects']
    if mutation == 'unchanged': assessment['scene_state_assessment'] = 'unchanged'
    else: assessment['source_shot_ids'] = ['S1', 'S3']
    row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))
    if private: seal_preview(frozen)
    with pytest.raises(ValueError, match='endpoint_state'):
        validate_submission(frozen, tmp_path, private_preview=private)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('entity,role,slot,passes', [
    ('ROOM', 'identity', 0, True), ('ROOM', 'appearance', 0, True),
    ('BAG', 'identity', 0, False), ('BAG', 'start_frame', 0, False),
    ('WIFE', 'identity', 1, False), ('WIFE', 'appearance', 1, False),
    ('WIFE', 'layout', 1, False), ('WIFE', 'scale', 1, False),
    ('WIFE', 'state_reference', 1, True)])
def test_endpoint_role_coverage_uses_entity_visibility(tmp_path, private, entity, role, slot, passes):
    frozen, _ = integrated(tmp_path, private)
    row = frozen['drafts'][0]
    row['required_reference_roles'][entity] = [role]
    ref = row['reference_checks'][slot]
    if role not in ref['roles']: ref['roles'].append(role)
    if private:
        ref['limited_use']['roles'] = list(ref['roles'])
        ref['limited_use']['excluded_roles'] = []
    bind_endpoint_quality(tmp_path, frozen)
    if private: seal_preview(frozen)
    if passes:
        assert validate_submission(frozen, tmp_path, private_preview=private)['ok']
    else:
        with pytest.raises(ValueError, match='reference role is missing'):
            validate_submission(frozen, tmp_path, private_preview=private)
