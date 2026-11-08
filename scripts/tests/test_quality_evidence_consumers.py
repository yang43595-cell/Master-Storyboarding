"""Synthetic evidence tests: no fixture constitutes a real visual/user approval."""
import base64
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import canvas_readback
from scripts.core import asset_refs, artifacts, quality_evidence as q
from scripts.core.io import write_json
from scripts.core.project_state import new_project_state


def observations(aspects):
    return dict(status='pass', observer='SYNTHETIC TEST ONLY', checked_at='2026-10-26T00:00:00Z',
                checks={k: dict(result='pass', observation='synthetic observation, not visual proof',
                                locator='synthetic fixture') for k in aspects})


def fixture(root):
    (root/'image.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
    write_json(root/'source.json', {'synthetic': True})
    sha = artifacts.digest(root/'image.png')
    evidence = observations(q.IMAGE_ASPECTS)
    evidence.update(image_sha256=sha, source_files=[{'path':'source.json','sha256':artifacts.digest(root/'source.json')}],
                    source_shot_ids=['S1'],view={'resolution':'original','locator':'synthetic pixel'})
    url='https://example.com/canvas/test'
    entry=dict(artifact_id='IMG',logical_id='image.test',type='visual-supplement-image',revision='R1',
               path_or_url='image.png',checksum=sha,status='current',user_approved=True,depends_on=[],
               confirmation_ref='confirmation.json',quality_evidence=evidence,
               upload_bindings=[dict(target_url=url,node_id='M1',media_id='MEDIA',revision='R1',sha256=sha)])
    write_json(root/'confirmation.json',dict(scope='artifact',artifact_ids=['IMG'],image_path='image.png',
                                          image_sha256=sha,usage={'source_shot_ids':['S1']}))
    manifest={'artifacts':[entry]}
    write_json(root/artifacts.MANIFEST_PATH,manifest)
    state=new_project_state();state.update(current_artifacts=['IMG'],user_confirmations=[{'scope':'artifact','artifact_ids':['IMG'],'approval_ref':'SYNTHETIC ONLY'}])
    write_json(root/'00_项目管理/project_state.json',state)
    draft=dict(node_id='D1',prompt='Synthetic action.',parameters={'duration':5},native_entity_type='media',
               duration_bounds=[1,10],shot_durations=[5],source_shot_ids=['S1'],ordered_media=[{'node_id':'M1','media_id':'MEDIA'}],
               required_entities=['E1'],required_reference_roles={'E1':['identity']},continuity={'event_id':'E'},
               reference_checks=[dict(artifact_id='IMG',revision='R1',sha256=sha,kind='image',roles=['identity'],entity_ids=['E1'])])
    entry['quality_evidence']['uses']=[q.image_use(draft['reference_checks'][0], ['S1'])]
    write_json(root/artifacts.MANIFEST_PATH,manifest)
    draft['quality_evidence']=observations(q.DRAFT_ASPECTS)
    draft['quality_evidence']['checks']['packaging_identity'].update(scope='none', items=[], result='not_applicable')
    draft['quality_evidence']['input_sha256']=q.fingerprint(q.draft_binding(draft))
    frozen=dict(target_url=url,revision='R1',source_artifacts=[dict(artifact_id='IMG',revision='R1',sha256=sha)],drafts=[draft])
    return frozen,manifest


def test_real_consumers_positive_is_only_consistency(tmp_path):
    frozen,_=fixture(tmp_path)
    report=canvas_readback.validate_submission(frozen,tmp_path)
    assert report['ok'] and report['visual_quality_verified'] is False
    assert report['generation_input_provenance']=='unknown'
    assert asset_refs.require_references(tmp_path,['IMG'],['S1'])==(True,[])


@pytest.mark.parametrize('mutation', ['missing','fail','unknown','source_hash','image_hash','scope','no_locator'])
def test_real_image_consumers_reject(tmp_path, mutation):
    frozen,manifest=fixture(tmp_path);e=manifest['artifacts'][0]['quality_evidence']
    if mutation=='missing': manifest['artifacts'][0].pop('quality_evidence')
    elif mutation in {'fail','unknown'}: e['checks']['product_phase']['result']=mutation
    elif mutation=='source_hash': e['source_files'][0]['sha256']='0'*64
    elif mutation=='image_hash': e['image_sha256']='0'*64
    elif mutation=='scope': e['source_shot_ids']=['S2']
    else: e['checks']['hands_support'].pop('locator')
    write_json(tmp_path/artifacts.MANIFEST_PATH,manifest)
    with pytest.raises(ValueError,match='quality_evidence'):
        canvas_readback.validate_submission(frozen,tmp_path)
    ok, errors=asset_refs.require_references(tmp_path,['IMG'],['S1'])
    assert not ok and any('quality_evidence' in x for x in errors)


@pytest.mark.parametrize('mutation',['missing','fail','unknown','text','parameters','reference','adjacent'])
def test_real_draft_consumer_rejects_stale_or_missing(tmp_path,mutation):
    frozen,_=fixture(tmp_path);d=frozen['drafts'][0]
    if mutation=='missing': d.pop('quality_evidence')
    elif mutation in {'fail','unknown'}: d['quality_evidence']['checks']['action_chain']['result']=mutation
    elif mutation=='text': d['prompt']+=' changed'
    elif mutation=='parameters': d['parameters']['duration']=6
    elif mutation=='reference': d['ordered_media'][0]['media_id']='changed'
    else: d['continuity']['previous_node_id']='changed'
    with pytest.raises(ValueError,match='quality_evidence'):
        canvas_readback.validate_submission(frozen,tmp_path)


def test_cli_fails_without_mutating_files(tmp_path):
    frozen,_=fixture(tmp_path);frozen['drafts'][0].pop('quality_evidence')
    target=tmp_path/'frozen.json';write_json(target,frozen)
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result=subprocess.run([sys.executable,'-m','scripts.canvas_readback','--delivery','--preflight',
                           '--project',str(tmp_path),'--frozen',str(target)],capture_output=True,text=True)
    assert result.returncode!=0 and 'quality_evidence' in result.stdout+result.stderr
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def required_shot_submission(tmp_path, *, source_seconds=5, declared_total=None):
    frozen, manifest = fixture(tmp_path)
    row = frozen['drafts'][0]
    clause = 'Keep the lantern flame and the striker together in close view until ignition.'
    segment = {'segment_id': 'SEG', 'prompt': clause, 'source_shot_ids': ['S1'], 'shot_durations': [source_seconds]}
    if declared_total is not None:
        segment['duration_seconds'] = declared_total
    source_files = {
        'BOARD': {'scenes': [{'scene_id': 'ROOM', 'shots': [{'shot_id': 'S1', 'required_visuals': [
            {'prompt_clause': clause, 'min_seconds': 3}]}]}]},
        'VIDEO': {'source_storyboard': {'path': 'board.json', 'revision': 'R1'}, 'generation_segments': [segment]},
    }
    state_path = tmp_path / '00_项目管理/project_state.json'
    state = json.loads(state_path.read_text())
    for ident, payload in source_files.items():
        path = tmp_path / (ident.lower() + '.json')
        write_json(path, payload)
        reference = dict(artifact_id=ident, revision='R1', sha256=artifacts.digest(path))
        frozen['source_artifacts'].append(reference)
        logical_id = 'storyboard.package' if ident == 'BOARD' else 'synthetic.video'
        manifest['artifacts'].append(dict(reference, logical_id=logical_id, type='document',
            path_or_url=path.name, checksum=reference['sha256'], status='current', user_approved=True,
            confirmation_ref='confirmation.json', depends_on=[]))
        state['current_artifacts'].append(ident)
        state['user_confirmations'][0]['artifact_ids'].append(ident)
    write_json(state_path, state)
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    row.update(source_segment_id='SEG', prompt=clause)
    row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))
    return frozen


