"""Regression of the actual candidate character dependency route; no generator."""
import json
from pathlib import Path
import pytest
from PIL import Image
from scripts import asset_generation_trial as t
from scripts.tests import test_asset_generation_trial as fixtures

@pytest.fixture
def trial():
    case = fixtures.TrialTests()
    case.setUp()
    yield case
    case.tearDown()

def request_for(req, role, ident):
    head = role == 'face'
    return {**req, 'attempt_id': ident, 'baseline_role': role,
            'stage': 'character_head_from_composite' if head else 'wardrobe_three_view_from_composite',
            'wardrobe_set': 'INITIAL-COMPOSITE',
            'generated_reference_bindings': [{**req['generated_reference_bindings'][0],
                'purpose': 'identity_for_head' if head else 'clothing_for_three_view'}]}

def finish(case, root, req, role):
    t.preflight(root, req)
    output = case.base / (req['attempt_id'] + '.png')
    Image.new('RGB', (1600, 900), (21, len(req['attempt_id']), 77)).save(output)
    receipt = t.finish(root, req['attempt_id'], output, {'passed': True, 'notes': 'Synthetic new baseline inspected'})
    t.approve_dependency(root, {'attempt_id': req['attempt_id'], 'dependency_role': role,
        'wardrobe_set': req['wardrobe_set'], 'headless_three_view': role == 'wardrobe_three_view',
        'review_kind': 'producer_self_check', 'observer': 'synthetic producer',
        'visual_observation': 'Synthetic exact output role inspected', 'image_sha256': receipt['image']['sha256']})
    return receipt

def baselines(case):
    source, root, req, original = case.cross_trial_fixture()
    before = (source / 'attempts/A01/request.json').read_bytes()
    head = finish(case, root, request_for(req, 'face', 'HEAD'), 'face')
    wardrobe = finish(case, root, request_for(req, 'wardrobe_three_view', 'CLOTHES'), 'wardrobe_three_view')
    assert (source / 'attempts/A01/request.json').read_bytes() == before
    board = {**case.request, 'attempt_id': 'BOARD', 'queue_id': req['queue_id'],
        'baseline_role': None, 'dependency_role': 'identity_board', 'wardrobe_set': 'INITIAL-COMPOSITE',
        'reference_paths': [head['image']['path'], wardrobe['image']['path']],
        'output_kind': '人物1x4', 'layout_requirements': t.CHARACTER_BOARD_LAYOUT}
    return root, board, head, wardrobe

def test_new_native_head_and_headless_three_view_produce_finite_board(trial):
    root, req, head, wardrobe = baselines(trial)
    record = t.preflight(root, req)['request']
    assert record['dependency']['depth'] == 2
    approval = t.read(root / 'attempts/CLOTHES/dependency-approval.json')
    assert approval['headless_three_view'] and not approval['independent_single_view']
    output = trial.base / 'final.png'
    Image.new('RGB', (1600, 900), 'purple').save(output)
    result = t.finish(root, 'BOARD', output, {'passed': True, 'notes': 'Synthetic candidate',
        'layout_visual_observation': 'Synthetic 1x4 head and headless body layout observation'})
    assert result['output_kind'] == '人物1x4'
    assert result['production_blocked'] and not result['user_approved']


def test_confirmed_library_head_with_same_trial_wardrobe(trial):
    queue = t.read(trial.queue)
    index = trial.base / 'library.json'
    entry = {'library_asset_id': 'HEAD', 'category': '大头照', 'state': '已确认',
             'destination': trial.ref.name, 'confirmation': {'id': 'receipt', 'asset_id': 'HEAD'}}
    t.create(index, {'assets': [entry]})
    queue['items'][0]['independent_face_reference'] = {
        'index_path': str(index), 'library_asset_id': 'HEAD'}
    path = trial.base / 'library-queue.json'
    t.create(path, queue)
    root = trial.base / 'library-trial'
    t.initialize(root, path, trial.auth)
    wardrobe = finish(trial, root, {**trial.request, 'attempt_id': 'WARDROBE',
        'baseline_role': 'wardrobe_three_view', 'wardrobe_set': 'A'}, 'wardrobe_three_view')
    req = {**trial.request, 'attempt_id': 'BOARD', 'baseline_role': None,
           'dependency_role': 'identity_board', 'wardrobe_set': 'A', 'output_kind': '人物1x4',
           'layout_requirements': t.CHARACTER_BOARD_LAYOUT,
           'reference_paths': [str(trial.ref), wardrobe['image']['path']]}
    edge, _ = t.preflight(root, req)['request']['dependency']['edges']
    assert edge['role'] == 'face' and edge['confirmation_id'] == 'receipt'
    index.write_text(json.dumps({'assets': [{**entry, 'state': '待确认'}]}))
    with pytest.raises(ValueError, match='active confirmation'):
        t.finish(root, 'BOARD', trial.output(), {'passed': False, 'notes': 'synthetic'})

