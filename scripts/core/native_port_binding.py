"""Offline consistency checks for native node/port + slot-download evidence.

This does not authenticate collection, establish approval, infer a media_id, or
permit production. Revisions are caller-supplied expectations, not DOM facts.
"""
import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote


_EXPECTED = {'target_url', 'target_project_id', 'draft_revision', 'draft_node_id',
             'source_segment_id', 'slot', 'node_id', 'reference_port', 'sha256',
             'revision', 'phase'}
_REPORTS = {'post_refresh': 'post-refresh-binding.json',
            'slot_downloads': 'final-binding-byte-check.json',
            'source_ports': 'final-source-ports.json'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _keys(value, keys, label):
    _require(isinstance(value, dict) and set(value) == set(keys),
             f'{label}: unsupported or missing keys')


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _file(root, entry, label):
    _keys(entry, {'path', 'sha256'}, label)
    _require(isinstance(entry['path'], str) and bool(entry['path'].strip()), f'{label}: invalid path')
    _require(_sha(entry['sha256']), f'{label}: invalid SHA256')
    try:
        path = (root / entry['path']).resolve(strict=True)
        _require(path.is_relative_to(root) and path.is_file(), f'{label}: file outside root or not regular')
        data = path.read_bytes()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f'{label}: unavailable file') from exc
    _require(bool(data), f'{label}: empty file')
    _require(hashlib.sha256(data).hexdigest() == entry['sha256'], f'{label}: file hash mismatch')
    return path, data


def _time(value, label):
    try:
        _require(isinstance(value, str), f'{label}: missing timestamp')
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        _require(result.tzinfo is not None and result.utcoffset() is not None,
                 f'{label}: timezone required')
        return result
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label}: invalid timezone timestamp') from exc


class _ProjectInput(HTMLParser):
    def __init__(self):
        super().__init__()
        self.titles = []

    def handle_starttag(self, tag, attrs):
        if tag == 'input':
            self.titles.append(dict(attrs).get('title'))


def verify_port_binding(root: Path, binding: dict, expected: dict) -> dict:
    """Verify one exact slot against local, hash-bound platform observations.

    Paths may be relative or absolute but must resolve to real files inside root.
    The old absolute receipt path is only a collection locator, never opened.
    Raw report schemas may contain additional UI observations; the declaration
    and file-descriptor schemas are closed. Raises ValueError on any mismatch.
    """
    try:
        return _verify(Path(root).resolve(strict=True), binding, expected)
    except (KeyError, TypeError, IndexError, AttributeError, OSError, RuntimeError) as exc:
        raise ValueError('native port binding: malformed or unavailable evidence') from exc


