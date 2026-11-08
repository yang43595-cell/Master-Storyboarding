"""Read-only evidence consistency checks, not computer vision or authorization.

Evidence lives on the existing image registration / delivery draft. A successful
check proves only that the supplied observations are complete and byte-bound.
It never writes approval, current status, or a generation job association.
"""
from __future__ import annotations

import hashlib
import json
import copy
import math
from datetime import datetime
from pathlib import Path

from .media_types import MEDIA_EXTENSIONS
from .prompt_body import submitted_prompt_body

IMAGE_ASPECTS = ('identity_wardrobe', 'hands_support', 'product_geometry',
                 'ownership', 'space_objects', 'product_phase', 'boundary_state')
DRAFT_ASPECTS = ('action_chain', 'persistent_objects', 'state_boundaries',
                 'framing', 'timing', 'reference_conflicts', 'packaging_identity')
PACKAGING_ASPECTS = ('shape_proportions', 'opening_structure', 'color_marks', 'product_version')


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


def validate_image_evidence(root, image_path, evidence, shot_ids, *, allowed_roots=(), reference=None,
                            product_reference=None):
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
    product = reference.get('product_reference') if reference is not None else product_reference
    if isinstance(product, dict) and product.get('reference_role') == 'packaging':
        validate_product_reference(root, product, allowed_roots=allowed_roots)
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
            'usage_checked': reference is not None, 'physical_variant_verified': False}


def image_use(reference, shot_ids):
    use = {'source_shot_ids': sorted(shot_ids),
            'roles': sorted(reference.get('roles', [reference.get('role')])),
            'entity_ids': sorted(reference.get('entity_ids', [])),
            'state_id': reference.get('state_id')}
    if 'endpoint_observations' in reference:
        use['endpoint_observations'] = copy.deepcopy(reference['endpoint_observations'])
    if 'product_reference' in reference:
        use['product_reference'] = copy.deepcopy(reference['product_reference'])
    return use


def draft_binding(draft):
    """Bind exact native submission fields; excludes evidence to avoid recursion."""
    return {key: value for key, value in draft.items() if key != 'quality_evidence'}


def validate_draft_evidence(draft):
    evidence = draft.get('quality_evidence')
    _observations(evidence, DRAFT_ASPECTS)
    if evidence.get('input_sha256') != fingerprint(draft_binding(draft)):
        raise ValueError('quality_evidence: 正文、参数或有序引用已变化；旧检查失效')
    validate_packaging_scope(draft)
    return {'verification': 'evidence_consistency_only', 'visual_quality_verified': False}


