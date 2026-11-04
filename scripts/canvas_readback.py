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

try:
    from .core.endpoint_control import verify_endpoint_control
    from .core.endpoint_states import validate_endpoint_strategy
    from .core.native_port_binding import verify_port_binding
    from .core.media_types import MEDIA_EXTENSIONS
    from .core.native_references import reference_readback_status, valid_native_media, endpoint_readback_status
except ImportError:
    from core.endpoint_control import verify_endpoint_control
    from core.endpoint_states import validate_endpoint_strategy
    from core.native_port_binding import verify_port_binding
    from core.media_types import MEDIA_EXTENSIONS
    from core.native_references import reference_readback_status, valid_native_media, endpoint_readback_status


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


def _port_expected(frozen, row, ref, media, slot):
    if ref.get('kind') != 'image' or row.get('native_entity_type') != 'image':
        raise ValueError('node_port_download supports observed image references only')
    if media['sha256'] != ref['sha256']:
        raise ValueError('native port media hash differs from exact source bytes')
    return dict(target_url=frozen['target_url'], target_project_id=frozen.get('target_project_id'),
                draft_revision=frozen['revision'], draft_node_id=row['node_id'],
                source_segment_id=row.get('source_segment_id'), slot=slot,
                node_id=media['node_id'], reference_port=media['reference_port'],
                sha256=ref['sha256'], revision=ref['revision'], phase='after_refresh')


def _endpoint_expected(frozen, row):
    params = row['parameters']
    aspects = [params[k] for k in ('aspect_ratio', 'aspect', 'ratio') if k in params]
    if not aspects or any(a != aspects[0] for a in aspects):
        raise ValueError('exact consistent endpoint aspect ratio required')
    return dict(target_url=frozen['target_url'], target_project_id=frozen.get('target_project_id'),
                draft_revision=frozen['revision'], draft_node_id=row['node_id'],
                source_segment_id=row.get('source_segment_id'), duration=params['duration'],
                model=params.get('model'), aspect_ratio=aspects[0], resolution=params.get('resolution'),
                ordered_media=row['ordered_media'])


def _endpoint_upload_expected(frozen, row, ref, media, slot):
    if ref.get('kind') != 'image' or media['sha256'] != ref['sha256'] or slot not in (1, 2):
        raise ValueError('endpoint needs exactly scoped image source bytes')
    control = row.get('state_control')
    if not isinstance(control, dict) or not isinstance(control.get('platform'), dict):
        raise ValueError('endpoint needs exact source strategy/platform evidence')
    return dict(identity_mode='first_last_frame_download', target_url=frozen['target_url'],
                target_project_id=frozen.get('target_project_id'), draft_revision=frozen['revision'],
                draft_node_id=row['node_id'], node_id=media['node_id'], reference_port=media['reference_port'],
                revision=ref['revision'], sha256=ref['sha256'], slot=slot,
                role=('start_frame', 'end_frame')[slot - 1], control_evidence_sha256=digest(control['platform']))


