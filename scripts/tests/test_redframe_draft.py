from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import quote
import json
import sys

import pytest

from scripts import redframe_draft
from scripts.core.draft_delivery import plan_bindings, read_draft_delivery
from scripts.core.io import read_json, write_json
from scripts.tests.test_workflow_repair_cli import bind_redframe_production_sources, redframe_fixture
from scripts.tests.test_delivery_preflight import bind_synthetic_quality


@pytest.mark.parametrize(
    'label,damage,error',
    [
        ('@图片1', None, None),
        ('@图片1', 'missing', '必需文件缺失'),
        ('@图片1', 'changed', 'reference file/hash'),
        ('@图片1', 'stale', 'reference artifact is unconfirmed, stale'),
        ('@图片1', 'missing-entity-type', 'native entity readback'),
        ('@图片2', None, '正文引用须按有序素材槽逐项对应'),
        ('@视频1', None, '正文引用须按有序素材槽逐项对应'),
        ('@图片 1', None, '正文引用须按有序素材槽逐项对应'),
        ('@图片1 @图片1', None, '正文引用须按有序素材槽逐项对应'),
        ('', None, '正文引用须按有序素材槽逐项对应'),
        ('{{ref 1}}', None, '不能使用平台原生'),
    ],
)
def test_upload_preflight_checks_the_entire_batch_before_opening_browser(tmp_path, monkeypatch, label, damage, error):
    plan, observed = redframe_fixture(tmp_path)
    for draft, marker in zip(plan['drafts'], ('@图片1', label)):
        draft['prompt'] += f' {marker}' if marker else ''
        draft.update(edges=[], source_nodes=[], realistic_face_slots=[])
        draft['reference_checks'][0]['canvas_binding'] = {
            'mode': 'direct', 'purpose': 'synthetic boundary', 'prompt_reference': True}
    bind_redframe_production_sources(tmp_path, plan, observed)
    for draft in plan['drafts']:
        del draft['ordered_media']
    if damage == 'missing-entity-type':
        del plan['drafts'][-1]['native_entity_type']
    bind_synthetic_quality(tmp_path, plan)
    if damage == 'missing':
        (tmp_path / 'frame.png').unlink()
    elif damage == 'changed':
        (tmp_path / 'frame.png').write_bytes(b'changed test bytes')
    elif damage == 'stale':
        from scripts.core.artifacts import save_manifest

        manifest = redframe_draft.load_manifest(tmp_path)
        next(item for item in manifest['artifacts'] if item['artifact_id'] == 'FRAME')['status'] = 'stale'
        save_manifest(tmp_path, manifest)

    class AtUploadBoundary(Exception):
        pass

    calls = []

    def browser_boundary(*args, **kwargs):
        calls.append('open_workspace')
        raise AtUploadBoundary

    def unexpected(*args, **kwargs):
        pytest.fail('offline test reached a real browser, write or layout operation')

    monkeypatch.setattr(redframe_draft, 'open_workspace', browser_boundary)
    with pytest.raises(AtUploadBoundary if error is None else ValueError, match=error):
        redframe_draft.run_new_draft_batch(
            tmp_path, plan, unexpected, host_browser=unexpected, check_layout=unexpected)
    assert calls == (['open_workspace'] if error is None else [])


@pytest.mark.parametrize('marker,error', [
    ('', None),
    ('@图片1', '正文引用须按有序素材槽逐项对应'),
    ('{{ref 1}}', '不能使用平台原生'),
])
def test_text_only_upload_plan_checks_explicit_empty_media(tmp_path, monkeypatch, marker, error):
    plan, observed = redframe_fixture(tmp_path)
    plan['drafts'] = plan['drafts'][:1]
    observed['drafts'] = observed['drafts'][:1]
    draft = plan['drafts'][0]
    draft.pop('native_entity_type', None)
    draft['prompt'] += f' {marker}' if marker else ''
    draft.update(ordered_media=[], reference_checks=[], required_entities=[], required_reference_roles={},
                 edges=[], source_nodes=[], realistic_face_slots=[])
    bind_redframe_production_sources(tmp_path, plan, observed)
    bind_synthetic_quality(tmp_path, plan)
    assert draft['ordered_media'] == []

    class AtUploadBoundary(Exception):
        pass

    def browser_boundary(*args, **kwargs):
        raise AtUploadBoundary

    def unexpected(*args, **kwargs):
        pytest.fail('offline test reached a real browser, write or layout operation')

    monkeypatch.setattr(redframe_draft, 'open_workspace', browser_boundary)
    with pytest.raises(AtUploadBoundary if error is None else ValueError, match=error):
        redframe_draft.run_new_draft_batch(
            tmp_path, plan, unexpected, host_browser=unexpected, check_layout=unexpected)


