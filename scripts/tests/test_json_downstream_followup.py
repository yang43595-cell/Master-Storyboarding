"""JSON-only delivery must reach existing consumers and reference scans."""
import json
import pytest
from scripts.core import artifacts, revision_control as rc
from scripts.core.asset_refs import current_files, scan_references
from scripts.core.product_refs import validate_documents
from scripts.core.io import read_json, write_json
from scripts.core.project_state import new_project_state, write_project_state
from scripts.tests.test_delivery_preflight import delivery


@pytest.mark.parametrize('folder', ['05_分镜', '06_视频'])
def test_asset_scan_includes_json_body_and_structured_ids(tmp_path, folder):
    directory = tmp_path / folder; directory.mkdir()
    file = directory / 'active.json'
    write_json(file, {'prompt': 'actor holds AST-CUP', 'source_asset_requirement_ids': ['AST-CUP']})
    assert file in current_files(tmp_path)
    listing = {'items': [{'asset_id': 'AST-CUP', 'name': 'cup', 'state': 'excluded'}]}
    report = scan_references(tmp_path, ['AST-CUP'], listing)
    hits = report['assets'][0]['reference_hits']
    assert hits and any(h['kind'] == 'reference' for h in hits)


@pytest.mark.parametrize('value', [
    {'PRODUCT_VISUAL_INPUT': 'REAL_PRODUCT'},
    {'references': [{'category': 'REAL_PRODUCT'}]},
    {'prompt': 'PRODUCT_VISUAL_INPUT: REAL_PRODUCT'},
])
def test_explicit_product_json_requires_real_reference(tmp_path, value):
    directory = tmp_path / '06_视频'; directory.mkdir()
    path = directory / 'current.json'; write_json(path, value)
    assert validate_documents(tmp_path, directory)
    reference = tmp_path / 'assets/product-references/bowl.png'; reference.parent.mkdir(parents=True)
    reference.write_bytes(b'fixture')
    value['reference_paths'] = ['assets/product-references/bowl.png']; write_json(path, value)
    assert validate_documents(tmp_path, directory) == []
    reference.unlink()
    assert validate_documents(tmp_path, directory)


@pytest.mark.parametrize(("candidate", "tier"), [
    ('{"nested":{"empty":{"obj":{},"list":[],"str":"","null":null},"items":[true,0,0]}}', 'T0'),
    ('{"nested":{"items":[1,0,0],"empty":{"null":null,"str":"","list":[],"obj":{}}}}', 'T2'),
    ('{"nested":{"items":[0,true,0],"empty":{"null":null,"str":"","list":[],"obj":{}}}}', 'T2'),
    ('{"nested":{"items":[true,0],"empty":{"null":null,"str":"","list":[],"obj":{}}}}', 'T2'),
    ('{"nested":{"items":[true,0,0],"empty":{"null":"","str":"","list":[],"obj":{}}}}', 'T2'),
    ('{"nested":{"items":[true,0,0],"empty":{"null":null,"str":"","list":{},"obj":{}}}}', 'T2'),
])
def test_inspect_change_compares_canonical_json(tmp_path, candidate, tier):
    source = tmp_path / 'current.json'
    write_json(source, {'nested': {'items': [True, 0, 0],
                                  'empty': {'null': None, 'str': '', 'list': [], 'obj': {}}}})
    (tmp_path / 'candidate.json').write_text(candidate, encoding='utf-8')
    write_json(tmp_path / artifacts.MANIFEST_PATH, {'artifacts': [{
        'artifact_id': 'SOURCE', 'logical_id': 'video.prompt', 'status': 'current',
        'revision': 'R01', 'path_or_url': source.name, 'checksum': artifacts.digest(source),
    }]})
    assert rc.inspect_change(tmp_path, ['video.prompt'], 'candidate.json')['tier'] == tier


def t0(root):
    frozen, _ = delivery(root)
    source = root / 'old-delivery.json'; write_json(source, frozen)
    manifest = artifacts.load_manifest(root)
    manifest['artifacts'].append({'artifact_id': 'OLD-DELIVERY', 'logical_id': 'video.prompt',
        'path_or_url': source.name, 'revision': frozen['revision'], 'status': 'current',
        'user_approved': True, 'checksum': artifacts.digest(source)})
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    state = read_json(root / '00_项目管理/project_state.json')
    state['current_artifacts'].append('OLD-DELIVERY')
    state['user_confirmations'].append({'artifact_ids': ['OLD-DELIVERY']})
    write_project_state(root, state)
    rc.open_change(root, change_id='COPY', tier='T0', logical_ids=['video.prompt'], reason='equivalent copy')
    output = rc.materialize_t0(root, change_id='COPY', source_path=source.name,
                               destination_path='equivalent.json', transform='canonical-json')
    equivalent = root / output['path']
    assert equivalent.read_bytes() == source.read_bytes()
    assert output['sha256'] == artifacts.digest(equivalent)
    rc.record_candidate(root, change_id='COPY', artifact_id='EQUIVALENT', logical_id='video.prompt',
                        artifact_type='json', candidate_path='equivalent.json', revision=frozen['revision'])


@pytest.mark.parametrize('phase', ['confirmation', 'adoption'])
def test_t0_channel_snapshot_cannot_be_adopted_as_platform_neutral_prompt(tmp_path, phase):
    t0(tmp_path)
    with pytest.raises(rc.RevisionControlError, match='渠道冻结快照'):
        rc.confirm_candidate(tmp_path, change_id='COPY', approval_ref='fixture:user')
    manifest = artifacts.load_manifest(tmp_path)
    assert next(a for a in manifest['artifacts'] if a['artifact_id'] == 'EQUIVALENT')['status'] == 'candidate'
    assert 'EQUIVALENT' not in read_json(tmp_path / '00_项目管理/project_state.json')['current_artifacts']


def test_t0_equivalent_channel_copy_still_cannot_become_formal_prompt(tmp_path):
    t0(tmp_path)
    assert read_json(tmp_path / rc.CONTROL_RELATIVE)['active_change']['review'] is None
    with pytest.raises(rc.RevisionControlError, match='渠道冻结快照'):
        rc.confirm_candidate(tmp_path, change_id='COPY', approval_ref='fixture:user')
