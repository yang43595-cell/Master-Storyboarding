"""Explicit controlled-run metering; synthetic logs are never live usage proof."""
import argparse
import copy
import json
import pytest
from scripts.core.candidate_test import configure, context
from scripts import stage_context_cli as cli
from scripts.tests.test_session_usage import record


def policy(turn='turn-1'):
    return {'required': True, 'turn_id': turn, 'limits': {'max_input':80000,'max_total':600000,'max_requests':8}}


def setup(tmp_path, requirement=True):
    project=tmp_path/'project';project.mkdir()
    configure(project,'synthetic-run','synthetic explicit metering authorization',[],usage_contract=policy() if requirement else None)
    return project


def args(project,**kwargs):
    return argparse.Namespace(project=str(project),**kwargs)


def test_omitted_flags_do_not_remove_explicit_requirement(tmp_path):
    project=setup(tmp_path)
    with pytest.raises(cli.StageContextError,match='准确 turn'):cli._check_run_budget(args(project))
    configure(project,'synthetic-run','synthetic explicit metering authorization',[])
    assert context(project)['usage_contract']==policy()
    with pytest.raises(cli.StageContextError):cli._check_run_budget(args(project))


def test_unmetered_ordinary_project_keeps_existing_optional_behavior(tmp_path):
    assert cli._check_run_budget(args(setup(tmp_path,False))) is None


def test_unknown_turn_keeps_requirement_and_blocks_first_prepare(tmp_path):
    project=tmp_path/'project';project.mkdir()
    configure(project,'synthetic-run','synthetic authorization',[],usage_contract=policy(None))
    with pytest.raises(cli.StageContextError,match='尚未知'):cli.prepare(args(project,skill='creative-director'))
    assert not (project/'_context').exists()
    configure(project,'synthetic-run','synthetic authorization',[],usage_contract=policy())
    assert context(project)['usage_contract']['turn_id']=='turn-1'


@pytest.mark.parametrize('change',['turn','increase','remove','newrun'])
def test_contract_cannot_be_downgraded_or_identity_replaced(tmp_path,change):
    project=setup(tmp_path);p=policy()
    if change=='turn':p['turn_id']='other'
    elif change=='increase':p['limits']['max_total']+=1
    elif change=='remove':p['required']=False
    with pytest.raises(ValueError):configure(project,'other-run' if change=='newrun' else 'synthetic-run','synthetic explicit metering authorization',[],usage_contract=p)
    assert context(project)['usage_contract']==policy()


def test_real_parser_positive_and_unknown_log_rejection(tmp_path):
    project=setup(tmp_path);log=tmp_path/'synthetic.jsonl'
    log.write_text(json.dumps({'type':'token_usage_record','payload':record('one')}))
    a=args(project,usage_log=[str(log)],usage_turn_id='turn-1')
    report=cli._check_run_budget(a)
    assert report['requests']==1
    log.write_text('{}\n')
    with pytest.raises(cli.StageContextError,match='usage_unknown'):cli._check_run_budget(a)


@pytest.mark.parametrize('extra',[{'usage_turn_id':'other'}, {'max_total':700000}, {'usage_boundaries_json':'arbitrary.json'}])
def test_cli_scope_and_budget_cannot_override_contract(tmp_path,extra):
    project=setup(tmp_path);kw={'usage_turn_id':'turn-1'};kw.update(extra)
    with pytest.raises(cli.StageContextError):cli._check_run_budget(args(project,**kw))


def test_prepare_and_ensure_refuse_before_method_or_output_work(tmp_path):
    project=setup(tmp_path)
    for function in (cli.prepare,cli._ensure_prepared):
        with pytest.raises(cli.StageContextError,match='准确 turn'):function(args(project,skill='creative-director'))
    assert list(project.glob('**/method_reading_packet.md'))==[]


def test_explicit_missing_contract_file_cannot_silently_disable(tmp_path):
    project=tmp_path/'project';project.mkdir()
    with pytest.raises(ValueError,match='usage_contract'):
        cli.main(['candidate-test','--project',str(project),'--run-id','run','--authorization-ref','synthetic','--usage-contract-json',str(tmp_path/'missing.json')])
    assert context(project) is None


@pytest.mark.parametrize('spelling',['absolute','relative','home'])
def test_project_path_spellings_cannot_skip_required_metering(tmp_path,monkeypatch,spelling):
    project=setup(tmp_path)
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('HOME',str(tmp_path))
    value={'absolute':str(project),'relative':'project','home':'~/project'}[spelling]
    with pytest.raises(cli.StageContextError,match='准确 turn'):cli._check_run_budget(args(value))
