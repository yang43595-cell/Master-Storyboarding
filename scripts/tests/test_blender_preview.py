"""Preview export refuses stale staging and never changes project adoption state."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.blender_previs import preview as p
from scripts import blender_previs_cli as cli


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def staged(tmp_path, monkeypatch):
    root = tmp_path / 'project'; root.mkdir()
    folder = root / '05_分镜/预演/布置/S01_R01'; folder.mkdir(parents=True)
    (folder / 'previs.blend').write_bytes(b'fixture-not-a-render')
    binding = {'shot': {'shot_id': 'S01'}, 'scene_base': {'path': 'base.json'}, 'reference_revision': 'R01'}
    recipe = {'project': {'duration_seconds': 1}, 'camera': {'shots': [{'id': 'S01', 'start': 0, 'end': 1}]}}
    write(folder / 'shot-input.json', binding); write(folder / 'resolved.recipe.json', recipe)
    write(root / 'base.json', {'files': [{'role': 'scene', 'sha256': 'base-hash'}]})
    staging = {'ok': True, 'room_geometry_unchanged': True, 'source_scene_sha256': 'base-hash',
               'staged_scene_sha256': p.file_sha256(folder / 'previs.blend'),
               'recipe_sha256': p.file_sha256(folder / 'resolved.recipe.json'),
               'binding_sha256': p.file_sha256(folder / 'shot-input.json')}
    write(folder / 'stage_manifest.json', staging)
    monkeypatch.setattr(p.project, 'check_binding', lambda *a: None)
    return root, folder


def test_staged_files_are_all_bound(staged):
    root, folder = staged
    binding, recipe, records, scene = p.stage_inputs(root, str(folder))
    assert scene == folder / 'previs.blend'
    assert {r['role'] for r in records} == {'scene', 'recipe', 'binding', 'staging'}
    assert binding['shot']['shot_id'] == 'S01'


@pytest.mark.parametrize('changed', ['previs.blend', 'resolved.recipe.json', 'shot-input.json'])
def test_changed_staging_refuses_preview(staged, changed):
    root, folder = staged
    path = folder / changed
    path.write_bytes(path.read_bytes() + (b' ' if path.suffix == '.json' else b'changed'))
    with pytest.raises(p.project.ProjectPrevisError, match='指纹'):
        p.stage_inputs(root, str(folder))


def test_legacy_unfingerprinted_staging_requires_new_staging(staged):
    root, folder = staged
    data = p.read(folder / 'stage_manifest.json'); del data['recipe_sha256']
    write(folder / 'stage_manifest.json', data)
    with pytest.raises(p.project.ProjectPrevisError, match='重新 stage-shot'):
        p.stage_inputs(root, str(folder))


@pytest.mark.parametrize('raw', ['elsewhere/preview', '05_分镜/预演/预览/../../wrong', '05_分镜/预演/预览/A/B'])
def test_output_is_a_new_fixed_project_subdirectory(staged, raw):
    root, _ = staged
    with pytest.raises(p.project.ProjectPrevisError):
        p.output_path(root, raw)


def test_failure_is_visible_and_does_not_register_or_confirm(staged, monkeypatch):
    root, folder = staged
    monkeypatch.setattr(cli, 'validate_recipe', lambda *a: None)
    monkeypatch.setattr(cli, 'find_blender', lambda *a: Path('test-blender'))
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=9, stdout='', stderr='bone export failed'))
    args = cli.parser().parse_args(['export-preview', '--project', str(root), '--stage-dir', str(folder),
                                  '--output-dir', '05_分镜/预演/预览/S01_R01'])
    with pytest.raises(cli.PrevisError, match='bone export failed'):
        args.func(args)
    failure = p.read(root / '05_分镜/预演/预览/S01_R01/export_error.json')
    assert failure['shot_id'] == 'S01'
    assert not (root / '00_项目管理').exists()


def test_stage_sources_are_read_and_hashed_once_per_capture(staged, monkeypatch):
    root, folder = staged
    original = Path.open
    counts = {}
    def observed(path, *args, **kwargs):
        if path.parent == folder:
            counts[path.name] = counts.get(path.name, 0) + 1
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', observed)
    _, _, records, _ = p.stage_inputs(root, str(folder))
    assert counts == {name: 1 for name in (
        'previs.blend', 'resolved.recipe.json', 'shot-input.json', 'stage_manifest.json')}
    assert len(records) == 4