@pytest.mark.parametrize('case', [
    'direct', 'wrong-bytes', 'linked', 'stale-face', 'stale-authority',
    'three-drafts', 'late-wrong-bytes', 'interrupted-write', 'interrupted-after-click',
    'interrupted-after-id', 'independent-batches',
])
def test_new_draft_batch_uses_one_refresh_and_one_ordered_download(tmp_path, monkeypatch, case):
    plan, observed = redframe_fixture(tmp_path)
    if case in {'three-drafts', 'late-wrong-bytes', 'interrupted-write', 'independent-batches'}:
        third = deepcopy(plan['drafts'][1])
        third.update(node_id='three', continuity={'event_id': 'separate-event'})
        plan['drafts'].append(third)
    linked = case in {'linked', 'stale-face'}
    for draft in plan['drafts']:
        draft.update(edges=[], source_nodes=[], realistic_face_slots=[], native_entity_type='reference')
        draft['prompt'] += ' @图片1'
        draft['reference_checks'][0]['canvas_binding'] = {
            'mode': 'direct', 'purpose': 'synthetic boundary', 'prompt_reference': True}
    first = plan['drafts'][0]
    if linked:
        first['realistic_face_slots'] = [1]
        first['reference_checks'][0]['canvas_binding'].update(mode='edge', source_port='image', target_port='in')
    bind_redframe_production_sources(tmp_path, plan, observed)
    for draft in plan['drafts']:
        del draft['ordered_media']
    bind_synthetic_quality(tmp_path, plan)
    if case == 'stale-authority':
        assert redframe_draft.validate_submission(plan, tmp_path, formal_delivery=True, preflight=True)['ok']
        (tmp_path / 'director.json').write_text('{"fixture": "changed after preflight"}', encoding='utf-8')

        def unexpected(*args, **kwargs):
            pytest.fail('stale project authority reached the browser')

        monkeypatch.setattr(redframe_draft, 'open_workspace', unexpected)
        with pytest.raises(ValueError, match='file/hash|登记字节'):
            redframe_draft.run_new_draft_batch(
                tmp_path, plan, unexpected, host_browser=unexpected, check_layout=unexpected)
        return

    nodes, edges, slots, calls = [], [], {}, []
    source_url = 'assets/frame-node/frame.png'
    checksum = first['reference_checks'][0]['sha256']
    certificate = {'sourceUrl': source_url, 'assetId': 'synthetic', 'imageDigest': checksum}
    source_node = {'id': 'frame-node', 'type': 'remote-imageNode', 'data': {
        'outputs': {'image': {'value': {'url': source_url}}}, 'faceCertification': certificate}}
    earlier_nodes = [source_node] if linked else []

    class Workspace:
        read_budget = 30_000
        page = SimpleNamespace(reload=lambda **_: calls.append('refresh'))

        def read_canvas(self, **kwargs):
            calls.append('read')
            return {'nodes': earlier_nodes + nodes, 'edges': edges.copy()}

        def digest_slots(self, actual):
            assert actual == [node for node in nodes if node in actual]
            calls.append('download')
            return {node['id']: slots[node['id']] for node in actual}

    @contextmanager
    def open_once(factory, target, project_id):
        calls.append('open')
        try:
            yield Workspace()
        finally:
            calls.append('close')

    interrupt = [case] if case.startswith('interrupted-') else []
    if case == 'independent-batches':
        alternate = {**plan, 'drafts': plan['drafts'][2:]}
        plan['drafts'] = plan['drafts'][:2]

    def write(ui, draft):
        ident = 'created-' + draft['source_segment_id']
        calls.append(ident)
        halt = interrupt.pop() if interrupt and ident == 'created-two' else None
        if halt == 'interrupted-write':
            raise RuntimeError('synthetic write interruption')
        path = f'assets/{ident}/frame.png'
        face_slot = bool(draft['realistic_face_slots'])
        handle = quote(json.dumps(['frame-node', 'frame-media'] if face_slot else [ident, 'slot']))
        chip = f'<span class="imageReference" data-reference-id="{handle}" data-value="{{{{ref 1}}}}"></span>'
        ref = {'source': ident, 'sourceHandle': 'slot', 'dataType': 'IMAGE', 'value': {'url': path}}
        node = {'id': ident, 'type': 'remote-videoGenerationNode', 'data': {
            'outputs': {}, 'videoMode': 'fixture-reference', 'model': '["fixture","fixture"]',
            'mode': '"fixture-reference"', 'localReferences': {'in': [] if face_slot else [ref]},
            'promptModel': [[{'type': 'Custom', 'html': chip}]],
            'prompt': draft['prompt'].replace('@图片1', '{{ref 1}}'),
            'ratio': '9:16', 'resolution': '480P', 'duration': draft['parameters']['duration'],
        }}
        nodes.append(node)
        checksum = draft['reference_checks'][0]['sha256']
        if ((case == 'wrong-bytes' and ident == 'created-two')
                or (case == 'late-wrong-bytes' and ident == 'created-three')):
            checksum = '0' * 64
        if face_slot:
            edges.append(dict(source='frame-node', sourceHandle='image', target=ident, targetHandle='in'))
            slots[ident] = []
        else:
            slots[ident] = [dict(source=ident, media_id='slot', path=path, index=1, sha256=checksum, status=200)]
        if halt == 'interrupted-after-id':
            ui._draft_created(ident)
        if halt:
            raise RuntimeError('synthetic write interruption')
        return node

    def layout(ui, graph, segment_nodes):
        assert graph['nodes'] == earlier_nodes + nodes
        calls.append('layout')
        return {'checked': list(segment_nodes.values())}

    def read_linked(ui, graph, segment_nodes, linked_drafts, direct_receipts):
        calls.append('read-linked')
        assert linked_drafts == {'one': first} and direct_receipts == slots
        node_id = segment_nodes['one']
        media = {'node_id': 'frame-node', 'media_id': 'frame-media', 'sha256': checksum}
        return [{
            'node_id': node_id, 'prompt': first['prompt'], 'parameters': first['parameters'],
            'ordered_media': [media],
            'native_mentions': [{**media, 'entity_type': 'reference', 'native_noneditable_entity': True}],
            'edges': [dict(source_node_id='frame-node', source_port='image', target_node_id=node_id,
                           target_port='in', slot=1)],
            'source_nodes': [dict(node_id='frame-node', port='image', sha256=checksum)],
            'face_certifications': {'1': {
                'node_id': 'frame-node', 'source_url': source_url,
                'faceCertification': {**certificate, 'assetId': 'old'} if case == 'stale-face' else certificate}},
        }]

    monkeypatch.setattr(redframe_draft, 'open_workspace', open_once)
    monkeypatch.setattr(redframe_draft.RedframeVideoDraftUI, '__init__', lambda *args, **kwargs: None)
    monkeypatch.setattr(redframe_draft.RedframeVideoDraftUI, 'assert_target', lambda ui: calls.append('target'))
    kwargs = {'host_browser': object(), 'check_layout': layout, 'collect_linked': read_linked if linked else None}
    def delivery_status():
        bindings = plan_bindings(tmp_path, plan)
        source = bindings['one']['source']
        return read_draft_delivery(tmp_path, plan, source, bindings)

    if case.startswith('interrupted-'):
        with pytest.raises(RuntimeError, match='synthetic write interruption'):
            redframe_draft.run_new_draft_batch(tmp_path, plan, write, **kwargs)
        assert calls == ['open', 'target', 'read', 'created-one', 'created-two', 'close']
        status = delivery_status()
        assert status['one']['node_id'] == 'created-one'
        assert status['one']['status'] == 'resume'
        if case == 'interrupted-after-id':
            assert status['two']['node_id'] == 'created-two'
            assert status['two']['status'] == 'resume'
        else:
            assert status['two']['status'] == 'unknown'
            calls.clear()
            with pytest.raises(ValueError, match='创建结果未知'):
                redframe_draft.run_new_draft_batch(tmp_path, plan, write, **kwargs)
            assert not calls
            resolve = redframe_draft.resolve_draft_creation
            if case == 'interrupted-after-click':
                with pytest.raises(ValueError, match='仍有新增节点'):
                    resolve(tmp_path, plan, 'two', host_browser=object(), confirm_absent=True)
                with pytest.raises(ValueError, match='不对应指定 ID'):
                    resolve(tmp_path, plan, 'two', host_browser=object(), node_id='created-one')
                resolution = resolve(tmp_path, plan, 'two', host_browser=object(), node_id='created-two')
                assert resolution['node_id'] == 'created-two'
            else:
                resolution = resolve(tmp_path, plan, 'two', host_browser=object(), confirm_absent=True)
                assert resolution['status'] == 'confirmed_absent'
                assert 'two' not in delivery_status()
        calls.clear()
        resumed = []
        result = redframe_draft.run_new_draft_batch(
            tmp_path, plan, write, resume=lambda _, draft, node_id: resumed.append((draft['source_segment_id'], node_id)), **kwargs)
        assert result['formal_readback']['ok']
        assert 'created-one' not in calls
        assert ('created-two' in calls) == (case == 'interrupted-write')
        assert resumed[0] == ('one', 'created-one')
        assert set(result['segment_nodes'].values()) == {node['id'] for node in nodes}
        calls.clear()
        replay = redframe_draft.run_new_draft_batch(tmp_path, plan, write, **kwargs)
        assert replay['reused_readback'] and replay['segment_nodes'] == result['segment_nodes']
        assert not calls
        return

    node_ids = ['created-' + draft['source_segment_id'] for draft in plan['drafts']]
    errors = {'wrong-bytes': 'two 实际输入槽身份或文件顺序',
              'late-wrong-bytes': 'three 实际输入槽身份或文件顺序',
              'stale-face': '源卡认证与刷新的持久画布不符'}
    if case in errors:
        with pytest.raises(ValueError, match=errors[case]):
            redframe_draft.run_new_draft_batch(tmp_path, plan, write, **kwargs)
    else:
        result = redframe_draft.run_new_draft_batch(tmp_path, plan, write, **kwargs)
        assert result['preflight']['ok']
        assert result['formal_readback']['ok']
        if linked:
            assert result['formal_readback']['face_certification_checks'] == {'created-one': {'1': 'passed'}}
        assert result['frozen']['drafts'][1]['continuity']['previous_node_id'] == 'created-one'
        if len(node_ids) == 3:
            assert result['frozen']['drafts'][2]['continuity'] == {'event_id': 'separate-event'}
        assert result['layout']['checked'] == node_ids
    assert calls == ['open', 'target', 'read', *node_ids,
                     'refresh', 'target', 'read', 'layout', 'download'] + (
                         ['read-linked'] if linked else []) + ['close']
    assert 'ordered_media' not in plan['drafts'][0]
    if case == 'independent-batches':
        calls.clear()
        second = redframe_draft.run_new_draft_batch(tmp_path, alternate, write, **kwargs)
        assert second['formal_readback']['ok']
        assert second['readback_path'] != result['readback_path']
        assert calls == ['open', 'target', 'read', 'created-three', 'refresh', 'target', 'read', 'layout', 'download', 'close']
        calls.clear()
        assert redframe_draft.run_new_draft_batch(tmp_path, plan, write, **kwargs)['reused_readback']
        assert redframe_draft.run_new_draft_batch(tmp_path, alternate, write, **kwargs)['reused_readback']
        assert not calls

        path = tmp_path / result['readback_path'] / 'observed.json'
        observed = read_json(path)
        observed['drafts'][0]['prompt'] += ' drift'
        write_json(path, observed)
        assert {k: v['status'] for k, v in delivery_status().items()} == {'one': 'resume', 'two': 'resume'}
        assert all(draft['node_id'] == f'created-{segment}' for segment, draft in delivery_status().items())



