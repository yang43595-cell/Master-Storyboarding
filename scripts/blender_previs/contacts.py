"""接触与测量工具箱：白模里所有"人对地面/椅子"的关系都从这里取。

历史上这些逻辑是遇到一次问题就地加一段（对齐脚、避让扶手、贴座面、净坐宽…），
常量散落各处。现在集中在这里，只做**纯几何与测量**（姿态与关键帧仍在 runner）；
所有容差都用命名常量，不再就地写数字。
"""

from __future__ import annotations

import math
from typing import Iterable

import bpy
from mathutils import Matrix, Vector

# —— 统一容差与默认值（改动只在这里改） ——
CONTACT_CLEARANCE = 0.005      # 身体/脚与接触面之间保留的缝隙（米）
DEFAULT_SEAT_HEIGHT = 0.45     # 没有椅子可用时的椅面高度（米）
HIP_SAMPLE_RADIUS = 0.26       # 取"身体最低点"时在骨盆周围采样的水平半径
BODY_PADDING = 0.09            # 大腿等部位的肉身半径（体积判定外扩量）
SEAT_GAP_TOLERANCE = 0.02      # Maximum supported butt/thigh clearance
GROUND_GAP_TOLERANCE = 0.04    # Standing support sampling tolerance
OVERLAP_EPSILON = 0.012        # 仅仅贴边不算相交
SEAT_SETBACK = 0.06            # 髋部落座时相对座面中心往前挪的量
SEAT_EDGE_MARGIN = 0.04        # 自动前挪时髋部与座面前缘保留的余量
HIP_WIDTH = 0.36               # 人体髋宽，用于净坐宽告警
LEGACY_HUMAN_HEIGHT = 2.15     # 旧配方 scale 的相对基准（仅兼容用）


def object_box(obj, basis=None) -> tuple[Vector, Vector]:
    matrix = obj.matrix_world if basis is None else basis @ obj.matrix_world
    points = [matrix @ Vector(corner) for corner in obj.bound_box]
    return (
        Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points))),
        Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points))),
    )


def boxes_intersect(first, second, epsilon: float = OVERLAP_EPSILON) -> bool:
    """带容差的体积相交：仅仅贴到边界不算相交。"""
    if first is None or second is None:
        return False
    (amin, amax), (bmin, bmax) = first, second
    return all(amin[i] + epsilon < bmax[i] and bmin[i] + epsilon < amax[i] for i in range(3))


def _chair_children(chair_id: str, keyword: str) -> list:
    chair = bpy.data.objects.get(chair_id)
    if chair is None:
        return []
    if keyword == "arm":
        return [
            obj
            for obj in chair.children_recursive
            if obj.type == "MESH" and "arm" in obj.name.lower()
        ]
    return [
        obj
        for obj in chair.children_recursive
        if obj.type == "MESH" and keyword in obj.name.lower()
    ]


def chair_seat_box(chair_id: str):
    """Placement hint from seat meshes; actual support is established by rays."""
    chair = bpy.data.objects.get(chair_id)
    if chair is None:
        return None
    bpy.context.view_layer.update()
    seats = chair_seat_meshes(chair_id)
    if not seats:
        return None
    points = [seat.matrix_world @ Vector(corner) for seat in seats for corner in seat.bound_box]
    top = max(point.z for point in points)
    center = Vector((
        (min(p.x for p in points) + max(p.x for p in points)) / 2,
        (min(p.y for p in points) + max(p.y for p in points)) / 2,
        0.0,
    ))
    return center, top, chair.matrix_world.to_euler().z


def chair_arm_boxes(chair_id: str, *, local=False) -> list:
    """左右扶手包围盒，按 x 从小到大；没有扶手返回空列表。"""
    chair = bpy.data.objects.get(chair_id)
    basis = chair.matrix_world.inverted() if local and chair is not None else None
    boxes = [object_box(obj, basis) for obj in _chair_children(chair_id, "arm")]
    boxes.sort(key=lambda box: box[0].x)
    return boxes


def chair_clear_seat_width(chair_id: str):
    """扶手之间剩下的净坐宽；没有扶手返回 None。"""
    boxes = chair_arm_boxes(chair_id, local=True)
    if len(boxes) < 2:
        return None
    chair = bpy.data.objects.get(chair_id)
    width = max(boxes[1][0].x - boxes[0][1].x, 0.0)
    return width * abs(chair.matrix_world.to_scale().x)


