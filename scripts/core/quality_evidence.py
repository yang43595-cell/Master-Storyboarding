"""Read-only evidence consistency checks, not computer vision or authorization.

Evidence lives on the existing image registration / delivery draft. A successful
check proves only that the supplied observations are complete and byte-bound.
It never writes approval, current status, or a generation job association.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

IMAGE_ASPECTS = ('identity_wardrobe', 'hands_support', 'product_geometry',
                 'ownership', 'space_objects', 'product_phase', 'boundary_state')
DRAFT_ASPECTS = ('action_chain', 'persistent_objects', 'state_boundaries',
                 'framing', 'timing', 'reference_conflicts')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _file(root, record, allowed_roots=()):
    if not isinstance(record, dict) or not _text(record.get('path')):
        raise ValueError('quality_evidence: 缺少准确证据文件路径')
    root = Path(root).resolve()
    path = (root / record['path']).resolve()
    if not any(path.is_relative_to(r) for r in (root, *map(lambda p: Path(p).resolve(), allowed_roots))) or not path.is_file():
        raise ValueError('quality_evidence: 证据文件缺失或越出项目：' + record['path'])
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if record.get('sha256') != actual:
        raise ValueError('quality_evidence: 文件指纹已变化或缺失：' + record['path'])
    return path


def _observations(evidence, aspects):
    if not isinstance(evidence, dict):
        raise ValueError('quality_evidence: 缺少本版本、本用途的检查；可继续候选，不能据此放行')
    if evidence.get('status') != 'pass':
        raise ValueError('quality_evidence: fail/unknown/未核不得作为活动参考或生成放行依据')
    if not _text(evidence.get('observer')) or not _text(evidence.get('checked_at')):
        raise ValueError('quality_evidence: 缺检查者或带时区检查时间')
    try:
        instant = datetime.fromisoformat(evidence['checked_at'].replace('Z', '+00:00'))
        if instant.tzinfo is None:
            raise ValueError()
    except ValueError as exc:
        raise ValueError('quality_evidence: 检查时间须含时区') from exc
    checks = evidence.get('checks')
    if not isinstance(checks, dict) or set(checks) != set(aspects):
        raise ValueError('quality_evidence: 检查范围不完整：' + ', '.join(aspects))
    for aspect, check in checks.items():
        if not isinstance(check, dict) or check.get('result') not in {'pass', 'not_applicable'}:
            raise ValueError('quality_evidence: 未通过检查项：' + aspect)
        if not _text(check.get('observation')) or not _text(check.get('locator')):
            raise ValueError('quality_evidence: 缺具体观察及来源位置：' + aspect)
    # Text is an assertion, never proof that a person/agent actually inspected it.


def validate_image_evidence(root, image_path, evidence, shot_ids, *, allowed_roots=(), reference=None):
    """Require exact image/source bytes and explicit observations for this use."""
    _observations(evidence, IMAGE_ASPECTS)
    path = Path(image_path).resolve()
    root = Path(root).resolve()
    _file(root, {'path': str(path), 'sha256': evidence.get('image_sha256')}, allowed_roots)
    if not isinstance(evidence.get('source_files'), list) or not evidence['source_files']:
        raise ValueError('quality_evidence: 缺实际对照的源文件及指纹')
    for source in evidence['source_files']:
        _file(root, source, allowed_roots)
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
        if not isinstance(uses, list) or expected not in uses:
            raise ValueError('quality_evidence: 本镜参考职责/实体/状态用途未核或已变化，旧图审失效')
    return {'verification': 'evidence_consistency_only', 'visual_quality_verified': False,
            'usage_checked': reference is not None}


def image_use(reference, shot_ids):
    return {'source_shot_ids': sorted(shot_ids),
            'roles': sorted(reference.get('roles', [reference.get('role')])),
            'entity_ids': sorted(reference.get('entity_ids', [])),
            'state_id': reference.get('state_id')}


def draft_binding(draft):
    """Bind exact native submission fields; excludes evidence to avoid recursion."""
    return {key: value for key, value in draft.items() if key != 'quality_evidence'}


def validate_draft_evidence(draft):
    evidence = draft.get('quality_evidence')
    _observations(evidence, DRAFT_ASPECTS)
    if evidence.get('input_sha256') != fingerprint(draft_binding(draft)):
        raise ValueError('quality_evidence: 正文、参数或有序引用已变化；旧检查失效')
    return {'verification': 'evidence_consistency_only', 'visual_quality_verified': False}


def native_input_binding(row):
    """Current business input, never the inherited readback claim itself."""
    return {key: value for key, value in row.items()
            if key not in {'native_binding', 'quality_evidence'}}


def validate_native_binding_claims(value, root, revision=None, target=None):
    """Reject stale verified claims without modifying historical receipts/drafts.

    Missing/not_bound/unknown permits an explicitly unverified candidate; this
    checker never generates a verified status or establishes remote provenance.
    Evidence uses the existing canvas readback's after_refresh/drafts fields.
    """
    if isinstance(value, list):
        for item in value:
            validate_native_binding_claims(item, root, revision, target)
        return
    if not isinstance(value, dict):
        return
    revision = value.get('revision') if revision is None else revision
    if target is None:
        target = value.get('target_url') or (value.get('target_canvas') or {}).get('url')
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
            if not _text(value.get('prompt')) or not isinstance(value.get('parameters'), dict):
                raise ValueError('native_binding: verified仅可绑定完整当前段正文/参数，不接受整包摘要冒充')
            expected_media = value.get('ordered_media')
            if expected_media is None:
                nodes, media = binding.get('ordered_asset_node_ids'), binding.get('ordered_native_media_ids')
                if not isinstance(nodes, list) or not isinstance(media, list) or len(nodes) != len(media):
                    raise ValueError('native_binding: 缺有序原生引用身份')
                expected_media = [dict(node_id=n, media_id=m) for n, m in zip(nodes, media)]
            if not isinstance(expected_media, list) or any(not isinstance(m, dict)
                    or not _text(m.get('node_id')) or not _text(m.get('media_id')) for m in expected_media):
                raise ValueError('native_binding: 当前有序引用身份无效')
            for material in value.get('materials', []):
                if isinstance(material, dict) and material.get('native_entity_verified') is True:
                    if {'node_id': material.get('native_node_id'), 'media_id': material.get('native_media_id')} not in expected_media:
                        raise ValueError('native_binding: 已核materials声明与当前原生实体不一致')
            if (actual.get('prompt') != value.get('prompt') or actual.get('parameters') != value.get('parameters')
                    or actual.get('ordered_media') != expected_media):
                raise ValueError('native_binding: 本稿正文/参数/有序引用与回读不一致')
            mentions = actual.get('native_mentions')
            entity_type = value.get('native_entity_type') or binding.get('native_entity_type')
            if (not _text(entity_type) or not isinstance(mentions, list) or any(not isinstance(m, dict)
                    or m.get('entity_type') != entity_type or m.get('native_noneditable_entity') is not True for m in mentions)
                    or [{'node_id': m.get('node_id'), 'media_id': m.get('media_id')} for m in mentions] != expected_media):
                raise ValueError('native_binding: 缺本节点原生实体回读，文字或图边不代替')
        elif status not in {None, 'unknown', 'not_bound', 'unverified', 'not_checked', 'stale'}:
            raise ValueError('native_binding: 未识别状态不能冒充已回读')
    # Historical receipts/archives remain data, not current verified claims.
    for key in ('drafts', 'generation_segments', 'creative_sections'):
        if key in value:
            validate_native_binding_claims(value[key], root, revision, target)