def test_native_creation_records_id_before_first_draft_edit():
    ui = object.__new__(redframe_draft.RedframeVideoDraftUI)
    ids = []
    ui._new_node = lambda *args, **kwargs: 'actual-node'
    ui._draft_created = ids.append

    def interrupted(node_id):
        assert ids == [node_id]
        raise RuntimeError('operation stopped before naming')

    ui._draft_locator = interrupted
    with pytest.raises(RuntimeError, match='operation stopped before naming'):
        ui.create_video_draft(10, 20, 'Draft title')
    assert ids == ['actual-node']


def test_retest_refreshes_in_the_same_browser_after_resave(monkeypatch):
    calls = []
    node = {'id': 'sample', 'type': 'remote-videoGenerationNode', 'position': {'x': 0, 'y': 0}}
    graph = {'nodes': [node], 'edges': []}
    frozen = {'target_url': 'canvas', 'target_project_id': 'project', 'revision': 'R01',
              'drafts': [{'node_id': 'sample', 'edges': [], 'source_nodes': []}]}
    browser = object()

    class Workspace:
        read_budget = 30_000
        page = SimpleNamespace(reload=lambda **_: calls.append('refresh'))

        def read_canvas(self, **kwargs):
            calls.append('read')
            return graph

        def write_canvas(self, expected, document):
            assert expected == document == graph
            calls.append('save')

        def digest_slots(self, nodes):
            assert nodes == [node]
            return {'sample': []}

    @contextmanager
    def open_once(factory, target, project):
        assert (factory, target, project) == (browser, 'canvas', 'project')
        calls.append('open')
        yield Workspace()
        calls.append('close')

    monkeypatch.setattr(sys, 'path', sys.path.copy())
    monkeypatch.setitem(sys.modules, 'headless_edge', SimpleNamespace(task_browser=browser))
    monkeypatch.setattr(redframe_draft, 'open_workspace', open_once)
    monkeypatch.setattr(redframe_draft, 'validate_submission', lambda *args, **kwargs: None)
    monkeypatch.setattr(redframe_draft, 'source_capabilities', lambda *args: {})
    monkeypatch.setattr(redframe_draft, 'before_write_readback', lambda *args: {})
    monkeypatch.setattr(redframe_draft, 'compare_baseline', lambda *args: {'ok': True})
    monkeypatch.setattr(redframe_draft, 'native_draft', lambda *args: {'node_id': 'sample'})
    monkeypatch.setattr(redframe_draft, 'compare', lambda *args, **kwargs: {'ok': True})

    result = redframe_draft.retest('project', frozen, {}, graph, [{'id': 'sample', 'w': 1, 'h': 1}], resave=True)

    assert result['ok']
    assert calls == ['open', 'read', 'save', 'refresh', 'read', 'close']


