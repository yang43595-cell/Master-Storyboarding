import hashlib
import json
import shutil
import subprocess
from urllib.parse import quote

import pytest

from scripts.core.redframe_workspace import REDFRAME_ORIGIN, RedframeWorkspace, RedframeWorkspaceError, _WORKSPACE_REQUESTS


class PageReplies:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.calls = []

    def evaluate(self, _script, params):
        self.calls.append(params)
        return next(self.replies)


def native_node(node_id, filenames):
    return {'id': node_id, 'data': {'localReferences': {'in': [
        {'source': node_id, 'sourceHandle': f'ref-{i}', 'dataType': 'IMAGE',
         'value': {'url': f'assets/{node_id}/{filename}'}}
        for i, filename in enumerate(filenames)
    ]}}}


def test_image_wait_requires_a_saved_body_native_references_and_fresh_result(monkeypatch):
    monkeypatch.setattr('scripts.core.redframe_workspace.time.sleep', lambda _delay: None)
    expected = {'model': 'seedream-fixture', 'size': '1K', 'ratio': '3:4', 'count': 1}
    sha = 'a' * 64
    native = native_node('image-node', ['head.png'])
    handle = quote(json.dumps(['image-node', 'ref-0']))
    chip = {'type': 'Custom', 'html': (
        f'<span class="imageReference" data-reference-id="{handle}" data-value="{{{{ref 1}}}}"></span>')}
    native['type'] = 'remote-imageGenerationNode'
    old = 'assets/image-node/old.png'
    new = 'assets/image-node/new.png'
    native['data'].update(expected, prompt='正面大头照 {{ref 1}}',
                          promptModel=[[{'type': 'Write', 'text': '正面大头照'}, chip]])

    def canvas(**data):
        node = {**native, 'data': {**native['data'], **data}}
        return {'results': [{'status': 200, 'error': None,
                             'graph': {'toonflowCanvas': True, 'nodes': [node], 'edges': []}}]}

    def output(path):
        return {'image': {'value': {'url': path}}}

    with pytest.raises(ValueError, match='尚未保存'):
        workspace = RedframeWorkspace(PageReplies(canvas(promptModel=[[chip]])), 'project')
        workspace.wait_for_image_request('image-node', '正面大头照', [sha], expected, timeout=0)
    with pytest.raises(ValueError, match='原件或顺序不符'):
        workspace = RedframeWorkspace(PageReplies(canvas(),
            {'results': [{'status': 200, 'error': None, 'sha256': 'b' * 64}]}), 'project')
        workspace.wait_for_image_request('image-node', '正面大头照', [sha], expected, timeout=0)

    page = PageReplies(canvas(prompt='{{ref 1}}'), canvas(outputs=output(old)),
                       {'results': [{'status': 200, 'error': None, 'sha256': sha}]},
                       canvas(outputs=output(old)), canvas(outputs=output(new)))
    workspace = RedframeWorkspace(page, 'project')
    previous = workspace.wait_for_image_request('image-node', '正面大头照', [sha], expected)
    assert previous == old
    assert workspace.wait_for_image_result('image-node', previous) == new


def test_attachment_reads_retry_only_transient_slots_and_preserve_order(monkeypatch):
    monkeypatch.setattr('scripts.core.redframe_workspace.time.sleep', lambda _delay: None)
    first_hash, retry_hash = 'a' * 64, 'b' * 64
    page = PageReplies(
        {'results': [{'status': 200, 'error': None, 'bytes': 42, 'sha256': first_hash},
                     {'status': 503, 'error': 'http'},
                     {'status': 200, 'error': 'timeout', 'phase': 'body'},
                     {'status': 403, 'error': 'http'}]},
        {'results': [{'status': 200, 'error': None, 'bytes': 43, 'sha256': retry_hash},
                     {'status': 200, 'error': None, 'bytes': 44, 'sha256': retry_hash}]},
    )
    workspace = RedframeWorkspace(page, 'newProjectExample')
    nodes = [native_node('first', ['start.png', '第二张.png']), native_node('second', ['stall.png', 'end.png'])]
    results = workspace.digest_slots(nodes)

    assert [s['sha256'] for s in results['first']] == [first_hash, retry_hash]
    assert [a['status'] for a in results['first'][1]['attempts']] == [503, 200]
    assert results['second'][0]['sha256'] == retry_hash
    assert results['second'][0]['attempts'][0]['phase'] == 'body'
    assert results['second'][1]['status'] == 403
    assert 'sha256' not in results['second'][1]
    assert [s['media_id'] for s in results['first']] == ['ref-0', 'ref-1']
    assert all(call['origin'] == REDFRAME_ORIGIN for call in page.calls)
    assert all(call['directory'] == '/app/data/workspaces/newProjectExample' for call in page.calls)
    assert page.calls[1]['payload']['slots'] == [
        {'path': 'assets/first/第二张.png', 'family': 'image'},
        {'path': 'assets/second/stall.png', 'family': 'image'},
    ]


