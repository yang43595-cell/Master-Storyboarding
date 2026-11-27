"""Read-only evidence consistency checks, not computer vision or authorization.

Evidence lives on the existing image registration / delivery draft. A successful
check proves only that the supplied observations are complete and byte-bound.
It never writes approval, current status, or a generation job association.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .media_types import MEDIA_EXTENSIONS
from .prompt_body import (
    reference_shot_ids,
    scene_state_requirements,
    source_shot_ids,
    submitted_prompt_body,
)
from .quality_rules import (
    DRAFT_ASPECTS,
    IMAGE_ASPECTS,
    MAX_GENERATION_SEGMENT_SECONDS,
    PACKAGING_ASPECTS,
    _PROMPT_TIMECODE,
    _file,
    _has_action_reference,
    _is_action_video,
    _observations,
    _positive_seconds,
    _text,
    director_shot_audio,
    draft_binding,
    fingerprint,
    generation_duration_values,
    image_use,
    native_input_binding,
    packaging_requirements,
    product_identity,
    replay_source_audio,
    required_visuals,
    validate_draft_evidence,
    validate_packaging_scope,
    validate_product_reference,
    validate_registered_product_reference,
    validate_required_visuals,
    validate_scene_state_coverage,
    validate_scene_state_references,
    validate_shot_durations,
)


def _image_confirmation_usages(root, artifact, state):
    """Read image permissions from human receipts, never a draft/self-check claim."""
    from .confirmation_scope import is_whole_artifact

    ident, checksum = artifact.get('artifact_id'), artifact.get('checksum')
    for receipt in state.get('user_confirmations', []):
        if not is_whole_artifact(receipt) or not _text(receipt.get('approval_ref')):
            continue
        if (receipt.get('artifact_id') == ident and receipt.get('sha256') == checksum
                and receipt.get('revision') == artifact.get('revision')):
            yield receipt.get('usage')


def _accepted_image_checks(root, image_path, evidence, shot_ids, reference, artifact, state):
    """Resolve explicitly accepted observations for exact pixels and one image use."""
    from .confirmation_scope import image_usage_shots
    from .project_state import load_project_state
    from .manifest_store import project_path

    if (not isinstance(artifact, dict) or artifact.get('type') != 'visual-supplement-image'
            or artifact.get('checksum') != evidence.get('image_sha256')
            or project_path(root, artifact.get('path_or_url', '')) != image_path):
        return {}
    state = load_project_state(root) if state is None else state
    expected = image_use(reference, shot_ids) if reference is not None else None
    observed_uses = evidence.get('uses', [])
    accepted = {}
    for usage in _image_confirmation_usages(root, artifact, state):
        if not isinstance(usage, dict) or not usage.get('accepted_deviations'):
            continue
        if not set(shot_ids) <= set(image_usage_shots(usage, required=True)):
            continue
        records = usage['accepted_deviations']
        if not isinstance(records, list):
            raise ValueError('quality_evidence: accepted_deviations 必须逐项记录准确用途和问题')
        for record in records:
            if (not isinstance(record, dict) or record.get('image_sha256') != artifact['checksum']
                    or not _text(record.get('quote')) or not _text(record.get('decision_ref'))):
                raise ValueError('quality_evidence: 接受偏差缺准确图片、用户原话或决定来源')
            use = record.get('use')
            if (not isinstance(use, dict) or not isinstance(use.get('source_shot_ids'), list)
                    or not set(shot_ids) <= set(use['source_shot_ids'])):
                continue
            # A query with no role only checks that a permitted use exists. The
            # real delivery consumer must also match its roles/entities/state.
            applicable = any(use == observed for observed in observed_uses)
            if expected is not None:
                narrowed = {**use, 'source_shot_ids': sorted(shot_ids)}
                applicable = applicable and narrowed == expected
            if not applicable:
                continue
            checks = record.get('checks')
            if not isinstance(checks, dict) or not checks or not set(checks) <= set(IMAGE_ASPECTS):
                raise ValueError('quality_evidence: 接受偏差须列明图片检查项，不能概括放行')
            for aspect, observation in checks.items():
                if (not isinstance(observation, dict) or observation.get('result') not in {'fail', 'unknown'}
                        or observation != evidence.get('checks', {}).get(aspect)):
                    raise ValueError('quality_evidence: 已接受问题与当前准确观察不一致：' + aspect)
                accepted[aspect] = observation
    return accepted


def validate_image_evidence(root, image_path, evidence, shot_ids, *, allowed_roots=(), reference=None,
                            product_reference=None, artifact=None, state=None, file_digest=None,
                            allow_narrower_use=False):
    """Require exact image/source bytes and explicit observations for this use."""
    path = Path(image_path).resolve()
    root = Path(root).resolve()
    accepted = {}
    checks = evidence.get('checks') if isinstance(evidence, dict) else None
    if isinstance(evidence, dict) and (evidence.get('status') != 'pass' or any(
            isinstance(check, dict) and check.get('result') in {'fail', 'unknown'}
            for check in (checks.values() if isinstance(checks, dict) else []))):
        accepted = _accepted_image_checks(root, path, evidence, shot_ids, reference, artifact, state)
    checked = copy.deepcopy(evidence)
    if accepted:
        # Validate completeness with a local projection; preserve every original
        # failed observation and never report accepted pixels as visually pass.
        checked['status'] = 'pass'
        for aspect in accepted:
            checked['checks'][aspect]['result'] = 'pass'
    _observations(checked, IMAGE_ASPECTS)
    _file(root, {'path': str(path), 'sha256': evidence.get('image_sha256')}, allowed_roots, file_digest=file_digest)
    if not isinstance(evidence.get('source_files'), list) or not evidence['source_files']:
        raise ValueError('quality_evidence: 缺实际对照的源文件及指纹')
    for source in evidence['source_files']:
        _file(root, source, allowed_roots, file_digest=file_digest)
    allowed = evidence.get('source_shot_ids')
    if (not isinstance(allowed, list) or not allowed or not all(_text(s) for s in allowed)
            or not shot_ids or not set(shot_ids) <= set(allowed)):
        raise ValueError('quality_evidence: 缺本次准确镜号或检查未覆盖本用途')
    view = evidence.get('view')
    if (not isinstance(view, dict) or view.get('resolution') != 'original'
            or not _text(view.get('locator'))):
        raise ValueError('quality_evidence: 缺原尺寸像素查看范围，缩略图或用途名不能代替')
    if reference is not None:
        expected = image_use(reference, shot_ids)
        uses = evidence.get('uses')
        def matches(use):
            if use == expected:
                return True
            return (allow_narrower_use and isinstance(use, dict)
                    and isinstance(use.get('source_shot_ids'), list)
                    and set(shot_ids) <= set(use['source_shot_ids'])
                    and {**use, 'source_shot_ids': expected['source_shot_ids']} == expected)
        if not isinstance(uses, list) or not any(matches(use) for use in uses):
            raise ValueError('quality_evidence: 本镜参考职责/实体/状态用途未核或已变化，旧图审失效')
    product = reference.get('product_reference') if reference is not None else product_reference
    if isinstance(product, dict) and product.get('reference_role') == 'packaging':
        validate_product_reference(root, product, allowed_roots=allowed_roots, file_digest=file_digest)
        packaging = evidence.get('packaging')
        if 'physical_variant' in product:
            basis = product['physical_variant']['basis']
            if (not set(shot_ids) <= set(basis['source_shot_ids'])
                    or basis['source'] not in evidence['source_files']
                    or not isinstance(packaging, dict)
                    or packaging.get('version_scope') != 'source_artwork_only'
                    or packaging.get('physical_variant_verified') is not False):
                raise ValueError('packaging: 可见原图QA不得冒称物理SKU已核；用途/来源须准确覆盖')
        _observations(packaging, PACKAGING_ASPECTS)
        if (packaging.get('product_sha256') != fingerprint(product)
                or product['source'] not in evidence['source_files']
                or any(c['result'] != 'pass' for c in packaging['checks'].values())):
            raise ValueError('packaging: 包装专项观察未覆盖准确商品版本/来源；旧动作QA不能替代')
    return {'verification': 'evidence_consistency_only', 'visual_quality_verified': False,
            'usage_checked': reference is not None, 'physical_variant_verified': False,
            'accepted_deviations': sorted(accepted),
            'image_use_status': 'user_accepted_deviations' if accepted else 'observations_passed'}


def _validate_persisted_download_evidence(row, root, revision, target, project_id):
    """Direct claim consumers must re-open raw evidence, not trust a mode/PASS.

    Artifact adoption and focal checks call this outside canvas preflight. The
    low-level validators remain the sole raw download/DOM evidence contracts;
    this does not replace later source authority, image QA or production gates.
    """
    from .native_port_binding import verify_port_binding
    from .endpoint_control import verify_endpoint_control
    media, refs = row['ordered_media'], row.get('reference_checks')
    if (not isinstance(refs, list) or len(refs) != len(media)
            or any(not isinstance(ref, dict) or ref.get('kind') != 'image'
                   or ref.get('sha256') != item['sha256'] for ref, item in zip(refs, media))):
        raise ValueError('native_binding: 下载身份必须逐槽绑定准确图片来源字节')
    common = dict(target_url=target, target_project_id=project_id, draft_revision=revision,
                  draft_node_id=row.get('node_id'), source_segment_id=row.get('source_segment_id'))
    if row['native_identity_mode'] == 'first_last_frame_download':
        params = row['parameters']
        aspects = [params[k] for k in ('aspect_ratio', 'aspect', 'ratio') if k in params]
        if not aspects or any(a != aspects[0] for a in aspects):
            raise ValueError('native_binding: 首尾比例缺失或别名冲突')
        expected = dict(common, duration=params.get('duration'), model=params.get('model'),
                        aspect_ratio=aspects[0], resolution=params.get('resolution'), ordered_media=media)
        verify_endpoint_control(root, (row.get('state_control') or {}).get('platform'), expected)
        return
    # A preview carries its bounded upload mapping; formal references consume
    # the existing artifact registration. Neither path infers a media_id.
    from .manifest_store import load_manifest
    for slot, (item, ref) in enumerate(zip(media, refs), 1):
        expected = dict(common, slot=slot, node_id=item['node_id'], reference_port=item['reference_port'],
                        sha256=item['sha256'], revision=ref.get('revision'), phase='after_refresh')
        evidence_root = Path(ref.get('project_path', root)).resolve()
        if 'upload_binding' in ref:
            upload = ref['upload_binding']
        else:
            sources = [a for a in load_manifest(evidence_root)['artifacts']
                       if a.get('artifact_id') == ref.get('artifact_id')
                       and a.get('revision') == ref.get('revision') and a.get('checksum') == ref.get('sha256')]
            if len(sources) != 1:
                raise ValueError('native_binding: 缺准确已登记下载来源')
            uploads = [b for b in sources[0].get('upload_bindings', []) if isinstance(b, dict)
                       and b.get('identity_mode') == 'node_port_download'
                       and all(b.get(k) == v for k, v in expected.items())]
            if len(uploads) != 1:
                raise ValueError('native_binding: 缺唯一逐槽原始下载映射')
            upload = uploads[0]
        verify_port_binding(evidence_root, upload, expected)


def validate_native_binding_claims(value, root, revision=None, target=None, project_id=None):
    """Reject stale verified claims without modifying historical receipts/drafts.

    Missing/not_bound/unknown permits an explicitly unverified candidate; this
    checker never generates a verified status or establishes remote provenance.
    Evidence uses the existing canvas readback's after_refresh/drafts fields.
    """
    if isinstance(value, list):
        for item in value:
            validate_native_binding_claims(item, root, revision, target, project_id)
        return
    if not isinstance(value, dict):
        return
    revision = value.get('revision') if revision is None else revision
    if target is None:
        target = value.get('target_url') or (value.get('target_canvas') or {}).get('url')
    if project_id is None:
        project_id = value.get('target_project_id')
    binding = value.get('native_binding')
    def current_flags(record):
        if not isinstance(record, dict):
            return False
        claimed = any(record.get(k) is True for k in ('native_binding_verified', 'native_entity_verified'))
        for field in ('materials', 'ordered_candidate_references', 'reference_checks', 'required_continuous_cutpoint_references'):
            rows = record.get(field, [])
            if isinstance(rows, list):
                claimed |= any(current_flags(r) for r in rows)
        return claimed
    if current_flags(value) and (not isinstance(binding, dict) or binding.get('status') != 'persisted_readback_verified'):
        raise ValueError('native_binding: 旧native_binding_verified/native_entity_verified不得替代本修订精确回读')
    scope = value.get('native_delivery_scope')
    if ((isinstance(scope, str) and scope.strip()) or
            (isinstance(scope, dict) and scope.get('status') not in {None, 'unknown', 'not_submitted', 'unverified', 'stale'})):
        raise ValueError('native_binding: 旧native_delivery_scope/final_native_receipt_path不是本修订逐段回读；保留到历史，不继承通过')
    if value.get('final_native_receipt_path') and not isinstance(scope, dict):
        raise ValueError('native_binding: final_native_receipt_path不能单独证明本修订已回读')
    if binding is not None:
        if not isinstance(binding, dict):
            raise ValueError('native_binding: 状态必须明确，不能伪作已回读')
        status = binding.get('status')
        if status == 'persisted_readback_verified':
            if not _text(revision) or binding.get('revision') != revision:
                raise ValueError('native_binding: 旧回读修订不适用于当前稿')
            if binding.get('input_sha256') != fingerprint(native_input_binding(value)):
                raise ValueError('native_binding: 当前正文/引用/参数已变化；旧verified声明失效')
            path = _file(root, {'path': binding.get('evidence_path'),
                                'sha256': binding.get('evidence_sha256')})
            observed = json.loads(path.read_text(encoding='utf-8-sig'))
            if (not isinstance(observed, dict) or observed.get('phase') != 'after_refresh'
                    or observed.get('revision') != revision):
                raise ValueError('native_binding: 缺本修订真实刷新回读字段，不能继承旧回执')
            node = value.get('node_id') or binding.get('node_id') or binding.get('draft_node_id')
            url = binding.get('canvas_url')
            if not _text(node) or not _text(url) or not _text(target) or url != target or observed.get('target_url') != target:
                raise ValueError('native_binding: 当前目标画布/节点身份缺失或不符')
            rows = observed.get('drafts')
            matches = [r for r in rows if isinstance(r, dict) and r.get('node_id') == node] if isinstance(rows, list) else []
            if len(matches) != 1:
                raise ValueError('native_binding: 缺当前节点唯一回读')
            actual = matches[0]
            prompt = submitted_prompt_body(value)
            if not _text(prompt) or not isinstance(value.get('parameters'), dict):
                raise ValueError('native_binding: verified仅可绑定完整当前段正文/参数，不接受整包摘要冒充')
            from .native_references import valid_native_media, reference_readback_status, endpoint_readback_status
            identity_mode = value.get('native_identity_mode', 'media_id')
            expected_media = value.get('ordered_media')
            if expected_media is None and identity_mode == 'media_id':
                nodes, media = binding.get('ordered_asset_node_ids'), binding.get('ordered_native_media_ids')
                if not isinstance(nodes, list) or not isinstance(media, list) or len(nodes) != len(media):
                    raise ValueError('native_binding: 缺有序原生引用身份')
                expected_media = [dict(node_id=n, media_id=m) for n, m in zip(nodes, media)]
            if not valid_native_media(expected_media, identity_mode):
                raise ValueError('native_binding: 当前有序引用身份无效')
            for material in value.get('materials', []):
                if isinstance(material, dict) and material.get('native_entity_verified') is True:
                    if {'node_id': material.get('native_node_id'), 'media_id': material.get('native_media_id')} not in expected_media:
                        raise ValueError('native_binding: 已核materials声明与当前原生实体不一致')
            if (actual.get('prompt') != prompt or actual.get('parameters') != value.get('parameters')
                    or actual.get('ordered_media') != expected_media):
                raise ValueError('native_binding: 本稿正文/参数/有序引用与回读不一致')
            mentions = actual.get('native_mentions')
            entity_type = value.get('native_entity_type') or binding.get('native_entity_type')
            if identity_mode == 'first_last_frame_download':
                status = endpoint_readback_status(expected_media, actual.get('frame_slots'), mentions)
            else:
                status = reference_readback_status(expected_media, mentions, entity_type, identity_mode)
            if status == 'failed':
                raise ValueError('native_binding: 缺本节点原生实体回读，文字或图边不代替')
            if identity_mode != 'media_id':
                if not _text(project_id) or observed.get('target_project_id') != project_id:
                    raise ValueError('native_binding: 缺当前准确目标项目，不能从回执反推')
                _validate_persisted_download_evidence(value, root, revision, target, project_id)
        elif status not in {None, 'unknown', 'not_bound', 'unverified', 'not_checked', 'stale'}:
            raise ValueError('native_binding: 未识别状态不能冒充已回读')
    # Historical receipts/archives remain data, not current verified claims.
    for key in ('drafts', 'generation_segments', 'creative_sections', 'independent_candidate_ranges'):
        if key in value:
            validate_native_binding_claims(value[key], root, revision, target, project_id)


def validate_scene_source_mapping(rows, sources, project, *, check_submitted_body=False, delivery_platform=None,
                                  read_evidence=None):
    """Bind draft shot mapping to byte-checked source segments and director.

    sources contains (authority reference, resolved path, decoded JSON). Source
    director links must be present in source_artifacts: omission cannot downgrade
    the state check to a legacy/no-change path.
    """
    from .prompt_body import SOURCE_SEGMENT_FIELDS, source_review_segments

    videos = []
    video_origins = {}
    all_shots = set()
    visual_requirements = {}
    has_states = False
    has_packaging = False
    delivery_shots = {shot for row in rows for shot in source_shot_ids(row)}
    delivery_segment_ids = ({row.get('source_segment_id', row.get('node_id')) for row in rows}
                            if delivery_platform == 'redframe' else None)
    content_segment_ids = delivery_segment_ids if check_submitted_body else None
    for ref, path, payload in sources:
        linked_director = False
        if not isinstance(payload, dict):
            continue
        segments = source_review_segments(payload) if any(key in payload for key in SOURCE_SEGMENT_FIELDS) else []
        for shot_id, items in required_visuals(payload).items():
            if shot_id in visual_requirements and visual_requirements[shot_id] != items:
                raise ValueError(f'required_visuals: {shot_id} 存在不同来源要求')
            visual_requirements[shot_id] = items
        for scene in payload.get('scenes', []):
            all_shots.update(s['shot_id'] for s in scene.get('shots', []))
            has_states |= bool(scene.get('scene_states'))
            has_packaging |= any(s.get('packaging_requirements') for s in scene.get('shots', []))
        for key in ('source_storyboard', 'source_director'):
            upstream = payload.get(key)
            if not isinstance(upstream, dict):
                continue
            expected_path = (Path(ref.get('project_path', project)) / upstream['path']).resolve() if upstream.get('path') else None
            matches = [(r, p, value) for r, p, value in sources if p != path and
                       (not upstream.get('artifact_id') or r.get('artifact_id') == upstream['artifact_id']) and
                       (expected_path is None or expected_path == p) and
                       (upstream.get('artifact_id') or expected_path is not None)]
            if len(matches) != 1:
                raise ValueError('scene_state: source_artifacts 必须包含准确来源导演，不能省略后跳过状态检查')
            r, p, value = matches[0]
            if ((upstream.get('revision') and upstream['revision'] != r.get('revision')) or
                    (upstream.get('sha256') and upstream['sha256'] != r.get('sha256')) or
                    not isinstance(value, dict) or not isinstance(value.get('scenes'), list)):
                raise ValueError('scene_state: 视频源与导演修订/字节不一致')
            if rows and segments:
                from .boundary_rules import validate_generation_boundaries
                validate_generation_boundaries(payload, value, Path(ref.get('project_path', project)),
                                               delivery_platform=delivery_platform, delivery_segment_ids=delivery_segment_ids,
                                               action_shots=delivery_shots,
                                               prompt_content_segment_ids=content_segment_ids, read_evidence=read_evidence)
                linked_director = True
        if segments:
            if rows and not linked_director and isinstance(payload.get('scenes'), list) and payload['scenes']:
                # Existing composite source snapshots carry their director and
                # segment mapping in the same byte-checked artifact.
                from .boundary_rules import validate_generation_boundaries
                validate_generation_boundaries(payload, payload, Path(ref.get('project_path', project)),
                                               delivery_platform=delivery_platform, delivery_segment_ids=delivery_segment_ids,
                                               action_shots=delivery_shots,
                                               prompt_content_segment_ids=content_segment_ids, read_evidence=read_evidence)
                linked_director = True
            if rows and not linked_director:
                raise ValueError('generation_boundary: 生成段源必须绑定准确导演，不能删来源绕过分段检查')
            from .boundary_rules import validate_delivery_parameters
            validate_delivery_parameters(rows, payload, Path(ref.get('project_path', project)),
                                         platform=delivery_platform, read_evidence=read_evidence)
            video_origins.update({segment['segment_id']: ref for segment in segments})
            videos.extend(segments)
    if any(row.get('source_segment_id') for row in rows) and not videos:
        raise ValueError('scene_state: 已声明source_segment_id但缺准确视频源，不能退回无映射检查')
    # Audio provenance is independent of persistent environment changes. Read
    # only existing director placements and video source_audio; no NLP claims.
    shot_audio = director_shot_audio(payload for _, _, payload in sources if isinstance(payload, dict))
    if videos and (shot_audio or any('source_audio' in s for s in videos)):
        replay_source_audio(videos, shot_audio)
    if videos:
        from .boundary_rules import validate_delivery_cutpoints
        external_predecessors = validate_delivery_cutpoints(rows, videos)
    if not has_states and not has_packaging and not visual_requirements and not videos:
        return
    if not videos:
        if visual_requirements:
            raise ValueError('required_visuals: 交付必须携带含准确镜号的 generation_segments 来源')
        raise ValueError('scene_state/packaging: 场景后态或包装交付必须携带准确视频源generation_segments，不能省略视频源绕过镜号映射')
    for row in rows:
        ids = source_shot_ids(row)
        if not ids or not set(ids) <= all_shots:
            raise ValueError('scene_state: 提交镜号不在准确源导演内')
        segment = row.get('source_segment_id', row.get('node_id'))
        matches = [s for s in videos if s.get('segment_id') == segment]
        if len(matches) != 1 or source_shot_ids(matches[0]) != ids:
            raise ValueError('scene_state: 提交 source_shots 必须逐镜匹配准确视频源 source_segment_id，不能换镜绕过后态')
    delivery_sources = {s['segment_id']: (s, Path(video_origins[s['segment_id']].get('project_path', project)))
                        for s in videos}
    validate_required_visuals(rows, visual_requirements, project=project, delivery_sources=delivery_sources)
    if check_submitted_body:
        for row in rows:
            segment_id = row.get('source_segment_id', row.get('node_id'))
            source, _ = delivery_sources[segment_id]
            body = submitted_prompt_body(source)
            if body and delivery_platform == 'redframe':
                from .native_references import redframe_source_prompt
                materials = source.get('materials', source.get('candidate_upload_order', []))
                body = redframe_source_prompt(body, materials)
            if body and submitted_prompt_body(row) != body:
                raise ValueError(f'{segment_id}: 冻结正文与采用源段不一致；正文与原生引用分别核对')
    return {node: {'previous_segment_id': ident, 'source': video_origins[ident]}
            for node, ident in external_predecessors.items()}

