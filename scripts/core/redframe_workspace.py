"""Read Redframe's workspace files through the authenticated canvas page."""

from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
import json
import re
import time
from urllib.parse import unquote


REDFRAME_ORIGIN = "https://redframe-ai.xmpaohong.com:28888"
_CANVAS_FILE = "画布1.json"
_RETRY_STATUSES = {408, 502, 503, 504}
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")

_WORKSPACE_REQUESTS = """async ({origin, directory, operation, payload, timeout, budget}) => {
  if (location.origin !== origin) return {error: 'wrong_origin'};
  const deadline = performance.now() + budget;
  async function request(path, mode, family = '') {
    if (performance.now() >= deadline) return {status: null, error: 'budget'};
    const abort = new AbortController();
    let timer, expired, phase;
    function armTimer(idleLimit = timeout) {
      clearTimeout(timer);
      const remaining = deadline - performance.now();
      const expire = () => {
        expired = remaining <= idleLimit ? 'budget' : 'timeout';
        abort.abort();
      };
      if (remaining <= 0) expire();
      else timer = setTimeout(expire, Math.min(idleLimit, remaining));
    }
    if (mode === 'attachment') phase = 'headers';
    armTimer();
    const query = new URLSearchParams({directory, path});
    const write = mode === 'write';
    if (write) query.set('exclusive', 'false');
    let status = null;
    try {
      const response = await fetch(`/api/workspaces/files/${write ? 'write' : 'read'}?${query}`, {
        credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: abort.signal,
        ...(write ? {method: 'PUT', body: JSON.stringify(payload.graph),
          headers: {'x-toonflow-workspace': '1', 'Content-Type': 'application/octet-stream'}} : {}),
      });
      status = response.status;
      const result = {status, error: null};
      if (write) return {...result, error: response.ok ? null : 'http'};
      if (status !== 200) return {...result, error: 'http'};
      if (mode === 'canvas') {
        try { return {...result, graph: await response.json()}; }
        catch (error) {
          if (abort.signal.aborted) throw error;
          return {...result, error: 'invalid_json'};
        }
      }
      const type = (response.headers.get('content-type') || '').split(';')[0].toLowerCase();
      if (type && type !== 'application/octet-stream' && !type.startsWith(family + '/')) {
        return {...result, error: 'not_media', content_type: type};
      }
      if (!globalThis.crypto?.subtle) return {...result, error: 'digest_unavailable', content_type: type};
      if (!response.body) return {...result, error: 'empty_media', content_type: type};
      const reader = response.body.getReader();
      const chunks = [];
      let bytes = 0;
      phase = 'body';
      armTimer();
      for (;;) {
        const {done, value} = await reader.read();
        if (done) break;
        if (!value.byteLength) continue;
        chunks.push(value);
        bytes += value.byteLength;
        // Receiving bytes proves progress. The batch deadline never moves.
        armTimer();
      }
      if (!bytes) return {...result, error: 'empty_media', content_type: type};
      phase = 'digest';
      armTimer(Infinity);
      const buffer = new Uint8Array(bytes);
      let offset = 0;
      for (const chunk of chunks) {
        buffer.set(chunk, offset);
        offset += chunk.byteLength;
      }
      const hash = await crypto.subtle.digest('SHA-256', buffer);
      if (performance.now() >= deadline) return {status, error: 'budget', phase};
      const sha256 = Array.from(new Uint8Array(hash), value => value.toString(16).padStart(2, '0')).join('');
      return {...result, sha256, bytes, content_type: type};
    } catch (error) {
      return {status, error: expired || 'network', ...(phase ? {phase} : {})};
    } finally {
      clearTimeout(timer);
    }
  }
  async function measured(path, mode, family) {
    const start = performance.now();
    const result = await request(path, mode, family);
    return {...result, elapsed_ms: Math.round(performance.now() - start)};
  }
  function equal(left, right) {
    if (left === right) return true;
    if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
    if (Array.isArray(left) !== Array.isArray(right)) return false;
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length
      && keys.every(key => Object.hasOwn(right, key) && equal(left[key], right[key]));
  }
  if (operation === 'write') {
    const {graph, ...previous} = await measured(payload.path, 'canvas');
    previous.phase = 'before_write';
    if (previous.error) return {results: [previous]};
    if (!equal(graph, payload.expected)) {
      return {results: [{...previous, error: 'canvas_changed'}]};
    }
    const result = await measured(payload.path, 'write');
    return {results: [{...result, phase: 'write', before_write: previous}]};
  }
  if (operation !== 'attachments') return {results: [await measured(payload.path, operation)]};
  const results = Array(payload.slots.length);
  let next = 0;
  async function worker() {
    while (next < payload.slots.length) {
      const index = next++;
      const slot = payload.slots[index];
      results[index] = await measured(slot.path, 'attachment', slot.family);
    }
  }
  await Promise.all(Array.from({length: Math.min(4, results.length)}, worker));
  return {results};
}"""


