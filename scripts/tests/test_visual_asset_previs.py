"""Derived preview reads preserve source binding and never expose arbitrary files."""
import hashlib
import base64
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import visual_asset_previs as p


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


@pytest.fixture
def preview(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    binding = root / '05_分镜/预演/逐镜/SHOT-01_R01/shot-input.json'
    write(binding, {'shot': {'shot_id': 'SHOT-01'}, 'reference_revision': 'R01'})
    recipe = binding.parent / 'recipe.json'
    write(recipe, {'fps': 24})
    folder = root / p.PREVIEW_FOLDER / 'SHOT-01_R01'
    folder.mkdir(parents=True)
    glb = folder / 'preview.glb'
    glb.write_bytes(b'local-preview-model')
    matrix = [float(i % 5 == 0) for i in range(16)]
    data = {'schema_version': '1.0', 'shot_id': 'SHOT-01', 'revision': 'R01',
            'fps': 24, 'resolution': [1920, 1080], 'duration_seconds': 1,
            'actors': [{'id': 'chr.a', 'label': '人物 A', 'color': '#123456'}],
            'bounds': {'min': [-2, 0, -2], 'max': [2, 2, 2]},
            'frames': [{'time': t, 'matrix_world': matrix, 'projection_matrix': matrix} for t in (0, 1)],
            'sources': [{'role': role, 'path': path.relative_to(root).as_posix(), 'sha256': p.digest_file(path)}
                        for role, path in (('binding', binding), ('recipe', recipe))],
            'glb': {'path': 'preview.glb', 'sha256': p.digest_file(glb)}}
    write(folder / 'preview_manifest.json', data)
    checked = []
    monkeypatch.setattr(p, 'check_binding', lambda project, value: checked.append((project, value)))
    library = SimpleNamespace(projects=SimpleNamespace(get=lambda ident: {'path': str(root)}
                              if ident == 'known-project' else (_ for _ in ()).throw(ValueError('未连接项目'))))
    return root, folder, data, library, recipe, checked


def test_pending_list_detail_and_model_consumption_check_binding(preview):
    root, folder, data, library, _, checked = preview
    listed = p.list_previews(library, 'known-project')['previews']
    assert listed == [{'id': 'SHOT-01_R01', 'shot_id': 'SHOT-01', 'revision': 'R01',
                       'status': 'pending', 'error': '', 'duration_seconds': 1}]
    result = p.detail(library, 'known-project', 'SHOT-01_R01')
    assert result['manifest'] == data
    assert result['status'] == 'pending'
    assert result['glb_url'] == ('/previs-media/known-project/SHOT-01_R01/preview.glb'
                                 '?manifest_sha256=' + result['manifest_sha256'])
    assert checked == []
    assert p.model_bytes(library, 'known-project', 'SHOT-01_R01') == b'local-preview-model'
    assert len(checked) == 1
    assert all(project == root for project, _ in checked)


def test_recipe_and_current_binding_changes_make_preview_stale(preview, monkeypatch):
    root, _, _, library, recipe, _ = preview
    recipe.write_text('changed', encoding='utf-8')
    result = p.inspect_preview(root, 'SHOT-01_R01')
    assert result['status'] == 'stale' and 'glb_url' not in result
    with pytest.raises(ValueError, match='来源文件已变化'):
        p.model_bytes(library, 'known-project', 'SHOT-01_R01')
    recipe.write_text(json.dumps({'fps': 24}), encoding='utf-8')
    def changed(*args):
        raise p.ProjectPrevisError('导演修订已更换')
    monkeypatch.setattr(p, 'check_binding', changed)
    result = p.inspect_preview(root, 'SHOT-01_R01')
    assert result['status'] == 'stale' and '导演修订已更换' in result['error']
    assert p.list_previews(library, 'known-project')['previews'][0]['status'] == 'pending'
    with pytest.raises(ValueError, match='导演修订已更换'):
        p.model_bytes(library, 'known-project', 'SHOT-01_R01')


@pytest.mark.parametrize('field,bad_value', [('bounds', None), ('sources', [None]), ('sources', {}),
                                           ('frames', [None]), ('frames', 'bad')])
def test_malformed_nested_fields_do_not_block_preview_list(preview, field, bad_value):
    _, folder, data, library, _, _ = preview
    data[field] = bad_value
    write(folder.parent / 'BROKEN/preview_manifest.json', data)
    results = {r['id']: r for r in p.list_previews(library, 'known-project')['previews']}
    assert results['BROKEN']['status'] == 'pending'
    assert p.detail(library, 'known-project', 'BROKEN')['status'] == 'error'
    assert results['SHOT-01_R01']['status'] == 'pending'


def test_bad_preview_and_export_failure_do_not_block_other_shots(preview):
    root, folder, _, library, _, _ = preview
    write(folder.parent / 'BROKEN/preview_manifest.json', {'schema_version': '1.0', 'shot_id': 'bad'})
    write(folder.parent / 'FAILED/export_error.json', {'shot_id': 'SHOT-02', 'revision': 'R01', 'error': '实际导出失败'})
    results = {r['id']: r for r in p.list_previews(library, 'known-project')['previews']}
    assert results['SHOT-01_R01']['status'] == 'pending'
    assert results['BROKEN']['status'] == 'error'
    assert results['FAILED']['shot_id'] == 'SHOT-02'
    assert results['FAILED']['error'] == '实际导出失败'
    write(folder / 'export_error.json', {'error': '重新导出失败'})
    assert p.detail(library, 'known-project', 'SHOT-01_R01')['status'] == 'error'


@pytest.mark.parametrize('raw', ['../../outside.json', '/outside.json', 'C:/outside.json', '..\\outside.json'])
def test_arbitrary_source_paths_are_rejected(preview, raw):
    _, folder, data, library, _, _ = preview
    data['sources'][1]['path'] = raw
    write(folder / 'preview_manifest.json', data)
    result = p.detail(library, 'known-project', 'SHOT-01_R01')
    assert result['status'] == 'error' and 'glb_url' not in result
    with pytest.raises(ValueError):
        p.model_bytes(library, 'known-project', 'SHOT-01_R01')


def test_model_change_and_unconnected_or_traversal_requests_are_rejected(preview):
    _, folder, _, library, _, _ = preview
    (folder / 'preview.glb').write_bytes(b'changed')
    assert p.detail(library, 'known-project', 'SHOT-01_R01')['status'] == 'pending'
    with pytest.raises(ValueError, match='模型已变化'):
        p.model_bytes(library, 'known-project', 'SHOT-01_R01')
    with pytest.raises(ValueError, match='未连接项目'):
        p.list_previews(library, 'outside')
    with pytest.raises(ValueError, match='编号'):
        p.detail(library, 'known-project', '../../outside')


def test_symlinks_cannot_leave_preview_or_project(preview, tmp_path):
    _, folder, data, library, _, _ = preview
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.json').write_text('secret', encoding='utf-8')
    try:
        (folder.parent / 'LINK').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('本机无创建符号链接权限')
    result = p.detail(library, 'known-project', 'LINK')
    assert result['status'] == 'error' and '越界' in result['error']
    data['sources'][1]['path'] = (folder.parent / 'LINK/secret.json').relative_to(folder.parents[3]).as_posix()
    write(folder / 'preview_manifest.json', data)
    result = p.detail(library, 'known-project', 'SHOT-01_R01')
    assert result['status'] == 'error'


def test_http_routes_keep_host_guard_and_static_whitelist(preview):
    from scripts import visual_asset_library as server
    _, _, _, library, _, _ = preview
    handler = object.__new__(server.Handler)
    handler.server = SimpleNamespace(library=library, server_port=8766)
    handler.headers = {'Host': '127.0.0.1:8766'}
    replies = []
    handler.reply = lambda data, status=200, content_type=None: replies.append((data, status, content_type))
    for path in ('/api/previs?project_id=known-project',
                 '/api/previs?project_id=known-project&preview_id=SHOT-01_R01',
                 '/previs-media/known-project/SHOT-01_R01/preview.glb'):
        handler.path = path
        handler.do_GET()
        assert replies[-1][1] == 200
    assert replies[-1][2] == 'model/gltf-binary'
    handler.path = '/vendor/three/../../visual_asset_library.py'
    handler.do_GET()
    assert replies[-1][1] == 404
    handler.headers['Origin'] = 'https://foreign.invalid'
    handler.path = '/api/previs?project_id=known-project'
    handler.do_GET()
    assert replies[-1][1] == 403


def test_html_csp_allows_exact_importmap_without_other_inline_scripts():
    from scripts import visual_asset_library as server
    handler = object.__new__(server.Handler)
    handler.server = SimpleNamespace(server_port=8766)
    handler.headers = {'Host': '127.0.0.1:8766'}
    handler.path = '/'
    headers = {}
    statuses = []
    handler.send_response = statuses.append
    handler.send_header = lambda name, value: headers.update({name: value})
    handler.end_headers = lambda: None
    handler.wfile = io.BytesIO()
    handler.do_GET()
    assert statuses == [200]
    html = handler.wfile.getvalue().decode('utf-8')
    maps = re.findall(r'<script type="importmap">(.*?)</script>', html, re.S)
    assert len(maps) == 1
    digest = base64.b64encode(hashlib.sha256(maps[0].encode('utf-8')).digest()).decode('ascii')
    assert digest == server.PREVIS_IMPORTMAP_SHA256
    script_policy = next(part.strip() for part in headers['Content-Security-Policy'].split(';')
                         if part.strip().startswith('script-src '))
    assert script_policy == f"script-src 'self' 'sha256-{digest}'"
    assert "'unsafe-inline'" not in script_policy


@pytest.mark.parametrize(
    ('search_root', 'module'),
    [('.', 'scripts.visual_asset_previs'), ('scripts', 'visual_asset_previs')],
)
def test_previs_imports_without_pytest_path_injection(search_root, module):
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, '-I', '-B', '-c',
         'import importlib, sys; sys.path.insert(0, sys.argv[1]); importlib.import_module(sys.argv[2])',
         str(repo / search_root), module],
        cwd=repo, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_binding_is_parsed_from_the_same_bytes_checked(preview, monkeypatch):
    root, folder, data, library, _, checked = preview
    binding = root / data['sources'][0]['path']
    original = Path.read_bytes
    reads = []
    def replaced_after_read(path):
        value = original(path)
        if path == binding:
            reads.append(path)
            write(binding, {'shot': {'shot_id': 'WRONG'}, 'reference_revision': 'R02'})
        return value
    monkeypatch.setattr(Path, 'read_bytes', replaced_after_read)
    assert p.model_bytes(library, 'known-project', folder.name) == b'local-preview-model'
    assert len(reads) == 1
    assert checked[0][1]['shot']['shot_id'] == 'SHOT-01'
    with pytest.raises(ValueError, match='来源文件已变化'):
        p.model_bytes(library, 'known-project', folder.name)


def test_detail_identity_rejects_camera_manifest_changed_before_model(preview):
    _, folder, data, library, _, checked = preview
    result = p.detail(library, 'known-project', folder.name)
    data['fps'] += 1
    write(folder / 'preview_manifest.json', data)
    with pytest.raises(ValueError, match='预览记录在载入时变化'):
        p.model_bytes(library, 'known-project', folder.name, result['manifest_sha256'])
    assert checked == []