def test_streaming_attachments_use_idle_timeout_and_fixed_batch_deadline():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js needed for an isolated JavaScript fetch simulation')
    harness = r"""
const {readFileSync} = require('node:fs');
globalThis.crypto = require('node:crypto').webcrypto;
const {script, origin} = JSON.parse(readFileSync(0, 'utf8'));
globalThis.location = {origin};
const run = eval('(' + script + ')');
const slot = (name) => ({path: `assets/draft/${name}.png`, family: 'image'});
globalThis.fetch = async (url, {signal}) => {
  const stalled = new URL(url, origin).searchParams.get('path').endsWith('/stall.png');
  const stream = new ReadableStream({start(controller) {
    let timer;
    signal.addEventListener('abort', () => {
      clearInterval(timer);
      controller.error(new DOMException('Aborted', 'AbortError'));
    }, {once: true});
    controller.enqueue(new Uint8Array([0]));
    if (stalled) return;
    let chunk = 1;
    timer = setInterval(() => {
      controller.enqueue(new Uint8Array([chunk++]));
      if (chunk === 6) {
        clearInterval(timer);
        controller.close();
      }
    }, 60);
  }});
  return new Response(stream, {status: 200, headers: {'content-type': 'image/png'}});
};
const params = {origin, directory: '/app/data/workspaces/project', operation: 'attachments', timeout: 200};
(async () => {
  const normal = await run({...params, budget: 1600, payload: {slots: [slot('progress'), slot('stall')]}});
  const short = await run({...params, budget: 260, payload: {slots: [slot('progress')]}});
  process.stdout.write(JSON.stringify([normal.results, short.results[0]]));
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run([node, '-e', harness], input=json.dumps({'script': _WORKSPACE_REQUESTS,
        'origin': REDFRAME_ORIGIN}), text=True, capture_output=True, check=True, timeout=8)
    (progress, stalled), deadline = json.loads(result.stdout)
    assert progress['elapsed_ms'] > 200
    assert progress['sha256'] == hashlib.sha256(bytes(range(6))).hexdigest()
    assert progress['bytes'] == 6
    assert (stalled['status'], stalled['error'], stalled['phase']) == (200, 'timeout', 'body')
    assert (deadline['error'], deadline['phase']) == ('budget', 'body')
    assert 'sha256' not in stalled and 'sha256' not in deadline


@pytest.mark.parametrize('path', ('../first.png', 'assets/first/../first.png', 'assets/other/first.png',
                                  'https://other.invalid/first.png', 'assets/first/file.png?source=other'))
def test_attachment_requires_native_slot_path_in_its_own_node(path):
    node = native_node('first', ['head.png'])
    node['data']['localReferences']['in'][0]['value']['url'] = path
    page = PageReplies()
    with pytest.raises(ValueError, match='invalid native attachment identity'):
        RedframeWorkspace(page, 'project').digest_slots([node])
    assert page.calls == []


def test_canvas_write_failure_is_not_replayed():
    canvas = {'toonflowCanvas': True, 'nodes': [], 'edges': []}
    page = PageReplies({'results': [{'status': 503, 'error': 'http', 'phase': 'write',
                                     'before_write': {'status': 200, 'error': None, 'phase': 'before_write'}}]})
    with pytest.raises(RedframeWorkspaceError, match='canvas save failed at write') as error:
        RedframeWorkspace(page, 'project').write_canvas(canvas, canvas)
    assert error.value.status == 503
    assert [call['operation'] for call in page.calls] == ['write']
    assert page.calls[0]['payload'] == {'path': '画布1.json', 'expected': canvas, 'graph': canvas}


def test_canvas_read_requires_persisted_graph_and_exact_origin():
    page = PageReplies({'results': [{'status': 200, 'error': None, 'graph': {'message': 'login'}}]},
                       {'error': 'wrong_origin'})
    workspace = RedframeWorkspace(page, 'project')
    with pytest.raises(RedframeWorkspaceError, match='invalid_canvas'):
        workspace.read_canvas()
    with pytest.raises(RedframeWorkspaceError, match='wrong_origin'):
        workspace.read_canvas()


def test_image_source_card_download_uses_native_output_path_and_bounded_transport():
    page = PageReplies({'results': [{'status': 200, 'error': None, 'bytes': 42, 'sha256': 'a' * 64}]})
    source = {'id': 'source', 'type': 'remote-imageNode',
              'data': {'outputs': {'image': {'value': {'url': 'assets/source/original.png'}}}}}
    result, = RedframeWorkspace(page, 'project').digest_image_outputs([source])
    assert (result['node_id'], result['path'], result['sha256']) == ('source', 'assets/source/original.png', 'a' * 64)
    assert page.calls[0]['payload'] == {'slots': [{'path': 'assets/source/original.png', 'family': 'image'}]}


def test_image_source_card_cannot_download_another_nodes_file():
    page = PageReplies()
    source = {'id': 'source', 'type': 'remote-imageNode',
              'data': {'outputs': {'image': {'value': {'url': 'assets/other/original.png'}}}}}
    with pytest.raises(ValueError, match='no native output'):
        RedframeWorkspace(page, 'project').digest_image_outputs([source])
    assert page.calls == []


def test_canvas_read_can_use_remaining_outer_budget():
    graph = {'toonflowCanvas': True, 'nodes': [], 'edges': []}
    page = PageReplies({'results': [{'status': 200, 'error': None, 'graph': graph}]})
    assert RedframeWorkspace(page, 'project').read_canvas(budget_ms=1500) == graph
    assert page.calls[0]['budget'] == 1500
