"""Shoot output protection and camera selection without executing Blender renders."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import blender_previs_cli as cli


@pytest.fixture
def shoot_runtime(monkeypatch):
    old_camera = SimpleNamespace(name="DirectorCamera.BASE")
    markers = [SimpleNamespace(frame=1, camera=old_camera),
               SimpleNamespace(frame=11, camera=old_camera)]
    scene = SimpleNamespace(camera=old_camera, timeline_markers=markers,
                            render=SimpleNamespace(fps=10, fps_base=1, resolution_x=96,
                                                   resolution_y=96, resolution_percentage=100))
    rendered = []
    opened = []

    def frame_set(frame):
        scene.frame_current = frame
        active = [marker for marker in markers if marker.camera and marker.frame <= frame]
        if active:
            scene.camera = max(active, key=lambda marker: marker.frame).camera

    def render(**options):
        frames = range(scene.frame_start, scene.frame_end + 1) if options.get("animation") else [scene.frame_current]
        for frame in frames:
            frame_set(frame)
            rendered.append((scene.render.filepath, scene.camera.name))
        Path(scene.render.filepath).write_bytes(b"SIMULATED_OUTPUT")

    scene.frame_set = frame_set
    bpy = SimpleNamespace(context=SimpleNamespace(scene=scene, view_layer=SimpleNamespace(update=lambda: None)),
                          data=SimpleNamespace(objects={}),
                          ops=SimpleNamespace(wm=SimpleNamespace(open_mainfile=lambda **kw: opened.append(kw)),
                                              render=SimpleNamespace(render=render)))
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(sys.modules, "mathutils", SimpleNamespace(Vector=lambda value: list(value)))
    monkeypatch.setitem(sys.modules, "compat", SimpleNamespace(use_still_output=lambda *args: None,
                                                              use_video_output=lambda *args: None))
    monkeypatch.setitem(sys.modules, "runner", SimpleNamespace(create_camera_object=lambda *args: None,
                                                              set_interpolation=lambda *args: None))
    monkeypatch.setattr(sys, "path", list(sys.path))
    path = Path(__file__).resolve().parents[1] / "blender_previs" / "shoot.py"
    spec = importlib.util.spec_from_file_location("shoot_boundary_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "make_camera", lambda shot: SimpleNamespace(name=f"ShotCamera.{shot['id']}"))
    return module, scene, rendered, opened


@pytest.mark.parametrize("entry", ["cli", "direct"])
@pytest.mark.parametrize("existing_kind", ["directory", "file"])
def test_shoot_refuses_existing_output_before_launch_or_scene_open(tmp_path, monkeypatch, shoot_runtime,
                                                                  entry, existing_kind):
    output = tmp_path / "output"
    if existing_kind == "directory":
        output.mkdir()
        protected = output / "confirmed.png"
    else:
        protected = output
    protected.write_bytes(b"CONFIRMED_OUTPUT")
    args = SimpleNamespace(output_dir=str(output), blender=None, scene="missing.blend", shots="missing.json")

    def must_not_launch(*args, **kwargs):
        pytest.fail("An occupied output must be refused before locating or launching Blender")

    monkeypatch.setattr(cli, "find_blender", must_not_launch)
    module, _scene, rendered, opened = shoot_runtime
    monkeypatch.setattr(module, "arguments", lambda: args)
    action = cli.command_shoot if entry == "cli" else lambda _args: module.main()
    with pytest.raises((cli.PrevisError, RuntimeError), match="输出目录必须为空"):
        action(args)
    assert protected.read_bytes() == b"CONFIRMED_OUTPUT"
    assert not opened and not rendered
    if existing_kind == "directory":
        assert list(output.iterdir()) == [protected]


def test_shoot_uses_its_camera_for_stills_and_video_with_base_cut_markers(tmp_path, monkeypatch, shoot_runtime):
    module, scene, rendered, opened = shoot_runtime
    shots = tmp_path / "shots.json"
    shots.write_text(json.dumps({"shots": [
        {"id": "S1", "start": 0, "end": 1, "still_times": [0.5], "video": True,
         "keyframes": [{"time": 0}]},
        {"id": "S2", "start": 1, "end": 2, "still_times": [1.5], "video": True,
         "keyframes": [{"time": 0}]},
    ]}), encoding="utf-8")
    base = tmp_path / "base.blend"
    base.write_bytes(b"UNCHANGED_BASE")
    output = tmp_path / "empty_output"
    output.mkdir()
    monkeypatch.setattr(module, "arguments", lambda: SimpleNamespace(
        output_dir=str(output), scene=str(base), shots=str(shots)))
    module.main()
    assert len(opened) == 1
    assert all(marker.camera is None for marker in scene.timeline_markers)
    assert rendered
    assert len(rendered) == 22  # Two stills plus exactly 10 frames per second of video.
    for path, camera_name in rendered:
        shot_id = "S1" if "S1" in Path(path).name else "S2"
        assert camera_name == f"ShotCamera.{shot_id}"
    assert base.read_bytes() == b"UNCHANGED_BASE"
    assert (output / "S1_previs.mp4").read_bytes() == b"SIMULATED_OUTPUT"
    assert (output / "S2_previs.mp4").read_bytes() == b"SIMULATED_OUTPUT"
    assert len(json.loads((output / "shoot_manifest.json").read_text())['shots']) == 2