def preview_scope(frozen: dict, project: Path, *, json_projections=None, context_node_ids=None):
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
    scoped_rows = [row for row in frozen['drafts'] if context_node_ids is None or row['node_id'] in context_node_ids]
    for row in scoped_rows:
        if set(row) - {'node_id','prompt','parameters','duration_bounds','shot_durations',
                      'native_entity_type','native_identity_mode','state_control','required_entities','required_reference_roles',
                      'ordered_media','reference_checks','continuity','timing_scope','source_shot_ids','source_shots','source_segment_id','quality_evidence','native_binding'}:
            raise ValueError('unknown preview draft execution fields')
        if set(row['parameters']) - {'duration','duration_seconds','aspect','aspect_ratio','ratio','resolution','model','reference_mode','mode'}:
            raise ValueError('unknown preview parameters; generation controls are forbidden')
    roots = auth.get('source_projects')
    if (not isinstance(roots, list) or not roots or any(not nonempty(p) or str(Path(p).resolve()) != p for p in roots)
            or str(project) not in roots or len(set(roots)) != len(roots)):
        raise ValueError('exact canonical source project roots required')
    input_roots = auth.get('input_roots', [])
    if (not isinstance(input_roots, list) or any(not nonempty(p) or str(Path(p).resolve()) != p
            or Path(p).parent == Path(p) for p in input_roots)):
        raise ValueError('exact narrow dependency input roots required')
    all_refs = frozen.get('source_artifacts', []) + [ref for row in scoped_rows for ref in row['reference_checks']]
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
            report = discover_dependencies(root, files=[record], input_roots=[Path(p) for p in roots + input_roots],
                                           json_projections=json_projections)
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
    def usage(row, ref, media, slot):
        path, receipt = authority(ref)
        use = ref.get('limited_use', {})
        assigned = ref.get('roles', [ref.get('role')])
        if (use.get('conclusion') != 'usable_for_private_preview'
                or any(not nonempty(use.get(k)) for k in ('reviewer','review_ref','observation'))
                or row['node_id'] not in use.get('draft_node_ids', [])
                or not set(assigned) <= set(use.get('roles', []))
                or not set(ref.get('entity_ids', [])) <= set(use.get('entity_ids', []))
                or set(assigned) & set(use.get('excluded_roles', []))):
            raise ValueError('reference usage exceeds actual limited visual review scope')
        if receipt.get('outcome') == 'failed_candidate' or receipt.get('_has_later_failure'):
            raise ValueError('quality_evidence: failed pixels cannot be admitted by limited-use scope; preserve history, remove from new active references')
        if ref.get('kind') == 'image':
            try:
                from .core.quality_evidence import validate_image_evidence, reference_shot_ids, validate_registered_product_reference
            except ImportError:
                from core.quality_evidence import validate_image_evidence, reference_shot_ids, validate_registered_product_reference
            root = Path(ref['project_path']).resolve()
            item = checked[(str(root), ref['artifact_id'])]
            validate_registered_product_reference(root, ref, item,
                                                  allowed_roots=[Path(p) for p in roots + input_roots])
            validate_image_evidence(root, path, item.get('quality_evidence'), reference_shot_ids(ref, row),
                                    allowed_roots=[Path(p) for p in roots + input_roots], reference=ref)
        binding = ref.get('upload_binding', {})
        if row.get('native_identity_mode') == 'first_last_frame_download':
            if digest(binding) != digest(_endpoint_upload_expected(frozen, row, ref, media, slot)):
                raise ValueError('preview endpoint upload mapping differs from exact source/control evidence')
            hashes = {r.get('sha256') for r in all_refs
                      if r.get('upload_binding', {}).get('identity_mode') == 'first_last_frame_download'
                      and r['upload_binding'].get('node_id') == media['node_id']
                      and r['upload_binding'].get('reference_port') == media['reference_port']}
            if hashes != {ref['sha256']}:
                raise ValueError('conflicting preview endpoint upload mapping')
            return
        if row.get('native_identity_mode', 'media_id') == 'node_port_download':
            verify_port_binding(Path(ref['project_path']), binding,
                                _port_expected(frozen, row, ref, media, slot))
            hashes = {r.get('sha256') for r in all_refs
                      if r.get('upload_binding', {}).get('identity_mode') == 'node_port_download'
                      and r['upload_binding'].get('node_id') == media['node_id']
                      and r['upload_binding'].get('reference_port') == media['reference_port']}
            if hashes != {ref['sha256']}:
                raise ValueError('conflicting preview node/port upload mapping')
            return
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


