"""预演产物自动验收。在 Blender 里打开已构建的 .blend，按配方逐项核对，输出 JSON 报告。

历史教训：接触类问题（人身高、坐姿、椅子穿模）一直是"渲出来看一眼"，改一处崩一处。
这里把可量化的验收固化成命令：接触、比例、机位、产物齐全度，任何一项不过就非零退出，
使后续改动无法悄悄破坏已有产物。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contacts import (  # noqa: E402
    CONTACT_CLEARANCE,
    HIP_WIDTH,
    body_lowest_z,
    chair_arm_boxes,
    chair_clear_seat_width,
    chair_seat_box,
    thighs_hit_armrests,
    thigh_boxes,
    rest_height,
    seat_contact,
    ground_contact,
    SEAT_GAP_TOLERANCE,
    GROUND_GAP_TOLERANCE,
)
from actor_state import actor_target_height, effective_actor_state
from output_contract import check_build_outputs, check_shoot_outputs, file_sha256, json_fingerprint, render_settings, shot_still_times, validate_shots  # noqa: E402
from shoot import apply_props as apply_prop_overrides, restore_props as restore_prop_overrides  # noqa: E402

FLOOR_TOLERANCE = 0.02      # 允许的最低点（脚不得低于此值，米）
SCALE_TOLERANCE = 0.04      # 身高允许误差（米）
SEAT_PENETRATION_TOLERANCE = 0.01  # 身体低于座面的容差（米）
GROUND_FEET_TRADEOFF = 0.28        # 开了 ground_feet（脚落地优先）时允许的陷座深度（已知取舍）


def arguments() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", required=True, help="场次/段配方；人物与接触验收依据")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--scene", help="直接指定要验收的 previs.blend（shoot 产物没有自己的 blend）")
    parser.add_argument("--shots", help="镜头清单 JSON；核对 shoot 产物、景别与镜内人物接触")
    return parser.parse_args(argv)


def check_actor(actor: dict, checks: list[dict], *, measure_height: bool = True) -> None:
    actor_id = actor["id"]
    rig = bpy.data.objects.get(f"{actor_id}.Rig")
    root = rig or bpy.data.objects.get(actor_id)
    if root is None or (actor.get("human_template") and rig is None):
        checks.append({"id": f"actor:{actor_id}:rig", "ok": False, "detail": "缺少骨架"})
        return
    declared = actor_target_height(actor, coarse=True)
    if measure_height and declared is not None:
        measured = rest_height(root, bpy.data.objects.get(actor_id))
        delta = abs(measured - float(declared)) if measured is not None else None
        checks.append({
            "id": f"actor:{actor_id}:height",
            "ok": delta is not None and delta <= SCALE_TOLERANCE,
            "detail": (f"实测 {measured:.3f} 米 / 声明 {float(declared):.3f} 米（差 {delta:.3f}）"
                       if measured is not None else "没有可测量身高的人物网格"),
        })
    scene = bpy.context.scene
    time = (scene.frame_current - 1) / scene.render.fps
    state = effective_actor_state(actor, scene.frame_current, scene.render.fps)
    action = state["action"]
    if state["seated"]:
        chair_id = action.get("chair_id")
        contact = seat_contact(root, action)
        gaps = contact["gaps"]
        ground_feet = bool(action.get("ground_feet"))
        limit = GROUND_FEET_TRADEOFF if ground_feet else SEAT_PENETRATION_TOLERANCE
        # Negative = penetration; positive = clearance. Both sides are bounded.
        ok = (bool(gaps) and contact["coverage"] >= .5
              and min(gaps) >= -limit and min(gaps) <= SEAT_GAP_TOLERANCE)
        checks.append({
            "id": f"seat:{actor_id}", "ok": ok,
            "detail": (f"{time:.3f}s：臀腿/实际座面间隙 {min(gaps):+.3f} 米；"
                       f"允许 [-{limit}, {SEAT_GAP_TOLERANCE}]，承托采样 {contact['coverage']:.0%}"
                       if gaps else f"{time:.3f}s：{contact['reason']}"),
            "signed_gap_m": min(gaps) if gaps else None, "support_coverage": contact["coverage"],
        })
        arms = chair_arm_boxes(str(chair_id)) if chair_id else []
        if arms and len(thigh_boxes(root)) < 2:
            checks.append({"id": f"armrest:{actor_id}", "ok": False,
                           "detail": f"{time:.3f}s：缺少左右大腿接触几何"})
        elif arms and thighs_hit_armrests(root, str(chair_id)):
            clear = chair_clear_seat_width(str(chair_id))
            checks.append({
                "id": f"armrest:{actor_id}",
                "ok": False,
                "detail": f"大腿与扶手相交；该椅净坐宽 {clear if clear is None else round(clear, 2)} 米（髋宽 {HIP_WIDTH}）",
            })
        else:
            checks.append({"id": f"armrest:{actor_id}", "ok": True, "detail": "与扶手无相交"})


def check_ground(actors: list[dict], checks: list[dict]) -> None:
    scene = bpy.context.scene
    roots = [bpy.data.objects.get(actor["id"]) for actor in actors]
    for actor in actors:
        root = bpy.data.objects.get(f"{actor['id']}.Rig") or bpy.data.objects.get(actor["id"])
        state = effective_actor_state(actor, scene.frame_current, scene.render.fps)
        action = state["action"] or {}
        chairs = [bpy.data.objects.get(str(item["chair_id"])) for item in actor.get("actions", [])
                  if item.get("chair_id")]
        contact = ground_contact(root, state["support_z"], excluded_roots=[*roots, *chairs])
        gaps = contact["gaps"]
        time = (scene.frame_current - 1) / scene.render.fps
        # A seated person's feet may hang. ground_feet explicitly requests
        # floor contact, while its separate bounded seat tradeoff remains on.
        needs_contact = (state["posture"] in {"stand", "crouch", "bend_over", "walk", "run"}
                         or bool(action.get("ground_feet")))
        ok = bool(gaps) and min(gaps) >= -FLOOR_TOLERANCE
        if needs_contact:
            ok = ok and min(gaps) <= GROUND_GAP_TOLERANCE
        checks.append({
            "id": f"floor:{actor['id']}", "ok": ok,
            "detail": (f"{time:.3f}s：最低接触区与支撑面间隙 {min(gaps):+.3f} 米；"
                       f"来源 {contact['source']}" if gaps else f"{time:.3f}s：缺少人物或可测量网格"),
            "signed_gap_m": min(gaps) if gaps else None, "support_source": contact["source"],
        })


def actor_sample_frames(recipe: dict, shot: dict | None = None) -> list[int]:
    """有界抽样：镜/段边界、机位与动作边界、输出时刻及相邻样点中点；不是逐帧保证。"""
    start = float(shot["start"]) if shot else 0.0
    end = float(shot["end"]) if shot else float(recipe["project"]["duration_seconds"])
    times = {start, end}
    shots = [shot] if shot else recipe.get("camera", {}).get("shots", [])
    if not shot:
        times.update(float(key["time"]) for key in recipe.get("camera", {}).get("keyframes", []))
    for item in shots:
        times.update((float(item["start"]), float(item["end"])))
        times.update(float(item["start"]) + float(key["time"]) for key in item.get("keyframes", []))
        times.update(shot_still_times(item))
    for actor in recipe.get("actors", []):
        for action in actor.get("actions", []):
            first = float(action["start"])
            last = first + float(action["duration"])
            times.update((first, last, (first + last) / 2))
    outputs = recipe.get("outputs", {})
    times.update(map(float, outputs.get("still_frames", [])))
    times.update(float(view["time"]) for view in outputs.get("reference_views", []))
    times.update(map(float, (outputs.get("topdown") or {}).get("still_times", [])))
    times = sorted(time for time in times if start <= time <= end)
    times += [(a + b) / 2 for a, b in zip(times, times[1:])]
    fps = bpy.context.scene.render.fps
    return sorted({round(time * fps) + 1 for time in times})


def check_actors(recipe: dict, checks: list[dict], shot: dict | None = None) -> dict:
    """按公开的有限样帧检查人物，保留进入动作前后的实际状态。"""
    scene = bpy.context.scene
    saved_frame = scene.frame_current
    frames = actor_sample_frames(recipe, shot)
    try:
        for index, frame in enumerate(frames):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            first_check = len(checks)
            for actor in recipe.get("actors", []):
                check_actor(actor, checks, measure_height=index == 0)
            check_ground(recipe.get("actors", []), checks)
            for check in checks[first_check:]:
                check["sample_frame"] = frame
    finally:
        scene.frame_set(saved_frame)
    return {"shot_id": shot["id"] if shot else None, "frames": frames,
            "scope": "镜/段起止、机位关键帧、动作起止与中点、输出静帧及相邻样点中点；非逐帧检查"}


def check_camera(recipe: dict, checks: list[dict]) -> None:
    duration = float(recipe["project"]["duration_seconds"])
    shots = recipe.get("camera", {}).get("shots") or []
    for shot in shots:
        start, end = float(shot["start"]), float(shot["end"])
        inside = 0.0 <= start < end <= duration + 0.01
        camera = ensure_shot_camera(shot)
        checks.append({
            "id": f"shot:{shot['id']}",
            "ok": inside and camera is not None and bool(shot.get("keyframes")),
            "detail": f"{start}–{end} 秒（段长 {duration}）；相机 {'有' if camera else '缺'}；关键帧 {len(shot.get('keyframes', []))}",
        })


def check_outputs(recipe: dict, output_dir: Path, checks: list[dict]) -> None:
    checks.extend(check_build_outputs(recipe, output_dir, render_settings(bpy.context.scene)))


def mesh_parts(obj) -> list:
    """道具/锚点在工程里是空物体父节点 + 子网格；判定与投影都要取到真正的网格。"""
    if obj is None:
        return []
    if obj.type == "MESH":
        return [obj]
    return [child for child in obj.children_recursive if child.type == "MESH" and not child.hide_render]


def mesh_world_points(parts: list) -> list:
    """动画、骨骼与修改器更新后的世界包围盒；不能沿用换帧前的坐标。"""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return [evaluated.matrix_world @ Vector(corner)
            for part in parts for evaluated in [part.evaluated_get(depsgraph)]
            for corner in evaluated.bound_box]


def find_shot_camera(shot_id: str):
    """镜头相机可能来自 build（DirectorCamera.*）或 shoot（ShotCamera.*）。"""
    for prefix in ("ShotCamera", "DirectorCamera"):
        camera = bpy.data.objects.get(f"{prefix}.{shot_id}")
        if camera is not None:
            return camera
    return None


def ensure_shot_camera(shot: dict):
    """shoot 产物不保存场景，镜头相机不在 blend 里：按镜头清单现场解算一个。

    缺相机时复用 shoot 的完整动画，验收实际插值路径。
    """
    camera = find_shot_camera(str(shot["id"]))
    if camera is not None:
        return camera
    from shoot import make_camera
    return make_camera(shot)


def target_world_point(target: str):
    """取一个对象/人物在画面里的代表点：道具用网格包围盒中心，人物用胸口。"""
    obj = bpy.data.objects.get(target)
    parts = mesh_parts(obj)
    if parts:
        points = mesh_world_points(parts)
        return sum(points, Vector()) / len(points)
    rig = bpy.data.objects.get(f"{target}.Rig")
    if rig is not None:
        bone = rig.pose.bones.get("spine_02") or rig.pose.bones.get("pelvis")
        if bone is not None:
            return rig.matrix_world @ bone.head
    if obj is not None:
        return obj.matrix_world.translation.copy()
    return None


def check_framing(recipe: dict, checks: list[dict]) -> None:
    """景别约束：shots[].must_include 列出的对象/人物必须落在画面内。

    白模当分镜参考时，这是硬约束——分镜图常错的就是景别与人物关系。
    """
    from bpy_extras.object_utils import world_to_camera_view

    scene = bpy.context.scene
    for shot in recipe.get("camera", {}).get("shots", []):
        targets = shot.get("must_include") or []
        if not targets:
            continue
        camera = ensure_shot_camera(shot)
        if camera is None:
            checks.append({"id": f"framing:{shot['id']}", "ok": False, "detail": "缺少该镜相机"})
            continue
        saved_camera = scene.camera
        scene.camera = camera
        inside = True
        details = []
        for frame in actor_sample_frames(recipe, shot):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            for target in targets:
                point = target_world_point(str(target))
                if point is None:
                    inside = False
                    details.append(f"{target}:找不到对象")
                    continue
                coord = world_to_camera_view(scene, camera, point)
                ok = coord.z > 0.0 and 0.0 <= coord.x <= 1.0 and 0.0 <= coord.y <= 1.0
                inside = inside and ok
                time = (frame - 1) / scene.render.fps
                details.append(f"{target}@{time:.3f}s=({coord.x:.2f},{coord.y:.2f},深度{coord.z:.2f}){'✓' if ok else '✗'}")
        scene.camera = saved_camera
        checks.append({"id": f"framing:{shot['id']}", "ok": inside, "detail": " ".join(details)})


def target_extent_points(target: str):
    """取目标的竖向范围代表点（道具用网格包围盒角点，人物用头骨与脚）。"""
    obj = bpy.data.objects.get(target)
    parts = mesh_parts(obj)
    if parts:
        return mesh_world_points(parts)
    rig = bpy.data.objects.get(f"{target}.Rig")
    if rig is not None:
        head = rig.pose.bones.get("head")
        foot = rig.pose.bones.get("foot_l") or rig.pose.bones.get("pelvis")
        return [rig.matrix_world @ bone.tail for bone in (head, foot)] if head and foot else None
    if obj is not None:
        return [obj.matrix_world.translation.copy()]
    return None


def check_subject_fill(recipe: dict, checks: list[dict]) -> None:
    """景别硬约束：主体占画面高度的比例必须落在声明区间内（近景/特写靠它落地）。"""
    from bpy_extras.object_utils import world_to_camera_view

    scene = bpy.context.scene
    for shot in recipe.get("camera", {}).get("shots", []):
        spec = shot.get("subject_fill")
        if not spec:
            continue
        camera = ensure_shot_camera(shot)
        targets = spec.get("targets") or [spec.get("target")]
        label = "、".join(str(t) for t in targets if t)
        if camera is None or not any(targets):
            checks.append({"id": f"fill:{shot['id']}", "ok": False, "detail": f"{label} 或相机缺失"})
            continue
        saved_camera = scene.camera
        scene.camera = camera
        details = []
        ok_all = True
        for frame in actor_sample_frames(recipe, shot):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            points = []
            for target in targets:
                extent = target_extent_points(str(target)) if target else None
                if not extent:
                    points = []
                    break
                points.extend(extent)
            time = (frame - 1) / scene.render.fps
            if not points:
                ok_all = False
                details.append(f"{time:.3f}s：目标或可测量范围缺失")
                continue
            coords = [world_to_camera_view(scene, camera, point) for point in points]
            ys, xs = [coord.y for coord in coords], [coord.x for coord in coords]
            # 主体占画幅取横竖两个方向的较大者：沿纵深躺着的主体（如落地的双剂）竖向占比会偏小
            fill = max(max(ys) - min(ys), max(xs) - min(xs))
            ok = all(coord.z > 0 for coord in coords) and float(spec["min"]) <= fill <= float(spec["max"])
            ok_all = ok_all and ok
            details.append(f"{time:.3f}s 占比 {fill:.2f}（要求 {spec['min']}–{spec['max']}）{'✓' if ok else '✗'}")
        scene.camera = saved_camera
        checks.append({"id": f"fill:{shot['id']}", "ok": ok_all, "detail": f"{label} 并集：" + " ".join(details)})


def main() -> None:
    args = arguments()
    recipe = json.loads(Path(args.recipe).read_text(encoding="utf-8"))
    output_dir = Path(args.output_dir)
    shots_spec = json.loads(Path(args.shots).read_text(encoding="utf-8")) if args.shots else None
    blend = Path(args.scene) if args.scene else output_dir / "previs.blend"
    checks: list[dict] = []
    sampling = []
    if blend.exists():
        bpy.ops.wm.open_mainfile(filepath=str(blend))
    else:
        checks.append({"id": "file:blend", "ok": False, "detail": f"缺少 {blend.name}，无法验收几何"})
    if shots_spec is not None:
        # shoot 产物：复现镜头与道具状态，同时核对配方中声明的人物。
        from shoot import make_camera, prepare_shoot_scene
        prepare_shoot_scene(bpy.context.scene)
        settings = render_settings(bpy.context.scene)
        validate_shots(shots_spec, settings["fps"])
        for shot in shots_spec["shots"]:
            # 基座可能含旧 build 相机；shoot 以本轮清单新建相机，验收也如此。
            make_camera(shot)
            for prop_id in shot.get("props") or {}:
                checks.append({"id": f"prop:{shot['id']}:{prop_id}",
                               "ok": bpy.data.objects.get(str(prop_id)) is not None,
                               "detail": "覆盖的道具必须存在于场次白模"})
            saved = apply_prop_overrides(shot.get("props"))
            one = {**recipe, "camera": {"shots": [shot]}}
            try:
                check_camera(one, checks)
                sampling.append(check_actors(one, checks, shot))
                check_framing(one, checks)
                check_subject_fill(one, checks)
            finally:
                restore_prop_overrides(saved)
        checks.extend(check_shoot_outputs(shots_spec, blend, output_dir, settings))
    else:
        sampling.append(check_actors(recipe, checks))
        check_camera(recipe, checks)
        check_framing(recipe, checks)
        check_subject_fill(recipe, checks)
        check_outputs(recipe, output_dir, checks)
    failed = [item for item in checks if not item["ok"]]
    report = {
        "blender_version": bpy.app.version_string,
        "recipe_sha256": json_fingerprint(recipe),
        "shot_ids": [shot["id"] for shot in (shots_spec or recipe.get("camera", {})).get("shots", [])],
        "shots_sha256": json_fingerprint(shots_spec) if shots_spec is not None else None,
        "scene_sha256": file_sha256(blend) if blend.is_file() else None,
        "render_manifest_sha256": file_sha256(output_dir / "shoot_manifest.json") if (output_dir / "shoot_manifest.json").is_file() else None,
        "ok": not failed,
        "checked": len(checks),
        "failed": len(failed),
        "checks": checks,
        "clearance_standard": CONTACT_CLEARANCE,
        "sampling": sampling,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["ok"] else 9)


if __name__ == "__main__":
    main()
