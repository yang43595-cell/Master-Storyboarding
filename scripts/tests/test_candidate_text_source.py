"""Historical approved visible text is a scoped candidate source, not audio approval."""
import copy
import json
import subprocess
import sys
from pathlib import Path
import pytest
from scripts.core import artifacts, candidate_test as mode, story_sources
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state
from test_candidate_test_mode import setup, sources


def add(root, ident, logical, value, typ=None):
    p=root/(ident+'.json');write_json(p,value)
    artifacts.register_workspace_artifact(root,dict(artifact_id=ident,logical_id=logical,type=typ or logical,
        revision=value.get('revision','R01'),path_or_url=p.name,checksum=artifacts.digest(p)))
    mode.configure(root,'synthetic-run','synthetic user: candidate text test only',mode.context(root)['input_artifact_ids']+[ident])


def fixture(root):
    setup(root);n,b=sources(root)
    (root/'review.json').unlink()
    add(root,'N','narrative.package',n)
    add(root,'A','source.approval',{'reference':'synthetic historical explicit visible text approval'},'source.evidence')
    e={'purpose':'approved_text_candidate_input','source':{'artifact_id':'N','project_id':n['project_id'],
       'revision':n['revision'],'sha256':artifacts.digest(root/'N.json')},'paragraph_ids':['STP-01'],
       'approval':{'reference':'synthetic historical explicit visible text approval','evidence_artifact_id':'A','sha256':artifacts.digest(root/'A.json')}}
    add(root,'E','source.scope',e,'source.evidence')
    return n,b,e


def check(root,n,b):
    return story_sources.validate_story_sources(n,b,project_root=root,candidate_test=True,
        candidate_paragraph_ids=['STP-01'],candidate_source_evidence='E')


def rewrite_registered(root,ident,value):
    write_json(root/(ident+'.json'),value)
    m=artifacts.load_manifest(root)
    next(r for r in m['artifacts'] if r['artifact_id']==ident)['checksum']=artifacts.digest(root/(ident+'.json'))
    artifacts.save_manifest(root,m)


def test_scoped_text_without_historical_audio_keeps_production_blocked(tmp_path):
    n,b,e=fixture(tmp_path)
    before=(tmp_path/'N.json').read_bytes()
    r=check(tmp_path,n,b)
    assert r['dialogue_evidence_status']=='missing_unverified'
    assert not r['source_fidelity_checked'] and not r['human_listening_verified']
    assert r['unresolved_dialogue_ids']==['DLG-01'] and r['production_blocked']
    assert not r['formal_adoption_allowed'] and not r['asset_approval_inferred']
    assert (tmp_path/'N.json').read_bytes()==before
    s=load_project_state(tmp_path);assert not s['current_artifacts'] and not s['user_confirmations']
    with pytest.raises(ValueError):mode.require_production(tmp_path)
    with pytest.raises(ValueError):artifacts.adopt_initial_package(tmp_path,'N','synthetic')
    with pytest.raises(FileNotFoundError):story_sources.validate_story_sources(n,project_root=tmp_path)


@pytest.mark.parametrize('mutation',['source_hash','source_version','source_project','approval_hash','scope','wrong_type','no_checksum','approval_changed','source_changed','unregistered'])
def test_bound_identity_negative(tmp_path,mutation):
    n,b,e=fixture(tmp_path)
    if mutation=='source_hash':e['source']['sha256']='0'*64
    if mutation=='source_version':e['source']['revision']='R99'
    if mutation=='source_project':e['source']['project_id']='OTHER'
    if mutation=='approval_hash':e['approval']['sha256']='0'*64
    if mutation=='scope':e['paragraph_ids']=['STP-03']
    if mutation=='unregistered':e['approval']['evidence_artifact_id']='NOT-ALLOWED'
    rewrite_registered(tmp_path,'E',e)
    if mutation in {'wrong_type','no_checksum'}:
        m=artifacts.load_manifest(tmp_path);row=next(r for r in m['artifacts'] if r['artifact_id']=='A')
        if mutation=='wrong_type':row['type']='narrative.package'
        else:row.pop('checksum')
        artifacts.save_manifest(tmp_path,m)
    if mutation=='approval_changed':(tmp_path/'A.json').write_text('{}')
    if mutation=='source_changed':n['objective']='changed in memory'
    with pytest.raises(ValueError):check(tmp_path,n,b)


