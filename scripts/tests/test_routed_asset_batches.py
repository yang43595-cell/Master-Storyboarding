"""Offline production handoff through the two real asset batch entry points."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.core.artifacts import MANIFEST_PATH, digest
from scripts.core.io import read_json, write_json
from test_formal_package_submission import confirmed_narrative
from test_production_handoff import kickoff


ROOT = Path(__file__).resolve().parents[2]
TARGET = 'AST-CHAR-A@main'


def run(project, command, *args):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/stage_context_cli.py'), command,
                           '--project', str(project), *map(str, args)], capture_output=True, text=True)


def checked(project, command, *args):
    result = run(project, command, *args)
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def batch(project, context, target, entry, *, objective='核对合成资产'):
    request = project / f'{context}-request.json'
    receipt = project / f'{context}-receipt.json'
    if not request.exists():
        write_json(request, {'image_targets': [target]})
    phase = ['--skill', 'asset-image-assistant', '--objective', objective] if entry == 'prepare' else [
        '--to-skill', 'asset-image-assistant']
    args = [*phase, '--context-id', context, '--request-json', request, '--batch-receipt', receipt]
    return run(project, entry, *args), request, receipt


@pytest.mark.parametrize('entry', ['prepare', 'handoff'])
def test_routed_asset_batch_keeps_exact_output_owned_until_review(tmp_path, entry):
    kickoff(tmp_path)
    confirmed_narrative(tmp_path)
    first, request, receipt = batch(tmp_path, 'first', TARGET, entry)
    assert first.returncode == 0, first.stderr or first.stdout
    assert batch(tmp_path, 'first', TARGET, entry)[0].returncode == 0
    assert batch(tmp_path, 'overlap', TARGET, 'handoff')[0].returncode != 0
    continue_args = ['--to-skill', 'asset-image-assistant', '--context-id', 'first', '--receive-batch']
    assert checked(tmp_path, 'handoff', *continue_args)['status'] == 'pending'

    # The first batch owns just this output. The same generic grant can cover
    # a separate output for the same asset in another batch.
    second = batch(tmp_path, 'second', 'AST-CHAR-A@alternate', 'handoff')[0]
    assert second.returncode == 0, second.stderr or second.stdout
    picture = tmp_path / 'synthetic.png'
    picture.write_bytes(b'digest fixture only')
    write_json(receipt, {'context_id': 'first', 'request_sha256': digest(request), 'items': [{
        'target': TARGET, 'candidate_id': 'synthetic-candidate', 'observer': 'synthetic worker',
        'visual_status': 'passed', 'observations': '合成测试的观察记录',
        'path': picture.name, 'sha256': digest(picture),
    }]})
    assert checked(tmp_path, 'handoff', *continue_args)['status'] == 'ready_for_review'
    auto = checked(tmp_path, 'handoff')
    review = [row for row in auto['steps'] if row['action'] == 'asset-review']
    assert [row['output_ids'] for row in review] == [[TARGET]]
    assert not any(TARGET in row.get('output_ids', []) for row in auto['next_actions'])
    assert batch(tmp_path, 'first', TARGET, 'handoff')[0].returncode != 0


def test_two_concurrent_asset_writers_cannot_bind_the_same_output(tmp_path):
    kickoff(tmp_path)
    confirmed_narrative(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda work: batch(tmp_path, work[0], TARGET, work[1])[0],
                                [('via-prepare', 'prepare'), ('via-handoff', 'handoff')]))
    assert sum(result.returncode == 0 for result in results) == 1


def test_generic_grant_dispatches_declared_sibling_without_inventing_outputs(tmp_path):
    kickoff(tmp_path)
    confirmed_narrative(tmp_path)
    alternate = 'AST-CHAR-A@alternate'
    choice = tmp_path / '04_资产/资产需求调整.json'
    cup_asset = 'AST-PROP-CUP'
    cup = {'asset_id': cup_asset, 'category': 'PROP', 'name': '合成杯子', 'narrative_purpose': '合成用途',
           'source_paragraph_ids': ['STP-01'], 'image_decision': '需要制作', 'image_reason': '合成测试'}
    write_json(choice, {'revision': 1, 'added': [cup], 'excluded': {}, 'replaced': {}, 'history': [],
                        'character_output_requirements': {'AST-CHAR-A': {
                            'source_revision': 'R01', 'evidence': '合成测试的已登记最终输出',
                            'outputs': {'main': '人物1x4', 'alternate': '人物1x4'},
                        }}})
    write_json(tmp_path / 'first-request.json', {
        'image_targets': [TARGET], 'image_output_kinds': {TARGET: '人物1x4'},
    })
    result, _, _ = batch(tmp_path, 'first', TARGET, 'handoff')
    assert result.returncode == 0, result.stderr or result.stdout
    # A damaged unrelated task cache carries no asset batch binding.
    unrelated = tmp_path / '00_项目管理/执行上下文/批次/unrelated/task_basis.json'
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text('{', encoding='utf-8')
    routed = checked(tmp_path, 'handoff')
    action, = [row for row in routed['next_actions'] if row['action'] == 'assets']
    assert action['output_ids'] == [alternate]
    assert action['open_output_asset_ids'] == [cup_asset]
    cup_task = tmp_path / 'cup-request.json'
    write_json(cup_task, {'image_targets': [cup_asset + '@main']})
    checked(tmp_path, 'prepare', '--skill', 'asset-image-assistant', '--objective', '本批杯子',
            '--context-id', action['context_id'], '--request-json', cup_task)


def test_bound_asset_batch_cannot_move_to_different_narrative_bytes(tmp_path):
    kickoff(tmp_path)
    confirmed_narrative(tmp_path)
    first, _, _ = batch(tmp_path, 'first', TARGET, 'handoff')
    assert first.returncode == 0, first.stderr or first.stdout
    card = Path(json.loads(first.stdout)['path'])
    original_card = card.read_bytes()
    story = tmp_path / 'N.json'
    payload = read_json(story)
    payload['change_summary'] = ['Synthetic state with different adopted story bytes']
    write_json(story, payload)
    manifest = read_json(tmp_path / MANIFEST_PATH)
    manifest['artifacts'][0]['checksum'] = digest(story)
    write_json(tmp_path / MANIFEST_PATH, manifest)
    resume = batch(tmp_path, 'first', TARGET, 'handoff')[0]
    assert resume.returncode != 0 and '当前未就绪' in resume.stderr
    assert card.read_bytes() == original_card
    fresh = batch(tmp_path, 'fresh', TARGET, 'handoff')[0]
    assert fresh.returncode == 0, fresh.stderr or fresh.stdout


def test_explicit_receipt_binds_main_context_without_a_batch_id(tmp_path):
    kickoff(tmp_path)
    confirmed_narrative(tmp_path)
    request = tmp_path / 'main-request.json'
    receipt = tmp_path / 'main-receipt.json'
    write_json(request, {'image_targets': [TARGET]})
    args = ['--skill', 'asset-image-assistant', '--objective', '同一原批', '--request-json', request,
            '--batch-receipt', receipt]
    checked(tmp_path, 'prepare', *args)
    extra = run(tmp_path, 'handoff', '--to-skill', 'asset-image-assistant',
                '--request-json', request, '--batch-receipt', receipt)
    assert extra.returncode != 0 and '须指定 --context-id' in extra.stderr
    assert checked(tmp_path, 'handoff', '--to-skill', 'asset-image-assistant', '--receive-batch')['status'] == 'pending'
    assert batch(tmp_path, 'overlap', TARGET, 'handoff')[0].returncode != 0
    checked(tmp_path, 'prepare', *args)
