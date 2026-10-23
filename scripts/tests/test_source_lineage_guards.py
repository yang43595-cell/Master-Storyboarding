"""Synthetic source separation and adopted dependency regression; no case data."""
import copy
import hashlib
import json
import pytest
from scripts.core import artifacts, story_sources
from scripts.core.io import write_json


def evidence(root):
    rows=[]
    for ident,text,kind in [('S','字甲','subtitle_verbatim'),('M','字乙','asr_hypothesis'),('N','字丙','asr_hypothesis')]:
        path=root/(ident+'.json');write_json(path,{'text':text})
        rows.append(dict(source_id=ident,kind=kind,text=text,evidence_path=path.name,
                         evidence_sha256=artifacts.digest(path),text_pointer='/text',
                         locator='synthetic frame/segment',producer='synthetic reader/model'))
    return {'sources':rows,'dialogues':[{'dialogue_id':'DLG-01','text':'字甲','source_ids':['S','M','N']}]}


def test_conflicting_sources_are_reported_not_repaired(tmp_path):
    data=evidence(tmp_path);before=copy.deepcopy(data)
    report=story_sources.validate_dialogue_review(data,tmp_path)
    assert report['conflicts']==['DLG-01'] and data==before
    assert report['verified_timing_ids']==[]


def test_asr_agreement_is_not_confirmation(tmp_path):
    data=evidence(tmp_path);data['dialogues'][0]['source_ids']=['M'];data['dialogues'][0]['text']='字乙'
    report=story_sources.validate_dialogue_review(data,tmp_path)
    assert report['verification']=='source_consistency_only'
    assert report['unresolved_ids']==['DLG-01']


def test_raw_asr_text_cannot_be_silently_rewritten(tmp_path):
    data=evidence(tmp_path);data['sources'][1]['text']='字甲'
    with pytest.raises(ValueError,match='原始证据'):
        story_sources.validate_dialogue_review(data,tmp_path)


def test_editorial_change_needs_exact_decision(tmp_path):
    data=evidence(tmp_path);data['dialogues'][0]['text']='新解释'
    with pytest.raises(ValueError,match='解释修订'):
        story_sources.validate_dialogue_review(data,tmp_path)
    data['dialogues'][0]['editorial_proposal']={'proposed_text':'新解释','reason':'synthetic', 'source_ids':['S'],'original_text':'字甲'}
    assert story_sources.validate_dialogue_review(data,tmp_path)['unresolved_ids']==['DLG-01']


def test_timestamp_claim_needs_verification(tmp_path):
    data=evidence(tmp_path);row=data['dialogues'][0]
    row['source_locator']={'start':1,'end':2}
    assert story_sources.validate_dialogue_review(data,tmp_path)['verified_timing_ids']==[]
    row['verified_timing']={'start':1,'end':2}
    with pytest.raises(ValueError,match='时码'):
        story_sources.validate_dialogue_review(data,tmp_path)


def test_punctuation_normalization_preserves_numbers():
    assert story_sources._dialogue_comparison_text('甲， 3。')=='甲3'
    assert story_sources._dialogue_comparison_text('三')!=story_sources._dialogue_comparison_text('3')


def dependency(root):
    a=root/'source.json';b=root/'board.json'
    write_json(a,{'revision':'R02'});write_json(b,{'source_narrative':{'revision':'R01','path':'old.json'}})
    src={'artifact_id':'N2','logical_id':'narrative.package','revision':'R02','status':'current','user_approved':True,'path_or_url':a.name,'checksum':artifacts.digest(a)}
    board={'artifact_id':'B','logical_id':'storyboard.package','revision':'R07','status':'current','user_approved':True,'path_or_url':b.name,'checksum':artifacts.digest(b),'depends_on':['narrative.package']}
    return board,{'artifacts':[src,board]},{'user_confirmations':[{'artifact_ids':['N2','B']}]}


def test_missing_current_upstream_cannot_fall_back_to_history(tmp_path):
    board,manifest,state=dependency(tmp_path);manifest['artifacts'][0]['status']='superseded'
    with pytest.raises(ValueError,match='current'):
        artifacts.validate_current_dependencies(board,manifest,state,tmp_path)


def test_embedded_source_revision_must_match_current_upstream(tmp_path):
    board,manifest,state=dependency(tmp_path)
    with pytest.raises(ValueError,match='来源修订'):
        artifacts.validate_current_dependencies(board,manifest,state,tmp_path)


