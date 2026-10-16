"""Project boundaries and adoption tests; these do not claim Blender renders."""
from pathlib import Path
import pytest
from PIL import Image

from scripts.blender_previs import project as p
from scripts import blender_previs_cli as cli
from scripts.core import artifacts
from scripts.core.io import read_json, write_json
from scripts.core.project_state import new_project_state


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / '项目'
    root.mkdir()
    text = root / '03_导演/R03.md'
    text.parent.mkdir()
    text.write_text('用户确认的导演正文', encoding='utf-8')
    shot = dict(shot_id='S03-150', source_asset_requirement_ids=['AST-A', 'AST-ROOM'],
                take=dict(start='坐着看精灵', process='精灵回答', end='转头看房间'),
                camera=dict(framing='双人同框', movement='未另定', focus='两张脸'))
    package_path = text.parent / 'R03.json'
    write_json(package_path, dict(schema_version='4.0', status='confirmed', revision='R03',
        human_readable_path='03_导演/R03.md', scenes=[dict(scene_id='S03',
        location='房间', shots=[shot, dict(shot, shot_id='S03-160')])]))
    state = new_project_state(current_stage='storyboard-director')
    state['custom_user_field'] = {'preserve': True}
    state['current_artifacts'] = ['PLAN', 'PACKAGE']
    state['user_confirmations'] = [dict(scope='artifact', artifact_ids=['PLAN', 'PACKAGE'], approval_ref='actual user')]
    write_json(root / '00_项目管理/project_state.json', state)
    for ident, logical, path in (('PLAN', 'storyboard.plan', text), ('PACKAGE', 'storyboard.package', package_path)):
        artifacts.register_workspace_artifact(root, dict(artifact_id=ident, logical_id=logical, type=logical,
            path_or_url=path.relative_to(root).as_posix(), status='current', revision='R03', user_approved=True))
    # Manifest saves reconcile selected IDs; select the completed pair afterwards.
    write_json(root / '00_项目管理/project_state.json', state)
    image = root / '04_资产/a.png'
    image.parent.mkdir()
    Image.new('RGB', (16, 32), 'blue').save(image)
    def scope(_root, requests):
        return [dict(outputs=[dict(path=str(image), confirmation_ref='actual-reference')]) for _ in requests], []
    monkeypatch.setattr(p, 'require_images', lambda *args: (True, []))
    monkeypatch.setattr(p, 'scope_assets', scope)
    scene = tmp_path / 'base.blend'
    scene.write_bytes(b'TEST_SCENE_NOT_RENDERED')
    evidence = tmp_path / 'checks.json'
    write_json(evidence, {'ok': True})
    base = p.register_scene(root, 'AST-ROOM', 'R01', scene, evidence, ['AST-ROOM@room'])
    binding = p.prepare_shot(root, 'S03-150', 'R02', base['artifact_id'])
    spec_path = tmp_path / 'spec.json'
    spec = dict(appearance_refs=['AST-A@face', 'AST-ROOM@room'],
        actors=[dict(proxy_id='chr.a', asset_id='AST-A', display_color='#123456',
                     state='坐', scale_basis='试验参数，未确认')],
        camera=dict(director_contract=shot['camera'], resolution=[16, 32], lens_mm=32),
        target_state='起幅', coverage='只覆盖起幅，不证明整个镜头运动', limitations=['无精细动作'],
        render_backend='local Blender test fixture, not a render',
        technical_evidence=str(evidence), files=[dict(role='main_image', path=str(image))])
    write_json(spec_path, spec)
    return root, binding, spec_path, base, package_path


def adopt(root, binding, spec):
    entry = p.register_reference(root, binding, spec)
    p.confirm_reference(root, entry['artifact_id'], 'message-id', '通过该白模')
    return entry


