#!/usr/bin/env python3
"""Real offline CLI replay using isolated metadata and reused native test bytes.
No renderer, remote CLI, production project, or real user approval is invoked.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

arg = argparse.ArgumentParser()
arg.add_argument('--repo', type=Path, required=True)
arg.add_argument('--output', type=Path, required=True)
arg.add_argument('--native-fixture', type=Path, required=True)
arg.add_argument('--expect-repaired', action='store_true')
a = arg.parse_args()
repo, out, native = a.repo.resolve(), a.output.resolve(), a.native_fixture.resolve()
if out.exists():
    raise SystemExit('Use a new evidence directory; previous evidence is preserved')
out.mkdir(parents=True)
sys.path[:0] = [str(repo), str(repo / 'scripts')]
from scripts.core import artifacts
from scripts.core.io import write_json, read_json
from scripts.core.project_state import new_project_state
from scripts.core.asset_refs import require_images, require_references

results = []
env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(repo)}
def cli(name, root, *args):
    command = [sys.executable, '-B', '-m', 'scripts.blender_previs_cli', *args, '--project', str(root)]
    run = subprocess.run(command, cwd=repo, env=env, text=True, capture_output=True)
    (out / (name + '.stdout')).write_text(run.stdout)
    (out / (name + '.stderr')).write_text(run.stderr)
    results.append(dict(name=name, command=command, exit=run.returncode, stderr=run.stderr))
    write_json(out / 'commands.json', results)
    return run

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def fixture(name, actor):
    root = out / name
    root.mkdir()
    (root / 'TEST_FIXTURE_NOT_PRODUCTION.txt').write_text('隔离离线CLI测试；导演、叙事、母图和白模批准全部是显式虚拟夹具，不是用户生产批准。复用既有原生Blend/PNG字节；不渲染，不声称图像或视频内容实测通过。')
    text = root / '03_导演/TEST.md'
    text.parent.mkdir()
    text.write_text('TEST ONLY: ' + ('人物粗路径测试' if actor else '无人物空房间空间测试'))
    ids = ['AST-TEST-ROOM'] + (['AST-TEST-A'] if actor else [])
    shot = dict(shot_id='TEST-A', source_asset_requirement_ids=ids, take=dict(start='测试起幅', process='测试过程', end='测试落幅'), camera=dict(framing='测试构图', movement='固定', focus='几何合同'), duration_seconds=1)
    pkg = text.with_suffix('.json')
    write_json(pkg, dict(schema_version='4.0', status='confirmed', revision='TEST-R01', human_readable_path=text.relative_to(root).as_posix(), scenes=[dict(scene_id='TEST', location='隔离测试', shots=[shot])]))
    narr = root / '02_编导/TEST.json'
    write_json(narr, dict(schema_version='4.0', status='confirmed', revision='TEST-R01', asset_requirements=[dict(asset_id=i, name=i, category='SCENE' if i.endswith('ROOM') else 'CHARACTER', image_decision='需要制作') for i in ids]))
    state = new_project_state(current_stage='blender-previs')
    state['custom_user_field'] = {'preserve': True}
    state['current_artifacts'] = ['TEST-NARRATIVE', 'TEST-PLAN', 'TEST-PACKAGE']
    state['user_confirmations'] = [dict(scope='artifact', artifact_ids=list(state['current_artifacts']), approval_ref='SYNTHETIC-FIXTURE-NOT-USER-APPROVAL')]
    write_json(root / '00_项目管理/project_state.json', state)
    for ident, logical, path in [('TEST-NARRATIVE','narrative.package',narr), ('TEST-PLAN','storyboard.plan',text), ('TEST-PACKAGE','storyboard.package',pkg)]:
        artifacts.register_workspace_artifact(root, dict(artifact_id=ident, logical_id=logical, type=logical, path_or_url=path.relative_to(root).as_posix(), status='current', revision='TEST-R01', user_approved=True, checksum=sha(path)))
    write_json(root / '00_项目管理/project_state.json', state)
    image = root / '04_资产/母版/TEST/reused-diagnostic.png'
    image.parent.mkdir(parents=True)
    shutil.copy2(native / 'input/reused-diagnostic.png', image)
    receipt = image.parent / 'synthetic-confirmation.json'
    write_json(receipt, dict(frame_path=image.relative_to(root).as_posix(), image_sha256=sha(image), scope='TEST ONLY', approval_ref='SYNTHETIC-FIXTURE-NOT-USER-APPROVAL'))
    write_json(image.parent/'visual-asset-manifest.json', dict(schema_version='2.0', assets=[dict(asset_id=i, status='confirmed', confirmed_outputs=[dict(output_id='test-master', path=image.relative_to(root).as_posix(), user_confirmation_ref=receipt.relative_to(root).as_posix())]) for i in ids]))
    refs = [i+'@test-master' for i in ids]
    assert require_images(root, refs) == (True, [])
    r = cli(name+'_scene', root, 'register-scene', '--scene-id', 'AST-TEST-ROOM', '--revision', 'R01', '--scene', str(native / 'input/restaurant-base.blend'), '--evidence', str(native / 'evidence/import_geometry.json'), '--reference', refs[0])
    assert r.returncode == 0, r.stderr
    r = cli(name+'_prepare', root, 'prepare-shot', '--shot-id', 'TEST-A', '--revision', 'R01', '--scene-artifact', 'ART-PREVIS-SCENE-AST-TEST-ROOM-R01')
    assert r.returncode == 0, r.stderr
    binding = root / '05_分镜/预演/输入/TEST-A_R01/shot-input.json'
    # A clearly synthetic successful check tests metadata gates only. It cannot
    # be mistaken for a new Blender verification or any visual approval.
    evidence = root / 'TEST-ONLY-checks.json'
    write_json(evidence, dict(ok=True, scope='SYNTHETIC CONTRACT TEST ONLY; NO RENDER/VERIFY EXECUTED'))
    from PIL import Image
    with Image.open(image) as im:
        resolution = list(im.size)
    spec = dict(appearance_refs=refs, actors=[dict(proxy_id='chr.test', asset_id='AST-TEST-A', display_color='#123456', state='测试站立', scale_basis='测试值，不是导演尺度')] if actor else [], camera=dict(director_contract=shot['camera'], resolution=resolution), target_state='测试起幅', coverage='TEST ONLY: 起落幅和俯视文件交接合同，不证明运动或视觉效果', limitations=['所有批准均为虚拟夹具；PNG复用同一诊断字节，不证明各画面内容'], render_backend='local Blender CONTRACT FIXTURE ONLY, NOT EXECUTED', technical_evidence=str(evidence), files=[dict(role=r, path=str(image)) for r in ('main_image','end_image','topdown')])
    spec_path = root/'TEST-ONLY-spec.json'
    write_json(spec_path, spec)
    return root, binding, spec_path

summary = dict(repo=str(repo), reused_native_fixture=str(native), renderer_executed=False, real_user_approval=False, cases={})
for name, actor in [('empty_actors', False), ('media_handoff', True)]:
    root, binding, spec = fixture(name, actor)
    r = cli(name+'_register', root, 'register-reference', '--input', str(binding), '--spec', str(spec))
    result = summary['cases'][name] = dict(register_exit=r.returncode)
    if a.expect_repaired:
        assert r.returncode == 0, r.stderr
    if r.returncode:
        result['error'] = r.stderr
        continue
    ident='ART-PREVIS-TEST-A-R01'
    r = cli(name+'_unconfirmed', root, 'handoff-reference', '--artifact-id', ident, '--output-dir', '05_分镜/预演/交接/unconfirmed')
    assert r.returncode != 0
    result['candidate_blocked'] = True
    r = cli(name+'_confirm', root, 'confirm-reference', '--artifact-id', ident, '--approval-ref', 'SYNTHETIC-FIXTURE-NOT-USER-APPROVAL', '--user-quote', '仅隔离测试虚拟批准；不是生产确认')
    assert r.returncode == 0, r.stderr
    r = cli(name+'_handoff', root, 'handoff-reference', '--artifact-id', ident, '--output-dir', '05_分镜/预演/交接/default')
    assert r.returncode == 0, r.stderr
    bundle = read_json(root/'05_分镜/预演/交接/default/reference-handoff.json')
    result.update(registered_media=['main_image','end_image','topdown'], handed_off_media=[f['source']['role'] for f in bundle['references'] if 'role' in f['source']], consumer=require_references(root, [ident], ['TEST-A']), generation_authorized=bundle['generation_authorized'])
    assert require_references(root, [ident], ['TEST-A']) == (True, [])
    assert not require_references(root, [ident], ['OTHER-SHOT'])[0]
    assert not bundle['generation_authorized'] and bundle['image_status']=='not_generated'
    assert read_json(root/'00_项目管理/project_state.json')['custom_user_field']=={'preserve':True}
    if a.expect_repaired:
        assert result['handed_off_media'] == result['registered_media']
        assert cli(name+'_static_subset', root, 'handoff-reference', '--artifact-id', ident, '--output-dir', '05_分镜/预演/交接/static', '--media-role', 'main_image').returncode == 0
        subset = read_json(root/'05_分镜/预演/交接/static/reference-handoff.json')
        assert [f['source']['role'] for f in subset['references'] if 'role' in f['source']] == ['main_image']
        assert '仅所选媒体' in subset['coverage']
        assert cli(name+'_invalid_role', root, 'handoff-reference', '--artifact-id', ident, '--output-dir', '05_分镜/预演/交接/bad-role', '--media-role', 'technical_evidence').returncode != 0
        assert not (root/'05_分镜/预演/交接/bad-role').exists()
        result['explicit_subset_and_invalid_role_checked'] = True
        media = next(f for f in bundle['references'] if f['source'].get('role') == 'end_image')['source']
        source = root/media['path']; saved = source.read_bytes(); source.write_bytes(saved+b'TAMPER')
        assert not require_references(root, [ident], ['TEST-A'])[0]
        assert cli(name+'_tampered_subset', root, 'handoff-reference', '--artifact-id', ident, '--output-dir', '05_分镜/预演/交接/tampered', '--media-role', 'main_image').returncode != 0
        source.write_bytes(saved)
        result['subset_cannot_bypass_source_hash'] = True
    before = {p:p.read_bytes() for p in (root/'05_分镜/预演/镜头/TEST-A_R01').iterdir() if p.is_file()}
    r = cli(name+'_prepare_rework', root, 'prepare-shot', '--shot-id', 'TEST-A', '--revision', 'R02', '--scene-artifact', 'ART-PREVIS-SCENE-AST-TEST-ROOM-R01')
    assert r.returncode==0
    revised=root/'05_分镜/预演/输入/TEST-A_R02/shot-input.json'
    assert cli(name+'_register_rework', root, 'register-reference', '--input', str(revised), '--spec', str(spec)).returncode==0
    assert require_references(root, [ident], ['TEST-A']) == (True, [])
    assert cli(name+'_confirm_rework', root, 'confirm-reference', '--artifact-id', 'ART-PREVIS-TEST-A-R02', '--approval-ref', 'SYNTHETIC-REWORK-TEST', '--user-quote', '仅隔离测试虚拟新版批准').returncode==0
    assert not require_references(root, [ident], ['TEST-A'])[0]
    assert require_references(root, ['ART-PREVIS-TEST-A-R02'], ['TEST-A']) == (True, [])
    assert all(p.read_bytes()==data for p,data in before.items())
    result['rework_old_bytes_preserved']=True
    result['old_consumer_blocked_after_new_confirmation']=True
if a.expect_repaired:
    root, binding, spec_path = fixture('omitted_actor_negative', True)
    spec = read_json(spec_path); spec['actors'] = []; write_json(spec_path, spec)
    run = cli('omitted_actor_negative_register', root, 'register-reference', '--input', str(binding), '--spec', str(spec_path))
    assert run.returncode == 0
    ident = 'ART-PREVIS-TEST-A-R01'
    assert not require_references(root, [ident], ['TEST-A'])[0]
    summary['cases']['omitted_actor_negative'] = dict(register_exit=run.returncode, automatic_presence_inference=False, semantic_result='执行者须识别正文有人而映射为空；技术结构不判在场人物', deliberately_not_confirmed=True, downstream_blocked_as_candidate=True)
write_json(out/'summary.json', summary)
print(json.dumps(summary, ensure_ascii=False, indent=2))
