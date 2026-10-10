import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.core.product_refs import validate_documents
from scripts.core.project_state import new_project_state, write_project_state
from scripts.init_desktop_project import initialize


def project_document(tmp_path, text):
    project = initialize(tmp_path / 'project')
    directory = project / '06_视频'
    (directory / 'S01.md').write_text(text, encoding='utf-8-sig')
    return project, directory


def register_product(project, category):
    narrative = {'asset_requirements': [
        {'asset_id': 'AST-BOWL', 'name': '玻璃碗', 'category': category}], 'story_paragraphs': []}
    (project / 'narrative.json').write_text(json.dumps(narrative), encoding='utf-8')
    manifest = {'artifacts': [{'artifact_id': 'narrative-r01', 'logical_id': 'narrative.package',
                              'status': 'current', 'user_approved': True,
                              'path_or_url': 'narrative.json', 'revision': 'R01'}]}
    (project / '00_项目管理' / 'artifact_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    state = new_project_state()
    state.update(current_artifacts=['narrative-r01'],
                 user_confirmations=[{'artifact_ids': ['narrative-r01'], 'at': '2026-09-22T00:00:00Z'}])
    write_project_state(project, state)


def test_ordinary_prop_names_do_not_claim_product_use(tmp_path):
    project, directory = project_document(tmp_path, '普通剧情物品：玻璃碗、硅胶刷、礼盒，放在桌上。')
    assert validate_documents(project, directory) == []


@pytest.mark.parametrize('category,blocked', [('PROP', False), ('REAL_PRODUCT', True)])
def test_same_name_uses_confirmed_category(tmp_path, category, blocked):
    project, directory = project_document(tmp_path, '使用 AST-BOWL 玻璃碗。')
    register_product(project, category)
    assert bool(validate_documents(project, directory)) is blocked


def test_explicit_product_requires_existing_real_reference(tmp_path):
    project, directory = project_document(tmp_path, 'PRODUCT_VISUAL_INPUT: REAL_PRODUCT')
    assert validate_documents(project, directory)
    document = directory / 'S01.md'
    document.write_text('PRODUCT_VISUAL_INPUT: REAL_PRODUCT\n`assets/product-references/bowl.png`', encoding='utf-8')
    assert validate_documents(project, directory)
    reference = project / 'assets/product-references/bowl.png'
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b'reference existence fixture')
    assert validate_documents(project, directory) == []


def test_unconfirmed_asset_category_cannot_be_used(tmp_path):
    project, directory = project_document(tmp_path, '使用 AST-BOWL。')
    register_product(project, 'PROP')
    write_project_state(project, new_project_state())
    assert validate_documents(project, directory)


@pytest.mark.skipif(os.name != 'nt' or not shutil.which('powershell.exe'), reason='Windows project validator')
def test_project_validator_uses_product_category_boundary(tmp_path):
    project, directory = project_document(tmp_path, '普通剧情物品：玻璃碗。')
    # A project without optional voice inputs must still pass the normal gate.
    (project / '07_声音与配音').rmdir()
    validator = Path(__file__).resolve().parents[1] / 'validate-project.ps1'
    command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
               '-File', str(validator), '-ProjectPath', str(project)]
    result = subprocess.run(command, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    (directory / 'S01.md').write_text('PRODUCT_VISUAL_INPUT: REAL_PRODUCT', encoding='utf-8-sig')
    result = subprocess.run(command, capture_output=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    assert b'Product reference validation:' in result.stdout


def test_relocated_repository_product_reference_uses_shared_path_resolution(tmp_path, monkeypatch):
    source = 'C:/Users/Owner/Documents/film-tools'
    repository = tmp_path / 'repo'
    reference = repository / 'assets/product-references/bowl.png'
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b'reference existence fixture')
    project, directory = project_document(tmp_path,
        'PRODUCT_VISUAL_INPUT: REAL_PRODUCT\n`' + source + '/assets/product-references/bowl.png`')
    monkeypatch.setenv('MSB_PROJECT_PATH_MAP', json.dumps({source: str(repository)}))
    assert validate_documents(project, directory) == []
    reference.unlink()
    assert validate_documents(project, directory)  # Mapping never substitutes absent bytes.


@pytest.mark.parametrize('asset_id', ['AST-BANQUET-HALL', 'AST-PRINCESS-2'])
def test_asset_ids_in_exported_filenames_are_not_truncated(tmp_path, asset_id):
    project, directory = project_document(tmp_path, f'已选素材 [{asset_id}_master.png](./{asset_id}_master.png)')
    register_product(project, 'PROP')
    path = project / 'narrative.json'
    narrative = json.loads(path.read_text())
    narrative['asset_requirements'][0]['asset_id'] = asset_id
    path.write_text(json.dumps(narrative), encoding='utf-8')
    assert validate_documents(project, directory) == []
