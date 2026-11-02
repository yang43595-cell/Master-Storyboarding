"""Isolated detailed text/JSON tests. No image/video, network, or approvals.

These check declared contracts, not semantic understanding or provenance.
"""
import copy
import hashlib
import json

import pytest
from scripts.core import quality_evidence as q
from scripts.core.prompt_body import submitted_prompt_body
from scripts.tests.test_native_binding_prompt_aliases import verified_payload, BODY


@pytest.mark.parametrize('body', [BODY, ' 前后空格保留 ', '\n多行\n文本\n', 'é', 'e\u0301', '正文\r\n第二行'])
@pytest.mark.parametrize('fields', ['prompt', 'prompt_text', 'both'])
def test_exact_alias_body_is_preserved_without_normalization(tmp_path, body, fields):
    bodies = {k: body for k in (['prompt', 'prompt_text'] if fields == 'both' else [fields])}
    payload = verified_payload(tmp_path, bodies)
    path = tmp_path / 'SYNTHETIC-readback.json'
    receipt = json.loads(path.read_text()); receipt['drafts'][0]['prompt'] = body
    path.write_text(json.dumps(receipt), encoding='utf-8')
    payload['generation_segments'][0]['native_binding']['evidence_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    before = copy.deepcopy(payload); receipt_before = path.read_bytes()
    assert submitted_prompt_body(bodies) == body
    q.validate_native_binding_claims(payload, tmp_path)
    assert payload == before and path.read_bytes() == receipt_before


@pytest.mark.parametrize('bad', ['', ' ', '\t\n', None, 0, False, [], {}, ['text']])
@pytest.mark.parametrize('field', ['prompt', 'prompt_text'])
def test_every_provided_invalid_alias_blocks_even_with_valid_other(tmp_path, bad, field):
    bodies = {'prompt': BODY, 'prompt_text': BODY}; bodies[field] = bad
    payload = verified_payload(tmp_path, bodies)
    before = copy.deepcopy(payload)
    with pytest.raises(ValueError): q.validate_native_binding_claims(payload, tmp_path)
    assert payload == before


@pytest.mark.parametrize('a,b', [('é','e\u0301'), ('a\nb','a\r\nb'), ('a','a '), ('汉字','漢字')])
def test_different_bytes_never_guess_alias_authority(tmp_path, a, b):
    payload = verified_payload(tmp_path, {'prompt': a, 'prompt_text': b})
    # Make the canonical receipt agree with prompt so an unrelated mismatch
    # cannot hide the old bug that ignored a conflicting prompt_text.
    rewrite_receipt(tmp_path, payload, lambda d: d['drafts'][0].update(prompt=a))
    with pytest.raises(ValueError, match='不一致'): q.validate_native_binding_claims(payload, tmp_path)


def rewrite_receipt(tmp_path, payload, change):
    path = tmp_path / 'SYNTHETIC-readback.json'
    data = json.loads(path.read_text()); change(data)
    path.write_text(json.dumps(data), encoding='utf-8')
    payload['generation_segments'][0]['native_binding']['evidence_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('mutation', ['phase','revision','target','duplicate_node','missing_node',
    'parameter','media','editable_entity','entity_type','missing_mentions','canonical_missing','stale_receipt_sha'])
def test_alias_does_not_relax_readback_contract(tmp_path, mutation):
    payload = verified_payload(tmp_path, {'prompt_text': BODY})
    def change(d):
        r = d['drafts'][0]
        if mutation == 'phase': d['phase'] = 'before_refresh'
        elif mutation == 'revision': d['revision'] = 'OTHER'
        elif mutation == 'target': d['target_url'] = 'https://example.com/other'
        elif mutation == 'duplicate_node': d['drafts'].append(copy.deepcopy(r))
        elif mutation == 'missing_node': r['node_id'] = 'OTHER'
        elif mutation == 'parameter': r['parameters']['duration'] = 6
        elif mutation == 'media': r['ordered_media'][0]['media_id'] = 'OTHER'
        elif mutation == 'editable_entity': r['native_mentions'][0]['native_noneditable_entity'] = False
        elif mutation == 'entity_type': r['native_mentions'][0]['entity_type'] = 'text'
        elif mutation == 'missing_mentions': r.pop('native_mentions')
        elif mutation == 'canonical_missing': r['prompt_text'] = r.pop('prompt')
    rewrite_receipt(tmp_path, payload, change)
    if mutation == 'stale_receipt_sha':
        payload['generation_segments'][0]['native_binding']['evidence_sha256'] = '0' * 64
    with pytest.raises(ValueError): q.validate_native_binding_claims(payload, tmp_path)


