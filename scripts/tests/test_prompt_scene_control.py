"""Scoped synthetic user decisions; never imply native or generated-pixel proof."""
import copy
import json

import pytest

from scripts.canvas_readback import digest, prompt_scene_control
from scripts.core.quality_evidence import validate_scene_state_references


def case(root):
    source = {'artifact_id': 'SYNTHETIC-PROMPT', 'revision': 'R01', 'sha256': 'a' * 64}
    shots = ['S01-001', 'S01-002']
    requirements = [dict(entity_id='TABLE', state_id=state, source_shot_ids=[shot])
                    for state, shot in zip(('before', 'after'), shots)]
    decision = {'issue_id': 'SYNTHETIC-DECISION', 'kind': 'prompt_scene_state_control',
                'status': 'user_accepted', 'project_path': str(root.resolve()),
                'target_url': 'https://fixture.invalid/canvas', 'source_artifact': source,
                'segments': {'V03': shots}, 'entity_ids': ['TABLE'],
                'quote': 'SYNTHETIC USER DECISION ONLY', 'at': '2026-11-18T00:00:00+08:00',
                'review_ref': 'synthetic message'}
    row = {'node_id': 'node', 'source_segment_id': 'V03', 'source_shot_ids': shots,
           'reference_checks': [], 'prompt_state_control': {
               'decision_id': decision['issue_id'], 'sha256': digest(decision)},
           'quality_evidence': {'checks': {'persistent_objects': {
               'scene_state_assessment': 'changed', 'source_shot_ids': shots,
               'observation': 'Synthetic declared table transition, no generated pixels.',
               'locator': 'synthetic source'}}}}
    frozen = {'target_url': decision['target_url'], 'source_artifacts': [copy.deepcopy(source)]}
    return row, frozen, {'accepted_issues': [decision]}, requirements


def test_scoped_prompt_control_keeps_generated_state_pending_and_default_strict(tmp_path):
    row, frozen, state, requirements = case(tmp_path)
    with pytest.raises(ValueError, match='多个持久状态'):
        validate_scene_state_references(row, requirements)
    result = prompt_scene_control(row, frozen, tmp_path, state, requirements, formal_delivery=True)
    assert result['status'] == 'user_accepted_prompt_control'
    assert result['generated_state_verification'] == 'pending'
    validate_scene_state_references(row, requirements, accepted_prompt_control=result is not None)
    del row['quality_evidence']
    with pytest.raises(ValueError, match='持久状态判断'):
        validate_scene_state_references(row, requirements, accepted_prompt_control=True)


@pytest.mark.parametrize('mutation', ['frozen_only', 'project', 'canvas', 'source', 'segment',
                                     'shots', 'entity', 'changed_decision', 'not_formal'])
def test_prompt_control_cannot_widen_or_forge_scope(tmp_path, mutation):
    row, frozen, state, requirements = case(tmp_path)
    formal = True
    if mutation == 'frozen_only':
        frozen['accepted_issues'] = state['accepted_issues']; state = {'accepted_issues': []}
    elif mutation == 'project':
        tmp_path = tmp_path / 'another-project'
    elif mutation == 'canvas':
        frozen['target_url'] += '-other'
    elif mutation == 'source':
        frozen['source_artifacts'][0]['sha256'] = 'b' * 64
    elif mutation == 'segment':
        row['source_segment_id'] = 'V04'
    elif mutation == 'shots':
        row['source_shot_ids'] = [*row['source_shot_ids'], 'S01-003']
    elif mutation == 'entity':
        requirements[0]['entity_id'] = 'OTHER'
    elif mutation == 'changed_decision':
        state['accepted_issues'][0]['quote'] = 'changed'
    else:
        formal = False
    with pytest.raises(ValueError, match='prompt_state_control'):
        prompt_scene_control(row, frozen, tmp_path, state, requirements, formal_delivery=formal)


def test_accepted_prompt_control_still_rejects_conflicting_active_scene_reference(tmp_path):
    row, frozen, state, requirements = case(tmp_path)
    row['reference_checks'] = [{'kind': 'image', 'entity_ids': ['TABLE'], 'roles': ['layout'],
                                'state_id': 'before', 'source_shot_ids': row['source_shot_ids']}]
    control = prompt_scene_control(row, frozen, tmp_path, state, requirements, formal_delivery=True)
    with pytest.raises(ValueError, match='旧态/基础环境图|用途越出'):
        validate_scene_state_references(row, requirements, accepted_prompt_control=control is not None)
    # A real opening-only use remains compatible with a later generated state.
    row['reference_checks'][0]['source_shot_ids'] = ['S01-001']
    validate_scene_state_references(row, requirements, accepted_prompt_control=True)