def product_identity(product):
    """Exact product variant OR explicitly bounded visible-artwork identity.

    Artwork byte revision is never a physical SKU/batch. Unknown applicability
    cannot use the bounded branch; selected source requirements still compare
    the complete identity and cannot be downgraded by the downstream draft.
    """
    import re
    if not isinstance(product, dict) or not _text(product.get('product_id')):
        raise ValueError('packaging: 缺明确商品身份')
    bounded = {'packaging_reference_revision', 'physical_variant'}
    if bounded & set(product):
        revision, variant = product.get('packaging_reference_revision'), product.get('physical_variant')
        if ('sku' in product or 'version' in product or not _text(product.get('product_name'))
                or not isinstance(revision, dict) or set(revision) != {'sha256'}
                or not isinstance(revision['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', revision['sha256'])
                or not isinstance(variant, dict) or set(variant) != {'status', 'scope', 'basis'}
                or variant['status'] != 'not_applicable' or variant['scope'] != 'visible_packaging_only'
                or not isinstance(variant['basis'], dict)):
            raise ValueError('packaging: 可见包装范围须有准确原件版本及物理变体不适用依据，不能混填/猜测SKU')
        basis = variant['basis']
        opened_interior = (isinstance(basis.get('visible_faces'), list)
                           and 'opened_interior' in basis['visible_faces'])
        excluded = ['back_face', 'formula_or_specification', 'efficacy_claims',
                    'opening_process' if opened_interior else 'opened_packaging']
        if (set(basis) != {'source', 'source_shot_ids', 'rationale', 'visible_faces', 'excluded_uses'}
                or not isinstance(basis['source'], dict) or set(basis['source']) != {'path', 'sha256'}
                or not _text(basis['source']['path']) or not isinstance(basis['source']['sha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', basis['source']['sha256'])
                or not _text(basis['rationale'])
                or not isinstance(basis['source_shot_ids'], list) or not basis['source_shot_ids']
                or not all(_text(v) for v in basis['source_shot_ids'])
                or len(set(basis['source_shot_ids'])) != len(basis['source_shot_ids'])
                or not isinstance(basis['visible_faces'], list) or not basis['visible_faces']
                or not all(v in {'front', 'top', 'right', 'left', 'opened_interior'} for v in basis['visible_faces'])
                or len(set(basis['visible_faces'])) != len(basis['visible_faces'])
                or not (opened_interior or 'front' in basis['visible_faces'])
                or not isinstance(basis['excluded_uses'], list)
                or sorted(basis['excluded_uses']) != sorted(excluded)):
            raise ValueError('packaging: 物理变体不适用须绑定来源字节、镜号和有限可见面用途')
        return {key: product[key] for key in ('product_id', 'product_name', 'packaging_reference_revision', 'physical_variant')}
    if any(not _text(product.get(key)) for key in ('sku', 'version')):
        raise ValueError('packaging: 缺本镜目标商品/SKU/版本；未知不能等同物理变体不适用')
    return {key: product[key] for key in ('product_id', 'sku', 'version')}


def validate_product_reference(root, product, *, allowed_roots=()):
    """Byte-bound declared identity, not an inference of SKU from pixels."""
    if (not isinstance(product, dict) or product.get('reference_role') != 'packaging'
            or not _text(product.get('evidence'))
            or not isinstance(product.get('coverage'), list)
            or set(product['coverage']) != set(PACKAGING_ASPECTS)):
        raise ValueError('packaging: 缺明确商品/SKU/版本、来源依据或完整包装覆盖')
    identity = product_identity(product)
    _file(root, product.get('source'), allowed_roots)
    if 'physical_variant' in identity:
        _file(root, identity['physical_variant']['basis']['source'], allowed_roots)
    if ('packaging_reference_revision' in identity
            and identity['packaging_reference_revision']['sha256'] != product['source']['sha256']):
        raise ValueError('packaging: 包装原件版本与实际来源字节不一致')


def validate_registered_product_reference(root, reference, artifact, *, allowed_roots=()):
    declared, registered = reference.get('product_reference'), artifact.get('product_reference')
    if any(isinstance(p, dict) and p.get('reference_role') == 'packaging' for p in (declared, registered)):
        if declared != registered:
            raise ValueError('packaging: 引用商品/SKU/版本与当前已登记来源不符，正确hash不能替代')
        validate_product_reference(root, registered, allowed_roots=allowed_roots)


def validate_packaging_scope(draft, requirements=()):
    """Explicit assessment on the existing QA; no box-name classifier.

    Omission is not a no-packaging decision. A truthful source/visual inventory
    remains the reviewer's job; this code cannot discover an undeclared object.
    """
    check = (draft.get('quality_evidence') or {}).get('checks', {}).get('packaging_identity')
    if not isinstance(check, dict) or check.get('scope') not in {'none', 'present'}:
        raise ValueError('packaging: 缺本段明确包装归属盘点；旧动作/草稿QA不能晋升')
    items = check.get('items')
    if not isinstance(items, list) or (check['scope'] == 'none') != (items == []):
        raise ValueError('packaging: 包装检查范围与逐项声明不符')
    if check['scope'] == 'none' and check.get('result') != 'not_applicable':
        raise ValueError('packaging: 无包装仅记不适用，不报包装还原通过')
    packaging_refs = [r for r in draft.get('reference_checks', []) if isinstance(r, dict)
                      and isinstance(r.get('product_reference'), dict)
                      and r['product_reference'].get('reference_role') == 'packaging']
    if check['scope'] == 'none' and packaging_refs:
        raise ValueError('packaging: 声明无包装但活动引用明确用于包装，须核对盘点')
    if items and check.get('result') != 'pass':
        raise ValueError('packaging: 出镜包装必须逐项核对，不能标不适用')
    by_entity = {}
    shots = source_shot_ids(draft)
    if requirements and not shots:
        raise ValueError('packaging: 来源含包装要求但本段缺准确镜号，不能跳过')
    prompt = submitted_prompt_body(draft)
    for item in items:
        if (not isinstance(item, dict) or not _text(item.get('entity_id'))
                or item['entity_id'] in by_entity or not _text(item.get('source_basis'))
                or item.get('classification') not in {'ordinary_prop', 'product'}
                or not isinstance(item.get('source_shot_ids'), list) or not item['source_shot_ids']
                or not set(item['source_shot_ids']) <= set(shots)):
            raise ValueError('packaging: 未知/缺失归属、来源或镜号不能作为包装通过')
        by_entity[item['entity_id']] = item
        if item['classification'] == 'ordinary_prop':
            if item.get('product_reference') or item.get('prompt_claim') != 'ordinary_prop':
                raise ValueError('packaging: 普通道具声明与商品要求冲突')
            continue
        target = item.get('product_reference')
        target_identity = product_identity(target)
        if ('physical_variant' in target_identity
                and not set(item['source_shot_ids']) <= set(target_identity['physical_variant']['basis']['source_shot_ids'])):
            raise ValueError('packaging: 可见包装不适用依据未覆盖本镜用途')
        quote = item.get('prompt_quote')
        if item.get('prompt_claim') != 'product' or not _text(quote) or quote not in prompt:
            raise ValueError('packaging: 正文排除商品或缺准确逐字商品用途核对')
        # Bounded known contradiction in the quoted decision, not inference
        # from the word box. Other semantic contradictions require real review.
        import re
        if re.search(r'(?:不认定为|不把.{0,40}确定为|不属于|不是).{0,40}(?:商品|产品)', quote):
            raise ValueError('packaging: 正文排除商品身份与商品还原要求冲突')
        refs = [r for r in draft.get('reference_checks', []) if isinstance(r, dict)
                and item['entity_id'] in r.get('entity_ids', [])
                and isinstance(r.get('product_reference'), dict)
                and r['product_reference'].get('reference_role') == 'packaging'
                and product_identity(r['product_reference']) == target_identity
                and 'appearance' in r.get('roles', [r.get('role')]) and r.get('kind') == 'image']
        if not refs:
            raise ValueError('packaging: 有序引用虽一致但未覆盖本镜准确商品包装')
        covered = set()
        for ref in refs:
            used = set(reference_shot_ids(ref, draft))
            if not used <= set(shots):
                raise ValueError('packaging: 包装引用镜号越出当前段')
            covered.update(used)
        if not set(item['source_shot_ids']) <= covered:
            raise ValueError('packaging: 准确商品包装参考未覆盖所需镜号')
    # A composite image may contain people and ordinary props as well as the
    # product. Its whole entity_ids list is NOT a list of packaging entities.
    # Require an explicit product inventory target for each packaging reference;
    # forward checks above bind every declared product back to a matching use.
    # Undeclared visual objects remain a semantic QA responsibility, not inferred
    # from entity names, image contents or the ordinary-prop label.
    for ref in packaging_refs:
        used = set(reference_shot_ids(ref, draft))
        entities = ref.get('entity_ids')
        if not isinstance(entities, list) or not entities:
            raise ValueError('packaging: 活动包装引用缺准确实体归属')
        matches = [item for item in items if item['classification'] == 'product'
                   and item['entity_id'] in entities
                   and product_identity(item['product_reference']) == product_identity(ref['product_reference'])
                   and used <= set(item['source_shot_ids'])]
        if not matches:
            raise ValueError('packaging: 活动包装引用缺同实体/商品/本段镜号的明确商品盘点')
    for required in requirements:
        needed = set(required['source_shot_ids']) & set(shots)
        if not needed:
            continue
        item = by_entity.get(required['entity_id'])
        if (item is None or not needed <= set(item['source_shot_ids'])
                or item['classification'] != required['classification']
                or item.get('product_reference') != required.get('product_reference')):
            raise ValueError('packaging: 来源导演的包装要求被省略、降级或换版')
    return items


def packaging_requirements(payload):
    """Only current selected director shots; never scan historical QA as truth."""
    rows = []
    for scene in payload.get('scenes', []):
        for shot in scene.get('shots', []):
            declared = shot.get('packaging_requirements', [])
            if not isinstance(declared, list):
                raise ValueError('packaging: 来源包装要求须为数组')
            for item in declared:
                if (not isinstance(item, dict) or not _text(item.get('entity_id'))
                        or item.get('classification') not in {'ordinary_prop', 'product', 'unknown'}
                        or not _text(item.get('source_basis'))):
                    raise ValueError('packaging: 来源包装归属声明无效')
                rows.append({**item, 'source_shot_ids': [shot['shot_id']]})
    return rows


def native_input_binding(row):
    """Current business input, never the inherited readback claim itself."""
    return {key: value for key, value in row.items()
            if key not in {'native_binding', 'quality_evidence'}}


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
    from .artifacts import load_manifest
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
    for key in ('drafts', 'generation_segments', 'creative_sections'):
        if key in value:
            validate_native_binding_claims(value[key], root, revision, target, project_id)


def source_shot_ids(row):
    """Resolve both existing aliases as ordered IDs; absence stays unspecified.

    Duplicate IDs inside one segment and conflicting aliases are invalid. A shot
    may recur across segments (continuous subranges); this is not duplicate use.
    """
    direct = row.get('source_shot_ids')
    mapped = row.get('source_shots')
    if mapped is not None:
        if not isinstance(mapped, list):
            raise ValueError('scene_state: source_shots 必须为准确镜号数组')
        mapped = [s.get('shot_id') if isinstance(s, dict) else s for s in mapped]
    for ids in (direct, mapped):
        if ids is not None and (not isinstance(ids, list) or not ids or
                                any(not _text(s) for s in ids) or len(set(ids)) != len(ids)):
            raise ValueError('scene_state: 缺准确、唯一的 source_shots/source_shot_ids')
    if direct is not None and mapped is not None and direct != mapped:
        raise ValueError('scene_state: source_shots 与 source_shot_ids 映射不一致')
    return direct if direct is not None else mapped or []


def required_visuals(storyboard):
    """Read only explicit director requirements; do not infer them from prose."""
    requirements = {}
    for scene in storyboard.get('scenes', []):
        for shot in scene.get('shots', []):
            if 'required_visuals' not in shot:
                continue
            items = shot['required_visuals']
            shot_id = shot['shot_id']
            if (not isinstance(items, list) or not items or any(
                    not isinstance(item, dict) or set(item) - {'prompt_clause', 'min_seconds', 'action_reference'}
                    or not _text(item.get('prompt_clause'))
                    or item['prompt_clause'] != item['prompt_clause'].strip()
                    or '\n' in item['prompt_clause'] or '\r' in item['prompt_clause']
                    or ('min_seconds' in item and not _positive_seconds(item['min_seconds']))
                    for item in items)
                    or len({item['prompt_clause'] for item in items}) != len(items)
                    or sum('min_seconds' in item for item in items) > 1):
                raise ValueError(f'required_visuals: {shot_id} 必保画面须有准确、唯一的单行正文指令及有效时长')
            for item in items:
                if 'action_reference' not in item:
                    continue
                action = item['action_reference']
                assets = shot.get('source_asset_requirement_ids')
                if (not isinstance(action, dict) or set(action) != {'product_ast_id', 'action_key'}
                        or any(not _text(value) or value != value.strip() for value in action.values())
                        or not isinstance(assets, list) or action['product_ast_id'] not in assets):
                    raise ValueError(f'action_reference: {shot_id} 缺本镜产品资产编号或准确动作名')
            requirements[shot_id] = items
    return requirements


def _positive_seconds(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def generation_duration_values(row, parameters):
    """Collect existing generation duration aliases, excluding edit targets."""
    values = [row[key] for key in ('duration_seconds', 'generation_seconds') if key in row]
    values.extend(parameters[key] for key in ('duration_seconds', 'duration') if key in parameters)
    return values


def validate_shot_durations(row, parameters):
    """Check a declared schedule against the same budget in source and delivery."""
    identity = row.get('segment_id') or row.get('node_id')
    parts = row.get('shot_durations')
    if not isinstance(parts, list) or not parts or not all(_positive_seconds(part) for part in parts):
        raise ValueError(f'{identity}: 逐镜时长必须是非空正数数组')
    if not isinstance(parameters, dict):
        raise ValueError(f'{identity}: 生成参数结构无效')
    durations = generation_duration_values(row, parameters)
    if not durations:
        return  # Legacy source rows can carry a schedule without a generation budget.
    duration = durations[0]
    if any(not _positive_seconds(value) or value != duration for value in durations):
        raise ValueError(f'{identity}: 生成时长声明无效或冲突')
    total = sum(parts)
    if not math.isclose(total, duration, abs_tol=1e-6):
        raise ValueError(f'{identity}: 片段总长 {duration:g} 秒，逐镜合计 {total:g} 秒')


def _is_action_video(reference):
    if not isinstance(reference, dict) or reference.get('kind') != 'video':
        return False
    roles = reference.get('roles', [reference.get('role', reference.get('primary_role'))])
    return isinstance(roles, list) and 'action' in roles


def _has_action_reference(row, shot_id, action, project, delivery_sources):
    """Check the declared source clip and, for delivery, the actual bound bytes."""
    product, action_key = action['product_ast_id'], action['action_key']
    source, root = row, project
    delivered_hashes = None
    if delivery_sources is not None:
        segment = row.get('source_segment_id', row.get('node_id'))
        source, root = delivery_sources[segment]
        checks = row.get('reference_checks', [])
        if not isinstance(checks, list):
            return False
        delivered_hashes = {
            ref['sha256'] for ref in checks
            if _is_action_video(ref) and _text(ref.get('sha256'))
            and isinstance(ref.get('entity_ids'), list) and product in ref['entity_ids']
            and shot_id in reference_shot_ids(ref, row)
        }
    if root is None:
        return False
    materials = source.get('materials', source.get('candidate_upload_order', []))
    if not isinstance(materials, list):
        return False
    for ref in materials:
        if not _is_action_video(ref) or ref.get('asset_id') != product:
            continue
        uses = ref.get('action_references')
        if not isinstance(uses, list) or {'shot_id': shot_id, 'action_key': action_key} not in uses:
            continue
        if (not isinstance(ref.get('source_shot_ids'), list) or shot_id not in reference_shot_ids(ref, source)
                or not _text(ref.get('path')) or Path(ref['path']).suffix.lower() not in MEDIA_EXTENSIONS['video']
                or (delivered_hashes is not None and ref.get('sha256') not in delivered_hashes)):
            continue
        try:
            if _file(root, ref).stat().st_size > 0:
                return True
        except (ValueError, OSError):
            continue
    return False


def validate_required_visuals(rows, requirements, *, shot_order=(), project=None,
                              delivery_sources=None, action_shots=None):
    """Bind approved clauses to actual body and budget, never claim visual QA."""
    if not requirements:
        return
    by_shot = {}
    for row in rows:
        ids = source_shot_ids(row)
        body_lines = submitted_prompt_body(row).splitlines() if any(key in requirements for key in ids) else []
        for index, shot_id in enumerate(ids):
            by_shot.setdefault(shot_id, []).append((row, index, len(ids), body_lines))
    scoped = set(by_shot)
    positions = [index for index, shot_id in enumerate(shot_order) if shot_id in scoped]
    if positions:
        scoped.update(shot_order[positions[0]:positions[-1] + 1])
    for shot_id, items in requirements.items():
        if shot_id not in scoped:
            continue
        carriers = by_shot.get(shot_id, [])
        for item in items:
            clause = item['prompt_clause']
            matches = [(row, index, count) for row, index, count, lines in carriers if clause in lines]
            if not matches:
                raise ValueError(f'required_visuals: {shot_id} 正文缺少必保画面：{clause}')
            minimum = item.get('min_seconds')
            if minimum is not None:
                matches = [(row, index, count) for row, index, count in matches
                           if isinstance(parts := row.get('shot_durations'), list) and len(parts) == count
                           and all(_positive_seconds(part) for part in parts) and parts[index] >= minimum]
                if not matches:
                    raise ValueError(f'required_visuals: {shot_id} 需按镜号保留至少 {minimum:g} 秒：{clause}')
            action = item.get('action_reference')
            if (action is not None and (action_shots is None or shot_id in action_shots)
                    and not any(_has_action_reference(row, shot_id, action, project, delivery_sources)
                                for row, _, _ in matches)):
                raise ValueError(f"action_reference: {shot_id} 缺实际可用的{action['action_key']}动作参考"
                                 f"（{action['product_ast_id']}）")


def scene_state_requirements(payload):
    """Read persistent changes from the existing director JSON; no NLP/vision.

    An omitted declaration is NOT proof that the story has no scene changes.
    The creative reader must detect and record these before image/video work.
    """
    requirements = []
    for scene in payload.get('scenes', []):
        if 'scene_states' not in scene:
            continue
        states = scene['scene_states']
        if not isinstance(states, list):
            raise ValueError('scene_state: scene_states 必须是数组')
        shots = [s['shot_id'] for s in scene.get('shots', [])]
        known, occupied, latest = set(), set(), {}
        for state in states:
            if (not isinstance(state, dict) or any(not _text(state.get(k)) for k in
                    ('state_id', 'entity_id', 'change_event', 'description'))
                    or 'previous_state_id' not in state
                    or (state['previous_state_id'] is not None and not _text(state['previous_state_id']))
                    or state['state_id'] == state['previous_state_id']):
                raise ValueError('scene_state: 缺旧态→事件→新态及场景实体')
            for key in ('preserve', 'changes', 'source_shot_ids'):
                values = state.get(key)
                if not isinstance(values, list) or not values or any(not _text(v) for v in values):
                    raise ValueError('scene_state: 缺不变量、变化或后续镜号范围')
            ids = state['source_shot_ids']
            ident = (state['entity_id'], state['state_id'])
            if ident in known or len(set(ids)) != len(ids) or not set(ids) <= set(shots):
                raise ValueError('scene_state: 重复状态或镜号越出来源场景')
            positions = [shots.index(shot) for shot in ids]
            prior = latest.get(state['entity_id'])
            if positions != sorted(positions) or (prior and (
                    state['previous_state_id'] != prior['state_id'] or
                    shots.index(prior['source_shot_ids'][-1]) >= positions[0])):
                raise ValueError('scene_state: 状态链前驱或镜号顺序不一致')
            latest[state['entity_id']] = state
            pairs = {(state['entity_id'], shot) for shot in ids}
            if occupied & pairs:
                raise ValueError('scene_state: 同镜同实体状态范围冲突；明确起终变化后再处理提交')
            known.add(ident)
            occupied.update(pairs)
            requirements.append(state)
    return requirements


def reference_shot_ids(reference, row):
    """A scoped use may narrow a multi-shot draft, never widen its authority."""
    whole = source_shot_ids(row)
    ids = reference.get('source_shot_ids', whole)
    if (not isinstance(ids, list) or not ids or any(not _text(s) for s in ids)
            or len(set(ids)) != len(ids) or not set(ids) <= set(whole)):
        raise ValueError('scene_state: reference 镜号范围不属于当前生成段')
    return ids


def validate_scene_state_references(row, requirements):
    """Consumer-side declared state coverage, not image or native control proof."""
    shots = source_shot_ids(row)
    refs = row.get('reference_checks', [])
    environment = any('layout' in r.get('roles', [r.get('role')]) for r in refs)
    assessment = row.get('quality_evidence', {}).get('checks', {}).get('persistent_objects', {})
    conclusion = assessment.get('scene_state_assessment')
    needed = [s for s in requirements if set(shots) & set(s['source_shot_ids'])]
    if environment or needed or conclusion is not None:
        if (conclusion not in {'changed', 'unchanged'} or
                assessment.get('source_shot_ids') != shots or not shots or
                not _text(assessment.get('observation')) or not _text(assessment.get('locator'))):
            raise ValueError('scene_state: 活动环境参考缺逐镜持久状态判断，待检查；省略scene_states不等于无变化')
        if (conclusion == 'changed') != bool(needed):
            raise ValueError('scene_state: 持久状态判断与源状态链不一致；不能删state声明绕过后态图')
    if not requirements:
        return
    if not shots:
        raise ValueError('scene_state: 有持久场景状态时必须提供完整 source_shots 映射')
    validate_scene_state_coverage(row, needed)


def validate_scene_state_coverage(row, needed):
    """Check declared coverage of the applicable states, independently of observation."""
    shots = source_shot_ids(row)
    refs = row.get('reference_checks', [])
    by_entity = {}
    for state in needed:
        by_entity.setdefault(state['entity_id'], set()).add(state['state_id'])
    if any(len(states) > 1 for states in by_entity.values()):
        raise ValueError('scene_state: 同段多个持久状态，逐镜原生控制未证明；明确起终态并核平台能力或在真实剧情变化处分段，不能拆开用户限定的产品连续段')
    for state in needed:
        if not set(shots) <= set(state['source_shot_ids']):
            raise ValueError('scene_state: 同段含变化前后镜号，文字用途范围不等于原生逐镜控制；先核真实起终控制或合法叙事分段')
    for state in needed:
        required_shots = set(shots) & set(state['source_shot_ids'])
        covered = set()
        for ref in refs:
            if state['entity_id'] not in ref.get('entity_ids', []):
                continue
            roles = set(ref.get('roles', [ref.get('role')]))
            # A stale full scene remains a conflicting active reference even if
            # the author merely relabels it as layout/appearance.
            if not roles & {'state_reference', 'layout', 'appearance', 'start_frame', 'end_frame'}:
                continue
            used = set(reference_shot_ids(ref, row))
            if used & required_shots:
                if ref.get('state_id') != state['state_id']:
                    raise ValueError('scene_state: 旧态/基础环境图仍为变化后活动全景参考；保留来源历史，不得继续绑定')
                if not used <= set(state['source_shot_ids']):
                    raise ValueError('scene_state: 新态图用途越出源导演状态范围')
                if ref.get('kind') == 'image' and 'state_reference' in roles:
                    covered.update(used)
        if not required_shots <= covered:
            raise ValueError('scene_state: 新场景状态图待制作/待审或未实际引用，阻止提交；文字保持或假@不代替图片')


def validate_scene_source_mapping(rows, sources, project, *, check_submitted_body=False):
    """Bind draft shot mapping to byte-checked source segments and director.

    sources contains (authority reference, resolved path, decoded JSON). Source
    director links must be present in source_artifacts: omission cannot downgrade
    the state check to a legacy/no-change path.
    """
    videos = []
    video_origins = {}
    all_shots = set()
    visual_requirements = {}
    has_states = False
    has_packaging = False
    delivery_shots = {shot for row in rows for shot in source_shot_ids(row)}
    for ref, path, payload in sources:
        linked_director = False
        if not isinstance(payload, dict):
            continue
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
            if rows and ('generation_segments' in payload or 'creative_sections' in payload):
                from .generation_boundaries import validate_generation_boundaries
                validate_generation_boundaries(payload, value, Path(ref.get('project_path', project)),
                                               action_shots=delivery_shots)
                linked_director = True
        segments = payload.get('generation_segments')
        if segments is None and isinstance(payload.get('creative_sections'), list):
            segments = [s for section in payload['creative_sections'] for s in section.get('generation_segments', [])]
        if segments:
            if rows and not linked_director and isinstance(payload.get('scenes'), list) and payload['scenes']:
                # Existing composite source snapshots carry their director and
                # segment mapping in the same byte-checked artifact.
                from .generation_boundaries import validate_generation_boundaries
                validate_generation_boundaries(payload, payload, Path(ref.get('project_path', project)),
                                               action_shots=delivery_shots)
                linked_director = True
            if rows and not linked_director:
                raise ValueError('generation_boundary: 生成段源必须绑定准确导演，不能删来源绕过分段检查')
            from .generation_boundaries import validate_delivery_parameters
            validate_delivery_parameters(rows, payload, Path(ref.get('project_path', project)))
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
        from .generation_boundaries import validate_delivery_cutpoints
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
        if videos:
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
            if body and submitted_prompt_body(row) != body:
                raise ValueError(f'{segment_id}: 冻结正文与采用源段不一致；正文与原生引用分别核对')
    return {node: {'previous_segment_id': ident, 'source': video_origins[ident]}
            for node, ident in external_predecessors.items()}


def director_shot_audio(boards):
    shot_audio = {}
    for board in boards:
        for scene in board.get('scenes', []):
            for shot in scene.get('shots', []):
                if isinstance(shot.get('audio'), dict) and 'dialogue' in shot['audio']:
                    key = shot['shot_id']
                    if key in shot_audio:
                        raise ValueError('source_audio: 来源导演镜号冲突')
                    shot_audio[key] = shot['audio']['dialogue']
    return shot_audio


def replay_source_audio(segments, shot_audio):
    """Resolve existing source_audio fragments with a transient per-shot cursor.

    No NLP, synthesized speech, new dialogue ledger, or native job guarantee.
    Call with the complete selected source segments (not an arbitrary mid-shot
    excerpt). Context/prompt prose is deliberately not a source of utterances.
    The return value is an audit projection, not another business authority.
    """
    cursors, selected, result = {}, [], []
    known_order = list(shot_audio)
    last_position = -1
    for segment in segments:
        ids = source_shot_ids(segment)
        audio = segment.get('source_audio')
        if not ids or not isinstance(audio, list) or any(not isinstance(a, dict) for a in audio):
            raise ValueError('source_audio: 缺本段准确镜号和对白片段')
        # Existing prompts also store flat fragments with source_shot_id.
        # Project that form into shot groups without reordering any utterance.
        if not audio or any('source_shot_id' in part for part in audio):
            groups = {shot: [] for shot in ids}
            order = {shot: i for i, shot in enumerate(ids)}
            previous = -1
            for part in audio:
                shot = part.get('source_shot_id')
                if not isinstance(shot, str) or shot not in order or order[shot] < previous:
                    raise ValueError('source_audio: 平铺片段必须按本段准确镜号顺序排列')
                previous = order[shot]
                groups[shot].append(part)
            audio = [{'shot_id': shot, 'dialogue': groups[shot]} for shot in ids]
        if [a.get('shot_id') for a in audio] != ids:
            raise ValueError('source_audio: 分组片段必须逐镜对应本段完整source_shots')
        output = []
        for item in audio:
            shot = item['shot_id']
            if shot not in shot_audio:
                raise ValueError('source_audio: 镜号不在准确来源导演中')
            position = known_order.index(shot)
            if position < last_position:
                raise ValueError('source_audio: 来源镜头顺序倒退')
            last_position = position
            if shot not in selected:
                selected.append(shot)
            expected = shot_audio[shot]
            placements = item.get('dialogue')
            if not isinstance(placements, list):
                raise ValueError('source_audio: 缺准确dialogue片段')
            index, offset = cursors.get(shot, (0, 0))
            for part in placements:
                if (not isinstance(part, dict) or not _text(part.get('text')) or
                        index >= len(expected)):
                    raise ValueError('source_audio: 片段为空或重复发声')
                original = expected[index]
                if any(part.get(k) != original.get(k) for k in
                       ('dialogue_id', 'speaker_id', 'presentation')):
                    raise ValueError('source_audio: 源ID/说话人/声画职责不一致')
                text = part['text']
                if not original['text'].startswith(text, offset):
                    raise ValueError('source_audio: 片段重复、漏字或改写，不得按ID重取整句')
                output.append({k: part[k] for k in
                               ('dialogue_id', 'speaker_id', 'presentation', 'text')})
                offset += len(text)
                if offset == len(original['text']):
                    index, offset = index + 1, 0
            cursors[shot] = (index, offset)
        result.append({'segment_id': segment.get('segment_id'), 'dialogue': output})
    for shot in selected:
        if cursors[shot] != (len(shot_audio[shot]), 0):
            raise ValueError('source_audio: 所选源镜对白没有完整覆盖；片段游标尚未到句末')
    return result
