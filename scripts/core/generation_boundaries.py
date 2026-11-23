"""Read-only checks for current generation_segments, not an automatic splitter.

The author still judges rhythm, actual model support and image contents. This
checks declared reasons against exact source fields/bytes and never invents a
duration ceiling, a scene boundary, a reference or an approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from .quality_evidence import (generation_duration_values, required_visuals, reference_shot_ids, scene_state_requirements,
                               source_shot_ids, validate_required_visuals,
                               validate_scene_state_coverage, validate_shot_durations)
from .prompt_body import QUOTED_TEXT_RE, standalone_quality_issues, submitted_body_issues, submitted_prompt_body
from .project_kickoff import load_kickoff

_CREATOR_BOWL_BRUSH_OFFER = re.compile(r"送(?:给?你|给大家)?\s*高定\s*调膜\s*碗\s*(?:[、和与及]\s*)?刷")
_DIRECTOR_BODY_FIELDS = {
    "action": ("take", "continuity"),
    "reaction": ("take", "continuity", "camera"),
    "movement": ("camera.movement",),
    "cut": ("cut", "rhythm", "take", "audio"),
}
_UNDECIDED_DIRECTOR_TEXT = re.compile(
    r"(?:动作|反应|运镜|切点|拍法)?\s*(?:[:：]\s*)?(?:待定|待补|待确认|待导演确认|现场决定)[。.]?"
)
_OUTGOING_CUT = re.compile(
    r"(?:硬切|跳切|切(?:走|出|到|至|入|回|成|换|镜)|"
    r"切(?=[^，。！？；,\n]{0,18}(?:近景|中景|全景|远景|特写|画面|视角|镜头))|收镜|"
    r"结束(?:这个|这一个|该|当前|本)?(?:镜头|全景|中景|近景|远景|特写)|镜头(?:就此)?结束)"
)
_NEGATED_CUT_PREFIX = re.compile(r"(?:不(?:要|能|再|会|用|做)?|无须|无需|没有|别|避免|禁止)(?:提前|中途|直接)?\s*$")
_BEFORE_CUT_SUFFIX = re.compile(r"[^，。！？；,\n]{0,18}?(?:之前|前)(?=[，。！？；,\n\s]|$)")

_PHYSICAL_DIMENSIONS = ("action", "positions", "props", "visibility")
_PHYSICAL_ROLES = {
    "action": {"state_reference"},
    "positions": {"layout", "state_reference"},
    "props": {"appearance", "state_reference"},
    "visibility": {"appearance", "state_reference"},
}


def _need(value, message):
    if not value:
        raise ValueError("generation_boundary: " + message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _seconds(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _evidence_file(proof, root):
    _need(isinstance(proof, dict) and all(_text(proof.get(k)) for k in
          ("path", "sha256", "locator", "checked_at")), "缺少本轮限制核验的原始证据定位与字节")
    path = (root / proof["path"]).resolve()
    _need(path.is_relative_to(root) and path.is_file() and path.stat().st_size > 0,
          "限制证据必须是项目内可读的真实文件")
    evidence = path.read_bytes()
    _need(hashlib.sha256(evidence).hexdigest() == proof["sha256"], "限制证据 hash 已过期")
    return evidence


def _redframe_duration_options(evidence, model_id, mode):
    try:
        record = json.loads(evidence)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("generation_boundary: 红帧须提供原生模型与时长选项 JSON 证据") from exc
    native = record.get("model") if isinstance(record, dict) else None
    _need(isinstance(native, dict) and native.get("type") == "video"
          and all(_text(native.get(key)) for key in ("modelId", "providerId", "label")),
          "红帧能力证据缺可识别的原生视频模型")
    native_id, provider, label = (native[key] for key in ("modelId", "providerId", "label"))
    identities = {native_id, label, json.dumps([provider, native_id], ensure_ascii=False, separators=(",", ":"))}
    if provider == "volcengineArkVideo" and native_id.startswith("doubao-") and label.startswith("Doubao "):
        identities.add(label.removeprefix("Doubao "))
    _need(model_id in identities, "红帧能力证据的原生模型与已选模型不一致")

    descriptors = native.get("mode")
    _need(isinstance(descriptors, list) and descriptors, "红帧能力证据缺原生参考模式")
    reference_tokens = (token for entry in descriptors for token in (entry if isinstance(entry, list) else [entry]))
    supports_reference = any(isinstance(token, str) and re.fullmatch(r"(?:image|video|audio)Reference:[1-9]\d*", token)
                             for token in reference_tokens)
    _need(mode in descriptors or (mode == "reference" and supports_reference),
          "红帧能力证据不支持已选参考模式")

    options = {}
    constraints = native.get("durationResolutionMap")
    _need(isinstance(constraints, list) and constraints, "红帧能力证据缺原生时长与分辨率选项")
    for row in constraints:
        _need(isinstance(row, dict), "红帧原生时长选项结构无效")
        resolutions, durations = row.get("resolution"), row.get("duration")
        _need(isinstance(resolutions, list) and resolutions and all(_text(item) for item in resolutions)
              and isinstance(durations, list) and durations and all(_seconds(item) for item in durations),
              "红帧原生时长与分辨率选项无效")
        for resolution in resolutions:
            options.setdefault(resolution, set()).update(durations)
    return options


def _rows(payload):
    # Composite source snapshots may carry shot mappings without prompt prose;
    # formal adoption checks the complete submitted body independently.
    from .web_content import source_review_segments
    _need(isinstance(payload, dict) and ("generation_segments" in payload or "creative_sections" in payload),
          "须使用现行 generation_segments，不使用历史专用分段格式")
    rows = source_review_segments(payload)
    return rows


def _local_dependency_payload(payload, review):
    covered = set(review['checked_segment_ids'])
    for boundary in review['adjacent_boundaries_reviewed']:
        covered.update(boundary)

    def selected(segments):
        # Creative sections can reference the top-level rows by ID instead of
        # containing the rows themselves. The source parser validates both forms.
        return [entry for entry in segments
                if (entry if isinstance(entry, str) else entry['segment_id']) in covered]

    scoped = dict(payload)
    if 'generation_segments' in payload:
        scoped['generation_segments'] = selected(payload['generation_segments'])
    for key in ('creative_sections', 'independent_candidate_ranges'):
        if payload.get(key) is None:
            continue
        scoped[key] = []
        for group in payload[key]:
            rows = selected(group['generation_segments'])
            if rows:
                scoped[key].append({**group, 'generation_segments': rows})
    return scoped


def _source_boundary(record, boundary_shots, shots, *, allow_dialogue=False):
    """Bind the rationale to an existing director field, never inferred prose."""
    _need(isinstance(record, dict), "拆段须引用准确导演边界")
    shot_id, field, quote = (record.get(k) for k in ("shot_id", "field", "quote"))
    _need(shot_id in boundary_shots and _text(field) and _text(quote),
          "拆段依据须定位两侧准确镜号、字段及原文")
    # Only existing action/rhythm/cut fields can substantiate a boundary.
    _need(field.split(".")[0] in {"take", "cut", "rhythm", "continuity", "audio"},
          "导演边界须引用动作、节奏、完整对白或剪辑字段")
    if field.startswith("audio."):
        _need(allow_dialogue
              and re.fullmatch(r"audio\.dialogue\.\d+\.text", field),
              "完整句末只作承载限制下的连续切点，不自动证明新事件或时间省略")
    value = _director_field(shots[shot_id][1], field)
    _need(isinstance(value, str) and quote in value, "拆段依据不在准确导演字段原文中")


def _director_field(value, field):
    for key in field.split("."):
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return None
    return value


def _director_exit_marker(body, visible_body, anchor, start):
    """Find an explicit outgoing edit within a located cut anchor."""
    if anchor["field"] not in {"cut.exit", "cut.trigger"}:
        return None
    excerpt = anchor["prompt_quote"]
    position = body.find(excerpt, start)
    while position != -1:
        for cue in _OUTGOING_CUT.finditer(visible_body, position, position + len(excerpt)):
            prefix = visible_body[max(0, cue.start() - 12):cue.start()]
            if (_NEGATED_CUT_PREFIX.search(prefix)
                    or _BEFORE_CUT_SUFFIX.match(visible_body, cue.end())):
                continue
            return cue
        position = body.find(excerpt, position + len(excerpt))
    return None


def validate_director_body(row, shot_ids, shots, *, require_coverage):
    """Check located source and submitted clauses; the author judges their meaning."""
    segment_id = row["segment_id"]
    body = submitted_prompt_body(row)
    if issues := submitted_body_issues(body):
        raise ValueError(f"{segment_id}: " + "；".join(issues))
    _need(row.get("production_blocked") is not True and row.get("submit_ready") is not False
          and not row.get("blockers"), f"{segment_id} 尚有导演或制作缺口；退回对应阶段补齐后再检查正文")
    coverage = row.get("director_coverage")
    if not require_coverage and "director_coverage" not in row:
        return 0
    _need(isinstance(coverage, list) and all(isinstance(item, dict) for item in coverage)
          and [item.get("shot_id") for item in coverage] == shot_ids,
          f"{segment_id} 的 director_coverage 须按所选镜号逐镜记录导演原文和实际提交正文；旧稿新交付须先补核")
    visible_body = QUOTED_TEXT_RE.sub(lambda quoted: " " * len(quoted[0]), body)
    shot_start = 0
    for item in coverage:
        shot_id = item["shot_id"]
        for aspect, fields in _DIRECTOR_BODY_FIELDS.items():
            anchor = item.get(aspect)
            locator = f"{segment_id}/{shot_id} {aspect}"
            if aspect == "reaction" and aspect in item and anchor is None:
                continue
            _need(isinstance(anchor, dict) and all(_text(anchor.get(k)) for k in ("field", "quote", "prompt_quote")),
                  f"{locator} 缺导演原文或正文落点；拍法不清楚须退导演补齐")
            field, quote, prompt_quote = (anchor[k] for k in ("field", "quote", "prompt_quote"))
            _need(any(field == name or field.startswith(name + ".") for name in fields),
                  f"{locator} 引用了不适用的导演字段：{field}")
            source = _director_field(shots[shot_id][1], field)
            _need(isinstance(source, str) and quote in source
                  and not _UNDECIDED_DIRECTOR_TEXT.fullmatch(source.strip())
                  and not _UNDECIDED_DIRECTOR_TEXT.fullmatch(quote.strip())
                  and not standalone_quality_issues(quote),
                  f"{locator} 无可核的导演拍法原文；退回导演核对 {field}")
            _need(prompt_quote in body and not _UNDECIDED_DIRECTOR_TEXT.fullmatch(prompt_quote.strip())
                  and not standalone_quality_issues(prompt_quote),
                  f"{locator} 的落点未出现在实际提交正文或只含泛词")
        exit_marker = _director_exit_marker(body, visible_body, item["cut"], shot_start)
        if exit_marker is not None:
            for aspect in ("action", "reaction"):
                if anchor := item[aspect]:
                    _need(body.find(anchor["prompt_quote"], shot_start, exit_marker.start()) != -1,
                          f"{segment_id}/{shot_id} {aspect} 正文顺序与出镜切点冲突；本镜动作或反应须先于切走")
            shot_start = exit_marker.end()
    return len(coverage)


def _cross_scene_review(left, right, before_shot, after_shot, shots):
    review = right.get("cross_scene_continuity")
    boundary = f"{left['segment_id']}→{right['segment_id']}"
    _need(isinstance(review, dict) and review.get("from_segment_id") == left["segment_id"]
          and review.get("decision") in {"action_continues", "new_action_or_time_jump"}
          and _text(review.get("basis")),
          f"{boundary} 缺跨场动作接续判断；须核对前段尾镜与后段首镜")
    _source_boundary(review.get("previous_source"), (before_shot,), shots)
    _source_boundary(review.get("current_source"), (after_shot,), shots)
    if review["decision"] == "action_continues":
        state = right.get("physical_continuity")
        _need(isinstance(state, dict) and "action" in state,
              f"{boundary} 跨场仍继续同一动作；须核对并记录前段尾态与后段开场动作")


def _model_capability(payload, root, *, platform=None):
    model = payload.get("selected_video_model")
    _need(isinstance(model, dict), "缺少本轮实际模型、参考方式和已核生成时长能力")
    # A delegated draft choice binds technical parameters. A formal draft may
    # inherit its model from kickoff while retaining the no-generation scope.
    kickoff = load_kickoff(root) if model.get("confirmed_by_user") is False else None
    canvas = kickoff.get("canvas", {}) if kickoff else {}
    kickoff_draft = (payload.get("production_blocked") is False
        and model.get("id") == canvas.get("model")
        and model.get("platform") == canvas.get("platform"))
    draft_selection = (model.get("confirmed_by_user") is False
        and model.get("selection_basis") == "agent_selected_for_authorized_draft"
        and _text(model.get("authorization_ref"))
        and payload.get("status") == "candidate"
        and payload.get("review_status") == "unreviewed"
        and (payload.get("production_blocked") is True or kickoff_draft)
        and payload.get("generation_authorized") is False
        and payload.get("user_approved") is not True)
    _need(all(_text(model.get(k)) for k in ("id", "platform", "entry_point", "reference_mode"))
          and (model.get("confirmed_by_user") is True or draft_selection)
          and _seconds(model.get("max_duration_seconds")),
          "模型、入口、参考模式、确认选择与真实时长上限不完整")
    minimum, maximum = model.get("min_duration_seconds"), model["max_duration_seconds"]
    if "min_duration_seconds" in model or platform is not None or model["platform"] == "redframe":
        _need(_seconds(minimum) and minimum <= maximum, "缺少有效的本轮已核生成时长下限")
    if platform is not None:
        _need(model["platform"] == platform, "源能力证据所属平台与实际交付平台不一致")
    common_proof = model.get("capability_evidence")
    evidence = _evidence_file(common_proof, root)
    modes = model.get("reference_modes")
    if modes is None:
        modes = [model["reference_mode"]]
        proofs = {modes[0]: common_proof}
    else:
        _need(isinstance(modes, list) and modes and all(_text(mode) for mode in modes)
              and len(set(modes)) == len(modes), "已核参考模式列表无效")
        proofs = model.get("reference_mode_evidence")
        _need(isinstance(proofs, dict) and set(proofs) == set(modes),
              "每种参考模式须有本轮实际能力证据")
    per_mode = {mode: evidence if proofs[mode] == common_proof else _evidence_file(proofs[mode], root)
                for mode in modes}
    options = None
    if model["platform"] == "redframe":
        options = {mode: _redframe_duration_options(proof, model["id"], mode)
                   for mode, proof in per_mode.items()}
    return model["id"], modes, minimum, maximum, options


def _check_generation_duration(row, parameters, capability):
    model, modes, minimum, maximum, options = capability
    reference_modes = [parameters[key] for key in ("reference_mode", "mode") if key in parameters]
    _need(parameters.get("model") == model and reference_modes and reference_modes[0] in modes
          and all(mode == reference_modes[0] for mode in reference_modes),
          "拆段能力与实际段落模型/参考方式不一致")
    values = generation_duration_values(row, parameters)
    _need(values and all(_seconds(value) and value == values[0] for value in values),
          "生成时长缺失或冲突")
    _need(values[0] <= maximum, "生成时长超过本轮已核上限")
    if minimum is not None:
        _need(values[0] >= minimum, f"生成时长 {values[0]:g} 秒低于本轮已核下限 {minimum:g} 秒")
    if options is not None:
        resolution = parameters.get("resolution")
        _need(_text(resolution), "红帧生成参数缺准确分辨率")
        durations = options[reference_modes[0]].get(resolution)
        _need(durations, f"红帧能力证据不支持分辨率 {resolution}")
        _need(min(durations) == minimum and max(durations) == maximum,
              "已声明时长上下限与红帧原生选项不一致")
        _need(values[0] in durations, f"生成时长 {values[0]:g} 秒不在本轮红帧模型、参考模式和分辨率的原生选项中")


def _check_target_edit_duration(row, minimum, *, platform, required=False):
    if minimum is None:
        return
    ident = row["segment_id"]
    target = row.get("target_edit_duration_seconds")
    if "target_edit_duration_seconds" in row:
        _need(_seconds(target), f"{ident} 的目标剪辑时长必须为有效正数")
    if "edit_in_seconds" in row or "edit_out_seconds" in row:
        start, end = row.get("edit_in_seconds"), row.get("edit_out_seconds")
        _need(type(start) in (int, float) and math.isfinite(start) and start >= 0
              and _seconds(end) and end > start, f"{ident} 的目标剪辑起止时间无效")
        _need(target is None or math.isclose(target, end - start, abs_tol=1e-6),
              f"{ident} 的目标剪辑时长与源起止时间不一致")
        target = end - start
    if required:
        _need(target is not None, f"{ident} 须在准确源稿登记目标剪辑时长或源剪辑起止时间")
    if target is not None and target < minimum and not math.isclose(target, minimum, abs_tol=1e-6):
        guidance = ("默认并入下一段并保留原切点；无可用下一段时核对能否并入前一段或重划相邻段"
                    if platform == "redframe" else "须与允许接续的相邻镜合并")
        raise ValueError(f"generation_boundary: {ident} 的目标剪辑时长 {target:g} 秒低于本轮已核下限 "
                         f"{minimum:g} 秒；{guidance}")


def _model_limit(payload, left, right, capability):
    maximum = capability[3]
    for row in (left, right):
        parameters = row.get("parameters", payload.get("platform", {}))
        _need(isinstance(parameters, dict), "交付/源参数结构无效")
        _check_generation_duration(row, parameters, capability)
    return maximum


def validate_delivery_parameters(rows, payload, project, *, platform=None):
    """Bind submission values to the exact source; caller bounds are not proof."""
    sources = {row["segment_id"]: row for row in _rows(payload)}
    capability = None
    for actual in rows:
        source = sources.get(actual.get("source_segment_id", actual.get("node_id")))
        if source is None:
            continue  # The combined source mapping rejects unknown/ambiguous IDs.
        expected = source.get("parameters", payload.get("platform", {}))
        parameters = actual.get("parameters")
        _need(isinstance(parameters, dict) and isinstance(expected, dict), "交付/源参数结构无效")
        for aliases, label in ((("model",), "模型"), (("reference_mode", "mode"), "参考模式")):
            values = [expected[k] for k in aliases if k in expected]
            if values:
                observed = [parameters[k] for k in aliases if k in parameters]
                _need(all(_text(v) and v == values[0] for v in values) and observed
                      and all(v == values[0] for v in observed), "实际交付" + label + "与准确源稿不一致")
        duration = generation_duration_values(source, expected)
        if duration:
            observed = [parameters[k] for k in ("duration_seconds", "duration") if k in parameters]
            _need(all(_seconds(v) and v == duration[0] for v in duration) and observed
                  and all(_seconds(v) and v == duration[0] for v in observed),
                  "实际交付生成时长与准确源稿不一致，不能用自填 bounds 改时长")
        if "selected_video_model" in payload or platform is not None:
            if capability is None:
                capability = _model_capability(payload, Path(project).resolve(), platform=platform)
            minimum, maximum = capability[2:4]
            if capability[4] is not None:
                _need(expected.get("resolution") == parameters.get("resolution"),
                      "实际交付分辨率与准确源稿不一致")
            _check_target_edit_duration(source, minimum, platform=payload["selected_video_model"]["platform"],
                                        required=platform is not None)
            bounds = actual.get("duration_bounds")
            if minimum is not None and (bounds is not None or platform is not None):
                _need(isinstance(bounds, list) and len(bounds) == 2 and all(_seconds(v) for v in bounds)
                      and bounds[0] == minimum and bounds[1] == maximum,
                      "冻结交付 duration_bounds 与同模型/参考方式的已核源能力上下限不一致")
            _check_generation_duration(source, expected, capability)
            _check_generation_duration(actual, parameters, capability)


def _cutpoint(left, right):
    continuity = right.get("continuity")
    _need(isinstance(continuity, dict) and continuity.get("previous_segment_id") == left["segment_id"],
          "连续事件拆段缺少准确前段及共用切点参考")
    boundary = continuity.get("boundary")
    _need(isinstance(boundary, dict) and _text(boundary.get("state_id"))
          and _text(boundary.get("description")), "连续事件拆段缺少切点状态和具体描述")
    for side, row in (("previous", left), ("current", right)):
        boundary_shot = source_shot_ids(row)[-1 if side == "previous" else 0]
        hashes = boundary.get(side + "_sha256s")
        _need(isinstance(hashes, list) and hashes and all(_text(h) for h in hashes)
              and len(set(hashes)) == len(hashes), "切点须列两侧实际输入的准确参考 hash")
        refs = row.get("materials", row.get("candidate_upload_order", []))
        _need(isinstance(refs, list), "缺少本段实际素材清单")
        matched = set()
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            roles = ref.get("roles", [ref.get("role")])
            expected = {"state_reference", "end_frame" if side == "previous" else "start_frame"}
            if (isinstance(roles, list) and all(_text(r) for r in roles) and set(roles) & expected
                    and ref.get("state_id") == boundary["state_id"] and _text(ref.get("sha256"))
                    and boundary_shot in reference_shot_ids(ref, row)):
                matched.add(ref["sha256"])
        _need(set(hashes) <= matched, "基础环境/身份图不能冒充准确切点状态参考；须在两段实际输入中绑定")
    # Different views of the same state are allowed; the existing image-use and
    # canvas gates check their confirmed group/visual evidence and native bytes.


def _physical_requirements(left, right):
    state = right.get("physical_continuity")
    boundary = f"{left['segment_id']}→{right['segment_id']}"
    _need(isinstance(state, dict) and state.get("from_segment_id") == left["segment_id"]
          and _text(state.get("permitted_change")),
          f"{boundary} 缺人物、持物和可见状态的独立接缝检查")
    dimensions = [dimension for dimension in _PHYSICAL_DIMENSIONS if dimension in state]
    _need(dimensions, f"{boundary} 未列出任何实际继承的状态")
    for dimension in dimensions:
        check = state[dimension]
        _need(isinstance(check, dict) and check.get("status") in {"visual_reference", "no_visual_needed"}
              and all(_text(check.get(k)) for k in ("previous_state", "current_state", "basis")),
              f"{boundary} 的 {dimension} 缺两侧可见状态和依据")
        for side, row, shot in (
            ("previous", left, source_shot_ids(left)[-1]),
            ("current", right, source_shot_ids(right)[0]),
        ):
            hashes = check.get(f"{side}_sha256s", [])
            _need(isinstance(hashes, list) and all(isinstance(value, str) and
                  re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes)
                  and len(set(hashes)) == len(hashes),
                  f"{boundary} 的 {dimension} 引用须为两侧准确图像摘要")
            if check["status"] == "no_visual_needed":
                _need(not hashes, f"{boundary} 的 {dimension} 无需补图判断与引用冲突")
                continue
            _need(hashes and _text(check.get(f"{side}_observation")),
                  f"{boundary} 的 {dimension} 缺 {side} 侧实际关系／状态图及实看证据")
            yield row["segment_id"], shot, dimension, side, set(hashes), boundary


def _require_physical_refs(refs, row, shot, dimension, side, hashes, boundary, *, source):
    _need(isinstance(refs, list), f"{boundary} 缺实际参考清单")
    covered = set()
    expected_roles = _PHYSICAL_ROLES[dimension] | {"end_frame" if side == "previous" else "start_frame"}
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("sha256") not in hashes:
            continue
        role = ref.get("role", ref.get("primary_role"))
        roles = ref.get("roles", [role])
        scope = ref.get("physical_scope", [])
        is_image = (Path(ref.get("path") or "").suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                    if source else ref.get("kind") == "image")
        # A general layout or product photograph does not prove that it shows
        # the actors, in-hand assembly, or visibility state being checked.
        if (is_image and isinstance(roles, list) and set(roles) & expected_roles
                and isinstance(scope, list) and dimension in scope and shot in reference_shot_ids(ref, row)):
            covered.add(ref["sha256"])
    segment_id = row.get("source_segment_id", row.get("segment_id"))
    _need(hashes <= covered, f"{boundary} 的 {dimension} 缺 {segment_id} 侧实际匹配的画面参考")


def validate_generation_boundaries(payload, storyboard, project, *, require_physical=True,
                                   delivery_platform=None, delivery_segment_ids=None, action_shots=None,
                                   review_segment_ids=None, source_artifact=None, state=None,
                                   prompt_content_segment_ids=None):
    """Validate a full or partial current draft without writing/approving it.

    Missing facts may remain in a candidate, but this strict check fails until
    supplied. It does not auto-merge/split, demand an image for every shot, or
    impose model settings on an unsplit platform-neutral manuscript.
    Canvas preflight can narrow action coverage to the shots being delivered.
    A local review checks named segments and their joins in the actual draft.
    Author review, adoption and formal Redframe delivery pass the exact segment
    scope for submitted body checks. A historical read does not add that check.
    """
    try:
        result = _validate(payload, storyboard, Path(project).resolve(), require_physical=require_physical,
                           delivery_platform=delivery_platform, delivery_segment_ids=delivery_segment_ids,
                           action_shots=action_shots, review_segment_ids=review_segment_ids,
                           prompt_content_segment_ids=prompt_content_segment_ids)
        result['scene_state_plan'] = diagnose_scene_state_plan(
            payload, storyboard, segment_ids=review_segment_ids, project=project,
            source_artifact=source_artifact, state=state,
            strict_prompt_controls=require_physical and source_artifact is not None)
        return result
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("generation_boundary: 分段依据或来源结构无效") from exc


def _validate(payload, storyboard, root, *, require_physical, delivery_platform, delivery_segment_ids, action_shots,
              review_segment_ids, prompt_content_segment_ids):
    rows = _rows(payload)
    local_review = review_segment_ids is not None
    if local_review:
        review_segment_ids = set(review_segment_ids)
        unknown = review_segment_ids - {row['segment_id'] for row in rows}
        _need(review_segment_ids and not unknown, "局部检查须指定稿内准确段号，未找到：" + "、".join(sorted(unknown)))
    reviewed_rows = [row for row in rows if not local_review or row['segment_id'] in review_segment_ids]
    content_ids = set(prompt_content_segment_ids or [])
    _need(content_ids <= {row['segment_id'] for row in rows}, "正文自查包含未登记的生成段")
    director_anchors_checked = 0
    offer_rows = [row['segment_id'] for row in reviewed_rows
                  if _CREATOR_BOWL_BRUSH_OFFER.search(submitted_prompt_body(row))]
    if offer_rows:
        kickoff = load_kickoff(root)
        product = kickoff["product"] if kickoff else payload.get("product_reference_pack")
        if isinstance(product, dict) and product.get("product_id") == "creator-polishing-mask-2":
            raise ValueError("、".join(offer_rows) + " 的赠品口播请在视频生成正文使用「还送高定条膜碗刷」")
    shots = {}
    staged_scenes = set()
    for scene in storyboard.get("scenes", []):
        if scene.get("space_rules"):
            staged_scenes.add(scene.get("scene_id"))
        for shot in scene.get("shots", []):
            key = shot.get("shot_id")
            _need(_text(key) and key not in shots and _text(scene.get("scene_id")), "导演场景/镜号缺失或重复")
            shots[key] = (scene["scene_id"], shot)
    mapped = []
    order = {key: index for index, key in enumerate(shots)}
    previous = -1
    scope = payload.get("scope")
    scope_type = scope.get("type") if isinstance(scope, dict) else None
    check_scope = scope_type in ("full_local_authored", "selected_ranges")
    selected_ranges = (scope.get("independent_ranges") or []) if scope_type == "selected_ranges" else []
    range_joins = {(before["segment_ids"][-1], after["segment_ids"][0])
                   for before, after in zip(selected_ranges, selected_ranges[1:])}
    selected_model = payload.get("selected_video_model")
    check_cross_scene = require_physical and (
        delivery_platform == "redframe"
        or isinstance(selected_model, dict) and selected_model.get("platform") == "redframe")
    capability = (_model_capability(payload, root) if isinstance(selected_model, dict)
                  and ("min_duration_seconds" in selected_model or selected_model.get("platform") == "redframe") else None)
    for row in rows:
        ids = source_shot_ids(row)
        _need(ids and all(key in shots for key in ids), "每个生成段必须映射准确导演镜号，不能省略映射绕过分段检查")
        if row['segment_id'] in content_ids:
            director_anchors_checked += validate_director_body(
                row, ids, shots, require_coverage=storyboard.get("schema_version") == "4.0")
        row_reviewed = not local_review or row['segment_id'] in review_segment_ids
        if check_scope and row_reviewed:
            _need(all(order[after] == order[before] + 1 for before, after in zip(ids, ids[1:])),
                  f"{row['segment_id']} 的生成段内存在未覆盖的导演镜号")
        for key in ids:
            _need(order[key] >= previous, "生成段来源顺序与导演顺序冲突")
            previous = order[key]
        mapped.append(ids)
        if not row_reviewed:
            continue
        parameters = row.get("parameters", payload.get("platform", {}))
        if capability is not None:
            _need(isinstance(parameters, dict), "交付/源参数结构无效")
            _check_target_edit_duration(row, capability[2], platform=selected_model["platform"])
            _check_generation_duration(row, parameters, capability)
        if "shot_durations" in row or (check_cross_scene and capability is not None):
            validate_shot_durations(row, parameters)
    covered = {shot for ids in mapped for shot in ids}
    if scope_type == "full_local_authored" and not local_review:
        _need(covered == set(shots), "全片稿生成段未完整覆盖准确导演镜号")
    reviewed_shots = {shot for row in reviewed_rows for shot in source_shot_ids(row)} if local_review else None
    selected_shots = () if local_review else tuple(shot for shot in shots if not range_joins or shot in covered)
    validate_required_visuals(reviewed_rows, required_visuals(storyboard, shot_ids=reviewed_shots),
                              shot_order=selected_shots, project=root,
                              action_shots=action_shots)
    checked = 0
    cross_scene_checked = 0
    gaps_skipped = 0
    reviewed_boundaries = []
    for index, (left, right) in enumerate(zip(rows, rows[1:])):
        join = left["segment_id"], right["segment_id"]
        if local_review and review_segment_ids.isdisjoint(join):
            continue
        before, after = mapped[index:index + 2]
        if check_scope:
            end, start = left.get("edit_out_seconds"), right.get("edit_in_seconds")
            gap = (start - end if all(type(t) in (int, float) and math.isfinite(t) for t in (end, start))
                   else None)
            omitted_shots = order[after[0]] > order[before[-1]] + 1
            _need(gap is None or gap >= -1e-6, f"{join[0]}→{join[1]} 剪辑时码重叠")
            if omitted_shots or (gap is not None and gap > 1e-6):
                _need(join in range_joins and omitted_shots and gap is not None and gap > 1e-6,
                      f"{join[0]}→{join[1]} 存在未覆盖镜号或时码空档；须如实列为不同的已选范围")
                _need(not any(right.get(key) for key in
                              ("same_scene_split", "physical_continuity", "cross_scene_continuity"))
                      and not (right.get("continuity") or {}).get("previous_segment_id"),
                      f"{join[0]}→{join[1]} 的未制作范围不能声明为两段直接接续")
                gaps_skipped += 1
                continue
        if local_review:
            reviewed_boundaries.append(join)
        before_scene, after_scene = shots[before[-1]][0], shots[after[0]][0]
        same_scene = before_scene == after_scene
        touches_delivery = (delivery_segment_ids is None or left["segment_id"] in delivery_segment_ids
                            or right["segment_id"] in delivery_segment_ids)
        if not same_scene and touches_delivery and (check_cross_scene or "cross_scene_continuity" in right):
            _cross_scene_review(left, right, before[-1], after[0], shots)
            cross_scene_checked += 1
        if "physical_continuity" in right or (require_physical and same_scene and before_scene in staged_scenes):
            by_side = {left["segment_id"]: left, right["segment_id"]: right}
            for ident, shot, dimension, side, hashes, boundary in _physical_requirements(left, right):
                row = by_side[ident]
                _require_physical_refs(row.get("materials", row.get("candidate_upload_order", [])),
                                       row, shot, dimension, side, hashes, boundary, source=True)
        if not same_scene:
            continue
        split = right.get("same_scene_split")
        _need(isinstance(split, dict) and split.get("from_segment_id") == left["segment_id"]
              and _text(split.get("evidence")), "同场景默认合并；拆段须说明具体依据，不能按固定秒数、镜头数或图片数拆分")
        reason = split.get("reason")
        _need(reason in {"model_duration_exceeded", "execution_limit", "event_complete", "time_ellipsis"},
              "缺少真实承载限制或导演已有事件/时间边界的拆段理由")
        _source_boundary(split.get("source_boundary"), (before[-1], after[0]), shots,
                         allow_dialogue=reason in {"model_duration_exceeded", "execution_limit"})
        if reason == "model_duration_exceeded":
            if capability is None:
                capability = _model_capability(payload, root)
            limit = _model_limit(payload, left, right, capability)
            estimate = split.get("estimated_scene_duration_seconds")
            _need(_seconds(estimate) and estimate > limit and _text(split.get("timing_basis")),
                  "待承载连续内容未证明超出模型上限，不能机械拆段")
        if reason == "execution_limit":
            _evidence_file(split.get("limitation_evidence"), root)
        if reason in {"model_duration_exceeded", "execution_limit"}:
            _cutpoint(left, right)
        checked += 1
    scope_report = {"validation_scope": "local_review", "checked_segment_ids": [row['segment_id'] for row in reviewed_rows],
                    "adjacent_boundaries_reviewed": reviewed_boundaries} if local_review else {}
    if prompt_content_segment_ids is not None:
        scope_report["director_body_shots_checked"] = director_anchors_checked
    return {"segment_count": len(reviewed_rows), "same_scene_boundaries_checked": checked,
            "cross_scene_boundaries_checked": cross_scene_checked,
            "selected_range_gaps_skipped": gaps_skipped,
            "scope": "source boundaries, capability evidence, and declared physical-state and action references; not semantic/visual approval",
            **scope_report}


def validate_delivery_cutpoints(rows, segments):
    """Require source cutpoint references in the actual submitted draft inputs."""
    by_id = {s["segment_id"]: s for s in segments}
    _need(len(by_id) == len(segments), "交付源生成段 ID 重复，不能猜来源")
    delivered = {row.get("source_segment_id"): row for row in rows}
    _need(len(delivered) == len(rows), "同一源生成段不能对应多个本次交付节点")
    required, external_predecessors = {}, {}
    for right in segments:
        physical = right.get("physical_continuity")
        if physical is not None:
            predecessor = by_id.get(physical.get("from_segment_id")) if isinstance(physical, dict) else None
            _need(predecessor is not None, "实际交付缺物理接缝的准确前段")
            for ident, shot, dimension, side, hashes, boundary in _physical_requirements(predecessor, right):
                if ident in delivered:
                    row = delivered[ident]
                    _require_physical_refs(row.get("reference_checks", []), row, shot, dimension,
                                           side, hashes, boundary, source=False)
        split = right.get("same_scene_split", {})
        if not isinstance(split, dict) or split.get("reason") not in {"model_duration_exceeded", "execution_limit"}:
            continue
        boundary = right["continuity"]["boundary"]  # strict source check ran first
        left_id, right_id = split["from_segment_id"], right["segment_id"]
        if left_id in delivered and right_id in delivered:
            previous, current = delivered[left_id], delivered[right_id]
            before, after = previous.get("continuity"), current.get("continuity")
            _need(isinstance(before, dict) and isinstance(after, dict)
                  and _text(before.get("event_id")) and before["event_id"] == after.get("event_id")
                  and _text(previous.get("node_id")) and after.get("previous_node_id") == previous["node_id"]
                  and rows.index(previous) < rows.index(current),
                  "实际交付前驱/事件与源连续切点不一致，不能伪造新事件跳过切点核验")
        elif right_id in delivered:
            current = delivered[right_id]
            continuity = current.get("continuity", {})
            _need(_text(continuity.get("previous_node_id")),
                  "部分交付必须保留源连续前驱，不能删前驱或伪造新事件")
            external_predecessors[current["node_id"]] = left_id
        for ident, side in ((split["from_segment_id"], "previous"), (right["segment_id"], "current")):
            required.setdefault(ident, []).append((boundary["state_id"], set(boundary[side + "_sha256s"]), side))
    for row in rows:
        ident = row.get("source_segment_id")
        _need(ident in by_id, "scene_state: 交付必须绑定准确源生成段，不能换号绕过分段依据")
        for state, hashes, side in required.get(ident, []):
            boundary_shot = source_shot_ids(by_id[ident])[-1 if side == "previous" else 0]
            refs = row.get("reference_checks", [])
            actual = {r.get("sha256") for r in refs if isinstance(r, dict)
                      and r.get("state_id") == state and _text(r.get("sha256"))
                      and isinstance(r.get("roles", [r.get("role")]), list)
                      and all(_text(role) for role in r.get("roles", [r.get("role")]))
                      and set(r.get("roles", [r.get("role")])) &
                      {"state_reference", "end_frame" if side == "previous" else "start_frame"}
                      and boundary_shot in reference_shot_ids(r, row)}
            _need(hashes <= actual, "源稿切点参考未进入本次实际输入，不能仅在文字中声明")
    return external_predecessors


def diagnose_scene_state_plan(payload, storyboard, *, segment_ids=None, project=None,
                              source_artifact=None, state=None, strict_prompt_controls=False):
    """Report state coverage in the proposed references, before any native binding."""
    rows = _rows(payload)
    if segment_ids is not None:
        rows = [row for row in rows if row['segment_id'] in segment_ids]
        shots = {shot for row in rows for shot in source_shot_ids(row)}
        storyboard = {'scenes': [scene for scene in storyboard.get('scenes', [])
                                 if any(shot.get('shot_id') in shots for shot in scene.get('shots', []))]}
    requirements = scene_state_requirements(storyboard)
    if project is not None and state is None:
        from .project_state import load_project_state
        state = load_project_state(Path(project))
    report = []
    for segment in rows:
        shots = source_shot_ids(segment)
        needed = [state for state in requirements if set(shots) & set(state["source_shot_ids"])]
        if not needed:
            continue
        result = {"segment_id": segment["segment_id"], "status": "covered"}
        refs = []
        control = None
        try:
            if project is not None:
                from .prompt_scene_control import source_prompt_scene_control
                control = source_prompt_scene_control(segment, payload, project, state,
                                                       source_artifact, requirements)
            for ref in segment.get("materials", segment.get("candidate_upload_order", [])):
                planned = dict(ref)
                if "kind" not in planned:
                    path = planned.get("path")
                    if isinstance(path, str) and Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                        planned["kind"] = "image"
                refs.append(planned)
            validate_scene_state_coverage(
                {"source_shot_ids": shots, "reference_checks": refs}, needed,
                accepted_prompt_control=control is not None)
            if control is not None:
                result.update(status='user_accepted_prompt_control', prompt_state_control=control)
        except ValueError as exc:
            if strict_prompt_controls and (control is not None or 'prompt_state_control' in str(exc)):
                raise
            result.update(status="needs_review", issue=str(exc))
        except TypeError as exc:
            if strict_prompt_controls and control is not None:
                raise ValueError('scene_state: 拟用素材结构无效') from exc
            result.update(status="needs_review", issue="scene_state: 拟用素材结构无效")
        report.append(result)
    return {"scope": "declared plan only; native control and visual observations not checked",
            "segments": report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prompt", type=Path, help="视频稿，支持项目相对路径")
    mode.add_argument("--shot-id", action="append", help="写词前核这些导演镜号的检查结论和视觉依赖，可重复")
    parser.add_argument("--storyboard", type=Path, required=True, help="准确导演稿，支持项目相对路径")
    parser.add_argument("--segment-id", action="append", help="仅自查所列生成段及稿内相邻接缝，可重复；省略时检查整稿")
    args = parser.parse_args(argv)
    if args.shot_id and args.segment_id:
        parser.error('--segment-id 只用于 --prompt')
    try:
        from .artifacts import (_read_confirmed_package_source, load_manifest, project_path,
                                validate_current_dependencies)
        from .project_state import load_project_state
        from .visual_readiness import require_visual_readiness
        project = args.project.resolve()
        board_path = project_path(project, str(args.storyboard))
        manifest = load_manifest(project)
        directors = [item for item in manifest['artifacts']
                     if item.get('logical_id') == 'storyboard.package' and item.get('status') == 'current'
                     and project_path(project, item['path_or_url']) == board_path]
        if args.shot_id:
            if len(directors) != 1:
                raise ValueError('写词前核须使用准确已采用导演稿')
            director = directors[0]
            state = load_project_state(project)
            storyboard, _ = _read_confirmed_package_source(
                project, manifest, state,
                {'revision': director['revision'], 'path': str(board_path), 'artifact_id': director['artifact_id']},
                'storyboard.package')
            validate_current_dependencies(director, manifest, state, project)
            required_visuals(storyboard, shot_ids=set(args.shot_id))
            result = require_visual_readiness(project, storyboard, director, args.shot_id,
                                                   manifest=manifest, require_review=True)
            print(json.dumps({"ok": True, **result}, ensure_ascii=False))
            return 0
        prompt_path = (args.project / args.prompt).resolve()
        prompt_bytes = prompt_path.read_bytes()
        payload = json.loads(prompt_bytes.decode('utf-8-sig'))
        storyboard = json.loads(board_path.read_text(encoding="utf-8-sig"))
        sources = [entry for entry in manifest['artifacts']
                   if entry.get('logical_id') == 'video.prompt'
                   and entry.get('status') in {'candidate', 'current'}
                   and project_path(args.project, entry.get('path_or_url', '')) == prompt_path]
        source_artifact = None
        if sources:
            if len(sources) != 1:
                raise ValueError('prompt_state_control source registration is ambiguous')
            entry, actual = sources[0], hashlib.sha256(prompt_bytes).hexdigest()
            if entry.get('checksum', actual) != actual or entry.get('revision') != payload.get('revision', entry.get('revision')):
                raise ValueError('prompt_state_control source bytes/revision differ from registration')
            source_artifact = {'artifact_id': entry['artifact_id'], 'revision': entry['revision'], 'sha256': actual}
        content_ids = args.segment_id or [row['segment_id'] for row in _rows(payload)]
        result = validate_generation_boundaries(payload, storyboard, args.project, review_segment_ids=args.segment_id,
                                                source_artifact=source_artifact,
                                                prompt_content_segment_ids=content_ids)
        if len(directors) == 1:
            rows = [row for row in _rows(payload) if not args.segment_id or row['segment_id'] in args.segment_id]
            result['visual_readiness'] = require_visual_readiness(
                project, storyboard, directors[0],
                {shot for row in rows for shot in source_shot_ids(row)}, rows=rows, manifest=manifest)
        if (payload.get('scope') or {}).get('type') in {'full_local_authored', 'selected_ranges'}:
            from .delivery_files import declared_input_roots, discover_dependencies
            dependency_payload = _local_dependency_payload(payload, result) if args.segment_id else payload
            dependencies = discover_dependencies(project, payload=dependency_payload,
                                                 input_roots=declared_input_roots(dependency_payload),
                                                 verify_hashes=True)
            if not dependencies['ok']:
                raise ValueError('视频稿输入不可用：' + '; '.join(dependencies['errors'] + dependencies['uncheckable']))
            result['input_files_checked'] = len(dependencies['checked_files'])
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
