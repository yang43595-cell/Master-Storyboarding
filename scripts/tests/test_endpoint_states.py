"""Synthetic source declarations, not real image QA or platform observations."""
import copy
from pathlib import Path
import pytest
from scripts.core.endpoint_states import validate_endpoint_strategy
from scripts.core.quality_evidence import image_use


def fixture():
    states=[]
    for ident, prior, event, shots in [('LIFTED','FLOOR','Lift the bag.',['S1','S2']),('EXITED','LIFTED','Leave the frame.',['S3'])]:
        states.append(dict(state_id=ident,entity_id='ROOM',previous_state_id=prior,change_event=event,
            description=ident,preserve=['Fixed architecture.'],changes=[event],source_shot_ids=shots))
    strategy=dict(strategy_id='BOUND',mode='endpoint_constrained_continuous_action',segment_id='V1',
        source_shot_ids=['S1','S2','S3'],entity_id='ROOM',state_ids=['LIFTED','EXITED'],
        initial_boundary=dict(state_id='FLOOR',shot_id='S1',position='segment_start',source_quote='Bag starts on floor.',description='Bag starts on floor.'),
        final_boundary=dict(state_id='EXITED',shot_id='S3',position='segment_end',source_quote='Wife leaves frame.',description='Wife leaves frame.'),
        intermediate_control='source_intent_only',required_native_intermediate_states=[],
        start_facts=[dict(fact_id='BAG',entity_id='BAG',expected_presence='present',evidence_requirement='source_constraint_allowed',source_quote='Bag starts on floor.')],
        end_facts=[dict(fact_id='WIFE',entity_id='WIFE',expected_presence='absent_from_visible_frame',evidence_requirement='visible',source_quote='Wife leaves frame.')])
    source=dict(scenes=[dict(scene_id='S',start_state='Bag starts on floor.',end_state='Wife leaves frame.',
        shots=[dict(shot_id=s) for s in ['S1','S2','S3']],scene_states=states,endpoint_strategies=[strategy])])
    authority=dict(artifact_id='DIRECTOR',revision='R2',sha256='a'*64)
    row=dict(source_segment_id='V1',source_shot_ids=['S1','S2','S3'],required_entities=['ROOM','BAG','WIFE'],
        prompt='Bag starts on floor. Lift the bag. Leave the frame. Wife leaves frame.',
        state_control=dict(source_artifact_id='DIRECTOR',source_revision='R2',source_sha256='a'*64,scene_id='S',strategy_id='BOUND',platform={}),reference_checks=[])
    for role,state,fact,visibility in [('start_frame','FLOOR','BAG','not_visible'),('end_frame','EXITED','WIFE','absent_from_visible_frame')]:
        row['reference_checks'].append(dict(kind='image',roles=[role],state_id=state,entity_ids=['ROOM'],
            endpoint_observations=dict(role=role,state_id=state,boundary_shot_id='S1' if role=='start_frame' else 'S3',facts=[dict(fact_id=fact,entity_id=fact,visibility=visibility,observation='Synthetic scoped observation; no real visual claim')])) )
    row['quality_evidence']={'checks':{'persistent_objects':dict(scene_state_assessment='changed',source_shot_ids=row['source_shot_ids'],observation='Synthetic exact change inventory',locator='synthetic source')}}
    for label,ref in zip(['start','end'],row['reference_checks']):
        ref['entity_ids']=['ROOM','BAG' if label=='start' else 'WIFE']
        strategy[label+'_facts'].append(dict(fact_id='ROOM',entity_id='ROOM',expected_presence='present',evidence_requirement='visible',source_quote='Fixed room.'))
        ref['endpoint_observations']['facts'].append(dict(fact_id='ROOM',entity_id='ROOM',visibility='visible',observation='Synthetic visible room'))
    row['prompt']+=' Fixed room.'
    return row,[(authority,Path('synthetic.json'),source)],strategy


def test_continuous_same_first_shot_event_is_not_a_fake_split():
    row,sources,_=fixture();r=validate_endpoint_strategy(row,sources,{'endpoint_constraints_verified':True})
    assert r['endpoint_facts'][0]['evidence_scope']=='source_constraint_not_visual'
    assert r['endpoint_facts'][1]['global_absence_verified'] is False
    assert r['per_shot_control'] is r['dynamics_verified'] is r['production_ready'] is False
    assert r['events'][0]['source_shot_ids']==['S1','S2']


@pytest.mark.parametrize('mutation',['no_proof','mode_only','source_revision','source_hash','missing_strategy','wrong_segment',
 'wrong_shots','missing_middle','reverse','wrong_entity','chain_gap','uncovered_shot','event_missing',
 'native_middle_required','implicit_policy','wrong_start','wrong_end','fake_split','no_initial_description',
 'wrong_initial_quote','third_reference','old_role','wrong_ref_state','missing_endpoint_qa','missing_fact',
 'duplicate_fact','foreign_fact_entity','unchanged_assessment','partial_assessment','wrong_boundary_shot','reference_scope_excludes_boundary','reference_outside_segment','unseen_required','unseen_absence','absence_as_present','unknown_visibility','unquoted_fact'])