def test_full_adoption_handoff_preserves_other_project_facts(project):
    root, binding, spec, base, _ = project
    entry = adopt(root, binding, spec)
    output = root / '05_分镜/预演/交接/S03-150_R02'
    confirmed, data = p.confirmed_reference_data(root, entry['artifact_id'])
    assert confirmed['artifact_id'] == entry['artifact_id']
    assert data['shot_id'] == 'S03-150'
    bundle = read_json(p.handoff(root, entry['artifact_id'], output))
    assert bundle['scene_base']['artifact_id'] == base['artifact_id']
    assert bundle['source_shot_ids'] == ['S03-150']
    assert len(bundle['references']) == 3
    assert bundle['generation_authorized'] is False
    state = read_json(root / '00_项目管理/project_state.json')
    assert state['custom_user_field'] == {'preserve': True}
    assert state['current_stage'] == 'storyboard-director'
    assert [a['artifact_id'] for a in artifacts.load_manifest(root)['artifacts'] if a['logical_id'] == 'storyboard.package'] == ['PACKAGE']


def test_two_shots_share_identical_scene_version_without_launching_blender(project, monkeypatch):
    root, binding, _, base, _ = project
    monkeypatch.setattr(cli, 'find_blender', lambda *a: pytest.fail('No Blender needed to prepare'))
    other = p.prepare_shot(root, 'S03-160', 'R01', base['artifact_id'])
    assert read_json(binding)['scene_base'] == read_json(other)['scene_base']
    assert read_json(other)['shot']['shot_id'] == 'S03-160'


def test_candidate_cannot_handoff(project):
    root, binding, spec, _, _ = project
    entry = p.register_reference(root, binding, spec)
    with pytest.raises(p.ProjectPrevisError, match='未经用户确认'):
        p.confirmed_reference_data(root, entry['artifact_id'])
    with pytest.raises(p.ProjectPrevisError, match='未经用户确认'):
        p.handoff(root, entry['artifact_id'], root / '05_分镜/预演/交接/out')


@pytest.mark.parametrize('changed', ['selected_id', 'receipt_checksum', 'receipt_files'])
def test_read_only_confirmed_gate_rejects_invalid_adoption(project, changed):
    root, binding, spec, _, _ = project
    entry = adopt(root, binding, spec)
    if changed == 'selected_id':
        path = root / '00_项目管理/project_state.json'
        data = read_json(path)
        data['current_artifacts'].remove(entry['artifact_id'])
    else:
        path = p.project_file(root, p.entry_by_id(root, entry['artifact_id'])['confirmation_ref'])
        data = read_json(path)
        data['checksum' if changed == 'receipt_checksum' else 'files'] = 'tampered'
    write_json(path, data)
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    with pytest.raises(p.ProjectPrevisError):
        p.confirmed_reference_data(root, entry['artifact_id'])
    assert all(path.read_bytes() == value for path, value in before.items())


@pytest.mark.parametrize('changed', ['director_text', 'director_package', 'white_image', 'room', 'appearance'])
def test_changed_inputs_block_previously_confirmed_reference(project, changed):
    root, binding, spec, base, package_path = project
    entry = adopt(root, binding, spec)
    data = read_json(p.project_file(root, entry['path_or_url']))
    if changed == 'director_text':
        path = root / '03_导演/R03.md'
    elif changed == 'director_package':
        path = package_path
    elif changed == 'white_image':
        path = p.project_file(root, data['files'][0]['path'])
    elif changed == 'room':
        scene_data = read_json(p.project_file(root, base['path_or_url']))
        path = p.project_file(root, scene_data['files'][0]['path'])
    else:
        path = root / '04_资产/a.png'
    if changed == 'director_package':
        package = read_json(path); package['scenes'][0]['shots'][0]['take']['start'] = 'changed'
        write_json(path, package)
    else:
        path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(p.ProjectPrevisError):
        p.handoff(root, entry['artifact_id'], root / '05_分镜/预演/交接/out')


def test_reference_cannot_handoff_after_asset_rejected(project, monkeypatch):
    root, binding, spec, _, _ = project
    entry = adopt(root, binding, spec)
    monkeypatch.setattr(p, 'require_images', lambda *a: (False, ['user rejected output']))
    with pytest.raises(p.ProjectPrevisError, match='不可用'):
        p.handoff(root, entry['artifact_id'], root / '05_分镜/预演/交接/out')