def test_formal_preflight_checks_required_shot_in_actual_prompt_even_with_fresh_evidence(tmp_path):
    frozen = required_shot_submission(tmp_path)
    assert canvas_readback.validate_submission(frozen, tmp_path)['ok']

    row = frozen['drafts'][0]
    row['prompt'] = 'The lantern is already lit.'
    row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))
    with pytest.raises(ValueError, match='required_visuals: S1.*正文缺少'):
        canvas_readback.validate_submission(frozen, tmp_path)


@pytest.mark.parametrize('source_seconds', [5, 6], ids=['matching', 'impossible'])
def test_formal_preflight_checks_linked_source_shot_schedule_against_declared_total(tmp_path, source_seconds):
    frozen = required_shot_submission(tmp_path, source_seconds=source_seconds, declared_total=5)
    if source_seconds == 6:
        with pytest.raises(ValueError, match=r'SEG.*(?:片段总长|逐镜合计)'):
            canvas_readback.validate_submission(frozen, tmp_path)
    else:
        assert canvas_readback.validate_submission(frozen, tmp_path)['ok']


def test_historical_compare_not_upgraded(tmp_path):
    frozen,_=fixture(tmp_path)
    legacy={k:copy.deepcopy(frozen[k]) for k in ('target_url','drafts')}
    for d in legacy['drafts']: d.pop('quality_evidence')
    observed=copy.deepcopy(legacy);observed.update(phase='after_refresh',frozen_sha256=canvas_readback.digest(legacy))
    observed['drafts'][0]['native_mentions']=[dict(node_id='M1',media_id='MEDIA',entity_type='media',native_noneditable_entity=True)]
    report=canvas_readback.compare(legacy,observed)
    assert report['submission_checks']=='not_checked'


