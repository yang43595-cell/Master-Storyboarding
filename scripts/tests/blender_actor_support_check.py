"""Real Blender repair assertions adapted from 2026-10-07 root-cause fixtures.

blender -b --factory-startup --disable-autoexec --python-exit-code 1
  --python scripts/tests/blender_actor_support_check.py -- --output-dir NEW_DIR
No rendering, MPFB installation, source overwrite, or substitute production functions.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'blender_previs'))
import contacts
import runner
import verify
from actor_state import effective_actor_state


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = 10


def empty(name, location=(0, 0, 0)):
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    return obj


def box(name, location, size, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.name, obj.parent, obj.location, obj.scale = name, parent, location, size
    bpy.context.view_layer.update()
    return obj


def chair(location=(0, 0, 0), arms=None, angle=0):
    root = empty('chair', location)
    root.rotation_euler.z = math.radians(angle)
    box('chair.seat', (0, 0, .4), (.6, .6, .1), root)
    if arms is not None:
        for side in (-1, 1):
            box(f'chair.arm.{side}', (side * arms, 0, .60), (.10, .6, .30), root)
    bpy.context.view_layer.update()
    return root


def checks(actor, frame=1):
    bpy.context.scene.frame_set(frame)
    bpy.context.view_layer.update()
    result = []
    verify.check_actor(actor, result)
    verify.check_ground([actor], result)
    return result


def one(result, prefix):
    return next(item for item in result if item['id'].startswith(prefix))


def joined_mesh(name, objects):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    result = bpy.context.object
    result.name = name
    bpy.context.view_layer.update()
    return result


def export_synthetic_rig(path):
    """A real weighted test rig with required preset bones, explicitly not MPFB."""
    fresh()
    data = bpy.data.armatures.new("TestArmature")
    rig = bpy.data.objects.new("TestRig", data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    definitions = {
        'pelvis': ((0, 0, .95), (0, 0, 1.10), None),
        'spine_01': ((0, 0, 1.10), (0, 0, 1.30), 'pelvis'),
        'spine_02': ((0, 0, 1.30), (0, 0, 1.50), 'spine_01'),
        'spine_03': ((0, 0, 1.50), (0, 0, 1.65), 'spine_02'),
    }
    for side, x in (('l', -.14), ('r', .14)):
        definitions.update({
            'thigh_' + side: ((x, 0, .95), (x, 0, .51), 'pelvis'),
            'calf_' + side: ((x, 0, .51), (x, 0, .12), 'thigh_' + side),
            'foot_' + side: ((x, 0, .12), (x, -.16, .07), 'calf_' + side),
            'upperarm_' + side: ((x * 2, 0, 1.5), (x * 3.7, 0, 1.3), 'spine_03'),
            'lowerarm_' + side: ((x * 3.7, 0, 1.3), (x * 4.5, 0, 1.1), 'upperarm_' + side),
        })
    for name, (head, tail, parent) in definitions.items():
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode='OBJECT')
    material = runner.material('RigBody', (.4, .4, .4, 1))
    for name, (head, tail, parent) in definitions.items():
        center = (Vector(head) + Vector(tail)) / 2
        radius = .095 if name.startswith('thigh') else .065
        if name == 'pelvis':
            size = (.32, .20, .18)
        else:
            size = tuple(abs(tail[i] - head[i]) + radius for i in range(3))
        mesh = box('TestBody.' + name, center, size, rig)
        # Freeze object transforms before binding to the bone's rest space.
        bpy.context.view_layer.objects.active = mesh
        bpy.ops.object.select_all(action='DESELECT')
        mesh.select_set(True)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        mesh.data.materials.append(material)
        group = mesh.vertex_groups.new(name=name)
        group.add(list(range(len(mesh.data.vertices))), 1, 'REPLACE')
        modifier = mesh.modifiers.new('Skin', 'ARMATURE')
        modifier.object = rig
    head = box('TestBody.head', (0, 0, 1.78), (.25, .20, .25), rig)
    bpy.context.view_layer.objects.active = head
    bpy.ops.object.select_all(action='DESELECT')
    head.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    head.data.materials.append(material)
    group = head.vertex_groups.new(name='spine_03')
    group.add(list(range(len(head.data.vertices))), 1, 'REPLACE')
    head.modifiers.new('Skin', 'ARMATURE').object = rig
    bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', required=True)
    output = Path(parser.parse_args(sys.argv[sys.argv.index('--') + 1:]).output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'blender_version': bpy.app.version_string, 'rendered': False,
              'fixture_scope': 'Actual coarse actors, synthetic geometry and GLB import; not MPFB', 'cases': {}}
    cases = report['cases']
    # Whole built geometry after depsgraph update, including princess adornments.
    heights = []
    for role in ('generic', 'princess', 'maid', 'butler'):
        for height in (.15, 1.5, 1.9):
            for support in (0, .82, -2):
                fresh()
                actor = dict(id='chr.test', role=role, height_cm=height * 100, position=[0, 0, support])
                root = runner.create_actor(actor, 10)
                measured = verify.rest_height(root)
                low = contacts.body_lowest_z(root)
                assert abs(measured - height) < 1e-5, (actor, measured)
                assert abs(low - support - contacts.CONTACT_CLEARANCE) < 1e-5, (actor, low)
                heights.append({'role': role, 'height': height, 'support': support, 'measured': measured, 'low': low})
    cases['coarse_declared_heights_and_support'] = heights
    # Preserve old scale as role-geometry multiplier, while explicitly declared height wins.
    for role in ('generic', 'princess'):
        actual = []
        for scale in (1, .5):
            fresh()
            root = runner.create_actor(dict(id='chr.test', role=role, scale=scale), 10)
            actual.append(verify.rest_height(root))
        assert abs(actual[1] * 2 - actual[0]) < 1e-5
        cases[f'legacy_scale_{role}'] = actual
    # Actual generated postures, support plane at positive and negative elevations.
    for support in (0, .82, -2):
        for preset in ('stand', 'bend_over', 'crouch', 'walk'):
            fresh()
            box('floor', (0, 0, support - .05), (8, 8, .1))
            action = dict(preset=preset, start=0, duration=1)
            if preset == 'walk':
                action['to'] = [1, 0, support]
            actor = dict(id='chr.test', height_cm=170, position=[0, 0, support], actions=[action, dict(preset='idle', start=1, duration=1)])
            runner.create_actor(actor, 10)
            samples = [checks(actor, frame) for frame in (1, 6, 10, 11, 16, 21)]
            assert all(item['ok'] for sample in samples for item in sample), (preset, support, samples)
            cases[f'coarse_support_{support}_{preset}'] = samples
    # chair_id placement, actual coarse thighs and rotated-chair armrest checks.
    for angle in (0, 65):
        for arms in (None, .5, .17):
            fresh()
            chair((2, 1, 0), arms=arms, angle=angle)
            box('floor', (2, 1, -.05), (8, 8, .1))
            actor = dict(id='chr.test', height_cm=170, actions=[dict(preset='sit', start=0, duration=1, chair_id='chair')])
            root = runner.create_actor(actor, 10)
            result = checks(actor)
            assert abs(root.location.x - 2) < 1e-5 and abs(root.location.y - 1) < 1e-5
            assert one(result, 'seat:')['ok'], result
            assert one(result, 'armrest:')['ok'] == (arms != .17), result
            cases[f'coarse_chair_{angle}_{arms}'] = result
    # Persistent sit through idle/turn; stand explicitly exits. Include a later
    # stand to catch backwards interpolation corrupting the earlier seated hold.
    fresh()
    chair()
    actor = dict(id='chr.test', height_cm=170, actions=[
        dict(preset='sit', start=0, duration=1, chair_id='chair'),
        dict(preset='idle', start=1, duration=1),
        dict(preset='turn', start=2, duration=1, degrees=30),
        dict(preset='stand', start=3, duration=1)])
    root = runner.create_actor(actor, 10)
    samples = []
    for frame in (1, 6, 10, 11, 16, 20, 21, 26, 30, 31, 36, 41):
        result = checks(actor, frame)
        seats = [item for item in result if item['id'].startswith('seat:')]
        assert bool(seats) == (frame < 31), (frame, result)
        assert all(item['ok'] for item in result), (frame, result)
        samples.append({'frame': frame, 'checks': result})
    cases['sit_idle_turn_stand_actual_runner'] = samples
    # Adapted original real-mesh counterexamples, now assertions of correct behavior.
    for name, low, ground_feet, expected in (
            ('touch', .455, False, True), ('penetration', .35, False, False),
            ('floating', 1.0, False, False), ('ground_feet_bounded', .35, True, True),
            ('ground_feet_too_deep', .10, True, False), ('ground_feet_floating', 1.0, True, False)):
        fresh()
        chair()
        root = empty('chr.test')
        box('chr.test.hip', (0, 0, low + .1), (.2, .2, .2), root)
        actor = dict(id='chr.test', actions=[dict(preset='sit', start=0, duration=1, chair_id='chair', ground_feet=ground_feet)])
        result = checks(actor)
        assert one(result, 'seat:')['ok'] == expected, result
        cases[f'contact_{name}'] = result
    fresh()
    chair()
    root = empty('chr.test')
    box('chr.test.hip', (0, 0, .555), (.2, .2, .2), root)
    box('chr.test.feet', (0, 0, .055), (.15, .15, .1), root)
    actor = dict(id='chr.test', actions=[dict(preset='sit', start=0, duration=1, chair_id='chair')])
    result = checks(actor)
    assert one(result, 'seat:')['ok'], result
    cases['feet_do_not_replace_butt_contact'] = result
    fresh()
    seat = empty('chair')
    box('chair.seat.left', (-.4, 0, .4), (.2, .5, .1), seat)
    box('chair.seat.right', (.4, 0, .4), (.2, .5, .1), seat)
    root = empty('chr.test')
    box('chr.test.hip', (0, 0, .555), (.2, .2, .2), root)
    result = checks(actor)
    assert not one(result, 'seat:')['ok'], result
    assert one(result, 'seat:')['support_coverage'] == 0, result
    cases['seat_bbox_hole_rejected'] = result
    # Actual floor ray determines signed gap; translations do not change result.
    for support in (-2, 0, .9):
        for gap in (.005, -.10, .25):
            fresh()
            box('table' if support > 0 else 'floor', (0, 0, support - .05), (2, 2, .1))
            root = empty('chr.test', (0, 0, support + gap))
            box('chr.test.body', (0, 0, .5), (.2, .2, 1), root)
            actor = dict(id='chr.test', position=[0, 0, support], actions=[dict(preset='stand', start=0, duration=1)])
            result = checks(actor)
            floor = one(result, 'floor:')
            assert floor['support_source'] == 'mesh', result
            assert abs(floor['signed_gap_m'] - gap) < 1e-5, result
            assert floor['ok'] == (gap == .005), result
            cases[f'actual_support_{support}_{gap}'] = result
    # A real exported/imported GLB runs the same completed-geometry normalization.
    fresh()
    body = box('ImportedBody', (0, 0, 2), (.4, .4, 4))
    body.data.materials.append(runner.material('body', (.3, .3, .3, 1)))
    source = output / 'synthetic-human.glb'
    bpy.ops.export_scene.gltf(filepath=str(source), export_format='GLB')
    for height in (.15, 1.5, 1.9):
        for support in (-2, .82):
            fresh()
            actor = dict(id='chr.external', height_cm=height * 100, position=[0, 0, support], asset_path=str(source))
            root = runner.create_actor(actor, 10)
            measured, low = verify.rest_height(root), contacts.body_lowest_z(root)
            assert abs(measured - height) < 1e-5 and abs(low - support - .005) < 1e-5
            cases[f'imported_glb_{height}_{support}'] = {'height': measured, 'low': low}
    rig_source = output / 'synthetic-weighted-human.glb'
    export_synthetic_rig(rig_source)
    for support in (0, .82, -2):
        for preset in ('stand', 'bend_over', 'crouch', 'walk'):
            fresh()
            box('floor', (0, 0, support - .05), (8, 8, .1))
            action = dict(preset=preset, start=0, duration=1)
            if preset == 'walk':
                action['to'] = [1, 0, support]
            actor = dict(id='chr.external', height_cm=170, position=[0, 0, support], asset_path=str(rig_source),
                         actions=[action, dict(preset='idle', start=1, duration=1)])
            runner.create_actor(actor, 10)
            samples = [checks(actor, frame) for frame in (1, 6, 10, 11, 16, 21)]
            assert all(item['ok'] for sample in samples for item in sample), (support, preset, samples)
            cases[f'weighted_rig_support_{support}_{preset}'] = samples
    for following in ('idle', 'turn', 'stand', 'stand_up', 'walk'):
        fresh()
        chair()
        box('floor', (0, 0, -.05), (8, 8, .1))
        actor = dict(id='chr.external', height_cm=170, asset_path=str(rig_source), actions=[
            dict(preset='sit', start=0, duration=1, chair_id='chair'),
            dict(preset=following, start=1, duration=1, degrees=20, to=[1, 0, 0])])
        runner.create_actor(actor, 10)
        samples = [checks(actor, frame) for frame in (1, 6, 10, 11, 16, 21)]
        assert all(item['ok'] for sample in samples for item in sample), (following, samples)
        assert bool([c for c in samples[-1] if c['id'].startswith('seat:')]) == (following in {'idle', 'turn'})
        cases[f'weighted_rig_sit_then_{following}'] = samples
    # Independent review counterexample: all floor/ceiling slabs legally share
    # ONE mesh. Traverse every actual layer before choosing nearest support_z.
    for support in (-2, 0, 1.5):
        for gap in (.005, -.10, .25):
            fresh()
            slabs = [box(f'level.{height}', (0, 0, height - .05), (6, 6, .1))
                     for height in (-2, 0, 1.5, 3.1)]
            shell = joined_mesh('room.shell', slabs)
            root = empty('chr.test', (0, 0, support + gap))
            box('chr.test.body', (0, 0, .5), (.2, .2, 1), root)
            actor = dict(id='chr.test', position=[0, 0, support],
                         actions=[dict(preset='stand', start=0, duration=1)])
            result = checks(actor)
            floor = one(result, 'floor:')
            assert floor['support_source'] == 'mesh', result
            assert abs(floor['signed_gap_m'] - gap) < 1e-5, result
            assert floor['ok'] == (gap == .005), result
            cases[f'joined_room_layers_{support}_{gap}'] = result
    # One seat mesh with an upper cushion and lower slab; a hole through the
    # upper cushion must not be filled by either its AABB or a distant layer.
    for hole in (False, True):
        for lower_slab in (False, True):
            fresh()
            seat_root = empty('chair')
            if hole:
                slabs = [box('left', (-.4, 0, .4), (.2, .5, .1)),
                         box('right', (.4, 0, .4), (.2, .5, .1))]
            else:
                slabs = [box('upper', (0, 0, .4), (1, .5, .1))]
            if lower_slab:
                slabs.append(box('lower', (0, 0, .1), (1, .5, .1)))
            seat_mesh = joined_mesh('chair.seat', slabs)
            # Joining has kept the first object's world matrix. Preserve that
            # transform on parenting rather than moving the test surface.
            matrix = seat_mesh.matrix_world.copy()
            seat_mesh.parent = seat_root
            seat_mesh.matrix_world = matrix
            root = empty('chr.test')
            box('chr.test.hip', (0, 0, .555), (.2, .2, .2), root)
            actor = dict(id='chr.test', actions=[dict(preset='sit', start=0, duration=1, chair_id='chair')])
            result = checks(actor)
            assert one(result, 'seat:')['ok'] is (not hole), result
            if hole and lower_slab:
                assert one(result, 'seat:')['signed_gap_m'] > .25, result
            if hole and not lower_slab:
                assert one(result, 'seat:')['support_coverage'] == 0, result
            cases[f'joined_layered_seat_hole_{hole}_lower_{lower_slab}'] = result
    report['ok'] = True
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'ok': True, 'cases': len(cases), 'report': str(output / 'result.json')}))


if __name__ == '__main__':
    main()