def source_case(root):
    from scripts.core import artifacts
    from scripts.core.io import write_json
    from scripts.core.project_state import new_project_state, write_project_state
    row, frozen, state, requirements = case(root)
    states = [{**item, 'previous_state_id': None if index == 0 else 'before',
               'change_event': 'Synthetic transition', 'description': item['state_id'],
               'preserve': ['Synthetic table position'], 'changes': ['Synthetic item location']}
              for index, item in enumerate(requirements)]
    board = {'scenes': [{'scene_id': 'S01', 'shots': [{'shot_id': s} for s in row['source_shot_ids']],
                          'scene_states': states}]}
    payload = {'revision': 'R01', 'target_canvas': {'target_url': frozen['target_url']},
               'generation_segments': [{'segment_id': 'V03', 'source_shot_ids': row['source_shot_ids'],
                                         'prompt': 'Synthetic continuous transition.', 'materials': []}]}
    write_json(root / 'video.json', payload)
    write_json(root / 'board.json', board)
    actual = artifacts.digest(root / 'video.json')
    source = {'artifact_id': 'SYNTHETIC-PROMPT', 'revision': 'R01', 'sha256': actual}
    write_json(root / artifacts.MANIFEST_PATH, {'artifacts': [
        {**source, 'logical_id': 'video.prompt', 'status': 'candidate',
         'path_or_url': 'video.json', 'checksum': actual}]})
    state['accepted_issues'][0]['source_artifact'] = source
    saved_state = new_project_state()
    saved_state['accepted_issues'] = state['accepted_issues']
    write_project_state(root, saved_state)
    return payload, board, saved_state, source


@pytest.mark.parametrize('accepted', [False, True])
def test_source_cli_reuses_exact_existing_decision_without_rewriting_source(tmp_path, capsys, accepted):
    from scripts.core.generation_boundaries import main
    from scripts.core.project_state import write_project_state
    payload, board, state, source = source_case(tmp_path)
    original = (tmp_path / 'video.json').read_bytes()
    if not accepted:
        state['accepted_issues'] = []
        write_project_state(tmp_path, state)
    assert main(['--project', str(tmp_path), '--prompt', 'video.json', '--storyboard', 'board.json']) == 0
    report = json.loads(capsys.readouterr().out)
    result, = report['scene_state_plan']['segments']
    if accepted:
        assert result['status'] == 'user_accepted_prompt_control'
        assert result['prompt_state_control']['generated_state_verification'] == 'pending'
    else:
        assert result['status'] == 'needs_review'
    assert (tmp_path / 'video.json').read_bytes() == original


def test_source_cli_rejects_a_decision_with_different_shot_scope(tmp_path, capsys):
    from scripts.core.generation_boundaries import main
    from scripts.core.project_state import write_project_state
    _, _, state, _ = source_case(tmp_path)
    state['accepted_issues'][0]['segments']['V03'] = ['S01-001']
    write_project_state(tmp_path, state)
    assert main(['--project', str(tmp_path), '--prompt', 'video.json', '--storyboard', 'board.json']) == 1
    assert 'shot scope' in json.loads(capsys.readouterr().out)['error']


def test_source_cli_does_not_reuse_decision_after_source_bytes_change(tmp_path, capsys):
    from scripts.core.generation_boundaries import main
    source_case(tmp_path)
    with (tmp_path / 'video.json').open('a') as stream:
        stream.write('\n')
    assert main(['--project', str(tmp_path), '--prompt', 'video.json', '--storyboard', 'board.json']) == 1
    assert 'source bytes/revision' in json.loads(capsys.readouterr().out)['error']


def test_canvas_preflight_explains_a_source_manuscript_is_not_a_frozen_delivery(tmp_path):
    from scripts.canvas_readback import validate_submission
    with pytest.raises(ValueError, match='交付冻结快照'):
        validate_submission({'generation_segments': []}, tmp_path, formal_delivery=True, preflight=True)