def preview_fixture(root, *, image=True, historical_failure=False):
    from scripts.core import candidate_test
    frozen,manifest=fixture(root)
    write_json(root/'00_项目管理/project_state.json',new_project_state())
    entry=manifest['artifacts'][0];entry.update(status='candidate',user_approved=False)
    entry.pop('confirmation_ref');write_json(root/artifacts.MANIFEST_PATH,manifest)
    candidate_test.configure(root,'synthetic-run','SYNTHETIC TEST ONLY',['IMG'])
    manifest=artifacts.load_manifest(root);entry=manifest['artifacts'][0]
    row=frozen['drafts'][0];row.pop('quality_evidence')
    ref=row['reference_checks'][0]
    ref.update(project_path=str(root),record_sha256=entry['checksum'],path=str(root/'image.png'))
    ref['limited_use']=dict(conclusion='usable_for_private_preview',reviewer='SYNTHETIC',review_ref='SYNTHETIC',
        observation='SYNTHETIC only',draft_node_ids=['D1'],roles=['identity'],entity_ids=['E1'],excluded_roles=['layout'],failure_scope_note='Cannot waive failure')
    ref['upload_binding']=dict(target_url=frozen['target_url'],target_project_id='P',node_id='M1',media_id='MEDIA',revision='R1',sha256=entry['checksum'])
    if historical_failure:
        history=root/'history.json';write_json(history,{'image':{'path':str(root/'image.png'),'sha256':entry['checksum']},'outcome':'failed_candidate'})
        historic={'location':str(history),'sha256':artifacts.digest(history)}
        entry.update(type='preview-reference',logical_id='preview.reference.synthetic',
                     preview_origin={'purpose':'private_preview_only_not_generation_baseline','source_sha256':entry['checksum'],
                                     'history_gaps':[],'historical_evidence':[historic]})
        ref['limited_use']['review_evidence']=historic
        write_json(root/artifacts.MANIFEST_PATH,manifest)
    frozen['source_artifacts']=[copy.deepcopy(ref)]
    if not image:
        row.update(ordered_media=[],reference_checks=[],required_entities=[],required_reference_roles={})
    frozen.update(target_project_id='P',status='candidate',review_status='unreviewed',production_blocked=True,operations=['save_drafts'])
    frozen['preview_authorization']=dict(mode='private_candidate_preview',project_path=str(root),visibility='private',
        target_url=frozen['target_url'],target_project_id='P',evidence=[{'message_id':'synthetic','quote':'synthetic only'}],
        source_projects=[str(root)],allowed_operations=['save_drafts'],drafts_sha256=canvas_readback.digest(frozen['drafts']),
        sources_sha256=canvas_readback.digest(frozen['source_artifacts']),cleanup_sha256=canvas_readback.digest([]))
    return frozen,manifest


