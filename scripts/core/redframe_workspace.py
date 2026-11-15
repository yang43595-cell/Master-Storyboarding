"""Read Redframe's workspace files through the authenticated canvas page."""

from __future__ import annotations

from html.parser import HTMLParser
import json
import re
import time
from urllib.parse import unquote


REDFRAME_ORIGIN = "https://redframe-ai.xmpaohong.com:28888"
_CANVAS_FILE = "画布1.json"
_RETRY_STATUSES = {408, 502, 503, 504}
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
REF_TOKEN = re.compile(r'\{\{ref (\d+)\}\}')
_CHARACTER_CERT_FIELDS = {'characterId', 'assetId', 'projectName', 'sourceUrl', 'mimeType', 'imageDigest'}

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


class _NativeTag(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.refs = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'data-reference-id' not in values:
            return
        if tag != 'span' or 'imageReference' not in values.get('class', '').split():
            raise ValueError('原生提示词引用格式已改变')
        identity = json.loads(unquote(values['data-reference-id']))
        slot = REF_TOKEN.fullmatch(values.get('data-value', ''))
        if not isinstance(identity, list) or len(identity) != 2 or not all(
                isinstance(part, str) and part for part in identity) or slot is None:
            raise ValueError('原生提示词引用缺少准确节点、端口或槽位')
        self.refs.append((*identity, int(slot[1])))


def native_prompt_mentions(data):
    """Read persisted chips, excluding plain text that only looks like a reference."""
    result = []
    for line in data['promptModel']:
        for token in line:
            if token['type'] == 'Custom':
                (mention,) = _NativeTag(token['html']).refs
                result.append(mention)
    return result


def native_reference_slots(graph, node):
    """Place direct uploads around the exact slots of certified character edges."""
    ident = node['id']
    data = node.get('data')
    references = data.get('localReferences', {}) if isinstance(data, dict) else None
    refs = references.get('in', []) if isinstance(references, dict) else None
    if not isinstance(refs, list):
        raise ValueError(f'invalid persisted attachment list: {ident}')
    incoming = [edge for edge in graph['edges'] if edge.get('target') == ident] if graph is not None else []
    if not incoming:
        return refs, {}
    nodes = {row['id']: row for row in graph['nodes']}
    if len(nodes) != len(graph['nodes']):
        raise ValueError('画布返回重复节点 ID')
    mentions = native_prompt_mentions(node['data'])
    total = len(refs) + len(incoming)
    linked = {}
    for edge in incoming:
        source_id = edge.get('source')
        source = nodes.get(source_id)
        if (source is None or source_id == ident or source.get('type') != 'remote-characterNode'
                or (edge.get('type'), edge.get('sourceHandle'), edge.get('targetHandle'))
                != ('simple-bezier', 'image', 'in')):
            raise ValueError(f'{ident} 含未支持的红帧入边')
        data = source.get('data') or {}
        output = (data.get('outputs') or {}).get('image') or {}
        value, certificate = output.get('value') or {}, data.get('faceCertification')
        mime = value.get('mimeType')
        url = value.get('url')
        if output.get('dataType') != 'IMAGE' or not _slot_path(url, source_id):
            raise ValueError(f'{ident} 的角色连线缺准确图片输出')
        if (not isinstance(certificate, dict) or set(certificate) != _CHARACTER_CERT_FIELDS
                or certificate != (data.get('faceCertifications') or {}).get('image')
                or certificate.get('characterId') != (data.get('characterLibrary') or {}).get('id')
                or certificate.get('assetId') != value.get('assetId')
                or certificate.get('sourceUrl') != url or certificate.get('mimeType') != mime
                or not isinstance(mime, str) or not mime.startswith('image/')
                or not all(isinstance(certificate.get(key), str) and certificate[key].strip()
                           for key in ('characterId', 'assetId', 'projectName'))
                or not re.fullmatch(r'[a-f0-9]{64}', str(certificate.get('imageDigest', '')))):
            raise ValueError(f'{ident} 的角色连线认证记录或身份无法核实')
        matches = [slot for owner, port, slot in mentions if (owner, port) == (source_id, 'image')]
        if len(matches) != 1 or not 1 <= matches[0] <= total or matches[0] in linked:
            raise ValueError(f'{ident} 的角色连线缺少唯一对应的正文原生 @ 槽')
        linked[matches[0]] = (dict(source=source_id, sourceHandle='image', dataType='IMAGE', value=value), certificate)
    direct = iter(refs)
    return [linked[i][0] if i in linked else next(direct) for i in range(1, total + 1)], {
        index: certificate for index, (_, certificate) in linked.items()}


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

    def _read(self, operation, payload, slots=None):
        deadline = time.monotonic() + self.read_budget / 1000
        count = len(slots) if slots is not None else 1
        if not count:
            return []
        first = self._request(operation, payload, count, self.read_budget)
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

    def read_canvas(self):
        result, = self._read('canvas', {'path': _CANVAS_FILE})
        graph = result.get('graph')
        if result.get('error') is not None or result.get('status') != 200 or not _is_canvas(graph):
            attempts = result['attempts']
            if attempts[-1]['error'] is None:
                attempts[-1]['error'] = 'invalid_canvas'
            raise RedframeWorkspaceError('canvas read', attempts)
        return graph

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

    def digest_slots(self, nodes, *, graph=None):
        """Hash persisted attachment files directly, in the canvas's slot order."""
        out = {}
        records, slots = [], []
        for node in nodes:
            node_id = node.get('id') if isinstance(node, dict) else None
            if not isinstance(node_id, str) or not _ID.fullmatch(node_id) or node_id in out:
                raise ValueError('expected unique native draft IDs')
            out[node_id] = []
            native_slots, linked = native_reference_slots(graph, node)
            handles = set()
            for index, slot in enumerate(native_slots, 1):
                value = slot.get('value') if isinstance(slot, dict) else None
                if not isinstance(value, dict):
                    raise ValueError(f'invalid persisted attachment: {node_id} slot {index}')
                path = value.get('url')
                owner = slot.get('source')
                handle = slot.get('sourceHandle')
                family = {'IMAGE': 'image', 'VIDEO': 'video', 'AUDIO': 'audio'}.get(str(slot.get('dataType')))
                if (not isinstance(owner, str) or not _ID.fullmatch(owner)
                        or (index not in linked and owner != node_id)
                        or not isinstance(handle, str) or not handle or (owner, handle) in handles
                        or not family or not _slot_path(path, owner)):
                    raise ValueError(f'invalid native attachment identity: {node_id} slot {index}')
                handles.add((owner, handle))
                records.append((node_id, {'index': index, 'source': owner, 'path': path,
                    ('reference_port' if index in linked else 'media_id'): handle}))
                slots.append({'path': path, 'family': family})
        results = self._read('attachments', {'slots': slots}, slots=slots)
        for (draft_id, record), result in zip(records, results):
            out[draft_id].append({**record, **result})
        return out


def _slot_path(path, node_id):
    if not isinstance(path, str) or any(char in path for char in '\\?#:\x00'):
        return False
    parts = path.split('/')
    return (len(parts) >= 3 and parts[:2] == ['assets', node_id]
            and all(part not in {'', '.', '..'} for part in parts))
