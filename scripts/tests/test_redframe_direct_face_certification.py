"""Offline tests of certification scope in formal RedFrame node readback."""

import copy
import json

import pytest

from scripts.canvas_readback import digest
from scripts.core import artifacts
from scripts.core.io import write_json
from scripts.tests.test_workflow_repair_cli import (
    bind_redframe_production_sources,
    canvas_cli,
    redframe_fixture,
)


def direct_fixture(root, faces=(True, False)):
    frozen, observed = redframe_fixture(root)
    for row, face in zip(frozen['drafts'], faces):
        row['edges'] = []
        row['source_nodes'] = []
        ref, = row['reference_checks']
        ref['canvas_binding'] = dict(mode='direct', purpose='Synthetic boundary', prompt_reference=True)
        if face is not None:
            ref['contains_realistic_face'] = face
    bind_redframe_production_sources(root, frozen, observed)
    return frozen, observed


def certification_record(row):
    return dict(slot=1, draft_node_id=row['node_id'], **row['ordered_media'][0],
                sha256=row['reference_checks'][0]['sha256'], status='certified',
                locator='Synthetic current direct-slot native certification readout')


@pytest.mark.parametrize('mutation, status, reason', [
    ('exact', 'passed', None),
    ('legacy_source_node', 'unknown', 'missing_slot_evidence'),
    ('missing', 'unknown', 'missing_slot_evidence'),
    ('pending', 'unknown', 'pending'),
    ('not_certified', 'failed', 'not_certified'),
    ('wrong_draft', 'failed', 'slot_identity_or_sha256_mismatch'),
    ('wrong_media', 'failed', 'slot_identity_or_sha256_mismatch'),
    ('wrong_hash', 'failed', 'slot_identity_or_sha256_mismatch'),
    ('unlocated', 'failed', 'invalid_certification_state_or_locator'),
    ('unknown_status', 'failed', 'invalid_certification_state_or_locator'),
    ('duplicate_slot', 'failed', 'invalid_or_duplicate_slot'),
])
def test_actual_direct_slot_certification_is_separate_from_structural_readback(tmp_path, mutation, status, reason):
    frozen, observed = direct_fixture(tmp_path)
    row = observed['drafts'][0]
    cert = certification_record(row)
    if mutation == 'legacy_source_node':
        observed['source_nodes'] = [{'faceCertification': {'assetId': 'source_asset'}}]
    elif mutation != 'missing':
        row['face_certification'] = [cert]
    if mutation in {'pending', 'not_certified'}:
        cert['status'] = mutation
    elif mutation == 'wrong_draft':
        cert['draft_node_id'] = observed['drafts'][1]['node_id']
    elif mutation == 'wrong_media':
        cert['media_id'] = 'old-upload'
    elif mutation == 'wrong_hash':
        cert['sha256'] = '0' * 64
    elif mutation == 'unlocated':
        cert.pop('locator')
    elif mutation == 'unknown_status':
        cert['status'] = 'uploaded'
    elif mutation == 'duplicate_slot':
        row['face_certification'].append(copy.copy(cert))

    result = canvas_cli(tmp_path, frozen, observed)
    assert result.returncode == 0, result.stdout
    report = json.loads(result.stdout)
    assert report['native_reference_checks']['one'] == 'passed'
    assert report['face_certification_status'] == status
    assert report['face_certification_checks']['two']['status'] == 'not_applicable'
    item = report['face_certification_checks']['one']
    assert item['status'] == status
    assert (item['slots'][0] if item['slots'] else item).get('reason') == reason
    assert report['production_ready'] is False


def test_exact_certificate_cannot_disguise_changed_actual_slot(tmp_path):
    frozen, observed = direct_fixture(tmp_path)
    row = observed['drafts'][0]
    row['face_certification'] = [certification_record(row)]
    row['ordered_media'][0]['media_id'] = 'new-upload'
    result = canvas_cli(tmp_path, frozen, observed)
    assert result.returncode == 1, result.stdout
    report = json.loads(result.stdout)
    assert report['face_certification_status'] == 'failed'
    assert report['face_certification_checks']['one']['slots'][0]['reason'] == 'slot_identity_or_sha256_mismatch'


@pytest.mark.parametrize('other_face', [True, None])
def test_unknown_other_slot_does_not_inherit_already_certified_slot(tmp_path, other_face):
    frozen, observed = direct_fixture(tmp_path, (True, other_face))
    row = observed['drafts'][0]
    row['face_certification'] = [certification_record(row)]
    result = canvas_cli(tmp_path, frozen, observed)
    assert result.returncode == 0, result.stdout
    report = json.loads(result.stdout)
    assert report['face_certification_checks']['one']['status'] == 'passed'
    assert report['face_certification_checks']['two']['status'] == 'unknown'
    assert report['face_certification_status'] == 'unknown'


@pytest.mark.parametrize('frozen_face', [False, None])
def test_face_declaration_remains_bound_to_adopted_source(tmp_path, frozen_face):
    frozen, observed = direct_fixture(tmp_path, (frozen_face, False))
    path = tmp_path / 'video.json'
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['generation_segments'][0]['materials'][0]['contains_realistic_face'] = True
    write_json(path, payload)
    sha = artifacts.digest(path)
    manifest = artifacts.load_manifest(tmp_path)
    next(item for item in manifest['artifacts'] if item['artifact_id'] == 'VIDEO')['checksum'] = sha
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    frozen['source_artifacts'][0]['sha256'] = sha
    observed['frozen_sha256'] = digest(frozen)
    row = observed['drafts'][0]
    row['face_certification'] = [certification_record(row)]

    if frozen_face is False:
        result = canvas_cli(tmp_path, frozen)
        assert result.returncode == 2, result.stdout
        assert '写实人脸声明无效或与采用源段不符' in json.loads(result.stdout)['error']
    else:
        result = canvas_cli(tmp_path, frozen, observed)
        assert result.returncode == 0, result.stdout
        report = json.loads(result.stdout)
        assert report['face_certification_status'] == 'passed'
        assert report['face_certification_checks']['one']['slots'][0]['contains_realistic_face'] is True