def test_refuses_unproven_or_downgraded_endpoint_state(mutation):
    row,sources,s=fixture();proof={'endpoint_constraints_verified':True};scene=sources[0][2]['scenes'][0]
    if mutation=='no_proof':proof=None
    elif mutation=='mode_only':row['state_control']={'mode':'first_last'}
    elif mutation=='source_revision':row['state_control']['source_revision']='R1'
    elif mutation=='source_hash':row['state_control']['source_sha256']='b'*64
    elif mutation=='missing_strategy':scene.pop('endpoint_strategies')
    elif mutation=='wrong_segment':s['segment_id']='other'
    elif mutation=='wrong_shots':s['source_shot_ids']=['S1']
    elif mutation=='missing_middle':s['state_ids']=['EXITED']
    elif mutation=='reverse':s['state_ids'].reverse()
    elif mutation=='wrong_entity':s['entity_id']='OTHER'
    elif mutation=='chain_gap':scene['scene_states'][1]['previous_state_id']='OTHER'
    elif mutation=='uncovered_shot':scene['scene_states'][0]['source_shot_ids']=['S1']
    elif mutation=='event_missing':row['prompt']=row['prompt'].replace('Lift the bag.','')
    elif mutation=='native_middle_required':s['required_native_intermediate_states']=['LIFTED']
    elif mutation=='implicit_policy':s.pop('intermediate_control')
    elif mutation=='wrong_start':s['initial_boundary']['state_id']='UNKNOWN'
    elif mutation=='wrong_end':s['final_boundary']['state_id']='LIFTED'
    elif mutation=='fake_split':s['initial_boundary']['shot_id']='S0'
    elif mutation=='no_initial_description':s['initial_boundary']['description']=''
    elif mutation=='wrong_initial_quote':s['initial_boundary']['source_quote']='Other state.'
    elif mutation=='third_reference':row['reference_checks'].append(copy.deepcopy(row['reference_checks'][0]))
    elif mutation=='old_role':row['reference_checks'][0]['roles']=['state_reference']
    elif mutation=='wrong_ref_state':row['reference_checks'][0]['state_id']='LIFTED'
    elif mutation=='missing_endpoint_qa':row['reference_checks'][0].pop('endpoint_observations')
    elif mutation=='missing_fact':row['reference_checks'][0]['endpoint_observations']['facts']=[]
    elif mutation=='duplicate_fact':row['reference_checks'][0]['endpoint_observations']['facts']*=2
    elif mutation=='foreign_fact_entity':row['reference_checks'][0]['entity_ids']=['ROOM']
    elif mutation=='unchanged_assessment':row['quality_evidence']['checks']['persistent_objects']['scene_state_assessment']='unchanged'
    elif mutation=='partial_assessment':row['quality_evidence']['checks']['persistent_objects']['source_shot_ids']=['S1']
    elif mutation=='wrong_boundary_shot':row['reference_checks'][0]['endpoint_observations']['boundary_shot_id']='S2'
    elif mutation=='reference_scope_excludes_boundary':row['reference_checks'][0]['source_shot_ids']=['S2']
    elif mutation=='reference_outside_segment':row['reference_checks'][0]['source_shot_ids']=['S0','S1']
    elif mutation=='unseen_required':s['start_facts'][0]['evidence_requirement']='visible'
    elif mutation=='unseen_absence':s['start_facts'][0]['expected_presence']='absent_from_visible_frame'
    elif mutation=='absence_as_present':row['reference_checks'][0]['endpoint_observations']['facts'][0]['visibility']='absent_from_visible_frame'
    elif mutation=='unknown_visibility':row['reference_checks'][0]['endpoint_observations']['facts'][0]['visibility']='probably'
    elif mutation=='unquoted_fact':s['start_facts'][0]['source_quote']='Not in source.'
    with pytest.raises(ValueError):validate_endpoint_strategy(row,sources,proof)


def test_visibility_and_endpoint_role_changes_invalidate_existing_image_use():
    row,_,_=fixture();ref=row['reference_checks'][0];before=image_use(ref,['S1'])
    ref['endpoint_observations']['facts'][0]['visibility']='visible'
    assert image_use(ref,['S1'])!=before
    ref.pop('endpoint_observations');assert image_use(ref,['S1'])!=before


def test_endpoint_boundary_and_general_appearance_have_distinct_scopes():
    row,sources,_=fixture();ref=row['reference_checks'][0]
    ref.update(roles=['start_frame','identity','appearance'],source_shot_ids=['S1','S2','S3'])
    before=image_use(ref,ref['source_shot_ids'])
    assert validate_endpoint_strategy(row,sources,{'endpoint_constraints_verified':True})['production_ready'] is False
    ref['endpoint_observations']['boundary_shot_id']='S2'
    assert image_use(ref,ref['source_shot_ids'])!=before
    with pytest.raises(ValueError):validate_endpoint_strategy(row,sources,{'endpoint_constraints_verified':True})


def test_source_schema_requires_explicit_strategy_without_weakening_old_scenes():
    import json
    from jsonschema import Draft202012Validator
    schema=json.loads((Path(__file__).resolve().parents[2]/'schemas/storyboard-package.schema.json').read_text())
    validator=Draft202012Validator({'$defs':schema['$defs'],'$ref':'#/$defs/endpointStrategy'})
    _,_,strategy=fixture();assert not list(validator.iter_errors(strategy))
    for key in strategy:
        value=copy.deepcopy(strategy);value.pop(key)
        assert list(validator.iter_errors(value))
    value=copy.deepcopy(strategy);value['required_native_intermediate_states']=['LIFTED']
    assert list(validator.iter_errors(value))