@pytest.mark.parametrize('change', [ {'output_kind': '人物单图'}, {'layout_requirements': {}},
    {'wardrobe_set': 'OTHER'}, {'reference_paths': []} ])
def test_board_rejects_missing_or_wrong_actual_contract(trial, change):
    root, req, _, _ = baselines(trial)
    with pytest.raises(ValueError):
        t.preflight(root, {**req, **change})

def test_board_dimensions_and_observation_are_separate_checks(trial):
    root, req, _, _ = baselines(trial)
    t.preflight(root, req)
    with pytest.raises(ValueError, match='16:9'):
        t.finish(root, 'BOARD', trial.output(), {'passed': True, 'notes': 'Synthetic'})
    output = trial.base / 'final.png'; Image.new('RGB', (1600, 900), 'purple').save(output)
    with pytest.raises(ValueError, match='visual layout observation'):
        t.finish(root, 'BOARD', output, {'passed': True, 'notes': 'Synthetic'})

def test_clothing_rebuild_accepts_only_one_root_derived_exact_state(trial):
    source, state_root, req, original = trial.cross_trial_fixture()
    t.preflight(state_root, req)
    state_output = trial.base / 'state.png'; Image.new('RGB', (1600, 900), 'yellow').save(state_output)
    state = t.finish(state_root, req['attempt_id'], state_output, {'passed': True, 'notes': 'Synthetic state'})
    queue = t.read(trial.queue)
    queue['items'][0].update(asset_id='AST-A', output_id='NEW-CLOTHING', references=[])
    queue_path = trial.base / 'third-queue.json'; t.create(queue_path, queue)
    auth = t.read(trial.auth); auth.update(run_id='third')
    auth_path = trial.base / 'third-auth.json'; t.create(auth_path, auth)
    root = trial.base / 'third'; t.initialize(root, queue_path, auth_path)
    new = request_for(req, 'wardrobe_three_view', 'REBUILD')
    new.update(queue_id='test:A', wardrobe_set='VISIT-COMPOSITE', reference_paths=[state['image']['path']])
    new['generated_reference_bindings'][0].update(source_trial_root=str(state_root), attempt_id=req['attempt_id'])
    with pytest.raises(ValueError):
        t.preflight(root, {**new, 'wardrobe_set': 'OTHER'})
    record = t.preflight(root, new)['request']
    assert record['generated_reference_context'][0]['source_provenance_depth'] == 1
    t.create(source / 'attempts/A01/review-overrides/rejected.json',
        {'image_sha256': original['image']['sha256'], 'passed': False, 'notes': 'Synthetic root rejected'})
    with pytest.raises(ValueError):
        t.finish(root, 'REBUILD', trial.output(), {'passed': True, 'notes': 'Synthetic'})

def isolated_trial(case, name, queue_id):
    queue = t.read(case.queue)
    queue['items'][0].update(queue_id=queue_id, asset_id='AST-A', output_id='INITIAL-COMPOSITE', references=[])
    queue_path = case.base / (name+'-queue.json'); t.create(queue_path, queue)
    auth = t.read(case.auth); auth.update(run_id=name, allowed_queue_ids=[queue_id])
    auth_path = case.base / (name+'-auth.json'); t.create(auth_path, auth)
    root = case.base / name; t.initialize(root, queue_path, auth_path)
    return root

