"""Check recorded visual dependencies for the requested director shots."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .artifacts import load_manifest, project_path
from .asset_refs import director_location_readiness, require_references, resolve_final_images
from .media_types import MEDIA_EXTENSIONS
from .quality_evidence import reference_shot_ids, required_visuals, source_shot_ids
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
        if not affected:
            continue
        error = None
        try:
            require_schema(request, 'visual-supplement-request.schema.json', label=str(path))
            if (project_path(root, ref['path']) != project_path(root, source['path_or_url'])
                    or ref['status'] != ('confirmed' if source.get('user_approved') is True else 'agent_accepted')):
                raise ValueError('视觉补充请求来源与已采用导演不一致：' + str(path))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            error = str(exc)
        yield path, request, affected, error


def _carries(root, refs, path, checksum, *, artifact_id=None):
    return any(ref.get('sha256') == checksum
               and ref.get('kind', 'image') == 'image'
               and (ref.get('path') is None or project_path(root, ref['path']) == path)
               and (artifact_id is None or ref.get('artifact_id') == artifact_id)
               for ref in refs)


def _selected_relationship(root, request, manifest, shots, supplement_scope):
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
        image = resolve_final_images(root, [key])[key]
        approved_path, checksum = image['path'], image['sha256']
        asset_id, output_id = key.split('@')
        identity = {'asset_id': asset_id, 'output_id': output_id}
        covered = shots
    elif chosen['kind'] == 'confirmed_supplement':
        covered, problems = supplement_scope(key, tuple(sorted(shots)))
        if not covered:
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
    return (path, checksum, identity), covered


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
        if not isinstance(evidence, dict):
            continue
        sources, shots = evidence.get('source_files'), evidence.get('source_shot_ids')
        if (not isinstance(sources, list) or not isinstance(shots, list)
                or any(not isinstance(shot, str) for shot in shots)):
            continue
        if any(
            isinstance(source, dict) and isinstance(source.get('path'), str)
            and project_path(root, source['path']) == request_path
            for source in sources
        ):
            yield entry, set(shots)


def require_visual_readiness(root, storyboard, source, shot_ids, *, rows=None, manifest=None,
                             require_review=False, collect_blockers=False, asset_blockers=None):
    """Read the exact director's existing requests and report only in-scope gaps.

    The handoff entry point requires an explicit review of each requested shot.
    Availability applies to all requested shots even if there are only partial
    prompt rows. Actual reference bindings apply to the shots in those rows.
    """
    root = Path(root).resolve()
    known = {shot['shot_id']: shot for scene in storyboard.get('scenes', []) for shot in scene['shots']}
    shots = set(shot_ids)
    if not shots or shots - known.keys():
        raise ValueError('视觉前序核对须指定准确导演镜号：' + '、'.join(sorted(shots - known.keys())))
    availability, bindings, binding_segments = [], [], set()
    for shot in sorted(shots):
        if 'required_visuals' in known[shot]:
            try:
                required_visuals({'scenes': [{'shots': [known[shot]]}]})
            except ValueError as exc:
                availability.append(({shot}, str(exc)))
    for gap in storyboard.get('asset_gaps', []):
        affected = shots & set(gap['affected_shot_ids'])
        if affected and gap.get('return_required'):
            availability.append((affected,
                                 f"{gap['gap_id']}（{'、'.join(sorted(affected))}）：{gap['missing_information']}"))
    manifest = load_manifest(root) if manifest is None else manifest
    rows = list(rows) if rows is not None else []

    @lru_cache(maxsize=None)
    def supplement_scope(ident, requested):
        ok, problems = require_references(root, [ident], list(requested))
        if ok:
            return set(requested), []
        # A partial-use approval must not remove the same image from another
        # requested shot that the approval does cover.
        covered = {shot for shot in requested if supplement_scope(ident, (shot,))[0]} if len(requested) > 1 else set()
        return covered, problems

    ids, reviewed = {}, set()
    for path, request, affected, error in _requests(root, manifest, source, shots):
        if error:
            availability.append((affected, error))
            continue
        ident = request['request_id']
        if (ident in ids or request['project_id'] != storyboard.get('project_id')
                or set(request['source_shot_ids']) - known.keys()):
            availability.append((affected | ids.get(ident, set()), '视觉补充请求重复或与导演项目、镜号不符：' + ident))
            continue
        ids[ident] = affected
        label = f"{ident}（{'、'.join(sorted(affected))}）"
        review = request.get('visual_review')
        if review:
            if review['storyboard_sha256'] == source.get('checksum'):
                reviewed.update(affected)
            else:
                availability.append((affected, f'{label}：视觉检查记录对应的导演稿字节已变，须复核本范围'))
        decision = request['decision']
        if decision == 'no_supplement':
            continue
        if decision == 'return_upstream':
            availability.append((affected, f"{label}：{request['return_upstream']['reason']}"))
            continue
        if decision == 'reuse_existing':
            if request.get('relationship_reference'):
                try:
                    selection, covered = _selected_relationship(root, request, manifest, affected, supplement_scope)
                except ValueError as exc:
                    availability.append((affected, f'{label}：{exc}'))
                    continue
                if uncovered := affected - covered:
                    availability.append((uncovered, f'{label}：既有关系图未获准用于这些镜头'))
                missing = [(segment, shot) for segment, shot, refs in _segment_references(rows, covered)
                           if not _carries_relationship(root, refs, *selection)]
                if missing:
                    key = request['relationship_reference']
                    ref_id = request['existing_references'][0]['ref_id'] if key is True else key['ref_id']
                    binding_segments.update(segment for segment, _ in missing if segment)
                    bindings.append(({shot for _, shot in missing},
                                     f'{label}：缺本镜实际选用的既有关系图 {ref_id} 或空间用途'))
            continue

        options = []
        covered = set()
        for entry, declared in _available_images(root, manifest, path):
            if needed := affected & declared:
                permitted, _ = supplement_scope(entry['artifact_id'], tuple(sorted(needed)))
                if permitted:
                    options.append((entry, permitted))
                    covered.update(permitted)
        missing = affected - covered
        if missing:
            availability.append((missing, f"{ident}（{'、'.join(sorted(missing))}）：缺已采用并覆盖本用途的分镜图"))
        unbound = [(segment, shot) for segment, shot, refs in _segment_references(rows, covered) if not any(
            shot in permitted and _carries(root, refs, project_path(root, entry['path_or_url']),
                                           entry['checksum'], artifact_id=entry['artifact_id'])
            for entry, permitted in options
        )]
        if unbound:
            binding_segments.update(segment for segment, _ in unbound if segment)
            unbound_shots = {shot for _, shot in unbound}
            bindings.append((unbound_shots,
                             f"{ident}（{'、'.join(sorted(unbound_shots))}）：缺已采用分镜图的实际引用"))
    unchecked = shots - reviewed
    if require_review and unchecked:
        availability.insert(0, (unchecked, '缺本版视觉检查结论：' + '、'.join(sorted(unchecked))))
    # Modern callers already validated all effective ASTs, including locations.
    if asset_blockers is None:
        availability.extend(director_location_readiness(root, storyboard, shots, scoped=True))
    asset_errors = [({shot}, message) for shot, issues in (asset_blockers or {}).items() for message in issues]
    errors = asset_errors + availability + bindings
    if errors and not collect_blockers:
        raise ValueError('视觉前序未就绪：' + '；'.join(message for _, message in errors))
    result = {'source_shot_ids': sorted(shots), 'request_ids': sorted(ids),
              'reviewed_shot_ids': sorted(reviewed)}
    if collect_blockers:
        result.update(blocked_shot_ids=_by_shot(errors), asset_blockers=asset_blockers or {},
                      availability_blockers=_by_shot(availability), binding_blockers=_by_shot(bindings),
                      binding_segment_ids=sorted(binding_segments))
    return result


def _by_shot(errors):
    blockers = {}
    for scope, message in errors:
        for shot in sorted(scope):
            blockers.setdefault(shot, []).append(message)
    return blockers


def _references_for_shot(row, shot):
    refs = row.get('materials', row.get('candidate_upload_order', row.get('reference_checks', [])))
    return [ref for ref in refs if isinstance(ref, dict) and shot in reference_shot_ids(ref, row)]


def _segment_references(rows, shots):
    for row in rows:
        segment_id = row.get('source_segment_id') or row.get('segment_id') or row.get('node_id')
        for shot in sorted(shots & set(source_shot_ids(row))):
            yield segment_id, shot, _references_for_shot(row, shot)
