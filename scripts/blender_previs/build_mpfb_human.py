"""Build a reusable, neutral, skinned MPFB human from Blender's command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def arguments() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--keep-helpers",
        action="store_true",
        help="保留头发/裙子等辅助几何与手指脚趾细节（默认删掉，避免白模被误读成穿裙子或诱导精细手部动作）",
    )
    return parser.parse_args(argv)


# 辅助几何：这些是给衣服/毛发贴合用的网格，白模里会被误读成服装；手指脚趾细节
# 则容易诱导成片复刻精细手部动作，一并去掉，只保留方位识别所需的轮廓。
HELPER_GROUPS = {
    "helper-hair",
    "helper-skirt",
    "helper-tights",
    "helper-genital",
    "helper-l-eyelashes-1",
    "helper-l-eyelashes-2",
    "helper-r-eyelashes-1",
    "helper-r-eyelashes-2",
    "helper-lower-teeth",
    "helper-upper-teeth",
    "helper-tongue",
}
# MPFB 的 JointCubes / 关节标记方块是给绑骨调试用的，白模里会变成脸上、下巴上的小方块。
EXTRA_GROUP_MARKERS = ("jointcube", "joint-cube")
FINGER_PREFIXES = ("index_", "middle_", "pinky_", "ring_", "thumb_")
TOE_MARKERS = ("-toe-", "toenails")


def simplify_body(body) -> int:
    """删掉辅助几何与手指脚趾，并补上切口，返回删除的顶点数。"""
    import bmesh

    doomed_groups = {
        group.index
        for group in body.vertex_groups
        if group.name in HELPER_GROUPS
        or group.name.startswith(FINGER_PREFIXES)
        or any(marker in group.name for marker in TOE_MARKERS)
        or any(marker in group.name.lower() for marker in EXTRA_GROUP_MARKERS)
    }
    if not doomed_groups:
        return 0
    bm = bmesh.new()
    bm.from_mesh(body.data)
    layer = bm.verts.layers.deform.verify()
    doomed = [
        vert
        for vert in bm.verts
        if sum(weight for group, weight in vert[layer].items() if group in doomed_groups) > 0.5
    ]
    bmesh.ops.delete(bm, geom=doomed, context="VERTS")
    open_edges = [edge for edge in bm.edges if len(edge.link_faces) == 1]
    if open_edges:
        bmesh.ops.holes_fill(bm, edges=open_edges, sides=0)
    bm.to_mesh(body.data)
    bm.free()
    body.data.update()
    return len(doomed)


def main() -> None:
    args = arguments()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        from bl_ext.blender_org.mpfb.services import HumanService
    except ImportError as exc:
        raise RuntimeError("Blender 中未安装官方 MPFB 扩展") from exc

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    macro = {
        "age": 0.5,
        "gender": 0.35,
        "muscle": 0.45,
        "weight": 0.42,
        "height": 0.52,
        "proportions": 0.5,
        "race": {"african": 0.15, "asian": 0.7, "caucasian": 0.15},
    }
    body = HumanService.create_human(
        mask_helpers=True,
        detailed_helpers=True,
        extra_vertex_groups=True,
        feet_on_ground=True,
        scale=0.1,
        macro_detail_dict=macro,
    )
    body.name = "PrevisHuman.Body"
    # 先绑骨再删几何：MPFB 绑骨要按顶点组算位置，删空的组会让它除零。
    rig = HumanService.add_builtin_rig(body, "game_engine", import_weights=True)
    rig.name = "PrevisHuman.Rig"
    removed = 0 if args.keep_helpers else simplify_body(body)
    body["asset_license"] = "CC0-1.0"
    body["asset_generator"] = "MPFB"
    body["simplified_vertex_removal"] = removed
    rig["rig_profile"] = "makehuman.game_engine"

    skin = bpy.data.materials.new("PrevisHuman.NeutralSkin")
    skin.diffuse_color = (0.56, 0.42, 0.36, 1)
    skin.use_nodes = True
    bsdf = skin.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.56, 0.42, 0.36, 1)
    bsdf.inputs["Roughness"].default_value = 0.58
    body.data.materials.clear()
    body.data.materials.append(skin)

    blend_path = output_dir / "mpfb-neutral-human.blend"
    glb_path = output_dir / "mpfb-neutral-human.glb"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.export_scene.gltf(
        filepath=str(glb_path),
        export_format="GLB",
        use_selection=True,
        export_skins=True,
        export_animations=True,
        export_apply=False,
    )
    report = {
        "ok": True,
        "generator": "MPFB",
        "license": "CC0-1.0",
        "rig_profile": "makehuman.game_engine",
        "mesh_vertices": len(body.data.vertices),
        "bones": len(rig.data.bones),
        "simplified_vertex_removal": removed,
        "helper_geometry": "removed" if removed else "kept",
        "blend": str(blend_path),
        "glb": str(glb_path),
    }
    (output_dir / "mpfb-neutral-human.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
