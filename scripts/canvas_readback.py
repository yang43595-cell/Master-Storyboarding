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


def preview_scope(frozen: dict, project: Path):
    """Validate a caller's bounded declaration, NOT the authenticity of consent.

    The executor must establish permission and inspect pixels separately. This is
    a read-only gate, not a sandbox for browser/provider actions.
    """
    try:
        from .core import artifacts, candidate_test
        from .core.delivery_files import discover_dependencies
    except ImportError:
        from core import artifacts, candidate_test
        from core.delivery_files import discover_dependencies
    auth = frozen.get('preview_authorization')
    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())
    if not isinstance(auth, dict) or auth.get('mode') != 'private_candidate_preview':
        raise ValueError('explicit private preview authorization scope required')
    evidence = auth.get('evidence')
    if (not isinstance(evidence, list) or not evidence or any(not isinstance(e, dict)
            or not nonempty(e.get('message_id')) or not nonempty(e.get('quote')) for e in evidence)):
        raise ValueError('preview requires explicit authorization evidence references and quotes')
    if (auth.get('project_path') != str(project) or auth.get('visibility') != 'private'
            or target_url(auth.get('target_url', '')) != target_url(frozen['target_url'])
            or not nonempty(frozen.get('target_project_id'))
            or auth.get('target_project_id') != frozen['target_project_id']):
        raise ValueError('preview target/project/private scope mismatch')
    flags = dict(status='candidate', review_status='unreviewed', production_blocked=True)
    if any(frozen.get(k) != v for k, v in flags.items()) or frozen.get('production_blocked') is not True:
        raise ValueError('preview must retain candidate/unreviewed/production_blocked')
    if (auth.get('drafts_sha256') != digest(frozen['drafts'])
            or auth.get('sources_sha256') != digest(frozen.get('source_artifacts'))):
        raise ValueError('preview draft/source scope hash mismatch')
    allowed = {'upload_references', 'save_drafts', 'remove_unreferenced_candidates'}
    operations = frozen.get('operations')
    if (not isinstance(operations, list) or not operations or len(set(operations)) != len(operations)
            or not set(operations) <= allowed or auth.get('allowed_operations') != operations
            or 'save_drafts' not in operations):
        raise ValueError('preview allows only explicitly scoped reference uploads, draft saves and cleanup; no generation')
    # Unknown execution controls are not silently accepted. Prompt prose remains
    # content to save, never instructions this validator executes.
    permitted = {'target_url','target_project_id','revision','source_artifacts','drafts',
                 'preview_authorization','operations','cleanup', *flags}
    if set(frozen) - permitted:
        raise ValueError('unknown preview execution fields')
    for row in frozen['drafts']:
        if set(row) - {'node_id','prompt','parameters','duration_bounds','shot_durations',
                      'native_entity_type','required_entities','required_reference_roles',
                      'ordered_media','reference_checks','continuity','timing_scope'}:
            raise ValueError('unknown preview draft execution fields')
        if set(row['parameters']) - {'duration','aspect','aspect_ratio','ratio','resolution','model'}:
            raise ValueError('unknown preview parameters; generation controls are forbidden')
    roots = auth.get('source_projects')
    if (not isinstance(roots, list) or not roots or any(not nonempty(p) or str(Path(p).resolve()) != p for p in roots)
            or str(project) not in roots or len(set(roots)) != len(roots)):
        raise ValueError('exact canonical source project roots required')
    input_roots = auth.get('input_roots', [])
    if (not isinstance(input_roots, list) or any(not nonempty(p) or str(Path(p).resolve()) != p
            or Path(p).parent == Path(p) for p in input_roots)):
        raise ValueError('exact narrow dependency input roots required')
    all_refs = frozen.get('source_artifacts', []) + [ref for row in frozen['drafts'] for ref in row['reference_checks']]
    checked, reports = {}, []
    def authority(ref):
        root = Path(ref['project_path']).resolve()
        if str(root) not in roots or ref['project_path'] != str(root):
            raise ValueError('reference belongs to an unscoped source project')
        context = candidate_test.context(root)
        entries = artifacts.load_manifest(root)['artifacts']
        found = [a for a in entries if a.get('artifact_id') == ref.get('artifact_id')]
        if not context or len(found) != 1:
            raise ValueError('exact registered candidate source required')
        item = found[0]
        if (any(item.get(k) != v for k, v in flags.items()) or item.get('user_approved', False) is not False
                or item.get('production_blocked') is not True or item.get('candidate_test') != context['run_id'] or not item.get('revision')
                or item['revision'] != ref.get('revision')):
            raise ValueError('preview reference is not the exact blocked, unreviewed candidate revision')
        record = artifacts.project_path(root, item['path_or_url']).resolve()
        display_source = item.get('type') == 'preview-reference'
        allowed_record = record.is_relative_to(root) or (display_source and any(record.is_relative_to(Path(p)) for p in input_roots))
        if (not allowed_record or not record.is_file() or not record.stat().st_size
                or not ref.get('record_sha256')
                or (item.get('checksum') and item['checksum'] != ref['record_sha256'])
                or artifacts.digest(record) != ref['record_sha256']):
            raise ValueError('candidate registration file/hash mismatch')
        key = (str(root), item['artifact_id'])
        if display_source:
            # A display-use registration preserves historical evidence without
            # replaying an old generator against today's methods/absolute paths.
            # These records are NEVER eligible for formal production/adoption.
            origin = item.get('preview_origin', {})
            evidence = origin.get('historical_evidence', [])
            if (origin.get('purpose') != 'private_preview_only_not_generation_baseline'
                    or origin.get('source_sha256') != ref.get('sha256')
                    or not isinstance(origin.get('history_gaps'), list)
                    or not isinstance(evidence, list) or not evidence):
                raise ValueError('display reference requires explicit preserved provenance and historical gaps')
            payloads, snapshots = [], {}
            for entry in evidence:
                path = Path(entry['location']).resolve()
                if (not any(path.is_relative_to(Path(p)) for p in roots + input_roots)
                        or not path.is_file() or artifacts.digest(path) != entry.get('sha256')):
                    raise ValueError('display provenance evidence file/hash mismatch')
                snapshots[str(path)] = entry['sha256']
                payloads.append((path, json.loads(path.read_text(encoding='utf-8'))))
            def binds(value):
                if isinstance(value, dict):
                    return value.get('sha256') == ref['sha256'] or any(binds(v) for v in value.values())
                return isinstance(value, list) and any(binds(v) for v in value)
            if not any(binds(value) for _, value in payloads):
                raise ValueError('historical evidence does not bind these exact displayed bytes')
            failed = False
            for path, value in payloads:
                if value.get('image', {}).get('sha256') == ref['sha256']:
                    failed |= value.get('outcome') == 'failed_candidate' or value.get('assessment', {}).get('passed') is False
                    if path.name == 'result.json':
                        required = [path.parent/'request.json', *sorted((path.parent/'review-overrides').glob('*.json'))]
                        if any(str(p.resolve()) not in snapshots for p in required):
                            raise ValueError('display provenance omits original request or later failure reviews')
                if value.get('image_sha256') == ref['sha256'] and value.get('passed') is False:
                    failed = True
            review = ref.get('limited_use', {}).get('review_evidence')
            if not isinstance(review, dict) or snapshots.get(review.get('location')) != review.get('sha256'):
                raise ValueError('display use requires exact preserved root review evidence')
            if key not in checked:
                reports.append({'ok': True, 'artifact_id': item['artifact_id'],
                    'scope': 'exact display bytes and preserved provenance only',
                    'historical_generation_revalidated': False, 'history_gaps': origin['history_gaps']})
                checked[key] = item
            if str(record) != ref.get('path') or artifacts.digest(record) != ref.get('sha256'):
                raise ValueError('actual display candidate file/hash mismatch')
            return record, {'_has_later_failure': failed}
        if key not in checked:
            report = discover_dependencies(root, files=[record], input_roots=[Path(p) for p in roots + input_roots])
            if not report['ok']:
                raise ValueError('Candidate dependency readiness failed: ' + '; '.join(report['errors'] + report['uncheckable']))
            reports.append(report)
            checked[key] = item
            # Keep existing exact revision/dependency checks for ordinary candidate
            # sources. Trial receipts have their own frozen source-byte checks.
            if item.get('type') == 'visual-asset-trial':
                try:
                    from .asset_generation_trial import root_path
                except ImportError:
                    from asset_generation_trial import root_path
                root_path(root)
            else:
                candidate_test.resolve_inputs(root, [item['artifact_id']])
        path, receipt = record, {}
        if item.get('type') == 'visual-asset-trial':
            receipt = json.loads(record.read_text(encoding='utf-8'))
            if (any(receipt.get(k) != v for k, v in flags.items())
                    or receipt.get('production_blocked') is not True or receipt.get('user_approved', False) is not False
                    or receipt.get('outcome') not in {'qualified_candidate','failed_candidate'}):
                raise ValueError('trial receipt does not identify an actual candidate image')
            try:
                from .asset_generation_trial import verify
            except ImportError:
                from asset_generation_trial import verify
            provenance = [record.parent / 'request.json', root / 'asset_generation_trial.json']
            actual_provenance = [{'path': str(p.resolve()), 'sha256': artifacts.digest(p)} for p in provenance]
            if ref.get('trial_provenance') != actual_provenance:
                raise ValueError('exact trial request/scope provenance missing or stale')
            request = json.loads(provenance[0].read_text(encoding='utf-8'))
            if (request.get('prompt_sha256') != receipt.get('prompt_sha256')
                    or hashlib.sha256(request['prompt'].encode()).hexdigest() != receipt['prompt_sha256']):
                raise ValueError('trial request prompt hash differs from result')
            verify(request['references_before']); verify(request['methods'])
            verify(request.get('reference_amendments', [])); verify(receipt['references_after'])
            verify([receipt['source_image']])
            for edge in (request.get('dependency') or {}).get('edges', []):
                verify([edge['approval'], edge['parent_request'], edge['parent_result']])
            overrides = sorted((record.parent / 'review-overrides').glob('*.json'))
            actual_reviews = [{'path': str(p.resolve()), 'sha256': artifacts.digest(p)} for p in overrides]
            if ref.get('review_evidence', []) != actual_reviews:
                raise ValueError('trial later review evidence missing or stale')
            for review_path in overrides:
                review = json.loads(review_path.read_text(encoding='utf-8'))
                if (review.get('image_sha256') != receipt['image']['sha256']
                        or review.get('passed') is not False or not nonempty(review.get('notes'))):
                    raise ValueError('invalid later visual review; reference blocked')
            receipt = {**receipt, '_has_later_failure': bool(overrides)}
            path = Path(receipt['image']['path']).resolve()
            if not path.is_relative_to(root) or receipt['image']['sha256'] != ref.get('sha256'):
                raise ValueError('trial image/hash mismatch')
        if (not path.is_file() or not path.stat().st_size or str(path) != ref.get('path')
                or artifacts.digest(path) != ref.get('sha256')):
            raise ValueError('actual candidate file/hash mismatch')
        return path, receipt
    def usage(row, ref, media):
        _, receipt = authority(ref)
        use = ref.get('limited_use', {})
        assigned = ref.get('roles', [ref.get('role')])
        if (use.get('conclusion') != 'usable_for_private_preview'
                or any(not nonempty(use.get(k)) for k in ('reviewer','review_ref','observation'))
                or row['node_id'] not in use.get('draft_node_ids', [])
                or not set(assigned) <= set(use.get('roles', []))
                or not set(ref.get('entity_ids', [])) <= set(use.get('entity_ids', []))
                or set(assigned) & set(use.get('excluded_roles', []))):
            raise ValueError('reference usage exceeds actual limited visual review scope')
        if (receipt.get('outcome') == 'failed_candidate' or receipt.get('_has_later_failure')) and (
                not use.get('excluded_roles') or not nonempty(use.get('failure_scope_note'))):
            raise ValueError('failed candidate requires explicit excluded failed-purpose scope; original failure remains')
        binding = ref.get('upload_binding', {})
        expected = dict(target_url=frozen['target_url'], target_project_id=frozen['target_project_id'],
                        node_id=media['node_id'], media_id=media['media_id'],
                        revision=ref['revision'], sha256=ref['sha256'])
        if binding != expected:
            raise ValueError('preview native upload mapping differs from exact candidate bytes/target')
        hashes = {r.get('sha256') for r in all_refs if r.get('upload_binding', {}).get('node_id') == media['node_id']
                  and r.get('upload_binding', {}).get('media_id') == media['media_id']}
        if hashes != {ref['sha256']}:
            raise ValueError('conflicting preview native upload mapping')
    cleanup = frozen.get('cleanup', [])
    if not isinstance(cleanup, list) or ('remove_unreferenced_candidates' in operations) != bool(cleanup):
        raise ValueError('cleanup operation requires exact scoped cleanup targets')
    if auth.get('cleanup_sha256') != digest(cleanup):
        raise ValueError('cleanup scope hash mismatch')
    used = {m['node_id'] for row in frozen['drafts'] for m in row['ordered_media']} | set(rows_by_id(frozen['drafts']))
    for item in cleanup:
        if (not nonempty(item.get('node_id')) or item['node_id'] in used
                or item.get('target_url') != frozen['target_url'] or item.get('target_project_id') != frozen['target_project_id']
                or item.get('status') != 'candidate' or item.get('reference_count') != 0
                or item.get('reference_scan') != 'whole_canvas' or item.get('recoverable') is not True
                or type(item.get('reference_count')) is not int or item.get('phase') != 'after_refresh'
                or not nonempty(item.get('readback_ref'))
                or item.get('authorization_ref') not in {e['message_id'] for e in evidence}):
            raise ValueError('cleanup requires authorized same-canvas zero-reference old candidate readback')
    return authority, usage, reports