def test_preview_text_save_is_not_release(tmp_path):
    frozen,manifest=preview_fixture(tmp_path,image=False)
    manifest['artifacts'][0].pop('quality_evidence');write_json(tmp_path/artifacts.MANIFEST_PATH,manifest)
    result=canvas_readback.validate_submission(frozen,tmp_path,private_preview=True)
    assert result['production_blocked'] is True and result['visual_quality_verified'] is False
    with pytest.raises(ValueError): canvas_readback.validate_submission(frozen,tmp_path)


@pytest.mark.parametrize('failure',['missing','fail','historical'])
def test_preview_active_image_cannot_waive_pixel_failure(tmp_path,failure):
    frozen,manifest=preview_fixture(tmp_path,historical_failure=failure=='historical')
    if failure=='missing': manifest['artifacts'][0].pop('quality_evidence')
    elif failure=='fail': manifest['artifacts'][0]['quality_evidence']['checks']['identity_wardrobe']['result']='fail'
    write_json(tmp_path/artifacts.MANIFEST_PATH,manifest)
    with pytest.raises(ValueError,match='quality_evidence'):
        canvas_readback.validate_submission(frozen,tmp_path,private_preview=True)


def test_preview_valid_image_only_checks_evidence_not_approval(tmp_path):
    frozen,_=preview_fixture(tmp_path)
    result=canvas_readback.validate_submission(frozen,tmp_path,private_preview=True)
    assert result['production_blocked'] is True and result['quality_evidence_checks']=='image_evidence_only_not_draft_release'


@pytest.mark.parametrize('actual,declared,expected', [(1,2,'至少2秒'),(2,3,'differs'),(2,2,None)])
def test_xiaoyunque_probes_actual_video_not_internal_cuts(tmp_path,actual,declared,expected):
    frozen,manifest=fixture(tmp_path);d=frozen['drafts'][0];entry=manifest['artifacts'][0]
    video=tmp_path/'reference.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=black:s=16x16:r=10',
                    '-t',str(actual),'-c:v','libx264','-y',str(video)],check=True)
    sha=artifacts.digest(video);url='https://xyq.jianying.com/canvas/test'
    entry.update(path_or_url='reference.mp4',checksum=sha);entry.pop('quality_evidence')
    entry['upload_bindings'][0].update(sha256=sha,target_url=url)
    write_json(tmp_path/artifacts.MANIFEST_PATH,manifest)
    frozen.update(target_url=url,source_artifacts=[dict(artifact_id='IMG',revision='R1',sha256=sha)])
    d['parameters']['resolution']='480p';d['shot_durations']=[1,1,1,1,1]
    d['required_reference_roles']={'E1':['action']}
    d['reference_checks'][0].update(sha256=sha,kind='video',roles=['action'],duration=declared)
    d['quality_evidence']['input_sha256']=q.fingerprint(q.draft_binding(d))
    if expected:
        with pytest.raises(ValueError,match=expected): canvas_readback.validate_submission(frozen,tmp_path)
    else:
        assert canvas_readback.validate_submission(frozen,tmp_path)['ok']


def test_existing_standard_loader_freezes_new_image_reference():
    from scripts.stage_context_cli import _local_standard_documents
    root=Path(__file__).resolve().parents[2]
    assert root/'skills/storyboard-director/references/image-evidence-admission.md' in _local_standard_documents(root/'skills/storyboard-director')


def test_same_shot_new_role_cannot_reuse_old_image_review(tmp_path):
    frozen,_=fixture(tmp_path);d=frozen['drafts'][0]
    d['reference_checks'][0]['roles']=['layout']
    d['required_reference_roles']={'E1':['layout']}
    # Explicit synthetic scene assessment keeps this test focused on stale image use.
    d['quality_evidence']['checks']['persistent_objects'].update(
        scene_state_assessment='unchanged', source_shot_ids=d['source_shot_ids'])
    d['quality_evidence']['input_sha256']=q.fingerprint(q.draft_binding(d))
    with pytest.raises(ValueError,match='职责/实体/状态'): canvas_readback.validate_submission(frozen,tmp_path)


