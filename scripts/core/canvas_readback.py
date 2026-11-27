"""Narrow, read-only comparison of frozen draft payloads and editor readback.

This consumes evidence from a platform-specific reader; it does not invent a
remote backend or infer editor persistence from graph edges. No network/writes.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .endpoint_control import verify_endpoint_control
from .endpoint_states import validate_endpoint_strategy
from .io import canonical_json_bytes, sha256_json as digest
from .native_port_binding import verify_port_binding
from .media_types import MEDIA_EXTENSIONS
from .native_references import (reference_readback_status, valid_native_media, endpoint_readback_status,
                                     validate_redframe_reference_labels)
from .project_kickoff import load_kickoff


def target_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('exact HTTP(S) target URL required')
    return urlunsplit(parsed)


def _declared_project_id(snapshot: dict) -> str | None:
    ident = snapshot.get('target_project_id')
    if ident is not None and (not isinstance(ident, str) or not ident.strip()):
        raise ValueError('target_project_id must be a nonempty string when provided')
    return ident


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


def validate_canvas_inputs(row: dict, *, redframe: bool = False, before_upload: bool = False,
                           preflight: bool = False) -> None:
    """Check declared graph/slot uses in the existing draft, without UI access."""
    media, refs = row.get('ordered_media'), row.get('reference_checks')
    if not isinstance(refs, list) or (not before_upload and (
            not isinstance(media, list) or len(media) != len(refs))):
        raise ValueError('canvas inputs require one reference check per actual slot')
    face_slots = row.get('realistic_face_slots')
    visual_slots = {slot for slot, ref in enumerate(refs, 1) if ref.get('kind') in {'image', 'video'}}
    if redframe and ('realistic_face_slots' in row or (before_upload and visual_slots)):
        if (not isinstance(face_slots, list)
                or any(type(slot) is not int or slot not in visual_slots for slot in face_slots)
                or len(set(face_slots)) != len(face_slots)):
            raise ValueError('红帧上传前须实看视觉参考并列出写实人脸槽 realistic_face_slots；没有则填 []')
    else:
        face_slots = []
    expected_edges, expected_sources, uses = [], {}, set()
    for slot, ref in enumerate(refs, 1):
        binding = ref.get('canvas_binding')
        if (not isinstance(binding, dict) or binding.get('mode') not in {'edge', 'direct'}
                or not isinstance(binding.get('purpose'), str) or not binding['purpose'].strip()
                or type(binding.get('prompt_reference')) is not bool):
            raise ValueError(f'canvas slot {slot}: exact binding mode, purpose and prompt_reference required')
        use = (ref.get('sha256'), binding['purpose'].strip())
        if use in uses:
            raise ValueError(f'canvas slot {slot}: duplicate same-purpose media slot')
        uses.add(use)
        if not binding['prompt_reference'] and (not isinstance(binding.get('unmentioned_reason'), str)
                                                or not binding['unmentioned_reason'].strip()):
            raise ValueError(f'canvas slot {slot}: unmentioned input needs its actual purpose/reason')
        if slot in face_slots:
            if binding['mode'] != 'edge':
                raise ValueError(f'红帧第 {slot} 槽含写实人脸，上传前改用认证源卡')
            if ref.get('kind') == 'image' and (binding.get('source_port'), binding.get('target_port')) != ('image', 'in'):
                raise ValueError(f'红帧第 {slot} 槽含写实人脸，上传前改用认证源卡 image → in')
        if binding['mode'] == 'direct':
            if not redframe and (ref.get('product_reference') or 'action' in ref.get('roles', [ref.get('role')])):
                raise ValueError('canvas product/action reference requires an independent source node and edge')
            if 'source_port' in binding or 'target_port' in binding:
                raise ValueError('direct input cannot declare graph ports')
            continue
        if any(not isinstance(binding.get(key), str) or not binding[key].strip()
               for key in ('source_port', 'target_port')):
            raise ValueError(f'canvas slot {slot}: exact source and target ports required')
        if before_upload:
            if slot not in face_slots:
                raise ValueError('红帧未上传计划的连线槽须列入 realistic_face_slots')
            continue
        native = media[slot - 1]
        if native.get('reference_port', binding['source_port']) != binding['source_port']:
            raise ValueError('canvas source port differs from native reference port')
        if native.get('node_id') == row['node_id']:
            raise ValueError('canvas edge requires an independent source node')
        edge = dict(source_node_id=native['node_id'], source_port=binding['source_port'],
                    target_node_id=row['node_id'], target_port=binding['target_port'], slot=slot)
        expected_edges.append(edge)
        source = dict(node_id=native['node_id'], port=binding['source_port'], sha256=ref['sha256'])
        source_key = (source['node_id'], source['port'])
        if source_key in expected_sources and expected_sources[source_key] != source:
            raise ValueError('canvas source node/port names conflicting file bytes')
        expected_sources[source_key] = source
    if redframe and (before_upload or (preflight and media == [])):
        try:
            validate_redframe_reference_labels(row['prompt'], refs)
        except ValueError as exc:
            raise ValueError(f"{row['node_id']}: {exc}") from exc
    for field, expected in (('edges', expected_edges), ('source_nodes', list(expected_sources.values()))):
        actual = row.get(field)
        if not isinstance(actual, list) or sorted(map(canonical_json_bytes, actual)) != sorted(map(canonical_json_bytes, expected)):
            raise ValueError(f'canvas {field}: missing, extra or conflicting source/edge')
    from .quality_evidence import validate_shot_durations
    validate_shot_durations(row, row.get('parameters', {}))


def compare_baseline(baseline: dict, current: dict) -> dict:
    """Compare two collected whole-node readings before any scoped update."""
    if (baseline.get('phase') != 'after_refresh' or current.get('phase') != 'before_write'
            or target_url(baseline['target_url']) != target_url(current['target_url'])
            or baseline.get('target_project_id') != current.get('target_project_id')):
        raise ValueError('baseline requires exact target and after_refresh/before_write readings')
    expected, actual = rows_by_id(baseline['drafts']), rows_by_id(current['drafts'])
    for row in [*expected.values(), *actual.values()]:
        if not isinstance(row.get('edges'), list) or not isinstance(row.get('source_nodes'), list):
            raise ValueError('baseline requires collected edges and source_nodes lists')
        if (not isinstance(row.get('prompt'), str) or not row['prompt'].strip()
                or not isinstance(row.get('parameters'), dict) or not row['parameters']
                or not isinstance(row.get('ordered_media'), list)
                or not isinstance(row.get('native_mentions'), list)):
            raise ValueError('baseline requires complete collected node fields')
    changed = sorted(node for node in expected.keys() | actual.keys()
                     if canonical_json_bytes(expected.get(node)) != canonical_json_bytes(actual.get(node)))
    return dict(ok=not changed, changed_node_ids=changed, baseline_sha256=digest(baseline),
                current_sha256=digest(current), scope='collected whole nodes before write')


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
    from . import artifacts, candidate_test
    from .delivery_files import discover_dependencies
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
                      'ordered_media','reference_checks','continuity','timing_scope','source_shot_ids','source_shots','source_segment_id','quality_evidence','native_binding','edges','source_nodes'}:
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
                raise ValueError('visual-asset-trial 已归档移除')
            else:
                candidate_test.resolve_inputs(root, [item['artifact_id']])
        path, receipt = record, {}
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
            from .quality_evidence import validate_image_evidence, reference_shot_ids, validate_registered_product_reference
            root = Path(ref['project_path']).resolve()
            item = checked[(str(root), ref['artifact_id'])]
            validate_registered_product_reference(root, ref, item,
                                                  allowed_roots=[Path(p) for p in roots + input_roots])
            validate_image_evidence(root, path, item.get('quality_evidence'), reference_shot_ids(ref, row),
                                    allowed_roots=[Path(p) for p in roots + input_roots], reference=ref, artifact=item)
        binding = ref.get('upload_binding', {})
        if row.get('native_identity_mode') == 'first_last_frame_download':
            expected_binding = _endpoint_upload_expected(frozen, row, ref, media, slot)
            if canonical_json_bytes(binding) != canonical_json_bytes(expected_binding):
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
    from .artifacts import project_path, digest as file_digest
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


def prompt_scene_control(row, frozen, project, state, requirements, *, formal_delivery=False):
    from .prompt_scene_control import resolve_prompt_scene_control
    return resolve_prompt_scene_control(row, frozen, project, state, requirements,
                                        formal_delivery=formal_delivery)


def validate_submission(frozen: dict, project: Path, *, private_preview: bool = False,
                        formal_delivery: bool = False, preflight: bool = False, read_json_bytes=None) -> dict:
    """Check the existing frozen delivery JSON, using current project authority.

    Roles/states are explicit production decisions, not inferred visual facts.
    No remote reads, generation, adoption or additional ledger.
    """
    if isinstance(frozen, dict) and 'generation_segments' in frozen and 'drafts' not in frozen:
        raise ValueError('画布预检需要含 target_url/drafts 的交付冻结快照，不能直接传入 video.prompt 源稿')
    import math
    from .artifacts import (
        load_manifest, artifact_is_adopted, project_path, cache_file_digests, cache_file_reads,
        digest as artifact_digest, validate_current_dependencies,
    )
    from .project_state import load_project_state
    from .candidate_test import require_production
    from .delivery_files import discover_dependencies
    project = project.resolve()
    if not private_preview:
        require_production(project)
    platform_project_id = _declared_project_id(frozen)
    kickoff = None if private_preview else load_kickoff(project)
    canvas = kickoff.get("canvas") if kickoff else None
    if formal_delivery and kickoff and canvas is None:
        raise ValueError("正式画布交付须先登记目标画布")
    if canvas and (not canvas.get("target_url")
                   or target_url(canvas["target_url"]) != target_url(frozen["target_url"])
                   or canvas.get("target_project_id") != platform_project_id):
        raise ValueError("交付目标与开工记录不符；新建画布须先登记其实际地址和项目 ID")
    # New submissions use current output policy. Legacy compare without a project
    # remains an evidence comparison and must not rewrite historical payloads.
    from .prompt_body import generated_overlay_issues, submitted_body_issues
    host = (urlsplit(target_url(frozen['target_url'])).hostname or '').lower()
    xiaoyunque = host in {'xiaoyunque.com', 'www.xiaoyunque.com', 'xyq.jianying.com'}
    redframe = host == 'redframe-ai.xmpaohong.com' or bool(canvas and canvas.get('platform') == 'redframe')
    formal_redframe = formal_delivery and redframe
    if formal_redframe and (not canvas or canvas['platform'] != 'redframe' or not platform_project_id):
        raise ValueError('正式红帧写入须登记准确画布和项目 ID')
    drafts = rows_by_id(frozen['drafts'])
    planned_ids = {ident for ident, row in drafts.items() if 'ordered_media' not in row}
    if planned_ids and formal_redframe and not preflight:
        raise ValueError('红帧正式回读须提供实际 ordered_media；写前计划不能用于交付')
    for row in drafts.values():
        if canvas:
            params = row.get("parameters", {})
            aspects = [params[key] for key in ("aspect_ratio", "aspect", "ratio") if key in params]
            if (params.get("model") != canvas["model"]
                    or str(params.get("resolution", "")).lower() != canvas["resolution"].lower()
                    or not aspects or any(aspect != canvas["aspect_ratio"] for aspect in aspects)):
                raise ValueError(f"草稿 {row['node_id']} 的模型、画幅或分辨率与开工决定不符")
        if xiaoyunque and str(row.get('parameters', {}).get('resolution', '')).lower() != '480p':
            raise ValueError('小云雀新写入分辨率必须显式为 480p；不沿用 720p，不自动改时长或模型')
        if isinstance(row.get('prompt'), str):
            issues = submitted_body_issues(row['prompt']) if formal_redframe else generated_overlay_issues(row['prompt'])
            if issues:
                raise ValueError(f"{row['node_id']}: " + '; '.join(issues))
    from .quality_evidence import (MAX_GENERATION_SEGMENT_SECONDS, validate_native_binding_claims, scene_state_requirements,
                                        validate_scene_state_references, reference_shot_ids, source_shot_ids, validate_scene_source_mapping,
                                        validate_shot_durations)
    validate_native_binding_claims(frozen, project)
    manifest, state = load_manifest(project), load_project_state(project)
    contexts, projections = predecessor_contexts(frozen, project, manifest, private_preview)
    if private_preview:
        preview_authority, preview_usage, reports = preview_scope(frozen, project, json_projections=projections)
        dependencies = {'ok': True, 'candidate_reports': reports}
    else:
        if 'preview_authorization' in frozen:
            raise ValueError('preview scope cannot be used by formal delivery')
    if not isinstance(frozen.get('revision'), str) or not frozen['revision'].strip():
        raise ValueError('submission revision required')
    # This cache is local to one validation phase. The refreshed compare creates a new one.
    read_json_bytes = cache_file_reads(Path.read_bytes) if read_json_bytes is None else read_json_bytes

    def read_evidence(path):
        return read_json_bytes(path) if path.suffix.lower() == '.json' else path.read_bytes()

    def hash_file(path):
        if path.suffix.lower() == '.json':
            return hashlib.sha256(read_json_bytes(path)).hexdigest()
        return artifact_digest(path)

    file_digest = cache_file_digests(hash_file, check_for_changes=True)

    def read_payload(path):
        return json.loads(read_json_bytes(path).decode('utf-8'))

    def authority(ref):
        if private_preview:
            return preview_authority(ref)[0]
        found = [a for a in manifest['artifacts'] if a.get('artifact_id') == ref.get('artifact_id')]
        if len(found) != 1:
            raise ValueError('missing/ambiguous exact reference artifact')
        item = found[0]
        if (item['artifact_id'] not in state['current_artifacts']
                or not artifact_is_adopted(item, state, project)
                or not item.get('revision') or item['revision'] != ref.get('revision')):
            raise ValueError(f"reference artifact is unconfirmed, stale or another revision: {item['artifact_id']}")
        path = project_path(project, item['path_or_url'])
        if (not path.is_file() or path.stat().st_size == 0 or not item.get('checksum')
                or item['checksum'] != ref.get('sha256') or file_digest(path) != ref['sha256']):
            raise ValueError('reference file/hash differs from current confirmed artifact')
        validate_current_dependencies(item, manifest, state, project, file_digest=file_digest, read_payload=read_payload)
        return path
    agent_reports = {}
    def agent_report(item):
        if item.get('agent_acceptance') is None:
            return None
        ident = item['artifact_id']
        if ident not in agent_reports:
            from .agent_acceptance import validate_agent_acceptance
            agent_reports[ident] = validate_agent_acceptance(project, item, state=state, file_digest=file_digest)
        return agent_reports[ident]
    sources = frozen.get('source_artifacts')
    if not isinstance(sources, list) or not sources:
        raise ValueError('exact confirmed source_artifacts required')
    scene_requirements = []
    from .quality_evidence import (packaging_requirements, validate_registered_product_reference,
                                        validate_draft_evidence)
    package_requirements = []
    scene_sources = []
    for ref in sources:
        source_path = authority(ref)
        if source_path.suffix.lower() == '.json':
            raw_source = read_json_bytes(source_path)
            if not private_preview and hashlib.sha256(raw_source).hexdigest() != ref['sha256']:
                raise ValueError('reference file/hash differs from current confirmed artifact')
            source_payload = json.loads(raw_source.decode('utf-8-sig'))
            if isinstance(source_payload, dict):
                scene_requirements.extend(scene_state_requirements(source_payload))
                package_requirements.extend(packaging_requirements(source_payload))
                scene_sources.append((ref, source_path.resolve(), source_payload))
    if not private_preview:
        from .delivery_files import declared_input_roots
        # Only exact, currently adopted source bytes can extend input scope.
        # Untrusted roots placed on the frozen payload are not authority.
        input_roots = list(dict.fromkeys(
            path for _, _, source_payload in scene_sources
            for path in declared_input_roots(source_payload)))
        dependencies = discover_dependencies(
            project, input_roots=input_roots, payload=frozen,
            json_projections=projections, read_json_bytes=read_json_bytes)
    if not dependencies['ok']:
        raise ValueError('Dependency readiness failed: ' + '; '.join(dependencies['errors'] + dependencies['uncheckable']))
    entries = {item['artifact_id']: item for item in manifest['artifacts']}
    depth_materials_by_segment = {}
    reuse_source_review = False
    product_pack = None
    product_requirements = []
    if formal_redframe:
        from .prompt_body import source_review_segments, submitted_prompt_body
        from .product_reference_pack import resolve_product_pack, resolve_pack_media
        videos = [payload for ref, _, payload in scene_sources
                  if entries[ref['artifact_id']].get('logical_id') == 'video.prompt']
        boards = [payload for ref, _, payload in scene_sources
                  if entries[ref['artifact_id']].get('logical_id') == 'storyboard.package']
        if (len(videos) != 1 or not videos[0].get('source_storyboard') or len(boards) != 1
                or not boards[0].get('source_narrative')):
            raise ValueError('正式红帧写入须绑定已采用的视频稿、导演稿和已确认的来源编导稿')
        reuse_source_review = any(entries[ref['artifact_id']].get('agent_acceptance')
                                  for ref, _, payload in scene_sources if payload is videos[0])
        if any('product_reference_id' in ref for row in drafts.values() for ref in row.get('reference_checks', [])
               if isinstance(ref, dict)):
            product_pack = resolve_product_pack(kickoff['product'])
            narrative_id = boards[0]['source_narrative'].get('artifact_id')
            product_requirements = [asset for ref, _, payload in scene_sources if ref['artifact_id'] == narrative_id
                                    for asset in payload.get('asset_requirements', [])]
        source_segments = {row['segment_id']: row for row in source_review_segments(videos[0])}
        if kickoff.get('creation', {}).get('workflow') == 'depth-guided-recreation':
            depth_materials_by_segment = {
                ident: source.get('materials', source.get('candidate_upload_order', []))
                for ident, source in source_segments.items() if source.get('route') == 'depth'
            }
        for row in drafts.values():
            source = source_segments.get(row.get('source_segment_id'))
            if not source or not submitted_prompt_body(source):
                raise ValueError(f"{row['node_id']}: 正式红帧写入须绑定已采用视频稿的准确段落及正文")
            planned_materials = source.get('materials', source.get('candidate_upload_order', []))
            inputs = row.get('reference_checks')
            if any(not isinstance(refs, list) or any(not isinstance(ref, dict) for ref in refs)
                   for refs in (planned_materials, inputs)):
                raise ValueError(f"{row['node_id']}: 正式红帧写入须提供明确的源素材和引用列表")
            if ([ref.get('sha256') for ref in planned_materials] != [ref.get('sha256') for ref in inputs]
                    or any(expected.get(key) is not None and expected[key] != actual.get(key)
                           for expected, actual in zip(planned_materials, inputs)
                           for key in ('asset_id', 'output_id', 'artifact_id', 'product_reference_id', 'kind', 'role'))):
                raise ValueError(f"{row['node_id']}: 正式红帧写入素材须逐槽匹配采用源段的顺序、身份和用途")
    external = validate_scene_source_mapping(
        frozen['drafts'], scene_sources, project, check_submitted_body=not private_preview,
        delivery_platform='redframe' if formal_redframe else None, read_evidence=read_evidence,
    ) or {}
    if not private_preview:
        from .production_handoff import require_handoff_visual_inputs
        submitted_shots = {shot for row in drafts.values() for shot in source_shot_ids(row)}
        for ref, _, payload in scene_sources:
            source = entries[ref['artifact_id']]
            if source.get('logical_id') != 'storyboard.package':
                continue
            shots = submitted_shots.intersection(
                shot['shot_id'] for scene in payload['scenes'] for shot in scene['shots'])
            if shots:
                require_handoff_visual_inputs(project, payload, source, shots,
                                              rows=drafts.values(), manifest=manifest, state=state,
                                              file_digest=file_digest)
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
        remaining = validate_scene_source_mapping(
            validation_rows, scene_sources, project, delivery_platform='redframe' if formal_redframe else None,
            read_evidence=read_evidence,
        ) or {}
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
        from .confirmation_scope import is_whole_artifact, classify, problems_for_write, KIND_SEGMENT
        def covers_usage(receipt):
            if is_whole_artifact(receipt):
                return True
            scope = classify(receipt)
            return (scope.kind == KIND_SEGMENT and not problems_for_write(receipt)
                    and {prior_id, current_id} <= set(scope.ids.get('segment_ids', [])))
        groups = [(set(receipt.get("artifact_ids", [])), group) for receipt in state["user_confirmations"]
                  if covers_usage(receipt) and receipt.get("approval_ref")
                  for group in receipt.get("reference_groups", []) if group.get("group_id") == group_id]
        # Group claims belong to a validated adoption report and its exact source
        # closure. They never become project_state user-confirmation receipts.
        relevant = {ref["artifact_id"] for ref in sources} | {tail["artifact_id"], head["artifact_id"]}
        for item in manifest['artifacts']:
            if (item.get('artifact_id') not in relevant or item.get('agent_acceptance') is None
                    or item.get('artifact_id') not in state['current_artifacts']
                    or not artifact_is_adopted(item, state, project)):
                continue
            report = agent_report(item)
            bindings = [report['artifact'], *report['upstreams']]
            for group in report.get('reference_groups', []):
                if group.get('group_id') != group_id:
                    continue
                members = group.get('members')
                if not isinstance(members, list) or not members:
                    return False
                if any(not any(all(member.get(k) == binding.get(k)
                                   for k in ('artifact_id', 'revision', 'sha256'))
                               for binding in bindings) for member in members):
                    return False
                groups.append(({binding['artifact_id'] for binding in bindings}, group))
        if len(groups) != 1:
            return False
        approved_ids, group = groups[0]
        if (group.get("event_id") != event or group.get("state_id") != tail["state_id"]
                or set(group.get("entity_ids", [])) != set(tail["entity_ids"])):
            return False
        for ref, role, node in [(tail, "end_frame", prior_id), (head, "start_frame", current_id)]:
            if ref["artifact_id"] not in approved_ids:
                return False
            members = [m for m in group.get("members", [])
                       if all(m.get(k) == ref[k] for k in ("artifact_id", "revision", "sha256"))
                       and role in m.get("roles", []) and node in m.get("draft_node_ids", [])]
            if len(members) != 1:
                return False
        return True
    exact_outputs = {
        f"{ref.get('asset_id', '')}@{ref.get('output_id', '')}"
        for row in validation_rows for ref in row.get('reference_checks', [])
        if formal_redframe and ref.get('output_id') is not None
    }
    asset_outputs = {}
    if exact_outputs:
        from .asset_refs import resolve_final_images
        asset_outputs = resolve_final_images(project, sorted(exact_outputs), file_digest=file_digest)
    endpoint_control_checks = {}
    prompt_state_control_checks = {}
    accepted_image_uses = []
    for row in validation_rows:
        row_frozen = context_scopes.get(row['node_id'], frozen)
        before_upload = formal_redframe and preflight and row['node_id'] in planned_ids
        row_authority, row_preview_usage = authority, preview_usage if private_preview else None
        if private_preview and row['node_id'] in context_scopes:
            prior_authority, row_preview_usage, _ = preview_scope(row_frozen, project, json_projections=projections, context_node_ids={row["node_id"]})
            row_authority = lambda ref: prior_authority(ref)[0]
        endpoint_mode = row.get('native_identity_mode') == 'first_last_frame_download'
        if before_upload and (endpoint_mode or 'native_binding' in row):
            raise ValueError('红帧上传前计划不能声明已采集平台绑定或首尾帧回执')
        if not endpoint_mode:
            if 'state_control' in row:
                raise ValueError('endpoint state_control requires its explicit verified identity mode')
            prompt_control = prompt_scene_control(
                row, frozen, project, state, scene_requirements,
                formal_delivery=formal_delivery and not private_preview)
            validate_scene_state_references(
                row, scene_requirements, accepted_prompt_control=prompt_control is not None)
            if prompt_control is not None:
                prompt_state_control_checks[row['node_id']] = prompt_control
        packaging_selected = any(set(item['source_shot_ids']) & set(source_shot_ids(row))
                                 for item in package_requirements) or any(
            (ref.get('product_reference') or {}).get('reference_role') == 'packaging'
            for ref in row.get('reference_checks', []))
        if not private_preview and (not reuse_source_review or packaging_selected
                                    or 'status' in (row.get('quality_evidence') or {})):
            validate_draft_evidence(row, package_requirements)
        if not isinstance(row.get('prompt'), str) or not row['prompt'].strip():
            raise ValueError('complete submitted prompt body required')
        duration = seconds(row['parameters']['duration'])
        if duration > MAX_GENERATION_SEGMENT_SECONDS:
            raise ValueError(f"草稿 {row['node_id']} 的生成段超过 {MAX_GENERATION_SEGMENT_SECONDS} 秒")
        low, high = map(seconds, row['duration_bounds'])
        if low > high or not low <= duration <= high:
            raise ValueError('duration outside explicitly selected platform bounds')
        if 'timing_scope' in row and (not private_preview or row['timing_scope'] != 'segment_total_only'):
            raise ValueError('only private preview may explicitly declare segment-total timing without a shot schedule')
        if redframe or 'edges' in row or 'source_nodes' in row:
            validate_canvas_inputs(row, redframe=redframe, before_upload=before_upload,
                                   preflight=formal_redframe and preflight and row['node_id'] in drafts)
        else:
            validate_shot_durations(row, row['parameters'])
        media, refs = row.get('ordered_media'), row.get('reference_checks')
        if before_upload:
            media = [None] * len(refs)
        if not isinstance(media, list) or not isinstance(refs, list) or len(refs) != len(media):
            raise ValueError('every uploaded media needs one explicit role/authority check in actual order')
        entity_type = row.get('native_entity_type')
        if media and not endpoint_mode and (not isinstance(entity_type, str) or not entity_type.strip()):
            raise ValueError(f"{row['node_id']}: versioned delivery requires native entity readback for all references")
        identity_mode = row.get('native_identity_mode', 'media_id')
        if not before_upload and not valid_native_media(media, identity_mode):
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
            output = asset_outputs.get(f"{ref.get('asset_id', '')}@{ref.get('output_id', '')}")
            packed = formal_redframe and 'product_reference_id' in ref
            face_slot = formal_redframe and slot in (row.get('realistic_face_slots') or [])
            if output and output.get('requires_face_certification') and not face_slot:
                raise ValueError(f'固定人物资产第 {slot} 槽须列入 realistic_face_slots 并使用已认证源卡')
            direct_slot = (formal_redframe and not before_upload and identity_mode == 'media_id'
                           and ref.get('canvas_binding', {}).get('mode') == 'direct'
                           and 'sha256' in media_item)
            if direct_slot and (media_item['node_id'] != row['node_id']
                                or media_item['sha256'] != ref.get('sha256')):
                raise ValueError('direct upload slot does not match the exact draft node and source bytes')
            depth_id = ref.get('source_depth_reference_id')
            if depth_id is not None:
                planned = depth_materials_by_segment.get(row.get('source_segment_id'))
                material = planned[slot - 1] if isinstance(planned, list) and slot <= len(planned) else None
                if (not isinstance(depth_id, str) or not depth_id or not isinstance(material, dict)
                        or depth_id != material.get('reference_id')
                        or material.get('kind') != 'video' or material.get('roles') != ['action']
                        or ref.get('kind') != 'video' or ref.get('sha256') != material.get('sha256')
                        or packed or output is not None or face_slot or ref.get('artifact_id')
                        or ref.get('roles') != material['roles']
                        or ref.get('source_shot_ids') != material.get('source_shot_ids')
                        or ref.get('path') != material.get('path')
                        or ref.get('canvas_binding', {}).get('mode') != 'direct'
                        or not (before_upload or direct_slot)):
                    raise ValueError('红帧深度参考须匹配已采用复刻源段中的准确视频、镜号及直传用途')
                path = project_path(project, material['path']).resolve()
                if (not path.is_relative_to(project) or not path.is_file()
                        or material['sha256'] != file_digest(path)):
                    raise ValueError('红帧深度参考的本地原件路径或字节与采用源稿不符')
                item = {}
            elif packed:
                if output is not None:
                    raise ValueError('产品包参考须使用唯一的产品包原件身份')
                if face_slot:
                    if not before_upload and media_item.get('sha256') != ref.get('sha256'):
                        raise ValueError('产品包认证源卡须回读实际字节并匹配准确原件')
                elif ref.get('canvas_binding', {}).get('mode') != 'direct' or not (before_upload or direct_slot):
                    raise ValueError('未走认证源卡的产品包参考须使用直传，回读时提供实际文件字节')
                path = resolve_pack_media(project, product_pack, ref, product_requirements)
                if 'product_reference' in ref:
                    product = ref['product_reference']
                    if (not isinstance(product, dict) or product.get('reference_role') != 'packaging'
                            or product.get('product_id') != product_pack['product_id']):
                        raise ValueError('包装声明与已锁产品不一致')
                    validate_registered_product_reference(project, ref, ref,
                        allowed_roots=(Path(product_pack['manifest_path']).parent,))
                    if (project / product['source']['path']).resolve() != path:
                        raise ValueError('包装声明须绑定本槽实际参考原件')
            elif output is not None:
                path = Path(output['path'])
                if (ref.get('sha256') != output['sha256'] or ref.get('kind') != 'image'
                        or (ref.get('path') is not None and project_path(project, ref['path']).resolve() != path.resolve())):
                    raise ValueError('direct image differs from the confirmed asset output')
                if face_slot:
                    if not before_upload and media_item.get('sha256') != output['sha256']:
                        raise ValueError('认证源卡槽须回读其实际文件字节并匹配已确认资产输出')
                elif ref.get('canvas_binding', {}).get('mode') != 'direct' or not (before_upload or direct_slot):
                    raise ValueError('confirmed asset output requires a direct slot with downloaded bytes')
                item = {}
            else:
                path = row_authority(ref)
            if private_preview:
                row_preview_usage(row, ref, media_item, slot)
            elif output is None and not packed and depth_id is None:
                item = next(a for a in manifest['artifacts'] if a['artifact_id'] == ref['artifact_id'])
                if item.get('type') == 'visual-supplement-image' and item.get('agent_acceptance') is not None:
                    from .confirmation_scope import image_usage_shots
                    permitted = set(image_usage_shots(agent_report(item).get('usage'), required=True))
                    if not set(reference_shot_ids(ref, row)) <= permitted:
                        raise ValueError('agent image acceptance usage does not cover these source shots')
                validate_registered_product_reference(project, ref, item)
                # Bind actual native identities to source bytes in the existing source
                # artifact registration, not a caller-provided parallel list of labels.
                # Actual slot identity is collected after upload and required by formal readback.
                if endpoint_mode:
                    expected_binding = canonical_json_bytes(
                        _endpoint_upload_expected(row_frozen, row, ref, media_item, slot))
                    matches = sum(canonical_json_bytes(binding) == expected_binding
                                  for binding in item.get('upload_bindings', []))
                    if matches != 1:
                        raise ValueError('endpoint has no unique exact manifest upload mapping')
                elif not before_upload and identity_mode == 'node_port_download':
                    expected_binding = _port_expected(row_frozen, row, ref, media_item, slot)
                    bindings = [b for b in item.get('upload_bindings', [])
                                if b.get('identity_mode') == 'node_port_download'
                                and all(b.get(k) == v for k, v in expected_binding.items())]
                    if len(bindings) != 1:
                        raise ValueError('node/port has no unique exact registered upload binding')
                    verify_port_binding(project, bindings[0], expected_binding)
                if endpoint_mode or (not before_upload and identity_mode == 'node_port_download'):
                    binding_mode = 'first_last_frame_download' if endpoint_mode else identity_mode
                    reverse_hashes = {b.get('sha256') for source in manifest['artifacts']
                        if source.get('status') == 'current' and artifact_is_adopted(source, state, project)
                        for b in source.get('upload_bindings', [])
                        if b.get('identity_mode') == binding_mode
                        and b.get('target_url') == frozen['target_url']
                        and b.get('target_project_id') == frozen.get('target_project_id')
                        and b.get('node_id') == media_item['node_id']
                        and b.get('reference_port') == media_item['reference_port']
                        and b.get('revision') == source.get('revision')
                        and b.get('sha256') == source.get('checksum')}
                    if reverse_hashes != {ref['sha256']}:
                        raise ValueError('conflicting endpoint upload source bytes' if endpoint_mode else
                                         'conflicting upload node/port source bytes')
                elif not before_upload and not direct_slot:
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
                        and artifact_is_adopted(source, state, project)
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
            if kind == 'image' and not private_preview and output is None and not packed:
                from .quality_evidence import validate_image_evidence, reference_shot_ids
                image_check = validate_image_evidence(
                    project, path, item.get('quality_evidence'), reference_shot_ids(ref, row),
                    reference=ref, artifact=item, state=state, file_digest=file_digest,
                    allow_narrower_use=formal_redframe and bool(item.get('agent_acceptance')))
                if image_check['accepted_deviations']:
                    accepted_image_uses.append({'node_id': row['node_id'], 'artifact_id': item['artifact_id'],
                                                'source_shot_ids': reference_shot_ids(ref, row),
                                                **image_check})
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
                scene_entities = {state['entity_id'] for state in scene_requirements
                                  if set(state['source_shot_ids']) & set(source_shot_ids(row))}
                adopted_shot_state = (formal_redframe and output is None and not packed
                                      and item.get('type') == 'visual-supplement-image'
                                      and not scene_entities.intersection(ref.get('entity_ids', [])))
                if not adopted_shot_state:
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
    face_source_card_slots = {row['source_segment_id']: row['realistic_face_slots']
                              for row in drafts.values() if formal_redframe and row.get('realistic_face_slots')}
    return {'ok': True, 'mode': 'private_candidate_preview' if private_preview else 'formal_delivery',
            **({'status': 'candidate', 'review_status': 'unreviewed', 'production_blocked': True} if private_preview else {}),
            'revision': frozen['revision'], 'frozen_sha256': digest(frozen),
            **({'target_url': frozen['target_url'], 'target_project_id': platform_project_id,
                'source_segment_ids': [row['source_segment_id'] for row in frozen['drafts']],
                'upload_mapping_pending_nodes': sorted(planned_ids)}
               if formal_redframe and preflight else {}),
            **({'face_source_card_slots': face_source_card_slots}
               if face_source_card_slots or (formal_redframe and preflight) else {}),
            'dependency_checks': dependencies,
            'endpoint_control_checks': endpoint_control_checks,
            **({'prompt_state_control_checks': prompt_state_control_checks}
               if prompt_state_control_checks else {}),
            **({'accepted_image_uses': accepted_image_uses} if accepted_image_uses else {}),
            'predecessor_context_nodes': list(context_scopes),
            'canvas_input_checks': {row['node_id']: ('passed' if redframe or 'edges' in row else 'not_checked')
                                    for row in frozen['drafts']},
            'quality_evidence_checks': 'image_evidence_only_not_draft_release' if private_preview else 'evidence_consistency_only',
            'packaging_checks': 'not_checked' if private_preview else 'declared_scope_and_evidence_consistency_only',
            'native_refresh_binding': 'not_checked',
            'generation_authorization': 'not_checked', 'production_ready': False,
            'visual_quality_verified': False, 'generation_input_provenance': 'unknown',
            'scope': 'registered bytes, declared reference roles/entities, durations and continuous split boundaries',
            'limitations': 'Declaration consistency only, not proof of user consent or a browser sandbox. Requires actual visual inspection of roles/states and current platform bounds; does not verify pixels, remote provenance or generation authorization; action-video and Xiaoyunque video durations are locally probed only.'}


def _face_certification_status(row, got, slot):
    records = got.get('face_certifications')
    record = records.get(str(slot)) if isinstance(records, dict) else None
    if not isinstance(record, dict):
        return 'missing'
    ref = row['reference_checks'][slot - 1]
    media = row['ordered_media'][slot - 1]
    cert = record.get('faceCertification')
    source_url = record.get('source_url')
    if (record.get('node_id') != media['node_id'] or media.get('sha256') != ref['sha256']
            or not isinstance(source_url, str) or not source_url.strip()
            or not isinstance(cert, dict) or cert.get('sourceUrl') != source_url
            or not isinstance(cert.get('assetId'), str) or not cert['assetId'].strip()):
        return 'failed'
    if ref['kind'] == 'image' and cert.get('imageDigest') != ref['sha256']:
        return 'failed'
    if ref['kind'] == 'video' and 'videoDigest' in cert and cert['videoDigest'] != ref['sha256']:
        return 'failed'
    return 'passed'


def compare(frozen: dict, observed: dict, project: Path | None = None, *, private_preview: bool = False,
            formal_delivery: bool = False) -> dict:
    """Report only selected full-field readback; metadata is not remote proof."""
    if 'preview_authorization' in frozen and not private_preview:
        raise ValueError('preview snapshot requires explicit --private-preview; not formal delivery')
    if private_preview and project is None:
        raise ValueError('private preview requires --project')
    port_mode = any(row.get('native_identity_mode') in ('node_port_download', 'first_last_frame_download') for row in frozen['drafts'])
    if port_mode and project is None:
        raise ValueError('download identity modes require --project and original slot evidence')
    submission = validate_submission(frozen, project, private_preview=private_preview,
                                     formal_delivery=formal_delivery) if project is not None else None
    face_slots_by_segment = (submission or {}).get('face_source_card_slots', {})
    platform_project_id = _declared_project_id(frozen)
    if ((platform_project_id is not None or port_mode or private_preview)
            and observed.get('target_project_id') != platform_project_id):
        raise ValueError('readback project differs from frozen target')
    if 'revision' in frozen and project is None:
        raise ValueError('versioned delivery requires --project to revalidate current reference authority')
    if target_url(frozen['target_url']) != target_url(observed['target_url']):
        raise ValueError('readback target differs from frozen target')
    if observed.get('phase') != 'after_refresh':
        raise ValueError('post-refresh editor readback required; graph/write receipt is insufficient')
    frozen_sha256 = submission['frozen_sha256'] if submission is not None else digest(frozen)
    if observed.get('frozen_sha256') != frozen_sha256:
        raise ValueError('readback does not identify this frozen payload')
    expected = rows_by_id(frozen['drafts'])
    actual = rows_by_id(observed['drafts'])
    differences = []
    if private_preview and frozen.get('cleanup'):
        expected_cleanup = [{'node_id': item['node_id'], 'absent': True} for item in frozen['cleanup']]
        if observed.get('cleanup_results') != expected_cleanup:
            differences.append({'field': 'cleanup_results', 'expected': expected_cleanup})
    native_reference_checks = {}
    canvas_input_checks = {}
    endpoint_control_checks = {}
    face_certification_checks = {}
    if set(actual) != set(expected):
        differences.append({'field': 'node_ids', 'missing': sorted(set(expected)-set(actual)),
                            'extra': sorted(set(actual)-set(expected))})
    for node_id, row in expected.items():
        got = actual.get(node_id, {})
        canvas_input_checks[node_id] = 'not_checked'
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
            expected_bytes = canonical_json_bytes(value)
            observed_bytes = canonical_json_bytes(got[field]) if field in got else None
            if field == 'ordered_media':
                ordered_media_bytes = expected_bytes
            if observed_bytes != expected_bytes:
                differences.append({'node_id': node_id, 'field': field,
                                    'expected_sha256': hashlib.sha256(expected_bytes).hexdigest(),
                                    'observed_sha256': (hashlib.sha256(observed_bytes).hexdigest()
                                                        if observed_bytes is not None else None)})
        for field in ('edges', 'source_nodes'):
            if field in row:
                expected_graph = sorted(map(canonical_json_bytes, row[field]))
                observed_graph = (sorted(map(canonical_json_bytes, got[field]))
                                  if isinstance(got.get(field), list) else None)
                if observed_graph != expected_graph:
                    # Reports hash the sorted element digests, regardless of byte sort order.
                    expected_hashes = sorted(hashlib.sha256(item).hexdigest() for item in expected_graph)
                    observed_hashes = (sorted(hashlib.sha256(item).hexdigest() for item in observed_graph)
                                       if observed_graph is not None else None)
                    differences.append({'node_id': node_id, 'field': field,
                                        'expected_sha256': digest(expected_hashes),
                                        'observed_sha256': digest(observed_hashes)})
        if project is not None and 'edges' in row:
            canvas_input_checks[node_id] = 'passed'
        face_slots = face_slots_by_segment.get(row.get('source_segment_id'), [])
        if face_slots:
            checks = {}
            for slot in face_slots:
                status = _face_certification_status(row, got, slot)
                checks[str(slot)] = status
                if status != 'passed':
                    differences.append({'node_id': node_id, 'field': 'face_certifications', 'slot': slot,
                                        'status': status})
            face_certification_checks[node_id] = checks
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
            mentioned_media = row['ordered_media']
            if 'edges' in row:
                mentioned_media = [media for media, ref in zip(row['ordered_media'], row['reference_checks'])
                                   if ref['canvas_binding']['prompt_reference']]
            status = reference_readback_status(mentioned_media, mentions, entity_type, identity_mode)
            native_reference_checks[node_id] = status
            if status == 'failed':
                differences.append({'node_id': node_id, 'field': 'native_mentions',
                                    'expected_sha256': hashlib.sha256(ordered_media_bytes).hexdigest(),
                                    'observed_sha256': digest(mentions) if mentions is not None else None})
    for difference in differences:
        node = difference.get('node_id')
        if node in canvas_input_checks and canvas_input_checks[node] == 'passed':
            canvas_input_checks[node] = 'failed'
    return {'ok': not differences, 'scope': 'selected post-refresh editor fields only',
            'native_refresh_binding': 'selected_fields_match' if not differences else 'failed',
            'generation_authorization': 'not_checked', 'production_ready': False,
            'draft_count': len(expected), 'submission_checks': submission or 'not_checked',
            'revision': frozen.get('revision'), 'frozen_sha256': frozen_sha256,
            'observed_sha256': digest(observed), 'differences': differences,
            **({'observed_target_project_id': observed['target_project_id']} if platform_project_id is not None else {}),
            'native_reference_checks': native_reference_checks,
            'canvas_input_checks': canvas_input_checks,
            'endpoint_control_checks': endpoint_control_checks,
            **({'face_certification_checks': face_certification_checks} if face_certification_checks else {}),
            'limitations': 'Evidence collector must actually refresh and read native editor values. '
                           'Native entity checks only cover explicitly requested drafts. '
                           'This comparison cannot establish remote provenance, pixel quality, generation status or generation authorization.'}
