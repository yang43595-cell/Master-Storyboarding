"""Scope separation: ordinary workflow checks do not waive explicit usage gates."""
import json
from pathlib import Path
import pytest
from scripts import stage_context_cli as cli
from scripts.core.candidate_test import configure, context, require_production
from test_stage_context_cli import _project, _method_vault, _synthetic_method_profile


def run(tmp_path, name, *, measured=False, source_exists=True):
    project=_project(tmp_path/name)
    configure(project,name,'synthetic separately scoped test authorization',[],current_stage='creative-director',
              usage_contract={'required':True,'turn_id':None,'limits':{'max_input':1000,'max_total':5000,'max_requests':5}} if measured else None)
    source=project/'source.txt'
    if source_exists:source.write_text('Synthetic exact source, not a real dialogue or audiovisual review')
    request=project/'request.json';request.write_text(json.dumps({'sources':[str(source)]}))
    vault=_method_vault(tmp_path/'vault')
    args=cli.parser().parse_args(['prepare','--candidate-test','--project',str(project),'--skill','creative-director',
        '--objective','synthetic workflow compliance only, no usage measurement claim','--request-json',str(request),
        '--vault',str(vault),'--actor-id',name+'-actor','--task-id',name+'-task'])
    return project,args


def test_workflow_preparation_can_reach_source_checks_without_mandatory_usage(tmp_path):
    project,args=run(tmp_path,'workflow')
    result=cli.prepare(args)
    assert result['production_blocked'] is True
    basis=json.loads((project/cli.CONTEXT_DIR/'task_basis.json').read_text())
    assert len(basis['input_snapshot']['sources'])==1
    assert 'usage_contract' not in context(project)
    assert 'run_usage' not in result  # Never a fabricated zero-cost/PASS measurement.
    with pytest.raises(ValueError,match='禁止'):require_production(project)


def test_workflow_still_rejects_missing_source(tmp_path):
    project,args=run(tmp_path,'missing-source',source_exists=False)
    with pytest.raises(cli.StageContextError,match='来源'):cli.prepare(args)
    assert not (project/cli.CONTEXT_DIR/'task_basis.json').exists()


def test_workflow_does_not_inherit_reading_or_claim_approval(tmp_path,monkeypatch):
    project,args=run(tmp_path,'reading')
    _synthetic_method_profile(tmp_path/'binding',Path(args.vault),monkeypatch)
    request=Path(args.request_json);data=json.loads(request.read_text());data['use_story_method']=True
    request.write_text(json.dumps(data))
    cli.prepare(args)
    ensure=cli.parser().parse_args(['ensure-prepared','--candidate-test','--project',str(project),'--skill','creative-director',
                                  '--actor-id','reading-actor','--task-id','reading-task'])
    result=cli.ensure_prepared(ensure)
    assert result['ok'] is False and result['method_reading_evidence']=='required'
    assert result['production_blocked'] is True


def test_separate_workflow_does_not_disable_old_required_run(tmp_path):
    old,args=run(tmp_path,'metering',measured=True)
    state=old/'00_项目管理/project_state.json';before=state.read_bytes()
    with pytest.raises(cli.StageContextError,match='尚未知'):cli.prepare(args)
    new,new_args=run(tmp_path,'workflow')
    cli.prepare(new_args)
    assert state.read_bytes()==before
    with pytest.raises(cli.StageContextError,match='尚未知'):cli.prepare(args)
    assert context(old)['usage_contract']['required'] is True
    for project in (old,new):
        data=json.loads((project/'00_项目管理/project_state.json').read_text())
        assert not data['user_confirmations'] and not data['current_artifacts']
        with pytest.raises(ValueError):require_production(project)