def native_verified(root, frozen):
    d=frozen['drafts'][0]
    observed={'phase':'after_refresh','revision':frozen['revision'],'target_url':frozen['target_url'],
              'drafts':[{'node_id':d['node_id'],'prompt':d['prompt'],'parameters':copy.deepcopy(d['parameters']),
                         'ordered_media':copy.deepcopy(d['ordered_media']),
                         'native_mentions':[dict(**m,native_noneditable_entity=True,entity_type='media') for m in d['ordered_media']]}]}
    path=root/'native-readback.json';write_json(path,observed)
    d['native_binding']={'status':'persisted_readback_verified','revision':frozen['revision'],
                         'input_sha256':q.fingerprint(q.native_input_binding(d)),
                         'evidence_path':path.name,'evidence_sha256':artifacts.digest(path),
                         'canvas_url':frozen['target_url'],'node_id':d['node_id']}
    d['quality_evidence']['input_sha256']=q.fingerprint(q.draft_binding(d))


@pytest.mark.parametrize('change',['prompt','parameters','references','revision','evidence','missing'])
def test_native_verified_claim_rejected_by_actual_canvas_consumer(tmp_path,change):
    frozen,_=fixture(tmp_path);native_verified(tmp_path,frozen);d=frozen['drafts'][0]
    assert canvas_readback.validate_submission(frozen,tmp_path)['ok']
    if change=='prompt': d['prompt']+=' changed'
    elif change=='parameters': d['parameters']['duration']=6
    elif change=='references': d['ordered_media'][0]['media_id']='other'
    elif change=='revision': frozen['revision']='R2'
    elif change=='evidence': (tmp_path/'native-readback.json').write_text('{}')
    else: d['native_binding'].pop('input_sha256')
    d['quality_evidence']['input_sha256']=q.fingerprint(q.draft_binding(d))
    with pytest.raises(ValueError,match='native_binding|指纹'):
        canvas_readback.validate_submission(frozen,tmp_path)


def test_native_unknown_preview_save_never_claims_verified(tmp_path):
    frozen,_=preview_fixture(tmp_path,image=False)
    frozen['drafts'][0]['native_binding']={'status':'unknown'}
    frozen['preview_authorization']['drafts_sha256']=canvas_readback.digest(frozen['drafts'])
    report=canvas_readback.validate_submission(frozen,tmp_path,private_preview=True)
    assert report['production_blocked'] and report['generation_input_provenance']=='unknown'


def test_focal_real_cli_rejects_inherited_verified_and_accepts_unknown(tmp_path):
    frozen,_=fixture(tmp_path);native_verified(tmp_path,frozen)
    d=frozen['drafts'][0];d['prompt']+=' changed'
    payload={'revision':'R2','generation_segments':[dict(d,segment_id='G1')]}
    path=tmp_path/'prompt.json';write_json(path,payload)
    cmd=[sys.executable,'scripts/check_video_prompt_focal_intent.py',str(path),'--project',str(tmp_path)]
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode!=0 and 'native_binding' in result.stdout
    payload['generation_segments'][0]['native_binding']={'status':'unknown'};write_json(path,payload)
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode==0, result.stdout+result.stderr


def test_director_loader_freezes_concrete_method_and_cross_stage_link():
    from scripts.stage_context_cli import _local_standard_documents
    root=Path(__file__).resolve().parents[2]
    method=root/'skills/storyboard-planner/modules/shot-planning.md'
    assert method in _local_standard_documents(root/'skills/storyboard-planner')
    video_method=root/'skills/voiceover-to-video-script/modules/video-method.md'
    assert (video_method.parent/'../../storyboard-planner/modules/shot-planning.md').resolve()==method
    assert '../../storyboard-planner/modules/shot-planning.md#' in video_method.read_text()
    assert artifacts.digest(method)==__import__('hashlib').sha256(method.read_bytes()).hexdigest()


