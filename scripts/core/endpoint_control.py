"""Strict, offline first/last-frame evidence consistency, never motion approval.

Collection DTO: six hash-bound reports share target identity, phase, refresh_id,
observed_at and observed_after_refresh (= post_refresh.observed_at). Two explicit
collection shapes are supported without mixing aliases: original url + scope
project/title/outerHTML/workspace_directory/segment/evidence_hashes, or native
target_url + project_input/title/html/draft_node_ids/evidence_sha256. Native
source ports are parsed from actual handle HTML and cross-checked against their
source node HTML; original structured port attributes remain a separate shape.
Post-refresh retains the raw node buttons/text/html/reference_thumbnails/mentions.
Source/download/edge reports retain normal-UI receipts and actual DOM, not
invented inline reference entities. Original receipt paths are locators only;
local copies are independently hashed. Caller revisions are not platform facts.
"""
import json
import math
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from .native_port_binding import _file, _keys, _require, _sha, _time, _ProjectInput

_FIELDS = {'target_url', 'target_project_id', 'draft_revision', 'draft_node_id',
           'source_segment_id', 'duration', 'model', 'aspect_ratio', 'resolution', 'ordered_media'}
_REPORTS = {'scope', 'post_refresh', 'source_ports', 'slot_downloads', 'source_downloads', 'edges'}
_HINT = '图片引用按顺序作为首帧、尾帧'
_VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


class _DOM(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes, self.stack = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        _require(len(dict(attrs)) == len(attrs), 'duplicate DOM attribute')
        node = dict(tag=tag, attrs=dict(attrs), text=[], parents=list(self.stack))
        self.nodes.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]['tag'] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node['text'].append(data)


def _class(node, name):
    return name in node['attrs'].get('class', '').split()


def _text(node):
    return ' '.join(''.join(node['text']).split())


def _no_media_id(value):
    if isinstance(value, dict):
        _require('media_id' not in value, 'media_id cannot substitute for endpoint evidence')
        for item in value.values():
            _no_media_id(item)
    elif isinstance(value, list):
        for item in value:
            _no_media_id(item)


def verify_endpoint_control(root: Path, declaration: dict, expected: dict) -> dict:
    """Return endpoint-only consistency or raise ValueError; no writes/network."""
    try:
        return _verify(Path(root).resolve(strict=True), declaration, expected)
    except (KeyError, TypeError, IndexError, AttributeError, OSError, RuntimeError) as exc:
        raise ValueError('endpoint control: malformed or unavailable evidence') from exc