def validate_submission(frozen: dict, project: Path, *, private_preview: bool = False) -> dict:
    """Check the existing frozen delivery JSON, using current project authority.

    Roles/states are explicit production decisions, not inferred visual facts.
    No remote reads, generation, adoption or additional ledger.
    """
    import math
    try:
        from .core.artifacts import load_manifest, artifact_is_confirmed, project_path, digest as file_digest, validate_current_dependencies
        from .core.project_state import load_project_state
        from .core.candidate_test import require_production
        from .core.delivery_files import discover_dependencies
    except ImportError:
        from core.artifacts import load_manifest, artifact_is_confirmed, project_path, digest as file_digest, validate_current_dependencies
        from core.project_state import load_project_state
        from core.candidate_test import require_production
        from core.delivery_files import discover_dependencies
    project = project.resolve()
    if private_preview:
        preview_authority, preview_usage, reports = preview_scope(frozen, project)
        dependencies = {'ok': True, 'candidate_reports': reports}
    else:
        if 'preview_authorization' in frozen:
            raise ValueError('preview scope cannot be used by formal delivery')
        require_production(project)
        dependencies = discover_dependencies(project, payload=frozen)
    if not dependencies['ok']:
        raise ValueError('Dependency readiness failed: ' + '; '.join(dependencies['errors'] + dependencies['uncheckable']))
    manifest, state = load_manifest(project), load_project_state(project)
    rows_by_id(frozen['drafts'])
    if not isinstance(frozen.get('revision'), str) or not frozen['revision'].strip():
        raise ValueError('submission revision required')
    def authority(ref):
        if private_preview:
            return preview_authority(ref)[0]
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
    if private_preview:
        roles.add('state_reference')
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
        if 'timing_scope' in row and (not private_preview or row['timing_scope'] != 'segment_total_only'):
            raise ValueError('only private preview may explicitly declare segment-total timing without a shot schedule')
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
            if private_preview:
                preview_usage(row, ref, media_item)
            else:
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
            if set(assigned) & {'start_frame', 'end_frame', 'scale', 'layout', 'state_reference'} and kind != 'image':
                raise ValueError('frame/identity/scale/layout roles require image')
            if 'state_reference' in assigned and not ref.get('state_id'):
                raise ValueError('state reference requires explicit state_id; not a hard frame')
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
            if private_preview and continuity.get('mode') == 'state_reference':
                boundary = continuity.get('boundary', {})
                if (boundary.get('claim') != 'shared_state_only_not_hard_frames'
                        or not boundary.get('state_id') or not boundary.get('description')
                        or boundary.get('previous_node_id') != prior_row['node_id']
                        or boundary.get('current_node_id') != row['node_id']):
                    raise ValueError('explicit bounded state-reference continuity required')
                for side, draft in [('previous', prior_row), ('current', row)]:
                    expected_hashes = boundary.get(side + '_sha256s')
                    if not isinstance(expected_hashes, list) or not expected_hashes:
                        raise ValueError('state boundary requires actual references on both sides')
                    actual = {r['sha256'] for r in draft['reference_checks']
                              if 'state_reference' in r.get('roles', [r.get('role')])
                              and r.get('state_id') == boundary['state_id']}
                    if set(expected_hashes) != actual:
                        raise ValueError('state boundary does not bind exact scoped reference bytes')
                event_last[event] = row, frames
                continue
            tail, head = prior_frames.get('end_frame'), frames.get('start_frame')
            if not tail or not head:
                raise ValueError('continuous split requires actual confirmed end/start frames')
            if not same_boundary(tail, head, event, prior_row['node_id'], row['node_id']):
                raise ValueError('cross-segment boundary frame/state/entity/group usage mismatch')
        elif continuity.get('previous_node_id'):
            raise ValueError('event root predecessor is unknown, cyclic or belongs to another event')
        event_last[event] = row, frames
    return {'ok': True, 'mode': 'private_candidate_preview' if private_preview else 'formal_delivery',
            **({'status': 'candidate', 'review_status': 'unreviewed', 'production_blocked': True} if private_preview else {}),
            'revision': frozen['revision'], 'frozen_sha256': digest(frozen),
            'dependency_checks': dependencies,
            'scope': 'registered bytes, declared reference roles/entities, durations and continuous split boundaries',
            'limitations': 'Declaration consistency only, not proof of user consent or a browser sandbox. Requires actual visual inspection of roles/states and current platform bounds; does not verify pixels, media duration metadata, remote provenance or generation authorization.'}


