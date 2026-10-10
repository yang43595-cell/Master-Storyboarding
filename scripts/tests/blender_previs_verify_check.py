"""Real Blender geometry regressions in isolated synthetic scenes, without rendering.

Run with blender -b --python this_file -- --output-dir NEW_DIR.
The shoot-branch case substitutes file checks; real output evidence is covered by
the existing blender_shoot_path_check.py integration check.
"""
import argparse
import contextlib
import io
import json
import math
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'blender_previs'))
import shoot
import verify


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps = 10
    scene.render.resolution_x = scene.render.resolution_y = 96
    scene.render.resolution_percentage = 100
    return scene


def box(name, position, size=.4, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    obj.name = name
    obj.parent = parent
    obj.location = position
    bpy.context.view_layer.update()
    return obj


def shot():
    return {'id': 'TEST', 'start': 0, 'end': 1, 'lens_mm': 35,
            'keyframes': [{'time': 0, 'position': [0, 0, 0], 'look_at': [0, 0, -1]},
                          {'time': 1, 'position': [0, 0, 0], 'look_at': [0, 0, -1]}]}


def recipe(actor=None, camera_shot=None, duration=1):
    return {'project': {'duration_seconds': duration}, 'actors': [actor] if actor else [],
            'camera': {'shots': [camera_shot]} if camera_shot else {}, 'outputs': {}}


def actor_body(name='chr.test'):
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    body = box(f'{name}.body', (0, 0, .2), parent=root)
    return root, body, {'id': name, 'height_cm': 40, 'actions': []}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = {'blender': bpy.app.version_string, 'rendered': False, 'cases': {}}

    fresh()
    obj = box('Subject', (0, 0, -4))
    camera_shot = dict(shot(), must_include=['Subject'])
    checks = []
    verify.check_framing(recipe(camera_shot=camera_shot), checks)
    assert all(c['ok'] for c in checks), checks
    obj.location.z = 4
    checks = []
    verify.check_framing(recipe(camera_shot=camera_shot), checks)
    assert any(not c['ok'] for c in checks), checks
    result['cases']['front_passes_back_rejected'] = checks

    scene = fresh()
    obj = box('Moving', (0, 0, -4))
    obj.keyframe_insert('location', frame=1)
    obj.location.z = -20
    obj.keyframe_insert('location', frame=11)
    camera_shot = dict(shot(), subject_fill={'target': 'Moving', 'min': .08, 'max': .12})
    checks = []
    verify.check_subject_fill(recipe(camera_shot=camera_shot), checks)
    assert any(not c['ok'] for c in checks), checks
    assert '0.10' in checks[0]['detail'] and '0.02' in checks[0]['detail'], checks
    result['cases']['moving_fill_recomputed'] = checks
    obj.animation_data_clear()
    obj.location.z = -4
    scene.frame_set(1)
    box('Other', (.1, 0, -4), size=.1)
    camera_shot['subject_fill'] = {'targets': ['Moving', 'Other'], 'min': .08, 'max': .12}
    checks = []
    verify.check_subject_fill(recipe(camera_shot=camera_shot), checks)
    assert all(c['ok'] for c in checks), checks
    result['cases']['group_fill_passes'] = checks

    fresh()
    obj = box('Prop', (0, 0, -4))
    before = tuple(obj.rotation_euler)
    assert verify.apply_prop_overrides is shoot.apply_props
    saved = verify.apply_prop_overrides({'Prop': {'rotation_degrees': 90}})
    assert abs(obj.rotation_euler.z - math.pi / 2) < 1e-6
    verify.restore_prop_overrides(saved)
    assert tuple(obj.rotation_euler) == before
    result['cases']['prop_rotation_shared_and_restored'] = True

    scene = fresh()
    root, _body, actor = actor_body()
    actor['actions'] = [{'preset': 'walk', 'start': 0, 'duration': 1}]
    root.keyframe_insert('location', frame=1)
    root.location.z = -1
    root.keyframe_insert('location', frame=11)
    checks = []
    sampling = verify.check_actors(recipe(actor), checks)
    assert any(c['id'].startswith('floor:') and c['sample_frame'] == 1 and c['ok'] for c in checks)
    assert any(c['id'].startswith('floor:') and c['sample_frame'] == 11 and not c['ok'] for c in checks)
    result['cases']['later_floor_penetration_rejected'] = {'sampling': sampling, 'checks': checks}

    # Rest height is not world-Z clearance: tilting a long character cannot shrink it.
    fresh()
    root, body, actor = actor_body()
    body.scale = (.5, .25, 2)
    root.scale = (.5,) * 3
    bpy.context.view_layer.update()
    upright = verify.rest_height(root)
    root.rotation_euler = tuple(math.radians(v) for v in (65, 17, 31))
    root.location = (2, -3, 4)
    bpy.context.view_layer.update()
    prone = verify.rest_height(root)
    assert abs(upright - .4) < 1e-5 and abs(prone - upright) < 1e-5
    checks = []
    verify.check_actor(actor, checks)
    assert all(c['ok'] for c in checks if c['id'].endswith(':height'))
    root.scale = (.75,) * 3
    bpy.context.view_layer.update()
    checks = []
    verify.check_actor(actor, checks)
    assert any(not c['ok'] for c in checks if c['id'].endswith(':height'))
    result['cases']['rest_height_rotation_invariant_scale_change_rejected'] = {
        'upright': upright, 'prone': prone, 'changed_scale_checks': checks}

    # A sit state persists after duration, until the following stand_up begins.
    scene = fresh()
    root, _body, actor = actor_body()
    actor['actions'] = [{'preset': 'sit', 'start': .5, 'duration': .25, 'surface_height': .45},
                        {'preset': 'stand_up', 'start': 1.5, 'duration': .5}]
    root.location.z = .45
    root.keyframe_insert('location', frame=1)
    root.location.z = .3
    root.keyframe_insert('location', frame=6)
    root.keyframe_insert('location', frame=11)
    root.location.z = 1
    root.keyframe_insert('location', frame=16)
    root.keyframe_insert('location', frame=21)
    checks = []
    seating_recipe = recipe(actor, duration=2)
    seating_recipe['outputs']['still_frames'] = [1]
    verify.check_actors(seating_recipe, checks)
    assert any(c['id'].startswith('seat:') and c['sample_frame'] == 6 and not c['ok'] for c in checks)
    assert any(c['id'].startswith('seat:') and c['sample_frame'] == 11 and not c['ok'] for c in checks)
    assert not any(c['id'].startswith('seat:') and c['sample_frame'] >= 16 for c in checks)
    result['cases']['sit_state_and_following_stand'] = checks

    fresh()
    _root, _body, actor = actor_body()
    actor['actions'] = [{'preset': 'sit', 'start': 0, 'duration': 1, 'chair_id': 'missing-chair'}]
    checks = []
    verify.check_actor(actor, checks)
    assert any(c['id'].startswith('seat:') and not c['ok'] for c in checks)
    result['cases']['missing_seat_rejected'] = checks

    # Exercise verify.main's --shots dispatch with real saved Blender geometry.
    # File checks are separate from this geometry-only case.
    original_outputs = verify.check_shoot_outputs
    verify.check_shoot_outputs = lambda *args: [{'id': 'fixture:files', 'ok': True, 'detail': 'geometry-only fixture'}]
    try:
        for sinking in (False, True):
            scene = fresh()
            root, _body, actor = actor_body()
            actor['actions'] = [{'preset': 'walk', 'start': 0, 'duration': 1}]
            root.keyframe_insert('location', frame=1)
            root.location.z = -1 if sinking else 0
            root.keyframe_insert('location', frame=11)
            scene.frame_set(1)
            scene_path = output / f'fixture-{sinking}.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(scene_path))
            camera_shot = shot()
            recipe_path, shots_path = output / 'recipe.json', output / 'shots.json'
            recipe_path.write_text(json.dumps(recipe(actor, camera_shot)), encoding='utf-8')
            shots_path.write_text(json.dumps({'shots': [camera_shot]}), encoding='utf-8')
            sys.argv[:] = ['verify', '--', '--recipe', str(recipe_path), '--scene', str(scene_path),
                           '--shots', str(shots_path), '--output-dir', str(output)]
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                try:
                    verify.main()
                except SystemExit as exc:
                    code = exc.code
            report = json.loads(captured.getvalue())
            assert bool(report['ok']) is (not sinking), report
            assert code == (9 if sinking else 0), report
            assert any(c['id'].startswith('floor:') for c in report['checks']), report
            result['cases'][f'shoot_actor_checks_sinking_{sinking}'] = report
    finally:
        verify.check_shoot_outputs = original_outputs
    # stage-shot already stores ShotCamera.*; shoot/export/verify must update it
    # in place, including old files predating the ownership tag.
    fresh()
    import runner
    original = shot()
    camera = runner.create_camera_object(original, 10, 'ShotCamera.TEST')
    target = camera.constraints[-1].target
    unrelated = bpy.data.objects.new('UnrelatedCamera', camera.data)
    bpy.context.scene.collection.objects.link(unrelated)
    old_lens = unrelated.data.lens
    revised = dict(original, lens_mm=50, keyframes=[
        {'time': 0, 'position': [1, -4, 1], 'look_at': [0, 0, 0]},
        {'time': 1, 'position': [2, -4, 1], 'look_at': [0, 0, 0]}])
    for _ in range(3):
        assert shoot.make_camera(revised) == camera
        assert camera.constraints[-1].target == target
        assert verify.find_shot_camera('TEST') == camera
        bpy.context.scene.frame_set(11)
        assert abs(camera.location.x - 2) < 1e-6
        assert camera.data.lens == 50 and unrelated.data.lens == old_lens
    assert not bpy.data.objects.get('ShotCamera.TEST.001')
    result['cases']['staged_camera_reused_and_updated'] = True

    actor_body()
    tracking = dict(revised, position_space='tracking_actor_relative', tracking_actor='chr.test')
    assert shoot.make_camera(tracking) == camera
    rig = camera.parent
    assert rig is not None
    assert shoot.make_camera(revised) == camera and camera.parent is None
    assert 'tracking_actor' not in camera and camera['position_space'] == 'world'
    assert shoot.make_camera(tracking) == camera and camera.parent == rig
    assert camera['tracking_actor'] == 'chr.test'
    assert len(camera.constraints) == len(rig.constraints) == 1
    assert not bpy.data.objects.get('ShotCamera.TEST.RelativeRig.001')
    result['cases']['camera_follow_world_follow_reuses_helpers'] = True

    for kind in ('camera', 'helper', 'other_scene'):
        fresh()
        if kind == 'helper':
            foreign = bpy.data.objects.new('ShotCamera.TEST.Target', None)
        else:
            foreign = bpy.data.objects.new('ShotCamera.TEST', bpy.data.cameras.new('Foreign'))
        if kind != 'other_scene':
            bpy.context.scene.collection.objects.link(foreign)
        foreign.location = (7, 8, 9)
        before = len(bpy.data.objects)
        try:
            shoot.make_camera(original)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Unrelated camera/helper name was silently reused')
        assert tuple(foreign.location) == (7, 8, 9) and len(bpy.data.objects) == before
        result['cases'][f'unrelated_{kind}_preserved'] = True

    result['ok'] = True
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'cases': len(result['cases']), 'report': str(output / 'result.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