@pytest.mark.parametrize('bad', ['failed_checks', 'camera_contract', 'duplicate_colors', 'wrong_asset', 'resolution'])
def test_registration_rejects_mismatched_spec_before_writing(project, bad):
    root, binding, spec_path, _, _ = project
    spec = read_json(spec_path)
    if bad == 'failed_checks':
        write_json(Path(spec['technical_evidence']), {'ok': False})
    elif bad == 'camera_contract':
        spec['camera']['director_contract']['movement'] = 'invented move'
    elif bad == 'duplicate_colors':
        spec['actors'].append(dict(spec['actors'][0], proxy_id='chr.b'))
    elif bad == 'wrong_asset':
        spec['actors'][0]['asset_id'] = 'AST-UNKNOWN'
    else:
        spec['camera']['resolution'] = [32, 32]
    write_json(spec_path, spec)
    with pytest.raises(p.ProjectPrevisError):
        p.register_reference(root, binding, spec_path)
    assert not (root / '05_分镜/预演/镜头/S03-150_R02').exists()


def test_no_overwrite_unknown_shot_and_project_escape(project):
    root, binding, spec, _, _ = project
    entry = adopt(root, binding, spec)
    before = p.project_file(root, entry['path_or_url']).read_bytes()
    with pytest.raises(p.ProjectPrevisError):
        p.register_reference(root, binding, spec)
    with pytest.raises(p.ProjectPrevisError, match='不可重写'):
        p.confirm_reference(root, entry['artifact_id'], 'new', 'new')
    assert p.project_file(root, entry['path_or_url']).read_bytes() == before
    with pytest.raises(p.ProjectPrevisError, match='镜号不存在'):
        p.director_input(root, 'S99-999')
    with pytest.raises(p.ProjectPrevisError, match='项目登记路径'):
        p.handoff(root, entry['artifact_id'], root.parent / 'escape')


def test_new_room_revision_invalidates_old_shot_and_keeps_one_active_base(project):
    root, binding, spec, base, _ = project
    entry = adopt(root, binding, spec)
    data = read_json(p.project_file(root, base['path_or_url']))
    new_base = p.register_scene(root, 'AST-ROOM', 'R02',
        p.project_file(root, data['files'][0]['path']), p.project_file(root, data['files'][1]['path']), ['AST-ROOM@room'])
    assert p.entry_by_id(root, base['artifact_id'])['status'] == 'superseded'
    assert p.entry_by_id(root, entry['artifact_id'])['status'] == 'stale'
    assert new_base['status'] == 'current'
    with pytest.raises(p.ProjectPrevisError, match='空间基座'):
        p.handoff(root, entry['artifact_id'], root / '05_分镜/预演/交接/out')


