"""Pure lower-L2 quality, packaging, duration, scene-state, and audio validation rules."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path

from .media_types import MEDIA_EXTENSIONS
from .prompt_body import (
    reference_shot_ids,
    scene_state_requirements,
    source_shot_ids,
    submitted_prompt_body,
)

IMAGE_ASPECTS = ('identity_wardrobe', 'hands_support', 'product_geometry',
                 'ownership', 'space_objects', 'product_phase', 'boundary_state')
DRAFT_ASPECTS = ('action_chain', 'persistent_objects', 'state_boundaries',
                 'framing', 'timing', 'reference_conflicts', 'packaging_identity')
PACKAGING_ASPECTS = ('shape_proportions', 'opening_structure', 'color_marks', 'product_version')
_PROMPT_TIMECODE = re.compile(r'^\s*\d+(?:\.\d+)?\s*[-–—至]\s*\d+(?:\.\d+)?\s*秒\s*[:：]\s*')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _file(root, record, allowed_roots=(), *, file_digest=None):
    if not isinstance(record, dict) or not _text(record.get('path')):
        raise ValueError('quality_evidence: 缺少准确证据文件路径')
    root = Path(root).resolve()
    path = (root / record['path']).resolve()
    if not any(path.is_relative_to(r) for r in (root, *map(lambda p: Path(p).resolve(), allowed_roots))) or not path.is_file():
        raise ValueError('quality_evidence: 证据文件缺失或越出项目：' + record['path'])
    actual = hashlib.sha256(path.read_bytes()).hexdigest() if file_digest is None else file_digest(path)
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


def validate_draft_evidence(draft, packaging_requirements=()):
    evidence = draft.get('quality_evidence')
    _observations(evidence, DRAFT_ASPECTS)
    if evidence.get('input_sha256') != fingerprint(draft_binding(draft)):
        raise ValueError('quality_evidence: 正文、参数或有序引用已变化；旧检查失效')
    validate_packaging_scope(draft, packaging_requirements)
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


def validate_product_reference(root, product, *, allowed_roots=(), file_digest=None):
    """Byte-bound declared identity, not an inference of SKU from pixels."""
    if (not isinstance(product, dict) or product.get('reference_role') != 'packaging'
            or not _text(product.get('evidence'))
            or not isinstance(product.get('coverage'), list)
            or set(product['coverage']) != set(PACKAGING_ASPECTS)):
        raise ValueError('packaging: 缺明确商品/SKU/版本、来源依据或完整包装覆盖')
    identity = product_identity(product)
    _file(root, product.get('source'), allowed_roots, file_digest=file_digest)
    if 'physical_variant' in identity:
        _file(root, identity['physical_variant']['basis']['source'], allowed_roots, file_digest=file_digest)
    if ('packaging_reference_revision' in identity
            and identity['packaging_reference_revision']['sha256'] != product['source']['sha256']):
        raise ValueError('packaging: 包装原件版本与实际来源字节不一致')


def validate_registered_product_reference(root, reference, artifact, *, allowed_roots=(), file_digest=None):
    declared, registered = reference.get('product_reference'), artifact.get('product_reference')
    if any(isinstance(p, dict) and p.get('reference_role') == 'packaging' for p in (declared, registered)):
        if declared != registered:
            raise ValueError('packaging: 引用商品/SKU/版本与当前已登记来源不符，正确hash不能替代')
        validate_product_reference(root, registered, allowed_roots=allowed_roots, file_digest=file_digest)


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


def required_visuals(storyboard, *, shot_ids=None):
    """Read only explicit director requirements; do not infer them from prose."""
    requirements = {}
    for scene in storyboard.get('scenes', []):
        for shot in scene.get('shots', []):
            if 'required_visuals' not in shot or (shot_ids is not None and shot.get('shot_id') not in shot_ids):
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


MAX_GENERATION_SEGMENT_SECONDS = 30


def generation_duration_values(row, parameters):
    """Collect existing generation duration aliases, excluding edit targets."""
    values = [row[key] for key in ('duration_seconds', 'generation_seconds', 'generation_duration_seconds') if key in row]
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
        lines = submitted_prompt_body(row).splitlines() if any(key in requirements for key in ids) else []
        body_lines = set(lines) | {_PROMPT_TIMECODE.sub('', line) for line in lines}
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


def validate_scene_state_references(row, requirements, *, accepted_prompt_control=False):
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
    validate_scene_state_coverage(row, needed, accepted_prompt_control=accepted_prompt_control)


def validate_scene_state_coverage(row, needed, *, accepted_prompt_control=False):
    """Check declared coverage of the applicable states, independently of observation."""
    shots = source_shot_ids(row)
    refs = row.get('reference_checks', [])
    by_entity = {}
    for state in needed:
        by_entity.setdefault(state['entity_id'], set()).add(state['state_id'])
    if not accepted_prompt_control and any(len(states) > 1 for states in by_entity.values()):
        raise ValueError('scene_state: 同段多个持久状态，逐镜原生控制未证明；明确起终态并核平台能力或在真实剧情变化处分段，不能拆开用户限定的产品连续段')
    for state in needed:
        if not accepted_prompt_control and not set(shots) <= set(state['source_shot_ids']):
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
        if not accepted_prompt_control and not required_shots <= covered:
            raise ValueError('scene_state: 新场景状态图待制作/待审或未实际引用，阻止提交；文字保持或假@不代替图片')


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
