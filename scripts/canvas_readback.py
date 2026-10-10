"""Narrow, read-only comparison of frozen draft payloads and editor readback.

This consumes evidence from a platform-specific reader; it does not invent a
remote backend or infer editor persistence from graph edges. No network/writes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def target_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('exact HTTP(S) target URL required')
    return urlunsplit(parsed)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


def rows_by_id(rows: list) -> dict:
    if not isinstance(rows, list) or not rows:
        raise ValueError('drafts must be a nonempty list')
    result = {}
    for row in rows:
        node_id = row.get('node_id') if isinstance(row, dict) else None
        if not isinstance(node_id, str) or not node_id or node_id in result:
            raise ValueError('missing or duplicate exact draft node_id')
        result[node_id] = row
    return result


def compare(frozen: dict, observed: dict) -> dict:
    """Report only selected full-field readback; metadata is not remote proof."""
    if target_url(frozen['target_url']) != target_url(observed['target_url']):
        raise ValueError('readback target differs from frozen target')
    if observed.get('phase') != 'after_refresh':
        raise ValueError('post-refresh editor readback required; graph/write receipt is insufficient')
    if observed.get('frozen_sha256') != digest(frozen):
        raise ValueError('readback does not identify this frozen payload')
    expected = rows_by_id(frozen['drafts'])
    actual = rows_by_id(observed['drafts'])
    differences = []
    native_reference_checks = {}
    if set(actual) != set(expected):
        differences.append({'field': 'node_ids', 'missing': sorted(set(expected)-set(actual)),
                            'extra': sorted(set(actual)-set(expected))})
    for node_id, row in expected.items():
        got = actual.get(node_id, {})
        for field in ('prompt', 'parameters', 'ordered_media'):
            value = row.get(field)
            valid = (isinstance(value, str) if field == 'prompt' else
                     isinstance(value, dict) if field == 'parameters' else
                     isinstance(value, list) and all(isinstance(r, dict) and
                         isinstance(r.get('node_id'), str) and r['node_id'] and
                         isinstance(r.get('media_id'), str) and r['media_id'] for r in value))
            if field not in row or not valid:
                raise ValueError(f'incomplete frozen {node_id}: {field}')
            if field not in got or digest(got[field]) != digest(value):
                differences.append({'node_id': node_id, 'field': field,
                                    'expected_sha256': digest(value),
                                    'observed_sha256': digest(got[field]) if field in got else None})
        # Native rich-text entities are distinct evidence from plain prompt text
        # and media edges. Opt in per draft so historical/plain-text clients keep
        # their original comparison scope without claiming entity verification.
        native_reference_checks[node_id] = 'not_checked'
        if 'native_entity_type' in row:
            entity_type = row['native_entity_type']
            if not isinstance(entity_type, str) or not entity_type.strip():
                raise ValueError(f'incomplete frozen {node_id}: native_entity_type')
            mentions = got.get('native_mentions')
            valid_mentions = isinstance(mentions, list) and all(
                isinstance(ref, dict) and ref.get('entity_type') == entity_type
                and ref.get('native_noneditable_entity') is True
                and isinstance(ref.get('node_id'), str) and ref['node_id']
                and isinstance(ref.get('media_id'), str) and ref['media_id']
                for ref in mentions)
            identities = ([{'node_id': ref['node_id'], 'media_id': ref['media_id']}
                           for ref in mentions] if valid_mentions else None)
            matched = valid_mentions and identities == row['ordered_media']
            native_reference_checks[node_id] = 'passed' if matched else 'failed'
            if not matched:
                differences.append({'node_id': node_id, 'field': 'native_mentions',
                                    'expected_sha256': digest(row['ordered_media']),
                                    'observed_sha256': digest(identities) if identities is not None else None})
    return {'ok': not differences, 'scope': 'selected post-refresh editor fields only',
            'draft_count': len(expected), 'frozen_sha256': digest(frozen),
            'observed_sha256': digest(observed), 'differences': differences,
            'native_reference_checks': native_reference_checks,
            'limitations': 'Evidence collector must actually refresh and read native editor values. '
                           'Native entity checks only cover explicitly requested drafts. '
                           'This comparison cannot establish remote provenance, pixel quality, generation status or generation authorization.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    parser.add_argument('--readback', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compare(json.loads(args.frozen.read_text(encoding='utf-8')),
                         json.loads(args.readback.read_text(encoding='utf-8')))
    except (ValueError, KeyError, TypeError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
