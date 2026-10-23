"""Local Blender staging on a saved reusable room; never rebuild its geometry."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner import create_actor, resolve_actor_identity_colors
from shoot import make_camera


def geometry_digest(objects):
    digest = hashlib.sha256()
    for obj in sorted(objects, key=lambda o: o.name):
        digest.update(obj.name.encode())
        digest.update(str([list(row) for row in obj.matrix_world]).encode())
        if obj.type == 'MESH':
            for vertex in obj.data.vertices:
                digest.update(str(tuple(vertex.co)).encode())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scene', required=True)
    parser.add_argument('--recipe', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    base = Path(args.scene).resolve()
    base_hash = hashlib.sha256(base.read_bytes()).hexdigest()
    recipe = json.loads(Path(args.recipe).read_text(encoding='utf-8'))
    output = Path(args.output_dir).resolve()
    if (output / 'previs.blend').exists():
        raise RuntimeError('不可覆盖逐镜布置产物')
    bpy.ops.wm.open_mainfile(filepath=str(base))
    scene = bpy.context.scene
    old_objects = [obj for obj in scene.objects if obj.type == 'MESH']
    before = geometry_digest(old_objects)
    actor_ids = [a['id'] for a in recipe['actors']]
    if any(bpy.data.objects.get(actor_id) for actor_id in actor_ids):
        raise RuntimeError('基座已有同名人物；先核对它的状态，不能静默重建/覆盖')
    scene.render.fps = recipe['project']['fps']
    scene.render.resolution_x, scene.render.resolution_y = recipe['project']['resolution']
    scene.render.resolution_percentage = 100
    resolve_actor_identity_colors(recipe)
    for actor in recipe['actors']:
        create_actor(actor, scene.render.fps)
    for marker in scene.timeline_markers:
        marker.camera = None
    for shot in recipe['camera']['shots']:
        camera = make_camera(shot)
        camera.data.sensor_fit = 'VERTICAL' if scene.render.resolution_y > scene.render.resolution_x else 'HORIZONTAL'
        camera.data.sensor_height = 36
        scene.camera = camera
    scene.frame_set(1)
    bpy.context.view_layer.update()
    if geometry_digest(old_objects) != before:
        raise RuntimeError('房间基座几何或位置变化，不保存逐镜场景')
    scene['previs_source_scene'] = str(base)
    scene['previs_shot_ids'] = ','.join(s['id'] for s in recipe['camera']['shots'])
    bpy.ops.wm.save_as_mainfile(filepath=str(output / 'previs.blend'))
    if hashlib.sha256(base.read_bytes()).hexdigest() != base_hash:
        raise RuntimeError('原空间基座文件变化')
    report = dict(ok=True, source_scene=str(base), source_scene_sha256=base_hash,
                  room_geometry_unchanged=True, actors=actor_ids,
                  camera_shot_ids=[s['id'] for s in recipe['camera']['shots']],
                  staged_scene_sha256=hashlib.sha256((output / 'previs.blend').read_bytes()).hexdigest(),
                  recipe_sha256=hashlib.sha256(Path(args.recipe).read_bytes()).hexdigest(),
                  rendered=False, video_rendered=False)
    if (output / 'shot-input.json').is_file():
        report['binding_sha256'] = hashlib.sha256((output / 'shot-input.json').read_bytes()).hexdigest()
    (output / 'stage_manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
