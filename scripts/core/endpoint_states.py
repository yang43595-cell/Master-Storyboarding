"""Explicit source-selected endpoint strategy, never dynamic output proof.

Called only after the consumer validates actual platform control and both image
uses. No fallback from a mode string or missing source strategy is allowed.
"""
from .quality_evidence import source_shot_ids, scene_state_requirements, reference_shot_ids
from .prompt_body import submitted_prompt_body


def _need(value, message):
    if not value:
        raise ValueError('endpoint_state: ' + message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_endpoint_strategy(row, sources, control_proof):
    try:
        return _validate_endpoint_strategy(row, sources, control_proof)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("endpoint_state: malformed source strategy or observations") from exc


def _validate_endpoint_strategy(row, sources, control_proof):
    _need(isinstance(control_proof, dict) and control_proof.get('endpoint_constraints_verified') is True,
          'actual endpoint control evidence required; mode alone is insufficient')
    selection = row.get('state_control')
    _need(isinstance(selection, dict) and set(selection) == {
        'source_artifact_id', 'source_revision', 'source_sha256', 'scene_id', 'strategy_id', 'platform'},
        'exact selected source strategy and platform evidence required')
    selected = [(ref, payload) for ref, _, payload in sources
                if ref.get('artifact_id') == selection['source_artifact_id']
                and ref.get('revision') == selection['source_revision']
                and ref.get('sha256') == selection['source_sha256']]
    _need(len(selected) == 1, 'strategy is not from the exact authority-checked source revision')
    _, payload = selected[0]
    scenes = [s for s in payload.get('scenes', []) if s.get('scene_id') == selection['scene_id']]
    _need(len(scenes) == 1, 'missing or ambiguous source scene')
    scene = scenes[0]
    strategies = [s for s in scene.get('endpoint_strategies', []) if s.get('strategy_id') == selection['strategy_id']]
    _need(len(strategies) == 1, 'source must explicitly select the endpoint-constrained strategy')
    strategy = strategies[0]
    keys = {'strategy_id', 'mode', 'segment_id', 'source_shot_ids', 'entity_id', 'state_ids',
            'initial_boundary', 'final_boundary', 'intermediate_control',
            'required_native_intermediate_states', 'start_facts', 'end_facts'}
    _need(set(strategy) == keys and strategy['mode'] == 'endpoint_constrained_continuous_action',
          'unsupported or incomplete source endpoint strategy')
    shots = source_shot_ids(row)
    assessment = row.get('quality_evidence', {}).get('checks', {}).get('persistent_objects', {})
    _need(assessment.get('scene_state_assessment') == 'changed'
          and assessment.get('source_shot_ids') == shots
          and _text(assessment.get('observation')) and _text(assessment.get('locator')),
          'persistent-object assessment must explicitly agree with the changed full-segment chain')
    _need(shots and strategy['source_shot_ids'] == shots
          and strategy['segment_id'] == row.get('source_segment_id'), 'strategy segment/shot mapping differs')
    _need(strategy['intermediate_control'] == 'source_intent_only'
          and strategy['required_native_intermediate_states'] == [],
          'endpoint mode cannot satisfy required native intermediate locks')
    requirements = [s for s in scene_state_requirements(payload) if set(shots) & set(s['source_shot_ids'])]
    _need(requirements and all(s['entity_id'] == strategy['entity_id'] for s in requirements),
          'only one complete continuous entity chain is supported; other chains may not be omitted')
    _need(strategy['state_ids'] == [s['state_id'] for s in requirements], 'state chain omitted, reordered or substituted')
    _need(set().union(*(set(s['source_shot_ids']) for s in requirements)) == set(shots),
          'source chain must cover every selected shot without widening scope')
    for prior, current in zip(requirements, requirements[1:]):
        _need(current['previous_state_id'] == prior['state_id'], 'state chain branches or skips a predecessor')
    prompt = submitted_prompt_body(row)
    events = []
    for state in requirements:
        _need(state['change_event'] in prompt, 'source event is absent from the current submitted prompt')
        events.append({'state_id': state['state_id'], 'source_shot_ids': state['source_shot_ids'],
                       'event_quote': state['change_event'], 'verification': 'source_intent_only'})
    refs = row.get('reference_checks', [])
    _need(len(refs) == 2, 'endpoint strategy requires exactly two active image references')
    observations = []
    for index, (name, role, boundary, expected_state, shot, description) in enumerate([
        ('start', 'start_frame', strategy['initial_boundary'], requirements[0]['previous_state_id'], shots[0], scene.get('start_state')),
        ('end', 'end_frame', strategy['final_boundary'], requirements[-1]['state_id'], shots[-1], scene.get('end_state'))]):
        _need(isinstance(boundary, dict) and set(boundary) == {'state_id', 'shot_id', 'position', 'source_quote', 'description'},
              'boundary must identify the actual within-shot segment start/end')
        _need(_text(expected_state) and boundary['state_id'] == expected_state and boundary['shot_id'] == shot
              and boundary['position'] == 'segment_' + name,
              'boundary is not the exact first predecessor/final state at the segment boundary')
        quote = boundary['source_quote']
        _need(_text(quote) and _text(boundary['description']) and quote in boundary['description'] and quote in prompt,
              'boundary requires explicit byte-bound source description and current prompt quote')
        ref = refs[index]
        roles = ref.get('roles', [ref.get('role')])
        _need(ref.get('kind') == 'image' and role in roles
              and ({'start_frame', 'end_frame'} & set(roles)) == {role}
              and ref.get('state_id') == expected_state,
              'reference role/state does not match its actual endpoint slot')
        observation = ref.get('endpoint_observations')
        _need(isinstance(observation, dict) and set(observation) == {'role', 'state_id', 'boundary_shot_id', 'facts'}
              and observation['role'] == role and observation['state_id'] == expected_state
              and observation['boundary_shot_id'] == shot and shot in reference_shot_ids(ref, row),
              'exact endpoint-use observations required; old image QA is insufficient')
        facts = strategy[name + '_facts']
        actual = observation['facts']
        _need(isinstance(facts, list) and facts and isinstance(actual, list), 'source and observed endpoint facts required')
        ids = [f.get('fact_id') for f in facts if isinstance(f, dict)]
        actual_ids = [f.get('fact_id') for f in actual if isinstance(f, dict)]
        _need(len(ids) == len(facts) and all(_text(i) for i in ids) and len(set(ids)) == len(ids)
              and len(actual_ids) == len(actual) and len(set(actual_ids)) == len(actual_ids)
              and set(ids) == set(actual_ids), 'endpoint facts missing, duplicated or silently omitted')
        by_id = {f['fact_id']: f for f in actual}
        for fact in facts:
            _need(set(fact) == {'fact_id', 'entity_id', 'expected_presence', 'evidence_requirement', 'source_quote'}
                  and fact['entity_id'] in row.get('required_entities', [])
                  and fact['entity_id'] in ref.get('entity_ids', [])
                  and fact['expected_presence'] in {'present', 'absent_from_visible_frame'}
                  and fact['evidence_requirement'] in {'visible', 'source_constraint_allowed'}
                  and _text(fact['source_quote']) and fact['source_quote'] in prompt,
                  'source endpoint fact/presence/visibility scope is incomplete')
            got = by_id[fact['fact_id']]
            _need(set(got) == {'fact_id', 'entity_id', 'visibility', 'observation'}
                  and got['entity_id'] == fact['entity_id'] and _text(got['observation']),
                  'actual endpoint observation must be explicit and scoped')
            visibility = got['visibility']
            if visibility == 'not_visible':
                _need(fact['evidence_requirement'] == 'source_constraint_allowed'
                      and fact['expected_presence'] == 'present',
                      'not_visible cannot satisfy required-visible or absence claims')
                result = 'source_constraint_not_visual'
            else:
                expected_visibility = 'visible' if fact['expected_presence'] == 'present' else 'absent_from_visible_frame'
                _need(visibility == expected_visibility, 'observed endpoint visibility contradicts the source requirement')
                result = 'visible_frame_observation'
            observations.append({'role': role, 'fact_id': fact['fact_id'], 'entity_id': fact['entity_id'],
                                 'visibility': visibility, 'evidence_scope': result, 'global_absence_verified': False})
    return {'verification': 'endpoint_constraints_and_source_intent_only', 'endpoint_facts': observations,
            'events': events, 'per_shot_control': False, 'dynamics_verified': False,
            'production_ready': False}