def test_product_refs_real_multi_report_protocol(tmp_path):
    write_json(tmp_path/'source.json',{'synthetic':True})
    package=tmp_path/'sample.zip';package.write_bytes(b'synthetic size-only package check')
    cmd=[sys.executable,'-m','scripts.core.product_refs','--project',str(tmp_path),'--require-file','source.json',
         '--discover-dependencies','--package',str(package)]
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    reports=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    assert len(reports)==2 and reports[0]['ok'] and 'packages' in reports[1]


def test_nested_old_revision_cannot_override_current_package(tmp_path):
    frozen,_=fixture(tmp_path);native_verified(tmp_path,frozen)
    frozen['drafts'][0]['revision']='R1';frozen['revision']='R2'
    d=frozen['drafts'][0];d['native_binding']['input_sha256']=q.fingerprint(q.native_input_binding(d))
    with pytest.raises(ValueError,match='旧回读修订'):
        canvas_readback.validate_submission(frozen,tmp_path)


def test_archived_native_receipt_is_not_current_claim(tmp_path):
    from scripts.check_video_prompt_focal_intent import inspect_document
    payload={'revision':'R2','generation_segments':[{'segment_id':'G1','prompt':'A quiet room.','native_binding':{'status':'unknown'}}],
             'history':[{'revision':'R1','native_binding':{'status':'persisted_readback_verified'}}]}
    path=tmp_path/'prompt.json';write_json(path,payload)
    count,errors=inspect_document(path,project=tmp_path)
    assert count==1 and not errors


def test_director_actual_method_snapshot_changes_in_isolation(tmp_path,monkeypatch):
    from scripts.tests.source_fixture import copy_source_fixture
    from scripts import stage_context_cli as cli
    root=Path(__file__).resolve().parents[2]
    replica=copy_source_fixture(root,tmp_path/'repo')
    monkeypatch.setattr(cli,'ROOT',replica)
    method=replica/'skills/storyboard-planner/modules/shot-planning.md'
    documents=cli._local_standard_documents(replica/'skills/storyboard-planner')
    frozen={str(p):cli.digest(p) for p in documents}
    assert str(method) in frozen
    method.write_text(method.read_text()+'\nSynthetic method revision only in isolated test.\n')
    assert frozen[str(method)]!=cli.digest(method)


@pytest.mark.parametrize('mutation',['canvas','entity_type'])
def test_native_target_and_entity_type_cannot_drift(tmp_path,mutation):
    frozen,_=fixture(tmp_path);native_verified(tmp_path,frozen)
    d=frozen['drafts'][0]
    if mutation=='canvas': frozen['target_url']='https://example.com/other'
    else:
        path=tmp_path/'native-readback.json';v=json.loads(path.read_text())
        v['drafts'][0]['native_mentions'][0]['entity_type']='plain_text';write_json(path,v)
        d['native_binding']['evidence_sha256']=artifacts.digest(path)
    d['quality_evidence']['input_sha256']=q.fingerprint(q.draft_binding(d))
    with pytest.raises(ValueError,match='native_binding'):
        canvas_readback.validate_submission(frozen,tmp_path)


@pytest.mark.parametrize('alias',['native_binding_verified','native_entity_verified','native_delivery_scope','final_native_receipt_path'])
def test_legacy_readback_aliases_do_not_grant_verified(tmp_path,alias):
    from scripts.check_video_prompt_focal_intent import inspect_document
    row={'segment_id':'G1','prompt':'A quiet room.'}
    payload={'revision':'R2','generation_segments':[row]}
    if alias=='native_entity_verified':row['materials']=[{alias:True,'native_readback_source':{'path':'old.json','sha256':'0'*64}}]
    elif alias=='native_binding_verified':row[alias]=True
    else:payload[alias]='old verified receipt'
    path=tmp_path/'prompt.json';write_json(path,payload)
    count,errors=inspect_document(path,project=tmp_path)
    assert count==0 and any('native_binding' in e for e in errors)


