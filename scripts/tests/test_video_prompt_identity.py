"""Typed delivery identity uses existing project keys and registered source bytes."""
import json, subprocess, sys
from pathlib import Path
import pytest
from scripts.canvas_readback import compare, digest, validate_submission
from scripts.core import artifacts, candidate_test
from scripts.core.io import write_json
from scripts.core.schema_contracts import validate_video_prompt_identity
from test_delivery_preflight import delivery
from test_candidate_test_mode import setup, sources, add


def test_correct_delivery_and_readback_are_read_only(tmp_path):
    frozen, observed = delivery(tmp_path)
    before = {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert validate_submission(frozen,tmp_path)['document_identity']['semantic_translation']=='requires_manual_review'
    assert compare(frozen, observed, tmp_path)['ok']
    assert before == {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('field', ['schema_version','document_type','stage','project_id','revision','source_storyboard','source_narrative','drafts'])
def test_missing_identity_never_implicitly_migrates(tmp_path, field):
    frozen,_ = delivery(tmp_path); frozen.pop(field)
    with pytest.raises(ValueError):validate_submission(frozen,tmp_path)


@pytest.mark.parametrize('field,value', [('schema_version','4.0'),('document_type','storyboard.package'),('stage','storyboard-planner'),('project_id','OTHER'),('project_id',' '),('revision',' ')])
def test_wrong_identity_rejected(tmp_path,field,value):
    frozen,_ = delivery(tmp_path);frozen[field]=value
    with pytest.raises(ValueError):validate_submission(frozen,tmp_path)


@pytest.mark.parametrize('ref', ['source_storyboard','source_narrative'])
@pytest.mark.parametrize('field,value', [('artifact_id','WRONG'),('logical_id','video.prompt'),('revision','R99'),('path','frame.png'),('sha256','0'*64)])
def test_exact_source_binding_rejected(tmp_path,ref,field,value):
    frozen,_ = delivery(tmp_path);frozen[ref][field]=value
    with pytest.raises(ValueError):validate_submission(frozen,tmp_path)


@pytest.mark.parametrize('case',['source_type','source_body_project','source_body_revision','source_bytes','narrative_link','director_relabel','new_hash_old_confirmation','shot_id','duplicate_node','missing_shots'])
def test_registration_and_body_cannot_be_relabelled(tmp_path,case):
    frozen,_=delivery(tmp_path);manifest=artifacts.load_manifest(tmp_path)
    board=tmp_path/'director.json';payload=json.loads(board.read_text())
    entry=next(a for a in manifest['artifacts'] if a['artifact_id']=='DIRECTOR')
    if case=='source_type':entry['type']='video.prompt';artifacts.save_manifest(tmp_path,manifest)
    elif case in ('source_body_project','source_body_revision','narrative_link','new_hash_old_confirmation'):
        if case=='source_body_project':payload['project_id']='OTHER'
        if case=='source_body_revision':payload['revision']='R99'
        if case=='narrative_link':payload['source_narrative']['artifact_id']='FRAME'
        if case=='new_hash_old_confirmation':payload['open_questions']=['changed after confirmation']
        write_json(board,payload);frozen['source_storyboard']['sha256']=artifacts.digest(board);frozen['source_artifacts'][0]['sha256']=artifacts.digest(board)
        if case!='new_hash_old_confirmation':entry['checksum']=artifacts.digest(board);artifacts.save_manifest(tmp_path,manifest)
    elif case=='source_bytes':board.write_text('{}')
    elif case=='director_relabel':frozen={**payload,**{k:v for k,v in frozen.items() if k in ['schema_version','document_type','stage','project_id','revision','source_storyboard','source_narrative','drafts']}}
    elif case=='shot_id':frozen['drafts'][0]['source_shot_ids']=['FOREIGN-SHOT']
    elif case=='duplicate_node':frozen['drafts'][1]['node_id']=frozen['drafts'][0]['node_id']
    elif case=='missing_shots':frozen['drafts'][0].pop('source_shot_ids')
    with pytest.raises(ValueError):validate_submission(frozen,tmp_path)


@pytest.mark.parametrize('field',['schema_version','document_type','stage','project_id','revision','source_storyboard','source_narrative'])
def test_readback_cannot_reuse_hash_with_another_identity(tmp_path,field):
    frozen,observed=delivery(tmp_path);observed[field]='different'
    assert not compare(frozen,observed,tmp_path)['ok']


def test_body_semantics_is_explicitly_not_machine_proven(tmp_path):
    frozen,_=delivery(tmp_path)
    frozen['drafts'][0]['prompt']='导演稿：固定中景，尚未完成提示词转译。'
    report=validate_submission(frozen,tmp_path)
    assert report['document_identity']['semantic_translation']=='requires_manual_review'
    assert report['document_identity']['generation_authorization']=='not_granted'


def candidate_prompt(root):
    project=setup(root);n,b=sources(project);add(project,'N','narrative.package',n);add(project,'B','storyboard.package',b,['narrative.package'])
    value={'schema_version':'video-prompt-1.0','document_type':'video.prompt','stage':'voiceover-to-video-script','project_id':n['project_id'],'revision':'R01','status':'candidate',
        'source_storyboard':{'artifact_id':'B','logical_id':'storyboard.package','revision':b['revision'],'path':'B.json','sha256':artifacts.digest(project/'B.json')},
        'source_narrative':{'artifact_id':'N','logical_id':'narrative.package','revision':n['revision'],'path':'N.json','sha256':artifacts.digest(project/'N.json')},
        'drafts':[{'node_id':'P01','prompt':'不。人物说完放下手中物。','source_shot_ids':['S01-010']}]}
    return project,value,b


def test_candidate_identity_pass_never_authorizes_production(tmp_path):
    project,value,_=candidate_prompt(tmp_path);add(project,'P','video.prompt',value,['storyboard.package','narrative.package'])
    assert len(candidate_test.resolve_inputs(project))==3
    result=validate_video_prompt_identity(value,project,candidate_mode=True)
    assert result['production_blocked'] and result['review_status']=='unreviewed'
    with pytest.raises(ValueError,match='unreviewed'):validate_submission(value,project)


@pytest.mark.parametrize('case',['director_as_prompt','registration_revision','changed_narrative','changed_director','foreign_source','missing_metadata'])
def test_candidate_relabels_are_blocked(tmp_path,case):
    project,value,b=candidate_prompt(tmp_path)
    if case=='director_as_prompt':value=b
    if case=='missing_metadata':value.pop('document_type')
    add(project,'P','video.prompt',value,['storyboard.package','narrative.package'])
    if case=='registration_revision':
        manifest=artifacts.load_manifest(project);manifest['artifacts'][-1]['revision']='R99';artifacts.save_manifest(project,manifest)
    if case=='changed_narrative':
        n=json.loads((project/'N.json').read_text());n['objective']='changed';write_json(project/'N.json',n)
    if case=='changed_director':b['open_questions']=['changed'];write_json(project/'B.json',b)
    if case=='foreign_source':value['source_storyboard']['artifact_id']='FOREIGN';write_json(project/'P.json',value)
    with pytest.raises(ValueError):candidate_test.resolve_inputs(project)


def test_old_versioned_delivery_needs_explicit_history_mode(tmp_path):
    frozen,observed=delivery(tmp_path)
    for field in ['schema_version','document_type','stage','project_id','source_storyboard','source_narrative']:frozen.pop(field)
    observed['frozen_sha256']=digest(frozen)
    with pytest.raises(ValueError):validate_submission(frozen,tmp_path)
    with pytest.raises(ValueError):compare(frozen,observed)
    report=compare(frozen,observed,legacy_read_only=True)
    assert report['ok'] and report['production_blocked'] and report['submission_checks']=='not_checked'
    with pytest.raises(ValueError):compare(frozen,observed,tmp_path,legacy_read_only=True)


def test_json_file_cli_has_no_type_by_filename_bypass(tmp_path):
    frozen,_=delivery(tmp_path);p=tmp_path/'misleading_director_filename.json';write_json(p,frozen)
    script=Path(__file__).parents[1]/'check_video_prompt_focal_intent.py';cmd=[sys.executable,str(script),str(p)]
    assert subprocess.run(cmd,capture_output=True).returncode==2
    assert subprocess.run(cmd+['--project',str(tmp_path)],capture_output=True).returncode==0
    write_json(p,json.loads((tmp_path/'director.json').read_text()))
    assert subprocess.run(cmd+['--project',str(tmp_path)],capture_output=True).returncode==1


@pytest.mark.parametrize('candidate_mode', [False, True])
@pytest.mark.parametrize('moment', ['before_snapshot', 'after_snapshot'])
def test_source_hash_and_parse_use_same_bytes(tmp_path, monkeypatch, candidate_mode, moment):
    if candidate_mode:
        project, value, board = candidate_prompt(tmp_path)
        target = project/'B.json'
    else:
        project = tmp_path
        value, _ = delivery(project)
        target = project/'director.json'
        board = json.loads(target.read_bytes())
    board['scenes'][0]['shots'][0]['shot_id'] = 'S01-999'
    changed = json.dumps(board, ensure_ascii=False).encode()
    for row in value['drafts']:
        row['source_shot_ids'] = ['S01-999']
    read_bytes = Path.read_bytes
    swaps = []

    def racing_read(path):
        if path == target and not swaps:
            swaps.append(True)
            if moment == 'before_snapshot':
                path.write_bytes(changed)
            raw = read_bytes(path)
            if moment == 'after_snapshot':
                path.write_bytes(changed)
            return raw
        return read_bytes(path)

    monkeypatch.setattr(Path, 'read_bytes', racing_read)
    with pytest.raises(ValueError):
        validate_video_prompt_identity(value, project, candidate_mode=candidate_mode)
    assert swaps


def test_source_cannot_change_after_snapshot_even_if_old_shots_still_match(tmp_path, monkeypatch):
    project, value, board = candidate_prompt(tmp_path)
    target = project/'B.json'
    board['open_questions'] = ['changed after snapshot']
    changed = json.dumps(board, ensure_ascii=False).encode()
    read_bytes = Path.read_bytes
    swaps = []

    def racing_read(path):
        raw = read_bytes(path)
        if path == target and not swaps:
            swaps.append(True)
            path.write_bytes(changed)
        return raw

    monkeypatch.setattr(Path, 'read_bytes', racing_read)
    with pytest.raises(ValueError, match='changed during'):
        validate_video_prompt_identity(value, project, candidate_mode=True)


@pytest.mark.parametrize('missing', [False, True])
def test_readback_requires_exact_source_shots(tmp_path, missing):
    frozen, observed = delivery(tmp_path)
    if missing:
        observed['drafts'][0].pop('source_shot_ids')
    else:
        observed['drafts'][0]['source_shot_ids'] = ['FOREIGN-SHOT']
    report = compare(frozen, observed, tmp_path)
    assert not report['ok']
    assert any(d.get('field') == 'source_shot_ids' for d in report['differences'])


@pytest.mark.parametrize('field,value', [('logical_id','video.prompt'), ('type','video.prompt'),
    ('path','frame.png'), ('path_or_url','frame.png'), ('project_id','OTHER')])
def test_duplicate_source_metadata_must_agree(tmp_path, field, value):
    frozen, _ = delivery(tmp_path)
    frozen['source_artifacts'][0][field] = value
    with pytest.raises(ValueError, match='contradictory'):
        validate_submission(frozen, tmp_path)


def test_duplicate_source_equivalent_paths_allowed(tmp_path):
    frozen, _ = delivery(tmp_path)
    frozen['source_artifacts'][0].update(logical_id='storyboard.package', type='storyboard-package',
        path='./director.json', path_or_url=str(tmp_path/'director.json'), project_id=frozen['project_id'])
    assert validate_submission(frozen, tmp_path)['ok']


def test_readback_duplicate_source_association_must_agree(tmp_path):
    frozen, observed = delivery(tmp_path)
    observed['source_artifacts'][0]['path'] = 'frame.png'
    assert not compare(frozen, observed, tmp_path)['ok']


def test_unversioned_api_cannot_silently_downgrade_to_history():
    from test_canvas_readback import sample
    frozen, observed = sample()
    with pytest.raises(ValueError, match='explicit legacy'):
        compare(frozen, observed)
    report = compare(frozen, observed, legacy_read_only=True)
    assert report['ok'] and report['legacy_read_only'] and report['production_blocked']
    assert report['submission_checks'] == 'not_checked'
