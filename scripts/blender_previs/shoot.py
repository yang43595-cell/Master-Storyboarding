"""在同一份场次白模上"运镜"：打开已建好的 previs.blend，按镜头清单出图/出视频。

为什么要有它：场次白模（人物、房间、道具）只搭一次，之后每个镜头只是换机位；
此前每镜都重新 build 一套场景，人还不全、道具临时摆，既重复又容易错。
本脚本只加相机、只按镜头临时挪动声明的道具，**不改动、不保存**场景几何。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compat import use_still_output, use_video_output  # noqa: E402
from runner import create_camera_object, set_interpolation  # noqa: E402
from output_contract import (file_sha256, json_fingerprint, output_record, reference_name,
                             render_settings, require_empty_output, shot_frame_range,
                             shot_still_times, validate_shots)  # noqa: E402


def arguments() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", required=True, help="已建好的场次白模 previs.blend")
    parser.add_argument("--shots", required=True, help="镜头清单 JSON")
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def shot_camera_objects(name: str) -> dict:
    """Reuse only our shot camera/aim/follow objects, including pre-tag staging."""
    camera = bpy.data.objects.get(name)
    target = bpy.data.objects.get(f"{name}.Target")
    rig = bpy.data.objects.get(f"{name}.RelativeRig")
    if camera is None:
        if target is not None or rig is not None:
            raise RuntimeError(f"镜头摄影机辅助对象重名且无所属相机：{name}")
        return {}
    for obj in (camera, target, rig):
        if obj is not None and (obj.name not in bpy.context.scene.objects or
                any(scene != bpy.context.scene for scene in obj.users_scene)):
            raise RuntimeError(f"镜头摄影机对象未专属于当前场景：{obj.name}")
    # Older stage-shot files have no ownership tag, but have this exact generated
    # camera -> LookAt -> Target topology. A name alone is not ownership.
    look = camera.constraints.get(f"{name}.LookAt")
    legacy = (look is not None and look.type == 'TRACK_TO' and look.target == target
              and target is not None and camera.parent == rig)
    owned = camera.get('previs_camera_owner') == name
    if camera.type != 'CAMERA' or not (owned or legacy):
        raise RuntimeError(f"镜头摄影机名称被无关对象占用：{name}")
    for obj, role in ((target, 'Target'), (rig, 'RelativeRig')):
        if obj is not None and (obj.type != 'EMPTY' or not
                (obj.get('previs_camera_owner') == name or legacy)):
            raise RuntimeError(f"镜头摄影机辅助对象不属于本镜：{name}.{role}")
    if target is None:
        raise RuntimeError(f"镜头摄影机缺少所属目标：{name}")
    return {'camera': camera, 'target': target, 'rig': rig}


def make_camera(shot: dict):
    # 与 build 复用同一相机/目标实现；time 是相对本镜 start 的秒数。
    # 逐段线性插值使相同状态的两个中间点形成真正停留，避免贝塞尔过冲。
    name = f"ShotCamera.{shot['id']}"
    camera = create_camera_object(shot, bpy.context.scene.render.fps,
                                  name, float(shot["start"]), reuse=shot_camera_objects(name))
    for obj in (camera, camera.constraints[-1].target, camera.parent):
        if obj is not None:
            obj['previs_camera_owner'] = name
    set_interpolation(camera, "location", "LINEAR")
    set_interpolation(camera.constraints[-1].target, "location", "LINEAR")
    set_interpolation(camera.data, "lens", "LINEAR")
    return camera


def apply_props(overrides: dict) -> dict:
    """按镜头临时挪道具，返回原状态以便恢复。"""
    saved = {}
    for prop_id, spec in (overrides or {}).items():
        obj = bpy.data.objects.get(prop_id)
        if obj is None:
            continue
        saved[prop_id] = (list(obj.location), list(obj.rotation_euler), list(obj.scale))
        if "position" in spec:
            obj.location = Vector(spec["position"])
        if "rotation_degrees" in spec:
            import math
            obj.rotation_euler = (0.0, 0.0, math.radians(float(spec["rotation_degrees"])))
        if "size" in spec:
            size = spec["size"]
            obj.scale = Vector((size[0], size[1], size[2]))
    bpy.context.view_layer.update()
    return saved


def restore_props(saved: dict) -> None:
    for prop_id, state in saved.items():
        obj = bpy.data.objects.get(prop_id)
        if obj is None:
            continue
        obj.location = Vector(state[0])
        obj.rotation_euler = state[1]
        obj.scale = state[2]
    bpy.context.view_layer.update()


def prepare_shoot_scene(scene) -> None:
    """shoot 与 verify 都按本轮镜头清单运行，解除基座切镜相机绑定。"""
    for marker in scene.timeline_markers:
        marker.camera = None


def main() -> None:
    args = arguments()
    output_dir = Path(args.output_dir).resolve()
    require_empty_output(output_dir)
    scene_path = Path(args.scene).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(scene_path))
    scene = bpy.context.scene
    # 与 runner 的俯视输出一致：旧 CUT 标记会在 frame_set / 动画渲染时抢回基座相机。
    # 只解除本次进程内的相机绑定；shoot 不保存基座场景。
    prepare_shoot_scene(scene)
    spec = json.loads(Path(args.shots).read_text(encoding="utf-8"))
    settings = render_settings(scene)
    if settings["fps_base"] != 1.0:
        raise RuntimeError("运镜白模的 fps_base 必须为 1；先核对基座实际帧率")
    validate_shots(spec, settings["fps"])
    (output_dir / "reference-frames").mkdir(parents=True, exist_ok=True)
    (output_dir / "stills").mkdir(parents=True, exist_ok=True)
    manifest = {"scene": str(scene_path), "scene_sha256": file_sha256(scene_path),
                "shots_sha256": json_fingerprint(spec), "render_settings": settings, "shots": []}

    for shot in spec["shots"]:
        camera = make_camera(shot)
        scene.camera = camera
        saved = apply_props(shot.get("props"))
        use_still_output(scene, "PNG")
        stills = []
        frames = shot_still_times(shot)
        for index, time_value in enumerate(frames):
            frame = round(float(time_value) * scene.render.fps) + 1
            scene.frame_set(frame)
            path = output_dir / "reference-frames" / reference_name(shot, index, time_value)
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            stills.append(output_record(path, time=time_value, frame=frame, camera=scene.camera.name))
        video = None
        if shot.get("video"):
            use_video_output(scene)
            video_path = output_dir / f"{shot['id']}_previs.mp4"
            scene.frame_start, scene.frame_end = shot_frame_range(shot, scene.render.fps)
            scene.render.filepath = str(video_path)
            bpy.ops.render.render(animation=True)
            video = output_record(video_path)
        restore_props(saved)
        manifest["shots"].append({"id": shot["id"], "label": shot.get("label"), "camera": camera.name,
                                  "stills": stills, "video": video})
    report_path = output_dir / "shoot_manifest.json"
    report_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "shots": len(manifest["shots"]), "output_dir": str(output_dir)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
