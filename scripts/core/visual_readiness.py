"""Check recorded visual dependencies for the requested director shots."""
from __future__ import annotations

import json
from pathlib import Path

from .artifacts import cache_file_digests, load_manifest, project_path
from .asset_refs import director_location_readiness, require_references, resolve_final_images
from .media_types import MEDIA_EXTENSIONS
from .quality_evidence import reference_shot_ids, source_shot_ids
from .schema_contracts import require_schema


def _requests(root, manifest, source, shots):
    folder = root / '05_分镜'
    paths = {*folder.glob('*视觉补充请求*.json'), *folder.glob('*visual-supplement-request*.json')}
    paths.update(project_path(root, item['path_or_url']) for item in manifest['artifacts']
                 if item.get('type') == 'visual-supplement-request'
                 and item.get('status') not in {'superseded', 'rejected'})
    for path in sorted({path.resolve() for path in paths}):
        if not path.is_relative_to(root):
            raise ValueError('视觉补充请求须位于当前项目：' + str(path))
        request = json.loads(path.read_text(encoding='utf-8-sig'))
        ref = request.get('source_storyboard') if isinstance(request, dict) else None
        if (not isinstance(ref, dict) or request.get('status') in {'superseded', 'rejected'}
                or ref.get('artifact_id') != source.get('artifact_id')
                or ref.get('revision') != source.get('revision')):
            continue
        scope = request.get('source_shot_ids')
        if not isinstance(scope, list) or not scope or any(not isinstance(shot, str) for shot in scope):
            raise ValueError('视觉补充请求缺镜号范围：' + str(path))
        affected = shots.intersection(scope)
        if affected:
            require_schema(request, 'visual-supplement-request.schema.json', label=str(path))
            if (project_path(root, ref['path']) != project_path(root, source['path_or_url'])
                    or ref['status'] != ('confirmed' if source.get('user_approved') is True else 'agent_accepted')):
                raise ValueError('视觉补充请求来源与已采用导演不一致：' + str(path))
            yield path, request, affected


def _carries(root, refs, path, checksum, *, artifact_id=None):
    return any(ref.get('sha256') == checksum
               and ref.get('kind', 'image') == 'image'
               and (ref.get('path') is None or project_path(root, ref['path']) == path)
               and (artifact_id is None or ref.get('artifact_id') == artifact_id)
               for ref in refs)


def _selected_relationship(root, request, manifest, shots, *, file_digest):
    """Resolve the selected, currently approved image, even in a mixed reference list."""
    selection = request['relationship_reference']
    refs = request['existing_references']
    if selection is True:
        if len(refs) != 1 or 'visual_review' in request:
            raise ValueError('请选择准确的既有关系图，登记 ref_id 和 sha256')
        chosen = refs[0]
    else:
        matches = [ref for ref in refs if ref['ref_id'] == selection['ref_id']]
        if len(matches) != 1:
            raise ValueError('所选关系图 ref_id 与现有参考缺少唯一对应：' + selection['ref_id'])
        chosen = matches[0]
    key = chosen['ref_id']
    if chosen['kind'] == 'confirmed_asset':
        image = resolve_final_images(root, [key], file_digest=file_digest)[key]
        approved_path, checksum = image['path'], image['sha256']
        asset_id, output_id = key.split('@')
        identity = {'asset_id': asset_id, 'output_id': output_id}
    elif chosen['kind'] == 'confirmed_supplement':
        ok, problems = require_references(root, [key], sorted(shots), file_digest=file_digest)
        if not ok:
            raise ValueError('；'.join(problems))
        image = next(entry for entry in manifest['artifacts'] if entry['artifact_id'] == key)
        approved_path, checksum = image['path_or_url'], image['checksum']
        identity = {'artifact_id': key}
    else:
        raise ValueError('关系图须对应当前已确认资产或已采用分镜图：' + key)
    path = project_path(root, chosen['path'])
    if (path != project_path(root, approved_path) or path.suffix.lower() not in MEDIA_EXTENSIONS['image']
            or (selection is not True and selection['sha256'] != checksum)):
        raise ValueError('关系图路径或图片字节与所选版本不一致：' + key)
    return path, checksum, identity


def _carries_relationship(root, refs, path, checksum, identity):
    for ref in refs:
        roles = ref.get('roles', [ref.get('role', ref.get('primary_role'))])
        binding = ref.get('canvas_binding') or {}
        if (isinstance(roles, list) and any(role in ('layout', 'state_reference') for role in roles)
                and isinstance(binding, dict) and binding.get('prompt_reference') is not False
                and all(ref.get(key) == value for key, value in identity.items())
                and _carries(root, [ref], path, checksum)):
            return True
    return False


def _available_images(root, manifest, request_path):
    for entry in manifest['artifacts']:
        if entry.get('type') != 'visual-supplement-image' or entry.get('status') != 'current':
            continue
        evidence = entry.get('quality_evidence') or {}
        if any(project_path(root, source['path']) == request_path for source in evidence.get('source_files', [])):
            yield entry, set(evidence.get('source_shot_ids', []))


def _supplement_coverage(root, options, shots, row, file_digest):
    covered = set()
    for entry, allowed in options:
        used = shots & allowed
        if row is not None:
            image = project_path(root, entry['path_or_url'])
            used = {shot for shot in used if _carries(
                root, _references_for_shot(row, shot), image, entry['checksum'],
                artifact_id=entry['artifact_id'])}
        if used and require_references(root, [entry['artifact_id']], sorted(used), file_digest=file_digest)[0]:
            covered.update(used)
    return covered