def test_matching_dependency_does_not_require_equal_downstream_revision(tmp_path):
    board,manifest,state=dependency(tmp_path)
    write_json(tmp_path/'board.json',{'source_narrative':{'revision':'R02','path':'source.json'}})
    artifacts.validate_current_dependencies(board,manifest,state,tmp_path)


def test_handoff_checks_real_dependency_entrypoint(tmp_path):
    from scripts.stage_context_cli import _confirmed_project_inputs, StageContextError
    board,manifest,state=dependency(tmp_path)
    state['current_artifacts']=['B'];manifest['artifacts'][0]['status']='superseded'
    write_json(tmp_path/artifacts.MANIFEST_PATH,manifest)
    with pytest.raises(StageContextError,match='current'):
        _confirmed_project_inputs(tmp_path,state)


def test_submission_checks_real_dependency_entrypoint(tmp_path):
    from scripts.tests.test_delivery_preflight import delivery
    from scripts.canvas_readback import validate_submission
    frozen,_=delivery(tmp_path)
    manifest=artifacts.load_manifest(tmp_path)
    manifest['artifacts'][1]['depends_on']=['missing.upstream']
    write_json(tmp_path/artifacts.MANIFEST_PATH,manifest)
    with pytest.raises(ValueError,match='current'):
        validate_submission(frozen,tmp_path)


def test_narrative_binding_blocks_unverified_and_changed_evidence(tmp_path):
    from test_story_paragraph_sources import narrative
    package=narrative(tmp_path);record=evidence(tmp_path)
    row=record['dialogues'][0];row['text']='不。';row['source_ids']=['S']
    record['sources'][0]['text']='不。'
    write_json(tmp_path/'S.json',{'text':'不。'});record['sources'][0]['evidence_sha256']=artifacts.digest(tmp_path/'S.json')
    path=tmp_path/'review.json';write_json(path,record)
    package.update(dialogue_evidence_path='review.json',dialogue_evidence_sha256=artifacts.digest(path))
    with pytest.raises(ValueError,match='未核'):
        story_sources.validate_story_sources(package,project_root=tmp_path)
    row['verification']={'scope':'dialogue','dialogue_id':'DLG-01','text':'不。','method':'user_confirmation','reference':'synthetic receipt only','reviewer':'synthetic user'}
    write_json(path,record)
    with pytest.raises(ValueError,match='字节'):
        story_sources.validate_story_sources(package,project_root=tmp_path)
    package['dialogue_evidence_sha256']=artifacts.digest(path)
    story_sources.validate_story_sources(package,project_root=tmp_path)


@pytest.mark.parametrize('mutation',['duplicate','cycle','checksum'])
def test_bad_dependency_chain_is_rejected(tmp_path,mutation):
    board,manifest,state=dependency(tmp_path)
    write_json(tmp_path/'board.json',{'source_narrative':{'revision':'R02','path':'source.json'}})
    if mutation=='duplicate':manifest['artifacts'].append(copy.deepcopy(manifest['artifacts'][0]))
    elif mutation=='cycle':manifest['artifacts'][0]['depends_on']=['storyboard.package'];board['checksum']=artifacts.digest(tmp_path/'board.json')
    else:(tmp_path/'source.json').write_text('{}')
    with pytest.raises(ValueError):artifacts.validate_current_dependencies(board,manifest,state,tmp_path)


@pytest.mark.parametrize('field',['artifact_id','logical_id'])
def test_explicit_source_identity_must_match_current(tmp_path,field):
    board,manifest,state=dependency(tmp_path)
    ref={'revision':'R02','path':'source.json',field:'WRONG'}
    write_json(tmp_path/'board.json',{'source_narrative':ref})
    with pytest.raises(ValueError,match='来源身份'):
        artifacts.validate_current_dependencies(board,manifest,state,tmp_path)


@pytest.mark.parametrize('field',['reference','reviewer'])
def test_boolean_is_not_traceable_verification(tmp_path,field):
    data=evidence(tmp_path)
    data['dialogues'][0]['verification']={'scope':'dialogue','dialogue_id':'DLG-01','text':'字甲','method':'user_confirmation','reference':'synthetic','reviewer':'synthetic',field:True}
    with pytest.raises(ValueError,match='核对记录'):
        story_sources.validate_dialogue_review(data,tmp_path)