@pytest.mark.parametrize('mutation',['speaker','text','scope','formal','wrong_run','historical_hash','historical_dialogue'])
def test_consumption_never_relaxes_existing_contracts(tmp_path,mutation):
    n,b,e=fixture(tmp_path)
    if mutation=='speaker':b['scenes'][0]['shots'][0]['audio']['dialogue'][0]['speaker_id']='OTHER'
    if mutation=='text':b['scenes'][0]['shots'][0]['audio']['dialogue'][0]['text']='改词'
    if mutation=='scope':b['source_paragraph_ids'].append('STP-03')
    if mutation=='formal':
        with pytest.raises(ValueError):story_sources.validate_story_sources(n,b,project_root=tmp_path,candidate_source_evidence='E')
        return
    if mutation=='wrong_run':
        from scripts.core.project_state import write_project_state
        s=load_project_state(tmp_path);s['candidate_test']['project_path']=str(tmp_path/'other');write_project_state(tmp_path,s)
    if mutation=='historical_hash':write_json(tmp_path/'review.json',{})
    if mutation=='historical_dialogue':
        # Valid raw source/review structure, but disagrees with the approved narrative.
        other=copy.deepcopy(n);other['dialogues'][0]['text']='是。'
        record={'sources':[{'source_id':'S','kind':'subtitle_verbatim','text':'是。','evidence_path':'raw.json',
          'evidence_sha256':artifacts.digest(tmp_path/'raw.json'),'locator':'fixture','producer':'fixture'}],
          'dialogues':[{'dialogue_id':'DLG-01','speaker_id':'CHR-A','text':'是。','source_ids':['S']}]}
        write_json(tmp_path/'review.json',record);n['dialogue_evidence_sha256']=artifacts.digest(tmp_path/'review.json')
        rewrite_registered(tmp_path,'N',n);e['source']['sha256']=artifacts.digest(tmp_path/'N.json');rewrite_registered(tmp_path,'E',e)
    with pytest.raises(ValueError):check(tmp_path,n,b)


def test_real_cli_explicit_scoped_candidate(tmp_path):
    n,b,e=fixture(tmp_path);write_json(tmp_path/'B.json',b)
    p=subprocess.run([sys.executable,'-m','scripts.core.story_sources','--project',str(tmp_path),
      '--narrative',str(tmp_path/'N.json'),'--storyboard',str(tmp_path/'B.json'),'--candidate-test',
      '--candidate-paragraph-id','STP-01','--candidate-source-evidence','E'],capture_output=True,text=True,
      cwd=Path(__file__).resolve().parents[2])
    assert p.returncode==0,p.stderr
    assert 'missing_unverified' in p.stdout and '"production_blocked": true' in p.stdout


@pytest.mark.parametrize('missing_explicit_scope',[False,True])
def test_known_paragraph_outside_scope_rejected(tmp_path,missing_explicit_scope):
    n,b,e=fixture(tmp_path)
    n['story_paragraphs'].append({'paragraph_id':'STP-02','text':'另一个真实段落。'})
    rewrite_registered(tmp_path,'N',n);e['source']['sha256']=artifacts.digest(tmp_path/'N.json');rewrite_registered(tmp_path,'E',e)
    b['source_paragraph_ids'].append('STP-02')
    with pytest.raises(ValueError,match='范围|来源'):
        story_sources.validate_story_sources(n,b,project_root=tmp_path,candidate_test=True,
            candidate_paragraph_ids=None if missing_explicit_scope else ['STP-01','STP-02'],candidate_source_evidence='E')


@pytest.mark.parametrize('flag',['--dialogue-review','--markdown','--shot-plan','--reading-supplement','no-candidate-test'])
def test_cli_never_silently_ignores_source_evidence(tmp_path,flag):
    fixture(tmp_path)
    args=[sys.executable,'-m','scripts.core.story_sources','--project',str(tmp_path),
          '--narrative',str(tmp_path/'N.json'),'--candidate-source-evidence','E']
    if flag in {'--dialogue-review','--markdown','--shot-plan'}:
        pos=args.index('--narrative');del args[pos:pos+2]
    if flag!='no-candidate-test':
        args+=['--candidate-test',flag]
        if flag!='--reading-supplement':args+=[str(tmp_path/'A.json')]
    p=subprocess.run(args,capture_output=True,text=True,cwd=Path(__file__).resolve().parents[2])
    assert p.returncode==2 and '仅用于' in p.stderr