@pytest.mark.parametrize('change',['prompt','parameters','references'])
def test_rehashed_new_draft_still_cannot_use_old_native_readback(tmp_path,change):
    frozen,_=fixture(tmp_path);native_verified(tmp_path,frozen);d=frozen['drafts'][0]
    if change=='prompt':d['prompt']+=' changed'
    elif change=='parameters':d['parameters']['duration']=6
    else:d['ordered_media'][0]['media_id']='other'
    d['native_binding']['input_sha256']=q.fingerprint(q.native_input_binding(d))
    d['quality_evidence']['input_sha256']=q.fingerprint(q.draft_binding(d))
    with pytest.raises(ValueError,match='回读不一致'):
        canvas_readback.validate_submission(frozen,tmp_path)


def test_real_ensure_prepared_rejects_stale_director_method_fingerprint(tmp_path):
    from scripts import stage_context_cli as cli
    method=cli.ROOT/'skills/storyboard-planner/modules/shot-planning.md'
    context=tmp_path/cli.CONTEXT_DIR;context.mkdir(parents=True)
    (context/'stage_startup.md').write_text('# 阶段启动包\n- Skill：`storyboard-planner`\n# storyboard-planner 当前知识包\n\n## 选择记录\n- 已选知识块：无\n')
    write_json(context/'task_basis.json',dict(schema_version='1.0',skill='storyboard-planner',objective='SYNTHETIC OLD SNAPSHOT',
        deliverables=[],non_goals=[],input_versions=[],standards={'local_documents':[{'path':str(method),'sha256':'0'*64}],
            'root_rules':str(cli.ROOT/'PROJECT_RULES.md'),'root_rules_sha256':cli.digest(cli.ROOT/'PROJECT_RULES.md')}))
    args=cli.parser().parse_args(['ensure-prepared','--project',str(tmp_path),'--skill','storyboard-planner',
                                '--required-reference','skills/storyboard-planner/modules/shot-planning.md'])
    with pytest.raises(cli.StageContextError,match='必需本地规范已变更'):
        cli.ensure_prepared(args)


def adopt_agent_fixture_entry(root, entry, *, usage=None, reference_groups=None):
    """Exercise real adoption APIs with explicitly synthetic self-check evidence."""
    from scripts.core import agent_acceptance as acceptance
    from scripts.core.project_state import load_project_state
    state = load_project_state(root)
    if not state.get('workflow_authorizations'):
        acceptance.register_workflow_authorization(root, authorization_id='SYNTHETIC-WF',
            authorization_ref='SYNTHETIC ONLY: user authorized the downstream workflow',
            skills=['storyboard-planner', 'storyboard-director', 'voiceover-to-video-script'])
    profile_path = acceptance.ROOT / 'skills' / acceptance.skill_for_artifact(entry) / 'review-profile.json'
    profile = json.loads(profile_path.read_text())
    report = {**acceptance.acceptance_binding(root, entry), 'schema_version': '1.0',
              'kind': 'agent-self-check', 'authorization_id': 'SYNTHETIC-WF',
              'agent_id': 'synthetic-agent', 'checked_at': '2026-11-05T00:00:00Z',
              'scope': 'artifact', 'verdict': 'pass',
              'review_profile_sha256': artifacts.digest(profile_path),
              'checks': [{'id': item['id'], 'result': 'pass',
                          'observation': 'SYNTHETIC ONLY, no actual creative or visual acceptance',
                          'locator': 'synthetic consumer fixture'}
                         for item in profile['production_artifact_checks']]}
    if usage is not None:
        report['usage'] = usage
    if reference_groups is not None:
        report['reference_groups'] = reference_groups
    path = root / ('agent-' + entry['artifact_id'] + '.json')
    write_json(path, report)
    return artifacts.adopt_initial_agent_artifact(root, entry['artifact_id'], path.name)['artifact']


def agent_board_fixture(root):
    from test_formal_package_submission import confirmed_narrative, board_candidate, register
    story, _ = confirmed_narrative(root)
    board = board_candidate(root, story)
    second = copy.deepcopy(board['scenes'][0]['shots'][0])
    second.update(shot_id='S01-020', source_dialogue_ids=[])
    second['audio']['dialogue'] = []
    board['scenes'][0]['shots'].append(second)
    entry = register(root, 'BOARD', 'storyboard.package', board, ['narrative.package'])
    return adopt_agent_fixture_entry(root, entry)


