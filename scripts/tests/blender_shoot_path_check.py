"""Real Blender integration check; run with blender -b --python this_file -- --output-dir NEW_DIR.

Creates only an isolated test scene and tiny PNGs; never reads production assets.
"""
import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'blender_previs'))
import shoot
import verify
import runner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    root = Path(args.output_dir).resolve()
    root.mkdir(parents=True, exist_ok=False)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x = 96
    scene.render.resolution_y = 96
    scene.render.resolution_percentage = 100
    scene.render.fps = 10
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, 0))
    bpy.context.object.name = 'TestSubject'
    # Include a stale build camera: verify --shots must honor the new shot list.
    data = bpy.data.cameras.new('DirectorCamera.TEST')
    stale = bpy.data.objects.new('DirectorCamera.TEST', data)
    scene.collection.objects.link(stale)
    stale.location = (100, 100, 100)
    scene.camera = stale
    marker = scene.timeline_markers.new('CUT.BASE', frame=1)
    marker.camera = stale
    base = root / 'base.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(base))
    keys = [
        {'time': 0, 'position': [0, -4, 1], 'look_at': [0, 0, 0]},
        {'time': 1, 'position': [1, -4, 1], 'look_at': [0.1, 0, 0], 'lens_mm': 50},
        {'time': 2, 'position': [1, -4, 1], 'look_at': [0.1, 0, 0], 'lens_mm': 50},
        {'time': 3, 'position': [-1, -4, 1.5], 'look_at': [0, 0, 0], 'lens_mm': 40},
        {'time': 4, 'position': [0, -5, 1], 'look_at': [0, 0, 0], 'cut': True},
    ]
    shot = {'id': 'TEST', 'start': 1, 'end': 5, 'lens_mm': 35,
            'keyframes': keys, 'must_include': ['TestSubject'],
            'subject_fill': {'target': 'TestSubject', 'min': 0.01, 'max': 0.5},
            'still_times': [1, 2, 2.5, 3, 4, 5], 'video': True}
    shots = root / 'shots.json'
    shots.write_text(json.dumps({'shots': [shot]}), encoding='utf-8')
    recipe = root / 'recipe.json'
    recipe.write_text(json.dumps({'project': {'duration_seconds': 5}}), encoding='utf-8')
    output = root / 'shoot'
    sys.argv = ['shoot', '--', '--scene', str(base), '--shots', str(shots),
                '--output-dir', str(output)]
    shoot.main()
    scene = bpy.context.scene  # shoot reopens the base file; old RNA handles expire.
    camera = bpy.data.objects['ShotCamera.TEST']
    assert all(marker.camera is None for marker in scene.timeline_markers)
    target = camera.constraints[-1].target
    measurements = []

    def check_at(time, expected):
        frame = round((shot['start'] + time) * scene.render.fps) + 1
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        assert scene.camera == camera, (time, scene.camera.name)
        actual = camera.evaluated_get(bpy.context.evaluated_depsgraph_get())
        position_error = (actual.matrix_world.translation - Vector(expected['position'])).length
        target_error = (target.matrix_world.translation - Vector(expected['look_at'])).length
        lens_error = abs(camera.data.lens - expected.get('lens_mm', shot['lens_mm']))
        forward = actual.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        wanted = (Vector(expected['look_at']) - actual.matrix_world.translation).normalized()
        assert position_error < 1e-5, (time, position_error)
        assert target_error < 1e-5, (time, target_error)
        assert lens_error < 1e-5, (time, lens_error)
        assert forward.dot(wanted) > 0.99999, (time, forward.dot(wanted))
        measurements.append({'time': time, 'frame': frame, 'position_error': position_error,
                             'target_error': target_error, 'lens_error': lens_error})

    for key in keys:
        check_at(key['time'], key)
    check_at(1.5, keys[1])  # intermediate hold must not overshoot
    check_at(3.9, keys[3])  # cut preserves preceding state until the previous frame
    frames = sorted((output / 'reference-frames').glob('*.png'))
    assert len(frames) == 6 and all(p.stat().st_size > 0 for p in frames)
    def pixels(path):
        image = bpy.data.images.load(str(path), check_existing=False)
        result = tuple(image.pixels)
        bpy.data.images.remove(image)
        return result
    # PNG metadata includes the rendered frame; compare image pixels instead.
    assert pixels(frames[1]) == pixels(frames[2]) == pixels(frames[3]), 'hold pixels differ'
    assert pixels(frames[0]) != pixels(frames[4]), 'motion renders did not change'
    before_manifest = (output / 'shoot_manifest.json').read_bytes()
    before_frames = {path.name: path.read_bytes() for path in frames}
    try:
        shoot.main()
    except RuntimeError as exc:
        assert '输出目录必须为空' in str(exc)
    else:
        raise AssertionError('shoot accepted a nonempty output directory')
    assert (output / 'shoot_manifest.json').read_bytes() == before_manifest
    assert {path.name: path.read_bytes() for path in frames} == before_frames
    # Verify real PNGs and encoded video in a reopened base scene.
    def verify_result():
        sys.argv[:] = ['verify', '--', '--recipe', str(recipe), '--scene', str(base),
                       '--shots', str(shots), '--output-dir', str(output)]
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            try:
                verify.main()
            except SystemExit as exc:
                code = exc.code
        return code, json.loads(captured.getvalue())
    code, report = verify_result()
    assert code == 0, report
    assert report['ok'] and report['failed'] == 0
    # Keeping the total PNG count cannot disguise a missing required frame.
    missing = frames[-1]
    original = missing.read_bytes()
    missing.unlink()
    unrelated = missing.parent / 'unrelated.png'
    unrelated.write_bytes(original)
    code, missing_report = verify_result()
    assert code != 0 and not missing_report['ok']
    unrelated.unlink()
    missing.write_bytes(original)
    # Old output must not validate against a revised camera list.
    shot['keyframes'][0]['position'][0] = 0.1
    shots.write_text(json.dumps({'shots': [shot]}), encoding='utf-8')
    code, changed_report = verify_result()
    assert code != 0 and any(c['id'] == 'inputs:shots' and not c['ok'] for c in changed_report['checks'])
    shot['keyframes'][0]['position'][0] = 0
    shots.write_text(json.dumps({'shots': [shot]}), encoding='utf-8')
    video_path = output / 'TEST_previs.mp4'
    video_bytes = video_path.read_bytes()
    video_path.unlink()
    code, video_report = verify_result()
    assert code != 0 and any(c['id'] == 'file:TEST:video' and not c['ok'] for c in video_report['checks'])
    video_path.write_bytes(video_bytes)
    # Exercise build through the real runner as well: the same file evidence
    # must reject revised input, same-count replacement PNGs and missing video.
    build_recipe = {
        'schema_version': '1.0',
        'project': {'name': 'BuildEvidence', 'fps': 10, 'duration_seconds': 2, 'resolution': [64, 64]},
        'environment': {'preset': 'studio'}, 'actors': [],
        'camera': {'shots': [{'id': 'B0', 'start': 0, 'end': 1, 'position_space': 'world',
                              'keyframes': [{'time': 0, 'position': [0, -4, 1], 'look_at': [0, 0, 1]},
                                            {'time': 1, 'position': [0, -4, 1], 'look_at': [0, 0, 1]}]},
                             {'id': 'B1', 'start': 1, 'end': 2, 'position_space': 'world',
                              'keyframes': [{'time': 0, 'position': [0, -3, 1], 'look_at': [0, 0, 1]},
                                            {'time': 1, 'position': [0, -3, 1], 'look_at': [0, 0, 1]}]}]},
        'outputs': {'blend': True, 'preview_video': True, 'preview_video_shots': ['B1'], 'still_frames': [0, 2],
                    'reference_views': [{'id': 'BREF', 'time': 1.5, 'mode': 'timeline_camera',
                                         'purpose': 'test', 'shot_id': 'B1'}],
                    'topdown': {'enabled': True, 'video': True, 'still_times': [.5]}}}
    build_path, build_output = root / 'build.recipe.json', root / 'build'
    build_path.write_text(json.dumps(build_recipe), encoding='utf-8')
    bpy.ops.wm.read_factory_settings(use_empty=False)  # build CLI starts in its own fresh process.
    sys.argv[:] = ['runner', '--', '--recipe', str(build_path), '--output-dir', str(build_output)]
    runner.main()
    def verify_build_result():
        sys.argv[:] = ['verify', '--', '--recipe', str(build_path), '--output-dir', str(build_output)]
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            try:
                verify.main()
            except SystemExit as exc:
                code = exc.code
        return code, json.loads(captured.getvalue())
    code, build_report = verify_build_result()
    assert code == 0 and build_report['ok'], build_report
    build_recipe['project']['name'] = 'Revised'
    build_path.write_text(json.dumps(build_recipe), encoding='utf-8')
    code, build_changed = verify_build_result()
    assert code != 0 and any(c['id'] == 'inputs:recipe' and not c['ok'] for c in build_changed['checks'])
    build_recipe['project']['name'] = 'BuildEvidence'
    build_path.write_text(json.dumps(build_recipe), encoding='utf-8')
    build_missing = {}
    for relative in ('reference-frames/BREF.png', 'stills/frame-00.00.png'):
        required = build_output / relative
        data = required.read_bytes()
        required.unlink()
        replacement = required.parent / 'unrelated.png'
        replacement.write_bytes(data)
        code, rejected = verify_build_result()
        assert code != 0 and not rejected['ok']
        build_missing[relative] = rejected['failed']
        replacement.unlink()
        required.write_bytes(data)
    required_video = build_output / 'previs.mp4'
    data = required_video.read_bytes()
    required_video.unlink()
    code, build_video_missing = verify_build_result()
    assert code != 0 and not build_video_missing['ok']
    required_video.write_bytes(data)
    result = {'ok': True, 'blender': bpy.app.version_string, 'renders': len(frames),
              'measurements': measurements, 'verify': report, 'video_rendered': True,
              'rejections': {'missing_frame': missing_report['failed'],
                             'changed_shots': changed_report['failed'], 'missing_video': video_report['failed']},
              'build_verify': build_report,
              'build_rejections': {'changed_recipe': build_changed['failed'],
                                   'missing_pngs': build_missing, 'missing_video': build_video_missing['failed']}}
    (root / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'renders': len(frames), 'output_dir': str(root)}))


if __name__ == '__main__':
    main()
