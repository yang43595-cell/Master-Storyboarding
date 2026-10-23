"""Isolated native geometry only; no rendering, MPFB, source scene or production."""
import argparse
import json
import sys
from pathlib import Path
import bpy
sys.dont_write_bytecode = True
p = argparse.ArgumentParser()
p.add_argument('--repo', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args(sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(a.repo.resolve() / 'scripts/blender_previs'))
import runner
import contacts
import verify
cases = []
for kind, width in [('chair', .5), ('armchair', 1.1), ('armchair', .4)]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = 10
    item = dict(id='TEST-CHAIR', kind=kind, position=[0,0,0], size=[width,1.0,1.05])
    build = runner.build_simple_chair_item if kind == 'chair' else runner.build_armchair_item
    build(item, None)
    actor = dict(id='chr.test', height_cm=170, actions=[dict(preset='sit', start=0, duration=1, chair_id=item['id'])])
    root = runner.create_actor(actor, 10)
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    checks = []
    verify.check_actor(actor, checks)
    clear = contacts.chair_clear_seat_width(item['id'])
    hit = contacts.thighs_hit_armrests(root, item['id'])
    assert hit == (kind == 'armchair' and width == .4), (kind, width, checks)
    cases.append(dict(kind=kind, width_m=width, clear_width_m=clear, thigh_hits_armrests=hit, warning=contacts.narrow_seat_warning(item['id']), checks=checks))
report = dict(blender_version=bpy.app.version_string, rendered=False, production_used=False, mpfb=False, cases=cases)
a.output.parent.mkdir(parents=True, exist_ok=True)
if a.output.exists():
    raise RuntimeError('Keep previous evidence; select a new output')
a.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False))
