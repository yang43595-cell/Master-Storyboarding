"""Synthetic-only native-port contract tests; no live project/pass claims."""
import copy
import hashlib
import json
from urllib.parse import quote

import pytest

from scripts.core.native_port_binding import verify_port_binding


FILENAMES = {'scope': 'scope.json', 'post_refresh': 'post-refresh-binding.json',
             'slot_downloads': 'final-binding-byte-check.json',
             'source_ports': 'final-source-ports.json'}


def synthetic(root, port='image'):
    data = b'explicitly synthetic image bytes, not a platform download'
    sha = hashlib.sha256(data).hexdigest()
    (root / 'download.bin').write_bytes(data)
    expected = dict(target_url='https://synthetic.invalid/#canvas', target_project_id='/synthetic/workspace',
                    draft_revision='draft-r1', draft_node_id='synthetic-draft', source_segment_id='SYNTHETIC-09',
                    slot=1, node_id='synthetic-source', reference_port=port, sha256=sha,
                    revision='source-r1', phase='after_refresh')
    mention = dict(noneditable=True, order=1, raw_reference_id=quote(json.dumps(['synthetic-source', port])),
                   decoded=['synthetic-source', port], token='{{ref 1}}', text='参考 1')
    timestamp = '2026-01-01T12:00:00Z'
    reports = {
        'scope': dict(observed_at=timestamp, url=expected['target_url'],
                      project=dict(title=expected['target_project_id'], outerHTML='<input title="/synthetic/workspace">'),
                      workspace_directory=expected['target_project_id'], **{'SYNTHETIC-09': 'synthetic-draft'}),
        'post_refresh': dict(observed_at=timestamp, url=expected['target_url'],
                             **{'SYNTHETIC-09': dict(id='synthetic-draft', mentions=[mention])}),
        'slot_downloads': dict(observed_after_refresh=timestamp, media_id_exposed=False, video_submitted=False,
                               checks={'SYNTHETIC-09': dict(bound_media=[dict(slot=1, path='/old/observed/locator.bin',
                                      sha256=sha, expected_sha256=sha, match=True, native=copy.deepcopy(mention))])}),
        'source_ports': dict(observed_at=timestamp, sources=[dict(id='synthetic-source',
                            ports=[dict(port=port, handleid=port, **{'class': 'handle source connected'})],
                            images=[dict(naturalWidth=100, naturalHeight=80)])])}
    binding = dict(expected, identity_mode='node_port_download', download=dict(path='download.bin', sha256=sha), evidence={})
    persist(root, reports, binding)
    return binding, expected, reports


def persist(root, reports, binding):
    """Rehash synthetic reports, so negative checks test semantics, not stale hashes."""
    evidence = {}
    for label in ('post_refresh', 'slot_downloads', 'source_ports', 'scope'):
        if label == 'scope':
            reports[label]['evidence_hashes'] = {FILENAMES[k]: v['sha256'] for k, v in evidence.items()}
        payload = json.dumps(reports[label], ensure_ascii=False).encode()
        (root / FILENAMES[label]).write_bytes(payload)
        evidence[label] = dict(path=FILENAMES[label], sha256=hashlib.sha256(payload).hexdigest())
    binding['evidence'] = evidence


@pytest.mark.parametrize('port', ['image', 'main'])
def test_synthetic_happy_output_port(tmp_path, port):
    binding, expected, _ = synthetic(tmp_path, port)
    result = verify_port_binding(tmp_path, binding, expected)
    assert result['ok'] and result['consistency_only']
    assert result['production_ready'] is False and result['media_id_inferred'] is False
    assert 'media_id' not in result


@pytest.mark.parametrize('field', sorted({'target_url', 'target_project_id', 'draft_revision', 'draft_node_id',
                                         'source_segment_id', 'slot', 'node_id', 'reference_port', 'sha256', 'revision', 'phase'}))