def test_adopting_reworked_shot_invalidates_downstream_only_after_confirmation(project):
    root, binding, spec, base, _ = project
    old = adopt(root, binding, spec)
    old_dir = p.project_file(root, old['path_or_url']).parent
    old_bytes = {path: path.read_bytes() for path in old_dir.iterdir() if path.is_file()}
    dependents = (
        ('VISUAL', 'visual.shot.S03-150', [old['logical_id']]),
        ('SEGMENT', 'video.segment.S03', ['visual.shot.S03-150']),
        ('UNRELATED', 'visual.shot.S03-160', ['storyboard.package']),
    )
    for ident, logical, dependencies in dependents:
        path = root / f'05_分镜/{ident}.txt'
        path.write_text(f'Existing confirmed {ident}', encoding='utf-8')
        artifacts.register_workspace_artifact(root, dict(
            artifact_id=ident, logical_id=logical, type='test-downstream',
            path_or_url=path.relative_to(root).as_posix(), status='current',
            revision='R01', user_approved=True, depends_on=dependencies))
        p.save_state_additions(root, ident, dict(scope='artifact', artifact_ids=[ident],
                                               approval_ref='simulated test approval'))
    before_downstream = {ident: (root / f'05_分镜/{ident}.txt').read_bytes()
                         for ident, _, _ in dependents}
    revised_input = p.prepare_shot(root, 'S03-150', 'R03', base['artifact_id'])
    revised = p.register_reference(root, revised_input, spec)
    assert p.entry_by_id(root, old['artifact_id'])['status'] == 'current'
    assert all(p.entry_by_id(root, ident)['status'] == 'current' for ident, _, _ in dependents)
    p.handoff(root, old['artifact_id'], root / '05_分镜/预演/交接/before-adoption')
    with pytest.raises(p.ProjectPrevisError, match='未经用户确认'):
        p.handoff(root, revised['artifact_id'], root / '05_分镜/预演/交接/candidate')

    p.confirm_reference(root, revised['artifact_id'], 'rework-message', '采用新版空间参考')
    assert p.entry_by_id(root, old['artifact_id'])['status'] == 'superseded'
    assert p.entry_by_id(root, revised['artifact_id'])['status'] == 'current'
    for ident in ('VISUAL', 'SEGMENT'):
        entry = p.entry_by_id(root, ident)
        assert entry['status'] == 'stale'
        assert entry['stale_reasons'][-1]['revision'] == 'R03'
    assert p.entry_by_id(root, 'UNRELATED')['status'] == 'current'
    selected = read_json(root / '00_项目管理/project_state.json')['current_artifacts']
    assert not {'VISUAL', 'SEGMENT', old['artifact_id']} & set(selected)
    assert {revised['artifact_id'], 'UNRELATED'} <= set(selected)
    with pytest.raises(p.ProjectPrevisError, match='未经用户确认'):
        p.handoff(root, old['artifact_id'], root / '05_分镜/预演/交接/old-after-adoption')
    bundle = read_json(p.handoff(root, revised['artifact_id'], root / '05_分镜/预演/交接/reworked'))
    assert bundle['generation_authorized'] is False
    assert bundle['image_status'] == 'not_generated'
    assert all(path.read_bytes() == data for path, data in old_bytes.items())
    assert all((root / f'05_分镜/{ident}.txt').read_bytes() == data
               for ident, data in before_downstream.items())


def test_disabled_mode_does_not_require_a_previs_scene_or_blender(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'find_blender', lambda *a: pytest.fail('Rendering was not selected'))
    # Existing director/reference workflow has no newly required flag or artifact.
    args = cli.parser().parse_args(['shoot', '--scene', 'base.blend', '--shots', 'shots.json', '--output-dir', 'out'])
    assert args.command == 'shoot'
    assert not hasattr(args, 'project')


def test_confirmed_handoff_respects_revision_freeze_and_cancellation(project):
    from scripts.core import revision_control
    root, binding, spec, _, _ = project
    entry = adopt(root, binding, spec)
    revision_control.open_change(root, change_id='REWORK', tier='T1',
                                logical_ids=[entry['logical_id']], reason='fixture rework')
    with pytest.raises(ValueError, match='冻结'):
        p.confirmed_reference_data(root, entry['artifact_id'])
    with pytest.raises(ValueError, match='冻结'):
        p.handoff(root, entry['artifact_id'], root / 'blocked-handoff')
    assert not (root / 'blocked-handoff').exists()
    revision_control.cancel_change(root, change_id='REWORK', reason='fixture cancelled')
    assert p.confirmed_reference_data(root, entry['artifact_id'])[0]['artifact_id'] == entry['artifact_id']


