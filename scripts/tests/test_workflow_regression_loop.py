"""Engineering loop evidence invariants, synthetic only; no business approvals."""
import json
import subprocess
from pathlib import Path
import pytest
from scripts import workflow_regression_loop as loop


def git(repo,*args):
    return subprocess.check_output(['git','-C',str(repo),*args]).decode().strip()


def setup(tmp_path):
    repo=tmp_path/'repo';repo.mkdir();git(repo,'init','-q');git(repo,'config','user.name','Fixture');git(repo,'config','user.email','fixture@example.invalid')
    (repo/'tracked').write_text('one');git(repo,'add','.');git(repo,'commit','-qm','fixture')
    work=tmp_path/'evidence';plan={'case_ids':['natural','negative'],'case_kinds':{'natural':'natural','negative':'synthetic'},'pytest_targets':['tracked'],'node_targets':[]}
    loop.init(work,plan);loop.begin(work,repo,'Synthetic initial reproduction')
    return repo,work,plan


def seed_tests(work):
    state=loop.read(work/'loop.json');row=state['rounds'][-1]
    for kind in ['focused','full']:
        path=work/f'{kind}.log';path.write_text('1 passed')
        row['tests'].append({'kind':kind,'status':'passed','log':str(path),'sha256':loop.digest(path.read_bytes())})
    loop.write_json(work/'loop.json',state)


def evidence(tmp_path,repo,phase,coverage=None):
    p=tmp_path/(phase+'.json');log=tmp_path/(phase+'.log');log.write_text('Synthetic evidence, not live execution')
    value={'phase':phase,'snapshot':loop.snapshot(repo),'reviewer':phase+'-fixture','coverage':coverage or {'natural':'pass','negative':'correctly_blocked'},'evidence_files':[{'path':str(log),'sha256':loop.digest(log.read_bytes())}]}
    if phase=='blind':
        clone=tmp_path/'fresh';subprocess.check_call(['git','clone','-q',str(repo),str(clone)]);value.update(fresh_checkout=str(clone),fresh_execution=True)
    p.write_text(json.dumps(value));return p


def test_missing_review_or_blind_never_complete(tmp_path):
    r,w,_=setup(tmp_path);seed_tests(w)
    assert loop.status_report(w,r)['all_required_evidence_passed'] is False
    loop.record(w,r,'review',evidence(tmp_path,r,'review'))
    assert loop.status_report(w,r)['all_required_evidence_passed'] is False
    result=loop.record(w,r,'blind',evidence(tmp_path,r,'blind'))
    assert result['all_required_evidence_passed'] is True
    assert result['production_authorized'] is False


@pytest.mark.parametrize('state',['fail','blocked_external','not_tested','out_of_authorization','correctly_blocked'])
def test_natural_missing_coverage_not_success(tmp_path,state):
    r,w,_=setup(tmp_path);seed_tests(w);loop.record(w,r,'review',evidence(tmp_path,r,'review'))
    result=loop.record(w,r,'blind',evidence(tmp_path,r,'blind',{'natural':state,'negative':'correctly_blocked'}))
    assert not result['all_required_evidence_passed']


def test_no_drop_failed_cases_or_mutate_scope(tmp_path):
    r,w,p=setup(tmp_path);p['case_ids'].pop();loop.write_json(w/'plan.json',p)
    with pytest.raises(ValueError,match='scope changed'):loop.status_report(w,r)


def test_source_drift_invalidates_old_evidence(tmp_path):
    r,w,_=setup(tmp_path);(r/'tracked').write_text('new')
    with pytest.raises(ValueError,match='clean local commit'):loop.status_report(w,r)
    git(r,'add','.');git(r,'commit','-qm','new version')
    with pytest.raises(ValueError,match='identity changed'):loop.status_report(w,r)
    loop.begin(w,r,'Changed exact source after root-cause reproduction')
    assert len(loop.read(w/'loop.json')['rounds'])==2
    assert not loop.status_report(w,r)['all_required_evidence_passed']


def test_same_unexplained_retry_rejected(tmp_path):
    r,w,_=setup(tmp_path)
    with pytest.raises(ValueError,match='Unchanged retry'):loop.begin(w,r,'Synthetic initial reproduction')


def test_log_drift_detected_after_restart(tmp_path):
    r,w,_=setup(tmp_path);seed_tests(w);(w/'full.log').write_text('modified')
    with pytest.raises(ValueError,match='log changed'):loop.status_report(w,r)


def test_review_receipt_omission_and_wrong_version_rejected(tmp_path):
    r,w,_=setup(tmp_path);p=evidence(tmp_path,r,'review');d=loop.read(p);d['coverage'].pop('negative');p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='Every original'):loop.record(w,r,'review',p)
    d['coverage']['negative']='pass';d['snapshot']['tree']='0'*40;p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='exact tested source'):loop.record(w,r,'review',p)


def test_fresh_phase_requires_distinct_reviewer_and_checkout(tmp_path):
    r,w,_=setup(tmp_path);seed_tests(w);loop.record(w,r,'review',evidence(tmp_path,r,'review'));p=evidence(tmp_path,r,'blind');d=loop.read(p);d['reviewer']='review-fixture';p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='distinct independent'):loop.record(w,r,'blind',p)
    d['reviewer']='blind-fixture';d['fresh_checkout']=str(r);p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='distinct exact'):loop.record(w,r,'blind',p)


