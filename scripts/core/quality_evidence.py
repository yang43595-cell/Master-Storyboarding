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

from .prompt_body import submitted_prompt_body

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
            prompt = submitted_prompt_body(value)
            if not _text(prompt) or not isinstance(value.get('parameters'), dict):
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
            if (actual.get('prompt') != prompt or actual.get('parameters') != value.get('parameters')
                    or actual.get('ordered_media') != expected_media):
                raise ValueError('native_binding: 本稿正文/参数/有序引用与回读不一致')
            mentions = actual.get('native_mentions')
            entity_type = value.get('native_entity_type') or binding.get('native_entity_type')
            from .native_references import reference_readback_status
            if reference_readback_status(expected_media, mentions, entity_type) == 'failed':
                raise ValueError('native_binding: 缺本节点原生实体回读，文字或图边不代替')
        elif status not in {None, 'unknown', 'not_bound', 'unverified', 'not_checked', 'stale'}:
            raise ValueError('native_binding: 未识别状态不能冒充已回读')
    # Historical receipts/archives remain data, not current verified claims.
    for key in ('drafts', 'generation_segments', 'creative_sections'):
        if key in value:
            validate_native_binding_claims(value[key], root, revision, target)


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


def validate_scene_source_mapping(rows, sources, project):
    """Bind draft shot mapping to byte-checked source segments and director.

    sources contains (authority reference, resolved path, decoded JSON). Source
    director links must be present in source_artifacts: omission cannot downgrade
    the state check to a legacy/no-change path.
    """
    videos = []
    all_shots = set()
    has_states = False
    for ref, path, payload in sources:
        if not isinstance(payload, dict):
            continue
        for scene in payload.get('scenes', []):
            all_shots.update(s['shot_id'] for s in scene.get('shots', []))
            has_states |= bool(scene.get('scene_states'))
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
        segments = payload.get('generation_segments')
        if segments is None and isinstance(payload.get('creative_sections'), list):
            segments = [s for section in payload['creative_sections'] for s in section.get('generation_segments', [])]
        if segments:
            videos.extend(segments)
    if any(row.get('source_segment_id') for row in rows) and not videos:
        raise ValueError('scene_state: 已声明source_segment_id但缺准确视频源，不能退回无映射检查')
    # Audio provenance is independent of persistent environment changes. Read
    # only existing director placements and video source_audio; no NLP claims.
    shot_audio = {}
    for _, _, payload in sources:
        for scene in payload.get('scenes', []):
            for shot in scene.get('shots', []):
                if isinstance(shot.get('audio'), dict) and 'dialogue' in shot['audio']:
                    key = shot['shot_id']
                    if key in shot_audio:
                        raise ValueError('source_audio: 来源导演镜号冲突')
                    shot_audio[key] = shot['audio']['dialogue']
    if videos and (shot_audio or any('source_audio' in s for s in videos)):
        replay_source_audio(videos, shot_audio)
    if not has_states:
        return
    if not videos:
        raise ValueError('scene_state: 场景后态交付必须携带准确视频源generation_segments，不能省略视频源绕过镜号映射')
    for row in rows:
        ids = source_shot_ids(row)
        if not ids or not set(ids) <= all_shots:
            raise ValueError('scene_state: 提交镜号不在准确源导演内')
        if videos:
            segment = row.get('source_segment_id', row.get('node_id'))
            matches = [s for s in videos if s.get('segment_id') == segment]
            if len(matches) != 1 or source_shot_ids(matches[0]) != ids:
                raise ValueError('scene_state: 提交 source_shots 必须逐镜匹配准确视频源 source_segment_id，不能换镜绕过后态')


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
        if (not ids or not isinstance(audio, list) or
                any(not isinstance(a, dict) for a in audio) or
                [a.get('shot_id') for a in audio] != ids):
            raise ValueError('source_audio: 必须逐镜对应本段完整source_shots，不能漏掉无对白镜')
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
