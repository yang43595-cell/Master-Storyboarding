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


def validate_submission(frozen: dict, project: Path) -> dict:
    """Check the existing frozen delivery JSON, using current project authority.

    Roles/states are explicit production decisions, not inferred visual facts.
    No remote reads, generation, adoption or additional ledger.
    """
    import math
    try:
        from .core.artifacts import load_manifest, artifact_is_confirmed, project_path, digest as file_digest, validate_current_dependencies
        from .core.project_state import load_project_state
        from .core.candidate_test import require_production
    except ImportError:
        from core.artifacts import load_manifest, artifact_is_confirmed, project_path, digest as file_digest, validate_current_dependencies
        from core.project_state import load_project_state
        from core.candidate_test import require_production
    project = project.resolve()
    require_production(project)
    manifest, state = load_manifest(project), load_project_state(project)
    rows_by_id(frozen['drafts'])
    if not isinstance(frozen.get('revision'), str) or not frozen['revision'].strip():
        raise ValueError('submission revision required')
    def authority(ref):
        found = [a for a in manifest['artifacts'] if a.get('artifact_id') == ref.get('artifact_id')]
        if len(found) != 1:
            raise ValueError('missing/ambiguous exact reference artifact')
        item = found[0]
        if (item['artifact_id'] not in state['current_artifacts']
                or not artifact_is_confirmed(item, state, project)
                or not item.get('revision') or item['revision'] != ref.get('revision')):
            raise ValueError('reference artifact is unconfirmed, stale or another revision')
        path = project_path(project, item['path_or_url'])
        if (not path.is_file() or path.stat().st_size == 0 or not item.get('checksum')
                or item['checksum'] != ref.get('sha256') or file_digest(path) != ref['sha256']):
            raise ValueError('reference file/hash differs from current confirmed artifact')
        validate_current_dependencies(item, manifest, state, project)
        return path
    sources = frozen.get('source_artifacts')
    if not isinstance(sources, list) or not sources:
        raise ValueError('exact confirmed source_artifacts required')
    for ref in sources:
        authority(ref)
    def seconds(value):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
            raise ValueError('finite positive duration required')
        return value
    roles = {'identity', 'appearance', 'start_frame', 'end_frame', 'action', 'scale', 'layout', 'voice'}
    event_last = {}
    def same_boundary(tail, head, event, prior_id, current_id):
        if (tail["state_id"] != head["state_id"]
                or set(tail["entity_ids"]) != set(head["entity_ids"])):
            return False
        if tail["sha256"] == head["sha256"]:
            return True
        group_id = tail.get("reference_group_id")
        if not group_id or head.get("reference_group_id") != group_id:
            return False
        try:
            from .core.confirmation_scope import is_whole_artifact, classify, problems_for_write, KIND_SEGMENT
        except ImportError:
            from core.confirmation_scope import is_whole_artifact, classify, problems_for_write, KIND_SEGMENT
        def covers_usage(receipt):
            if is_whole_artifact(receipt):
                return True
            scope = classify(receipt)
            return (scope.kind == KIND_SEGMENT and not problems_for_write(receipt)
                    and {prior_id, current_id} <= set(scope.ids.get('segment_ids', [])))
        groups = [(receipt, group) for receipt in state["user_confirmations"]
                  if covers_usage(receipt) and receipt.get("approval_ref")
                  for group in receipt.get("reference_groups", []) if group.get("group_id") == group_id]
        if len(groups) != 1:
            return False
        receipt, group = groups[0]
        if (group.get("event_id") != event or group.get("state_id") != tail["state_id"]
                or set(group.get("entity_ids", [])) != set(tail["entity_ids"])):
            return False
        for ref, role, node in [(tail, "end_frame", prior_id), (head, "start_frame", current_id)]:
            if ref["artifact_id"] not in receipt.get("artifact_ids", []):
                return False
            members = [m for m in group.get("members", [])
                       if all(m.get(k) == ref[k] for k in ("artifact_id", "revision", "sha256"))
                       and role in m.get("roles", []) and node in m.get("draft_node_ids", [])]
            if len(members) != 1:
                return False
        return True
    for row in frozen['drafts']:
        if not isinstance(row.get('prompt'), str) or not row['prompt'].strip():
            raise ValueError('complete submitted prompt body required')
        if not isinstance(row.get('native_entity_type'), str) or not row['native_entity_type'].strip():
            raise ValueError('versioned delivery requires native entity readback for all references')
        duration = seconds(row['parameters']['duration'])
        low, high = map(seconds, row['duration_bounds'])
        if low > high or not low <= duration <= high:
            raise ValueError('duration outside explicitly selected platform bounds')
        parts = row.get('shot_durations')
        if not isinstance(parts, list) or not parts or not math.isclose(sum(map(seconds, parts)), duration, abs_tol=1e-6):
            raise ValueError('shot durations do not cover submitted duration exactly')
        media, refs = row.get('ordered_media'), row.get('reference_checks')
        if not isinstance(media, list) or not isinstance(refs, list) or len(refs) != len(media):
            raise ValueError('every uploaded media needs one explicit role/authority check in actual order')
        if any(not isinstance(m, dict) or not isinstance(m.get('node_id'), str) or not m['node_id']
               or not isinstance(m.get('media_id'), str) or not m['media_id'] for m in media):
            raise ValueError('every uploaded media needs exact native node/media identity')
        required = row.get('required_entities')
        if not isinstance(required, list) or any(not isinstance(e, str) or not e for e in required):
            raise ValueError('required_entities must explicitly name entities needing reference coverage')
        required_roles = row.get('required_reference_roles')
        if (not isinstance(required_roles, dict) or set(required_roles) != set(required)
                or any(not isinstance(v, list) or not v or not set(v).issubset(roles) for v in required_roles.values())):
            raise ValueError('required reference roles must cover exactly the required entities')
        covered, covered_roles, frames = set(), {}, {}
        for media_item, ref in zip(media, refs):
            path = authority(ref)
            item = next(a for a in manifest['artifacts'] if a['artifact_id'] == ref['artifact_id'])
            # Bind actual native identities to source bytes in the existing source
            # artifact registration, not a caller-provided parallel list of labels.
            bindings = [b for b in item.get('upload_bindings', [])
                        if b.get('node_id') == media_item['node_id']
                        and b.get('media_id') == media_item['media_id']
                        and b.get('target_url') == frozen.get('target_url')
                        and b.get('revision') == ref['revision'] and b.get('sha256') == ref['sha256']]
            if not bindings:
                raise ValueError('actual upload node/media has no exact current source mapping')
            reverse_hashes = {binding['sha256']
                for source in manifest['artifacts']
                if source.get('status') == 'current'
                and artifact_is_confirmed(source, state, project)
                for binding in source.get('upload_bindings', [])
                if binding.get('target_url') == frozen.get('target_url')
                and binding.get('node_id') == media_item['node_id']
                and binding.get('media_id') == media_item['media_id']
                and binding.get('revision') == source.get('revision')
                and binding.get('sha256') == source.get('checksum')}
            if reverse_hashes != {ref['sha256']}:
                raise ValueError('conflicting upload mapping: one native identity names different source bytes')

            assigned = ref.get('roles', [ref.get('role')])
            kind = ref.get('kind')
            if (not isinstance(assigned, list) or not assigned or not set(assigned).issubset(roles)
                    or kind not in {'image', 'video', 'audio'}):
                raise ValueError('unknown reference role/kind')
            extensions = {'image': {'.png','.jpg','.jpeg','.webp'}, 'video': {'.mp4','.mov','.mkv','.webm'}, 'audio': {'.wav','.mp3','.m4a'}}
            if path.suffix.lower() not in extensions[kind]:
                raise ValueError('reference kind differs from actual registered file type')
            if 'action' in assigned:
                if kind != 'video':
                    raise ValueError('action video cannot be replaced by identity/still reference')
                seconds(ref.get('duration'))
                import subprocess
                try:
                    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                            '-of', 'json', str(path)], check=True, capture_output=True, text=True, timeout=15)
                    actual_duration = seconds(float(json.loads(probe.stdout)['format']['duration']))
                except (subprocess.SubprocessError, OSError, KeyError, ValueError) as exc:
                    raise ValueError('cannot read actual action video duration') from exc
                if not math.isclose(actual_duration, ref['duration'], abs_tol=0.1):
                    raise ValueError('declared action duration differs from actual video bytes')
            if 'voice' in assigned:
                if kind != 'audio':
                    raise ValueError('voice role requires audio')
            if set(assigned) & {'start_frame', 'end_frame', 'scale', 'layout'} and kind != 'image':
                raise ValueError('frame/identity/scale/layout roles require image')
            entities = ref.get('entity_ids')
            if not isinstance(entities, list) or not entities or any(not isinstance(e,str) or not e for e in entities):
                raise ValueError('reference requires explicit actual entity_ids')
            if not set(entities).issubset(required):
                raise ValueError('reference is assigned to an undeclared/wrong entity')
            covered.update(entities)
            for entity in entities:
                covered_roles.setdefault(entity, set()).update(assigned)
            for role in set(assigned) & {'start_frame', 'end_frame'}:
                if role in frames or not isinstance(ref.get('state_id'), str) or not ref['state_id']:
                    raise ValueError('unique start/end frame state required')
                frames[role] = ref
        if any(not set(expected).issubset(covered_roles.get(e, set())) for e, expected in required_roles.items()):
            raise ValueError('required start/end/identity/action reference role is missing')
        if covered != set(required):
            raise ValueError('required entity has no actual reference')
        continuity = row.get('continuity')
        if not isinstance(continuity, dict) or not continuity.get('event_id'):
            raise ValueError('explicit event continuity required')
        event = continuity['event_id']
        prior = event_last.get(event)
        if prior:
            prior_row, prior_frames = prior
            if continuity.get('previous_node_id') != prior_row['node_id']:
                raise ValueError('event predecessor missing, forked, cyclic or out of order')
            tail, head = prior_frames.get('end_frame'), frames.get('start_frame')
            if not tail or not head:
                raise ValueError('continuous split requires actual confirmed end/start frames')
            if not same_boundary(tail, head, event, prior_row['node_id'], row['node_id']):
                raise ValueError('cross-segment boundary frame/state/entity/group usage mismatch')
        elif continuity.get('previous_node_id'):
            raise ValueError('event root predecessor is unknown, cyclic or belongs to another event')
        event_last[event] = row, frames
    return {'ok': True, 'revision': frozen['revision'], 'frozen_sha256': digest(frozen),
            'scope': 'current registered bytes, declared reference roles/entities, durations and continuous split boundaries',
            'limitations': 'Requires actual visual inspection of roles/states and current platform bounds; does not verify pixels, media duration metadata, remote provenance or generation authorization.'}


