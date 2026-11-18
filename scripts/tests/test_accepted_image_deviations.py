"""Synthetic human decisions exercise actual image/delivery consumers, not visual QA."""
import copy

import pytest

from scripts import canvas_readback
from scripts.core import asset_refs, artifacts, quality_evidence as q
from scripts.core.io import read_json, write_json
from test_quality_evidence_consumers import fixture


def accepted_fixture(root):
    frozen, manifest = fixture(root)
    row = frozen['drafts'][0]
    ref = row['reference_checks'][0]
    ref['roles'] = ['layout']
    row['required_reference_roles'] = {'E1': ['layout']}
    row['quality_evidence']['checks']['persistent_objects'].update(
        scene_state_assessment='unchanged', source_shot_ids=['S1'])
    evidence = manifest['artifacts'][0]['quality_evidence']
    evidence['uses'] = [q.image_use(ref, ['S1'])]
    evidence['status'] = 'fail'
    evidence['checks']['space_objects'] = {
        'result': 'fail', 'observation': 'Synthetic bag proportions differ.',
        'locator': 'Synthetic bag on the seat.'}
    receipt = read_json(root / 'confirmation.json')
    receipt['approval_ref'] = 'SYNTHETIC exact user message only'
    receipt['usage']['accepted_deviations'] = [{
        'image_sha256': manifest['artifacts'][0]['checksum'],
        'use': q.image_use(ref, ['S1']),
        'checks': {'space_objects': copy.deepcopy(evidence['checks']['space_objects'])},
        'quote': 'SYNTHETIC: accept the bag proportions only as spatial reference.',
        'decision_ref': 'SYNTHETIC user message reference'}]
    row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))
    write_json(root / 'confirmation.json', receipt)
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    return frozen, manifest, receipt


def test_human_accepted_pixels_work_in_real_consumers_without_rewriting_failure(tmp_path):
    frozen, manifest, _ = accepted_fixture(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report = canvas_readback.validate_submission(frozen, tmp_path)
    assert report['ok'] and report['visual_quality_verified'] is False
    assert report['accepted_image_uses'][0]['image_use_status'] == 'user_accepted_deviations'
    assert asset_refs.require_references(tmp_path, ['IMG'], ['S1']) == (True, [])
    entry = manifest['artifacts'][0]
    result = q.validate_image_evidence(tmp_path, tmp_path / 'image.png', entry['quality_evidence'], ['S1'],
                                       artifact=entry, reference=frozen['drafts'][0]['reference_checks'][0])
    assert result['image_use_status'] == 'user_accepted_deviations'
    assert result['accepted_deviations'] == ['space_objects']
    assert entry['quality_evidence']['checks']['space_objects']['result'] == 'fail'
    assert entry['quality_evidence']['status'] == 'fail'
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('mutation', ['unaccepted_leg', 'changed_observation', 'old_pixels', 'no_quote',
                                     'no_decision', 'no_human_receipt', 'wrong_shots', 'wrong_entity',
                                     'wrong_state', 'changed_role', 'self_check_claim'])
def test_accepted_deviations_cannot_expand_actual_delivery_permission(tmp_path, mutation):
    frozen, manifest, receipt = accepted_fixture(tmp_path)
    row = frozen['drafts'][0]
    evidence = manifest['artifacts'][0]['quality_evidence']
    accepted = receipt['usage']['accepted_deviations'][0]
    if mutation == 'unaccepted_leg':
        evidence['checks']['hands_support']['result'] = 'fail'
    elif mutation == 'changed_observation':
        evidence['checks']['space_objects']['observation'] = 'Different unaccepted problem.'
    elif mutation == 'old_pixels':
        accepted['image_sha256'] = '0' * 64
    elif mutation == 'no_quote':
        accepted.pop('quote')
    elif mutation == 'no_decision':
        accepted.pop('decision_ref')
    elif mutation == 'no_human_receipt':
        receipt.pop('approval_ref')
    elif mutation == 'wrong_shots':
        accepted['use']['source_shot_ids'] = ['S2']
    elif mutation == 'wrong_entity':
        accepted['use']['entity_ids'] = ['E2']
    elif mutation == 'wrong_state':
        accepted['use']['state_id'] = 'another-state'
    elif mutation == 'changed_role':
        row['reference_checks'][0]['roles'] = ['identity']
        row['required_reference_roles'] = {'E1': ['identity']}
        evidence['uses'].append(q.image_use(row['reference_checks'][0], ['S1']))
    else:
        evidence['accepted_deviations'] = receipt['usage'].pop('accepted_deviations')
    row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))
    write_json(tmp_path / 'confirmation.json', receipt)
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    with pytest.raises(ValueError, match='quality_evidence'):
        canvas_readback.validate_submission(frozen, tmp_path)


def test_exact_revision_confirmation_is_also_consumed(tmp_path):
    frozen, manifest, receipt = accepted_fixture(tmp_path)
    entry = manifest['artifacts'][0]
    entry.pop('confirmation_ref')
    state_path = tmp_path / '00_项目管理/project_state.json'
    state = read_json(state_path)
    state['user_confirmations'] = [{
        'scope': 'artifact', 'kind': 'revision-candidate', 'artifact_id': 'IMG',
        'revision': entry['revision'], 'sha256': entry['checksum'],
        'approval_ref': receipt['approval_ref'], 'usage': receipt['usage']}]
    write_json(state_path, state)
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    assert canvas_readback.validate_submission(frozen, tmp_path)['ok']
    assert asset_refs.require_references(tmp_path, ['IMG'], ['S1']) == (True, [])


def test_image_acceptance_does_not_release_failed_draft_checks(tmp_path):
    frozen, _, _ = accepted_fixture(tmp_path)
    row = frozen['drafts'][0]
    row['quality_evidence']['checks']['action_chain']['result'] = 'fail'
    with pytest.raises(ValueError, match='quality_evidence'):
        canvas_readback.validate_submission(frozen, tmp_path)
