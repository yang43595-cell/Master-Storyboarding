"""Engineering regression rounds; not a business ledger or an autonomous fixer.

Runs only pytest/Node tests. Independent review and fresh blind execution are
external, byte-bound evidence; this runner cannot manufacture either judgment.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.core.io import file_lock, write_json


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def now():
    return datetime.now(timezone.utc).isoformat()


def snapshot(repo):
    repo = Path(repo).resolve()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE).decode().strip()
    if git('status', '--porcelain'):
        raise ValueError('Freeze a clean local commit before a round; do not overwrite earlier evidence')
    return {'commit': git('rev-parse', 'HEAD'), 'tree': git('rev-parse', 'HEAD^{tree}')}


def init(work, plan):
    work = Path(work)
    if work.exists():
        raise ValueError('New loop workspace must not exist; resume with status/begin instead')
    ids = plan.get('case_ids')
    if (set(plan) != {'case_ids', 'case_kinds', 'pytest_targets', 'node_targets'} or not isinstance(ids, list)
            or not ids or any(not isinstance(i, str) or not i.strip() for i in ids) or len(ids) != len(set(ids))):
        raise ValueError('Explicit immutable required case IDs and test targets required')
    if (not isinstance(plan['case_kinds'], dict) or set(plan['case_kinds']) != set(ids)
            or any(k not in {'natural','synthetic','risk_retest'} for k in plan['case_kinds'].values())):
        raise ValueError('Keep natural, synthetic and known-risk coverage distinct')
    for key in ('pytest_targets', 'node_targets'):
        if not isinstance(plan[key], list) or any(not isinstance(t, str) or not t for t in plan[key]):
            raise ValueError('Test targets must be arrays of repository-relative paths')
    if not plan['pytest_targets']:
        raise ValueError('No test coverage is not a pass')
    work.mkdir(parents=True)
    write_json(work / 'plan.json', plan)
    write_json(work / 'loop.json', {'plan_sha256': digest((work/'plan.json').read_bytes()), 'rounds': []})


def load(work):
    work = Path(work).resolve()
    state, plan = read(work/'loop.json'), read(work/'plan.json')
    if digest((work/'plan.json').read_bytes()) != state['plan_sha256']:
        raise ValueError('Required scope changed; create a separately declared loop, never delete failures')
    return work, state, plan


def begin(work, repo, reason, reason_evidence=None):
    work, state, plan = load(work)
    if not reason.strip():
        raise ValueError('Record the evidence-based reason for this round, not a retry count')
    snap = snapshot(repo)
    evidence = None
    if reason_evidence is not None:
        path = Path(reason_evidence).resolve()
        evidence = {'path': str(path), 'sha256': digest(path.read_bytes())}
    prior_same_source = [row for row in state['rounds'] if row['snapshot'] == snap]
    if prior_same_source:
        used_hashes = {row['reason_evidence']['sha256'] for row in prior_same_source
                       if row.get('reason_evidence')}
        if not evidence or evidence['sha256'] in used_hashes:
            raise ValueError('Unchanged retry requires materially new evidence, not renamed or replayed bytes')
    n = len(state['rounds']) + 1
    (work/f'round-{n:04d}').mkdir()
    state['rounds'].append({'number': n, 'snapshot': snap, 'repo': str(Path(repo).resolve()),
                           'runner': {'path': str(Path(__file__).resolve()), 'sha256': digest(Path(__file__).read_bytes())},
                           'reason': reason, 'reason_evidence': evidence, 'started_at': now(), 'tests': [], 'evidence': {}})
    write_json(work/'loop.json', state)
    return state['rounds'][-1]


def current(work, repo):
    work, state, plan = load(work)
    if not state['rounds']:
        raise ValueError('Begin an explicit round first')
    row = state['rounds'][-1]
    if row.get('runner') != {'path': str(Path(__file__).resolve()), 'sha256': digest(Path(__file__).read_bytes())}:
        raise ValueError('Runner source changed; old orchestration evidence is stale')
    if snapshot(repo) != row['snapshot'] or str(Path(repo).resolve()) != row['repo']:
        raise ValueError('Git source identity changed; old review/tests are stale, begin a new round')
    reason = row.get('reason_evidence')
    if reason:
        path = Path(reason['path'])
        if not path.is_file() or digest(path.read_bytes()) != reason['sha256']:
            raise ValueError('Current round reason evidence missing or changed; begin a justified new round')
    return work, state, plan, row


def failure_fingerprint(kind, text):
    # Keep original logs; normalize only volatile timing/temp paths for grouping.
    lines = [s for s in text.splitlines() if re.search(r'FAILED |ERROR |Error:|AssertionError|not ok ', s)]
    value = '\n'.join(lines) if lines else text[-2000:]
    value = re.sub(r'/tmp/[^\s:]+', '<tmp>', value)
    value = re.sub(r'\b\d+(?:\.\d+)?s\b', '<elapsed>', value)
    return digest((kind+'\n'+value).encode())


def run_tests(work, repo):
    work, state, plan, row = current(work, repo)
    if row['tests']:
        raise ValueError('This round already has test evidence; preserve it and begin a justified new round')
    repo = Path(repo).resolve()
    for key in ('pytest_targets', 'node_targets'):
        for value in plan[key]:
            path = (repo / value.split('::')[0]).resolve()
            if not path.is_relative_to(repo) or not path.exists() or value.startswith('-'):
                raise ValueError('Test target missing/outside frozen repository: '+value)
    commands = [('focused', [sys.executable, '-m', 'pytest', '-c', 'config/pytest.ini', *plan['pytest_targets']]),
                ('full', [sys.executable, '-m', 'pytest', '-c', 'config/pytest.ini', 'scripts/tests', 'tests'])]
    if plan['node_targets']:
        commands.append(('node', ['node', '--test', *plan['node_targets']]))
    for label, command in commands:
        # Direct argv, never shell pipelines or arbitrary repair/production commands.
        started_at = now()
        try:
            result = subprocess.run(command, cwd=repo, capture_output=True, text=True)
            output, code = result.stdout+'\n'+result.stderr, result.returncode
        except OSError as exc:
            output, code = str(exc), None
        target = work/f"round-{row['number']:04d}"/(label+'.log')
        target.write_text(output, encoding='utf-8')
        has_tests = bool(re.search(r'\b[1-9]\d* passed\b|(?:#|ℹ) pass [1-9]\d*', output))
        status = 'blocked' if code is None else ('passed' if code == 0 and has_tests else 'failed' if code else 'unchecked')
        row['tests'].append({'kind': label, 'argv': command, 'exit_code': code, 'status': status,
                             'started_at': started_at, 'finished_at': now(),
                             'skipped_counts': re.findall(r'\b(\d+) skipped\b|(?:#|ℹ) skipped (\d+)', output),
                             'log': str(target), 'sha256': digest(target.read_bytes()),
                             'fingerprint': None if status == 'passed' else failure_fingerprint(label, output),
                             'failures': [{'line': line, 'fingerprint': failure_fingerprint(label,line)}
                                          for line in output.splitlines() if re.match(r'FAILED |ERROR |not ok ', line)]})
        write_json(work/'loop.json', state)
    current(work, repo)  # Tests modifying source cannot retain a valid frozen round.
    return status_report(work, repo)


def record(work, repo, phase, receipt):
    work, state, plan, row = current(work, repo)
    if phase not in {'review', 'blind'} or phase in row['evidence']:
        raise ValueError('One immutable review and blind receipt per round')
    receipt = Path(receipt).resolve()
    value = read(receipt)
    if value.get('snapshot') != row['snapshot'] or not value.get('reviewer') or value.get('phase') != phase:
        raise ValueError('Receipt must identify its actual reviewer and exact tested source')
    coverage = value.get('coverage')
    if (not isinstance(coverage, dict) or set(coverage) != set(plan['case_ids'])
            or any(v not in {'pass', 'fail', 'correctly_blocked', 'blocked_external', 'not_tested', 'out_of_authorization'} for v in coverage.values())):
        raise ValueError('Every original required case must remain explicitly classified')
    records = value.get('evidence_files')
    if not isinstance(records, list) or not records:
        raise ValueError('Actual evidence files are required, not a PASS assertion alone')
    for item in records:
        path = Path(item['path']).resolve()
        if not path.is_file() or digest(path.read_bytes()) != item.get('sha256'):
            raise ValueError('External receipt evidence missing or changed')
    if phase == 'blind':
        prior = row['evidence'].get('review')
        if not prior or read(prior['path']).get('reviewer') == value['reviewer']:
            raise ValueError('Fresh blind phase follows a distinct independent review')
        if any(t['status'] != 'passed' for t in row['tests']) or len(row['tests']) < 2:
            raise ValueError('Repair failed/unchecked tests before fresh blind execution')
        fresh = value.get('fresh_checkout')
        if (not fresh or Path(fresh).resolve() == Path(repo).resolve() or snapshot(fresh) != row['snapshot']
                or value.get('fresh_execution') is not True):
            raise ValueError('Blind evidence requires a distinct exact clean checkout and fresh execution')
    target = work/f"round-{row['number']:04d}"/(phase+'.json')
    target.write_bytes(receipt.read_bytes())
    row['evidence'][phase] = {'path': str(target), 'sha256': digest(target.read_bytes())}
    write_json(work/'loop.json', state)
    return status_report(work, repo)


def status_report(work, repo):
    work, state, plan, row = current(work, repo)
    pending = []
    if len(row['tests']) != 2 + bool(plan['node_targets']):
        pending.append('tests_not_complete')
    for test in row['tests']:
        if digest(Path(test['log']).read_bytes()) != test['sha256']:
            raise ValueError('Recorded test log changed')
        if test['status'] != 'passed':
            pending.append(test['kind']+':'+test['status'])
    for phase in ('review', 'blind'):
        entry = row['evidence'].get(phase)
        if not entry:
            pending.append(phase+':unchecked'); continue
        path = Path(entry['path'])
        if digest(path.read_bytes()) != entry['sha256']:
            raise ValueError('Recorded external receipt changed')
        value = read(path)
        if phase == 'blind' and snapshot(value['fresh_checkout']) != row['snapshot']:
            raise ValueError('Fresh blind checkout changed; prior results are stale')
        for evidence in value['evidence_files']:
            if digest(Path(evidence['path']).read_bytes()) != evidence['sha256']:
                raise ValueError('External evidence changed after recording')
        pending += [phase+':'+k+':'+v for k,v in value['coverage'].items()
                    if v != 'pass' and not (v == 'correctly_blocked' and plan['case_kinds'][k] != 'natural')]
    return {'round': row['number'], 'snapshot': row['snapshot'], 'all_required_evidence_passed': not pending,
            'pending': pending, 'production_authorized': False,
            'scope': 'Engineering test/review/declared fresh-blind evidence only; not proof of human cognition or business approval'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init','begin','test','record','status'])
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--reason', default='')
    parser.add_argument('--reason-evidence', type=Path)
    parser.add_argument('--phase', choices=['review','blind'])
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    if args.action == 'init':
        init(args.workspace, read(args.plan)); result = {'initialized': True}
    else:
        with file_lock(args.workspace/'loop.lock'):
            if args.action == 'begin': result = begin(args.workspace,args.repo,args.reason,args.reason_evidence)
            elif args.action == 'test': result = run_tests(args.workspace,args.repo)
            elif args.action == 'record': result = record(args.workspace,args.repo,args.phase,args.receipt)
            else: result = status_report(args.workspace,args.repo)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