def test_formal_canvas_consumes_scoped_decision_and_still_requires_source_and_draft_checks(tmp_path):
    from scripts.canvas_readback import validate_submission
    from scripts.core.project_state import load_project_state, write_project_state
    from scripts.tests.test_scene_state_references import scene_delivery, bind_synthetic_quality
    frozen = scene_delivery(tmp_path)
    row = frozen['drafts'][0]
    row.update(reference_checks=[], ordered_media=[], required_entities=[], required_reference_roles={})
    _, _, state, _ = case(tmp_path)
    decision = state['accepted_issues'][0]
    decision.update(target_url=frozen['target_url'],
                    source_artifact=copy.deepcopy(frozen['source_artifacts'][-1]),
                    segments={row['source_segment_id']: row['source_shot_ids']}, entity_ids=['DINING-ROOM'])
    saved = load_project_state(tmp_path)
    saved['accepted_issues'] = [decision]
    write_project_state(tmp_path, saved)
    row['prompt_state_control'] = {'decision_id': decision['issue_id'], 'sha256': digest(decision)}
    bind_synthetic_quality(tmp_path, frozen)
    report = validate_submission(frozen, tmp_path, formal_delivery=True, preflight=True)
    assert report['prompt_state_control_checks'][row['node_id']]['generated_state_verification'] == 'pending'
    assert report['visual_quality_verified'] is False
    # No acceptance is manufactured from a claim copied onto a frozen draft.
    saved['accepted_issues'] = []
    write_project_state(tmp_path, saved)
    with pytest.raises(ValueError, match='decision missing'):
        validate_submission(frozen, tmp_path, formal_delivery=True, preflight=True)


@pytest.mark.parametrize('wrong_scope', [False, True])
def test_formal_source_adoption_uses_the_same_prompt_decision_scope(tmp_path, wrong_scope):
    from scripts.core import artifacts
    from scripts.core.io import read_json, write_json
    from scripts.core.project_state import load_project_state, write_project_state
    from scripts.tests.test_formal_package_submission import video_project, register
    payload = video_project(tmp_path, current_prompt=False)
    board = read_json(tmp_path / 'B.json')
    shot = payload['generation_segments'][0]['source_shot_ids'][0]
    board['scenes'][0]['scene_states'] = [{
        'state_id': 'PROP-PLACED', 'entity_id': 'TABLE', 'previous_state_id': None,
        'change_event': 'Synthetic object is placed.', 'description': 'Synthetic object remains.',
        'preserve': ['Synthetic table'], 'changes': ['Synthetic object position'], 'source_shot_ids': [shot]}]
    write_json(tmp_path / 'B.json', board)
    manifest = artifacts.load_manifest(tmp_path)
    next(a for a in manifest['artifacts'] if a['artifact_id'] == 'B')['checksum'] = artifacts.digest(tmp_path / 'B.json')
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    payload['target_canvas'] = {'target_url': 'https://fixture.invalid/canvas'}
    entry = register(tmp_path, 'P-PROMPT-CONTROL', 'video.prompt', payload, ['storyboard.package'])
    _, _, original, _ = case(tmp_path)
    decision = original['accepted_issues'][0]
    decision.update(source_artifact={'artifact_id': entry['artifact_id'], 'revision': entry['revision'],
                                     'sha256': artifacts.digest(tmp_path / 'P-PROMPT-CONTROL.json')},
                    segments={'G-01': ['S99-999'] if wrong_scope else [shot]})
    state = load_project_state(tmp_path)
    state['accepted_issues'] = [decision]
    write_project_state(tmp_path, state)
    if wrong_scope:
        with pytest.raises(ValueError, match='shot scope'):
            artifacts.adopt_initial_package(tmp_path, entry['artifact_id'], 'synthetic exact approval')
        assert next(a for a in artifacts.load_manifest(tmp_path)['artifacts']
                    if a['artifact_id'] == entry['artifact_id'])['status'] == 'candidate'
    else:
        artifacts.adopt_initial_package(tmp_path, entry['artifact_id'], 'synthetic exact approval')
        assert next(a for a in artifacts.load_manifest(tmp_path)['artifacts']
                    if a['artifact_id'] == entry['artifact_id'])['status'] == 'current'