@pytest.mark.parametrize('status', [None, 'unknown', 'not_bound', 'unverified', 'not_checked', 'stale'])
def test_unverified_candidate_is_not_upgraded_and_is_not_alias_schema(status, tmp_path):
    # Document the intentional scope boundary: only verified claims are bound.
    row = {'prompt': BODY, 'prompt_text': 'conflict', 'native_binding': {'status': status}}
    before = copy.deepcopy(row)
    q.validate_native_binding_claims(row, tmp_path)
    assert row == before
    with pytest.raises(ValueError): submitted_prompt_body(row)


def evidence(aspects):
    return {'status':'pass', 'observer':'SYNTHETIC TEXT ONLY', 'checked_at':'2026-10-26T18:00:00Z',
        'checks': {k:{'result':'pass','observation':'test declaration only','locator':'synthetic JSON'} for k in aspects}}


@pytest.mark.parametrize('aspect', q.IMAGE_ASPECTS)
@pytest.mark.parametrize('result', ['fail','unknown'])
def test_failed_or_unknown_whole_image_never_excused_by_limited_use(aspect, result):
    e = evidence(q.IMAGE_ASPECTS); e['checks'][aspect]['result'] = result
    e['limited_use'] = {'purpose':'only_prop', 'excluded_roles':[aspect]}
    with pytest.raises(ValueError, match=aspect): q._observations(e, q.IMAGE_ASPECTS)


def state(ids=None):
    return {'state_id':'SERVED','entity_id':'ROOM','previous_state_id':'EMPTY',
        'change_event':'Food is served.','description':'Food remains on the table.',
        'preserve':['Architecture.'],'changes':['Food on table.'],'source_shot_ids':ids or ['S2','S3']}


def row():
    return {'source_shots':['S2','S3'], 'reference_checks':[{'kind':'image','role':'state_reference',
        'state_id':'SERVED','entity_ids':['ROOM']}], 'quality_evidence':{'checks':{'persistent_objects':{
        'scene_state_assessment':'changed','source_shot_ids':['S2','S3'], 'observation':'Declared table change.', 'locator':'S2-S3'}}}}


def test_declared_state_coverage_accepts_only_consistency_and_does_not_mutate():
    r = row(); before = copy.deepcopy(r)
    q.validate_scene_state_references(r, [state()])
    assert r == before and 'user_approved' not in r and 'native_binding' not in r


@pytest.mark.parametrize('mutation', ['old_state','missing_ref','text_only','wrong_entity','partial_reference',
    'extra_old_layout','extra_old_appearance','before_and_after','two_states','assessment_missing','assessment_unchanged',
    'assessment_wrong_shots','source_mapping_missing','source_mapping_conflict'])