def test_tests_use_direct_argv_and_zero_coverage_not_pass(tmp_path,monkeypatch):
    r,w,_=setup(tmp_path);calls=[];real=subprocess.run
    def fake(argv,**kw):
        if argv[0]=='git':return real(argv,**kw)
        calls.append((argv,kw));return subprocess.CompletedProcess(argv,0,'no tests ran','')
    monkeypatch.setattr(loop.subprocess,'run',fake)
    result=loop.run_tests(w,r)
    assert len(calls)==2 and all('shell' not in kw for _,kw in calls)
    assert not result['all_required_evidence_passed']
    assert all(t['status']=='unchecked' for t in loop.read(w/'loop.json')['rounds'][0]['tests'])


def test_fingerprint_keeps_original_failure_and_normalizes_temporary_path():
    assert loop.failure_fingerprint('focused','FAILED /tmp/a/test.py in 2.0s')==loop.failure_fingerprint('focused','FAILED /tmp/b/test.py in 4.0s')
    assert loop.failure_fingerprint('focused','FAILED wrong mode')!=loop.failure_fingerprint('focused','FAILED wrong SKU')


def test_same_source_retry_needs_new_actual_evidence(tmp_path):
    r,w,_=setup(tmp_path)
    with pytest.raises(ValueError,match='new evidence'):loop.begin(w,r,'Different wording is not evidence')
    receipt=tmp_path/'environment-repair.log';receipt.write_text('Synthetic missing dependency restoration evidence')
    loop.begin(w,r,'Actual environment evidence changed',receipt)
    with pytest.raises(ValueError,match='new evidence'):loop.begin(w,r,'Again',receipt)


def test_external_log_drift_invalidates_completion(tmp_path):
    r,w,_=setup(tmp_path);seed_tests(w);loop.record(w,r,'review',evidence(tmp_path,r,'review'));loop.record(w,r,'blind',evidence(tmp_path,r,'blind'))
    (tmp_path/'blind.log').write_text('changed external evidence')
    with pytest.raises(ValueError,match='External evidence changed'):loop.status_report(w,r)


def test_runner_identity_recorded_separately_from_target_repo(tmp_path):
    r,w,_=setup(tmp_path)
    row=loop.read(w/'loop.json')['rounds'][0]
    assert row['runner']['path']==str(Path(loop.__file__).resolve())
    assert row['runner']['sha256']==loop.digest(Path(loop.__file__).read_bytes())
    assert not Path(row['runner']['path']).is_relative_to(r)


def test_each_command_has_utc_timestamps_and_skip_evidence(tmp_path,monkeypatch):
    r,w,_=setup(tmp_path);real=subprocess.run
    def fake(argv,**kw):
        if argv[0]=='git':return real(argv,**kw)
        return subprocess.CompletedProcess(argv,0,'2 passed, 1 skipped','')
    monkeypatch.setattr(loop.subprocess,'run',fake);loop.run_tests(w,r)
    for test in loop.read(w/'loop.json')['rounds'][0]['tests']:
        assert test['started_at']<=test['finished_at']
        assert test['started_at'].endswith('+00:00')
        assert test['skipped_counts']==[['1','']]


@pytest.mark.parametrize('replay',['renamed','intervening_evidence','source_return'])
def test_same_source_evidence_cannot_be_replayed(tmp_path,replay):
    r,w,_=setup(tmp_path)
    original=git(r,'rev-parse','HEAD')
    a=tmp_path/'a.log';a.write_text('Synthetic evidence A')
    loop.begin(w,r,'New evidence A',a)
    if replay=='intervening_evidence':
        b=tmp_path/'b.log';b.write_text('Synthetic evidence B')
        loop.begin(w,r,'New evidence B',b)
    elif replay=='source_return':
        (r/'tracked').write_text('different source');git(r,'add','.');git(r,'commit','-qm','different source')
        loop.begin(w,r,'Changed source')
        git(r,'checkout','-q',original)
    renamed=tmp_path/'copy.log';renamed.write_bytes(a.read_bytes())
    before=(w/'loop.json').read_bytes();folders=sorted(p.name for p in w.iterdir())
    with pytest.raises(ValueError,match='replayed bytes'):
        loop.begin(w,r,'A new path is not new evidence',renamed)
    assert (w/'loop.json').read_bytes()==before
    assert sorted(p.name for p in w.iterdir())==folders


def test_same_path_new_bytes_can_begin_without_inheriting_results(tmp_path):
    r,w,_=setup(tmp_path)
    p=tmp_path/'repair.log';p.write_text('Synthetic evidence first observation')
    first=loop.begin(w,r,'First observation',p)
    p.write_text('Synthetic evidence materially changed observation')
    second=loop.begin(w,r,'Changed actual bytes',p)
    assert first['reason_evidence']['sha256']!=second['reason_evidence']['sha256']
    assert second['tests']==[] and second['evidence']=={}
    assert loop.status_report(w,r)['all_required_evidence_passed'] is False


@pytest.mark.parametrize('mutation',['modified','deleted'])
def test_current_reason_evidence_drift_rejected(tmp_path,mutation):
    r,w,_=setup(tmp_path)
    p=tmp_path/'repair.log';p.write_text('Synthetic new observation')
    loop.begin(w,r,'New actual evidence',p)
    before=(w/'loop.json').read_bytes()
    if mutation=='modified':p.write_text('Changed after begin')
    else:p.unlink()
    with pytest.raises(ValueError,match='reason evidence missing or changed'):
        loop.status_report(w,r)
    assert (w/'loop.json').read_bytes()==before
