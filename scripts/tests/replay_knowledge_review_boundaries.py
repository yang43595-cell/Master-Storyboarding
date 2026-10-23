"""Replay the isolated knowledge/review CLI chain against an existing fixture vault.

Run from the candidate checkout with --output ../knowledge_review.
All output is test data; fixture-actor is not a user confirmation or production reviewer.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    root=Path(__file__).resolve().parents[2];base=Path(args.output).resolve();p=base/'candidate_chain';vault=base/'candidate_vault'
    cli=root/'scripts/stage_context_cli.py';history=[]
    def run(arguments, success=True):
        result=subprocess.run([sys.executable,'-B',str(cli),*arguments],text=True,capture_output=True,
                              env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        history.append({'argv':arguments,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        assert (result.returncode==0)==success,result.stdout+result.stderr
        return json.loads(result.stdout) if result.stdout.strip().startswith('{') else result.stderr
    common=['--project',str(p),'--skill','creative-director']
    actor=['--actor-id','fixture-actor','--task-id','fixture-chain']
    prepare=['prepare',*common,'--objective','隔离审核依赖验证','--vault',str(vault),*actor]
    run(prepare)
    context=p/'00_项目管理/执行上下文';basis=json.loads((context/'task_basis.json').read_text());manifest=basis['reading_manifest']
    assert len(manifest['selected'])==8
    assert {i['id'] for i in manifest['candidate_catalog']}=={'MSB-KB-TEST-008','MSB-KB-BOARD-015','MSB-KB-PERF-001'}
    # The fixture bytes are explicitly read; this does not claim semantic proof.
    (context/'method_reading_packet.md').read_text();(vault/'wiki/concepts/fixture.md').read_text()
    receipt=['record-method-reading',*common,*actor,'--method','CREATIVE-DIRECTION-METHOD',
             '--candidate-rule','MSB-KB-TEST-008','--candidate-rule','MSB-KB-BOARD-015',
             '--candidate-rule','MSB-KB-PERF-001','--vault',str(vault)]
    missing_scope=run(receipt,False)
    run([*receipt,'--candidate-scope','MSB-KB-BOARD-015=只比较高强度动作是否夺走故事主题；不采用导演实现。',
         '--candidate-scope','MSB-KB-PERF-001=只采用隐藏情绪的目的和人物动机；不写生成实现。'])
    gate=['ensure-prepared',*common,*actor,'--required-rule','MSB-KB-TEST-008','--required-rule','MSB-KB-BOARD-015']
    assert run(gate)['ok']
    mismatch=run(['ensure-prepared',*common,'--actor-id','other-fixture-actor','--task-id','fixture-chain'],False)
    assert mismatch['ok'] is False
    unfrozen=run([*gate,'--required-reference','skills/storyboard-planner/references/lighting-prompt-standard.md'],False)
    evidence=p/'evidence.md';evidence.write_text('人物甲持有蓝杯。\n来源拼字a。\n')
    initial=run(['review-pack',*common,'--artifact',str(p/'candidate.md'),'--evidence',str(evidence)])
    scope=json.loads(Path(initial['review_scope_path']).read_text());(base/'candidate_initial_scope.json').write_text(json.dumps(scope,ensure_ascii=False,indent=2))
    (p/'previous.json').write_text('{"issues":[]}')
    changes=[]
    for label, content, include in [('typo','人物甲持有蓝杯。\n来源拼字b。\n',True),
                                     ('fact_reversal','人物乙持有红杯。\n',True),
                                     ('withdrawal',None,False)]:
        if content is not None:evidence.write_text(content)
        revision=run(['review-pack',*common,'--artifact',str(p/'candidate.md'),'--review-mode','revision',
                      '--previous-review',str(p/'previous.json'),*(['--evidence',str(evidence)] if include else [])])
        delta=json.loads(Path(revision['review_delta_path']).read_text())
        assert not delta['changed_artifacts'] and delta['evidence_impact']=='requires_reviewer_judgment'
        key='changed' if include else 'removed';assert len(delta['direct_evidence_changes'][key])==1
        (base/f'candidate_{label}_delta.json').write_text(json.dumps(delta,ensure_ascii=False,indent=2))
        changes.append({'scenario':label,'change_type':key,'task_basis_identity_unchanged':delta['task_basis_identity']==scope['task_basis_identity']})
    evidence.write_text('正式模式隔离来源A。\n')
    formal_initial=run(['review-pack',*common,'--artifact',str(p/'candidate.md'),'--evidence',str(evidence),'--formal-archive'])
    formal_scope=json.loads(Path(formal_initial['review_scope_path']).read_text())
    baseline=p/'formal-baseline.md';baseline.write_bytes((p/'candidate.md').read_bytes())
    check=basis['review_acceptance']['production']['required_checks'][0]['id']
    formal_previous=p/'formal-previous.json'
    formal_previous.write_text(json.dumps({'reviewer_id':'fixture-reviewer-not-production','scope':formal_scope,
        'issues':[{'id':'B-001','check_id':check,'status':'unresolved','target':'candidate.md:2',
                   'evidence':'隔离来源核对','impact':'隔离未决义务'}]},ensure_ascii=False))
    evidence.write_text('正式模式隔离来源B。\n')
    formal=run(['review-pack',*common,'--artifact',str(p/'candidate.md'),'--evidence',str(evidence),
                '--review-mode','revision','--formal-archive','--previous-review',str(formal_previous),
                '--baseline',str(baseline),'--producer-response',json.dumps({'responses':[{'id':'B-001','summary':'候选未改，来源换版待审核判断','evidence':str(evidence)}]},ensure_ascii=False)])
    formal_delta=json.loads(Path(formal['review_delta_path']).read_text())
    assert formal_delta['direct_evidence_changes']['changed']
    assert formal_delta['issue_updates'][0]['id']=='B-001'
    assert formal_delta['reviewer_id']=='fixture-reviewer-not-production'
    assert formal_delta['actual_diff'][0]['before_sha256']==formal_delta['actual_diff'][0]['after_sha256']
    (base/'candidate_formal_evidence_delta.json').write_text(json.dumps(formal_delta,ensure_ascii=False,indent=2))
    state=json.loads((p/'00_项目管理/project_state.json').read_text());assert state['user_confirmations']==[]
    report={'selected_count':8,'discoverable_candidates':3,'required_budget_rule_after_adoption':'passed',
            'missing_scope_rejected':missing_scope,'actor_mismatch_blocked':True,'unfrozen_reference_rejected':unfrozen,
            'external_evidence_scenarios':changes,'user_confirmations':state['user_confirmations'],
            'formal_evidence_change_and_unresolved_identity_preserved':True,
            'semantic_verdicts':'not_computed; reviewer must judge impact'}
    (base/'candidate_chain_log.json').write_text(json.dumps(history,ensure_ascii=False,indent=2))
    (base/'candidate_chain_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