def _verify(root, binding, expected):
    _require(root.is_dir(), 'root must be a directory')
    _keys(expected, _EXPECTED, 'expected')
    _keys(binding, _EXPECTED | {'identity_mode', 'download', 'evidence'}, 'binding')
    for key in _EXPECTED - {'slot'}:
        _require(isinstance(expected[key], str) and bool(expected[key].strip()), f'expected {key}: empty or invalid')
    _require(type(expected['slot']) is int and expected['slot'] > 0, 'slot must be a positive integer')
    _require(expected['phase'] == 'after_refresh', 'phase must be after_refresh')
    _require(expected['reference_port'] in {'image', 'main'}, 'unsupported reference port')
    _require(_sha(expected['sha256']), 'expected: invalid SHA256')
    _require(binding['identity_mode'] == 'node_port_download', 'wrong identity_mode')
    for key in _EXPECTED:
        _require(type(binding[key]) is type(expected[key]) and binding[key] == expected[key], f'binding {key}: mismatch')
    _keys(binding['evidence'], {'scope', *_REPORTS}, 'evidence')
    _, download = _file(root, binding['download'], 'download')
    _require(hashlib.sha256(download).hexdigest() == expected['sha256'], 'download differs from expected source bytes')
    reports = {}
    for label, entry in binding['evidence'].items():
        path, data = _file(root, entry, label)
        if label in _REPORTS:
            _require(path.name == _REPORTS[label], f'{label}: original report filename required')
        try:
            reports[label] = json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f'{label}: invalid JSON') from exc
        _require(isinstance(reports[label], dict), f'{label}: expected JSON object')
    scope, post, slots, ports = (reports[k] for k in ('scope', 'post_refresh', 'slot_downloads', 'source_ports'))
    for label, filename in _REPORTS.items():
        _require(scope['evidence_hashes'][filename] == binding['evidence'][label]['sha256'],
                 f'scope: {label} hash mismatch')
    _require(scope['url'] == post['url'] == expected['target_url'], 'raw target URL mismatch')
    _require(scope['project']['title'] == scope['workspace_directory'] == expected['target_project_id'],
             'raw project identity mismatch')
    parser = _ProjectInput()
    parser.feed(scope['project']['outerHTML'])
    _require(parser.titles == [expected['target_project_id']], 'project input title mismatch')
    # Additive collection aliases must not contradict the original UI observations.
    for key in ('phase', 'target_url', 'target_project_id'):
        if key in scope:
            _require(scope[key] == expected[key], f'scope {key}: mismatch')
    segment = expected['source_segment_id']
    _require(scope[segment] == post[segment]['id'] == expected['draft_node_id'], 'raw draft node mismatch')
    if 'draft_node_ids' in scope:
        _require(scope['draft_node_ids'][segment] == expected['draft_node_id'], 'scope draft alias mismatch')
    if 'evidence_sha256' in scope:
        for label in _REPORTS:
            _require(scope['evidence_sha256'][label] == binding['evidence'][label]['sha256'], 'scope hash alias mismatch')
    refreshed = _time(post['observed_at'], 'post_refresh')
    _require(slots['observed_after_refresh'] == post['observed_at'], 'slot receipt from different refresh')
    _require(_time(scope['observed_at'], 'scope') >= refreshed, 'scope predates refresh')
    _require(_time(ports['observed_at'], 'source_ports') >= refreshed, 'ports predate refresh')
    _require(slots['media_id_exposed'] is False and slots['video_submitted'] is False,
             'receipt must not expose media_id or submit video')
    mentions = post[segment]['mentions']
    _require(isinstance(mentions, list) and len(mentions) >= expected['slot'], 'missing native slot')
    # Position is authoritative; duplicate/misordered slot numbering is rejected.
    for order, mention in enumerate(mentions, 1):
        _require(isinstance(mention, dict) and type(mention.get('order')) is int
                 and mention['order'] == order, 'native order mismatch')
    mention = mentions[expected['slot'] - 1]
    _require(mention['noneditable'] is True, 'native reference is editable')
    _require(mention['token'] == '{{ref %d}}' % expected['slot'], 'native token mismatch')
    pair = [expected['node_id'], expected['reference_port']]
    _require(mention['decoded'] == pair, 'native decoded tuple mismatch')
    try:
        raw_pair = json.loads(unquote(mention['raw_reference_id'], errors='strict'))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('invalid raw reference tuple') from exc
    _require(raw_pair == pair, 'native raw tuple mismatch')
    rows = slots['checks'][segment]['bound_media']
    _require(isinstance(rows, list), 'missing slot download receipts')
    receipts = [row for row in rows if type(row.get('slot')) is int and row['slot'] == expected['slot']]
    _require(len(receipts) == 1, 'missing or duplicate slot receipt')
    receipt = receipts[0]
    _require(isinstance(receipt['path'], str) and bool(receipt['path'].strip()), 'missing observed download locator')
    _require(receipt['match'] is True and receipt['sha256'] == receipt['expected_sha256'] == expected['sha256'],
             'raw slot download hash mismatch')
    _require(json.dumps(receipt['native'], sort_keys=True) == json.dumps(mention, sort_keys=True),
             'slot receipt native entity mismatch')
    sources = [source for source in ports['sources'] if source['id'] == expected['node_id']]
    _require(len(sources) == 1, 'missing or duplicate source node')
    source = sources[0]
    handles = [handle for handle in source['ports'] if handle['port'] == expected['reference_port']]
    _require(len(handles) == 1, 'missing or duplicate source port')
    handle = handles[0]
    classes = handle['class'].split()
    _require(handle['handleid'] == expected['reference_port'] and 'source' in classes and 'target' not in classes,
             'reference port is not an output handle')
    # Offscreen/collapsed source nodes may omit their IMG. The actual input-slot
    # download above proves bytes; source DOM proves the output port, not visibility.
    return {'ok': True, 'identity_mode': 'node_port_download', 'consistency_only': True,
            'production_ready': False, 'media_id_inferred': False,
            'slot': expected['slot'], 'node_id': expected['node_id'],
            'reference_port': expected['reference_port'], 'sha256': expected['sha256']}