def cloud_fixture(project):
    """Synthetic byte evidence only; the real 5.2 run is recorded separately."""
    root, binding, spec_path, base, package_path = project
    spec = read_json(spec_path)
    scene = spec_path.parent / 'cloud-scene.blend'
    scene.write_bytes(b'CLOUD_FIXTURE_NOT_A_REAL_BLEND')
    manifest = spec_path.parent / 'shoot_manifest.json'
    image = Path(spec['files'][0]['path'])
    write_json(manifest, {'scene_sha256': p.sha256_file(scene), 'shots': [
        {'id': 'S03-150', 'stills': [{'sha256': p.sha256_file(image)}]}]})
    evidence = Path(spec['technical_evidence'])
    write_json(evidence, {'ok': True, 'blender_version': '5.2.2 LTS',
        'scene_sha256': p.sha256_file(scene), 'render_manifest_sha256': p.sha256_file(manifest)})
    spec.update(runtime_location='cloud', render_backend='cloud portable Blender 5.2.2 LTS test fixture')
    spec['files'] += [dict(role='shot_scene', path=str(scene)), dict(role='render_manifest', path=str(manifest))]
    write_json(spec_path, spec)
    return spec, evidence, scene, manifest


def test_explicit_cloud_candidate_persists_provenance_but_needs_confirmation(project):
    root, binding, spec_path, _, _ = project
    cloud_fixture(project)
    entry = p.register_reference(root, binding, spec_path)
    assert entry['status'] == 'candidate' and not entry.get('user_approved')
    reloaded, data = p.reference_data(root, entry['artifact_id'])
    assert data['runtime_location'] == 'cloud' and data['blender_version'] == '5.2.2 LTS'
    with pytest.raises(p.ProjectPrevisError, match='未经用户确认'):
        p.handoff(root, entry['artifact_id'], root / 'unconfirmed-handoff')
    # Only this isolated fixture supplies simulated approval to exercise metadata propagation.
    p.confirm_reference(root, entry['artifact_id'], 'SIMULATED-FIXTURE', '测试确认，不是生产确认')
    bundle = read_json(p.handoff(root, entry['artifact_id'], root / 'fixture-handoff'))
    assert bundle['runtime_location'] == 'cloud'
    assert bundle['render_backend'] == data['render_backend']
    assert bundle['blender_version'] == '5.2.2 LTS'
    assert bundle['generation_authorized'] is False


@pytest.mark.parametrize('bad', ['implicit_cloud', 'unknown_location', 'invalid_version', 'missing_version',
    'backend_version', 'scene_bytes', 'manifest_bytes', 'image_bytes', 'missing_manifest'])
def test_cloud_registration_rejects_invalid_runtime_or_bytes_before_writing(project, bad):
    root, binding, spec_path, _, _ = project
    spec, evidence_path, scene, manifest = cloud_fixture(project)
    if bad == 'implicit_cloud':
        spec.pop('runtime_location')
    elif bad == 'unknown_location':
        spec['runtime_location'] = 'guessed_remote'
    elif bad in ('invalid_version', 'missing_version'):
        evidence = read_json(evidence_path)
        evidence['blender_version'] = 'not-a-version' if bad == 'invalid_version' else None
        write_json(evidence_path, evidence)
    elif bad == 'backend_version':
        spec['render_backend'] = 'cloud Blender 5.1.0'
    elif bad == 'missing_manifest':
        spec['files'] = [f for f in spec['files'] if f['role'] != 'render_manifest']
    else:
        path = {'scene_bytes': scene, 'manifest_bytes': manifest,
                'image_bytes': Path(spec['files'][0]['path'])}[bad]
        path.write_bytes(path.read_bytes() + b'changed')
    write_json(spec_path, spec)
    with pytest.raises(p.ProjectPrevisError):
        p.register_reference(root, binding, spec_path)
    assert not (root / '05_分镜/预演/镜头/S03-150_R02').exists()


def test_cloud_reference_rechecks_copied_evidence_before_consumption(project):
    root, binding, spec_path, _, _ = project
    cloud_fixture(project)
    entry = p.register_reference(root, binding, spec_path)
    _, data = p.reference_data(root, entry['artifact_id'])
    evidence = next(f for f in data['files'] if f['role'] == 'technical_evidence')
    path = p.project_file(root, evidence['path'])
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(p.ProjectPrevisError, match='文件缺失或内容变化'):
        p.reference_data(root, entry['artifact_id'])