def test_expected_identity_cannot_drift(tmp_path, field):
    binding, expected, _ = synthetic(tmp_path)
    binding[field] = 2 if field == 'slot' else 'wrong'
    with pytest.raises(ValueError):
        verify_port_binding(tmp_path, binding, expected)


@pytest.mark.parametrize('mutation', [
    'wrong_url', 'wrong_project', 'wrong_html_title', 'wrong_workspace', 'wrong_draft',
    'wrong_node', 'wrong_port', 'raw_tuple', 'order', 'token', 'editable', 'native_receipt',
    'wrong_receipt_hash', 'wrong_expected_hash', 'receipt_match', 'duplicate_receipt',
    'missing_receipt', 'missing_locator', 'stale_scope', 'stale_ports', 'wrong_refresh',
    'naive_timestamp', 'missing_timestamp', 'receipt_boolean_order', 'target_port', 'substring_source', 'wrong_handle',
    'missing_source', 'duplicate_source', 'media_exposed', 'video_submitted',
])
def test_raw_crosschecks_not_just_report_hashes(tmp_path, mutation):
    binding, expected, reports = synthetic(tmp_path)
    scope, post, slots, ports = (reports[k] for k in ('scope', 'post_refresh', 'slot_downloads', 'source_ports'))
    mention = post['SYNTHETIC-09']['mentions'][0]
    rows = slots['checks']['SYNTHETIC-09']['bound_media']
    source = ports['sources'][0]
    if mutation == 'wrong_url': post['url'] = 'https://other.invalid/'
    elif mutation == 'wrong_project': scope['project']['title'] = '/wrong'
    elif mutation == 'wrong_html_title': scope['project']['outerHTML'] = '<div title="/synthetic/workspace"></div>'
    elif mutation == 'wrong_workspace': scope['workspace_directory'] = '/wrong'
    elif mutation == 'wrong_draft': post['SYNTHETIC-09']['id'] = 'wrong'
    elif mutation == 'wrong_node': mention['decoded'][0] = 'wrong'
    elif mutation == 'wrong_port': mention['decoded'][1] = 'main'
    elif mutation == 'raw_tuple': mention['raw_reference_id'] = quote(json.dumps(['synthetic-source', 'main']))
    elif mutation == 'order': mention['order'] = 2
    elif mutation == 'token': mention['token'] = '{{ref 2}}'
    elif mutation == 'editable': mention['noneditable'] = False
    elif mutation == 'native_receipt': rows[0]['native']['text'] = 'different raw entity'
    elif mutation == 'receipt_boolean_order': rows[0]['native']['order'] = True
    elif mutation == 'wrong_receipt_hash': rows[0]['sha256'] = '0' * 64
    elif mutation == 'wrong_expected_hash': rows[0]['expected_sha256'] = '0' * 64
    elif mutation == 'receipt_match': rows[0]['match'] = False
    elif mutation == 'duplicate_receipt': rows.append(copy.deepcopy(rows[0]))
    elif mutation == 'missing_receipt': rows.clear()
    elif mutation == 'missing_locator': rows[0]['path'] = ''
    elif mutation == 'stale_scope': scope['observed_at'] = '2025-01-01T12:00:00Z'
    elif mutation == 'stale_ports': ports['observed_at'] = '2025-01-01T12:00:00Z'
    elif mutation == 'wrong_refresh': slots['observed_after_refresh'] = '2026-01-01T12:01:00Z'
    elif mutation == 'naive_timestamp': post['observed_at'] = slots['observed_after_refresh'] = '2026-01-01T12:00:00'
    elif mutation == 'missing_timestamp': scope.pop('observed_at')
    elif mutation == 'target_port': source['ports'][0]['class'] = 'source target'
    elif mutation == 'substring_source': source['ports'][0]['class'] = 'not-source'
    elif mutation == 'wrong_handle': source['ports'][0]['handleid'] = 'main'
    elif mutation == 'missing_source': ports['sources'].clear()
    elif mutation == 'duplicate_source': ports['sources'].append(copy.deepcopy(source))
    elif mutation == 'media_exposed': slots['media_id_exposed'] = True
    elif mutation == 'video_submitted': slots['video_submitted'] = True
    persist(tmp_path, reports, binding)
    with pytest.raises(ValueError):
        verify_port_binding(tmp_path, binding, expected)


