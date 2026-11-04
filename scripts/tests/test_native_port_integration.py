"""Synthetic local integration only; never a live binding or approval claim."""
import copy
import hashlib
import json

import pytest

from scripts.canvas_readback import compare, digest, validate_submission
from scripts.core import artifacts
from scripts.core.io import write_json
from scripts.core.native_references import reference_readback_status
from scripts.tests.test_delivery_preflight import delivery, bind_synthetic_quality
from scripts.tests.test_private_canvas_preview import preview, seal
from scripts.tests.test_native_port_binding import synthetic, persist


def port_delivery(root, private):
    frozen = preview(root) if private else delivery(root)[0]
    frozen['target_project_id'] = 'exact'
    bindings = []
    for index, row in enumerate(frozen['drafts']):
        folder = root / ('synthetic-evidence-' + str(index)); folder.mkdir()
        binding, expected, reports = synthetic(folder)
        ref = row['reference_checks'][0]
        row.update(native_entity_type='image', native_identity_mode='node_port_download',
                   source_segment_id='SYNTHETIC-' + str(index), source_shot_ids=['SYNTHETIC-SHOT-' + str(index)])
        media = dict(node_id='frame-node', reference_port='image', sha256=ref['sha256'])
        row['ordered_media'] = [media]
        expected.update(target_url=frozen['target_url'], target_project_id='exact', draft_revision=frozen['revision'],
                        draft_node_id=row['node_id'], source_segment_id=row['source_segment_id'],
                        node_id=media['node_id'], revision=ref['revision'], sha256=ref['sha256'])
        binding.update(expected)
        data = (root / 'frame.png').read_bytes()
        (folder / 'download.bin').write_bytes(data)
        binding['download']['sha256'] = hashlib.sha256(data).hexdigest()
        scope, post, slots, ports = (reports[k] for k in ('scope', 'post_refresh', 'slot_downloads', 'source_ports'))
        scope.update(url=frozen['target_url'], workspace_directory='exact', project=dict(title='exact', outerHTML='<input title="exact">'))
        scope.pop('SYNTHETIC-09'); scope[row['source_segment_id']] = row['node_id']
        draft = post.pop('SYNTHETIC-09'); post['url'] = frozen['target_url']
        draft['id'] = row['node_id']; post[row['source_segment_id']] = draft
        mention = draft['mentions'][0]
        mention['decoded'][0] = 'frame-node'
        mention['raw_reference_id'] = '%5B%22frame-node%22%2C%22image%22%5D'
        receipt = slots['checks'].pop('SYNTHETIC-09')
        receipt['bound_media'][0].update(path=str(folder / 'download.bin'), native=copy.deepcopy(mention), sha256=ref['sha256'], expected_sha256=ref['sha256'])
        slots['checks'][row['source_segment_id']] = receipt
        ports['sources'][0]['id'] = 'frame-node'
        persist(folder, reports, binding)
        for entry in [binding['download'], *binding['evidence'].values()]:
            entry['path'] = str(folder.relative_to(root) / entry['path'])
        bindings.append(binding)
        if private:
            ref['upload_binding'] = binding
    (root / 'director.json').write_text(json.dumps({'fixture': 'synthetic only',
        'scenes': [{'scene_id': 'SYNTHETIC-SCENE-' + str(index),
                    'shots': [{'shot_id': row['source_shot_ids'][0]}]}
                   for index, row in enumerate(frozen['drafts'])],
        'generation_segments': [{'segment_id': row['source_segment_id'], 'source_shot_ids': row['source_shot_ids']}
                                for row in frozen['drafts']]}))
    manifest = artifacts.load_manifest(root)
    source = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'DIRECTOR')
    source['checksum'] = source['sha256'] = artifacts.digest(root / 'director.json')
    frozen['source_artifacts'][0]['sha256'] = source['checksum']
    if private:
        frozen['source_artifacts'][0]['record_sha256'] = source['checksum']
    if not private:
        next(a for a in manifest['artifacts'] if a['artifact_id'] == 'FRAME')['upload_bindings'] = bindings
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    bind_synthetic_quality(root, frozen)
    if private: seal(frozen)
    observed = copy.deepcopy(frozen)
    observed.update(phase='after_refresh', frozen_sha256=digest(frozen))
    for row in observed['drafts']:
        row['native_mentions'] = [dict(node_id=m['node_id'], reference_port=m['reference_port'],
                                      download_sha256=m['sha256'], entity_type='image',
                                      native_noneditable_entity=True) for m in row['ordered_media']]
    return frozen, observed


@pytest.mark.parametrize('private', [False, True])
def test_synthetic_port_full_integration(tmp_path, private):
    frozen, observed = port_delivery(tmp_path, private)
    result = compare(frozen, observed, tmp_path, private_preview=private)
    assert result['ok'] and result['production_ready'] is False
    assert set(result['native_reference_checks'].values()) == {'passed'}
    with pytest.raises(ValueError, match='--project'):
        compare(frozen, observed, private_preview=private)
    observed['drafts'][0]['native_mentions'][0]['download_sha256'] = '0' * 64
    assert not compare(frozen, observed, tmp_path, private_preview=private)['ok']


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('mutation', ['evidence', 'slot', 'hash', 'mix_media_id'])
def test_synthetic_binding_rejections(tmp_path, private, mutation):
    frozen, _ = port_delivery(tmp_path, private)
    if private:
        binding = frozen['drafts'][0]['reference_checks'][0]['upload_binding']
    else:
        manifest = artifacts.load_manifest(tmp_path)
        binding = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'FRAME')['upload_bindings'][0]
    if mutation == 'evidence': binding.pop('evidence')
    elif mutation == 'slot': binding['slot'] = 2
    elif mutation == 'hash': binding['download']['sha256'] = '0' * 64
    elif mutation == 'mix_media_id': binding['media_id'] = 'image'
    if private: seal(frozen)
    else: write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path, private_preview=private)


def test_mode_does_not_relax_legacy_or_production_guard(tmp_path):
    expected = [dict(node_id='n', media_id='m')]
    mention = [dict(node_id='n', entity_type='chip', native_noneditable_entity=True, reference_port='image', download_sha256='0' * 64)]
    assert reference_readback_status(expected, mention, 'chip') == 'failed'
    mention[0]['media_id'] = 'm'
    assert reference_readback_status(expected, mention, 'chip') == 'passed'
    frozen, _ = port_delivery(tmp_path, True)
    frozen.pop('preview_authorization')
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('mutation', ['audio', 'video', 'foreign_entity'])
def test_image_port_contract_is_not_a_generic_media_adapter(tmp_path, private, mutation):
    frozen, _ = port_delivery(tmp_path, private)
    row = frozen['drafts'][0]
    if mutation == 'foreign_entity': row['native_entity_type'] = 'other-chip'
    else: row['reference_checks'][0]['kind'] = mutation
    bind_synthetic_quality(tmp_path, frozen)
    if private: seal(frozen)
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path, private_preview=private)