def test_first_saved_node_waits_for_the_original_id(monkeypatch):
    monkeypatch.setattr(redframe_draft.time, 'sleep', lambda _: None)
    node = {'id': 'original', 'type': 'remote-videoGenerationNode', 'data': {'prompt': 'new'}}
    reads = iter([{'nodes': [dict(node, id='some-other-node')]}, {'nodes': [node]}])

    class Workspace:
        read_budget = 30_000

        def read_canvas(self, *, budget_ms):
            return next(reads)

    assert redframe_draft.verify_saved(Workspace(), {'original': None}) == {'nodes': [node]}


def test_persistence_rejects_a_stale_existing_node_on_a_single_read():
    desired = {'id': 'original', 'type': 'remote-videoGenerationNode', 'data': {'prompt': 'new'}}
    calls = []

    class Workspace:
        def read_canvas(self):
            calls.append('read')
            return {'nodes': [dict(desired, data={'prompt': 'old'})]}

    with pytest.raises(ValueError, match='原节点 ID.*original'):
        redframe_draft.verify_saved(Workspace(), {'original': desired}, timeout=0)
    assert calls == ['read']


@pytest.mark.parametrize(('timeout', 'read_seconds', 'expected_budgets', 'expected_pauses'), [
    (15, 10, [15_000, 4_000], [1]),
    (0.5, 0, [500], [0.5]),
])
def test_persistence_reads_only_within_the_remaining_poll_budget(
        monkeypatch, timeout, read_seconds, expected_budgets, expected_pauses):
    now, budgets, pauses = 0, [], []

    def sleep(seconds):
        nonlocal now
        pauses.append(seconds)
        now += seconds

    class Workspace:
        read_budget = 30_000

        def read_canvas(self, *, budget_ms):
            nonlocal now
            budgets.append(budget_ms)
            now += min(read_seconds, budget_ms / 1000)
            return {'nodes': []}

    monkeypatch.setattr(redframe_draft.time, 'monotonic', lambda: now)
    monkeypatch.setattr(redframe_draft.time, 'sleep', sleep)
    with pytest.raises(ValueError, match='原节点 ID.*original'):
        redframe_draft.verify_saved(Workspace(), {'original': None}, timeout=timeout)
    assert budgets == expected_budgets
    assert pauses == expected_pauses
    assert now == timeout


