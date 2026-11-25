"""Local CLI replays of coordination, RedFrame and usage failures. No remote I/O."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts.canvas_readback import compare_baseline, digest, validate_canvas_inputs
from scripts.core import artifacts
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state, write_project_state
from scripts.core.project_kickoff import REQUEST_PATH
from scripts.tests.test_delivery_preflight import delivery, bind_synthetic_canvas, bind_synthetic_quality
from scripts.tests.test_generation_boundaries import director_body_sample
from scripts.tests.test_session_usage import record


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('duration_key', ['duration', 'duration_seconds'])
def test_canvas_checks_duration_aliases_independent_of_prompt(duration_key):
    row = dict(node_id='synthetic-draft', ordered_media=[], reference_checks=[],
               edges=[], source_nodes=[], prompt='人物抬手，接着按下按钮。',
               shot_durations=[1, 3], parameters={duration_key: 4})
    validate_canvas_inputs(row)
    row['parameters'][duration_key] = 5
    with pytest.raises(ValueError, match='逐镜合计'):
        validate_canvas_inputs(row)
    row['parameters'][duration_key] = 4
    row['prompt'] = '0–1秒：人物抬手。\n2–4秒：按下按钮。'
    validate_canvas_inputs(row)


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
    bind_redframe_production_sources(root, frozen, observed)
    return frozen, observed


def bind_redframe_production_sources(root, frozen, observed, *, product_assets=()):
    manifest = artifacts.load_manifest(root)
    state = load_project_state(root)

    def register(ident, logical_id, filename, payload):
        write_json(root / filename, payload)
        sha = artifacts.digest(root / filename)
        manifest['artifacts'] = [row for row in manifest['artifacts'] if row['artifact_id'] != ident]
        manifest['artifacts'].append(dict(artifact_id=ident, logical_id=logical_id, type=logical_id,
            revision='R01', checksum=sha, path_or_url=filename, status='current', user_approved=True))
        if ident not in state['current_artifacts']:
            state['current_artifacts'].append(ident)
        state['user_confirmations'] = [row for row in state['user_confirmations']
                                       if row.get('artifact_ids') != [ident]]
        state['user_confirmations'].append(dict(artifact_ids=[ident], scope='artifact',
                                               approval_ref='synthetic only'))
        return dict(artifact_id=ident, revision='R01', sha256=sha)

    narrative = register('NARRATIVE', 'narrative.package', 'narrative.json',
                         {'fixture': 'narrative', 'asset_requirements': list(product_assets)})
    capability = root / 'synthetic-duration-capability.json'
    model_name = frozen['drafts'][0]['parameters']['model']
    write_json(capability, {'model': {'type': 'video', 'providerId': 'fixture',
        'modelId': 'fixture', 'label': model_name, 'mode': ['fixture-reference'],
        'durationResolutionMap': [{'resolution': ['480p'], 'duration': list(range(2, 9))}]}})
    shots = []
    segments = []
    for row in frozen['drafts']:
        ident = row['node_id']
        shot = ident + '-shot'
        row.update(source_segment_id=ident, source_shot_ids=[shot])
        row['parameters']['reference_mode'] = 'fixture-reference'
        shots.append(dict(scene_id=ident, shots=[dict(shot_id=shot, take={'start': f'{shot} starts a new action'})]))
        segments.append(dict(segment_id=ident, source_shot_ids=[shot], prompt=row['prompt'],
                             parameters=copy.deepcopy(row['parameters']),
                             shot_durations=copy.deepcopy(row['shot_durations']),
                             target_edit_duration_seconds=sum(row['shot_durations']),
                             materials=[dict(sha256=ref['sha256']) for ref in row['reference_checks']]))
    for previous, current in zip(segments, segments[1:]):
        previous_shot = previous['source_shot_ids'][-1]
        current_shot = current['source_shot_ids'][0]
        current['cross_scene_continuity'] = dict(
            from_segment_id=previous['segment_id'], decision='new_action_or_time_jump',
            basis='The next synthetic scene explicitly begins a new action.',
            previous_source=dict(shot_id=previous_shot, field='take.start', quote=f'{previous_shot} starts a new action'),
            current_source=dict(shot_id=current_shot, field='take.start', quote=f'{current_shot} starts a new action'),
        )
    director = register('DIRECTOR', 'storyboard.package', 'director.json', {
        'source_narrative': {**narrative, 'path': 'narrative.json'}, 'scenes': shots})
    video = register('VIDEO', 'video.prompt', 'video.json', {
        'source_storyboard': {**director, 'path': 'director.json'}, 'generation_segments': segments,
        'selected_video_model': {
            'id': segments[0]['parameters']['model'], 'platform': 'redframe', 'entry_point': 'fixture-editor',
            'reference_mode': 'fixture-reference', 'confirmed_by_user': True,
            'min_duration_seconds': 2, 'max_duration_seconds': 8,
            'capability_evidence': dict(path=capability.name, sha256=artifacts.digest(capability),
                                        locator='synthetic observation', checked_at='2026-11-15T00:00:00Z'),
        }})
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    write_project_state(root, state)
    frozen['source_artifacts'] = [video, director]
    bind_synthetic_quality(root, frozen)
    observed['source_artifacts'] = copy.deepcopy(frozen['source_artifacts'])
    observed['drafts'] = copy.deepcopy(frozen['drafts'])
    for row in observed['drafts']:
        row['native_mentions'] = [dict(**media, entity_type='fixture-chip', native_noneditable_entity=True)
                                  for media in row['ordered_media']]
    observed['frozen_sha256'] = digest(frozen)


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


def test_formal_redframe_preflight_checks_director_against_submitted_body(tmp_path):
    frozen, _ = redframe_fixture(tmp_path)
    row = frozen['drafts'][0]
    frozen['drafts'] = [row]
    board_path, video_path = tmp_path / 'director.json', tmp_path / 'video.json'
    board, video = [json.loads(path.read_bytes()) for path in (board_path, video_path)]
    source = video['generation_segments'][0]
    shot_id = source['source_shot_ids'][0]
    shot, body, coverage = director_body_sample(shot_id)
    board['schema_version'] = '4.0'
    original_shot = board['scenes'][0]['shots'][0]
    original_shot.update(shot, take={**original_shot['take'], **shot['take']})
    write_json(board_path, board)
    board_sha256 = artifacts.digest(board_path)
    video['source_storyboard']['sha256'] = board_sha256

    def submit(text):
        row['prompt'] = source['prompt'] = text
        write_json(video_path, video)
        new_hashes = {'DIRECTOR': board_sha256, 'VIDEO': artifacts.digest(video_path)}
        manifest = artifacts.load_manifest(tmp_path)
        for artifact in manifest['artifacts']:
            if artifact['artifact_id'] in new_hashes:
                artifact['checksum'] = new_hashes[artifact['artifact_id']]
        write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
        for item in frozen['source_artifacts']:
            item['sha256'] = new_hashes[item['artifact_id']]
        bind_synthetic_quality(tmp_path, frozen)
        return canvas_cli(tmp_path, frozen)

    missing = submit(body)
    assert missing.returncode != 0 and 'director_coverage' in missing.stdout + missing.stderr
    source['director_coverage'] = [coverage]
    passed = submit(body)
    # The fixture's unrelated second segment has no director_coverage.
    assert passed.returncode == 0, passed.stdout + passed.stderr
    cut_missing = submit(body.replace(coverage['cut']['prompt_quote'], ''))
    assert cut_missing.returncode != 0 and 'cut 的落点未出现在实际提交正文' in cut_missing.stdout + cut_missing.stderr
    reaction = coverage['reaction']['prompt_quote']
    after_cut = submit(body.replace(reaction, '') + reaction)
    assert after_cut.returncode != 0 and 'reaction 正文顺序与出镜切点冲突' in after_cut.stdout + after_cut.stderr
    filler = submit('动作自然，画质真实。')
    assert filler.returncode != 0 and '独立泛词' in filler.stdout + filler.stderr


@pytest.mark.parametrize('kind,suffix', [('image', '.png'), ('video', '.mp4')])
def test_redframe_product_pack_accepts_face_source_cards(tmp_path, monkeypatch, kind, suffix):
    from scripts.canvas_readback import compare, validate_submission
    from scripts.core import product_reference_pack

    frozen, observed = redframe_fixture(tmp_path)
    frozen['drafts'] = frozen['drafts'][:1]
    pack_root = tmp_path / 'packs' / 'synthetic' / 'R01'
    pack_root.mkdir(parents=True)
    monkeypatch.setattr(product_reference_pack, 'PACK_ROOT', pack_root.parents[1])
    source = pack_root / ('state' + suffix)
    source.write_bytes((tmp_path / 'frame.png').read_bytes() if kind == 'image' else b'synthetic video identity')
    sha256 = artifacts.digest(source)
    pack_manifest = pack_root / 'manifest.json'
    write_json(pack_manifest, {'product_id': 'synthetic', 'revision': 'R01', 'product_name': 'Synthetic',
        'references': [dict(id='state', role='state', usages=['real_use'], path=source.name,
                            sha256=sha256, source='fixture', coverage='fixture')]})
    request = json.loads((tmp_path / REQUEST_PATH).read_text())
    request['kickoff']['product'] = dict(mode='replace', product_id='synthetic', product_usage='real_use',
        reference_pack_revision='R01', reference_pack_sha256=artifacts.digest(pack_manifest))
    write_json(tmp_path / REQUEST_PATH, request)
    row = frozen['drafts'][0]
    row.update(realistic_face_slots=[1], required_reference_roles={'ACTOR': ['appearance']})
    row['reference_checks'] = [dict(kind=kind, role='appearance', entity_ids=['ACTOR'], asset_id='AST-PRODUCT',
        product_reference_id='state', path=str(source), sha256=sha256,
        canvas_binding=dict(mode='edge', source_port=kind, target_port='in', purpose='fixture', prompt_reference=True))]
    row['prompt'] += ' @图片1' if kind == 'image' else ' @视频1'
    row['edges'], row['source_nodes'] = [], []
    # Register source bytes before constructing observed graph data.
    saved_media = dict(node_id='face-card', media_id='actual-slot', sha256=sha256)
    row['ordered_media'] = [saved_media]
    bind_redframe_production_sources(tmp_path, frozen, observed,
        product_assets=[dict(asset_id='AST-PRODUCT', category='REAL_PRODUCT', reference_paths=[str(pack_manifest)])])
    frozen['source_artifacts'].append(dict(artifact_id='NARRATIVE', revision='R01',
        sha256=artifacts.digest(tmp_path / 'narrative.json')))
    row.pop('ordered_media')
    bind_synthetic_quality(tmp_path, frozen)
    result = validate_submission(frozen, tmp_path, formal_delivery=True, preflight=True)
    assert result['face_source_card_slots'] == {'one': [1]}

    row.update(ordered_media=[saved_media],
        edges=[dict(source_node_id='face-card', source_port=kind, target_node_id='one', target_port='in', slot=1)],
        source_nodes=[dict(node_id='face-card', port=kind, sha256=sha256)])
    bind_synthetic_quality(tmp_path, frozen)
    observed = copy.deepcopy(frozen)
    observed.update(phase='after_refresh', frozen_sha256=digest(frozen))
    observed['drafts'][0]['native_mentions'] = [dict(saved_media, entity_type='fixture-chip', native_noneditable_entity=True)]
    missing = compare(frozen, observed, tmp_path, formal_delivery=True)
    assert not missing['ok'] and missing['face_certification_checks'] == {'one': {'1': 'missing'}}
    source_url = f'assets/face-card{suffix}'
    native_cert = dict(assetId='synthetic-cert', sourceUrl=source_url)
    native_cert['imageDigest' if kind == 'image' else 'videoDigest'] = sha256
    observed['drafts'][0]['face_certifications'] = {'1': dict(
        node_id='face-card', source_url=source_url, faceCertification=native_cert)}
    valid = compare(frozen, observed, tmp_path, formal_delivery=True)
    assert valid['ok'] and valid['face_certification_checks'] == {'one': {'1': 'passed'}}
    stale = copy.deepcopy(observed)
    stale['drafts'][0]['face_certifications']['1']['faceCertification']['sourceUrl'] = 'assets/old-file'
    assert not compare(frozen, stale, tmp_path, formal_delivery=True)['ok']
    row['ordered_media'][0]['sha256'] = '0' * 64
    bind_synthetic_quality(tmp_path, frozen)
    with pytest.raises(ValueError, match='产品包认证源卡须回读实际字节'):
        validate_submission(frozen, tmp_path, formal_delivery=True, preflight=True)


@pytest.mark.parametrize('mutation', ['missing_binding', 'missing_edge', 'wrong_port', 'wrong_source', 'extra_edge_on_direct',
                                     'duplicate_slot', 'unmentioned', 'duration_budget'])
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
    elif mutation == 'extra_edge_on_direct':
        row['reference_checks'][0]['role'] = 'action'
        row['reference_checks'][0]['canvas_binding'] = dict(mode='direct', purpose='synthetic action',
                                                           prompt_reference=True)
    elif mutation == 'duplicate_slot':
        row['ordered_media'] *= 2
        row['reference_checks'] *= 2
    elif mutation == 'unmentioned':
        row['reference_checks'][0]['canvas_binding']['prompt_reference'] = False
    elif mutation == 'duration_budget':
        row['shot_durations'] = [1, 2]
    if mutation in ('duration_budget', 'duplicate_slot'):
        bind_redframe_production_sources(tmp_path, frozen, observed)
    else:
        bind_synthetic_quality(tmp_path, frozen)
    result = canvas_cli(tmp_path, frozen)
    assert result.returncode != 0
    expected = '逐镜合计' if mutation == 'duration_budget' else 'canvas'
    assert expected in json.loads(result.stdout)['error']


def test_money_tree_rush_uses_formal_redframe_write_preflight(tmp_path):
    from PIL import Image

    frozen, observed = redframe_fixture(tmp_path)
    kickoff = json.loads((tmp_path / REQUEST_PATH).read_text())['kickoff']
    kickoff.update(decision_ref='跑这个，产品换成造物者温泉酸面膜，其他不变，速推。',
                   creation={'mode': '原片保真改编', 'reference_scope': '原片用于核对剧情、字幕和镜头'},
                   product={'mode': 'replace', 'product_id': '造物者温泉酸面膜', 'product_usage': 'real_use'})
    answers = tmp_path / 'answers.json'
    write_json(answers, kickoff)
    result = run_cli('scripts.stage_context_cli', 'kickoff', '--project', tmp_path, '--answers-json', answers)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['kickoff']['product']['product_id'] == 'creator-polishing-mask-2'
    assert load_project_state(tmp_path).get('workflow_authorizations', []) == []

    manifest = artifacts.load_manifest(tmp_path)
    state = load_project_state(tmp_path)
    frame = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'FRAME')
    Image.new('RGB', (8, 8), 'green').save(tmp_path / 'second.png')
    second = dict(frame, artifact_id='FRAME-TWO', logical_id='FRAME-TWO', path_or_url='second.png',
                  checksum=artifacts.digest(tmp_path / 'second.png'), upload_bindings=[])
    manifest['artifacts'].append(second)
    state['current_artifacts'].append('FRAME-TWO')
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    write_project_state(tmp_path, state)
    for row in frozen['drafts']:
        row['parameters']['model'] = 'Seedance 2.5'
        row['prompt'] += ' @图片1'
        row['edges'], row['source_nodes'] = [], []
        row['realistic_face_slots'] = []  # The fixture references are solid-color images.
        row['required_reference_roles'] = {'ACTOR': ['identity']}
        row['continuity'] = dict(event_id=row['node_id'])
        ref = row['reference_checks'][0]
        ref.update(role='identity', canvas_binding=dict(mode='direct', purpose='synthetic identity',
                                                        prompt_reference=True))
        ref.pop('state_id')
    frozen['drafts'][1]['reference_checks'][0].update(artifact_id='FRAME-TWO', sha256=second['checksum'])
    bind_redframe_production_sources(tmp_path, frozen, observed)
    for row in frozen['drafts']:
        row.pop('ordered_media')
        row['realistic_face_slots'] = []
    bind_synthetic_quality(tmp_path, frozen)

    state = load_project_state(tmp_path)
    narrative_receipt = next(r for r in state['user_confirmations'] if r.get('artifact_ids') == ['NARRATIVE'])
    state['user_confirmations'].remove(narrative_receipt)
    write_project_state(tmp_path, state)
    result = canvas_cli(tmp_path, frozen)
    assert result.returncode == 2 and 'narrative.package' in result.stdout

    state['user_confirmations'].append(narrative_receipt)
    write_project_state(tmp_path, state)
    result = canvas_cli(tmp_path, frozen)
    assert result.returncode == 2 and 'unconfirmed' in result.stdout and 'FRAME-TWO' in result.stdout

    missing_reference = copy.deepcopy(frozen)
    missing_reference['drafts'][1]['reference_checks'].clear()
    result = canvas_cli(tmp_path, missing_reference)
    assert result.returncode == 2 and '逐槽匹配采用源段' in result.stdout

    first = dict(frozen, drafts=frozen['drafts'][:1])
    director_only = dict(first, source_artifacts=first['source_artifacts'][1:])
    result = canvas_cli(tmp_path, director_only)
    assert result.returncode == 2 and '已采用的视频稿' in result.stdout
    result = canvas_cli(tmp_path, first)
    assert result.returncode == 0, result.stdout
    report = json.loads(result.stdout)
    assert report['source_segment_ids'] == ['one']
    assert report['target_project_id'] == frozen['target_project_id']
    assert report['frozen_sha256'] == digest(first)
    assert report['upload_mapping_pending_nodes'] == ['one']
    assert report['face_source_card_slots'] == {}
    assert report['native_refresh_binding'] == 'not_checked'
    face_plan = copy.deepcopy(first)
    face_row = face_plan['drafts'][0]
    del face_row['realistic_face_slots']
    bind_synthetic_quality(tmp_path, face_plan)
    result = canvas_cli(tmp_path, face_plan)
    assert result.returncode == 2 and 'realistic_face_slots' in result.stdout
    face_row['realistic_face_slots'] = [1]
    bind_synthetic_quality(tmp_path, face_plan)
    result = canvas_cli(tmp_path, face_plan)
    assert result.returncode == 2 and '上传前改用认证源卡' in result.stdout
    face_row['reference_checks'][0]['canvas_binding'].update(mode='edge', source_port='image', target_port='in')
    bind_synthetic_quality(tmp_path, face_plan)
    result = canvas_cli(tmp_path, face_plan)
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)['face_source_card_slots'] == {'one': [1]}
    bind_synthetic_quality(tmp_path, frozen)
    result = canvas_cli(tmp_path, first, observed)
    assert result.returncode == 2 and '实际 ordered_media' in result.stdout

    # Simulate the handoff after platform slot collection without making a network request.
    stored = copy.deepcopy(first)
    stored_row = stored['drafts'][0]
    stored_row['ordered_media'] = [dict(node_id='one', media_id='synthetic-upload')]
    manifest = artifacts.load_manifest(tmp_path)
    current_frame = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'FRAME')
    current_frame['upload_bindings'].append(dict(**stored_row['ordered_media'][0],
        target_url=stored['target_url'], revision='R01', sha256=current_frame['checksum']))
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    bind_synthetic_quality(tmp_path, stored)
    reopened = copy.deepcopy(stored)
    reopened.update(phase='after_refresh', frozen_sha256=digest(stored))
    reopened['drafts'][0]['native_mentions'] = [dict(**stored_row['ordered_media'][0],
        entity_type='fixture-chip', native_noneditable_entity=True)]
    result = canvas_cli(tmp_path, stored, reopened)
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)['native_reference_checks']['one'] == 'passed'

    # The combined adoption response must feed the real finalize CLI directly.
    native = reopened['drafts'][0]
    native['node_id'] = 'created-node'
    native['ordered_media'][0].update(node_id='created-node', sha256=stored_row['reference_checks'][0]['sha256'])
    native['native_mentions'][0].update(node_id='created-node')
    write_json(tmp_path / 'plan.json', first)
    write_json(tmp_path / 'combined.json', {'ok': True, 'preflight': report})
    write_json(tmp_path / 'segment-nodes.json', {'one': 'created-node'})
    write_json(tmp_path / 'refresh.json', reopened)
    outcome = run_cli('scripts.redframe_draft', 'finalize-new', '--project', tmp_path,
                      '--plan', tmp_path / 'plan.json', '--preflight', tmp_path / 'combined.json',
                      '--segment-nodes', tmp_path / 'segment-nodes.json', '--readback', tmp_path / 'refresh.json',
                      '--output-dir', tmp_path / 'finalized')
    assert outcome.returncode == 0, outcome.stdout
    assert json.loads((tmp_path / 'finalized/formal_readback.json').read_text())['native_reference_checks'] == {
        'created-node': 'passed'}


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
    bind_redframe_production_sources(tmp_path, frozen, observed)
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


@pytest.mark.parametrize('mutation, changed', [
    ('none', []), ('add', ['new-draft']), ('remove', ['anchor']),
    ('field', ['empty-draft']), ('array-order', ['empty-draft']),
    ('boolean', ['empty-draft']), ('duplicate', None),
])
def test_write_baseline_compares_whole_nodes_with_exact_types(mutation, changed):
    baseline = dict(target_url='https://fixture.invalid/canvas', phase='after_refresh', drafts=[dict(
        node_id='empty-draft', prompt='Synthetic text.', parameters=dict(duration=4, enabled=True),
        ordered_media=[], native_mentions=[], edges=[], source_nodes=[])])
    anchor = copy.deepcopy(baseline['drafts'][0])
    anchor['node_id'] = 'anchor'
    baseline['drafts'].append(anchor)
    if mutation == 'array-order':
        baseline['drafts'][0]['edges'] = [{'slot': 1}, {'slot': 2}]
    current = copy.deepcopy(baseline)
    current['phase'] = 'before_write'
    row = current['drafts'][0]
    if mutation == 'add':
        current['drafts'].append(dict(row, node_id='new-draft'))
    elif mutation == 'remove':
        current['drafts'].pop()
    elif mutation == 'field':
        row['prompt'] += ' Edited.'
    elif mutation == 'array-order':
        row['edges'].reverse()
    elif mutation == 'boolean':
        row['parameters']['enabled'] = 1
    elif mutation == 'duplicate':
        current['drafts'].append(copy.deepcopy(row))
    if changed is None:
        with pytest.raises(ValueError, match='duplicate exact draft node_id'):
            compare_baseline(baseline, current)
    else:
        assert compare_baseline(baseline, current) == {
            'ok': not changed, 'changed_node_ids': changed,
            'baseline_sha256': digest(baseline), 'current_sha256': digest(current),
            'scope': 'collected whole nodes before write',
        }


@pytest.mark.parametrize('mutation, error_field', [
    ('reorder', None), ('duplicate-edge', 'edges'), ('source-port', 'source_nodes'),
    ('extra-field', 'edges'), ('boolean-slot', 'edges'),
])
def test_canvas_input_graph_compares_complete_elements_as_multisets(mutation, error_field):
    row = dict(node_id='draft', ordered_media=[], reference_checks=[], edges=[], source_nodes=[],
               parameters={'duration': 4}, shot_durations=[4])
    for slot in (1, 2):
        node, checksum = f'source-{slot}', f'checksum-{slot}'
        row['ordered_media'].append(dict(node_id=node, reference_port='image'))
        row['reference_checks'].append(dict(sha256=checksum, canvas_binding=dict(
            mode='edge', purpose='reference', prompt_reference=True, source_port='image', target_port='in')))
        row['edges'].append(dict(source_node_id=node, source_port='image', target_node_id='draft',
                                 target_port='in', slot=slot))
        row['source_nodes'].append(dict(node_id=node, port='image', sha256=checksum))
    row['edges'].reverse()
    row['source_nodes'].reverse()
    if mutation == 'duplicate-edge':
        row['edges'].append(copy.deepcopy(row['edges'][0]))
    elif mutation == 'source-port':
        row['source_nodes'][0]['port'] = 'different'
    elif mutation == 'extra-field':
        row['edges'][0]['extra'] = 'different'
    elif mutation == 'boolean-slot':
        row['edges'][-1]['slot'] = True
    if error_field:
        with pytest.raises(ValueError, match=f'canvas {error_field}:'):
            validate_canvas_inputs(row)
    else:
        validate_canvas_inputs(row)


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
@pytest.mark.parametrize('mode', ['direct', 'edge'])
def test_redframe_action_file_native_reference_and_actual_duration(tmp_path, mode):
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
               duration=1, entity_ids=['ACTOR'], canvas_binding=dict(
                   mode=mode, purpose='synthetic action', prompt_reference=True))
    row['reference_checks'].append(ref)
    if mode == 'edge':
        ref['canvas_binding'].update(source_port='main', target_port='input-2')
        row['edges'].append(dict(source_node_id='action-node', source_port='main', target_node_id=row['node_id'],
                                 target_port='input-2', slot=2))
        row['source_nodes'].append(dict(node_id='action-node', port='main', sha256=sha))
    else:
        row['edges'].clear()
        row['source_nodes'].clear()
        row['reference_checks'][0]['canvas_binding'] = dict(
            mode='direct', purpose='synthetic boundary', prompt_reference=True)
    bind_redframe_production_sources(tmp_path, frozen, observed)
    observed['drafts'][0]['native_mentions'] = [dict(**media, entity_type='fixture-chip',
        native_noneditable_entity=True) for media in row['ordered_media']]
    observed['frozen_sha256'] = digest(frozen)
    result = canvas_cli(tmp_path, frozen, observed)
    assert result.returncode == 0, result.stdout
    swapped = copy.deepcopy(frozen)
    swapped['drafts'][0]['reference_checks'].reverse()
    swapped['drafts'][0]['ordered_media'].reverse()
    assert '逐槽匹配采用源段' in canvas_cli(tmp_path, swapped).stdout
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