def compare(frozen: dict, observed: dict, project: Path | None = None) -> dict:
    """Report only selected full-field readback; metadata is not remote proof."""
    submission = validate_submission(frozen, project) if project is not None else None
    if 'revision' in frozen and project is None:
        raise ValueError('versioned delivery requires --project to revalidate current reference authority')
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
            'draft_count': len(expected), 'submission_checks': submission or 'not_checked',
            'revision': frozen.get('revision'), 'frozen_sha256': digest(frozen),
            'observed_sha256': digest(observed), 'differences': differences,
            'native_reference_checks': native_reference_checks,
            'limitations': 'Evidence collector must actually refresh and read native editor values. '
                           'Native entity checks only cover explicitly requested drafts. '
                           'This comparison cannot establish remote provenance, pixel quality, generation status or generation authorization.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    parser.add_argument('--readback', type=Path)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--preflight', action='store_true', help='只读核对现有交付 JSON，无远端操作')
    parser.add_argument('--delivery', action='store_true',
                        help='正式交付：强制当前项目与修订核验；不替代人工动作自查或生成授权')
    args = parser.parse_args()
    try:
        frozen = json.loads(args.frozen.read_text(encoding='utf-8'))
        if args.delivery:
            if args.project is None:
                raise ValueError('--delivery requires --project; legacy comparison is not delivery validation')
            if not isinstance(frozen.get('revision'), str) or not frozen['revision'].strip():
                raise ValueError('--delivery requires exact submission revision')
        if args.preflight:
            if args.project is None:
                parser.error('--preflight requires --project')
            result = validate_submission(frozen, args.project)
        else:
            if args.readback is None:
                parser.error('--readback required unless --preflight')
            result = compare(frozen, json.loads(args.readback.read_text(encoding='utf-8')), args.project)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
