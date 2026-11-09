"""Local CLI replays of coordination, RedFrame and usage failures. No remote I/O."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts.canvas_readback import compare_baseline, digest
from scripts.core import artifacts
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state, write_project_state
from scripts.core.project_kickoff import REQUEST_PATH
from scripts.tests.test_delivery_preflight import delivery, bind_synthetic_canvas, bind_synthetic_quality
from scripts.tests.test_session_usage import record


ROOT = Path(__file__).resolve().parents[2]


def run_cli(module, *args):
    return subprocess.run([sys.executable, '-m', module, *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})


def redframe_fixture(root):
    frozen, observed = delivery(root)
    frozen['target_project_id'] = observed['target_project_id'] = 'synthetic-project'
    for row in frozen['drafts']:
        row['parameters'].update(model='fixture', aspect_ratio='9:16', resolution='480p')
    write_json(root / REQUEST_PATH, {'kickoff': {
        'decision_ref': 'synthetic decision only', 'product': {'mode': 'none'},
        'canvas': dict(platform='redframe', model='fixture', aspect_ratio='9:16', resolution='480p',
                       target_url=frozen['target_url'], target_project_id='synthetic-project')}})
    bind_synthetic_canvas(root, frozen, observed)
    return frozen, observed


def canvas_cli(root, frozen, observed=None, *extra):
    write_json(root / 'frozen.json', frozen)
    args = ['--delivery', '--project', root, '--frozen', root / 'frozen.json']
    if observed is None:
        args.append('--preflight')
    else:
        write_json(root / 'observed.json', observed)
        args += ['--readback', root / 'observed.json']
    return run_cli('scripts.canvas_readback', *args, *extra)


def test_redframe_full_cli_and_saved_graph_readback(tmp_path):
    frozen, observed = redframe_fixture(tmp_path)
    assert canvas_cli(tmp_path, frozen).returncode == 0
    result = canvas_cli(tmp_path, frozen, observed)
    assert result.returncode == 0, result.stdout
    report = json.loads(result.stdout)
    assert set(report['canvas_input_checks'].values()) == {'passed'}
    assert report['production_ready'] is False
    assert report['generation_authorization'] == 'not_checked'
    # Source validity is rechecked even with a matching saved graph and old PASS.
    (tmp_path / 'director.json').write_text('{"fixture":"changed"}')
    assert canvas_cli(tmp_path, frozen, observed).returncode != 0


@pytest.mark.parametrize('mutation', ['missing_binding', 'missing_edge', 'wrong_port', 'wrong_source', 'direct_action',
                                     'duplicate_slot', 'unmentioned', 'timeline_end', 'timeline_gap'])
def test_redframe_preflight_rejects_declared_conflicts(tmp_path, mutation):
    frozen, observed = redframe_fixture(tmp_path)
    row = frozen['drafts'][0]
    if mutation == 'missing_binding':
        row['reference_checks'][0].pop('canvas_binding')
    elif mutation == 'missing_edge':
        row['edges'] = []
    elif mutation == 'wrong_port':
        row['edges'][0]['source_port'] = 'wrong'
    elif mutation == 'wrong_source':
        row['source_nodes'][0]['sha256'] = '0' * 64
    elif mutation == 'direct_action':
        row['reference_checks'][0]['role'] = 'action'
        row['reference_checks'][0]['canvas_binding'] = dict(mode='direct', purpose='synthetic action',
                                                           prompt_reference=True)
    elif mutation == 'duplicate_slot':
        row['ordered_media'] *= 2
        row['reference_checks'] *= 2
    elif mutation == 'unmentioned':
        row['reference_checks'][0]['canvas_binding']['prompt_reference'] = False
    elif mutation == 'timeline_end':
        row['prompt'] = row['prompt'].replace('1–4秒', '1–3秒')
    else:
        row['prompt'] = row['prompt'].replace('1–4秒', '2–5秒')
    bind_synthetic_quality(tmp_path, frozen)
    result = canvas_cli(tmp_path, frozen)
    assert result.returncode != 0
    assert 'canvas' in json.loads(result.stdout)['error']


@pytest.mark.parametrize('field', ['edges', 'source_nodes', 'native_mentions', 'prompt', 'parameters', 'ordered_media'])
def test_saved_whole_node_conflicts_fail_cli(tmp_path, field):
    frozen, observed = redframe_fixture(tmp_path)
    row = observed['drafts'][0]
    if isinstance(row[field], list):
        row[field] = []
    elif isinstance(row[field], dict):
        row[field]['duration'] = 28
    else:
        row[field] += '\nexisting edit'
    result = canvas_cli(tmp_path, frozen, observed)
    assert result.returncode == 1, result.stdout
    assert not json.loads(result.stdout)['ok']


def test_same_file_distinct_purposes_and_unmentioned_input_are_explicit(tmp_path):
    frozen, observed = redframe_fixture(tmp_path)
    row = frozen['drafts'][0]
    row['ordered_media'] *= 2
    second = copy.deepcopy(row['reference_checks'][0])
    second['role'] = 'scale'
    second['canvas_binding'] = dict(mode='direct', purpose='synthetic scale comparison',
                                   prompt_reference=False, unmentioned_reason='Visible input for scale comparison')
    row['reference_checks'].append(second)
    bind_synthetic_quality(tmp_path, frozen)
    observed['drafts'][0] = copy.deepcopy(row)
    observed['drafts'][0]['native_mentions'] = [{**row['ordered_media'][0],
        'entity_type': 'fixture-chip', 'native_noneditable_entity': True}]
    observed['frozen_sha256'] = digest(frozen)
    result = canvas_cli(tmp_path, frozen, observed)
    assert result.returncode == 0, result.stdout
    # Renaming the upload route does not allow duplicate same-purpose slots.
    second['canvas_binding']['purpose'] = row['reference_checks'][0]['canvas_binding']['purpose']
    bind_synthetic_quality(tmp_path, frozen)
    result = canvas_cli(tmp_path, frozen)
    assert 'duplicate same-purpose' in result.stdout


def test_write_preflight_detects_existing_edit_and_requires_complete_baseline(tmp_path):
    frozen, baseline = redframe_fixture(tmp_path)
    current = copy.deepcopy(baseline)
    current['phase'] = 'before_write'
    write_json(tmp_path / 'baseline.json', baseline)
    write_json(tmp_path / 'current.json', current)
    args = ('--baseline', tmp_path / 'baseline.json', '--current', tmp_path / 'current.json')
    result = canvas_cli(tmp_path, frozen, None, *args)
    assert result.returncode == 0, result.stdout
    current['drafts'][1]['parameters']['duration'] = 28
    write_json(tmp_path / 'current.json', current)
    result = canvas_cli(tmp_path, frozen, None, *args)
    assert result.returncode == 2
    assert json.loads(result.stdout)['baseline_check']['changed_node_ids'] == ['two']
    baseline['drafts'][0].pop('native_mentions')
    write_json(tmp_path / 'baseline.json', baseline)
    assert canvas_cli(tmp_path, frozen, None, *args).returncode == 2


@pytest.mark.parametrize('side', ['both', 'baseline', 'current'])
@pytest.mark.parametrize('fields', [('edges',), ('source_nodes',), ('edges', 'source_nodes')])
def test_write_baseline_rejects_missing_graph_readback(tmp_path, side, fields):
    frozen, baseline = redframe_fixture(tmp_path)
    current = copy.deepcopy(baseline)
    current['phase'] = 'before_write'
    readings = [baseline, current] if side == 'both' else [baseline if side == 'baseline' else current]
    for reading in readings:
        for row in reading['drafts']:
            for field in fields:
                row.pop(field)
    with pytest.raises(ValueError, match='collected edges and source_nodes'):
        compare_baseline(baseline, current)
    write_json(tmp_path / 'baseline.json', baseline)
    write_json(tmp_path / 'current.json', current)
    result = canvas_cli(tmp_path, frozen, None, '--baseline', tmp_path / 'baseline.json',
                        '--current', tmp_path / 'current.json')
    assert result.returncode == 2
    assert 'collected edges and source_nodes' in result.stdout


def test_write_baseline_accepts_explicit_empty_graph_readback():
    baseline = dict(target_url='https://fixture.invalid/canvas', phase='after_refresh', drafts=[dict(
        node_id='empty-draft', prompt='Synthetic text.', parameters=dict(duration=4),
        ordered_media=[], native_mentions=[], edges=[], source_nodes=[])])
    current = copy.deepcopy(baseline)
    current['phase'] = 'before_write'
    assert compare_baseline(baseline, current)['ok']


def usage_log(root, *, missing=False, partial=False):
    path = root / 'synthetic-usage.jsonl'
    payload = record('one')
    if missing:
        payload['usage'].pop('total_tokens')
    events = [{'type': 'token_usage_record', 'ordinal': 1, 'payload': payload}]
    path.write_text('\n'.join(map(json.dumps, events)) + ('\n{"partial":' if partial else '\n'))
    return path


@pytest.mark.parametrize('entry', ['prepare', 'ensure-prepared', 'usage-check', 'canvas'])
@pytest.mark.parametrize('stop', ['input', 'total', 'requests', 'missing', 'partial'])
def test_all_entry_points_stop_on_equal_limit_or_unknown(tmp_path, entry, stop):
    log = usage_log(tmp_path, missing=stop == 'missing', partial=stop == 'partial')
    limit = {'input': ('--max-input', '100'), 'total': ('--max-total', '107'),
             'requests': ('--max-requests', '1')}.get(stop, ('--max-total', '999'))
    args = ['--usage-log', log, '--usage-turn-id', 'turn-1', *limit]
    if entry == 'canvas':
        frozen, observed = redframe_fixture(tmp_path)
        result = canvas_cli(tmp_path, frozen, None, *args)
    else:
        args = [entry, '--project', tmp_path, *args]
        if entry in ('prepare', 'ensure-prepared'):
            args += ['--skill', 'creative-director']
        if entry == 'prepare':
            args += ['--objective', 'synthetic preparation']
        result = run_cli('scripts.stage_context_cli', *args)
    assert result.returncode != 0
    assert '执行预算停止' in result.stdout + result.stderr
    assert not (tmp_path / '00_项目管理/执行上下文').exists()


def test_direct_video_checkpoint_reports_scope_peak_total_and_calls(tmp_path):
    log = usage_log(tmp_path)
    result = run_cli('scripts.stage_context_cli', 'usage-check', '--project', tmp_path,
                     '--usage-log', log, '--usage-turn-id', 'turn-1',
                     '--max-input', '101', '--max-total', '108', '--max-requests', '2')
    assert result.returncode == 0, result.stderr
    usage = json.loads(result.stdout)['run_usage']
    assert usage['peak_input_tokens'] == 100
    assert usage['totals']['total_tokens'] == 107 and usage['requests'] == 1
    assert usage['scope']['turn_id'] == 'turn-1' and usage['last_response_id'] == 'one'
    assert 'calls' not in usage and usage['cost'] is None
    assert not (tmp_path / '00_项目管理').exists()


def test_canvas_checkpoint_reports_the_same_usage_without_bypassing_source_gate(tmp_path):
    log = usage_log(tmp_path)
    frozen, observed = redframe_fixture(tmp_path)
    args = ('--usage-log', log, '--usage-turn-id', 'turn-1', '--max-total', '108')
    result = canvas_cli(tmp_path, frozen, observed, *args)
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)['run_usage']['peak_input_tokens'] == 100
    (tmp_path / 'director.json').write_text('{"fixture":"changed"}')
    assert canvas_cli(tmp_path, frozen, observed, *args).returncode != 0


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local video tools unavailable')
def test_redframe_action_file_graph_native_reference_and_actual_duration(tmp_path):
    frozen, observed = redframe_fixture(tmp_path)
    video = tmp_path / 'synthetic-action.mov'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=16x16:r=10',
                    '-t', '1', '-c:v', 'png', '-pix_fmt', 'rgb24', str(video)], check=True, capture_output=True)
    sha = artifacts.digest(video)
    item = dict(artifact_id='ACTION', logical_id='ACTION', revision='R01', status='current',
                user_approved=True, path_or_url=video.name, checksum=sha, upload_bindings=[dict(
                    target_url=frozen['target_url'], node_id='action-node', media_id='action-media',
                    revision='R01', sha256=sha)])
    manifest = artifacts.load_manifest(tmp_path)
    manifest['artifacts'].append(item)
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    state = load_project_state(tmp_path)
    state['current_artifacts'].append('ACTION')
    state['user_confirmations'].append(dict(artifact_ids=['ACTION']))
    write_project_state(tmp_path, state)
    row = frozen['drafts'][0]
    row['ordered_media'].append(dict(node_id='action-node', media_id='action-media'))
    ref = dict(artifact_id='ACTION', revision='R01', sha256=sha, role='action', kind='video',
               duration=1, entity_ids=['ACTOR'], canvas_binding=dict(mode='edge', purpose='synthetic action',
                   prompt_reference=True, source_port='main', target_port='input-2'))
    row['reference_checks'].append(ref)
    row['edges'].append(dict(source_node_id='action-node', source_port='main', target_node_id=row['node_id'],
                             target_port='input-2', slot=2))
    row['source_nodes'].append(dict(node_id='action-node', port='main', sha256=sha))
    bind_synthetic_quality(tmp_path, frozen)
    observed['drafts'][0] = copy.deepcopy(row)
    observed['drafts'][0]['native_mentions'] = [dict(**media, entity_type='fixture-chip',
        native_noneditable_entity=True) for media in row['ordered_media']]
    observed['frozen_sha256'] = digest(frozen)
    result = canvas_cli(tmp_path, frozen, observed)
    assert result.returncode == 0, result.stdout
    ref['duration'] = 2
    bind_synthetic_quality(tmp_path, frozen)
    assert 'actual video' in canvas_cli(tmp_path, frozen).stdout


@pytest.mark.parametrize('entry', ['usage-check', 'canvas'])
def test_direct_checkpoints_cannot_omit_registered_required_usage(tmp_path, entry):
    from scripts.core.candidate_test import configure
    configure(tmp_path, 'synthetic-metering', 'synthetic authorization', [], usage_contract={
        'required': True, 'turn_id': 'turn-1',
        'limits': dict(max_input=101, max_total=108, max_requests=2)})
    if entry == 'usage-check':
        result = run_cli('scripts.stage_context_cli', entry, '--project', tmp_path)
    else:
        # The meter rejects before any attempt to consume candidate/production data.
        write_json(tmp_path / 'frozen.json', {})
        result = run_cli('scripts.canvas_readback', '--private-preview', '--project', tmp_path,
                         '--frozen', tmp_path / 'frozen.json', '--preflight')
    assert result.returncode != 0
    assert '准确 turn' in result.stdout + result.stderr


def test_usage_boundary_cannot_silently_drop_a_response_without_ordinal(tmp_path):
    log = usage_log(tmp_path)
    with log.open('a') as stream:
        stream.write(json.dumps(dict(type='token_usage_record', payload=record('missing-ordinal'))) + '\n')
    boundary = tmp_path / 'boundaries.json'
    write_json(boundary, [dict(turn_id='turn-1', logs=[str(log)], through_ordinal=1)])
    result = run_cli('scripts.stage_context_cli', 'usage-check', '--project', tmp_path,
                     '--usage-boundaries-json', boundary, '--max-total', '1000')
    assert result.returncode != 0
    assert 'missing_usage_ordinal' in result.stdout + result.stderr
    assert 'usage_unknown' in result.stdout + result.stderr
