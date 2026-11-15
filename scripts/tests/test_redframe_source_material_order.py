"""Exercise the adopted source slot order in the formal RedFrame CLI."""

import copy
import json

import pytest

from scripts.canvas_readback import digest, validate_source_material_order
from scripts.core import artifacts
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state, write_project_state
from scripts.tests.test_delivery_preflight import bind_synthetic_quality
from scripts.tests.test_workflow_repair_cli import (
    bind_redframe_production_sources,
    canvas_cli,
    redframe_fixture,
)


@pytest.mark.parametrize('mutation, error', [
    (None, None),
    ('reorder', '冻结第 1 槽的素材字节或顺序与采用源段不符'),
    ('missing', '缺少采用源段指定的素材字节'),
    ('extra', '含采用源段未列出的素材槽'),
])
def test_formal_redframe_compares_source_slots_in_preflight_and_readback(tmp_path, mutation, error):
    from PIL import Image

    frozen, observed = redframe_fixture(tmp_path)
    costume = tmp_path / 'costume.png'
    Image.new('RGB', (8, 8), 'yellow').save(costume)
    sha = artifacts.digest(costume)
    native = dict(node_id='costume-node', media_id='costume-media')
    manifest = artifacts.load_manifest(tmp_path)
    manifest['artifacts'].append(dict(
        artifact_id='COSTUME', logical_id='COSTUME', revision='R01', status='current',
        user_approved=True, path_or_url=costume.name, checksum=sha,
        upload_bindings=[dict(target_url=frozen['target_url'], revision='R01', sha256=sha, **native)],
    ))
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    state = load_project_state(tmp_path)
    state['current_artifacts'].append('COSTUME')
    state['user_confirmations'].append(dict(artifact_ids=['COSTUME']))
    write_project_state(tmp_path, state)

    for row in frozen['drafts']:
        row['edges'] = []
        row['source_nodes'] = []
        row['reference_checks'][0]['canvas_binding'] = dict(
            mode='direct', purpose='synthetic continuity', prompt_reference=True)
    row = frozen['drafts'][0]
    row['ordered_media'].append(native)
    row['reference_checks'].append(dict(
        artifact_id='COSTUME', revision='R01', sha256=sha, kind='image', role='appearance',
        entity_ids=['ACTOR'], canvas_binding=dict(
            mode='direct', purpose='synthetic costume', prompt_reference=True),
    ))
    bind_redframe_production_sources(tmp_path, frozen, observed)

    if mutation == 'reorder':
        row['ordered_media'].reverse()
        row['reference_checks'].reverse()
    elif mutation == 'missing':
        row['ordered_media'].pop()
        row['reference_checks'].pop()
    elif mutation == 'extra':
        extra = copy.deepcopy(row['reference_checks'][-1])
        extra['canvas_binding']['purpose'] = 'independent scale comparison using the same costume file'
        row['ordered_media'].append(native)
        row['reference_checks'].append(extra)

    planned = copy.deepcopy(frozen)
    for draft in planned['drafts']:
        draft.pop('ordered_media')
    bind_synthetic_quality(tmp_path, planned)
    preflight = canvas_cli(tmp_path, planned)

    bind_synthetic_quality(tmp_path, frozen)
    observed['drafts'][0] = copy.deepcopy(row)
    observed['drafts'][0]['native_mentions'] = [dict(
        **item, entity_type='fixture-chip', native_noneditable_entity=True) for item in row['ordered_media']]
    observed['frozen_sha256'] = digest(frozen)
    reopened = canvas_cli(tmp_path, frozen, observed)

    for result in (preflight, reopened):
        assert result.returncode == (0 if error is None else 2), result.stdout
        if error:
            assert error in json.loads(result.stdout)['error']


def test_source_material_slots_require_verifiable_sha_and_consistent_explicit_index():
    sha = 'a' * 64
    row = dict(node_id='one', reference_checks=[dict(sha256=sha)])
    validate_source_material_order(row, dict(candidate_upload_order=[dict(sha256=sha, upload_index=1)]))
    with pytest.raises(ValueError, match='缺准确素材 SHA-256'):
        validate_source_material_order(row, dict(materials=[dict(order=1)]))
    with pytest.raises(ValueError, match='与声明的素材顺序不符'):
        validate_source_material_order(row, dict(materials=[dict(sha256=sha, order=2)]))
