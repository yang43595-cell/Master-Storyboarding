"""Bounded real-Blender check: imported head attention, pose holds, clear errors.

blender --background --factory-startup --disable-autoexec --python-exit-code 1
--python scripts/tests/blender_actor_state_check.py -- --source-scene INPUT.blend
--actor-id chr.princess.mini --output-dir NEW_DIR [--baseline-runner OLD_RUNNER]

Read-only source; output is numeric evidence and before/after scene copies.
This does not check eye gaze, fine hand animation, contact physics, or shot quality.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'blender_previs'))
import runner


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(matrix):
    return [list(row) for row in matrix]


def delta(first, second):
    return max(abs(a - b) for row_a, row_b in zip(first, second) for a, b in zip(row_a, row_b))


def fixture(source, actor_id):
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False)
    bpy.context.scene.frame_set(1)
    root, rig = bpy.data.objects[actor_id], bpy.data.objects[f'{actor_id}.Rig']
    objects = [root, *root.children_recursive]
    transforms = {obj: obj.matrix_basis.copy() for obj in objects}
    poses = {bone.name: bone.matrix_basis.copy() for bone in rig.pose.bones}
    for obj in objects:
        obj.animation_data_clear()
        obj.matrix_basis = transforms[obj]
    for name, matrix in poses.items():
        rig.pose.bones[name].matrix_basis = matrix
    bpy.context.view_layer.update()
    actor = {'id': actor_id, 'position': list(root.location), 'actions': []}
    return root, rig, actor


def head_vectors(rig):
    head = rig.pose.bones['head']
    local_front = head.bone.matrix_local.to_3x3().inverted() @ Vector((0, -1, 0))
    world = rig.matrix_world @ head.matrix
    return world.translation, (world.to_3x3() @ local_front).normalized()


def target_at(location):
    target = bpy.data.objects.new('TEST.Attention.Bowl', None)
    bpy.context.scene.collection.objects.link(target)
    target.location = location
    bpy.context.view_layer.update()
    return target


def snapshot(root, rig, target, frame):
    bpy.context.scene.frame_set(frame)
    bpy.context.view_layer.update()
    origin, gaze = head_vectors(rig)
    direction = (target.matrix_world.translation - origin).normalized()
    return {'frame': frame, 'root': rows(root.matrix_world), 'head_origin': list(origin),
            'head_forward': list(gaze), 'target_direction': list(direction),
            'head_alignment': max(-1.0, min(1.0, gaze.dot(direction))),
            'bones': {bone.name: {'world': rows(rig.matrix_world @ bone.matrix),
                                   'basis': rows(bone.matrix_basis)} for bone in rig.pose.bones},
            'body_lowest_z': runner.actor_body_lowest_z(rig)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-scene', required=True)
    parser.add_argument('--actor-id', default='chr.princess.mini')
    parser.add_argument('--baseline-runner')
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    source, output = Path(args.source_scene).resolve(), Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source_hash = digest(source)
    root, rig, actor = fixture(source, args.actor_id)
    origin, front = head_vectors(rig)
    length = (rig.matrix_world.to_3x3() @ (rig.pose.bones['head'].tail - rig.pose.bones['head'].head)).length
    target_position = origin + length * (10 * front + Vector((0, 0, 4)))
    actions = [{'preset': 'look_at', 'start': 0, 'duration': 1, 'target': 'TEST.Attention.Bowl'},
               {'preset': 'idle', 'start': 1, 'duration': 1}]
    implementations = [('after', runner)]
    if args.baseline_runner:
        spec = importlib.util.spec_from_file_location('baseline_runner', args.baseline_runner)
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        implementations.insert(0, ('before', baseline))
    report = {'source': str(source), 'source_sha256': source_hash,
              'blender_version': bpy.app.version_string, 'actions': actions,
              'target_position': list(target_position),
              'scope': 'Sampled coarse head direction and inherited body/root/feet/hands; no eye animation, hand physics, or shot approval.',
              'cases': {}, 'rejections': {}}
    for label, implementation in implementations:
        root, rig, actor = fixture(source, args.actor_id)
        target = target_at(target_position)
        initial = snapshot(root, rig, target, 1)
        actor['actions'] = actions
        implementation.animate_external_actor(root, actor, 10, [rig])
        samples = {frame: snapshot(root, rig, target, frame) for frame in (1, 6, 11, 16, 21)}
        report['cases'][label] = {'initial': initial, 'samples': samples}
        bpy.context.scene.frame_set(21)
        bpy.ops.wm.save_as_mainfile(filepath=str(output / f'{label}.blend'))
    initial, samples = report['cases']['after']['initial'], report['cases']['after']['samples']
    body = [name for name in initial['bones'] if name != 'head'
            and not any(b.name == 'head' for b in rig.pose.bones[name].parent_recursive)]
    preservation = max(delta(initial['bones'][name]['world'], sample['bones'][name]['world'])
                       for sample in samples.values() for name in body)
    root_delta = max(delta(initial['root'], sample['root']) for sample in samples.values())
    support_delta = max(abs(initial['body_lowest_z'] - sample['body_lowest_z']) for sample in samples.values())
    alignment = min(samples[frame]['head_alignment'] for frame in (11, 16, 21))
    assert preservation < 2e-5, f'look_at/idle changed body/feet/hands: {preservation}'
    assert root_delta < 2e-5, f'look_at/idle moved root: {root_delta}'
    assert support_delta < 2e-5, f'look_at/idle changed support: {support_delta}'
    assert alignment > .9999, f'head does not face target: {alignment}'
    root, rig, actor = fixture(source, args.actor_id)
    target = target_at(target_position)
    initial = snapshot(root, rig, target, 1)
    runner.animate_external_actor(root, actor, 10, [rig])
    assert initial == snapshot(root, rig, target, 1)
    assert root.animation_data is None and rig.animation_data is None
    actor['actions'] = [{'preset': 'turn', 'start': 0, 'duration': 1, 'degrees': 20}]
    runner.animate_external_actor(root, actor, 10, [rig])
    turned = snapshot(root, rig, target, 11)
    turn_delta = max(delta(initial['bones'][name]['basis'], turned['bones'][name]['basis']) for name in initial['bones'])
    assert turn_delta < 2e-5, f'turn reset inherited pose: {turn_delta}'
    # Standing/crouching/bending on a declared tabletop must not be snapped
    # down to world-floor Z. Measure evaluated body geometry, as in the runner.
    report['support_cases'] = {}
    support_errors = []
    for label, implementation in implementations:
        cases = []
        for preset in ('stand', 'crouch', 'bend_over'):
            for height in (0.0, 0.82):
                root, rig, actor = fixture(source, args.actor_id)
                actor['position'][2] = height
                actor['actions'] = [{'preset': preset, 'start': 0, 'duration': 1},
                                    {'preset': 'idle', 'start': 1, 'duration': 1}]
                implementation.animate_external_actor(root, actor, 10, [rig])
                measured = []
                for frame in (1, 6, 11, 16, 21):
                    bpy.context.scene.frame_set(frame)
                    bpy.context.view_layer.update()
                    foot_z = runner.actor_body_lowest_z(rig)
                    measured.append({'frame': frame, 'body_lowest_z': foot_z,
                                     'root_z': root.location.z})
                    if label == 'after':
                        support_errors.append(abs(foot_z - (height + .005)))
                cases.append({'preset': preset, 'declared_support_z': height, 'samples': measured})
        report['support_cases'][label] = cases
    assert max(support_errors) < 2e-5, f'declared support lost: {max(support_errors)}'
    invalid = {'missing_target': ('look_at', 'MISSING'), 'empty_target': ('look_at', ''),
               'self_target': ('look_at', args.actor_id), 'too_close': ('look_at', 'close'),
               'behind': ('look_at', 'behind'), 'missing_head': ('look_at', 'valid'),
               'hold_prop': ('hold_prop', 'valid'), 'unknown': ('wave_magic', 'valid')}
    for label, (preset, kind) in invalid.items():
        root, rig, actor = fixture(source, args.actor_id)
        target = target_at(target_position)
        head = rig.pose.bones['head']
        origin, front = head_vectors(rig)
        if kind == 'close':
            target.location = origin
        elif kind == 'behind':
            local = head.bone.matrix_local.to_3x3().inverted() @ Vector((0, -1, 0))
            neutral = head.parent.matrix @ head.parent.bone.matrix_local.inverted() @ head.bone.matrix_local
            target.location = origin - (rig.matrix_world.to_3x3() @ neutral.to_3x3() @ local).normalized()
        if label == 'missing_head':
            head.name = 'UnsupportedHead'
        name = kind if kind in ('MISSING', '', args.actor_id) else target.name
        actor['actions'] = [{'preset': preset, 'start': 0, 'duration': 1, 'target': name}]
        try:
            runner.animate_external_actor(root, actor, 10, [rig])
        except RuntimeError as exc:
            report['rejections'][label] = str(exc)
        else:
            raise AssertionError(f'{label} silently accepted')
    for preset in ('look_at', 'hold_prop', 'run', 'lie_back'):
        try:
            runner.create_actor({'id': f'TEST.Proxy.{preset}', 'actions': [
                {'preset': preset, 'start': 0, 'duration': 1, 'target': target.name}]}, 10)
        except RuntimeError as exc:
            report['rejections'][f'proxy_{preset}'] = str(exc)
        else:
            raise AssertionError(f'proxy {preset} silently accepted')
    assert digest(source) == source_hash, 'modified source scene'
    summary = {'ok': True, 'head_alignment_min': alignment,
               'head_error_degrees_max': math.degrees(math.acos(min(1, alignment))),
               'root_matrix_delta': root_delta, 'body_feet_hand_matrix_delta': preservation,
               'support_z_delta': support_delta, 'turn_inherited_pose_delta': turn_delta,
               'declared_support_error_max': max(support_errors),
               'empty_actions_unchanged': True, 'rejections': len(report['rejections']), 'source_unchanged': True}
    if 'before' in report['cases']:
        old = report['cases']['before']['samples'][21]['head_alignment']
        summary['before_head_alignment_end'] = old
        summary['before_head_error_degrees_end'] = math.degrees(math.acos(max(-1, min(1, old))))
    report['summary'] = summary
    (output / 'actor_state_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