def cross_baselines(case):
    source, head_root, req, original = case.cross_trial_fixture()
    head = finish(case, head_root, request_for(req, 'face', 'HEAD'), 'face')
    clothing_root = isolated_trial(case, 'clothing-batch', 'clothing-queue')
    clothing_req = request_for(req, 'wardrobe_three_view', 'CLOTHES')
    clothing_req['queue_id'] = 'clothing-queue'
    clothing = finish(case, clothing_root, clothing_req, 'wardrobe_three_view')
    board_root = isolated_trial(case, 'final-batch', 'final-queue')
    board = {**case.request, 'attempt_id': 'BOARD', 'queue_id': 'final-queue',
        'baseline_role': None, 'dependency_role': 'identity_board', 'wardrobe_set': 'INITIAL-COMPOSITE',
        'reference_paths': [head['image']['path'], clothing['image']['path']],
        'output_kind': '人物1x4', 'layout_requirements': t.CHARACTER_BOARD_LAYOUT,
        'method_reference_bindings': [
            {'source_trial_root': str(head_root), 'attempt_id': 'HEAD', 'role': 'face'},
            {'source_trial_root': str(clothing_root), 'attempt_id': 'CLOTHES', 'role': 'wardrobe_three_view'}]}
    return board_root, board, head_root, clothing_root

def test_three_independent_trials_keep_native_sources_and_bounded_design_lineage(trial):
    root, request, head_root, clothing_root = cross_baselines(trial)
    originals = {str(p): p.read_bytes() for src in (head_root, clothing_root)
                 for p in src.glob('attempts/*/*.json')}
    record = t.preflight(root, request)['request']
    assert record['dependency']['depth'] == 2
    assert len(record['dependency']['edges']) == 2
    assert all(e['design_lineage'] for e in record['dependency']['edges'])
    output = trial.base / 'board.png'; Image.new('RGB', (1600, 900), 'pink').save(output)
    result = t.finish(root, 'BOARD', output, {'passed': True, 'notes': 'Synthetic final self-check',
        'layout_visual_observation': 'Synthetic exact 1x4 observation'})
    assert result['production_blocked'] and not result['user_approved']
    assert t.check_reference_reviews(root, [result['image']])[result['image']['path']] == 2
    assert all(Path(p).read_bytes() == value for p, value in originals.items())

@pytest.mark.parametrize('failure', ['wrong_ast', 'wrong_narrative', 'wrong_set', 'failed',
    'rejected', 'method_drift', 'copy', 'role_laundering', 'missing_binding', 'approval_drift'])
def test_cross_trial_rejects_unqualified_or_misbound_sources(trial, failure):
    root, request, head_root, clothing_root = cross_baselines(trial)
    folder = clothing_root / 'attempts/CLOTHES'
    if failure in {'wrong_ast', 'wrong_narrative'}:
        path = clothing_root / 'asset_generation_trial.json'; value = t.read(path)
        value['items']['clothing-queue']['asset_id' if failure == 'wrong_ast' else 'source_narrative'] = 'OTHER'
        path.write_text(json.dumps(value))
    elif failure == 'wrong_set':
        request['wardrobe_set'] = 'OTHER'
    elif failure == 'failed':
        path = folder / 'result.json'; value = t.read(path); value['assessment']['passed'] = False
        path.write_text(json.dumps(value))
    elif failure == 'rejected':
        t.create(folder / 'review-overrides/reject.json', {'image_sha256': t.read(folder/'result.json')['image']['sha256'],
            'passed': False, 'notes': 'Synthetic later rejection'})
    elif failure == 'method_drift':
        trial.method.write_text('Synthetic changed method')
    elif failure == 'copy':
        copy = trial.base / 'copy.png'; copy.write_bytes(Path(request['reference_paths'][1]).read_bytes())
        request['reference_paths'][1] = str(copy)
    elif failure == 'role_laundering':
        request['method_reference_bindings'][0]['attempt_id'] = 'CLOTHES'
        request['method_reference_bindings'][0]['source_trial_root'] = str(clothing_root)
    elif failure == 'missing_binding':
        request['method_reference_bindings'].pop()
    elif failure == 'approval_drift':
        path = folder / 'dependency-approval.json'; value = t.read(path); value['self_check']['image_sha256'] = 'wrong'
        path.write_text(json.dumps(value))
    with pytest.raises((ValueError, FileNotFoundError)):
        t.preflight(root, request)

def test_cross_trial_source_rejection_between_preflight_and_finish_blocks_completion(trial):
    root, request, head_root, clothing_root = cross_baselines(trial)
    t.preflight(root, request)
    folder = head_root / 'attempts/HEAD'
    t.create(folder / 'review-overrides/reject.json', {'image_sha256': t.read(folder/'result.json')['image']['sha256'],
        'passed': False, 'notes': 'Synthetic subsequent rejection'})
    with pytest.raises(ValueError):
        t.finish(root, 'BOARD', trial.output(), {'passed': True, 'notes': 'Synthetic'})