def predecessor_contexts(frozen, project, manifest, private_preview):
    """Select only byte-bound prior nodes; never upload or recurse into history."""
    from copy import deepcopy
    try:
        from .core.artifacts import project_path, digest as file_digest
    except ImportError:
        from core.artifacts import project_path, digest as file_digest
    contexts, projections = {}, {}
    current_ids = set(rows_by_id(frozen['drafts']))
    for row in frozen['drafts']:
        continuity = row.get('continuity', {})
        evidence = continuity.get('previous_delivery')
        if evidence is None:
            continue
        if (not isinstance(evidence, dict) or set(evidence) != {'artifact_id', 'revision', 'sha256'}
                or not continuity.get('previous_node_id') or continuity['previous_node_id'] in current_ids):
            raise ValueError('partial delivery requires an exact external predecessor snapshot reference')
        refs = [ref for ref in frozen.get('source_artifacts', []) if
                all(ref.get(k) == evidence[k] for k in evidence)]
        entries = [item for item in manifest['artifacts'] if item.get('artifact_id') == evidence['artifact_id']]
        if len(refs) != 1 or len(entries) != 1 or Path(refs[0].get('project_path', project)).resolve() != project:
            raise ValueError('partial predecessor must be an exact same-project source_artifact')
        path = project_path(project, entries[0]['path_or_url']).resolve()
        if (not path.is_relative_to(project) or not path.is_file()
                or file_digest(path) != evidence['sha256'] or entries[0].get('checksum') != evidence['sha256']
                or entries[0].get('revision') != evidence['revision']):
            raise ValueError('partial predecessor snapshot is missing, stale or another revision')
        prior = json.loads(path.read_text(encoding='utf-8-sig'))
        if (target_url(prior.get('target_url', '')) != target_url(frozen['target_url'])
                or prior.get('target_project_id') != frozen.get('target_project_id')
                or ('preview_authorization' in prior) != private_preview
                or prior.get('revision') != evidence['revision']):
            raise ValueError('partial predecessor target/project or formal/preview mode differs')
        nodes = rows_by_id(prior.get('drafts'))
        node = nodes.get(continuity['previous_node_id'])
        if not node or node.get('continuity', {}).get('event_id') != continuity.get('event_id'):
            raise ValueError('partial predecessor node/event is not the recorded prior node')
        if node.get('continuity', {}).get('previous_node_id') in current_ids | {node['node_id']}:
            raise ValueError('partial predecessor contains a current-node or self cycle')
        if node['node_id'] in {c[1]['node_id'] for c in contexts.values()}:
            raise ValueError('partial predecessor cannot be reused as a fork')
        contexts[row['node_id']] = (prior, node, refs[0])
        # Dependency walking checks this exact selected row, not unrelated old
        # drafts or its own earlier predecessor chain. Original bytes stay intact.
        selected = deepcopy(node)
        selected.get('continuity', {}).pop('previous_delivery', None)
        projected = projections.setdefault(str(path), (evidence['sha256'], {'drafts': []}))[1]
        projected['drafts'].append(selected)
    # Inspect only already selected nodes. Unknown older predecessors are outside
    # this task, but cycles among the nodes we actually rely on cannot be hidden.
    selected_nodes = {row['node_id']: row for row in frozen['drafts']}
    selected_nodes.update({node['node_id']: node for _, node, _ in contexts.values()})
    checked = set()
    for ident in selected_nodes:
        visiting = set()
        cursor = ident
        while cursor in selected_nodes and cursor not in checked:
            if cursor in visiting:
                raise ValueError('partial predecessor contexts contain a cycle')
            visiting.add(cursor)
            cursor = selected_nodes[cursor].get('continuity', {}).get('previous_node_id')
        checked.update(visiting)
    return contexts, projections


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
    if not private_preview:
        require_production(project)
    # New submissions use current output policy. Legacy compare without a project
    # remains an evidence comparison and must not rewrite historical payloads.
    try:
        from .core.prompt_body import generated_overlay_issues
    except ImportError:
        from core.prompt_body import generated_overlay_issues
    host = (urlsplit(target_url(frozen['target_url'])).hostname or '').lower()
    xiaoyunque = host in {'xiaoyunque.com', 'www.xiaoyunque.com', 'xyq.jianying.com'}
    for row in rows_by_id(frozen['drafts']).values():
        if xiaoyunque and str(row.get('parameters', {}).get('resolution', '')).lower() != '480p':
            raise ValueError('小云雀新写入分辨率必须显式为 480p；不沿用 720p，不自动改时长或模型')
        if isinstance(row.get('prompt'), str):
            issues = generated_overlay_issues(row['prompt'])
            if issues:
                raise ValueError(f"{row['node_id']}: " + '; '.join(issues))
    try:
        from .core.quality_evidence import (validate_native_binding_claims, scene_state_requirements,
                                            validate_scene_state_references, reference_shot_ids, validate_scene_source_mapping)
    except ImportError:
        from core.quality_evidence import (validate_native_binding_claims, scene_state_requirements,
                                           validate_scene_state_references, reference_shot_ids, validate_scene_source_mapping)
    validate_native_binding_claims(frozen, project)
    manifest, state = load_manifest(project), load_project_state(project)
    contexts, projections = predecessor_contexts(frozen, project, manifest, private_preview)
    if private_preview:
        preview_authority, preview_usage, reports = preview_scope(frozen, project, json_projections=projections)
        dependencies = {'ok': True, 'candidate_reports': reports}
    else:
        if 'preview_authorization' in frozen:
            raise ValueError('preview scope cannot be used by formal delivery')
        dependencies = discover_dependencies(project, payload=frozen, json_projections=projections)
    if not dependencies['ok']:
        raise ValueError('Dependency readiness failed: ' + '; '.join(dependencies['errors'] + dependencies['uncheckable']))
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
    scene_requirements = []
    try:
        from .core.quality_evidence import (packaging_requirements, validate_packaging_scope,
                                            validate_registered_product_reference)
    except ImportError:
        from core.quality_evidence import (packaging_requirements, validate_packaging_scope,
                                           validate_registered_product_reference)
    package_requirements = []
    scene_sources = []
    for ref in sources:
        source_path = authority(ref)
        if source_path.suffix.lower() == '.json':
            source_payload = json.loads(source_path.read_text(encoding='utf-8-sig'))
            if isinstance(source_payload, dict):
                scene_requirements.extend(scene_state_requirements(source_payload))
                package_requirements.extend(packaging_requirements(source_payload))
                scene_sources.append((ref, source_path.resolve(), source_payload))
    external = validate_scene_source_mapping(frozen['drafts'], scene_sources, project) or {}
    if set(contexts) != set(external):
        raise ValueError('partial delivery requires exact prior-node evidence for every source continuation')
    validation_rows, context_scopes = [], {}
    for row in frozen['drafts']:
        if row['node_id'] in contexts:
            prior, node, ref = contexts[row['node_id']]
            link = external[row['node_id']]
            source = link['source']
            if (node.get('source_segment_id') != link['previous_segment_id']
                    or not any(all(old.get(k) == source.get(k) for k in ('artifact_id', 'revision', 'sha256'))
                               and Path(old.get('project_path', project)).resolve() == Path(source.get('project_path', project)).resolve()
                               for old in prior.get('source_artifacts', []))):
                raise ValueError('partial predecessor does not bind the exact current source revision and segment')
            authority(ref)  # Current manifest/confirmation or bounded candidate scope, not a stored PASS.
            validate_native_binding_claims(node, project, prior['revision'], prior['target_url'], prior.get('target_project_id'))
            context_scopes[node['node_id']] = prior
            validation_rows.append(node)
        validation_rows.append(row)
    if contexts:
        remaining = validate_scene_source_mapping(validation_rows, scene_sources, project) or {}
        if set(remaining) - set(context_scopes):
            raise ValueError('partial predecessor evidence leaves a current continuation unresolved')
    def seconds(value):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
            raise ValueError('finite positive duration required')
        return value
    roles = {'identity', 'appearance', 'start_frame', 'end_frame', 'action', 'scale', 'layout', 'voice', 'state_reference'}
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
    endpoint_control_checks = {}
    for row in validation_rows:
        row_frozen = context_scopes.get(row['node_id'], frozen)
        row_authority, row_preview_usage = authority, preview_usage if private_preview else None
        if private_preview and row['node_id'] in context_scopes:
            prior_authority, row_preview_usage, _ = preview_scope(row_frozen, project, json_projections=projections, context_node_ids={row["node_id"]})
            row_authority = lambda ref: prior_authority(ref)[0]
        endpoint_mode = row.get('native_identity_mode') == 'first_last_frame_download'
        if not endpoint_mode:
            if 'state_control' in row:
                raise ValueError('endpoint state_control requires its explicit verified identity mode')
            validate_scene_state_references(row, scene_requirements)
        if not private_preview:
            try:
                from .core.quality_evidence import validate_draft_evidence
            except ImportError:
                from core.quality_evidence import validate_draft_evidence
            validate_draft_evidence(row)
            validate_packaging_scope(row, package_requirements)
        if not isinstance(row.get('prompt'), str) or not row['prompt'].strip():
            raise ValueError('complete submitted prompt body required')
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
        if media and not endpoint_mode and (not isinstance(row.get('native_entity_type'), str) or not row['native_entity_type'].strip()):
            raise ValueError('versioned delivery requires native entity readback for all references')
        identity_mode = row.get('native_identity_mode', 'media_id')
        if not valid_native_media(media, identity_mode):
            raise ValueError('every uploaded media needs exact supported native identity')
        required = row.get('required_entities')
        if not isinstance(required, list) or any(not isinstance(e, str) or not e for e in required):
            raise ValueError('required_entities must explicitly name entities needing reference coverage')
        required_roles = row.get('required_reference_roles')
        if (not isinstance(required_roles, dict) or set(required_roles) != set(required)
                or any(not isinstance(v, list) or not v or not set(v).issubset(roles) for v in required_roles.values())):
            raise ValueError('required reference roles must cover exactly the required entities')
        covered, covered_roles, frames = set(), {}, {}
        for slot, (media_item, ref) in enumerate(zip(media, refs), 1):
            path = row_authority(ref)
            if private_preview:
                row_preview_usage(row, ref, media_item, slot)
            else:
                item = next(a for a in manifest['artifacts'] if a['artifact_id'] == ref['artifact_id'])
                validate_registered_product_reference(project, ref, item)
                # Bind actual native identities to source bytes in the existing source
                # artifact registration, not a caller-provided parallel list of labels.
                if endpoint_mode:
                    expected_binding = _endpoint_upload_expected(row_frozen, row, ref, media_item, slot)
                    if sum(digest(b) == digest(expected_binding) for b in item.get('upload_bindings', [])) != 1:
                        raise ValueError('endpoint has no unique exact manifest upload mapping')
                    reverse_hashes = {b.get('sha256') for source in manifest['artifacts']
                        if source.get('status') == 'current' and artifact_is_confirmed(source, state, project)
                        for b in source.get('upload_bindings', [])
                        if b.get('identity_mode') == 'first_last_frame_download'
                        and b.get('target_url') == frozen['target_url']
                        and b.get('target_project_id') == frozen.get('target_project_id')
                        and b.get('node_id') == media_item['node_id']
                        and b.get('reference_port') == media_item['reference_port']
                        and b.get('revision') == source.get('revision')
                        and b.get('sha256') == source.get('checksum')}
                    if reverse_hashes != {ref['sha256']}:
                        raise ValueError('conflicting endpoint upload source bytes')
                elif identity_mode == 'node_port_download':
                    expected_binding = _port_expected(row_frozen, row, ref, media_item, slot)
                    bindings = [b for b in item.get('upload_bindings', [])
                                if b.get('identity_mode') == 'node_port_download'
                                and all(b.get(k) == v for k, v in expected_binding.items())]
                    if len(bindings) != 1:
                        raise ValueError('node/port has no unique exact registered upload binding')
                    verify_port_binding(project, bindings[0], expected_binding)
                    reverse_hashes = {b.get('sha256') for source in manifest['artifacts']
                        if source.get('status') == 'current' and artifact_is_confirmed(source, state, project)
                        for b in source.get('upload_bindings', [])
                        if b.get('identity_mode') == 'node_port_download'
                        and b.get('target_url') == frozen['target_url']
                        and b.get('target_project_id') == frozen.get('target_project_id')
                        and b.get('node_id') == media_item['node_id']
                        and b.get('reference_port') == media_item['reference_port']
                        and b.get('revision') == source.get('revision')
                        and b.get('sha256') == source.get('checksum')}
                    if reverse_hashes != {ref['sha256']}:
                        raise ValueError('conflicting upload node/port source bytes')
                else:
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
            if path.suffix.lower() not in MEDIA_EXTENSIONS[kind]:
                raise ValueError('reference kind differs from actual registered file type')
            if kind == 'image' and not private_preview:
                try:
                    from .core.quality_evidence import validate_image_evidence, reference_shot_ids
                except ImportError:
                    from core.quality_evidence import validate_image_evidence, reference_shot_ids
                validate_image_evidence(project, path, item.get('quality_evidence'), reference_shot_ids(ref, row), reference=ref)
            if 'action' in assigned and kind != 'video':
                raise ValueError('action video cannot be replaced by identity/still reference')
            if 'action' in assigned or (xiaoyunque and kind == 'video'):
                seconds(ref.get('duration'))
                import subprocess
                try:
                    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                            '-of', 'json', str(path)], check=True, capture_output=True, text=True, timeout=15)
                    actual_duration = seconds(float(json.loads(probe.stdout)['format']['duration']))
                except (subprocess.SubprocessError, OSError, KeyError, ValueError) as exc:
                    raise ValueError('cannot read actual action video duration') from exc
                if xiaoyunque and actual_duration < 2:
                    raise ValueError('小云雀上传参考视频的实际文件必须至少2秒；不限制段内切镜时长')
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
            for entity in entities:
                eligible = set(assigned)
                if endpoint_mode:
                    facts = ref.get('endpoint_observations', {}).get('facts', [])
                    visibility = {fact.get('visibility') for fact in facts
                                  if isinstance(fact, dict) and fact.get('entity_id') == entity}
                    if 'visible' not in visibility:
                        eligible &= ({'start_frame', 'end_frame', 'state_reference'}
                                     if 'absent_from_visible_frame' in visibility else set())
                if eligible:
                    covered.add(entity)
                    covered_roles.setdefault(entity, set()).update(eligible)
            for role in set(assigned) & {'start_frame', 'end_frame'}:
                if role in frames or not isinstance(ref.get('state_id'), str) or not ref['state_id']:
                    raise ValueError('unique start/end frame state required')
                frames[role] = ref
        if endpoint_mode:
            control = row.get('state_control')
            if not isinstance(control, dict) or 'platform' not in control:
                raise ValueError('endpoint mode requires actual platform evidence and source strategy')
            proof = verify_endpoint_control(project, control['platform'], _endpoint_expected(row_frozen, row))
            endpoint_control_checks[row['node_id']] = {
                **proof, 'source_strategy': validate_endpoint_strategy(row, scene_sources, proof)}
        if any(not set(expected).issubset(covered_roles.get(e, set())) for e, expected in required_roles.items()):
            raise ValueError('required start/end/identity/action reference role is missing')
        if covered != set(required):
            raise ValueError('required entity has no actual reference')
        continuity = row.get('continuity')
        if not isinstance(continuity, dict) or not continuity.get('event_id'):
            raise ValueError('explicit event continuity required')
        event = continuity['event_id']
        if row['node_id'] in context_scopes:
            # Only its outgoing boundary is in this task. Preserve but do not
            # recursively revalidate unrelated incoming history.
            event_last[event] = row, frames
            continue
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
            'endpoint_control_checks': endpoint_control_checks,
            'predecessor_context_nodes': list(context_scopes),
            'quality_evidence_checks': 'image_evidence_only_not_draft_release' if private_preview else 'evidence_consistency_only',
            'packaging_checks': 'not_checked' if private_preview else 'declared_scope_and_evidence_consistency_only',
            'native_refresh_binding': 'not_checked',
            'generation_authorization': 'not_checked', 'production_ready': False,
            'visual_quality_verified': False, 'generation_input_provenance': 'unknown',
            'scope': 'registered bytes, declared reference roles/entities, durations and continuous split boundaries',
            'limitations': 'Declaration consistency only, not proof of user consent or a browser sandbox. Requires actual visual inspection of roles/states and current platform bounds; does not verify pixels, remote provenance or generation authorization; action-video and Xiaoyunque video durations are locally probed only.'}


