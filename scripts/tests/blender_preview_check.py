"""Isolated Blender -> Three fixtures with real local MPFB skinning; no renders.

blender -b --python scripts/tests/blender_preview_check.py -- --output-dir NEW_DIR
Read expected.json in a Three.js browser to compare evaluated bounds/projection.
"""
import argparse
import json
import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'blender_previs'))
import runner
from export_preview import export_shot, Y_UP
from output_contract import file_sha256


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    template = Path(__file__).resolve().parents[2] / '.master-storyboarding/blender-assets/generated/mpfb-neutral-human/mpfb-neutral-human.glb'
    if not template.is_file():
        raise RuntimeError('先使用已有标准人体模板；本检查不下载或安装资产')
    for label, size in (('landscape', [480, 270]), ('portrait', [270, 480])):
        folder = output / label
        folder.mkdir()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.render.fps = 10
        scene.render.resolution_x, scene.render.resolution_y = size
        scene.render.resolution_percentage = 100
        mat = runner.material('test.gray', (.4, .4, .4, 1))
        runner.cube('Floor', (0, 0, -.1), (3, 3, .1), mat)
        prop = runner.cube('Prop', (2, 0, .5), (.2, .2, .2), mat)
        actors = [
            {'id': 'chr.walk', 'role': 'generic', 'label': '走位人物', 'position': [-1, 0, 0],
             'height_cm': 170, 'display_color': '#238BCB', 'asset_path': str(template),
             'actions': [{'preset': 'walk', 'start': 0, 'duration': 1, 'to': [0, 0, 0]},
                         {'preset': 'turn', 'start': 1, 'duration': 1, 'degrees': 90}]},
            {'id': 'chr.sit', 'role': 'generic', 'label': '坐姿人物', 'position': [1, 0, 0],
             'height_cm': 160, 'display_color': '#D45F41', 'asset_path': str(template),
             'actions': [{'preset': 'sit', 'start': 0, 'duration': 1, 'surface_height': .45}]}]
        runner.resolve_actor_identity_colors({'actors': actors})
        for actor in actors:
            runner.create_actor(actor, 10)
        for obj in list(scene.objects):
            if obj.type == 'MESH' and not obj.hide_render:
                obj['preview_probe'] = obj.name
        stale_data = bpy.data.cameras.new('Stale')
        stale = bpy.data.objects.new('Stale', stale_data)
        scene.collection.objects.link(stale)
        marker = scene.timeline_markers.new('CUT.OLD', frame=1)
        marker.camera = stale
        # Deliberately wrong base timeline. Export must take the current shot range.
        scene.frame_start, scene.frame_end = 1, 900
        source = folder / 'previs.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(source))
        before = file_sha256(source)
        shot = {'id': 'TEST', 'start': 0, 'end': 3, 'position_space': 'tracking_actor_relative',
                'tracking_actor': 'chr.walk', 'lens_mm': 35, 'props': {'Prop': {
                    'position': [1.5, 1, .4], 'rotation_degrees': 40, 'size': [.8, .9, 1.1]}},
                'keyframes': [{'time': 0, 'position': [0, -5, 2.5], 'look_at': [0, 0, .8]},
                              {'time': 1, 'position': [1, -5, 2.5], 'look_at': [0, 0, .8], 'lens_mm': 50},
                              {'time': 2, 'position': [1, -5, 2.5], 'look_at': [0, 0, .8], 'lens_mm': 50},
                              {'time': 3, 'position': [-1, -5, 2.5], 'look_at': [0, 0, .8], 'cut': True}],
                'still_times': [0, 1, 2, 3], 'video': False}
        recipe = {'schema_version': '1.0', 'project': {'fps': 10, 'resolution': size, 'duration_seconds': 3},
                  'actors': actors, 'camera': {'shots': [shot]}}
        manifest = export_shot(source, recipe, folder)
        scene = bpy.context.scene
        assert all(mark.camera is None for mark in scene.timeline_markers)
        assert file_sha256(source) == before, 'preview modified source scene'
        assert len(manifest['frames']) == 31 and manifest['frames'][-1]['time'] == 3
        expected = []
        for time in (0, .5, 1, 1.5, 2, 2.9, 3, 1, 0):
            scene.frame_set(round(time * 10) + 1)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            roots = {a['id']: list(Y_UP @ bpy.data.objects[a['id']].matrix_world.translation) for a in actors}
            meshes = {}
            for obj in scene.objects:
                if obj.type != 'MESH' or obj.hide_render:
                    continue
                actual = obj.evaluated_get(depsgraph)
                points = [Y_UP @ actual.matrix_world @ v.co for v in actual.data.vertices]
                meshes[obj['preview_probe']] = {
                    'min': [min(p[i] for p in points) for i in range(3)],
                    'max': [max(p[i] for p in points) for i in range(3)]}
            pixels = {}
            for name in ('chr.walk', 'chr.sit', 'Prop'):
                point = bpy.data.objects[name].matrix_world.translation
                pos = world_to_camera_view(scene, scene.camera, point)
                pixels[name] = [pos.x * size[0], (1 - pos.y) * size[1]]
            expected.append({'time': time, 'roots': roots, 'meshes': meshes, 'pixels': pixels})
        (folder / 'preview_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        (folder / 'expected.json').write_text(json.dumps(expected), encoding='utf-8')
    print(json.dumps({'ok': True, 'fixtures': 2, 'rendered': False, 'output': str(output)}))


if __name__ == '__main__':
    main()