def narrow_seat_warning(chair_id: str, hip_width: float = HIP_WIDTH):
    clear = chair_clear_seat_width(chair_id)
    if clear is None or clear >= hip_width:
        return None
    return (
        f"椅子 {chair_id} 的净坐宽只有 {clear:.2f} 米（人体髋宽约 {hip_width:.2f} 米）："
        "净宽小于当前核对髋宽，须检查实际人物与扶手几何；按确认参考核对代理类型和尺寸，不为通过检查擅自删扶手或加宽。"
    )


def chair_seat_front_along(chair_id: str, facing: Vector):
    """座面在给定朝向上的最前沿投影；用来限制自动前挪，别把人推出椅子。"""
    seats = chair_seat_meshes(chair_id)
    if not seats:
        return None
    points = [seat.matrix_world @ Vector(corner) for seat in seats for corner in seat.bound_box]
    return max(point.x * facing.x + point.y * facing.y for point in points)


def _actor_meshes(root) -> Iterable:
    if root is None:
        return
    candidates = [root, *root.children_recursive]
    if root.type == "ARMATURE":
        candidates += [obj for obj in bpy.context.scene.objects if obj.type == "MESH"
                       and any(mod.type == "ARMATURE" and mod.object == root for mod in obj.modifiers)]
    seen = set()
    for obj in candidates:
        if obj.type == "MESH" and not obj.hide_render and obj.name not in seen:
            seen.add(obj.name)
            yield obj


def body_points(root) -> list:
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return [evaluated.matrix_world @ vertex.co for obj in _actor_meshes(root)
            for evaluated in [obj.evaluated_get(depsgraph)] for vertex in evaluated.data.vertices]


def body_lowest_z(rig, center_x: float | None = None, center_y: float | None = None,
                  radius: float = HIP_SAMPLE_RADIUS):
    """Evaluated visible body minimum. Seat contact uses seat_contact_points instead."""
    points = body_points(rig)
    if center_x is not None and center_y is not None:
        points = [point for point in points
                  if (point.x - center_x) ** 2 + (point.y - center_y) ** 2 <= radius ** 2]
    return min((point.z for point in points), default=None)


def remember_rest_geometry(root) -> None:
    """Remember rest transforms, not a trusted measured-height scalar.

    Verification still measures the mesh's actual unposed vertices and current
    actor scale. Coarse body-part pose transforms cannot redefine its height.
    """
    bpy.context.view_layer.update()
    inverse = root.matrix_world.inverted()
    for obj in _actor_meshes(root):
        matrix = inverse @ obj.matrix_world
        obj["previs_rest_matrix"] = [value for row in matrix for value in row]
        obj["previs_actor_id"] = root.name


def rest_height(root, orientation_root=None):
    bpy.context.view_layer.update()
    basis = orientation_root if orientation_root is not None else root
    up = basis.matrix_world.to_3x3() @ Vector((0, 0, 1))
    if up.length <= 1e-10:
        return None
    up.normalize()
    values = []
    for obj in _actor_meshes(root):
        saved = obj.get("previs_rest_matrix")
        if saved is not None and obj.get("previs_actor_id") == basis.name:
            matrix = basis.matrix_world @ Matrix([saved[i:i + 4] for i in range(0, 16, 4)])
        else:
            matrix = obj.matrix_world
        values.extend((matrix @ vertex.co).dot(up) for vertex in obj.data.vertices)
    return max(values) - min(values) if values else None


def normalize_actor(root, actor: dict, target_height: float | None) -> None:
    """One evaluated geometry boundary for both backends, before animation."""
    points = body_points(root)  # Includes an explicit dependency-graph update.
    if not points:
        raise RuntimeError(f"人物 {actor['id']} 没有可测量的可见网格")
    height = max(point.z for point in points) - min(point.z for point in points)
    if target_height is not None:
        if height <= 1e-8 or target_height <= 0:
            raise RuntimeError(f"人物 {actor['id']} 的身高无效")
        root.scale *= target_height / height
    bpy.context.view_layer.update()
    support = float(actor.get("position", [0, 0, 0])[2])
    root.location.z += support + CONTACT_CLEARANCE - body_lowest_z(root)
    bpy.context.view_layer.update()
    root["previs_standing_offset_z"] = root.location.z - support
    remember_rest_geometry(root)


