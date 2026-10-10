"""Regression for protected output directories and render evidence reuse."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import blender_previs_cli as cli
from scripts.blender_previs import output_contract as outputs


@pytest.mark.parametrize("entry", ["build", "reconstruct-scene", "image-environment"])
def test_every_build_entry_refuses_nonempty_output_before_writes_or_launch(tmp_path, monkeypatch, entry):
    occupied = tmp_path / "output"
    occupied.mkdir()
    protected = occupied / "confirmed.blend"
    protected.write_bytes(b"CONFIRMED")
    recipe = tmp_path / "recipe.json"
    source = tmp_path / "reference.png"
    source.write_bytes(b"reference")
    value = {"environment": {"preset": "structured_scene", "source_image": str(source)}}
    monkeypatch.setattr(cli, "read_json", lambda path: value)
    monkeypatch.setattr(cli, "validate_recipe", lambda value: None)
    def must_not_run(*args, **kwargs):
        pytest.fail("Refuse occupied output before Blender or downloads")
    monkeypatch.setattr(cli, "registry", must_not_run)
    monkeypatch.setattr(cli, "download_assets", must_not_run)
    # The CLI may resolve the executable without changing any state.
    monkeypatch.setattr(cli, "find_blender", lambda explicit: Path("unused.exe"))
    args = SimpleNamespace(recipe=str(recipe), output_dir=str(occupied), image=str(source), blender=None)
    action = {"build": cli.command_build, "reconstruct-scene": cli.command_reconstruct_scene,
              "image-environment": cli.command_image_environment}[entry]
    with pytest.raises(outputs.OutputContractError, match="输出目录必须为空"):
        action(args)
    assert protected.read_bytes() == b"CONFIRMED"
    assert list(occupied.iterdir()) == [protected]


def test_prepared_depth_and_recipe_allow_only_files_created_by_current_entry(tmp_path):
    directory = tmp_path / "output"
    depth = directory / "depth/reference.png"
    depth.parent.mkdir(parents=True)
    depth.write_bytes(b"depth")
    recipe = directory / "image-environment.recipe.json"
    recipe.write_text("{}")
    outputs.require_empty_output(directory, [depth, recipe])
    protected = directory / "old.mp4"
    protected.write_bytes(b"OLD")
    with pytest.raises(outputs.OutputContractError):
        outputs.require_empty_output(directory, [depth, recipe])
    assert protected.read_bytes() == b"OLD"


def test_video_probe_checks_frame_count_and_shot_seconds_without_end_frame_duplication(tmp_path, monkeypatch):
    spec = {"shots": [{"id": "S1", "start": 2, "end": 3, "still_times": [2], "video": True,
                       "keyframes": [{"time": 0}]}]}
    directory = tmp_path / "output"
    reference = directory / "reference-frames/REF-S1_START.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"PNG fixture")
    base = tmp_path / "scene.blend"
    base.write_bytes(b"BASE")
    video = directory / "S1_previs.mp4"
    video.write_bytes(b"VIDEO fixture")
    settings = {"fps": 10, "fps_base": 1.0, "width": 96, "height": 96}
    manifest = {"scene_sha256": outputs.file_sha256(base), "shots_sha256": outputs.json_fingerprint(spec),
                "render_settings": settings, "shots": [{"id": "S1", "stills": [
                    outputs.output_record(reference, time=2, frame=21, camera="ShotCamera.S1")],
                    "video": outputs.output_record(video)}]}
    import json
    (directory / "shoot_manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(outputs, "png_size", lambda path: (96, 96))
    probe = {"width": 96, "height": 96, "fps": 10, "frames": 10, "duration": 1}
    monkeypatch.setattr(outputs, "probe_video", lambda path: probe)
    assert all(check['ok'] for check in outputs.check_shoot_outputs(spec, base, directory, settings))
    probe['frames'], probe['duration'] = 11, 1.1
    checks = outputs.check_shoot_outputs(spec, base, directory, settings)
    assert any(check['id'] == 'file:S1:video' and not check['ok'] for check in checks)
    probe['frames'], probe['duration'], probe['width'] = 10, 1, 128
    assert any(check['id'] == 'file:S1:video' and not check['ok']
               for check in outputs.check_shoot_outputs(spec, base, directory, settings))


def test_shot_validation_rejects_still_name_collision_and_zero_frame_video():
    shot = {"id": "S1", "start": 0, "end": 1, "still_times": [0, .101, .102],
            "keyframes": [{"time": 0}]}
    with pytest.raises(outputs.OutputContractError, match="文件名冲突"):
        outputs.validate_shots({"shots": [shot]}, 10)
    shot.update(end=.001, still_times=[0])
    with pytest.raises(outputs.OutputContractError, match="不足一帧"):
        outputs.validate_shots({"shots": [shot]}, 10)


def test_build_video_ranges_cover_exact_frames_and_selected_shots():
    recipe = {"project": {"duration_seconds": 4}, "camera": {"shots": [
        {"id": "A", "start": 0, "end": 1}, {"id": "B", "start": 1, "end": 3},
        {"id": "C", "start": 3, "end": 4}]}, "outputs": {}}
    assert outputs.build_video_frame_range(recipe, 24) == (1, 96)
    recipe['outputs']['preview_video_shots'] = ['B']
    assert outputs.build_video_frame_range(recipe, 24) == (25, 72)
    assert outputs.build_video_frame_range(recipe, 24, topdown=True) == (1, 96)
    recipe['outputs']['preview_video_shots'] = ['A', 'C']
    with pytest.raises(outputs.OutputContractError, match='相邻'):
        outputs.build_video_frame_range(recipe, 24)
    recipe['outputs'] = {'preview_video_range': [1, 2]}
    assert outputs.build_video_frame_range(recipe, 24) == (25, 48)


def test_build_cannot_use_legacy_manifest_or_changed_recipe_as_render_evidence(tmp_path, monkeypatch):
    import json
    recipe = {'project': {'name': 'original', 'duration_seconds': 1},
              'outputs': {'preview_video': False, 'still_frames': [0]}}
    settings = {'fps': 10, 'fps_base': 1., 'width': 96, 'height': 96}
    directory = tmp_path / 'output'
    still = directory / 'stills/frame-00.00.png'
    still.parent.mkdir(parents=True)
    still.write_bytes(b'PNG')
    blend = directory / 'previs.blend'
    blend.write_bytes(b'BLEND')
    manifest = {'source_recipe_sha256': outputs.json_fingerprint(recipe), 'render_settings': settings,
                'artifacts': [outputs.output_record(blend, type='blend'), outputs.output_record(still, type='still')]}
    path = directory / 'manifest.json'
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(outputs, 'png_size', lambda path: (96, 96))
    assert all(check['ok'] for check in outputs.check_build_outputs(recipe, directory, settings))
    recipe['project']['name'] = 'revised'
    assert any(check['id'] == 'inputs:recipe' and not check['ok']
               for check in outputs.check_build_outputs(recipe, directory, settings))
    manifest.pop('source_recipe_sha256')
    path.write_text(json.dumps(manifest))
    assert any(check['id'] == 'inputs:recipe' and not check['ok']
               for check in outputs.check_build_outputs(recipe, directory, settings))


@pytest.mark.parametrize('kind', ['still', 'topdown'])
def test_build_output_plan_rejects_duplicate_formatted_names_before_render(kind):
    recipe = {'project': {'duration_seconds': 1}, 'outputs': {'preview_video': False}}
    if kind == 'still':
        recipe['outputs']['still_frames'] = [.101, .102]
    else:
        recipe['outputs']['topdown'] = {'video': False, 'still_times': [.101, .102]}
    with pytest.raises(outputs.OutputContractError, match='文件名冲突'):
        outputs.build_output_plan(recipe)


@pytest.mark.parametrize('subject,accepted', [
    ({'target': 'chr.a'}, True),
    ({'targets': ['chr.a', 'chr.b']}, True),
    ({'target': 'chr.a', 'targets': ['chr.b']}, False),
    ({'targets': []}, False),
    ({'targets': ['chr.a', 'chr.a']}, False),
    ({}, False),
])
def test_documented_single_and_group_subject_fill_recipe(subject, accepted):
    recipe = {
        'schema_version': '1.0',
        'project': {'name': 'Framing', 'fps': 10, 'duration_seconds': 1, 'resolution': [96, 96]},
        'environment': {'preset': 'studio'}, 'actors': [],
        'camera': {'shots': [{'id': 'S1', 'start': 0, 'end': 1, 'position_space': 'world',
                              'keyframes': [{'time': 0, 'position': [0, -4, 1], 'look_at': [0, 0, 1]}],
                              'subject_fill': dict(subject, min=.1, max=.5)}]},
        'outputs': {'blend': True, 'preview_video': False},
    }
    if accepted:
        cli.validate_recipe(recipe)
    else:
        with pytest.raises(cli.PrevisError, match='配方校验失败'):
            cli.validate_recipe(recipe)


def test_topdown_stills_do_not_implicitly_request_video():
    recipe = {'project': {'duration_seconds': 1},
              'outputs': {'preview_video': False, 'topdown': {'still_times': [0]}}}
    plan = outputs.build_output_plan(recipe)
    assert plan == {'previs.blend': 'blend', 'topdown/topdown-00.00.png': 'topdown_still'}
    recipe['outputs']['topdown']['video'] = True
    assert outputs.build_output_plan(recipe)['topdown/topdown.mp4'] == 'topdown_video'