def test_handoff_retains_complete_shot_scene_and_predecessor(project):
    root, _, spec, base, _ = project
    later = p.prepare_shot(root, 'S03-160', 'R04', base['artifact_id'])
    source = read_json(later)
    assert source['adjacent_states']['previous_end'] == '转头看房间'
    entry = adopt(root, later, spec)  # Isolated fixture approval, not a production receipt.
    bundle = read_json(p.handoff(root, entry['artifact_id'], root / 'context-handoff'))
    assert bundle['scene'] == source['scene']
    assert bundle['adjacent_states'] == source['adjacent_states']
    assert bundle['shot']['camera'] == source['shot']['camera']


@pytest.mark.parametrize('target', [None, '', '   '])
def test_look_at_needs_target_without_requiring_every_actor_to_move(target):
    recipe = read_json(Path(__file__).parents[2] / 'examples/blender-previs-princess.json')
    recipe['actors'][0]['actions'] = []
    cli.validate_recipe(recipe)
    action = dict(preset='look_at', start=0, duration=1)
    if target is not None:
        action['target'] = target
    recipe['actors'][0]['actions'] = [action]
    with pytest.raises(cli.PrevisError):
        cli.validate_recipe(recipe)
    action['target'] = 'ExistingBrushTarget'
    cli.validate_recipe(recipe)  # Real object existence remains the renderer's check.


def test_cloud_432_capability_evidence_is_not_rejected_by_version_label(project):
    root, binding, spec_path, _, _ = project
    spec, evidence_path, _, _ = cloud_fixture(project)
    evidence = read_json(evidence_path)
    evidence['blender_version'] = '4.3.2'
    write_json(evidence_path, evidence)
    spec['render_backend'] = 'cloud Blender 4.3.2 test fixture'
    write_json(spec_path, spec)
    entry = p.register_reference(root, binding, spec_path)
    assert entry['status'] == 'candidate'


def test_no_actor_reference_keeps_adoption_and_consumer_gates(project):
    from scripts.core.asset_refs import require_references
    root, _, spec_path, base, package_path = project
    package = read_json(package_path)
    shot = package['scenes'][0]['shots'][0]
    shot.update(source_asset_requirement_ids=['AST-ROOM'],
                take=dict(start='空房起幅', process='固定构图', end='空房落幅'),
                camera=dict(framing='空房间', movement='固定', focus='空间布局'))
    write_json(package_path, package)  # Isolated fixture setup, no production edit.
    (root / '03_导演/R03.md').write_text('TEST ONLY：已虚拟确认的无人空房间镜头', encoding='utf-8')
    binding = p.prepare_shot(root, 'S03-150', 'EMPTY-R01', base['artifact_id'])
    spec = read_json(spec_path)
    spec.update(actors=[], appearance_refs=['AST-ROOM@room'])
    spec['camera']['director_contract'] = shot['camera']
    write_json(spec_path, spec)
    entry = p.register_reference(root, binding, spec_path)
    assert not require_references(root, [entry['artifact_id']], ['S03-150'])[0]
    p.confirm_reference(root, entry['artifact_id'], 'SYNTHETIC-EMPTY-ACTORS', '仅测试虚拟批准')
    bundle = read_json(p.handoff(root, entry['artifact_id'], root / 'empty-actors-handoff'))
    assert bundle['actors'] == [] and not bundle['generation_authorized']
    assert require_references(root, [entry['artifact_id']], ['S03-150']) == (True, [])
    assert not require_references(root, [entry['artifact_id']], ['OTHER-SHOT'])[0]


def test_missing_actor_list_is_not_the_empty_actor_case(project):
    root, binding, spec_path, _, _ = project
    spec = read_json(spec_path)
    spec['actors'] = None
    write_json(spec_path, spec)
    with pytest.raises(p.ProjectPrevisError, match='列表'):
        p.register_reference(root, binding, spec_path)


