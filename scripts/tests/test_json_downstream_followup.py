"""JSON-only delivery must reach existing consumers and reference scans."""
import json
import pytest
from scripts.core import artifacts, revision_control as rc
from scripts.core.asset_refs import current_files, scan_references
from scripts.core.product_refs import validate_documents
from scripts.core.io import read_json, write_json
from scripts.core.project_state import new_project_state, write_project_state
from scripts.blender_previs import project as previs
from scripts.tests.test_blender_previs_project import project
from scripts.tests.test_delivery_preflight import delivery


def test_json_only_director_reaches_previs_without_markdown(project):
    root, _, _, _, path = project
    data = read_json(path); data.pop('human_readable_path'); write_json(path, data)
    manifest = artifacts.load_manifest(root)
    entry = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'PACKAGE')
    entry['checksum'] = artifacts.digest(path)
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    result = previs.director_input(root, 'S03-150')
    assert result['shot']['shot_id'] == 'S03-150'
    assert result['source_director_text'] is None
    path.write_text('{}')
    with pytest.raises(ValueError): previs.director_input(root, 'S03-150')


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
    rc.materialize_t0(root, change_id='COPY', source_path=source.name,
                      destination_path='equivalent.json', transform='canonical-json')
    rc.record_candidate(root, change_id='COPY', artifact_id='EQUIVALENT', logical_id='video.prompt',
                        artifact_type='json', candidate_path='equivalent.json', revision=frozen['revision'])


@pytest.mark.parametrize('phase', ['confirmation', 'adoption'])
def test_t0_does_not_exempt_upstream_byte_checks(tmp_path, phase):
    t0(tmp_path)
    if phase == 'adoption':
        rc.confirm_candidate(tmp_path, change_id='COPY', approval_ref='fixture:user')
    (tmp_path / 'frame.png').write_bytes(b'changed-after-copy')
    with pytest.raises(rc.RevisionControlError, match='参考检查失败'):
        if phase == 'confirmation': rc.confirm_candidate(tmp_path, change_id='COPY', approval_ref='fixture:user')
        else: rc.commit_change(tmp_path, change_id='COPY')


def test_t0_equivalent_copy_still_needs_no_repeat_review(tmp_path):
    t0(tmp_path)
    assert read_json(tmp_path / rc.CONTROL_RELATIVE)['active_change']['review'] is None
    rc.confirm_candidate(tmp_path, change_id='COPY', approval_ref='fixture:user')
    rc.commit_change(tmp_path, change_id='COPY')
