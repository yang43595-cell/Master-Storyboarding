"""Synthetic direct-consumer and persisted/reloaded download-claim regressions."""
import json

import pytest

from scripts.canvas_readback import validate_submission
from scripts.core import artifacts, quality_evidence as q
from scripts.tests.test_native_port_integration import port_delivery
from scripts.tests.test_endpoint_integration import integrated
from scripts.tests.test_private_canvas_preview import seal


BUILDERS = {'node_port_download': port_delivery, 'first_last_frame_download': integrated}


def persisted(root, mode, private=False):
    frozen, observed = BUILDERS[mode](root, private)
    row = frozen['drafts'][0]
    path = root / 'verified.json'
    path.write_text(json.dumps(observed))
    row['native_binding'] = dict(status='persisted_readback_verified', revision=frozen['revision'],
        input_sha256=q.fingerprint(q.native_input_binding(row)), evidence_path=path.name,
        evidence_sha256=artifacts.digest(path), canvas_url=frozen['target_url'], node_id=row['node_id'])
    row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))
    if private: seal(frozen)
    return frozen, observed


@pytest.mark.parametrize('mode', BUILDERS)
@pytest.mark.parametrize('private', [False, True])
def test_exact_claim_survives_disk_reload_and_preflight(tmp_path, mode, private):
    frozen, _ = persisted(tmp_path, mode, private)
    path = tmp_path / 'frozen.json';path.write_text(json.dumps(frozen))
    reloaded = json.loads(path.read_text())
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    q.validate_native_binding_claims(reloaded, tmp_path)
    assert validate_submission(reloaded, tmp_path, private_preview=private)['ok']
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('mode', BUILDERS)
@pytest.mark.parametrize('mutation', ['prompt', 'parameters', 'media', 'revision', 'project', 'unknown_mode',
                                      'missing_raw', 'wrong_local_bytes', 'observed_identity', 'observation_hash',
                                      'phase', 'observation_project', 'missing_project', 'mode_only', 'reference_hash', 'missing_ref'])
def test_claim_is_not_an_escape_from_raw_or_current_scope(tmp_path, mode, mutation):
    frozen, observed = persisted(tmp_path, mode)
    row = frozen['drafts'][0]
    if mutation == 'prompt': row['prompt'] += ' changed'
    elif mutation == 'parameters': row['parameters']['duration'] += 1
    elif mutation == 'media': row['ordered_media'][0]['sha256'] = '0' * 64
    elif mutation == 'revision': frozen['revision'] = 'wrong'
    elif mutation == 'project': frozen['target_project_id'] = 'wrong'
    elif mutation == 'missing_project': frozen.pop('target_project_id')
    elif mutation == 'unknown_mode': row['native_identity_mode'] = 'unrecognized'
    elif mutation == 'reference_hash': row['reference_checks'][0]['sha256'] = '0' * 64
    elif mutation == 'missing_ref': row['reference_checks'].pop()
    elif mutation in ('missing_raw', 'wrong_local_bytes'):
        if mode == 'first_last_frame_download':
            control = row['state_control']['platform']
            descriptor = control['evidence']['edges'] if mutation == 'missing_raw' else control['downloads'][0]['input']
        else:
            item = next(a for a in artifacts.load_manifest(tmp_path)['artifacts'] if a['artifact_id'] == row['reference_checks'][0]['artifact_id'])
            control = item['upload_bindings'][0]
            descriptor = control['evidence']['post_refresh'] if mutation == 'missing_raw' else control['download']
        path = tmp_path / descriptor['path']
        if mutation == 'missing_raw': path.unlink()
        else: path.write_bytes(b'wrong bytes')
    elif mutation == 'observed_identity':
        obs = observed['drafts'][0]
        if mode == 'first_last_frame_download': obs['frame_slots'].reverse()
        else: obs['native_mentions'][0]['download_sha256'] = '0' * 64
    elif mutation == 'observation_hash': (tmp_path/'verified.json').write_text('{}')
    elif mutation == 'phase': observed['phase'] = 'before_refresh'
    elif mutation == 'observation_project': observed['target_project_id'] = 'wrong'
    elif mutation == 'mode_only':
        if mode == 'first_last_frame_download': row['state_control']['platform'] = {'kind': 'first_last_frame_download'}
        else:
            manifest=artifacts.load_manifest(tmp_path)
            next(a for a in manifest['artifacts'] if a['artifact_id']==row['reference_checks'][0]['artifact_id'])['upload_bindings']=[]
            (tmp_path/artifacts.MANIFEST_PATH).write_text(json.dumps(manifest))
    if mutation in ('observed_identity', 'phase', 'observation_project'):
        (tmp_path/'verified.json').write_text(json.dumps(observed))
        row['native_binding']['evidence_sha256'] = artifacts.digest(tmp_path/'verified.json')
    if mutation in ('unknown_mode', 'mode_only', 'reference_hash', 'missing_ref'):
        row['native_binding']['input_sha256'] = q.fingerprint(q.native_input_binding(row))
    with pytest.raises(ValueError): q.validate_native_binding_claims(frozen, tmp_path)