def _seat_region_points(root) -> list:
    """Actual butt/thigh geometry only; never foot/calf vertices in a hip disk.

    Coarse parts carry a role. Imported skins use dominant pelvis/thigh weights;
    unrigged semantic hip/butt/thigh meshes are also measurable. Unknown anatomy
    fails closed rather than silently substituting the whole body's minimum.
    """
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for obj in _actor_meshes(root):
        name = obj.name.lower()
        role = obj.get("previs_contact_role")
        whole = role in {"hip", "thigh"} or any(token in name for token in (".hip", ".butt", ".thigh"))
        groups = {group.index: group.name.lower() for group in obj.vertex_groups}
        evaluated = obj.evaluated_get(depsgraph)
        for vertex in evaluated.data.vertices:
            selected = whole
            if not selected and vertex.groups:
                dominant = max(vertex.groups, key=lambda group: group.weight)
                group_name = groups.get(dominant.group, "")
                selected = any(token in group_name for token in ("pelvis", "hips", "thigh", "upleg"))
            if selected:
                points.append(evaluated.matrix_world @ vertex.co)
    return points


def seat_contact_points(root, center_x=None, center_y=None, radius=HIP_SAMPLE_RADIUS) -> list:
    points = _seat_region_points(root)
    if center_x is not None and center_y is not None:
        points = [p for p in points if (p.x - center_x) ** 2 + (p.y - center_y) ** 2 <= radius ** 2]
    if not points:
        return []
    low = min(p.z for p in points)
    # Bottom contact patch plus its centroid, not the entire limb or its AABB.
    points = [p for p in points if p.z <= low + CONTACT_CLEARANCE]
    if points:
        points.append(sum(points, Vector()) / len(points))
    return points


def chair_seat_meshes(chair_id: str) -> list:
    chair = bpy.data.objects.get(chair_id)
    if chair is None:
        return []
    return [obj for obj in [chair, *chair.children_recursive]
            if obj.type == "MESH" and not obj.hide_render
            and ("seat" in obj.name.lower() or obj.get("previs_contact_role") == "seat")]