class RedframeWorkspaceError(RuntimeError):
    def __init__(self, operation, attempts):
        self.attempts = attempts
        last = attempts[-1]
        self.status = last['status']
        self.phase = last.get('phase')
        stage = f' at {self.phase}' if self.phase else ''
        super().__init__(f"{operation} failed{stage} ({last['error']}, status={self.status})")


class NativeTag(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.refs = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'data-reference-id' in values:
            if tag != 'span' or 'imageReference' not in values.get('class', '').split():
                raise ValueError('原生提示词引用格式已改变')
            self.refs.append((json.loads(unquote(values['data-reference-id'])), values['data-value']))


def _attempt(result):
    history = {key: result.get(key) for key in ('status', 'error', 'elapsed_ms')}
    if 'phase' in result:
        history['phase'] = result['phase']
    return history


def _retryable(result):
    return result['status'] in _RETRY_STATUSES or result['error'] in {'network', 'timeout'}


def _is_canvas(graph):
    return (isinstance(graph, dict) and graph.get('toonflowCanvas') is True
            and isinstance(graph.get('nodes'), list) and isinstance(graph.get('edges'), list))


class RedframeWorkspace:
    """Use the existing page session, with bounded reads and single-attempt saves."""

    def __init__(self, page, project_id, *, request_timeout_ms=8_000, read_budget_ms=30_000):
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
            raise ValueError('expected an exact Redframe project ID')
        if not 0 < request_timeout_ms <= read_budget_ms <= 60_000:
            raise ValueError('invalid Redframe request or read budget')
        self.page = page
        self.directory = f'/app/data/workspaces/{project_id}'
        self.timeout = request_timeout_ms
        self.read_budget = read_budget_ms

    def _request(self, operation, payload, count, budget):
        try:
            value = self.page.evaluate(_WORKSPACE_REQUESTS, {
                'origin': REDFRAME_ORIGIN, 'directory': self.directory, 'operation': operation,
                'payload': payload, 'timeout': self.timeout, 'budget': budget,
            })
        except Exception:
            raise RedframeWorkspaceError(operation, [_attempt({'error': 'page_evaluate'})]) from None
        if isinstance(value, dict) and value.get('error') == 'wrong_origin':
            raise RedframeWorkspaceError(operation, [_attempt({'error': 'wrong_origin'})])
        results = value.get('results') if isinstance(value, dict) else None
        if not isinstance(results, list) or len(results) != count or not all(isinstance(item, dict) for item in results):
            raise RedframeWorkspaceError(operation, [_attempt({'error': 'invalid_page_result'})])
        return results

    def _read(self, operation, payload, slots=None, *, budget_ms=None):
        budget = self.read_budget if budget_ms is None else budget_ms
        if not isinstance(budget, int) or not 0 < budget <= self.read_budget:
            raise ValueError("invalid Redframe read budget")
        deadline = time.monotonic() + budget / 1000
        count = len(slots) if slots is not None else 1
        if not count:
            return []
        first = self._request(operation, payload, count, budget)
        attempts = [[_attempt(item)] for item in first]
        retries = [i for i, attempt in enumerate(attempts) if _retryable(attempt[-1])]
        if retries and deadline - time.monotonic() > 0.25:
            time.sleep(0.25)
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining > 0:
                retry_payload = payload if slots is None else {'slots': [slots[i] for i in retries]}
                for i, value in zip(retries, self._request(operation, retry_payload, len(retries), remaining)):
                    first[i] = value
                    attempts[i].append(_attempt(value))
        return [{**item, 'attempts': history} for item, history in zip(first, attempts)]

    def read_canvas(self, *, budget_ms=None):
        result, = self._read('canvas', {'path': _CANVAS_FILE}, budget_ms=budget_ms)
        graph = result.get('graph')
        if result.get('error') is not None or result.get('status') != 200 or not _is_canvas(graph):
            attempts = result['attempts']
            if attempts[-1]['error'] is None:
                attempts[-1]['error'] = 'invalid_canvas'
            raise RedframeWorkspaceError('canvas read', attempts)
        return graph

    def read_video_submission(self, submission, *, budget_ms=None):
        """Find the task on the original video node in the existing page session."""
        if not isinstance(submission, dict):
            raise ValueError('须提供原提交记录')
        node_id = submission.get('node_id')
        task_id = submission.get('task_id')
        previous_task_id = submission.get('previous_task_id')
        if (not isinstance(node_id, str) or not _ID.fullmatch(node_id)
                or any(value is not None and (not isinstance(value, str) or not _ID.fullmatch(value))
                       for value in (task_id, previous_task_id))):
            raise ValueError('须提供准确的红帧视频节点和已知任务 ID')
        if task_id is not None and task_id == previous_task_id:
            raise ValueError('本次任务 ID 不能与提交前任务相同')
        unknown = {'node_id': node_id, 'phase': 'submission_unknown'}
        click_ms = None
        if task_id is None:
            if 'previous_task_id' not in submission or not isinstance(submission.get('click_intent_at'), str):
                return {**unknown, 'reason': 'missing_submission_baseline'}
            clicked_at = datetime.fromisoformat(submission['click_intent_at'])
            if clicked_at.utcoffset() is None or clicked_at.timestamp() <= 0:
                raise ValueError('提交前时间须包含时区')
            click_ms = clicked_at.timestamp() * 1000

        graph = self.read_canvas(budget_ms=budget_ms)
        nodes = [node for node in graph['nodes'] if node.get('id') == node_id]
        if not nodes:
            return {**unknown, 'reason': 'node_missing'}
        if (len(nodes) != 1 or nodes[0].get('type') != 'remote-videoGenerationNode'
                or not isinstance(nodes[0].get('data'), dict)):
            raise ValueError(f'视频节点身份不符：{node_id}')
        task = nodes[0]['data'].get('videoTask')
        observed = task.get('id') if isinstance(task, dict) else None
        if not isinstance(observed, str) or not _ID.fullmatch(observed):
            return {**unknown, 'reason': 'task_missing'}
        if task_id is not None:
            if task_id != observed:
                return {**unknown, 'reason': 'task_id_mismatch'}
        else:
            started_at = task.get('startedAt')
            if observed == previous_task_id or type(started_at) is not int or started_at < click_ms:
                return {**unknown, 'reason': 'task_not_new'}
        return {'node_id': node_id, 'phase': 'task_observed', 'task_id': observed}

    def _wait_for_image_node(self, node_id, ready, timeout, message):
        if not isinstance(node_id, str) or not _ID.fullmatch(node_id) or not 0 <= timeout <= 300:
            raise ValueError('须指定准确图片节点 ID 和有界等待时间')
        deadline = time.monotonic() + timeout
        while True:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            nodes = [node for node in self.read_canvas(budget_ms=min(self.read_budget, remaining_ms))['nodes']
                     if node.get('id') == node_id]
            if len(nodes) > 1 or (nodes and nodes[0].get('type') != 'remote-imageGenerationNode'):
                raise ValueError(f'图片节点身份不符：{node_id}')
            if nodes and ready(nodes[0]):
                return nodes[0]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError(f'{message}：{node_id}')
            time.sleep(min(1, remaining))

    def wait_for_image_request(self, node_id, prompt, reference_sha256, parameters, *, timeout=15):
        """Check the saved prompt, ordered original references and controls before submission."""
        if not isinstance(prompt, str) or not prompt.strip() or '{{ref' in prompt:
            raise ValueError('请传入不含原生引用占位符的生成正文')
        hashes = list(reference_sha256)
        if any(not isinstance(item, str) or not re.fullmatch(r'[0-9a-f]{64}', item) for item in hashes):
            raise ValueError('须按上传顺序提供参考原件 SHA-256')
        if not isinstance(parameters, dict) or set(parameters) != {'model', 'size', 'ratio', 'count'}:
            raise ValueError('须提供实际模型、尺寸、比例和张数')
        suffix = ''.join('{{ref %d}}' % index for index in range(1, len(hashes) + 1))
        expected = ' '.join(prompt.split())

        def ready(node):
            data = node['data']
            stored = data.get('prompt', '')
            if not isinstance(stored, str) or not stored.rstrip().endswith(suffix):
                return False
            body = stored.rstrip().removesuffix(suffix)
            refs = data.get('localReferences', {}).get('in', [])
            model = data.get('promptModel')
            if (' '.join(body.split()) != expected or not isinstance(model, list)
                    or len(refs) != len(hashes)
                    or any(data.get(key) != value for key, value in parameters.items())):
                return False
            written = ' '.join(''.join(token['text'] for token in line if token['type'] == 'Write')
                               for line in model)
            native = [ref for line in model for token in line if token['type'] == 'Custom'
                      for ref in NativeTag(token['html']).refs]
            expected_native = [([node_id, ref['sourceHandle']], '{{ref %d}}' % index)
                               for index, ref in enumerate(refs, 1)]
            return ' '.join(written.split()) == expected and native == expected_native

        node = self._wait_for_image_node(node_id, ready, timeout, '图片正文、原生引用或参数尚未保存')
        slots = self.digest_slots([node])[node_id]
        if [slot.get('sha256') for slot in slots] != hashes:
            raise ValueError(f'图片原生参考的原件或顺序不符：{node_id}')
        return _image_output_path(node)

    def wait_for_image_result(self, node_id, previous_output, *, timeout=120):
        """Wait for a new output on the same node after a single submission."""
        def ready(node):
            output = _image_output_path(node)
            return output is not None and output != previous_output

        node = self._wait_for_image_node(node_id, ready, timeout, '未读到新原图；先查询原任务')
        return _image_output_path(node)

    def write_canvas(self, expected_graph, next_graph):
        """Check the current canvas, then send a single save within that page evaluation."""
        if not all(map(_is_canvas, (expected_graph, next_graph))):
            raise ValueError('expected a native Redframe canvas')
        result, = self._request('write', {
            'path': _CANVAS_FILE, 'expected': expected_graph, 'graph': next_graph,
        }, 1, min(self.read_budget, self.timeout * 2))
        status = result.get('status')
        if result.get('error') is not None or not isinstance(status, int) or not 200 <= status < 300:
            attempts = [_attempt(item) for item in (result.get('before_write'), result) if item is not None]
            raise RedframeWorkspaceError('canvas save', attempts)

    def digest_image_outputs(self, nodes):
        """Read exact source-card image bytes through the existing bounded transport."""
        records, slots, seen = [], [], set()
        for node in nodes:
            node_id = node.get('id') if isinstance(node, dict) else None
            if (not isinstance(node_id, str) or not _ID.fullmatch(node_id) or node_id in seen
                    or node.get('type') not in {'remote-imageNode', 'remote-imageGenerationNode'}):
                raise ValueError('expected unique native source image nodes')
            path = _image_output_path(node)
            if path is None:
                raise ValueError(f'source image has no native output: {node_id}')
            seen.add(node_id)
            records.append({'node_id': node_id, 'path': path})
            slots.append({'path': path, 'family': 'image'})
        results = self._read('attachments', {'slots': slots}, slots=slots)
        return [{**record, **result} for record, result in zip(records, results)]

    def digest_slots(self, nodes):
        """Hash persisted attachment files directly, in the canvas's slot order."""
        out = {}
        records, slots = [], []
        for node in nodes:
            node_id = node.get('id') if isinstance(node, dict) else None
            if not isinstance(node_id, str) or not _ID.fullmatch(node_id) or node_id in out:
                raise ValueError('expected unique native draft IDs')
            out[node_id] = []
            data = node.get('data')
            references = data.get('localReferences', {}) if isinstance(data, dict) else None
            native_slots = references.get('in', []) if isinstance(references, dict) else None
            if not isinstance(native_slots, list):
                raise ValueError(f'invalid persisted attachment list: {node_id}')
            handles = set()
            for index, slot in enumerate(native_slots, 1):
                value = slot.get('value') if isinstance(slot, dict) else None
                if not isinstance(value, dict):
                    raise ValueError(f'invalid persisted attachment: {node_id} slot {index}')
                path = value.get('url')
                handle = slot.get('sourceHandle')
                family = {'IMAGE': 'image', 'VIDEO': 'video', 'AUDIO': 'audio'}.get(str(slot.get('dataType')))
                if (slot.get('source') != node_id or not isinstance(handle, str) or not handle or handle in handles
                        or not family or not _slot_path(path, node_id)):
                    raise ValueError(f'invalid native attachment identity: {node_id} slot {index}')
                handles.add(handle)
                records.append({'index': index, 'source': node_id, 'media_id': handle, 'path': path})
                slots.append({'path': path, 'family': family})
        results = self._read('attachments', {'slots': slots}, slots=slots)
        for record, result in zip(records, results):
            out[record['source']].append({**record, **result})
        return out


def _slot_path(path, node_id):
    if not isinstance(path, str) or any(char in path for char in '\\?#:\x00'):
        return False
    parts = path.split('/')
    return (len(parts) >= 3 and parts[:2] == ['assets', node_id]
            and all(part not in {'', '.', '..'} for part in parts))


def _image_output_path(node):
    image = node['data'].get('outputs', {}).get('image') or {}
    path = (image.get('value') or {}).get('url')
    return path if _slot_path(path, node['id']) else None