def _verify(root, declaration, expected):
    _require(root.is_dir(), 'root must be a directory')
    _keys(expected, _FIELDS, 'expected')
    _keys(declaration, {'kind', 'scope', 'phase', 'refresh_id', 'evidence', 'downloads'}, 'declaration')
    _no_media_id(declaration)
    for key in _FIELDS - {'duration', 'ordered_media'}:
        _require(isinstance(expected[key], str) and bool(expected[key].strip()), f'invalid expected {key}')
    duration = expected['duration']
    _require(type(duration) in (int, float) and math.isfinite(duration) and duration > 0, 'invalid duration')
    media = expected['ordered_media']
    _require(isinstance(media, list) and len(media) == 2, 'exactly two ordered endpoint references required')
    for ref in media:
        _keys(ref, {'node_id', 'reference_port', 'sha256'}, 'ordered media')
        _require(isinstance(ref['node_id'], str) and bool(ref['node_id'].strip())
                 and isinstance(ref['reference_port'], str) and ref['reference_port'] in {'image', 'main'}
                 and _sha(ref['sha256']), 'invalid endpoint source identity')
    _require(declaration['kind'] == 'first_last_frame_download', 'unsupported endpoint kind')
    _require(json.dumps(declaration['scope'], sort_keys=True) == json.dumps(expected, sort_keys=True), 'endpoint scope mismatch')
    _require(declaration['phase'] == 'after_refresh', 'endpoint requires after_refresh')
    _require(isinstance(declaration['refresh_id'], str) and bool(declaration['refresh_id'].strip()), 'refresh group required')
    _keys(declaration['evidence'], _REPORTS, 'evidence')
    reports, names = {}, {}
    for label, entry in declaration['evidence'].items():
        path, data = _file(root, entry, label)
        names[label] = path.name
        try:
            reports[label] = json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f'{label}: invalid JSON') from exc
        _require(isinstance(reports[label], dict), 'raw report must be an object')
        _no_media_id(reports[label])
    _require(len(set(names.values())) == len(names), 'distinct raw report filenames required')
    post, scope = reports['post_refresh'], reports['scope']
    native_collection = 'project_input' in scope
    forbidden = {'project', 'workspace_directory', 'evidence_hashes', expected['source_segment_id']} if native_collection else {'project_input', 'draft_node_ids', 'evidence_sha256'}
    _require(not (forbidden & set(scope)), 'mixed collection scope aliases')
    refresh_time = _time(post['observed_at'], 'post_refresh')
    for label, report in reports.items():
        url_key, forbidden_url = ('target_url', 'url') if native_collection else ('url', 'target_url')
        _require(forbidden_url not in report, 'mixed collection URL aliases')
        _require(report[url_key] == expected['target_url'] and report['target_project_id'] == expected['target_project_id'],
                 f'{label}: different target/project')
        _require(report['phase'] == 'after_refresh' and report['refresh_id'] == declaration['refresh_id']
                 and report['observed_after_refresh'] == post['observed_at'], f'{label}: different refresh group')
        _require(_time(report['observed_at'], label) >= refresh_time, f'{label}: stale observation')
        if label != 'scope':
            raw_hash = scope['evidence_sha256'][label] if native_collection else scope['evidence_hashes'][names[label]]
            _require(raw_hash == declaration['evidence'][label]['sha256'], 'scope raw report hash mismatch')
    project = scope['project_input'] if native_collection else scope['project']
    _require(project['title'] == expected['target_project_id'], 'raw project identity mismatch')
    if not native_collection:
        _require(scope['workspace_directory'] == expected['target_project_id'], 'raw workspace mismatch')
    project_dom = _ProjectInput(); project_dom.feed(project['html'] if native_collection else project['outerHTML'])
    _require(project_dom.titles == [expected['target_project_id']], 'project input title mismatch')
    segment = expected['source_segment_id']
    node = post[segment]
    draft_node_id = scope['draft_node_ids'][segment] if native_collection else scope[segment]
    _require(draft_node_id == node['id'] == expected['draft_node_id'], 'raw draft identity mismatch')
    _require(node['mentions'] == [], 'endpoint mode must not fabricate inline entities')
    _require(isinstance(node['text'], str), 'raw editor/node text required')
    if not native_collection:
        _require(_HINT in node['text'], 'missing ordered endpoint hint')
    dom = _DOM(node['html'])
    roots = [n for n in dom.nodes if _class(n, 'vue-flow__node')]
    _require(len(roots) == 1 and roots[0]['attrs'].get('data-id') == node['id'], 'raw node DOM mismatch')
    root_node = roots[0]
    inside = [n for n in dom.nodes if any(p is root_node for p in n['parents'])]
    hints = [n for n in inside if _class(n, 'referenceHint')]
    _require(len(hints) == 1 and _text(hints[0]) == _HINT, 'endpoint hint absent from real UI hint')
    controls = {'选择视频生成模式': '首尾帧', '选择视频生成时长': f'{duration:g}s',
                '选择视频比例和分辨率': f"{expected['aspect_ratio']} · {expected['resolution']}"}
    for label, value in controls.items():
        buttons = [b for b in node['buttons'] if b.get('label') == label]
        actual = [n for n in inside if n['tag'] == 'button'
                  and (n['attrs'].get('aria-label') or n['attrs'].get('title')) == label]
        _require(len(buttons) == len(actual) == 1 and buttons[0]['text'].strip() == value
                 and _text(actual[0]) == value, f'raw control mismatch: {label}')
    models = [n for n in inside if _class(n, 'el-select__placeholder')
              and any(_class(p, 'modelSelect') for p in n['parents'])]
    _require(len(models) == 1 and _text(models[0]) == expected['model'], 'raw selected model mismatch')
    thumbnails = node['reference_thumbnails']
    items = [n for n in inside if _class(n, 'referenceItem')]
    _require(isinstance(thumbnails, list) and len(thumbnails) == len(items) == 2, 'exactly two input thumbnails required')
    for slot, (thumb, item) in enumerate(zip(thumbnails, items), 1):
        _require(type(thumb['order']) is int and thumb['order'] == slot and isinstance(thumb['src'], str)
                 and bool(thumb['src']), 'thumbnail order/source mismatch')
        images = [n for n in inside if n['tag'] == 'img' and any(p is item for p in n['parents'])]
        _require(len(images) == 1 and images[0]['attrs'].get('src') == thumb['src']
                 and images[0]['attrs'].get('alt') == f'预览引用 {slot}', 'thumbnail DOM mismatch')
    edges = []
    for edge in reports['edges']['edges']:
        paths = [n for n in _DOM(edge['html']).nodes if n['tag'] == 'path' and 'source' in n['attrs']]
        _require(len(paths) == 1, 'edge needs actual path source/target attributes')
        a = paths[0]['attrs']
        edges.append((a['source'], a['sourcehandleid'], a['target'], a['targethandleid']))
    wanted = [(ref['node_id'], ref['reference_port'], node['id'], 'in') for ref in media]
    _require(Counter(edges) == Counter(wanted), 'actual incoming edge identities differ')
    slots = reports['slot_downloads']['checks'][segment]['bound_media']
    sources = reports['source_downloads']['sources']
    downloads = declaration['downloads']
    _require(isinstance(slots, list) and isinstance(sources, list) and isinstance(downloads, list)
             and len(slots) == len(sources) == len(downloads) == 2, 'two actual source and input downloads required')
    for slot, (ref, receipt, download, role) in enumerate(zip(media, slots, downloads, ('start_frame', 'end_frame')), 1):
        _keys(download, {'slot', 'role', 'source', 'input'}, 'download')
        _require(type(download['slot']) is int and download['slot'] == slot and download['role'] == role,
                 'endpoint role/order mismatch')
        _require(type(receipt['slot']) is int and receipt['slot'] == slot and receipt['src'] == thumbnails[slot-1]['src']
                 and receipt['method'] == 'input_thumbnail_download', 'raw slot receipt mismatch')
        source_receipts = [r for r in sources if r['node_id'] == ref['node_id'] and r['reference_port'] == ref['reference_port']]
        _require(len(source_receipts) == 1 and source_receipts[0]['method'] == 'normal_ui_download', 'normal UI source download missing')
        source = source_receipts[0]
        for label, raw in (('source', source), ('input', receipt)):
            _require(isinstance(raw['path'], str) and bool(raw['path'].strip()) and raw['sha256'] == ref['sha256'],
                     'raw download locator/hash mismatch')
            _file(root, download[label], f'{role} {label}')
            _require(download[label]['sha256'] == ref['sha256'], 'local copy differs from platform download/source hash')
        nodes = [s for s in reports['source_ports']['sources'] if s['id'] == ref['node_id']]
        _require(len(nodes) == 1, 'source DOM node missing/ambiguous')
        ports = [p for p in nodes[0]['ports'] if p['port'] == ref['reference_port']]
        _require(len(ports) == 1, 'missing/ambiguous output port')
        if native_collection:
            _require(not ({'handleid', 'class'} & set(ports[0])), 'mixed source port aliases')
            port_dom = _DOM(ports[0]['html'])
            handles = [n for n in port_dom.nodes if _class(n, 'vue-flow__handle')]
            _require(len(handles) == 1, 'source port DOM missing/ambiguous')
            a = handles[0]['attrs']
            _require(a.get('data-nodeid') == ref['node_id'] and a.get('data-node-port') == ref['reference_port'], 'source port DOM identity mismatch')
            handleid, classes = a.get('data-handleid'), a.get('class', '').split()
            source_dom = _DOM(nodes[0]['html'])
            source_nodes = [n for n in source_dom.nodes if _class(n, 'vue-flow__node')]
            _require(len(source_nodes) == 1 and source_nodes[0]['attrs'].get('data-id') == ref['node_id'], 'source node DOM identity mismatch')
            actual_handles = [n for n in source_dom.nodes if _class(n, 'vue-flow__handle') and n['attrs'].get('data-node-port') == ref['reference_port']]
            _require(len(actual_handles) == 1 and actual_handles[0]['attrs'] == a, 'source port absent from source node DOM')
        else:
            _require('html' not in ports[0], 'mixed source port aliases')
            handleid, classes = ports[0]['handleid'], ports[0]['class'].split()
        _require(handleid == ref['reference_port'] and 'source' in classes and 'target' not in classes, 'not an actual output port')
    return {'endpoint_constraints_verified': True, 'motion_verified': False, 'production_ready': False}