def test_two_drafts_cannot_reserve_the_same_default_position():
    existing = {'earlier': (0, 0, 220, 262)}
    planned = {'first': (6656, 2800, 220, 262), 'second': (6656, 2800, 220, 262)}
    with pytest.raises(ValueError, match='second 与 first'):
        redframe_draft.verify_placements(existing, planned)
    planned['second'] = (6944, 2800, 220, 262)
    redframe_draft.verify_placements(existing, planned)


def test_reusing_measured_geometry_requires_current_node_ids():
    graph = {'nodes': [{'id': 'kept', 'position': {'x': 0, 'y': 0}},
                       {'id': 'new', 'position': {'x': 250, 'y': 0}}]}
    with pytest.raises(ValueError, match='节点集合'):
        redframe_draft.measured_placements(graph, {'kept': (220, 262)}, ['kept'])


@pytest.mark.parametrize(('field', 'value'), [
    ('draft', {'sourceTaskId': 'old-job', 'taskId': 'old-provider-job'}),
    ('draftHistory', {'assets/old-node/output.mp4': {'taskId': 'old-provider-job'}}),
    ('videoTask', {'id': 'old-job'}),
])
def test_copied_generation_history_cannot_be_used_as_a_fresh_draft(field, value):
    node = {'id': 'new-node', 'type': 'remote-videoGenerationNode', 'data': {
        'outputs': {}, 'videoMode': 'reference', 'model': '["provider","model"]', 'mode': '"normal"',
        'localReferences': {'in': []}, 'promptModel': [], 'prompt': '',
        'ratio': '9:16', 'resolution': '1080P', 'duration': 9,
        field: value,
    }}
    graph = {'nodes': [node], 'edges': []}
    ui = SimpleNamespace(assert_target=lambda: None,
                         workspace=SimpleNamespace(read_canvas=lambda: graph),
                         page=SimpleNamespace(locator=lambda _: pytest.fail('dirty node reached UI')))
    models = {'reference': ('model', {'providerId': 'provider', 'modelId': 'model', 'mode': ['normal']})}
    with pytest.raises(ValueError, match='生成记录.*new-node'):
        redframe_draft.native_draft(graph, node, [], models)
    with pytest.raises(ValueError, match='生成记录.*new-node'):
        redframe_draft.RedframeVideoDraftUI._draft_locator(ui, 'new-node')