def test_handoff_carries_confirmed_media_and_explicit_subset(project):
    root, binding, spec_path, _, _ = project
    spec = read_json(spec_path)
    image = Path(spec['files'][0]['path'])
    # Synthetic bytes exercise transport only, never an actual video/scene.
    video = spec_path.parent / 'test-only-video.mp4'
    video.write_bytes(b'SIMULATED_VIDEO_NOT_RENDERED')
    scene = spec_path.parent / 'test-only.blend'
    scene.write_bytes(b'SIMULATED_SCENE_NOT_RENDERED')
    spec['files'] += [dict(role='end_image', path=str(image)), dict(role='topdown', path=str(image)),
                      dict(role='video', path=str(video)), dict(role='shot_scene', path=str(scene))]
    write_json(spec_path, spec)
    entry = adopt(root, binding, spec_path)  # Isolated simulated approval only.
    bundle = read_json(p.handoff(root, entry['artifact_id'], root / 'all-media'))
    copied = [f for f in bundle['references'] if 'role' in f['source']]
    assert [f['source']['role'] for f in copied] == ['main_image', 'end_image', 'topdown', 'video']
    assert all(p.verify_file(root, f).read_bytes() == p.verify_file(root, f['source']).read_bytes() for f in copied)
    assert bundle['coverage'] == spec['coverage']
    single = read_json(p.handoff(root, entry['artifact_id'], root / 'static-only', ['main_image']))
    assert [f['source']['role'] for f in single['references'] if 'role' in f['source']] == ['main_image']
    assert '仅所选媒体' in single['coverage'] and '回源核对' in single['coverage']
    assert '静帧不证明连续运动' in single['responsibility']
    assert '仅所选媒体' in (root / 'static-only/参考职责.md').read_text()
    assert not bundle['generation_authorized'] and not single['generation_authorized']
    for roles in ([], ['unregistered'], ['shot_scene'], ['technical_evidence']):
        with pytest.raises(p.ProjectPrevisError, match='已登记'):
            p.handoff(root, entry['artifact_id'], root / 'bad-media', roles)
        assert not (root / 'bad-media').exists()
    video_record = next(f for f in copied if f['source']['role'] == 'video')['source']
    p.project_file(root, video_record['path']).write_bytes(b'CHANGED')
    with pytest.raises(p.ProjectPrevisError, match='文件缺失或内容变化'):
        p.handoff(root, entry['artifact_id'], root / 'tampered-source', ['main_image'])


def test_handoff_cli_role_selection_preserves_default():
    args = cli.parser().parse_args(['handoff-reference', '--project', 'project', '--artifact-id', 'TEST', '--output-dir', 'out'])
    assert args.media_role is None
    args = cli.parser().parse_args(['handoff-reference', '--project', 'project', '--artifact-id', 'TEST', '--output-dir', 'out', '--media-role', 'main_image', '--media-role', 'video'])
    assert args.media_role == ['main_image', 'video']


def test_unsupported_registered_media_cannot_claim_complete_coverage(project):
    root, binding, spec_path, _, _ = project
    spec = read_json(spec_path)
    gif = spec_path.parent / 'offline-test-motion.gif'
    frames = [Image.new('RGB', (16, 16), color) for color in ['red', 'blue']]
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=100, loop=0)
    spec['files'].append(dict(role='motion_gif', path=str(gif)))
    spec['coverage'] = 'TEST ONLY: 包括GIF中的运动过程'
    write_json(spec_path, spec)
    entry = adopt(root, binding, spec_path)  # Isolated fixture, never a user approval.
    bundle = read_json(p.handoff(root, entry['artifact_id'], root / 'gif-boundary'))
    assert bundle['coverage'] != spec['coverage']
    assert '仅所选媒体' in bundle['coverage'] and 'motion_gif' in bundle['coverage']
    assert '不得据其声明包内覆盖' in bundle['coverage']
    assert [f['source']['role'] for f in bundle['references'] if 'role' in f['source']] == ['main_image']
    with pytest.raises(p.ProjectPrevisError, match='已登记'):
        p.handoff(root, entry['artifact_id'], root / 'unsupported-role', ['motion_gif'])
