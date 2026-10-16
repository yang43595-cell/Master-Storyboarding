"""Canonical source to the actual v78 website schema; synthetic projects only."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from scripts.core.web_content import compile_content, validate_web_request, effective_feedback, WebContentError, canonical, CONTRACT
from scripts.tests.web_fixture_bytes import PNG, MP4
from test_story_paragraph_sources import narrative
from test_director_compact import compact_package


def registry(tmp_path):
    result = {}
    for key, data, mime in [('video',MP4,'video/mp4'),('poster',PNG,'image/png'),('AST-CHAR-A@face',PNG,'image/png'),('FRAME@R01',PNG,'image/png'),('fixture/preview.png',PNG,'image/png')]:
        ident = 'object-' + hashlib.sha256(key.encode()).hexdigest()[:10]
        path = tmp_path / ident; path.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        result[key] = {'id':ident,'localPath':str(path),'url':'/api/content-media/'+sha,
            'sha256':sha,'mime':mime,'bytes':len(data),'caption':'仅合成测试',
            'readback':{'mediaVerified':ident,'sha256':sha,'bytes':len(data)}}
    return result


def sources(tmp_path):
    script = narrative(tmp_path); script.pop('human_readable_path'); (tmp_path/'script.md').unlink()
    script['asset_requirements'][0].update(image_decision='需要制作',image_reason='合成角色参考')
    director = compact_package(); director.pop('human_readable_path')
    asset = {'asset_id':'AST-CHAR-A','category':'CHARACTER','name':'合成主角','status':'confirmed',
        'paragraph_scope':['STP-01'],'shot_scope':['S01-010'],'interactions':[],
        'display_surfaces':[],'orientations':[],'required_states':['拒绝'],'must_inherit':['脸部身份'],
        'must_not_inherit':['不得作为服装参考'],'reference_paths':[],'candidates':[],
        'confirmed_asset':{'candidate_id':'AST-CHAR-A-C01','path':'fixture/face.png','user_confirmation_ref':'synthetic'},
        'confirmed_outputs':[{'output_id':'face','purpose':'脸部身份','view':'正面','state':'拒绝',
            'candidate_id':'AST-CHAR-A-C01','path':'fixture/face.png','user_confirmation_ref':'synthetic'}]}
    assets = {'schema_version':'2.0','project_id':'MSB-TEST','revision':'R01',
        'source_narrative':{'artifact_id':'NARRATIVE','logical_id':'narrative.package','revision':'R01','path':'story.json'},
        'source_asset_requirement_ids':['AST-CHAR-A'],'assets':[asset],'scope_gaps':[]}
    prompts = {'project_id':'MSB-TEST','revision':'R01','drafts':[{'node_id':'draft-one',
        'prompt':'主角（合成）说：不。','parameters':{'duration':4},
        'ordered_media':[{'node_id':'frame-node','media_id':'frame-media'}],
        'reference_checks':[{'artifact_id':'FRAME','revision':'R01','sha256':hashlib.sha256(PNG).hexdigest(),'roles':['identity']}]}]}
    return {'script':script,'director':director,'assets':assets,'prompts':prompts}


BENCHMARK = {'schemaVersion':1,'sourceUrl':'https://www.douyin.com/video/1234567890',
    'sourceVideoId':'1234567890','author':'合成测试夹具，非真实对标声明','clipStartSeconds':0,
    'clipEndSeconds':1,'clipLabel':'合成片段','videoMediaId':'benchmark-video','posterMediaId':'benchmark-poster',
    'videoKey':'video','posterKey':'poster'}


def compile_fixture(stage, source, media, case='01', basis=None, **extra):
    return compile_content(canonical(source).encode(),stage=stage,case_id=case,
        source_script_version=basis or hashlib.sha256(('synthetic-legacy-'+case).encode()).hexdigest(),
        expected_version=None,scope='qa',media_registry=media,
        benchmark=BENCHMARK if stage in {'script','assets'} else None,**extra)


@pytest.mark.parametrize('case', ['01','05','06'])
def test_cross_project_goldens_real_schema_and_source_basis(tmp_path, case):
    media = registry(tmp_path); values = sources(tmp_path)
    initial = copy.deepcopy(values)
    script = compile_fixture('script',values['script'],media,case)
    outputs = {'script':script}
    for stage in ['director','assets','prompts']:
        outputs[stage] = compile_fixture(stage,values[stage],media,case,basis=script['version'])
        assert outputs[stage]['request']['content']['sourceScriptVersion'] == script['version']
    golden = json.loads((Path(__file__).with_name('web_content_goldens.json')).read_text())
    assert {stage:item['version'] for stage,item in outputs.items()} == golden[case]
    for stage,item in outputs.items():
        content = item['request']['content']
        raw = next(b for b in content['blocks'] if b['id']=='canonical-source')
        assert json.loads(raw['text']) == values[stage]
        assert item['published'] is False and item['remoteReadbackPerformed'] is False
        assert content['reviewable'] is False
        assert {m['id'] for m in item['mediaMap']} == {m['id'] for b in content['blocks'] for m in b['media']}
        assert all(hashlib.sha256(Path(m['localPath']).read_bytes()).hexdigest()==m['sha256'] for m in item['mediaMap'])
        assert item['contractHashes'] == json.loads((CONTRACT/'PROVENANCE.json').read_text())['files']
    layout = json.loads(next(b for b in outputs['assets']['request']['content']['blocks'] if b['id']=='asset-layout')['text'])
    assert layout['groups'][0]['items'][0]['exclusions'] == ['不得作为服装参考']
    assert layout['groups'][0]['items'][0]['status'] == 'reference'  # Does not manufacture web approval.
    assert values == initial and not list(tmp_path.glob('*.md'))


@pytest.mark.parametrize('stage', ['white_model','storyboard'])
def test_registered_reference_view_is_supported_without_claiming_assets_v2(tmp_path,stage):
    media = registry(tmp_path)
    source = {'project_id':'fixture','revision':'R01','files':[{'path':'fixture/preview.png','sha256':hashlib.sha256(PNG).hexdigest()}]}
    assert compile_fixture(stage,source,media)['request']['content']['stage'] == stage
    with pytest.raises(WebContentError,match='assets-v2'):
        compile_fixture(stage,source,media,reviewable=True)
    with pytest.raises(WebContentError,match='files'):
        compile_fixture(stage,{'revision':'R01'},media)


@pytest.mark.parametrize('change', ['url','hash','readback','missing','mime','benchmark','asset_layout_gap'])
def test_missing_or_contradictory_evidence_is_not_guessed(tmp_path,change):
    media = registry(tmp_path); source = sources(tmp_path)
    stage = 'script'
    if change == 'url': media['video']['url'] = '/public/fake.mp4'
    elif change == 'hash': media['video']['sha256'] = '0'*64
    elif change == 'readback': media['video']['readback'] = {}
    elif change == 'missing': Path(media['video']['localPath']).unlink()
    elif change == 'mime': media['poster']['mime'] = 'image/jpeg'
    elif change == 'benchmark':
        with pytest.raises(WebContentError,match='benchmark'):
            compile_content(canonical(source['script']).encode(),stage='script',case_id='01',source_script_version='a'*64,
                            expected_version=None,scope='qa',media_registry=media)
        return
    else:
        stage = 'assets'; source[stage]['assets'][0].pop('confirmed_outputs')
    with pytest.raises((WebContentError,ValueError)):
        compile_fixture(stage,source[stage],media)


def test_actual_publish_schema_rejects_bound_assets_without_layout(tmp_path):
    item = compile_fixture('assets',sources(tmp_path)['assets'],registry(tmp_path))
    request = item['request']; request['content']['blocks']=[b for b in request['content']['blocks'] if b['id']!='asset-layout']
    with pytest.raises(WebContentError,match='asset-layout'):
        validate_web_request(request)


def test_explicit_head_and_source_version_not_revision_guess(tmp_path):
    media = registry(tmp_path); source = sources(tmp_path)['director']
    first = compile_fixture('director',source,media)
    second = compile_content(canonical(source).encode(),stage='director',case_id='01',
        source_script_version=first['request']['content']['sourceScriptVersion'],expected_version=first['version'],
        scope='qa',media_registry=media)
    assert second['version']==first['version'] and second['request']['expectedVersion']==first['version']
    with pytest.raises(WebContentError):
        compile_content(canonical(source).encode(),stage='director',case_id='01',source_script_version='R01',
                        expected_version=None,scope='qa',media_registry=media)


def state_response():
    version = hashlib.sha256(b'synthetic-current').hexdigest()
    state = {'scope':'qa','caseId':'05','stage':'director','version':version,'currentVersion':version,
             'isCurrent':True,'sourceStale':False,'reviewable':True,'approval':'pending','currentFeedback':None,
             'sourceScriptVersion':hashlib.sha256(b'synthetic-basis').hexdigest(),
             'packageSourceScriptVersion':hashlib.sha256(b'synthetic-basis').hexdigest()}
    return version, {'approval':'pending','currentState':state,'currentFeedback':None,'feedbackPage':{'hasMore':True}}


def test_current_feedback_overrides_default_and_old_history_without_writes():
    version,response = state_response()
    feedback = {'scope':'qa','caseId':'05','stage':'director','version':version,'decision':'changes','id':'current-change'}
    response['currentFeedback']=feedback; response['currentState']['currentFeedback']=feedback
    response['feedback']=[{**feedback,'decision':'approve','id':'old-approval'}]
    original=copy.deepcopy(response)
    result=effective_feedback(response,scope='qa',case_id='05',stage='director',version=version)
    assert result['effectiveApproval']=='changes_requested' and result['historyComplete'] is False
    assert result['currentFeedback']['id']=='current-change' and response==original
    assert effective_feedback({'approval':'pending'},scope='qa',case_id='05',stage='director',version=version)['effectiveApproval']=='unknown'


@pytest.mark.parametrize('field,value,expected',[('sourceStale',True,'stale'),('isCurrent',False,'historical'),('reviewable',False,'not_reviewable')])
def test_current_receipt_never_transfers_across_scope_or_freshness(field,value,expected):
    version,response=state_response();response['currentState'][field]=value
    assert effective_feedback(response,scope='qa',case_id='05',stage='director',version=version)['effectiveApproval']==expected
    response['currentState']['version']='0'*64
    with pytest.raises(WebContentError):effective_feedback(response,scope='qa',case_id='05',stage='director',version=version)


def test_feedback_cannot_hide_source_basis_mismatch_behind_pending_flag():
    version,response=state_response()
    response['currentState']['packageSourceScriptVersion']='0'*64
    assert effective_feedback(response,scope='qa',case_id='05',stage='director',version=version)['effectiveApproval']=='stale'


def test_stdin_entry_compiles_without_creating_another_source_or_posting(tmp_path):
    import subprocess,sys
    media=registry(tmp_path);source=sources(tmp_path)['director']
    path=tmp_path/'canonical.json';path.write_text(canonical(source))
    job={'sourcePath':str(path),'stage':'director','caseId':'06','sourceScriptVersion':hashlib.sha256(b'synthetic-basis').hexdigest(),
         'expectedVersion':None,'scope':'qa','mediaRegistry':media}
    original=path.read_bytes()
    command=[sys.executable,'-m','scripts.core.web_content']
    first=subprocess.run(command,input=canonical(job),text=True,capture_output=True,cwd=Path(__file__).resolve().parents[2])
    assert first.returncode==0,first.stderr
    result=json.loads(first.stdout)
    assert result['published'] is False and result['request']['content']['caseId']=='06'
    assert path.read_bytes()==original
    job.pop('expectedVersion')
    second=subprocess.run(command,input=canonical(job),text=True,capture_output=True,cwd=Path(__file__).resolve().parents[2])
    assert second.returncode==2 and json.loads(second.stderr)['published'] is False


def test_large_unicode_source_is_chunked_losslessly_for_real_utf16_schema(tmp_path):
    source=sources(tmp_path)['script'];source['objective']='合成😀'*15000
    result=compile_fixture('script',source,registry(tmp_path))
    blocks=result['request']['content']['blocks']
    parts=[b['text'] for b in blocks if b['id']=='canonical-source' or b['id'].startswith('canonical-source-part-')]
    assert len(parts)>1 and json.loads(''.join(parts))==source
    assert all(len(b['text'].encode('utf-16-le'))//2<=50000 for b in blocks)
