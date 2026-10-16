"""Bake the actual Blender shot for a read-only Three.js player, without rendering."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shoot import prepare_shoot_scene, make_camera, apply_props
from output_contract import file_sha256, render_settings, validate_shots

# Blender Z-up -> glTF/Three Y-up. Camera local axes already match (-Z forward).
Y_UP = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))


def column_major(matrix):
    return [float(matrix[row][col]) for col in range(4) for row in range(4)]


def export_shot(scene_path: Path, recipe: dict, output: Path) -> dict:
    bpy.ops.wm.open_mainfile(filepath=str(scene_path))
    scene = bpy.context.scene
    settings = render_settings(scene)
    settings['resolution'] = [settings['width'], settings['height']]
    if settings['fps_base'] != 1.0:
        raise RuntimeError('预览帧率 fps_base 必须为 1')
    if settings['fps'] != recipe['project']['fps'] or settings['resolution'] != recipe['project']['resolution']:
        raise RuntimeError('白模帧率或画幅与本镜有效配方不一致，请重新布置')
    spec = recipe['camera']
    validate_shots(spec, settings['fps'])
    if len(spec['shots']) != 1:
        raise RuntimeError('浏览器预览每次只导出一个已绑定镜头')
    shot = spec['shots'][0]
    prepare_shoot_scene(scene)
    camera = make_camera(shot)
    scene.camera = camera
    apply_props(shot.get('props'))
    fps = settings['fps']
    # Include the terminal state; unlike the rendered video it is a seekable endpoint.
    first = round(float(shot['start']) * fps) + 1
    last = round(float(shot['end']) * fps) + 1
    scene.frame_start, scene.frame_end = first, last
    actors = []
    for actor in recipe['actors']:
        root = bpy.data.objects.get(actor['id'])
        if root is None:
            raise RuntimeError('逐镜场景缺少人物：' + actor['id'])
        root['previs_actor_id'] = actor['id']
        actors.append({'id': actor['id'], 'label': actor.get('label') or actor['id'],
                       'color': root.get('display_color', actor.get('display_color', '#aaaaaa'))})
    width, height = settings['resolution']
    frames = []
    low, high = [float('inf')] * 3, [float('-inf')] * 3
    for frame in range(first, last + 1):
        scene.frame_set(frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        actual = camera.evaluated_get(depsgraph)
        projection = actual.calc_matrix_camera(depsgraph, x=width, y=height,
                                               scale_x=scene.render.pixel_aspect_x,
                                               scale_y=scene.render.pixel_aspect_y)
        frames.append({'time': (frame - first) / fps,
                       'matrix_world': column_major(Y_UP @ actual.matrix_world),
                       'projection_matrix': column_major(projection)})
        # Bounds across the entire motion keep orbit and top-down views useful.
        for obj in scene.objects:
            if obj.type != 'MESH' or obj.hide_render:
                continue
            evaluated = obj.evaluated_get(depsgraph)
            for corner in evaluated.bound_box:
                point = Y_UP @ evaluated.matrix_world @ Vector(corner)
                for axis in range(3):
                    low[axis] = min(low[axis], float(point[axis]))
                    high[axis] = max(high[axis], float(point[axis]))
    if not all(value != float('inf') for value in low):
        raise RuntimeError('白模没有可显示的网格')
    scene.frame_set(first)
    glb = output / 'preview.glb'
    # SCENE samples evaluated NLA/bone/constraint results, rather than exporting
    # separate raw Actions. All nodes use one common clock starting at zero.
    bpy.ops.export_scene.gltf(filepath=str(glb), export_format='GLB', use_active_scene=True,
        use_renderable=True, export_extras=True, export_yup=True,
        export_cameras=False, export_lights=False, export_animations=True,
        export_animation_mode='SCENE', export_anim_scene_split_object=False,
        export_force_sampling=True, export_bake_animation=True, export_frame_step=1,
        export_frame_range=True, export_anim_slide_to_zero=True,
        export_optimize_animation_size=False, export_morph=True, export_apply=True)
    return {'schema_version': '1.0', 'shot_id': shot['id'], 'fps': fps,
            'resolution': settings['resolution'], 'duration_seconds': (last - first) / fps,
            'actors': actors, 'bounds': {'min': low, 'max': high}, 'frames': frames,
            'glb': {'path': glb.name, 'sha256': file_sha256(glb)}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scene', required=True)
    parser.add_argument('--recipe', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    output = Path(args.output_dir).resolve()
    result = export_shot(Path(args.scene).resolve(), json.loads(Path(args.recipe).read_text(encoding='utf-8')), output)
    (output / 'preview_manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'shot_id': result['shot_id'], 'frames': len(result['frames']), 'rendered': False}))


if __name__ == '__main__':
    main()