def require_visual_readiness(root, storyboard, source, shot_ids, *, rows=None, manifest=None,
                             require_review=False, collect_blockers=False, assets=None, file_digest=None):
    """Read the exact director's existing requests and report only in-scope gaps.

    The handoff entry point requires an explicit review of each requested shot.
    With rows, a matching adopted supplement must be selected in each segment
    that uses its shots. This checks records and bytes, not the image contents.
    Handoff routing also distinguishes missing visual work from an upstream
    decision or a reference missing from the video manuscript.
    """
    root = Path(root).resolve()
    known = {shot['shot_id'] for scene in storyboard.get('scenes', []) for shot in scene['shots']}
    shots = set(shot_ids)
    if not shots or shots - known:
        raise ValueError('视觉前序核对须指定准确导演镜号：' + '、'.join(sorted(shots - known)))
    errors = []
    for gap in storyboard.get('asset_gaps', []):
        affected = shots & set(gap['affected_shot_ids'])
        if affected and gap.get('return_required'):
            errors.append(('upstream', affected,
                           f"{gap['gap_id']}（{'、'.join(sorted(affected))}）：{gap['missing_information']}"))
    manifest = load_manifest(root) if manifest is None else manifest
    requests = list(_requests(root, manifest, source, shots))
    # Share only unchanged bytes. Confirmation and use still get checked per reference.
    file_digest = cache_file_digests(check_for_changes=True) if file_digest is None else file_digest
    ids, reviewed = set(), set()
    for path, request, affected in requests:
        ident = request['request_id']
        if (ident in ids or request['project_id'] != storyboard.get('project_id')
                or set(request['source_shot_ids']) - known):
            raise ValueError('视觉补充请求重复或与导演项目、镜号不符：' + ident)
        ids.add(ident)
        label = f"{ident}（{'、'.join(sorted(affected))}）"
        review = request.get('visual_review')
        if review:
            if review['storyboard_sha256'] == source.get('checksum'):
                reviewed.update(affected)
            else:
                errors.append(('visual', affected, f'{label}：视觉检查记录对应的导演稿字节已变，须复核本范围'))
        decision = request['decision']
        if decision == 'no_supplement':
            continue
        if decision == 'return_upstream':
            upstream = request['return_upstream']
            errors.append(('upstream', affected, f"{label}：退回 {upstream['target']}，{upstream['reason']}"))
            continue
        if decision == 'reuse_existing':
            if request.get('relationship_reference'):
                try:
                    selection = _selected_relationship(root, request, manifest, affected, file_digest=file_digest)
                except ValueError as exc:
                    errors.append(('visual', affected, f'{label}：{exc}'))
                    continue
                missing = {shot for shot, refs in _segment_references(rows or (), affected)
                           if not _carries_relationship(root, refs, *selection)}
                if missing:
                    key = request['relationship_reference']
                    ref_id = request['existing_references'][0]['ref_id'] if key is True else key['ref_id']
                    errors.append(('video', missing, f'{label}：缺本镜实际选用的既有关系图 {ref_id} 或空间用途'))
            continue

        options = list(_available_images(root, manifest, path))
        if rows is None:
            uses = [(affected, None)]
        else:
            uses = [(set(source_shot_ids(row)) & affected, row) for row in rows
                    if set(source_shot_ids(row)) & affected]
        missing = set()
        row_shots = set()
        for needed, row in uses:
            row_shots.update(needed)
            missing.update(needed - _supplement_coverage(root, options, needed, row, file_digest))
        if collect_blockers:
            # Missing rows need the same visual prerequisites as a new video
            # draft. Check unresolved shots individually since the same image
            # may be adopted for only part of the requested scope.
            missing.update(affected - row_shots)
            if rows is not None or len(affected) > 1:
                available = {shot for shot in missing
                             if _supplement_coverage(root, options, {shot}, None, file_digest)}
                if rows is not None and (needs_binding := available & row_shots):
                    errors.append(('video', needs_binding, f'{label}：已采用的分镜图尚未在视频稿中实际选用'))
                missing.difference_update(available)
        if missing:
            errors.append(('visual', missing, f"{ident}（{'、'.join(sorted(missing))}）：缺已采用并覆盖本用途的分镜图"))
    unchecked = shots - reviewed
    if require_review and unchecked:
        errors.insert(0, ('visual', unchecked, '缺本版视觉检查结论：' + '、'.join(sorted(unchecked))))
    errors.extend(('upstream', set(scope), message)
                  for scope, message in director_location_readiness(root, storyboard, shots, scoped=True, assets=assets))
    if errors and not collect_blockers:
        raise ValueError('视觉前序未就绪：' + '；'.join(message for _, _, message in errors))
    result = {'source_shot_ids': sorted(shots), 'request_ids': sorted(ids),
              'reviewed_shot_ids': sorted(reviewed)}
    if collect_blockers:
        blockers = {}
        blockers_by_stage = {'visual': {}, 'upstream': {}, 'video': {}}
        for stage, scope, message in errors:
            for shot in sorted(scope):
                blockers.setdefault(shot, []).append(message)
                blockers_by_stage[stage].setdefault(shot, []).append(message)
        result['blocked_shot_ids'] = blockers
        result['blockers_by_stage'] = blockers_by_stage
    return result


def _references_for_shot(row, shot):
    refs = row.get('materials', row.get('candidate_upload_order', row.get('reference_checks', [])))
    return [ref for ref in refs if isinstance(ref, dict) and shot in reference_shot_ids(ref, row)]


def _segment_references(rows, shots):
    for row in rows:
        for shot in sorted(shots & set(source_shot_ids(row))):
            yield shot, _references_for_shot(row, shot)