@pytest.mark.parametrize('mutation', ['media_id', 'extra', 'missing_key', 'missing_evidence', 'extra_evidence',
                                    'missing_file', 'empty_file', 'stale_hash', 'scope_hash', 'download_hash',
                                    'different_bytes', 'arbitrary_hash_only', 'boolean_slot', 'invalid_phase',
                                    'unsupported_port', 'malformed_json', 'extra_descriptor', 'symlink_escape'])
def test_fail_closed_envelopes_and_files(tmp_path, mutation):
    binding, expected, reports = synthetic(tmp_path)
    if mutation == 'media_id': binding['media_id'] = 'image'
    elif mutation == 'extra': binding['production_ready'] = True
    elif mutation == 'missing_key': binding.pop('draft_revision')
    elif mutation == 'missing_evidence': binding['evidence'].pop('post_refresh')
    elif mutation == 'extra_evidence': binding['evidence']['untrusted'] = {}
    elif mutation == 'missing_file': (tmp_path / 'post-refresh-binding.json').unlink()
    elif mutation == 'empty_file': (tmp_path / 'download.bin').write_bytes(b'')
    elif mutation == 'stale_hash': (tmp_path / 'post-refresh-binding.json').write_text('{}')
    elif mutation == 'scope_hash':
        scope = reports['scope']; scope['evidence_hashes']['post-refresh-binding.json'] = '0' * 64
        data = json.dumps(scope).encode(); (tmp_path / 'scope.json').write_bytes(data)
        binding['evidence']['scope']['sha256'] = hashlib.sha256(data).hexdigest()
    elif mutation == 'download_hash': binding['download']['sha256'] = 'invalid'
    elif mutation == 'different_bytes':
        (tmp_path / 'download.bin').write_bytes(b'arbitrary local file')
        binding['download']['sha256'] = hashlib.sha256(b'arbitrary local file').hexdigest()
    elif mutation == 'arbitrary_hash_only': binding['evidence'] = {}
    elif mutation == 'boolean_slot': binding['slot'] = expected['slot'] = True
    elif mutation == 'invalid_phase': binding['phase'] = expected['phase'] = 'before_refresh'
    elif mutation == 'unsupported_port': binding['reference_port'] = expected['reference_port'] = 'audio'
    elif mutation == 'malformed_json':
        data = b'{'; (tmp_path / 'scope.json').write_bytes(data)
        binding['evidence']['scope']['sha256'] = hashlib.sha256(data).hexdigest()
    elif mutation == 'extra_descriptor': binding['download']['media_id'] = 'wrong'
    elif mutation == 'symlink_escape':
        outside = tmp_path.parent / (tmp_path.name + '-outside.bin')
        outside.write_bytes((tmp_path / 'download.bin').read_bytes())
        (tmp_path / 'download.bin').unlink(); (tmp_path / 'download.bin').symlink_to(outside)
    with pytest.raises(ValueError):
        verify_port_binding(tmp_path, binding, expected)


def test_collapsed_source_needs_slot_bytes_not_duplicate_source_thumbnail(tmp_path):
    binding, expected, reports = synthetic(tmp_path)
    reports['source_ports']['sources'][0]['images'] = []
    persist(tmp_path, reports, binding)
    assert verify_port_binding(tmp_path, binding, expected)['ok']
    (tmp_path / 'download.bin').unlink()
    with pytest.raises(ValueError):
        verify_port_binding(tmp_path, binding, expected)