def test_declared_state_reference_rejects_scope_and_phase_mutations(mutation):
    r = row(); needed = [state()]
    if mutation == 'old_state': r['reference_checks'][0]['state_id'] = 'EMPTY'
    elif mutation == 'missing_ref': r['reference_checks'] = []
    elif mutation == 'text_only': r['reference_checks'][0]['kind'] = 'text'
    elif mutation == 'wrong_entity': r['reference_checks'][0]['entity_ids'] = ['OTHER']
    elif mutation == 'partial_reference': r['reference_checks'][0]['source_shot_ids'] = ['S2']
    elif mutation.startswith('extra_old_'):
        r['reference_checks'].append({'kind':'image','role':mutation.removeprefix('extra_old_'), 'state_id':'EMPTY','entity_ids':['ROOM']})
    elif mutation == 'before_and_after': r['source_shots']=['S1','S2','S3'];r['quality_evidence']['checks']['persistent_objects']['source_shot_ids']=r['source_shots']
    elif mutation == 'two_states': needed=[state(['S2']),dict(state(['S3']),state_id='CLEARED',previous_state_id='SERVED')]
    elif mutation == 'assessment_missing': r['quality_evidence']['checks']['persistent_objects'].pop('scene_state_assessment')
    elif mutation == 'assessment_unchanged': r['quality_evidence']['checks']['persistent_objects']['scene_state_assessment']='unchanged'
    elif mutation == 'assessment_wrong_shots': r['quality_evidence']['checks']['persistent_objects']['source_shot_ids']=['S2']
    elif mutation == 'source_mapping_missing': r.pop('source_shots')
    else: r['source_shot_ids']=['S3','S2']
    with pytest.raises(ValueError, match='scene_state'): q.validate_scene_state_references(r, needed)


@pytest.mark.parametrize('mutation', ['same_previous','wrong_predecessor','reversed_shots','unknown_shot','duplicate_shot','overlap','empty_preserve','empty_changes'])
def test_declared_state_chain_is_checked_without_media(mutation):
    states = [state(['S2']),dict(state(['S3']), state_id='CLEARED',previous_state_id='SERVED')]
    if mutation == 'same_previous': states[0]['previous_state_id']='SERVED'
    elif mutation == 'wrong_predecessor': states[1]['previous_state_id']='OTHER'
    elif mutation == 'reversed_shots': states=[state(['S3','S2'])]
    elif mutation == 'unknown_shot': states[0]['source_shot_ids']=['S99']
    elif mutation == 'duplicate_shot': states[0]['source_shot_ids']=['S2','S2']
    elif mutation == 'overlap': states[1]['source_shot_ids']=['S2','S3']
    elif mutation == 'empty_preserve': states[0]['preserve']=[]
    else: states[0]['changes']=[]
    payload = {'scenes':[{'shots':[{'shot_id':x} for x in ['S1','S2','S3']], 'scene_states':states}]}
    with pytest.raises(ValueError,match='scene_state'): q.scene_state_requirements(payload)


@pytest.mark.parametrize('field', ['prompt','entry_state','exit_state','source_shots','parameters','ordered_media'])
def test_changed_input_invalidates_review_without_approving_new_text(field):
    d={'prompt':'The box is already placed.','entry_state':'Table holds box.','exit_state':'Hands free.',
       'source_shots':['S1'],'parameters':{'duration':5},'ordered_media':[{'node_id':'M1','media_id':'I1'}]}
    d['quality_evidence']=evidence(q.DRAFT_ASPECTS)
    d['quality_evidence']['checks']['packaging_identity'].update(scope='present', result='pass', items=[
        dict(entity_id='SYNTHETIC-BOX', classification='ordinary_prop', source_basis='Synthetic ordinary storage prop',
             source_shot_ids=['S1'], prompt_claim='ordinary_prop')])
    d['quality_evidence']['input_sha256']=q.fingerprint(q.draft_binding(d))
    assert q.validate_draft_evidence(d)['verification']=='evidence_consistency_only'
    d[field]={'changed':True}
    with pytest.raises(ValueError,match='旧检查失效'): q.validate_draft_evidence(d)


def test_fresh_hash_and_pass_declarations_do_not_become_semantic_or_visual_proof():
    d={'prompt':'She is already standing. She now stands up from the same chair again.',
       'entry_state':'Standing.','exit_state':'Seated.','quality_evidence':evidence(q.DRAFT_ASPECTS)}
    d['quality_evidence']['checks']['packaging_identity'].update(scope='none', items=[], result='not_applicable')
    d['quality_evidence']['input_sha256']=q.fingerprint(q.draft_binding(d))
    assert q.validate_draft_evidence(d)=={'verification':'evidence_consistency_only','visual_quality_verified':False}