def surface_z_at(objects: list, point: Vector, reference_z: float):
    """Vertical ray intersection with evaluated surfaces; never an AABB hit.

    Bounding boxes set a safe ray origin only. All intersected layers within
    each mesh are traversed; the upward surface closest to the declared support
    elevation is selected, so a joined ceiling cannot hide the floor.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    hits = []
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        matrix = evaluated.matrix_world
        if abs(matrix.determinant()) <= 1e-12:
            continue
        bounds = [(matrix @ Vector(corner)).z for corner in evaluated.bound_box]
        top, bottom = max(bounds), min(bounds)
        origin = Vector((point.x, point.y, max(top, point.z, reference_z) + 1.0))
        inverse = matrix.inverted()
        direction = (inverse.to_3x3() @ Vector((0, 0, -1))).normalized()
        normal_matrix = matrix.to_3x3().inverted().transposed()
        # One mesh may contain a ceiling, several floors or layered cushions.
        # Step past each actual face hit, not merely the first object's hit.
        # The loop count exceeds the maximum tessellated triangle count, so
        # the traversal is geometry-bounded rather than an arbitrary layer cap.
        epsilon = max(1e-6, (top - bottom) * 1e-8, abs(top) * 1e-7, abs(bottom) * 1e-7)
        object_hits = []
        for _ in range(len(evaluated.data.loops) + 1):
            hit, local, normal, _ = evaluated.ray_cast(inverse @ origin, direction)
            if not hit:
                hits.extend(object_hits)
                break
            world = matrix @ local
            if (normal_matrix @ normal).z > 1e-6:
                object_hits.append(world.z)
            next_z = min(world.z, origin.z) - epsilon
            if next_z < bottom - epsilon:
                hits.extend(object_hits)
                break
            origin.z = next_z
        # An exhausted bound means degenerate/numerically unresolved geometry:
        # do not promote an incomplete list of intersections into support.
    return min(hits, key=lambda height: abs(height - reference_z)) if hits else None


def seat_contact(root, action: dict) -> dict:
    chair_id = action.get("chair_id")
    seat = chair_seat_box(str(chair_id)) if chair_id else None
    if chair_id and seat is None:
        return {"gaps": [], "coverage": 0.0, "reason": f"找不到声明的椅子/座面 {chair_id}"}
    center = seat[0] if seat else root.matrix_world.translation
    top = seat[1] if seat else float(action.get("surface_height", DEFAULT_SEAT_HEIGHT))
    # The actor's pelvis may be offset within a large chair. Use its anatomical
    # contact region; the chair rays, not the sampling circle, prove support.
    points = seat_contact_points(root, center.x, center.y)
    surfaces = chair_seat_meshes(str(chair_id)) if chair_id else []
    gaps = []
    for point in points:
        actual = surface_z_at(surfaces, point, top) if chair_id else top
        if actual is not None:
            gaps.append(point.z - actual)
    return {"gaps": gaps, "coverage": len(gaps) / len(points) if points else 0.0,
            "low": min((point.z for point in points), default=None), "surface_z": top,
            "reason": "臀/大腿下方没有实际座面" if not gaps else ""}


def ground_contact(root, support_z: float, *, excluded_roots=()) -> dict:
    points = body_points(root)
    if not points:
        return {"gaps": [], "source": "missing_body"}
    low = min(point.z for point in points)
    points = [point for point in points if point.z <= low + CONTACT_CLEARANCE]
    excluded = set(_actor_meshes(root))
    for other in excluded_roots:
        if other is not None:
            excluded.update([other, *other.children_recursive])
    surfaces = [obj for obj in bpy.context.scene.objects
                if obj.type == "MESH" and not obj.hide_render and obj not in excluded
                and not obj.get("previs_actor_id")]
    gaps, real_hits = [], 0
    for point in points:
        top = surface_z_at(surfaces, point, support_z)
        if top is not None:
            real_hits += 1
        elif surfaces:
            continue
        # Legacy geometry-only recipes may declare a support plane without a
        # modeled floor. This is explicit in evidence, never used for a chair.
        gaps.append(point.z - (support_z if top is None else top))
    return {"gaps": gaps, "source": "mesh" if real_hits else ("missing_surface" if surfaces else "declared_plane"),
            "mesh_samples": real_hits, "samples": len(points), "low": low}


def thigh_boxes(root, padding: float = BODY_PADDING) -> list:
    """Separate measured thighs for rigs and coarse geometry; no rig waiver."""
    boxes = []
    if root is not None and root.type == "ARMATURE":
        for name in ("thigh_l", "thigh_r"):
            bone = root.pose.bones.get(name)
            if bone is None:
                continue
            head, tail = root.matrix_world @ bone.head, root.matrix_world @ bone.tail
            boxes.append((Vector(tuple(min(head[i], tail[i]) - padding for i in range(3))),
                          Vector(tuple(max(head[i], tail[i]) + padding for i in range(3)))))
    else:
        boxes = [object_box(obj) for obj in _actor_meshes(root)
                 if obj.get("previs_contact_role") == "thigh"]
    boxes.sort(key=lambda box: box[0].x)
    return boxes


def thighs_hit_armrests(root, chair_id: str) -> bool:
    """Use chair-local boxes, so rotating a clear chair cannot create a hit."""
    chair = bpy.data.objects.get(chair_id)
    if chair is None:
        return False
    inverse = chair.matrix_world.inverted()
    if root.type == "ARMATURE":
        boxes = []
        scale = chair.matrix_world.to_scale()
        for name in ("thigh_l", "thigh_r"):
            bone = root.pose.bones.get(name)
            if bone is None:
                continue
            points = [inverse @ root.matrix_world @ point for point in (bone.head, bone.tail)]
            padding = [BODY_PADDING / max(abs(scale[i]), 1e-8) for i in range(3)]
            boxes.append((Vector(tuple(min(p[i] for p in points) - padding[i] for i in range(3))),
                          Vector(tuple(max(p[i] for p in points) + padding[i] for i in range(3)))))
    else:
        boxes = [object_box(obj, inverse) for obj in _actor_meshes(root)
                 if obj.get("previs_contact_role") == "thigh"]
    return any(boxes_intersect(thigh, arm)
               for thigh in boxes for arm in chair_arm_boxes(chair_id, local=True))


def facing_vector(rotation_z: float) -> Vector:
    """角色朝向（本预演的约定：rotation_degrees=0 面向 -Y）。"""
    return Vector((math.sin(rotation_z), -math.cos(rotation_z), 0.0))