def agent_fixture(root, *, image_setup=None):
    """Human narrative -> agent director -> agent image, without new human receipts."""
    frozen, old_manifest = fixture(root)
    board = agent_board_fixture(root)
    entry = old_manifest['artifacts'][0]
    entry.update(status='candidate', user_approved=False, depends_on=['storyboard.package'])
    entry.pop('confirmation_ref')
    row = frozen['drafts'][0]
    row['source_shot_ids'] = ['S01-010']
    evidence = entry['quality_evidence']
    evidence['source_shot_ids'] = ['S01-010']
    evidence['uses'] = [q.image_use(row['reference_checks'][0], row['source_shot_ids'])]
    if image_setup is not None:
        image_setup(frozen, entry)  # Complete observations before their immutable adoption binding.
    artifacts.register_workspace_artifact(root, entry)
    adopted = adopt_agent_fixture_entry(root, entry, usage={'source_shot_ids': ['S01-010']})
    frozen['source_artifacts'] = [{'artifact_id': board['artifact_id'], 'revision': board['revision'],
                                  'sha256': board['checksum']}]
    row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))
    return frozen, adopted


def test_agent_image_consumers_use_real_adoption_without_human_confirmation(tmp_path):
    from scripts.core.project_state import load_project_state
    frozen, entry = agent_fixture(tmp_path)
    state = load_project_state(tmp_path)
    assert not entry['user_approved'] and not artifacts.artifact_is_confirmed(entry, state, tmp_path)
    assert artifacts.artifact_is_adopted(entry, state, tmp_path)
    assert state['user_confirmations'] == [{'artifact_ids': ['N']}]
    assert asset_refs.require_references(tmp_path, ['IMG'], ['S01-010']) == (True, [])
    assert canvas_readback.validate_submission(frozen, tmp_path)['ok']


@pytest.mark.parametrize('mutation', ['missing', 'fail', 'unknown', 'source_hash', 'image_hash',
                                     'scope', 'no_locator', 'report_hash', 'authorization', 'upstream_confirmation'])
def test_agent_image_acceptance_never_bypasses_observation_or_authorization(tmp_path, mutation):
    frozen, _ = agent_fixture(tmp_path)
    manifest = artifacts.load_manifest(tmp_path)
    entry = next(item for item in manifest['artifacts'] if item['artifact_id'] == 'IMG')
    evidence = entry['quality_evidence']
    if mutation == 'missing': entry.pop('quality_evidence')
    elif mutation in {'fail', 'unknown'}: evidence['checks']['product_phase']['result'] = mutation
    elif mutation == 'source_hash': evidence['source_files'][0]['sha256'] = '0' * 64
    elif mutation == 'image_hash': evidence['image_sha256'] = '0' * 64
    elif mutation == 'scope': evidence['source_shot_ids'] = ['S01-020']
    elif mutation == 'no_locator': evidence['checks']['hands_support'].pop('locator')
    elif mutation == 'report_hash': (tmp_path / entry['agent_acceptance']['report_path']).write_text('{}')
    else:
        path = tmp_path / '00_项目管理/project_state.json'
        state = json.loads(path.read_text())
        if mutation == 'upstream_confirmation': state['user_confirmations'] = []
        else: state['workflow_authorizations'][0]['status'] = 'revoked'
        write_json(path, state)
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    assert not asset_refs.require_references(tmp_path, ['IMG'], ['S01-010'])[0]
    with pytest.raises(ValueError): canvas_readback.validate_submission(frozen, tmp_path)


def test_agent_image_observed_extra_shot_does_not_expand_accepted_usage(tmp_path):
    def observe_extra_shot(frozen, image):
        row = frozen['drafts'][0]
        row['source_shot_ids'] = ['S01-020']
        image['quality_evidence']['source_shot_ids'].append('S01-020')
        image['quality_evidence']['uses'].append(q.image_use(row['reference_checks'][0], ['S01-020']))
    frozen, _ = agent_fixture(tmp_path, image_setup=observe_extra_shot)
    assert not asset_refs.require_references(tmp_path, ['IMG'], ['S01-020'])[0]
    with pytest.raises(ValueError, match='acceptance usage'):
        canvas_readback.validate_submission(frozen, tmp_path)