def compare(frozen: dict, observed: dict, project: Path | None = None, *, private_preview: bool = False) -> dict:
    """Report only selected full-field readback; metadata is not remote proof."""
    if 'preview_authorization' in frozen and not private_preview:
        raise ValueError('preview snapshot requires explicit --private-preview; not formal delivery')
    if private_preview and project is None:
        raise ValueError('private preview requires --project')
    port_mode = any(row.get('native_identity_mode') in ('node_port_download', 'first_last_frame_download') for row in frozen['drafts'])
    if port_mode and project is None:
        raise ValueError('download identity modes require --project and original slot evidence')
    submission = validate_submission(frozen, project, private_preview=private_preview) if project is not None else None
    if port_mode and observed.get('target_project_id') != frozen.get('target_project_id'):
        raise ValueError('readback project differs from native port target')
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
    endpoint_control_checks = {}
    if set(actual) != set(expected):
        differences.append({'field': 'node_ids', 'missing': sorted(set(expected)-set(actual)),
                            'extra': sorted(set(actual)-set(expected))})
    for node_id, row in expected.items():
        got = actual.get(node_id, {})
        identity_mode = row.get('native_identity_mode', 'media_id')
        if not isinstance(identity_mode, str) or identity_mode not in {'media_id', 'node_port_download', 'first_last_frame_download'}:
            raise ValueError('unsupported native_identity_mode')
        for field in ('prompt', 'parameters', 'ordered_media'):
            value = row.get(field)
            valid = (isinstance(value, str) if field == 'prompt' else
                     isinstance(value, dict) if field == 'parameters' else
                     valid_native_media(value, identity_mode))
            if field not in row or not valid:
                raise ValueError(f'incomplete frozen {node_id}: {field}')
            if field not in got or digest(got[field]) != digest(value):
                differences.append({'node_id': node_id, 'field': field,
                                    'expected_sha256': digest(value),
                                    'observed_sha256': digest(got[field]) if field in got else None})
        # Native rich-text entities are distinct evidence from plain prompt text
        # and media edges. Opt in per draft so historical/plain-text clients keep
        # their original comparison scope without claiming entity verification.
        if identity_mode == 'first_last_frame_download':
            status = endpoint_readback_status(row['ordered_media'], got.get('frame_slots'), got.get('native_mentions'))
            native_reference_checks[node_id] = 'not_applicable_endpoint_slots'
            endpoint_control_checks[node_id] = status
            if status != 'passed':
                differences.append({'node_id': node_id, 'field': 'frame_slots', 'status': 'failed'})
            continue
        native_reference_checks[node_id] = 'not_checked'
        # Current versioned text-only drafts still prove no native references
        # survived refresh. Legacy comparisons retain their original opt-in scope.
        if 'native_entity_type' in row or project is not None:
            entity_type = row.get('native_entity_type')
            if row['ordered_media'] and (not isinstance(entity_type, str) or not entity_type.strip()):
                raise ValueError(f'incomplete frozen {node_id}: native_entity_type')
            mentions = got.get('native_mentions')
            status = reference_readback_status(row['ordered_media'], mentions, entity_type, identity_mode)
            native_reference_checks[node_id] = status
            if status == 'failed':
                differences.append({'node_id': node_id, 'field': 'native_mentions',
                                    'expected_sha256': digest(row['ordered_media']),
                                    'observed_sha256': digest(mentions) if mentions is not None else None})
    return {'ok': not differences, 'scope': 'selected post-refresh editor fields only',
            'native_refresh_binding': 'selected_fields_match' if not differences else 'failed',
            'generation_authorization': 'not_checked', 'production_ready': False,
            'draft_count': len(expected), 'submission_checks': submission or 'not_checked',
            'revision': frozen.get('revision'), 'frozen_sha256': digest(frozen),
            'observed_sha256': digest(observed), 'differences': differences,
            'native_reference_checks': native_reference_checks,
            'endpoint_control_checks': endpoint_control_checks,
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
    if args.preflight and args.readback is not None:
        parser.error('--preflight only checks local inputs; use --readback without --preflight for actual binding comparison')
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
