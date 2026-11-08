"""Final-layout requirements use real entry points, without media generation."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts import stage_context_cli as cli
from scripts.core import asset_refs


@pytest.fixture
def contract(tmp_path, monkeypatch):
    narrative = {'revision': 'R04', 'asset_requirements': [
        {'asset_id': 'AST-001', 'category': 'CHARACTER', 'image_decision': '需要制作'}]}
    folder = tmp_path / '04_资产'
    folder.mkdir()
    (folder / '资产需求调整.json').write_text(json.dumps({
        'character_output_requirements': {'AST-001': {
            'source_revision': 'R04', 'evidence': 'synthetic explicit final-board requirement',
            'outputs': {'HOME-FINAL': '人物1x4', 'OUT-FINAL': '人物1x4'}}}}), encoding='utf-8')
    monkeypatch.setattr(asset_refs, 'current_narrative', lambda root: narrative)
    return tmp_path


def test_prepare_rejects_final_downgrade_before_production(contract):
    request = contract / 'request.json'
    request.write_text(json.dumps({'image_targets': ['AST-001@HOME-FINAL'],
                                  'image_output_kinds': {'AST-001@HOME-FINAL': '人物单图'}}), encoding='utf-8')
    args = cli.parser().parse_args(['prepare', '--project', str(contract), '--skill',
        'asset-image-assistant', '--objective', 'synthetic plan only', '--request-json', str(request)])
    with pytest.raises(cli.StageContextError, match='image_output_kinds'):
        cli.prepare(args)
    assert not (contract / '00_项目管理').exists()


def consumption_basis(contract, image_target):
    request = {'image_targets': [image_target]}
    request_path = contract / 'request.json'
    request_path.write_text(json.dumps(request), encoding='utf-8')
    return {'skill': 'asset-image-assistant', 'reading_manifest': {
                'project_path': str(contract), 'vault_root': str(cli._vault_root(None))},
            'input_snapshot': {}, 'request_path': str(request_path), 'task_request': request}


def test_consumption_rechecks_specification(contract):
    basis = consumption_basis(contract, 'AST-001@HOME-FINAL')
    with pytest.raises(cli.StageContextError, match='image_output_kinds'):
        cli._validate_current_inputs(contract, basis)


def test_consumption_rejects_changed_final_scope(contract):
    basis = consumption_basis(contract, 'AST-001@HEAD')
    with pytest.raises(cli.StageContextError, match='最终输出规格已变化'):
        cli._validate_current_inputs(contract, basis)


def test_final_gate_checks_only_requested_outfit_and_reuses_ordinary_checks(contract, monkeypatch):
    calls = []
    monkeypatch.setattr(asset_refs, 'require_images', lambda root, requests:
                        (calls.append(requests) is None, []))
    monkeypatch.setattr(asset_refs, 'confirmed_images', lambda root: {'AST-001': {'outputs': [
        {'output_id': 'HOME-FINAL', 'output_kind': '人物1x4', 'path': 'home.png'}]}})
    monkeypatch.setattr(asset_refs, 'character_board_image_problem', lambda path: None)
    assert asset_refs.require_final_images(contract, ['AST-001@HOME-FINAL']) == (True, [])
    assert calls == [['AST-001@HOME-FINAL']]
    ok, problems = asset_refs.require_final_images(contract, ['AST-001'])
    assert not ok and any('OUT-FINAL' in problem for problem in problems)
    assert calls[-1] == ['AST-001@HOME-FINAL', 'AST-001@OUT-FINAL']


def test_final_gate_cannot_replace_confirmation(contract, monkeypatch):
    monkeypatch.setattr(asset_refs, 'confirmed_images', lambda root: {'AST-001': {'outputs': [
        {'output_id': 'HOME-FINAL', 'output_kind': '人物1x4'}]}})
    monkeypatch.setattr(asset_refs, 'require_images', lambda root, requests: (False, ['not confirmed']))
    assert asset_refs.require_final_images(contract, ['AST-001@HOME-FINAL']) == (False, ['not confirmed'])


def test_old_projects_keep_original_gate_without_loading_story(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_refs, 'current_narrative', lambda root: pytest.fail('unneeded story read'))
    monkeypatch.setattr(asset_refs, 'require_images', lambda root, requests: (True, []))
    assert asset_refs.check_output_plan(tmp_path, {'image_targets': ['AST-001@SINGLE']}) == {}
    assert asset_refs.require_final_images(tmp_path, ['AST-001@SINGLE']) == (True, [])


@pytest.mark.parametrize('name', ['visual-asset-manifest.schema.json', 'visual-asset-manifest-v1.schema.json'])
def test_output_kind_is_optional_in_existing_schema(name):
    schema = json.loads((cli.ROOT / 'schemas' / name).read_text())
    validator = Draft202012Validator({'$defs': schema['$defs'], '$ref': '#/$defs/confirmedOutput'})
    output = {'output_id': 'HOME-FINAL', 'purpose': 'identity', 'view': 'front',
              'state': '', 'candidate_id': 'AST-001-C01',
              'path': 'asset.png', 'user_confirmation_ref': 'receipt.json#home'}
    validator.validate(output)
    validator.validate({**output, 'output_kind': '人物1x4'})


def test_cli_exposes_scoped_final_gate(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['asset_refs', '--project', str(tmp_path),
                                   '--require-final-image', 'AST-001@HOME-FINAL'])
    calls = []
    monkeypatch.setattr(asset_refs, 'require_final_images', lambda root, requests:
                        (calls.append(requests) is None, []))
    assert asset_refs.main() == 0
    assert calls == [['AST-001@HOME-FINAL']]
    assert '通过' in capsys.readouterr().out


@pytest.fixture
def web_character():
    import test_visual_asset_characters as fixtures
    case = fixtures.CharacterRoutes()
    case.setUp()
    try:
        yield case
    finally:
        case.tearDown()
        case.doCleanups()


def register_web_finals(case, outputs, **binding):
    root = Path(case.project['path'])
    narrative = json.loads((root / 'narrative.json').read_text())
    folder = root / '04_资产'
    folder.mkdir(exist_ok=True)
    (folder / '资产需求调整.json').write_text(json.dumps({
        'revision': 1, 'added': [], 'excluded': {}, 'replaced': {}, 'history': [],
        'character_output_requirements': {'AST-PERSON': {
            'source_revision': narrative['revision'], 'evidence': 'synthetic explicit requirement',
            'outputs': outputs, **binding}}}), encoding='utf-8')
    case.lib.projects.sync(case.project['id'])


def test_web_binds_known_final_without_renaming_legacy_step(web_character):
    import shutil
    from visual_asset_characters import prepare_character

    case = web_character
    service = case.lib.projects
    project_id = case.project['id']
    payload = {'project_id': project_id, 'ast_id': 'AST-PERSON'}
    job = prepare_character(service, payload)['rows'][0]['job']
    local_key = job['steps'][0]['key']
    case.confirm(job, local_key, '人物1x4')
    previous = case.lib.get(job['id'], 'jobs')['steps'][0]['project_confirmation']

    register_web_finals(case, {'HOME-FINAL': '人物1x4'})
    with pytest.raises(ValueError, match='HOME-FINAL'):
        service.export({'project_id': project_id, 'confirm': True})
    with pytest.raises(ValueError, match='同步服装与输出 ID'):
        service.select({'job_id': job['id'], 'index': 0, 'asset_id': case.assets['人物1x4']['id']})

    planned = prepare_character(service, payload)['rows'][0]['job']
    assert planned['id'] == job['id']
    assert len(planned['steps']) == 1
    step = planned['steps'][0]
    assert step['key'] == local_key
    assert step['output_id'] == 'HOME-FINAL'
    assert step['history'][-1]['project_confirmation'] == previous
    assert step['project_confirmation'] is None
    with pytest.raises(ValueError, match='已明确要求最终人物1×4'):
        service.character_output_mode({**payload, 'mode': 'single'})

    case.confirm(planned, local_key, '人物1x4')
    selected = case.lib.get(case.assets['人物1x4']['id'])
    backup = case.base / 'board-backup.png'
    shutil.copy2(selected['original'], backup)
    selected['backup'] = {'status': 'saved', 'path': str(backup)}
    case.lib.save(selected)
    export = service.export({'project_id': project_id, 'confirm': True})
    manifest = json.loads(Path(export['manifest']).read_text())
    output = manifest['assets'][0]['confirmed_outputs'][0]
    assert (output['output_id'], output['output_kind']) == ('HOME-FINAL', '人物1x4')


def test_web_multi_outfit_requires_explicit_assignment(web_character):
    from test_visual_asset_projects import update_narrative_checksum
    from visual_asset_characters import prepare_character

    case = web_character
    service = case.lib.projects
    project_id = case.project['id']
    path = Path(case.project['path']) / 'narrative.json'
    narrative = json.loads(path.read_text())
    narrative['asset_requirements'][0]['identity_constraints'] = ['日常装 → 出差装']
    path.write_text(json.dumps(narrative, ensure_ascii=False), encoding='utf-8')
    update_narrative_checksum(path.parent)
    outputs = {'TRAVEL-FINAL': '人物1x4', 'HOME-FINAL': '人物1x4'}
    register_web_finals(case, outputs)
    payload = {'project_id': project_id, 'ast_id': 'AST-PERSON'}
    with pytest.raises(ValueError, match='outfit_output_ids'):
        prepare_character(service, payload)
    assert service.get(project_id)['rows'][0]['job_id'] is None

    register_web_finals(case, outputs, outfit_output_ids={'日常装': 'HOME-FINAL', '出差装': 'TRAVEL-FINAL'})
    job = prepare_character(service, payload)['rows'][0]['job']
    assert [(step['state'], step['output_id']) for step in job['steps']] == [
        ('日常装', 'HOME-FINAL'), ('出差装', 'TRAVEL-FINAL')]
    assert all(step['key'] != step['output_id'] for step in job['steps'])


def test_explicit_candidate_plan_uses_registered_story_without_formal_adoption(contract, monkeypatch):
    from scripts.core import candidate_test
    story = contract / 'candidate-story.json'
    story.write_text(json.dumps({'revision': 'R04', 'asset_requirements': [
        {'asset_id': 'AST-001', 'category': 'CHARACTER', 'image_decision': '需要制作'}]}))
    monkeypatch.setattr(candidate_test, 'resolve_inputs', lambda root: [
        {'logical_id': 'narrative.package', 'path_or_url': story.name}])
    monkeypatch.setattr(asset_refs, 'current_narrative', lambda root: pytest.fail('candidate plan must not claim adoption'))
    request = {'image_targets': ['AST-001@HOME-FINAL'],
               'image_output_kinds': {'AST-001@HOME-FINAL': '人物1x4'}}
    assert asset_refs.check_output_plan(contract, request, candidate_test_mode=True)['AST-001']['HOME-FINAL'] == '人物1x4'
    request['image_output_kinds']['AST-001@HOME-FINAL'] = '人物单图'
    with pytest.raises(ValueError, match='image_output_kinds'):
        asset_refs.check_output_plan(contract, request, candidate_test_mode=True)


def test_candidate_plan_flag_cannot_enable_unconfigured_project(contract):
    with pytest.raises(ValueError, match='未显式配置'):
        asset_refs.check_output_plan(contract, {'image_targets': ['AST-001@HOME-FINAL']},
                                     candidate_test_mode=True)


def test_candidate_story_revision_drift_still_blocks_output_plan(contract, monkeypatch):
    from scripts.core import candidate_test
    story = contract / 'candidate-story.json'
    story.write_text(json.dumps({'revision': 'R05', 'asset_requirements': [
        {'asset_id': 'AST-001', 'category': 'CHARACTER', 'image_decision': '需要制作'}]}))
    monkeypatch.setattr(candidate_test, 'resolve_inputs', lambda root: [
        {'logical_id': 'narrative.package', 'path_or_url': story.name}])
    with pytest.raises(ValueError, match='来源版本已变化'):
        asset_refs.check_output_plan(contract, {'image_targets': ['AST-001@HOME-FINAL']},
                                     candidate_test_mode=True)


@pytest.fixture
def adopted_character_board(tmp_path):
    from test_review_text_cli import narrative

    _, story = narrative(tmp_path)
    requirement = {**story['asset_requirements'][0], 'asset_id': 'AST-001', 'entity_id': 'CHR-001',
                   'name': '测试人物', 'category': 'CHARACTER', 'image_decision': '需要制作',
                   'image_reason': '两套服装需固定跨镜身份', 'character_output_kind': '人物1x4',
                   'wardrobe_states': [
                       {'name': '居家服', 'evidence': '测试居家场', 'output_id': 'HOME-FINAL'},
                       {'name': '外出服', 'evidence': '测试外出场', 'output_id': 'OUT-FINAL'}]}
    path, story = narrative(tmp_path, asset_requirements=[requirement])
    cli.main(['adopt-package', '--project', str(tmp_path), '--artifact-id', 'STORY',
              '--logical-id', 'narrative.package', '--path', path.name, '--revision', story['revision'],
              '--approval-ref', 'synthetic:approval'])
    assert not (tmp_path / '04_资产' / '资产需求调整.json').exists()
    return tmp_path


def test_prepare_inherits_adopted_board_and_blocks_alternate_single_id(adopted_character_board):
    root = adopted_character_board
    request = root / 'request.json'
    args = cli.parser().parse_args(['prepare', '--project', str(root), '--skill', 'asset-image-assistant',
        '--objective', 'synthetic board batch', '--request-json', str(request), '--context-id', 'board',
        '--actor-id', 'synthetic-actor', '--task-id', 'synthetic-task'])
    for output in ['HOME-FINAL', 'OTHER-ID']:
        target = 'AST-001@' + output
        request.write_text(json.dumps({'image_targets': [target], 'image_output_kinds': {target: '人物单图'}}))
        with pytest.raises(cli.StageContextError, match='已要求最终'):
            cli.prepare(args)
    targets = {'AST-001@HOME-FINAL': '人物1x4', 'AST-001@HEAD': '大头照'}
    request.write_text(json.dumps({'image_targets': list(targets), 'image_output_kinds': targets}))
    assert cli.prepare(args)['valid']
    basis = json.loads((root / cli.CONTEXT_DIR / '批次/board/task_basis.json').read_text())
    assert basis['character_output_requirements'] == {
        'AST-001': {'HOME-FINAL': '人物1x4', 'OUT-FINAL': '人物1x4'}}


def test_final_delivery_checks_adopted_exact_output_and_real_canvas(adopted_character_board):
    from PIL import Image
    from scripts.core.artifacts import digest

    root = adopted_character_board
    folder = root / asset_refs.MASTER_DIR / 'synthetic'
    folder.mkdir(parents=True)
    image = folder / 'image.png'
    receipt = folder / 'receipt.json'
    manifest = folder / 'visual-asset-manifest.json'
    manifest.write_text(json.dumps({'assets': [{'asset_id': 'AST-001', 'status': 'confirmed',
        'confirmed_outputs': [{'output_id': 'HOME-FINAL', 'output_kind': '人物1x4',
                               'path': str(image), 'user_confirmation_ref': f'{receipt}#test-only'}]}]}))
    for size in [(90, 120), (160, 90)]:
        Image.new('RGB', size).save(image)
        receipt.write_text(json.dumps({'items': [{'ast_id': 'AST-001', 'output_id': 'HOME-FINAL',
            'confirmation': {'id': 'test-only', 'image_sha256': digest(image)}}]}))
        ok, problems = asset_refs.require_final_images(root, ['AST-001@HOME-FINAL'])
        assert ok == (size == (160, 90))
        if not ok:
            assert '90×120' in problems[0]
    ok, problems = asset_refs.require_final_images(root, ['AST-001'])
    assert not ok and any('OUT-FINAL' in issue for issue in problems)
