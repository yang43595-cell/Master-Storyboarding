"""Blender-side recipe executor. Run only through blender_previs_cli.py."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

# 版本适配与接触测量各自只有一个出口：compat.py（Blender 版本差异）、contacts.py（接触与测量）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from compat import (  # noqa: E402
    action_fcurves as _action_fcurves,
    clear_shape_keys,
    engine_name as _eevee_engine_name,
    use_still_output as _use_still_output,
    use_video_output as _use_video_output,
)
from contacts import (  # noqa: E402
    CONTACT_CLEARANCE,
    DEFAULT_SEAT_HEIGHT,
    LEGACY_HUMAN_HEIGHT,
    body_lowest_z as actor_body_lowest_z,
    boxes_intersect,
    chair_arm_boxes,
    chair_clear_seat_width,
    chair_seat_box,
    chair_seat_front_along,
    narrow_seat_warning as warn_narrow_seat,
    thigh_boxes,
    thighs_hit_armrests,
    normalize_actor,
    seat_contact,
)
from actor_state import actor_target_height, action_frames, effective_actor_state, ordered_actions
from output_contract import (build_output_plan, build_video_frame_range, file_sha256, json_fingerprint,
                             render_settings, require_empty_output)  # noqa: E402



ACTOR_IDENTITY_PALETTE = (
    "#0072B2",  # blue
    "#E69F00",  # orange
    "#009E73",  # green
    "#CC79A7",  # magenta
    "#D55E00",  # vermillion
    "#56B4E9",  # sky blue
    "#F0E442",  # yellow
    "#6A5ACD",  # purple
)


def arguments() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prepared-file", action="append", default=[])
    return parser.parse_args(argv)


def material(name: str, color: tuple[float, float, float, float], roughness: float = 0.72):
    value = bpy.data.materials.new(name)
    value.diffuse_color = color
    value.use_nodes = True
    bsdf = value.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = roughness
    return value


def rgba_from_hex(value: str) -> tuple[float, float, float, float]:
    normalized = value.lstrip("#")
    if len(normalized) != 6:
        raise ValueError(f"人物颜色必须是 #RRGGBB：{value}")
    return tuple(int(normalized[index:index + 2], 16) / 255.0 for index in (0, 2, 4)) + (1.0,)


def lighter_color(color: tuple[float, float, float, float], amount: float = 0.28):
    return tuple(channel + (1.0 - channel) * amount for channel in color[:3]) + (color[3],)


def resolve_actor_identity_colors(recipe: dict) -> None:
    actors = recipe.get("actors", [])
    configured_mode = recipe.get("outputs", {}).get("actor_color_mode")
    mode = configured_mode or ("auto" if len(actors) > 1 else "source")
    used_palette_indices: set[int] = set()
    ordered_actors = sorted(actors, key=lambda item: str(item["id"]))
    for actor in ordered_actors:
        explicit = actor.get("display_color")
        if explicit:
            actor["_resolved_display_color"] = str(explicit).upper()
            actor["_display_color_source"] = "explicit"
            if actor["_resolved_display_color"] in ACTOR_IDENTITY_PALETTE:
                used_palette_indices.add(ACTOR_IDENTITY_PALETTE.index(actor["_resolved_display_color"]))
    for actor in ordered_actors:
        if actor.get("_resolved_display_color"):
            continue
        if mode != "auto":
            continue
        digest = hashlib.sha256(str(actor["id"]).encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % len(ACTOR_IDENTITY_PALETTE)
        if len(used_palette_indices) < len(ACTOR_IDENTITY_PALETTE):
            while index in used_palette_indices:
                index = (index + 1) % len(ACTOR_IDENTITY_PALETTE)
        used_palette_indices.add(index)
        actor["_resolved_display_color"] = ACTOR_IDENTITY_PALETTE[index]
        actor["_display_color_source"] = "auto"


def apply_actor_identity_material(actor: dict, actor_meshes: list) -> None:
    color_value = actor.get("_resolved_display_color")
    if not color_value:
        return
    identity_material = material(f"{actor['id']}.identity", rgba_from_hex(str(color_value)), 0.68)
    for obj in actor_meshes:
        if len(obj.data.materials) == 0:
            obj.data.materials.append(identity_material)
            continue
        for index in range(len(obj.data.materials)):
            obj.data.materials[index] = identity_material


def emissive_material(name: str, color: tuple[float, float, float, float], strength: float = 3.0):
    value = material(name, color, 0.24)
    bsdf = value.node_tree.nodes.get("Principled BSDF")
    emission = bsdf.inputs.get("Emission Color")
    if emission is None:
        emission = bsdf.inputs.get("Emission")
    emission_strength = bsdf.inputs.get("Emission Strength")
    if emission is not None:
        emission.default_value = color
    if emission_strength is not None:
        emission_strength.default_value = strength
    return value


def apply_material(obj, value) -> None:
    if getattr(obj.data, "materials", None) is not None:
        obj.data.materials.append(value)


def cube(name: str, location, scale, mat=None, parent=None, bevel=0.04):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        modifier = obj.modifiers.new("Soft edges", "BEVEL")
        modifier.width = bevel
        modifier.segments = 2
    if mat:
        apply_material(obj, mat)
    if parent:
        obj.parent = parent
    return obj


def sphere(name: str, location, radius, mat=None, parent=None):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=radius, location=location)
    obj = bpy.context.object
    obj.name = name
    if mat:
        apply_material(obj, mat)
    if parent:
        obj.parent = parent
    return obj


def cylinder(name: str, location, radius, depth, mat=None, parent=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=20, radius=radius, depth=depth, location=location)
    obj = bpy.context.object
    obj.name = name
    if mat:
        apply_material(obj, mat)
    if parent:
        obj.parent = parent
    return obj


def cone(name: str, location, radius1, radius2, depth, mat=None, parent=None):
    bpy.ops.mesh.primitive_cone_add(vertices=32, radius1=radius1, radius2=radius2, depth=depth, location=location)
    obj = bpy.context.object
    obj.name = name
    if mat:
        apply_material(obj, mat)
    if parent:
        obj.parent = parent
    return obj


def keyframe(obj, frame: int, data_paths=("location", "rotation_euler")) -> None:
    for path in data_paths:
        obj.keyframe_insert(data_path=path, frame=frame)


def set_interpolation(obj, data_path: str, interpolation: str) -> None:
    if not obj.animation_data or not obj.animation_data.action:
        return
    for curve in _action_fcurves(obj.animation_data.action):
        if curve.data_path == data_path:
            for point in curve.keyframe_points:
                point.interpolation = interpolation


def distance_matched_gait(origin, destination, action: dict, preset: str) -> dict:
    path_distance = (Vector(destination) - Vector(origin)).length
    nominal_step_length = float(action.get("step_length", 0.65 if preset == "walk" else 1.05))
    step_count = max(1, round(path_distance / nominal_step_length))
    duration = max(float(action["duration"]), 1e-5)
    return {
        "path_distance": path_distance,
        "step_count": step_count,
        "step_length": path_distance / step_count,
        "cadence_spm": step_count / duration * 60.0,
    }


def role_palette(role: str):
    palettes = {
        "princess": ((0.72, 0.25, 0.56, 1), (0.95, 0.55, 0.78, 1)),
        "maid": ((0.08, 0.11, 0.18, 1), (0.92, 0.92, 0.96, 1)),
        "butler": ((0.035, 0.045, 0.065, 1), (0.88, 0.9, 0.94, 1)),
        "generic": ((0.25, 0.42, 0.64, 1), (0.75, 0.82, 0.9, 1)),
    }
    return palettes.get(role, palettes["generic"])


# 预演预设动作用的是 MakeHuman / UE(Unreal) 风格骨骼名（pelvis, spine_01, upperarm_l …）。
# Quaternius(UE/Godot) 与 MPFB.game_engine 正好都是这套命名，直接命中；
# 其他来源（Rigify .L/.R、Mixamo 等）命名不同，需靠别名表解析，否则明确报错提示重定向。
RIG_BONE_ALIASES = {
    "pelvis": ("pelvis", "hips", "root", "pelvis_master"),
    "spine_01": ("spine_01", "spine", "spine.001", "spine_02_00"),
    "spine_02": ("spine_02", "spine.001", "spine.002", "spine_03_00"),
    "spine_03": ("spine_03", "spine.002", "spine.003", "spine_04_00"),
    "upperarm_l": ("upperarm_l", "upper_arm.L", "upper_arm_l", "LeftArm", "upper_arm_l_l"),
    "upperarm_r": ("upperarm_r", "upper_arm.R", "upper_arm_r", "RightArm", "upper_arm_r_r"),
    "lowerarm_l": ("lowerarm_l", "lower_arm.L", "forearm.L", "LeftForeArm", "forearm_l"),
    "lowerarm_r": ("lowerarm_r", "lower_arm.R", "forearm.R", "RightForeArm", "forearm_r"),
    "thigh_l": ("thigh_l", "thigh.L", "upleg_l", "LeftUpLeg"),
    "thigh_r": ("thigh_r", "thigh.R", "upleg_r", "RightUpLeg"),
    "calf_l": ("calf_l", "shin.L", "lowleg_l", "LeftLeg"),
    "calf_r": ("calf_r", "shin.R", "lowleg_r", "RightLeg"),
    "foot_l": ("foot_l", "foot.L", "foot_l_l", "ball.L", "LeftFoot"),
    "foot_r": ("foot_r", "foot.R", "foot_r_r", "ball.R", "RightFoot"),
}


def resolve_rig_bone(rig, name: str) -> str | None:
    if name in rig.pose.bones:
        return name
    for alias in RIG_BONE_ALIASES.get(name, ()):
        if alias in rig.pose.bones:
            return alias
    return None


def animate_rig_pose(rig, frame: int, rotations: dict[str, tuple[float, float, float]]) -> None:
    missing: list[str] = []
    for bone_name, degrees in rotations.items():
        resolved = resolve_rig_bone(rig, bone_name)
        if resolved is None:
            missing.append(bone_name)
            continue
        bone = rig.pose.bones[resolved]
        bone.rotation_mode = "XYZ"
        bone.rotation_euler = tuple(math.radians(value) for value in degrees)
        bone.keyframe_insert("rotation_euler", frame=frame)
    if missing:
        raise RuntimeError(
            f"骨架 {rig.name} 缺少摆姿态所需骨骼：{', '.join(sorted(missing))}；"
            f"该导入资产骨架与本预演预设不兼容，需重定向到预设骨架、或改用同一副骨架的资产。"
        )


def animate_native_motion_clip(rig, clip_name: str, start: int, end: int, cycle_count: float) -> None:
    clip = bpy.data.actions.get(clip_name)
    if clip is None:
        available = ", ".join(sorted(action.name for action in bpy.data.actions))
        raise RuntimeError(f"外部人物缺少原生动作 {clip_name}；可用动作：{available}")
    if rig.animation_data is None:
        rig.animation_data_create()
    rig.animation_data.action = None
    for existing_track in rig.animation_data.nla_tracks:
        existing_track.mute = existing_track.name != "Licensed native motion"
    track = rig.animation_data.nla_tracks.get("Licensed native motion")
    if track is None:
        track = rig.animation_data.nla_tracks.new()
        track.name = "Licensed native motion"
    clip_start, clip_end = map(float, clip.frame_range)
    clip_frames = max(clip_end - clip_start, 1e-5)
    strip = track.strips.new(f"{clip_name}@{start}", start, clip)
    strip.action_frame_start = clip_start
    strip.action_frame_end = clip_end
    strip.repeat = max(float(cycle_count), 1e-5)
    strip.scale = max(end - start, 1) / (clip_frames * strip.repeat)
    strip.extrapolation = "NOTHING"


def keyframe_rig_state(rig, frame: int) -> None:
    """Hold the evaluated predecessor pose, including hands and support bones."""
    for bone in rig.pose.bones:
        rotation = ("rotation_quaternion" if bone.rotation_mode == "QUATERNION" else
                    "rotation_axis_angle" if bone.rotation_mode == "AXIS_ANGLE" else "rotation_euler")
        for path in ("location", rotation, "scale"):
            bone.keyframe_insert(path, frame=frame)


def animate_head_attention(root, rig, action: dict, start: int, end: int) -> None:
    """A head-only turn for the standard -Y-facing human, not eye/hand IK.

    The anatomical forward direction is transformed through the actual rest
    bone matrix; no bone-local Euler axis is assumed. Other poses stay intact.
    """
    target_name = action.get("target")
    target = bpy.context.scene.objects.get(target_name) if isinstance(target_name, str) else None
    if not target_name or target is None:
        raise RuntimeError(f"人物 {root.name} 的 look_at 需要已存在的 target 对象名：{target_name!r}")
    ancestor = target
    while ancestor is not None:
        if ancestor == root:
            raise RuntimeError(f"人物 {root.name} 的 look_at 不能指向自身或自身部件 {target_name}；请提供独立目标")
        ancestor = ancestor.parent
    required = ("head", "neck_01", "pelvis", "spine_03", "upperarm_l", "upperarm_r", "foot_l", "foot_r")
    missing = [name for name in required if name not in rig.pose.bones]
    if missing:
        raise RuntimeError(f"人物 {root.name} 的 look_at 仅支持标准 -Y 朝向人体骨架，缺少骨骼：{', '.join(missing)}；请重定向或补原生姿态")
    head = rig.pose.bones["head"]
    if head.parent is None or any(not c.mute and c.influence for c in head.constraints):
        raise RuntimeError(f"人物 {root.name} 的 head 存在不支持的父级/约束；请先提供可直接摆姿的标准头部骨骼")
    # Freeze the old head orientation at the action start; evaluate the target
    # at the action end. This is a bounded attention beat, not live tracking.
    bpy.context.scene.frame_set(end)
    bpy.context.view_layer.update()
    world = rig.matrix_world @ head.matrix
    origin = world.translation
    direction = target.matrix_world.translation - origin
    head_length = (rig.matrix_world.to_3x3() @ (head.tail - head.head)).length
    if not all(math.isfinite(value) for value in direction) or direction.length <= max(head_length, 1e-4):
        raise RuntimeError(f"人物 {root.name} 的 look_at 目标 {target_name} 太近或坐标无效；请将目标放到头部范围外")
    forward_local = head.bone.matrix_local.to_3x3().inverted() @ Vector((0, -1, 0))
    # Judge reachability relative to the inherited torso, rather than allowing
    # a second look_at to accumulate an impossible backwards neck twist.
    neutral = head.parent.matrix @ head.parent.bone.matrix_local.inverted() @ head.bone.matrix_local
    neutral_forward = (rig.matrix_world.to_3x3() @ neutral.to_3x3() @ forward_local).normalized()
    direction.normalize()
    if neutral_forward.dot(direction) < 0:
        raise RuntimeError(f"人物 {root.name} 的 look_at 目标 {target_name} 位于身体后方；请明确 turn/rotation_degrees 或调整布置，不自动转动身体")
    current_forward = (world.to_3x3() @ forward_local).normalized()
    delta = current_forward.rotation_difference(direction).to_matrix().to_4x4()
    rotated_world = delta @ world
    rotated_world.translation = origin
    head.matrix = rig.matrix_world.inverted() @ rotated_world
    bpy.context.view_layer.update()
    actual = (rig.matrix_world.to_3x3() @ head.matrix.to_3x3() @ forward_local).normalized()
    if actual.dot(direction) < 0.9999:
        raise RuntimeError(f"人物 {root.name} 的 head 无法到达 look_at 目标；请检查骨架缩放、动画或约束")
    # Keep the original rotation representation, including its rest-axis basis.
    keyframe_rig_state(rig, end)


def keep_transition_above_support(root, geometry, actor: dict, start: int, end: int, fps: int) -> None:
    """Correct stand-up root clearance while its existing pose keys interpolate."""
    for frame in range(start, end + 1):
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        low = actor_body_lowest_z(geometry)
        support = effective_actor_state(actor, frame, fps)["support_z"]
        if low is not None and low < support + CONTACT_CLEARANCE:
            root.location.z += support + CONTACT_CLEARANCE - low
        root.keyframe_insert("location", frame=frame)


def animate_external_actor(root, actor: dict, fps: int, imported: list) -> None:
    rig = next((obj for obj in imported if obj.type == "ARMATURE"), None)
    if rig is not None:
        rig.name = f"{actor['id']}.Rig"
    relaxed = {name: (0, 0, 0) for name in (
        "pelvis", "spine_01", "spine_02", "spine_03", "upperarm_l", "lowerarm_l", "upperarm_r", "lowerarm_r",
        "thigh_l", "calf_l", "foot_l", "thigh_r", "calf_r", "foot_r",
    )}
    relaxed.update({
        "upperarm_l": (0, 0, -45), "lowerarm_l": (0, 0, 0),
        "upperarm_r": (0, 0, 45), "lowerarm_r": (0, 0, 0),
    })
    supported = {"idle", "stand", "sit", "crouch", "bend_over", "stand_up", "walk", "run",
                 "turn", "look_at", "lie_back", "lie_side_left", "lie_side_right"}
    for action in ordered_actions(actor):
        preset = action["preset"]
        if preset not in supported:
            raise RuntimeError(f"人物 {actor['id']} 不支持动作 {preset}；hold_prop 尚无真实持物实现，请提供原生姿态/接触布置，不以站立或摆臂代替")
        if preset in {"walk", "run"} and "to" not in action:
            raise RuntimeError(f"人物 {actor['id']} 的 {preset} 需要 to 终点")
        if rig is None and preset not in {"idle", "turn", "walk", "run"}:
            raise RuntimeError(f"人物 {actor['id']} 没有骨架，无法执行 {preset}；请提供支持该动作的标准人体")
        start, end = action_frames(action, fps)
        support_z = effective_actor_state(actor, start, fps)["support_z"]
        motion_clip = action.get("motion_clip")
        if motion_clip:
            if rig is None:
                raise RuntimeError(f"人物 {actor['id']} 没有骨架，无法播放原生动作 {motion_clip}")
            if preset not in {"walk", "run"} or "to" not in action:
                raise RuntimeError("原生移动动作当前只支持带 to 终点的 walk/run")
            root.rotation_euler.x = root.rotation_euler.y = 0
            root.location.z = support_z + float(root.get("previs_standing_offset_z", 0))
            origin = root.location.copy()
            destination = Vector(action["to"])
            destination.z += root.location.z - support_z
            gait = distance_matched_gait(origin, destination, action, preset)
            steps_per_cycle = float(action.get("motion_steps_per_cycle", 2.0))
            cycle_count = gait["step_count"] / steps_per_cycle
            keyframe(root, start)
            root.location = destination
            root.keyframe_insert("location", frame=end)
            set_interpolation(root, "location", "LINEAR")
            animate_native_motion_clip(rig, str(motion_clip), start, end, cycle_count)
            root["gait_path_distance"] = gait["path_distance"]
            root["gait_step_count"] = gait["step_count"]
            root["gait_step_length"] = gait["step_length"]
            root["gait_cadence_spm"] = gait["cadence_spm"]
            root["gait_motion_source"] = "asset_clip"
            root["gait_motion_clip"] = str(motion_clip)
            root["gait_cycle_count"] = cycle_count
            continue
        # Evaluate the actual predecessor, not a default relaxed pose. Key all
        # existing state at both boundaries so later beats cannot pull a hand,
        # foot or seated pose through this hold by interpolation.
        bpy.context.scene.frame_set(max(1, start - 1))
        bpy.context.view_layer.update()
        if start > 1:
            keyframe(root, start - 1)
            if rig is not None:
                keyframe_rig_state(rig, start - 1)
        keyframe(root, start)
        if rig is not None:
            keyframe_rig_state(rig, start)
        if preset == "look_at":
            animate_head_attention(root, rig, action, start, end)
        elif preset in {"walk", "run"} and "to" in action:
            root.rotation_euler.x = root.rotation_euler.y = 0
            root.location.z = support_z + float(root.get("previs_standing_offset_z", 0))
            origin = root.location.copy()
            destination = Vector(action["to"])
            destination.z += root.location.z - support_z
            gait = distance_matched_gait(origin, destination, action, preset)
            step_count = gait["step_count"]
            amplitude = 28 if preset == "walk" else 48
            for index in range(step_count + 1):
                progress = index / step_count
                frame = round(start + (end - start) * progress)
                root.location = origin.lerp(destination, progress)
                root.keyframe_insert("location", frame=frame)
                sign = 1 if index % 2 == 0 else -1
                pose = dict(relaxed)
                pose.update({
                    "upperarm_l": (sign * amplitude, 0, -45), "upperarm_r": (-sign * amplitude, 0, 45),
                    "thigh_l": (-sign * amplitude * 0.75, 0, 0), "thigh_r": (sign * amplitude * 0.75, 0, 0),
                    "calf_l": (max(0, sign) * amplitude * 0.7, 0, 0), "calf_r": (max(0, -sign) * amplitude * 0.7, 0, 0),
                })
                if rig is not None:
                    animate_rig_pose(rig, frame, pose)
                    bpy.context.scene.frame_set(frame)
                    bpy.context.view_layer.update()
                    support = effective_actor_state(actor, frame, fps)["support_z"]
                    root.location.z += support + CONTACT_CLEARANCE - actor_body_lowest_z(rig)
                    root.keyframe_insert("location", frame=frame)
            destination.z = root.location.z
            root.location = destination
            root["gait_path_distance"] = gait["path_distance"]
            root["gait_step_count"] = gait["step_count"]
            root["gait_step_length"] = gait["step_length"]
            root["gait_cadence_spm"] = gait["cadence_spm"]
        elif preset == "turn":
            root.rotation_euler[2] += math.radians(float(action.get("degrees", 90)))
        elif preset == "sit":
            # 坐姿：有 chair_id 时位置/朝向/座面高度全部由椅子决定，避免人与椅穿模；
            # 没有椅子时退回 surface_height。
            seat_height = float(action.get("surface_height", 0.45))
            pose = {"pelvis": (-8, 0, 0), "thigh_l": (-88, 0, 0), "thigh_r": (-88, 0, 0), "calf_l": (88, 0, 0), "calf_r": (88, 0, 0)}
            chair_id = action.get("chair_id")
            if chair_id:
                seat_box = chair_seat_box(str(chair_id))
                if seat_box is not None:
                    center, top, chair_angle = seat_box
                    # 椅子模型（chair/armchair）的椅背在自身 -Y：人必须背对椅背，所以朝向要加 180°。
                    root.rotation_euler[2] = chair_angle + math.pi
                    facing = Vector((math.sin(root.rotation_euler[2]), -math.cos(root.rotation_euler[2]), 0.0))
                    root.location.x = center.x + facing.x * 0.06
                    root.location.y = center.y + facing.y * 0.06
                    seat_height = top
            if rig is not None:
                # 先把大腿调到水平（膝盖与髋齐平）——模板膝盖会高出髋部一截，脚因此悬空；
                # 再横向收拢膝盖与脚，避免顶进扶手。都按实测位置求解，不写死角度。
                pelvis_bone = rig.pose.bones.get("pelvis")
                thigh_bone = rig.pose.bones.get("thigh_l")
                if pelvis_bone is not None and thigh_bone is not None:
                    best = None
                    for flex in (-88, -92, -96, -100, -104, -108, -112, -84, -80):
                        trial = dict(pose)
                        trial["thigh_l"] = (flex, 0, 0)
                        trial["thigh_r"] = (flex, 0, 0)
                        animate_rig_pose(rig, start, trial)
                        bpy.context.view_layer.update()
                        hip_z = (rig.matrix_world @ pelvis_bone.head).z
                        knee_z = (rig.matrix_world @ thigh_bone.tail).z
                        gap = abs(knee_z - hip_z)
                        if best is None or gap < best[0]:
                            best = (gap, trial)
                    if best is not None:
                        pose = best[1]
                align_feet(rig, start, pose, end_frame=end, tolerance=0.18)
                bpy.context.view_layer.update()
                contact = seat_contact(rig, action)
                if not contact["gaps"] or contact["coverage"] < .5:
                    raise RuntimeError(f"人物 {actor['id']} 无有效臀腿承托：{contact['reason']}")
                if action.get("ground_feet"):
                    drop = actor_body_lowest_z(rig) - (support_z + CONTACT_CLEARANCE)
                else:
                    drop = min(contact["gaps"]) - CONTACT_CLEARANCE
            root.location.z -= drop
            if chair_id and rig is not None:
                narrow = warn_narrow_seat(str(chair_id))
                if narrow:
                    print(f"[previs][warn] {narrow}")
                # 落座之后大腿才降到扶手高度，这时才判断相交；相交就沿坐向往前挪（平移，不改姿态）
                settle_forward(root, rig, str(chair_id))
            keyframe(root, start)
        elif preset in {"stand", "crouch", "bend_over"}:
            # 基础动作档：站 / 蹲 / 弯腰。蹲与弯腰都要按脚掌重新贴地，别穿地板。
            if preset == "stand":
                pose = relaxed
            elif preset == "crouch":
                pose = {
                    "pelvis": (-10, 0, 0), "spine_01": (18, 0, 0), "spine_02": (10, 0, 0),
                    "thigh_l": (-70, 0, 0), "thigh_r": (-70, 0, 0),
                    "calf_l": (95, 0, 0), "calf_r": (95, 0, 0),
                    "upperarm_l": (16, 0, -30), "upperarm_r": (16, 0, 30),
                }
            else:
                pose = {
                    "pelvis": (-4, 0, 0), "spine_01": (30, 0, 0), "spine_02": (22, 0, 0), "spine_03": (12, 0, 0),
                    "thigh_l": (-26, 0, 0), "thigh_r": (-26, 0, 0),
                    "calf_l": (32, 0, 0), "calf_r": (32, 0, 0),
                    "upperarm_l": (28, 0, -16), "upperarm_r": (28, 0, 16),
                }
            root.rotation_euler.x = root.rotation_euler.y = 0
            root.location.z = support_z
            if rig is not None:
                if preset == "crouch":
                    align_feet(rig, start, pose, end_frame=end)
                else:
                    animate_rig_pose(rig, start, pose)
                    animate_rig_pose(rig, end, pose)
                bpy.context.view_layer.update()
                low = actor_body_lowest_z(rig)
                if low is None:
                    raise RuntimeError(f"人物 {actor['id']} 缺少可测量支撑网格")
                root.location.z += support_z + CONTACT_CLEARANCE - low
            keyframe(root, start)
        elif preset == "stand_up":
            root.location.z = support_z
            root.rotation_euler[0] = 0
            root.rotation_euler[1] = 0
            root.rotation_euler[2] = math.radians(float(actor.get("rotation_degrees", 0)))
            if rig is not None:
                animate_rig_pose(rig, end, relaxed)
                bpy.context.scene.frame_set(end)
                root.location.z = support_z
                bpy.context.view_layer.update()
                root.location.z += support_z + CONTACT_CLEARANCE - actor_body_lowest_z(rig)
        elif preset in {"lie_back", "lie_side_left", "lie_side_right"}:
            if preset == "lie_back":
                root.rotation_euler[0] = math.radians(90)
            else:
                root.rotation_euler[1] = math.radians(90 if preset.endswith("left") else -90)
            root.location.z = float(action.get("surface_height", 0.0)) + 0.18
            if rig is not None:
                relax = {"upperarm_l": (8, 0, -18), "upperarm_r": (8, 0, 18), "thigh_l": (-8, 0, 0), "thigh_r": (-12, 0, 0), "calf_l": (18, 0, 0), "calf_r": (24, 0, 0)}
                animate_rig_pose(rig, end, relax)
        keyframe(root, end)
        if rig is not None:
            keyframe_rig_state(rig, end)
        if preset == "stand_up":
            keep_transition_above_support(root, rig or root, actor, start, end, fps)
        if preset in {"walk", "run"} and "to" in action:
            set_interpolation(root, "location", "LINEAR")


def asset_decimate(meshes: list, target_faces: int) -> None:
    """外部几何加工厂的缺省降面：把导入的高模撞到目标面数预算内，保持轻量。

    target_faces <= 0 表示不做；每个 mesh 若已低于预算则跳过（避免无意义重算）。
    """
    if target_faces <= 0:
        return
    for obj in meshes:
        # 形态键会阻止修改器应用（MPFB 人体模板自带），先清掉再抽面
        clear_shape_keys(obj)
        mesh = obj.data
        face_count = len(mesh.polygons)
        if face_count == 0 or face_count <= target_faces:
            continue
        ratio = max(0.0, min(1.0, target_faces / face_count))
        modifier = obj.modifiers.new("PrevisDecimate", "DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = ratio
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        try:
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        finally:
            obj.select_set(False)


def align_feet(rig, frame: int, pose: dict, end_frame: int | None = None, max_inward: int = 64, tolerance: float = 0.15):
    """蹲/坐时腿会外八：逐步加内侧旋转把脚收到骨盆下方附近，再写关键帧。

    骨骼的 rest 朝向本身带外撇，加上屈髋后脚尖会甩到身体外侧很远（实测 0.46 米），
    坐姿还会把膝盖顶进扶手（实测膝盖 ±0.28 米，扶手在 ±0.27 起）。所以不写死角度，
    按脚与骨盆的横向距离数值求解，收到 tolerance 以内为止。
    """
    pelvis = rig.pose.bones.get("pelvis")
    solved = dict(pose)
    thigh_l = pose.get("thigh_l", (0, 0, 0))
    thigh_r = pose.get("thigh_r", (0, 0, 0))
    for inward in range(0, max_inward + 1, 4):
        trial = dict(pose)
        trial["thigh_l"] = (thigh_l[0], thigh_l[1], inward)
        trial["thigh_r"] = (thigh_r[0], thigh_r[1], -inward)
        animate_rig_pose(rig, frame, trial)
        bpy.context.view_layer.update()
        knees = [rig.pose.bones[name] for name in ("thigh_l", "thigh_r") if rig.pose.bones.get(name)]
        feet = [rig.pose.bones[name] for name in ("foot_l", "foot_r") if rig.pose.bones.get(name)]
        solved = trial
        if pelvis is None or not feet:
            break
        pelvis_x = (rig.matrix_world @ pelvis.head).x
        # 膝盖比脚更容易顶到扶手，两者都要量
        widest = max(
            [abs((rig.matrix_world @ bone.tail).x - pelvis_x) for bone in knees + feet] or [0.0]
        )
        if widest <= tolerance:
            break
    animate_rig_pose(rig, frame, solved)
    if end_frame is not None:
        animate_rig_pose(rig, end_frame, solved)


def settle_forward(root, rig, chair_id: str, step: float = 0.02, limit: int = 14) -> float:
    """坐姿自动往前挪：只在大腿真的与扶手重叠时平移（不改姿态），且髋部不越过座面前缘。"""
    if not chair_arm_boxes(chair_id):
        return 0.0
    facing = Vector((math.sin(root.rotation_euler[2]), -math.cos(root.rotation_euler[2]), 0.0))
    front = chair_seat_front_along(chair_id, facing)
    pelvis = rig.pose.bones.get("pelvis")
    moved = 0.0
    for _ in range(limit):
        bpy.context.view_layer.update()
        if not thighs_hit_armrests(rig, chair_id):
            break
        if front is not None and pelvis is not None:
            hip = rig.matrix_world @ pelvis.head
            room = front - (hip.x * facing.x + hip.y * facing.y) - 0.04
            if room < step:
                break
        root.location.x += facing.x * step
        root.location.y += facing.y * step
        moved += step
    return moved


# natural_proxy 支持的对象类型：这里是唯一权威，文档只指向它，不另抄一份（否则两边会漂移）
NATURAL_PROXY_KINDS = {
    "proxy_box",
    "proxy_cylinder",
    "proxy_cone",
    "proxy_sphere",
    "proxy_rock",
    "chair",
    "armchair",
    "bench_seat",
    "glass_balustrade",
}


def import_external_actor(root, actor: dict, fps: int) -> bool:
    asset_path = actor.get("asset_path")
    if not asset_path:
        return False
    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(asset_path), import_pack_images=True)
    imported = [obj for obj in bpy.context.scene.objects if obj not in before]
    top_level = [obj for obj in imported if obj.parent is None]
    holder = bpy.data.objects.new(f"{actor['id']}.external", None)
    bpy.context.collection.objects.link(holder)
    holder.parent = root
    for obj in top_level:
        obj.parent = holder
    mesh_objects = [obj for obj in imported if obj.type == "MESH"]
    skinned_meshes = [
        obj
        for obj in mesh_objects
        if any(modifier.type == "ARMATURE" for modifier in obj.modifiers)
    ]
    actor_meshes = skinned_meshes or [obj for obj in mesh_objects if len(obj.data.materials) > 0] or mesh_objects
    apply_actor_identity_material(actor, actor_meshes)
    decimate_to = actor.get("decimate_to_faces")
    if decimate_to:
        asset_decimate(actor_meshes, int(decimate_to))
    # Some reusable GLB actors contain unmaterialized helper geometry (for
    # example an export-time reference sphere). It is not part of the skinned
    # character and must not affect ground alignment or appear in previews.
    for obj in mesh_objects:
        if obj not in actor_meshes:
            obj.hide_render = True
            obj.hide_viewport = True
    for obj in imported:
        if obj.type in {"CAMERA", "LIGHT"}:
            obj.hide_render = True
            obj.hide_viewport = True
    normalize_actor(root, actor, actor_target_height(actor))
    animate_external_actor(root, actor, fps, imported)
    return True


def create_actor(actor: dict, fps: int):
    role = actor.get("role", "generic")
    primary_color, accent_color = role_palette(role)
    if actor.get("_resolved_display_color"):
        primary_color = rgba_from_hex(str(actor["_resolved_display_color"]))
        accent_color = lighter_color(primary_color)
    primary = material(f"{actor['id']}.primary", primary_color)
    accent = material(f"{actor['id']}.accent", accent_color)
    skin = material(f"{actor['id']}.skin", (0.72, 0.49, 0.37, 1))
    hair = material(f"{actor['id']}.hair", (0.035, 0.025, 0.022, 1))
    gold = material(f"{actor['id']}.gold", (0.72, 0.45, 0.08, 1), 0.35)

    root = bpy.data.objects.new(actor["id"], None)
    bpy.context.collection.objects.link(root)
    root.location = Vector(actor.get("position", [0, 0, 0]))
    root.rotation_euler[2] = math.radians(float(actor.get("rotation_degrees", 0)))
    root.scale = (float(actor.get("scale", 1)),) * 3
    if actor.get("_resolved_display_color"):
        root["display_color"] = str(actor["_resolved_display_color"])
        root["display_color_source"] = str(actor.get("_display_color_source", "explicit"))

    if import_external_actor(root, actor, fps):
        return root

    proxy_supported = {"idle", "stand", "sit", "crouch", "bend_over", "stand_up", "walk", "turn"}
    for action in ordered_actions(actor):
        if action["preset"] not in proxy_supported or action.get("motion_clip"):
            raise RuntimeError(f"占位体 {actor['id']} 不支持 {action['preset']}；look_at 需要标准人体头骨，hold_prop 需要原生持物/接触布置，不能用静止或摆臂代替")
        if action["preset"] == "walk" and "to" not in action:
            raise RuntimeError(f"占位体 {actor['id']} 的 walk 需要 to 终点")

    torso = cone(f"{actor['id']}.torso", (0, 0, 1.33), 0.31, 0.22, 0.7, primary, root)
    neck = cylinder(f"{actor['id']}.neck", (0, 0, 1.73), 0.085, 0.15, skin, root)
    head = sphere(f"{actor['id']}.head", (0, -0.01, 1.98), 0.25, skin, root)
    hair_cap = sphere(f"{actor['id']}.hair", (0, 0.035, 2.06), 0.255, hair, root)
    hair_cap.scale = (1.02, 1.02, 0.72)
    face = material(f"{actor['id']}.face", (0.035, 0.025, 0.02, 1))
    # Characters face +Y. Eyes and nose make front/back readable in every preset.
    sphere(f"{actor['id']}.eye.L", (-0.085, 0.225, 2.02), 0.025, face, root)
    sphere(f"{actor['id']}.eye.R", (0.085, 0.225, 2.02), 0.025, face, root)
    cone(f"{actor['id']}.nose", (0, 0.27, 1.96), 0.035, 0.008, 0.11, skin, root).rotation_euler[0] = math.radians(90)

    parts = {"root": root, "torso": torso}
    for side, x in (("L", -0.37), ("R", 0.37)):
        arm = cylinder(f"{actor['id']}.arm.{side}", (x, 0, 1.38), 0.075, 0.58, primary, root)
        hand = sphere(f"{actor['id']}.hand.{side}", (x, 0, 1.04), 0.09, skin, root)
        leg = cylinder(f"{actor['id']}.leg.{side}", (x * 0.48, 0, 0.85), 0.105, 0.44, primary, root)
        leg["previs_contact_role"] = "thigh"
        calf = cylinder(f"{actor['id']}.calf.{side}", (x * 0.48, 0, 0.41), 0.09, 0.44, primary, root)
        parts[f"calf.{side}"] = calf
        shoe = cube(f"{actor['id']}.shoe.{side}", (x * 0.48, 0.09, 0.14), (0.14, 0.24, 0.09), hair, root, 0.05)
        parts[f"arm.{side}"] = arm
        parts[f"hand.{side}"] = hand
        parts[f"leg.{side}"] = leg
        parts[f"shoe.{side}"] = shoe

    if role == "princess":
        cone(f"{actor['id']}.skirt", (0, 0, 0.8), 0.55, 0.26, 1.05, accent, root)
        bun = sphere(f"{actor['id']}.bun", (0, -0.16, 2.22), 0.16, hair, root)
        for index, x in enumerate((-0.12, 0, 0.12)):
            cone(f"{actor['id']}.crown.{index}", (x, 0, 2.33), 0.07, 0, 0.22, gold, root)
    elif role == "maid":
        cone(f"{actor['id']}.skirt", (0, 0, 0.78), 0.48, 0.25, 1.0, primary, root)
        cube(f"{actor['id']}.apron", (0, 0.265, 1.02), (0.28, 0.025, 0.42), accent, root, 0.02)
        cube(f"{actor['id']}.cap", (0, 0.01, 2.25), (0.23, 0.08, 0.055), accent, root, 0.03)
    elif role == "butler":
        cube(f"{actor['id']}.shirt", (0, 0.225, 1.42), (0.14, 0.03, 0.22), accent, root, 0.01)
        cube(f"{actor['id']}.bow", (0, 0.27, 1.61), (0.13, 0.035, 0.055), primary, root, 0.02)
        watch = sphere(f"{actor['id']}.watch", (0.38, 0.25, 1.13), 0.1, gold, root)
        watch.scale = (1, 0.25, 1)

    normalize_actor(root, actor, actor_target_height(actor, coarse=True))
    # Local part transforms are the coarse backend's rest pose. They provide
    # the same posture/support semantics as the rig without requiring a rig.
    parts = {obj.name: obj for obj in root.children_recursive if obj.type == "MESH"}
    rest = {name: (obj.location.copy(), obj.rotation_euler.copy(), obj.scale.copy())
            for name, obj in parts.items()}

    def part(name):
        return parts[f"{actor['id']}.{name}"]

    def hold(frame):
        keyframe(root, frame)
        for obj in parts.values():
            keyframe(obj, frame)
            obj.keyframe_insert("scale", frame=frame)

    def reset_pose():
        for name, obj in parts.items():
            obj.location, obj.rotation_euler, obj.scale = rest[name]

    def align_support(support):
        bpy.context.view_layer.update()
        root.location.z += support + CONTACT_CLEARANCE - actor_body_lowest_z(root)
        bpy.context.view_layer.update()

    def sit_pose(action):
        reset_pose()
        chair_id = action.get("chair_id")
        if chair_id:
            seat = chair_seat_box(str(chair_id))
            if seat is None:
                raise RuntimeError(f"人物 {actor['id']} 找不到声明的椅子/座面 {chair_id}")
            center, _top, chair_angle = seat
            # Coarse faces +Y; imported human faces -Y. Both face away from
            # this chair model's -Y back, using their own anatomical forward.
            root.rotation_euler.z = chair_angle
            root.location.x, root.location.y = center.x, center.y
        torso.rotation_euler.x = math.radians(-7)
        for side in ("L", "R"):
            thigh, calf, shoe = part(f"leg.{side}"), part(f"calf.{side}"), part(f"shoe.{side}")
            thigh.rotation_euler.x = math.radians(-90)
            thigh.location.y, thigh.location.z = .22, .98
            calf.location.y, calf.location.z = .44, .75
            shoe.location.y, shoe.location.z = .50, .49
        skirt = parts.get(f"{actor['id']}.skirt")
        if skirt is not None:
            skirt.scale.z *= .5
            skirt.location.z = .95
        bpy.context.view_layer.update()
        contact = seat_contact(root, action)
        if not contact["gaps"] or contact["coverage"] < .5:
            raise RuntimeError(f"人物 {actor['id']} 无有效臀腿承托：{contact['reason']}")
        root.location.z += CONTACT_CLEARANCE - min(contact["gaps"])
        if action.get("ground_feet"):
            align_support(effective_actor_state(actor, action_frames(action, fps)[0], fps)["support_z"])

    def standing_pose(preset, support):
        reset_pose()
        if preset == "bend_over":
            torso.rotation_euler.x = math.radians(52)
        elif preset == "crouch":
            torso.rotation_euler.x = math.radians(14)
            for side in ("L", "R"):
                part(f"leg.{side}").rotation_euler.x = math.radians(-58)
                part(f"leg.{side}").location.y = .16
        align_support(support)

    for action in ordered_actions(actor):
        preset = action["preset"]
        start, end = action_frames(action, fps)
        # Freeze all body parts immediately before the next action. This
        # prevents a later stand key from pulling an idle seated hold upright.
        bpy.context.scene.frame_set(max(1, start - 1))
        bpy.context.view_layer.update()
        if start > 1:
            hold(start - 1)
        support = effective_actor_state(actor, start, fps)["support_z"]
        if preset == "sit":
            sit_pose(action)
            hold(start)
            hold(end)
        elif preset in {"stand", "crouch", "bend_over"}:
            standing_pose(preset, support)
            hold(start)
            hold(end)
        elif preset == "stand_up":
            hold(start)
            standing_pose("stand", support)
            hold(end)
            keep_transition_above_support(root, root, actor, start, end, fps)
        elif preset == "walk":
            standing_pose("stand", support)
            origin = root.location.copy()
            destination = Vector(action["to"])
            destination.z += origin.z - support
            gait = distance_matched_gait(origin, destination, action, preset)
            for index in range(gait["step_count"] + 1):
                progress = index / gait["step_count"]
                frame = round(start + (end - start) * progress)
                root.location = origin.lerp(destination, progress)
                swing = math.radians(24 if index % 2 == 0 else -24)
                part("arm.L").rotation_euler.x, part("arm.R").rotation_euler.x = swing, -swing
                part("leg.L").rotation_euler.x, part("leg.R").rotation_euler.x = -swing * .55, swing * .55
                hold(frame)
            root["gait_path_distance"] = gait["path_distance"]
            root["gait_step_count"] = gait["step_count"]
            root["gait_step_length"] = gait["step_length"]
            root["gait_cadence_spm"] = gait["cadence_spm"]
            set_interpolation(root, "location", "LINEAR")
        elif preset == "turn":
            hold(start)
            root.rotation_euler.z += math.radians(float(action.get("degrees", 90)))
            hold(end)
        else:
            hold(start)
            hold(end)
    return root


def create_room(env: dict, preset: str):
    width = float(env.get("width", 8))
    depth = float(env.get("depth", 10))
    height = float(env.get("height", 4.5))
    floor_mat = material("Room.floor", (0.22, 0.13, 0.09, 1))
    wall_mat = material("Room.wall", (0.78, 0.62, 0.65, 1))
    trim_mat = material("Room.trim", (0.82, 0.67, 0.38, 1), 0.38)
    cube("Room.floor", (0, depth / 2 - 2, -0.08), (width / 2, depth / 2, 0.08), floor_mat, bevel=0)
    cube("Room.back", (0, depth - 2, height / 2), (width / 2, 0.08, height / 2), wall_mat, bevel=0)
    cube("Room.left", (-width / 2, depth / 2 - 2, height / 2), (0.08, depth / 2, height / 2), wall_mat, bevel=0)
    cube("Room.right", (width / 2, depth / 2 - 2, height / 2), (0.08, depth / 2, height / 2), wall_mat, bevel=0)
    if preset == "bedroom":
        bed = cube("Bedroom.bed", (-1.8, 0.1, 0.45), (1.45, 2.0, 0.45), material("Bed.base", (0.55, 0.26, 0.34, 1)), bevel=0.12)
        cube("Bedroom.mattress", (-1.8, 0.1, 0.94), (1.38, 1.93, 0.12), material("Bed.linen", (0.92, 0.75, 0.79, 1)), bevel=0.1)
        cube("Bedroom.headboard", (-1.8, 2.0, 1.3), (1.5, 0.15, 1.0), wall_mat, bevel=0.12)
        for x in (-3.0, 3.0):
            cube(f"Room.column.{x}", (x, 5.8, height / 2), (0.13, 0.13, height / 2), trim_mat, bevel=0.03)
        _ = bed


def object_root(name: str, location, rotation_degrees: float = 0):
    root = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(root)
    root.location = Vector(location)
    root.rotation_euler[2] = math.radians(rotation_degrees)
    return root


def tag_reconstruction(root, item: dict) -> None:
    root["reconstruction_kind"] = str(item.get("kind", "unknown"))
    root["reconstruction_provenance"] = str(item.get("provenance", "inferred"))
    root["reconstruction_confidence"] = float(item.get("confidence", 0.5))
    root["reconstruction_size"] = list(item.get("size", [1, 1, 1]))


def create_arch_window(item: dict, mats: dict):
    root = object_root(item["id"], item["position"], item.get("rotation_degrees", 0))
    tag_reconstruction(root, item)
    width, depth, height = item.get("size", [2.4, 0.18, 3.2])
    cube(f"{item['id']}.glass", (0, 0, height * 0.48), (width / 2, depth / 2, height * 0.48), mats["glass"], root, 0.01)
    for x in (-width / 2, 0, width / 2):
        cube(f"{item['id']}.mullion.{x}", (x, -depth * 0.52, height * 0.48), (0.045, 0.04, height * 0.48), mats["trim"], root, 0.01)
    for z in (0, height * 0.32, height * 0.64, height * 0.96):
        cube(f"{item['id']}.rail.{z}", (0, -depth * 0.52, z), (width / 2, 0.04, 0.045), mats["trim"], root, 0.01)
    return root


def create_curtain(item: dict, mats: dict):
    root = object_root(item["id"], item["position"], item.get("rotation_degrees", 0))
    tag_reconstruction(root, item)
    width, depth, height = item.get("size", [1.2, 0.25, 4.0])
    folds = max(5, int(width / 0.16))
    for index in range(folds):
        x = -width / 2 + width * (index + 0.5) / folds
        y = math.sin(index * math.pi) * depth * 0.38
        radius = width / folds * 0.62
        cylinder(f"{item['id']}.fold.{index:02d}", (x, y, height / 2), radius, height, mats["fabric"], root)
    cube(f"{item['id']}.pelmet", (0, 0, height), (width * 0.58, depth, 0.14), mats["fabric"], root, 0.08)
    return root


def create_furniture(item: dict, mats: dict):
    root = object_root(item["id"], item["position"], item.get("rotation_degrees", 0))
    tag_reconstruction(root, item)
    kind = item["kind"]
    width, depth, height = item.get("size", [1.4, 0.7, 1.0])
    if kind == "bed":
        cube(f"{item['id']}.frame", (0, 0, height * 0.3), (width / 2, depth / 2, height * 0.3), mats["wood"], root, 0.1)
        cube(f"{item['id']}.mattress", (0, 0, height * 0.68), (width * 0.48, depth * 0.48, height * 0.18), mats["linen"], root, 0.12)
        cube(f"{item['id']}.blanket", (0, depth * 0.12, height * 0.91), (width * 0.49, depth * 0.35, height * 0.055), mats["fabric"], root, 0.06)
        for x in (-width * 0.3, width * 0.3):
            cube(f"{item['id']}.pillow.{x}", (x, -depth * 0.31, height * 0.96), (width * 0.17, depth * 0.13, height * 0.1), mats["linen"], root, 0.12)
        cube(f"{item['id']}.headboard", (0, -depth / 2, height * 1.35), (width / 2, 0.11, height * 0.7), mats["fabric"], root, 0.16)
    elif kind in {"dresser", "vanity"}:
        cube(f"{item['id']}.body", (0, 0, height / 2), (width / 2, depth / 2, height / 2), mats["wood"], root, 0.06)
        for row in range(2):
            for col in range(2):
                x = (-0.24 if col == 0 else 0.24) * width
                z = (0.32 + row * 0.34) * height
                cube(f"{item['id']}.drawer.{row}.{col}", (x, -depth * 0.51, z), (width * 0.22, 0.025, height * 0.13), mats["panel"], root, 0.02)
                sphere(f"{item['id']}.knob.{row}.{col}", (x, -depth * 0.56, z), 0.035, mats["gold"], root)
    elif kind == "chair":
        cube(f"{item['id']}.seat", (0, 0, height * 0.48), (width / 2, depth / 2, height * 0.1), mats["fabric"], root, 0.08)
        cube(f"{item['id']}.back", (0, depth * 0.42, height * 0.95), (width / 2, depth * 0.09, height * 0.48), mats["fabric"], root, 0.12)
        for x in (-width * 0.4, width * 0.4):
            for y in (-depth * 0.38, depth * 0.38):
                cylinder(f"{item['id']}.leg.{x}.{y}", (x, y, height * 0.22), 0.045, height * 0.44, mats["gold"], root)
    return root


def create_decor(item: dict, mats: dict):
    root = object_root(item["id"], item["position"], item.get("rotation_degrees", 0))
    tag_reconstruction(root, item)
    kind = item["kind"]
    width, depth, height = item.get("size", [1.2, 0.12, 1.8])
    if kind in {"mirror", "portrait"}:
        cube(f"{item['id']}.frame", (0, 0, height / 2), (width / 2, depth / 2, height / 2), mats["gold"], root, 0.05)
        cube(f"{item['id']}.surface", (0, -depth * 0.55, height / 2), (width * 0.43, 0.015, height * 0.43), mats["mirror" if kind == "mirror" else "portrait"], root, 0.02)
    elif kind == "rug":
        cube(f"{item['id']}.surface", (0, 0, 0.025), (width / 2, depth / 2, max(height / 2, 0.025)), mats["rug"], root, 0.06)
    elif kind == "candle_cluster":
        count = int(item.get("count", 5))
        for index in range(count):
            x = (index - (count - 1) / 2) * min(0.18, width / max(count, 1))
            candle_h = height * (0.72 + 0.2 * ((index + 1) % 2))
            cylinder(f"{item['id']}.candle.{index}", (x, 0, candle_h / 2), 0.025, candle_h, mats["linen"], root)
            sphere(f"{item['id']}.flame.{index}", (x, 0, candle_h + 0.05), 0.045, mats["flame"], root)
    elif kind == "chandelier":
        cylinder(f"{item['id']}.stem", (0, 0, -height / 2), 0.035, height, mats["gold"], root)
        for index in range(8):
            angle = index * math.tau / 8
            x, y = math.cos(angle) * width * 0.35, math.sin(angle) * width * 0.35
            cylinder(f"{item['id']}.arm.{index}", (x / 2, y / 2, -height * 0.85), 0.025, width * 0.4, mats["gold"], root).rotation_euler[1] = math.radians(90)
            sphere(f"{item['id']}.drop.{index}", (x, y, -height * 0.92), 0.07, mats["glass"], root)
    return root


def annular_prism(name: str, outer_radius: float, inner_radius: float, height: float, mat, parent=None, segments=64):
    vertices = []
    for z in (0.0, height):
        for radius in (outer_radius, inner_radius):
            for index in range(segments):
                angle = math.tau * index / segments
                vertices.append((math.cos(angle) * radius, math.sin(angle) * radius, z))
    outer_bottom, inner_bottom = 0, segments
    outer_top, inner_top = segments * 2, segments * 3
    faces = []
    for index in range(segments):
        nxt = (index + 1) % segments
        faces.extend([
            (outer_bottom + index, outer_bottom + nxt, outer_top + nxt, outer_top + index),
            (inner_bottom + nxt, inner_bottom + index, inner_top + index, inner_top + nxt),
            (outer_top + index, outer_top + nxt, inner_top + nxt, inner_top + index),
            (outer_bottom + nxt, outer_bottom + index, inner_bottom + index, inner_bottom + nxt),
        ])
    mesh = bpy.data.meshes.new(f"{name}.Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    if mat:
        apply_material(obj, mat)
    if parent:
        obj.parent = parent
    bevel = obj.modifiers.new("Soft edges", "BEVEL")
    bevel.width = min(0.06, height * 0.12)
    bevel.segments = 2
    return obj


def curve_tube(name: str, points, bevel_depth: float, mat, parent=None):
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.bevel_depth = bevel_depth
    data.bevel_resolution = 3
    spline = data.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, co in zip(spline.points, points):
        point.co = (*co, 1)
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    apply_material(obj, mat)
    if parent:
        obj.parent = parent
    return obj


def create_circular_console(item: dict, mats: dict):
    root = object_root(item["id"], item["position"], item.get("rotation_degrees", 0))
    tag_reconstruction(root, item)
    width, depth, height = item.get("size", [6.4, 6.4, 1.25])
    outer = max(width, depth) / 2
    inner = outer * 0.58
    base = annular_prism(f"{item['id']}.base", outer, inner, height * 0.72, mats["fabric"], root)
    top = annular_prism(f"{item['id']}.top", outer * 1.03, inner * 0.93, height * 0.16, mats["panel"], root)
    top.location.z = height * 0.72
    gold_outer = annular_prism(f"{item['id']}.gold.outer", outer * 1.04, outer * 1.0, height * 0.05, mats["gold"], root)
    gold_outer.location.z = height * 0.9
    gold_inner = annular_prism(f"{item['id']}.gold.inner", inner * 1.02, inner * 0.94, height * 0.05, mats["gold"], root)
    gold_inner.location.z = height * 0.9
    tablets = int(item.get("count", 12))
    for index in range(tablets):
        angle = math.tau * index / tablets
        radius = (outer + inner) * 0.5
        x, y = math.cos(angle) * radius, math.sin(angle) * radius
        screen = cube(f"{item['id']}.tablet.{index:02d}", (x, y, height * 1.02), (0.25, 0.022, 0.15), mats["screen"], root, 0.02)
        screen.rotation_euler = (math.radians(68), 0, angle + math.pi / 2)
    for index in range(18):
        angle = math.tau * index / 18
        x, y = math.cos(angle) * outer * 1.012, math.sin(angle) * outer * 1.012
        rib = cube(f"{item['id']}.rib.{index:02d}", (x, y, height * 0.36), (0.035, 0.065, height * 0.31), mats["gold"], root, 0.01)
        rib.rotation_euler[2] = angle
    return root


def create_command_display(item: dict, mats: dict):
    root = object_root(item["id"], item["position"], item.get("rotation_degrees", 0))
    tag_reconstruction(root, item)
    width, depth, height = item.get("size", [8.0, 0.18, 4.0])
    spring = height * 0.65
    cube(f"{item['id']}.screen", (0, 0, spring / 2), (width / 2, depth / 2, spring / 2), mats["screen"], root, 0.08)
    arch_radius = width / 2
    points = [(math.cos(angle) * arch_radius, -depth * 0.62, spring + math.sin(angle) * height * 0.28) for angle in [math.pi * i / 32 for i in range(33)]]
    curve_tube(f"{item['id']}.arch", points, 0.065, mats["gold"], root)
    for x in (-width / 4, 0, width / 4):
        cube(f"{item['id']}.divider.x.{x}", (x, -depth * 0.62, spring / 2), (0.025, 0.025, spring / 2), mats["gold"], root, 0.01)
    for z in (height * 0.22, height * 0.43, height * 0.64):
        cube(f"{item['id']}.data.line.{z}", (0, -depth * 0.68, z), (width * 0.42, 0.012, 0.012), mats["screen_bright"], root, 0)
    for index in range(10):
        x = -width * 0.38 + (index % 5) * width * 0.19
        z = height * (0.22 + (index // 5) * 0.28)
        cube(f"{item['id']}.data.panel.{index:02d}", (x, -depth * 0.7, z), (width * 0.065, 0.01, height * 0.08), mats["screen_bright"], root, 0.02)
    return root


def create_console_bank(item: dict, mats: dict):
    root = object_root(item["id"], item["position"], item.get("rotation_degrees", 0))
    tag_reconstruction(root, item)
    width, depth, height = item.get("size", [9.0, 0.7, 1.05])
    cube(f"{item['id']}.cabinet", (0, 0, height * 0.4), (width / 2, depth / 2, height * 0.4), mats["fabric"], root, 0.05)
    count = int(item.get("count", 8))
    for index in range(count):
        x = -width * 0.44 + width * 0.88 * index / max(count - 1, 1)
        screen = cube(f"{item['id']}.screen.{index:02d}", (x, -depth * 0.28, height * 0.98), (width / count * 0.32, 0.025, height * 0.2), mats["screen"], root, 0.025)
        screen.rotation_euler[0] = math.radians(68)
    return root


def create_arch_door(item: dict, mats: dict):
    root = object_root(item["id"], item["position"], item.get("rotation_degrees", 0))
    tag_reconstruction(root, item)
    width, depth, height = item.get("size", [2.3, 0.25, 4.3])
    spring = height - width / 2
    if not item.get("open", False):
        cube(f"{item['id']}.door", (0, 0, spring / 2), (width * 0.43, depth / 2, spring / 2), mats["fabric"], root, 0.05)
    for x in (-width / 2, width / 2):
        cube(f"{item['id']}.pillar.{x}", (x, 0, spring / 2), (0.075, depth, spring / 2), mats["gold"], root, 0.02)
    points = [(math.cos(angle) * width / 2, 0, spring + math.sin(angle) * width / 2) for angle in [math.pi * i / 32 for i in range(33)]]
    curve_tube(f"{item['id']}.arch", points, 0.075, mats["gold"], root)
    return root


def create_corridor(item: dict, mats: dict):
    root = object_root(item["id"], item["position"], item.get("rotation_degrees", 0))
    tag_reconstruction(root, item)
    width, depth, height = item.get("size", [2.2, 7.0, 3.8])
    cube(f"{item['id']}.floor", (0, depth / 2, 0), (width / 2, depth / 2, 0.06), mats["floor"], root, 0)
    cube(f"{item['id']}.ceiling", (0, depth / 2, height), (width / 2, depth / 2, 0.06), mats["panel"], root, 0)
    open_near_side = bool(item.get("open_near_side", False))
    for x in (-width / 2, width / 2):
        if open_near_side and x > 0:
            continue
        cube(f"{item['id']}.wall.{x}", (x, depth / 2, height / 2), (0.06, depth / 2, height / 2), mats["wall"], root, 0)
    for index in range(5):
        y = depth * (index + 0.5) / 5
        cube(f"{item['id']}.light.{index}", (0, y, height - 0.08), (width * 0.32, depth * 0.045, 0.025), mats["screen_bright"], root, 0.01)
        cube(f"{item['id']}.wall.band.{index}", (-width / 2 + 0.04, y, height / 2), (0.035, 0.035, height / 2), mats["gold"], root, 0.01)
    return root


def create_architectural_detail(item: dict, mats: dict):
    root = object_root(item["id"], item["position"], item.get("rotation_degrees", 0))
    tag_reconstruction(root, item)
    kind = item["kind"]
    width, depth, height = item.get("size", [1.0, 0.2, 1.0])
    if kind == "ceiling_panel":
        cube(f"{item['id']}.frame", (0, 0, 0), (width / 2, depth / 2, height / 2), mats["gold"], root, 0.16)
        cube(f"{item['id']}.light", (0, 0, -height * 0.03), (width * 0.45, depth * 0.45, height * 0.54), mats["screen_bright"], root, 0.12)
        for index in range(1, 6):
            x = -width * 0.45 + width * 0.9 * index / 6
            cube(f"{item['id']}.grid.x.{index}", (x, 0, -height * 0.62), (0.018, depth * 0.45, 0.012), mats["gold"], root, 0)
        for index in range(1, 4):
            y = -depth * 0.45 + depth * 0.9 * index / 4
            cube(f"{item['id']}.grid.y.{index}", (0, y, -height * 0.62), (width * 0.45, 0.018, 0.012), mats["gold"], root, 0)
    elif kind == "wall_sconce":
        cube(f"{item['id']}.body", (0, 0, height / 2), (width / 2, depth / 2, height / 2), mats["screen_bright"], root, width * 0.2)
        cube(f"{item['id']}.frame", (0, depth * 0.55, height / 2), (width * 0.65, 0.025, height * 0.55), mats["gold"], root, 0.03)
    elif kind == "planter":
        cylinder(f"{item['id']}.pot", (0, 0, height * 0.25), width * 0.38, height * 0.5, mats["fabric"], root)
        for index in range(9):
            angle = math.tau * index / 9
            stem = cylinder(f"{item['id']}.stem.{index}", (math.cos(angle) * width * 0.14, math.sin(angle) * width * 0.14, height * 0.68), 0.018, height * 0.75, mats["plant"], root)
            stem.rotation_euler = (math.sin(angle) * 0.22, math.cos(angle) * 0.22, 0)
            sphere(f"{item['id']}.flower.{index}", (math.cos(angle) * width * 0.28, math.sin(angle) * width * 0.28, height * 1.0), width * 0.12, mats["flower"], root)
    elif kind == "floor_inlay":
        outer = max(width, depth) / 2
        ring = annular_prism(f"{item['id']}.ring", outer, outer * 0.965, max(height, 0.015), mats["gold"], root)
        ring.location.z = 0.005
    return root


def create_structured_scene(env: dict):
    room = env.get("room", {})
    width, depth, height = [float(value) for value in room.get("size", [8, 10, 4.8])]
    royal = env.get("style") == "royal_command_center"
    mats = {
        "floor": material("Scene.floor", (0.55, 0.32, 0.3, 1) if royal else (0.18, 0.075, 0.045, 1), 0.18 if royal else 0.72),
        "wall": material("Scene.wall", (0.72, 0.38, 0.41, 1) if royal else (0.82, 0.58, 0.62, 1), 0.28 if royal else 0.72),
        "panel": material("Scene.panel", (0.84, 0.56, 0.57, 1) if royal else (0.9, 0.72, 0.71, 1), 0.22 if royal else 0.72),
        "trim": material("Scene.trim", (0.72, 0.47, 0.18, 1), 0.32),
        "wood": material("Scene.wood", (0.32, 0.12, 0.08, 1)),
        "fabric": material("Scene.fabric", (0.66, 0.29, 0.36, 1) if royal else (0.78, 0.38, 0.49, 1)),
        "linen": material("Scene.linen", (0.94, 0.84, 0.84, 1)),
        "gold": material("Scene.gold", (0.72, 0.42, 0.09, 1), 0.28),
        "glass": material("Scene.glass", (0.45, 0.68, 0.82, 1), 0.16),
        "mirror": material("Scene.mirror", (0.38, 0.43, 0.48, 1), 0.08),
        "portrait": material("Scene.portrait", (0.34, 0.17, 0.19, 1)),
        "rug": material("Scene.rug", (0.55, 0.22, 0.34, 1)),
        "flame": material("Scene.flame", (1.0, 0.38, 0.05, 1), 0.18),
        "screen": emissive_material("Scene.screen", (0.88, 0.27, 0.4, 1), 1.5),
        "screen_bright": emissive_material("Scene.screen.bright", (1.0, 0.72, 0.68, 1), 5.0),
        "plant": material("Scene.plant", (0.11, 0.28, 0.13, 1), 0.7),
        "flower": material("Scene.flower", (0.62, 0.12, 0.28, 1), 0.55),
    }
    root = object_root("Reconstruction.room", [0, 0, 0])
    root["reconstruction_provenance"] = "mixed_visible_and_inferred"
    root["visible_evidence"] = json.dumps(room.get("visible_evidence", []), ensure_ascii=False)
    root["inferred_completion"] = json.dumps(room.get("inferred_completion", []), ensure_ascii=False)
    cube("Room.floor", (0, depth / 2, -0.08), (width / 2, depth / 2, 0.08), mats["floor"], root, 0)
    cube("Room.ceiling", (0, depth / 2, height + 0.08), (width / 2, depth / 2, 0.08), mats["panel"], root, 0)
    cube("Room.back", (0, depth, height / 2), (width / 2, 0.08, height / 2), mats["wall"], root, 0)
    cube("Room.front", (0, 0, height / 2), (width / 2, 0.08, height / 2), mats["wall"], root, 0)

    openings = room.get("openings", [])

    def side_wall(side: str, x: float) -> None:
        opening = next((value for value in openings if value.get("side") == side), None)
        if opening is None:
            cube(f"Room.{side}", (x, depth / 2, height / 2), (0.08, depth / 2, height / 2), mats["wall"], root, 0)
            return
        center_y = float(opening["center_y"])
        opening_width = min(float(opening["width"]), depth)
        opening_height = min(float(opening["height"]), height)
        start = max(0.0, center_y - opening_width / 2)
        end = min(depth, center_y + opening_width / 2)
        if start > 0:
            cube(f"Room.{side}.before-opening", (x, start / 2, height / 2), (0.08, start / 2, height / 2), mats["wall"], root, 0)
        if end < depth:
            cube(f"Room.{side}.after-opening", (x, (end + depth) / 2, height / 2), (0.08, (depth - end) / 2, height / 2), mats["wall"], root, 0)
        if opening_height < height:
            cube(
                f"Room.{side}.above-opening",
                (x, center_y, opening_height + (height - opening_height) / 2),
                (0.08, opening_width / 2, (height - opening_height) / 2),
                mats["wall"],
                root,
                0,
            )

    side_wall("left", -width / 2)
    side_wall("right", width / 2)
    # Repeated wall/ceiling mouldings make orientation and scale readable from new angles.
    for z in (0.18, height * 0.72, height - 0.2):
        cube(f"Room.back.moulding.{z}", (0, depth - 0.1, z), (width / 2, 0.045, 0.045), mats["trim"], root, 0.01)
    for x in (-width * 0.32, 0, width * 0.32):
        cube(f"Room.back.panel.{x}", (x, depth - 0.14, height * 0.43), (width * 0.13, 0.025, height * 0.28), mats["panel"], root, 0.025)
    for offset in (0.35, 0.62):
        cube(f"Room.ceiling.trim.x.{offset}", (0, depth / 2, height - offset), (width / 2 - offset, depth / 2 - offset, 0.025), mats["trim"], root, 0.01)
    for item in env.get("objects", []):
        kind = item.get("kind")
        if kind == "window":
            create_arch_window(item, mats)
        elif kind == "curtain":
            create_curtain(item, mats)
        elif kind in {"bed", "dresser", "vanity", "chair"}:
            create_furniture(item, mats)
        elif kind in {"mirror", "portrait", "rug", "candle_cluster", "chandelier"}:
            create_decor(item, mats)
        elif kind == "circular_console":
            create_circular_console(item, mats)
        elif kind == "command_display":
            create_command_display(item, mats)
        elif kind == "console_bank":
            create_console_bank(item, mats)
        elif kind == "arch_door":
            create_arch_door(item, mats)
        elif kind == "corridor":
            create_corridor(item, mats)
        elif kind in {"ceiling_panel", "wall_sconce", "planter", "floor_inlay"}:
            create_architectural_detail(item, mats)


def _solid_material(name: str, color: tuple, roughness: float = 0.5):
    return material(name, color, roughness)


def _glass_material(name: str, color: tuple):
    value = material(name, color, 0.09)
    bsdf = value.node_tree.nodes.get("Principled BSDF")
    alpha = bsdf.inputs.get("Alpha")
    if alpha is not None:
        alpha.default_value = color[3]
    if hasattr(value, "blend_method"):
        value.blend_method = "BLEND"
    return value


def build_simple_chair_item(item: dict, parent) -> bpy.types.Object:
    """无扶手椅代理：座面 + 靠背 + 四条腿；仅在本场景确认参考适用时选择。"""
    width, depth, height = [float(value) for value in item.get("size", [0.5, 0.5, 0.9])]
    fabric = _solid_material(f"{item['id']}.fabric", (0.28, 0.29, 0.32, 1), 0.5)
    legs = _solid_material(f"{item['id']}.legs", (0.32, 0.24, 0.16, 1), 0.4)
    root = object_root(item["id"], item["position"], item.get("rotation_degrees", 0))
    root.parent = parent
    tag_reconstruction(root, item)
    root["anchor_label"] = str(item.get("anchor_label", item["id"]))
    cube(f"{item['id']}.seat", (0, 0, height * 0.48), (width / 2, depth / 2, height * 0.08), fabric, root, 0.05)
    # 靠背放 -Y：与 armchair 保持同一套朝向规则（人朝 +Y 坐下、背对椅背），
    # 否则同一份配方在两种椅子上会一个正一个反。
    cube(f"{item['id']}.back", (0, -depth * 0.42, height * 0.95), (width / 2, depth * 0.08, height * 0.45), fabric, root, 0.05)
    for x in (-width * 0.4, width * 0.4):
        for y in (-depth * 0.38, depth * 0.38):
            cylinder(f"{item['id']}.leg.{x}.{y}", (x, y, height * 0.22), 0.04, height * 0.44, legs, root)
    return root


def build_armchair_item(item: dict, parent) -> bpy.types.Object:
    w, d, h = [float(value) for value in item.get("size", [1.1, 1.0, 1.05])]
    x, y, z = [float(value) for value in item["position"]]
    root = object_root(item["id"], [x, y, z], item.get("rotation_degrees", 0))
    root.parent = parent
    tag_reconstruction(root, item)
    root["anchor_label"] = str(item.get("anchor_label", item["id"]))
    leather = _solid_material(f"{item['id']}.leather", rgba_from_hex(str(item.get("color", "#C9A87C"))), 0.55)
    wood = _solid_material(f"{item['id']}.wood", (0.42, 0.29, 0.17, 1), 0.5)
    cube(f"{item['id']}.seat", (0, 0, h * 0.32), (w / 2, d / 2, h * 0.16), leather, root, 0.05)
    back = cube(f"{item['id']}.back", (0, -d * 0.30, h * 0.62), (w / 2, 0.09, h * 0.36), leather, root, 0.06)
    back.rotation_euler[0] = math.radians(-12)
    for sx in (-1, 1):
        cube(f"{item['id']}.arm.{'L' if sx < 0 else 'R'}", (sx * w * 0.46, 0, h * 0.44), (0.07, d * 0.54, 0.15), leather, root, 0.04)
    for sx in (-1, 1):
        for sy in (-1, 1):
            cube(f"{item['id']}.leg.{sx}.{sy}", (sx * w * 0.42, sy * d * 0.40, h * 0.06), (0.03, 0.03, h * 0.12), wood, root, 0)
    return root


def build_bench_item(item: dict, parent) -> bpy.types.Object:
    w, d, h = [float(value) for value in item.get("size", [0.9, 0.62, 0.9])]
    x, y, z = [float(value) for value in item["position"]]
    root = object_root(item["id"], [x, y, z], item.get("rotation_degrees", 0))
    root.parent = parent
    tag_reconstruction(root, item)
    root["anchor_label"] = str(item.get("anchor_label", item["id"]))
    seat_mat = _solid_material(f"{item['id']}.seat", rgba_from_hex(str(item.get("color", "#1F5FA8"))), 0.5)
    frame = _solid_material(f"{item['id']}.frame", (0.54, 0.56, 0.59, 1), 0.22)
    cube(f"{item['id']}.seat", (0, 0, h * 0.42), (w / 2, d / 2, 0.06), seat_mat, root, 0.02)
    back = cube(f"{item['id']}.back", (0, -d * 0.44, h * 0.7), (w / 2, 0.06, h * 0.38), seat_mat, root, 0.02)
    back.rotation_euler[0] = math.radians(-8)
    for sx in (-1, 1):
        cube(f"{item['id']}.leg.{sx}", (sx * w * 0.42, 0, h * 0.2), (0.05, 0.05, h * 0.42), frame, root, 0)
    return root


def build_glass_balustrade_item(item: dict, parent) -> bpy.types.Object:
    px, py, pz = [float(value) for value in item["position"]]
    thickness, length, height = [float(value) for value in item.get("size", [0.5, 18, 1.15])]
    root = object_root(item["id"], [px, py, pz], item.get("rotation_degrees", 0))
    root.parent = parent
    tag_reconstruction(root, item)
    root["anchor_label"] = str(item.get("anchor_label", item["id"]))
    glass = _glass_material(f"{item['id']}.glass", (0.72, 0.82, 0.95, 0.5))
    metal = _solid_material(f"{item['id']}.metal", (0.78, 0.81, 0.84, 1), 0.18)
    panel_count = max(2, round(length / 1.6))
    for index in range(panel_count):
        seg = length / panel_count
        center_y = py - length / 2 + seg * (index + 0.5)
        cube(f"{item['id']}.glass.{index}", (px, center_y, pz + height * 0.46), (0.05, seg / 2, height * 0.52), glass, root, 0)
    for index in range(panel_count + 1):
        center_y = py - length / 2 + index * (length / panel_count)
        cylinder(f"{item['id']}.post.{index}", (px, center_y, pz + height / 2), 0.05, height, metal, root)
    cube(f"{item['id']}.handrail", (px, py, pz + height), (0.1, length / 2, 0.09), metal, root, 0)
    return root


def create_natural_proxy(env: dict):
    """Create anchor-first natural-environment proxy geometry.

    Positions and sizes are explicit recipe facts. The resulting geometry is a
    director's white model, not a claim of photogrammetric reconstruction.
    """
    mats = {
        "snow": material("NaturalProxy.snow", (0.78, 0.82, 0.88, 1), 0.9),
        "stone": material("NaturalProxy.stone", (0.28, 0.32, 0.38, 1), 0.82),
        "water": material("NaturalProxy.water", (0.20, 0.48, 0.65, 1), 0.24),
        "metal": material("NaturalProxy.metal", (0.42, 0.46, 0.52, 1), 0.22),
        "anchor": material("NaturalProxy.anchor", (0.92, 0.56, 0.12, 1), 0.62),
    }
    scene_root = object_root("NaturalProxy.scene", [0, 0, 0])
    scene_root["reference_image"] = str(env.get("source_image", ""))
    scene_root["proxy_contract"] = "anchor_first_not_photogrammetry"
    for item in env.get("objects", []):
        kind = item["kind"]
        if kind in {"chair", "armchair", "bench_seat", "glass_balustrade"}:
            (build_simple_chair_item if kind == "chair" else
             build_armchair_item if kind == "armchair" else
             build_bench_item if kind == "bench_seat" else
             build_glass_balustrade_item)(item, scene_root)
            continue
        width, depth, height = [float(value) for value in item.get("size", [1, 1, 1])]
        role = str(item.get("material_role", "stone"))
        explicit_color = item.get("color")
        emissive = item.get("emissive")
        if emissive:
            mat = emissive_material(f"{item['id']}.emissive", rgba_from_hex(str(emissive)), float(item.get("emissive_strength", 3.0)))
        elif explicit_color:
            mat = _solid_material(f"{item['id']}.color", rgba_from_hex(str(explicit_color)), 0.5)
        else:
            mat = mats.get(role, mats["stone"])
        root = object_root(item["id"], item["position"], item.get("rotation_degrees", 0))
        root.parent = scene_root
        tag_reconstruction(root, item)
        root["anchor_label"] = str(item.get("anchor_label", item["id"]))
        if kind == "proxy_box":
            cube(f"{item['id']}.mesh", (0, 0, 0), (width / 2, depth / 2, height / 2), mat, root, min(width, depth, height) * 0.08)
        elif kind == "proxy_cylinder":
            cylinder(f"{item['id']}.mesh", (0, 0, 0), max(width, depth) / 2, height, mat, root)
        elif kind == "proxy_cone":
            obj = cone(f"{item['id']}.mesh", (0, 0, 0), max(width, depth) / 2, float(item.get("top_radius", 0)), height, mat, root)
            obj.scale.y = depth / max(width, 1e-6)
        elif kind == "proxy_sphere":
            obj = sphere(f"{item['id']}.mesh", (0, 0, 0), 0.5, mat, root)
            obj.scale = (width, depth, height)
        elif kind == "proxy_rock":
            bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.5, location=(0, 0, 0))
            obj = bpy.context.object
            obj.name = f"{item['id']}.mesh"
            obj.parent = root
            obj.scale = (width, depth, height)
            obj.rotation_euler = (
                math.radians(float(item.get("tilt_x_degrees", 0))),
                math.radians(float(item.get("tilt_y_degrees", 0))),
                math.radians(float(item.get("spin_degrees", 0))),
            )
            apply_material(obj, mat)
        else:
            raise RuntimeError(
                f"natural_proxy 不支持对象类型：{kind}；"
                f"支持：{'、'.join(sorted(NATURAL_PROXY_KINDS))}"
            )
    return scene_root


def textured_material(image_path: str):
    mat = bpy.data.materials.new("Reference image material")
    mat.use_nodes = True
    tree = mat.node_tree
    bsdf = tree.nodes.get("Principled BSDF")
    image_node = tree.nodes.new("ShaderNodeTexImage")
    image_node.image = bpy.data.images.load(image_path, check_existing=True)
    tree.links.new(image_node.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.82
    return mat


def create_image_relief(env: dict):
    image_path = str(Path(env["reference_image"]).resolve())
    depth_path = str(Path(env["depth_map"]).resolve())
    image = bpy.data.images.load(image_path, check_existing=True)
    depth = bpy.data.images.load(depth_path, check_existing=True)
    image_w, image_h = image.size
    depth_w, depth_h = depth.size
    samples_x = 96
    samples_z = max(48, round(samples_x * image_h / max(image_w, 1)))
    aspect = image_w / max(image_h, 1)
    world_h = 5.2
    world_w = world_h * aspect
    relief = float(env.get("relief_depth", 2.0))
    pixels = list(depth.pixels[:])
    vertices = []
    uvs = []
    for zi in range(samples_z):
        v = zi / (samples_z - 1)
        py = min(depth_h - 1, round(v * (depth_h - 1)))
        for xi in range(samples_x):
            u = xi / (samples_x - 1)
            px = min(depth_w - 1, round(u * (depth_w - 1)))
            value = pixels[(py * depth_w + px) * 4]
            x = (u - 0.5) * world_w
            z = v * world_h
            y = 3.5 - float(value) * relief
            vertices.append((x, y, z))
            uvs.append((u, v))
    faces = []
    for zi in range(samples_z - 1):
        for xi in range(samples_x - 1):
            a = zi * samples_x + xi
            faces.append((a, a + 1, a + samples_x + 1, a + samples_x))
    mesh = bpy.data.meshes.new("ImageReliefMesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("ImageRelief", mesh)
    bpy.context.collection.objects.link(obj)
    apply_material(obj, textured_material(image_path))
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            uv_layer.data[loop_index].uv = uvs[mesh.loops[loop_index].vertex_index]
    floor = material("Relief.floor", (0.16, 0.16, 0.18, 1))
    cube("Relief.floor", (0, 1.0, -0.08), (max(4, world_w / 2), 5, 0.08), floor, bevel=0)


def create_lighting(preset: str, asset_path: str | None = None):
    world = bpy.context.scene.world
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (0.16, 0.17, 0.2, 1)
    background.inputs["Strength"].default_value = 0.7
    if asset_path:
        env = world.node_tree.nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(str(asset_path), check_existing=True)
        world.node_tree.links.new(env.outputs["Color"], background.inputs["Color"])
        background.inputs["Strength"].default_value = 0.32
    bpy.ops.object.light_add(type="AREA", location=(-3.5, -2.5, 5.8))
    key = bpy.context.object
    key.name = "Light.key"
    key.data.energy = 950
    key.data.shape = "DISK"
    key.data.size = 5
    key.rotation_euler = (math.radians(22), 0, math.radians(-25))
    bpy.ops.object.light_add(type="AREA", location=(3.5, 1.0, 3.8))
    fill = bpy.context.object
    fill.name = "Light.fill"
    fill.data.energy = 650
    fill.data.size = 4
    fill.rotation_euler = (math.radians(65), 0, math.radians(150))
    bpy.ops.object.light_add(type="SUN", location=(-2, -2, 5))
    sun = bpy.context.object
    sun.name = "Light.sun"
    sun.data.energy = 1.4
    sun.rotation_euler = (math.radians(28), math.radians(-18), math.radians(-35))
    if preset == "royal_command_center":
        background.inputs["Color"].default_value = (0.22, 0.15, 0.16, 1)
        background.inputs["Strength"].default_value = 1.25
        bpy.context.scene.view_settings.look = "AgX - Medium Low Contrast"
        bpy.ops.object.light_add(type="AREA", location=(0, 5.4, 5.2))
        ceiling = bpy.context.object
        ceiling.name = "Light.ceiling"
        ceiling.data.energy = 3200
        ceiling.data.shape = "RECTANGLE"
        ceiling.data.size = 8.0
        ceiling.data.size_y = 4.5
        ceiling.data.color = (1.0, 0.78, 0.72)
        for name, location, energy, size in (
            ("Light.front.fill", (0, 0.9, 3.6), 1800, 6.0),
            ("Light.left.wash", (-5.4, 5.2, 3.4), 1050, 4.0),
            ("Light.right.wash", (5.4, 5.2, 3.4), 1050, 4.0),
        ):
            bpy.ops.object.light_add(type="AREA", location=location)
            light = bpy.context.object
            light.name = name
            light.data.energy = energy
            light.data.size = size
            light.data.color = (1.0, 0.72, 0.7)
            direction = Vector((0, 6.4, 1.4)) - light.location
            light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def create_camera_object(config: dict, fps: int, name: str, time_offset: float = 0.0, *, reuse=None):
    reuse = reuse or {}
    # shoot may reopen a staged scene containing this shot's camera. Reset only
    # the objects it has identified as owned; keep their stable object names.
    for obj in reuse.values():
        if obj is not None:
            obj.animation_data_clear()
            obj.constraints.clear()
            obj.parent = None
            obj.matrix_parent_inverse.identity()
            obj.location = (0, 0, 0)
            obj.rotation_euler = (0, 0, 0)
            obj.scale = (1, 1, 1)
    camera = reuse.get('camera')
    if camera is None:
        camera_data = bpy.data.cameras.new(name)
        camera = bpy.data.objects.new(name, camera_data)
        bpy.context.collection.objects.link(camera)
    else:
        if camera.data.users > 1:
            camera.data = camera.data.copy()
        camera.data.animation_data_clear()
    position_space = config.get("position_space", "world")
    if position_space == "tracking_actor_relative":
        actor_id = config.get("tracking_actor")
        actor = bpy.data.objects.get(str(actor_id)) if actor_id else None
        if actor is None:
            raise RuntimeError(f"找不到摄影机跟踪人物：{actor_id}")
        rig = reuse.get('rig')
        if rig is None:
            rig = bpy.data.objects.new(f"{name}.RelativeRig", None)
            bpy.context.collection.objects.link(rig)
        follow = rig.constraints.new(type="COPY_LOCATION")
        follow.name = f"{name}.FollowActor"
        follow.target = actor
        camera.parent = rig
    else:
        rig = None
    target = reuse.get('target')
    if target is None:
        target = bpy.data.objects.new(f"{name}.Target", None)
        bpy.context.collection.objects.link(target)
    if rig is not None:
        target.parent = rig
    tracking = camera.constraints.new(type="TRACK_TO")
    tracking.name = f"{name}.LookAt"
    tracking.target = target
    tracking.track_axis = "TRACK_NEGATIVE_Z"
    tracking.up_axis = "UP_Y"
    previous = None

    def apply_entry(entry, frame):
        camera.location = Vector(entry["position"])
        target.location = Vector(entry["look_at"])
        camera.data.lens = float(entry.get("lens_mm", config.get("lens_mm", 35)))
        camera.keyframe_insert("location", frame=frame)
        target.keyframe_insert("location", frame=frame)
        camera.data.keyframe_insert("lens", frame=frame)

    for entry in config["keyframes"]:
        frame = max(1, round((time_offset + float(entry["time"])) * fps) + 1)
        if entry.get("cut") and previous is not None and frame > 1:
            apply_entry(previous, frame - 1)
        apply_entry(entry, frame)
        previous = entry
    if camera.animation_data and camera.animation_data.action:
        for curve in _action_fcurves(camera.animation_data.action):
            for point in curve.keyframe_points:
                point.interpolation = "BEZIER"
    camera.data.sensor_width = 36
    camera["position_space"] = position_space
    if position_space == "tracking_actor_relative":
        camera["tracking_actor"] = str(config["tracking_actor"])
    elif "tracking_actor" in camera:
        del camera["tracking_actor"]
    return camera


def create_camera(config: dict, fps: int):
    shots = config.get("shots", [])
    if not shots:
        camera = create_camera_object(config, fps, "DirectorCamera")
        bpy.context.scene.camera = camera
        return camera

    cameras = []
    for shot in shots:
        shot_id = str(shot["id"])
        camera_name = f"DirectorCamera.{shot_id}"
        shot_config = {
            "lens_mm": shot.get("lens_mm", config.get("lens_mm", 35)),
            "position_space": shot.get("position_space", "world"),
            "tracking_actor": shot.get("tracking_actor"),
            "keyframes": shot["keyframes"],
        }
        camera = create_camera_object(shot_config, fps, camera_name, float(shot["start"]))
        camera["shot_id"] = shot_id
        camera["shot_start"] = float(shot["start"])
        camera["shot_end"] = float(shot["end"])
        if shot.get("label"):
            camera["shot_label"] = str(shot["label"])
        if shot.get("shot_size"):
            camera["shot_size"] = str(shot["shot_size"])
        marker_frame = max(1, round(float(shot["start"]) * fps) + 1)
        marker = bpy.context.scene.timeline_markers.new(f"CUT.{shot_id}", frame=marker_frame)
        marker.camera = camera
        cameras.append(camera)
    bpy.context.scene.camera = cameras[0]
    return cameras[0]


def _preview_video_frame_range(recipe: dict, scene, outputs: dict):
    """只渲指定镜时用：按 outputs.preview_video_shots / preview_video_range 限定帧范围。

    统一按输出区间计算包含式帧边界；缺省覆盖整段，末帧不重复结束时刻。
    """
    return build_video_frame_range({**recipe, "outputs": outputs}, max(int(scene.render.fps), 1))


def setup_scene(recipe: dict):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        pass
    project = recipe["project"]
    scene = bpy.context.scene
    scene.name = project["name"]
    scene.render.engine = _eevee_engine_name()
    scene.render.resolution_x = int(project["resolution"][0])
    scene.render.resolution_y = int(project["resolution"][1])
    scene.render.resolution_percentage = 100
    scene.render.fps = int(project["fps"])
    scene.render.fps_base = 1.0
    scene.frame_start = 1
    scene.frame_end = round(float(project["duration_seconds"]) * scene.render.fps) + 1
    _use_video_output(scene)
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
    scene.render.ffmpeg.ffmpeg_preset = "GOOD"
    scene.render.film_transparent = False
    scene.view_settings.look = "AgX - Medium High Contrast"
    env = recipe["environment"]
    if env["preset"] == "image_relief":
        create_image_relief(env)
    elif env["preset"] == "structured_scene":
        create_structured_scene(env)
    elif env["preset"] == "natural_proxy":
        create_natural_proxy(env)
    else:
        create_room(env, env["preset"])
    resolve_actor_identity_colors(recipe)
    for actor in recipe.get("actors", []):
        create_actor(actor, scene.render.fps)
    lighting = recipe.get("lighting", {})
    create_lighting(lighting.get("preset", "soft_daylight"), lighting.get("asset_path"))
    create_camera(recipe["camera"], scene.render.fps)
    return scene


REFERENCE_DIRECTION_VECTORS = {
    "front": Vector((0.0, -1.0, 0.0)),
    "front_left": Vector((-1.0, -1.0, 0.0)).normalized(),
    "left": Vector((-1.0, 0.0, 0.0)),
    "back_left": Vector((-1.0, 1.0, 0.0)).normalized(),
    "back": Vector((0.0, 1.0, 0.0)),
    "back_right": Vector((1.0, 1.0, 0.0)).normalized(),
    "right": Vector((1.0, 0.0, 0.0)),
    "front_right": Vector((1.0, -1.0, 0.0)).normalized(),
}

REFERENCE_SHOT_DISTANCE = {
    "extreme_wide": 13.0,
    "wide": 9.0,
    "full": 6.0,
    "medium": 4.2,
    "medium_close": 3.2,
    "close": 2.3,
    "extreme_close": 1.5,
    "insert": 1.8,
}

REFERENCE_TARGET_HEIGHT = {
    "extreme_wide": 1.0,
    "wide": 1.0,
    "full": 1.0,
    "medium": 1.25,
    "medium_close": 1.35,
    "close": 1.5,
    "extreme_close": 1.55,
    "insert": 1.25,
}


def timeline_camera_at_frame(scene, frame: int):
    markers = [marker for marker in scene.timeline_markers if marker.camera and marker.frame <= frame]
    if not markers:
        return scene.camera
    return max(markers, key=lambda marker: marker.frame).camera


def reference_occluders(view: dict, environment: dict, camera) -> list:
    if view.get("occlusion_policy", "preserve_environment") != "film_set_hide_occluders":
        return []
    preset = environment.get("preset")
    if preset == "structured_scene":
        width, depth, height = [float(value) for value in environment.get("room", {}).get("size", [8, 10, 4.8])]
        min_y, max_y = 0.0, depth
    elif preset in {"studio", "closed_room", "bedroom"}:
        width = float(environment.get("width", 8))
        depth = float(environment.get("depth", 10))
        height = float(environment.get("height", 4.5))
        min_y, max_y = -2.0, depth - 2.0
    else:
        return []
    prefixes = []
    if camera.location.x < -width / 2:
        prefixes.append("Room.left")
    if camera.location.x > width / 2:
        prefixes.append("Room.right")
    if camera.location.y < min_y:
        prefixes.append("Room.front")
    if camera.location.y > max_y:
        prefixes.append("Room.back")
    if camera.location.z > height:
        prefixes.append("Room.ceiling")
    return [obj for obj in bpy.data.objects if any(obj.name.startswith(prefix) for prefix in prefixes)]


def render_frame_with_camera(scene, camera, frame: int, path: Path, hidden_objects=None) -> None:
    """Render one still with an explicit camera even when timeline markers exist."""
    scene.frame_set(frame)
    active_markers = [marker for marker in scene.timeline_markers if marker.camera and marker.frame <= frame]
    active_marker = max(active_markers, key=lambda marker: marker.frame) if active_markers else None
    original_scene_camera = scene.camera
    original_marker_camera = active_marker.camera if active_marker is not None else None
    hidden_objects = list(hidden_objects or [])
    original_hidden = {obj.name: obj.hide_render for obj in hidden_objects}
    try:
        if active_marker is not None:
            active_marker.camera = camera
        for obj in hidden_objects:
            obj.hide_render = True
        scene.camera = camera
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
    finally:
        if active_marker is not None:
            active_marker.camera = original_marker_camera
        for obj in hidden_objects:
            obj.hide_render = original_hidden[obj.name]
        scene.camera = original_scene_camera


def create_coverage_reference_camera(view: dict, scene):
    subject_ids = [str(item) for item in view["subject_ids"]]
    subjects = [bpy.data.objects.get(subject_id) for subject_id in subject_ids]
    if any(subject is None for subject in subjects):
        missing = [subject_id for subject_id, subject in zip(subject_ids, subjects) if subject is None]
        raise RuntimeError(f"参考视图找不到人物：{', '.join(missing)}")
    locations = [subject.matrix_world.translation.copy() for subject in subjects]
    center = sum(locations, Vector((0.0, 0.0, 0.0))) / len(locations)
    shot_size = str(view["shot_size"])
    target_location = center + Vector((0.0, 0.0, REFERENCE_TARGET_HEIGHT[shot_size]))
    spread = max(((location - center).to_2d().length for location in locations), default=0.0)
    distance = REFERENCE_SHOT_DISTANCE[shot_size] + spread * 1.4
    direction = REFERENCE_DIRECTION_VECTORS[str(view["view_direction"])].copy()
    if view.get("direction_space", "world") == "subject":
        orientation_id = str(view.get("orientation_actor") or subject_ids[0])
        orientation_actor = bpy.data.objects.get(orientation_id)
        if orientation_actor is None:
            raise RuntimeError(f"参考视图找不到朝向人物：{orientation_id}")
        direction = orientation_actor.matrix_world.to_quaternion() @ direction
        direction.z = 0.0
        direction.normalize()
    elevation = str(view.get("elevation", "eye"))
    if elevation == "low":
        vertical = -0.45
        horizontal_scale = 1.0
    elif elevation == "high":
        vertical = 2.4
        horizontal_scale = 0.9
    elif elevation == "overhead":
        vertical = distance * 0.95
        horizontal_scale = 0.22
    else:
        vertical = 0.35
        horizontal_scale = 1.0
    camera_location = target_location + direction * distance * horizontal_scale + Vector((0.0, 0.0, vertical))
    name = f"ReferenceCamera.{view['id']}"
    camera_data = bpy.data.cameras.new(name)
    camera = bpy.data.objects.new(name, camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = camera_location
    camera.data.lens = float(view.get("lens_mm", 50.0))
    camera.data.sensor_width = 36
    target = bpy.data.objects.new(f"{name}.Target", None)
    bpy.context.collection.objects.link(target)
    target.location = target_location
    tracking = camera.constraints.new(type="TRACK_TO")
    tracking.name = f"{name}.LookAt"
    tracking.target = target
    tracking.track_axis = "TRACK_NEGATIVE_Z"
    tracking.up_axis = "UP_Y"
    camera["reference_view_id"] = str(view["id"])
    camera["shot_size"] = shot_size
    camera["view_direction"] = str(view["view_direction"])
    camera["direction_space"] = str(view.get("direction_space", "world"))
    camera["elevation"] = elevation
    camera["subject_ids"] = json.dumps(subject_ids, ensure_ascii=False)
    return camera


def env_bounds(recipe: dict) -> tuple:
    env = recipe.get("environment", {})
    preset = env.get("preset")
    if preset == "structured_scene":
        size = env.get("room", {}).get("size", [8, 10, 4.8])
        width, depth = float(size[0]), float(size[1])
        xs, ys = [-width / 2, width / 2], [0.0, depth]
    elif preset in {"studio", "closed_room", "bedroom"}:
        width = float(env.get("width", 8))
        depth = float(env.get("depth", 10))
        xs, ys = [-width / 2, width / 2], [-2.0, depth - 2.0]
    else:
        # natural_proxy / image_relief：用物体(锚点)位置+半尺寸框出地图范围，避免缩成默认小框。
        xs, ys = [], []
        for actor in recipe.get("actors", []):
            p = actor.get("position", [0, 0, 0])
            xs.append(float(p[0]))
            ys.append(float(p[1]))
        for item in env.get("objects", []):
            p = item.get("position", [0, 0, 0])
            s = item.get("size", [1, 1, 1])
            xs.append(float(p[0]) + float(s[0]) / 2)
            xs.append(float(p[0]) - float(s[0]) / 2)
            ys.append(float(p[1]) + float(s[1]) / 2)
            ys.append(float(p[1]) - float(s[1]) / 2)
        if not xs:
            xs, ys = [-4.0, 4.0], [-6.0, 6.0]
        if len(xs) == 1:
            xs = [xs[0] - 2.0, xs[0] + 2.0]
        if len(ys) == 1:
            ys = [ys[0] - 2.0, ys[0] + 2.0]
    return (min(xs) - 0.6, max(xs) + 0.6, min(ys) - 0.6, max(ys) + 0.6)


def create_topdown_camera(recipe: dict, scene) -> bpy.types.Object:
    min_x, max_x, min_y, max_y = env_bounds(recipe)
    center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    span_x, span_y = max_x - min_x, max_y - min_y
    res_x = int(recipe["project"]["resolution"][0])
    res_y = int(recipe["project"]["resolution"][1])
    camera_data = bpy.data.cameras.new("TopdownCamera")
    camera_data.type = "ORTHO"
    # ortho_scale = 画面横向覆盖的世界宽度；竖向覆盖 = ortho_scale * res_y / res_x。
    # 要让两个方向都装下，取两者所需的最大值。
    camera_data.ortho_scale = max(span_x, span_y * res_x / max(res_y, 1))
    camera = bpy.data.objects.new("TopdownCamera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = Vector((center_x, center_y, max(span_x, span_y) + 8.0))
    # 直上直下：屏幕上=世界+Y(纵深)、屏幕右=世界+X(宽度)，得到确定的俯视平面图。
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera["view_role"] = "topdown_path"
    return camera


def draw_actor_path_maps(recipe: dict) -> list[dict]:
    min_x, max_x, min_y, max_y = env_bounds(recipe)
    center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    cube(
        "PathMap.backdrop",
        (center_x, center_y, 0.015),
        ((max_x - min_x) / 2, (max_y - min_y) / 2, 0.015),
        material("PathMap.base", (0.055, 0.062, 0.072, 1), 0.9),
        bevel=0,
    )
    records = []
    for actor in recipe.get("actors", []):
        waypoints = [(float(actor["position"][0]), float(actor["position"][1]))]
        for action in ordered_actions(actor):
            if action.get("preset") in {"walk", "run"} and action.get("to"):
                waypoints.append((float(action["to"][0]), float(action["to"][1])))
        if len(waypoints) < 2:
            continue
        color = rgba_from_hex(str(actor.get("_resolved_display_color")) or "#FFFFFF")
        curve = bpy.data.curves.new(f"{actor['id']}.path", "CURVE")
        curve.dimensions = "3D"
        curve.bevel_depth = 0.05
        curve.bevel_resolution = 1
        spline = curve.splines.new("POLY")
        spline.points.add(len(waypoints) - 1)
        for index, (x, y) in enumerate(waypoints):
            spline.points[index].co = (x, y, 0.04, 1.0)
        path_object = bpy.data.objects.new(f"{actor['id']}.path", curve)
        bpy.context.collection.objects.link(path_object)
        path_object.data.materials.append(emissive_material(f"{actor['id']}.path", color, 2.2))
        records.append({"actor_id": actor["id"], "waypoints": waypoints, "path_object": path_object.name})
    return records


def render_outputs(recipe: dict, output_dir: Path, scene) -> dict:
    build_output_plan(recipe)
    if recipe.get("outputs", {}).get("preview_video", True):
        build_video_frame_range(recipe, scene.render.fps)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "previs"
    blend_path = output_dir / f"{safe_name}.blend"
    video_path = output_dir / f"{safe_name}.mp4"
    still_dir = output_dir / "stills"
    still_dir.mkdir(exist_ok=True)
    reference_dir = output_dir / "reference-frames"
    outputs = recipe["outputs"]
    original_camera = scene.camera
    reference_cameras = {}
    for view in outputs.get("reference_views", []):
        if view["mode"] != "coverage_camera":
            continue
        frame = min(scene.frame_end, max(scene.frame_start, round(float(view["time"]) * scene.render.fps) + 1))
        scene.frame_set(frame)
        reference_cameras[str(view["id"])] = create_coverage_reference_camera(view, scene)
    scene.frame_set(scene.frame_start)
    scene.camera = original_camera
    if outputs.get("blend", True):
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    stills = []
    _use_still_output(scene, "PNG")
    for time_value in outputs.get("still_frames", []):
        frame = min(scene.frame_end, max(scene.frame_start, round(float(time_value) * scene.render.fps) + 1))
        path = still_dir / f"frame-{float(time_value):05.2f}.png"
        scene.frame_set(frame)
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        stills.append(str(path.resolve()))
    reference_records = []
    if outputs.get("reference_views"):
        reference_dir.mkdir(exist_ok=True)
    for view in outputs.get("reference_views", []):
        frame = min(scene.frame_end, max(scene.frame_start, round(float(view["time"]) * scene.render.fps) + 1))
        scene.frame_set(frame)
        if view["mode"] == "coverage_camera":
            camera = reference_cameras[str(view["id"])]
            hidden_objects = reference_occluders(view, recipe["environment"], camera)
        else:
            camera = timeline_camera_at_frame(scene, frame)
            hidden_objects = []
        if camera is None:
            raise RuntimeError(f"参考视图 {view['id']} 找不到可用摄影机")
        path = reference_dir / f"{view['id']}.png"
        render_frame_with_camera(scene, camera, frame, path, hidden_objects)
        reference_records.append(
            {
                "reference_id": str(view["id"]),
                "path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "time": float(view["time"]),
                "frame": frame,
                "mode": str(view["mode"]),
                "purpose": str(view["purpose"]),
                "shot_id": view.get("shot_id"),
                "subject_ids": list(view.get("subject_ids", [])),
                "shot_size": view.get("shot_size"),
                "view_direction": view.get("view_direction"),
                "direction_space": view.get("direction_space"),
                "elevation": view.get("elevation"),
                "occlusion_policy": view.get("occlusion_policy", "preserve_environment"),
                "temporarily_hidden_occluders": [obj.name for obj in hidden_objects],
                "camera_object": camera.name,
            }
        )
    scene.frame_set(scene.frame_start)
    scene.camera = original_camera
    if outputs.get("preview_video", True):
        _use_video_output(scene)
        scene.render.ffmpeg.format = "MPEG4"
        scene.render.ffmpeg.codec = "H264"
        scene.render.filepath = str(video_path)
        # 混合段只导所选时序参考镜头：视频时长继承导演范围，不自动扩大渲染。
        saved_range = (scene.frame_start, scene.frame_end)
        video_range = _preview_video_frame_range(recipe, scene, outputs)
        if video_range:
            scene.frame_start, scene.frame_end = video_range
        scene.frame_set(scene.frame_start)
        bpy.ops.render.render(animation=True)
        scene.frame_start, scene.frame_end = saved_range
    topdown_record = None
    if outputs.get("topdown"):
        topdown_config = outputs["topdown"]
        if topdown_config.get("enabled", True):
            topdown_dir = output_dir / "topdown"
            topdown_dir.mkdir(exist_ok=True)
            topdown_camera = create_topdown_camera(recipe, scene)
            path_records = draw_actor_path_maps(recipe) if topdown_config.get("show_paths", False) else []
            previous_camera = scene.camera
            scene.camera = topdown_camera
            _use_still_output(scene, "PNG")
            # 俯视地图是"平面图"：天花板、屋顶、吊灯都会从上方挡住人物，渲染前临时隐藏，渲完恢复。
            topdown_occluders = [
                obj for obj in scene.objects
                if obj.type == "MESH"
                and any(key in obj.name.lower() for key in ("ceiling", "roof", "chandelier", "lamp", "sconce"))
            ]
            saved_occluder_state = {obj.name: obj.hide_render for obj in topdown_occluders}
            for obj in topdown_occluders:
                obj.hide_render = True
            topdown_stills = []
            # 俯视静帧与视频都必须清掉时间轴上的切镜相机：marker 会在该时刻把 scene.camera
            # 抢成导演机位，渲出来的"俯视图"就变成透视画面了。
            markers = list(bpy.context.scene.timeline_markers)
            saved_marker_cameras = {marker.name: marker.camera for marker in markers}
            for marker in markers:
                marker.camera = None
            for time_value in topdown_config.get("still_times", []):
                frame = min(scene.frame_end, max(scene.frame_start, round(float(time_value) * scene.render.fps) + 1))
                path = topdown_dir / f"topdown-{float(time_value):05.2f}.png"
                scene.frame_set(frame)
                scene.camera = topdown_camera
                scene.render.filepath = str(path)
                bpy.ops.render.render(write_still=True)
                topdown_stills.append({"time": float(time_value), "path": str(path.resolve()), "bytes": path.stat().st_size})
            video_record = None
            if topdown_config.get("video", False):
                topdown_video = topdown_dir / "topdown.mp4"
                _use_video_output(scene)
                scene.render.ffmpeg.format = "MPEG4"
                scene.render.ffmpeg.codec = "H264"
                scene.render.filepath = str(topdown_video)
                scene.camera = topdown_camera
                saved_range = (scene.frame_start, scene.frame_end)
                scene.frame_start, scene.frame_end = build_video_frame_range(recipe, scene.render.fps, topdown=True)
                bpy.ops.render.render(animation=True)
                scene.frame_start, scene.frame_end = saved_range
                if topdown_video.exists():
                    video_record = {"path": str(topdown_video.resolve()), "bytes": topdown_video.stat().st_size}
            for marker in markers:
                marker.camera = saved_marker_cameras[marker.name]
            for obj in topdown_occluders:
                obj.hide_render = saved_occluder_state[obj.name]
            scene.camera = previous_camera
            topdown_record = {"paths_by_actor": path_records, "still_frames": topdown_stills, "video": video_record}
    artifacts = []
    for kind, path in (("blend", blend_path), ("preview_video", video_path)):
        if path.exists():
            artifacts.append({"type": kind, "path": str(path.resolve()), "bytes": path.stat().st_size})
    for path in map(Path, stills):
        artifacts.append({"type": "still", "path": str(path.resolve()), "bytes": path.stat().st_size})
    for record in reference_records:
        artifacts.append({"type": "reference_frame", "path": record["path"], "bytes": record["bytes"], "reference_id": record["reference_id"]})
    if topdown_record:
        for still in topdown_record["still_frames"]:
            artifacts.append({"type": "topdown_still", "path": still["path"], "bytes": still["bytes"], "time": still["time"]})
        if topdown_record["video"]:
            artifacts.append({"type": "topdown_video", "path": topdown_record["video"]["path"], "bytes": topdown_record["video"]["bytes"]})
        for path_record in topdown_record["paths_by_actor"]:
            artifacts.append({"type": "topdown_path", "actor_id": path_record["actor_id"], "object": path_record["path_object"]})
    for artifact in artifacts:
        if artifact.get("path"):
            artifact["sha256"] = file_sha256(Path(artifact["path"]))
    return {
        "schema_version": "1.0",
        "ok": True,
        "project": recipe["project"]["name"],
        "render_settings": render_settings(scene),
        "artifacts": artifacts,
        "reference_views": reference_records,
        "topdown": topdown_record,
    }


def main():
    args = arguments()
    recipe_path = Path(args.recipe).resolve()
    output_dir = Path(args.output_dir).resolve()
    prepared = [Path(path).resolve() for path in args.prepared_file]
    if recipe_path == output_dir / "resolved.recipe.json":
        prepared.append(recipe_path)
    require_empty_output(output_dir, prepared)
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    build_output_plan(recipe)
    recipe_sha256 = json_fingerprint(recipe)
    scene = setup_scene(recipe)
    manifest = render_outputs(recipe, output_dir, scene)
    manifest["recipe"] = str(recipe_path)
    manifest["recipe_sha256"] = recipe_sha256
    manifest["anchor_ids"] = [item["id"] for item in recipe.get("environment", {}).get("objects", [])]
    manifest["actor_ids"] = [item["id"] for item in recipe.get("actors", [])]
    manifest["actor_identity_colors"] = [
        {
            "actor_id": actor["id"],
            "display_color": actor.get("_resolved_display_color"),
            "source": actor.get("_display_color_source", "source_material"),
        }
        for actor in recipe.get("actors", [])
    ]
    camera_config = recipe.get("camera", {})
    camera_shots = camera_config.get("shots", [])
    if camera_shots:
        shot_timeline = []
        for index, shot in enumerate(camera_shots):
            start_frame = max(1, round(float(shot["start"]) * scene.render.fps) + 1)
            if index + 1 < len(camera_shots):
                end_frame = max(start_frame, round(float(camera_shots[index + 1]["start"]) * scene.render.fps))
            else:
                end_frame = scene.frame_end
            shot_timeline.append(
                {
                    "shot_id": shot["id"],
                    "label": shot.get("label"),
                    "purpose": shot.get("purpose"),
                    "shot_size": shot.get("shot_size"),
                    "start": float(shot["start"]),
                    "end": float(shot["end"]),
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "position_space": shot.get("position_space", "world"),
                    "tracking_actor": shot.get("tracking_actor"),
                    "keyframe_count": len(shot.get("keyframes", [])),
                    "camera_object": f"DirectorCamera.{shot['id']}",
                }
            )
        manifest["camera_control"] = {
            "mode": "shot_timeline",
            "shot_count": len(shot_timeline),
            "cut_count": max(0, len(shot_timeline) - 1),
        }
        manifest["shot_timeline"] = shot_timeline
    else:
        manifest["camera_control"] = {
            "mode": "legacy_keyframes",
            "position_space": camera_config.get("position_space", "world"),
            "tracking_actor": camera_config.get("tracking_actor"),
            "keyframe_count": len(camera_config.get("keyframes", [])),
        }
    manifest["gait_controls"] = [
        {
            "actor_id": actor["id"],
            "path_distance": bpy.data.objects[actor["id"]].get("gait_path_distance"),
            "step_count": bpy.data.objects[actor["id"]].get("gait_step_count"),
            "step_length": bpy.data.objects[actor["id"]].get("gait_step_length"),
            "cadence_spm": bpy.data.objects[actor["id"]].get("gait_cadence_spm"),
            "motion_source": bpy.data.objects[actor["id"]].get("gait_motion_source", "procedural"),
            "motion_clip": bpy.data.objects[actor["id"]].get("gait_motion_clip"),
            "cycle_count": bpy.data.objects[actor["id"]].get("gait_cycle_count"),
        }
        for actor in recipe.get("actors", [])
        if bpy.data.objects.get(actor["id"]) and "gait_step_count" in bpy.data.objects[actor["id"]]
    ]
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BLENDER_PREVIS_COMPLETE", json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