def compare(frozen: dict, observed: dict, project: Path | None = None, *, private_preview: bool = False) -> dict:
    """Report only selected full-field readback; metadata is not remote proof."""
    if 'preview_authorization' in frozen and not private_preview:
        raise ValueError('preview snapshot requires explicit --private-preview; not formal delivery')
    if private_preview and project is None:
        raise ValueError('private preview requires --project')
    submission = validate_submission(frozen, project, private_preview=private_preview) if project is not None else None
    if private_preview and observed.get('target_project_id') != frozen['target_project_id']:
        raise ValueError('readback project differs from private preview target')
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
    if private_preview and frozen.get('cleanup'):
        expected_cleanup = [{'node_id': item['node_id'], 'absent': True} for item in frozen['cleanup']]
        if observed.get('cleanup_results') != expected_cleanup:
            differences.append({'field': 'cleanup_results', 'expected': expected_cleanup})
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
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--private-preview', action='store_true', help='仅显式授权的私有候选参考与未生成草稿；不授予权限或改变确认状态')
    modes.add_argument('--delivery', action='store_true',
                        help='正式交付：强制当前项目与修订核验；不替代人工动作自查或生成授权')
    parser.add_argument('--package', type=Path, action='append', default=[], help='交付文件包时必传实际成品路径，可重复；严格检查实际字节')
    parser.add_argument('--max-package-bytes', type=int, default=19_000_000, help='每包字节数必须严格小于此值')
    args = parser.parse_args()
    try:
        frozen = json.loads(args.frozen.read_text(encoding='utf-8'))
        if args.delivery or args.private_preview:
            if args.project is None:
                raise ValueError('selected delivery/preview mode requires --project; legacy comparison is insufficient')
            if not isinstance(frozen.get('revision'), str) or not frozen['revision'].strip():
                raise ValueError('selected delivery/preview mode requires exact submission revision')
        if args.preflight:
            if args.project is None:
                parser.error('--preflight requires --project')
            result = validate_submission(frozen, args.project, private_preview=args.private_preview)
        else:
            if args.readback is None:
                parser.error('--readback required unless --preflight')
            result = compare(frozen, json.loads(args.readback.read_text(encoding='utf-8')), args.project, private_preview=args.private_preview)
        if args.package:
            try:
                from .core.delivery_files import check_package_files
            except ImportError:
                from core.delivery_files import check_package_files
            result['packages'] = check_package_files(args.package, args.max_package_bytes)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
