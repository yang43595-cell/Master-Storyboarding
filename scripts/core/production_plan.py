"""口播—事件—镜头的结构化生产计划与语义预检。

**已退役（2026-09-21 标注）**：现行视频阶段按 `voiceover-to-video-script` 的入口直接产出
分段 Markdown，不再生成 `production_plan.json`；本模块与 `scripts/validate-production-plan.py`、
`schemas/production-plan*.json` 只用于读取和预检旧项目。新项目不要接线，也不要把它当成待补的缺口。

`production_plan.json` 把「原始口播锁 → 段落事件 → 导演预演 → 多模态输入」的结构化关联落盘，
`validate_production_plan` 在调用即梦 CLI 前做语义预检，防住本项目实际发生的问题：

硬错误（任何 ERROR 禁止提交）：
- `VOICEOVER_HASH_CHANGED`：原口播哈希变化但 revision 未吸收变化。哈希按
  `normalized_text_v1` 归一化（行结束统一为 `\n`）计算，LF/CRLF 内容等价，跨环境检出稳定。
- `AUDIO_POLICY_CONFLICT`：`post_dub_no_lipsync` 提示词残留“说话、台词、同步口型、生成角色语音”。
- `PRODUCT_NOT_AT_END`：`ending_only` 产品段不在最后，或产品段缺真实产品参考路径。
- `REFERENCE_FILE_MISSING`：引用文件（资产 / 预演视频 / 音频 / 可选视觉锚点 / 提示词文件 / 口播源）不存在。
- `DUPLICATE_CURRENT_LOGICAL_ID`：同一 logical_id 同时存在多个 current。

警告（WARN 由用户或主 Agent 决定是否继续）：
- `VOICEOVER_LINE_UNMAPPED` / `VOICEOVER_LINE_DUPLICATED`：口播行未覆盖或重复覆盖。
- `ADJACENT_DUPLICATE_EVENT`：相邻段 event_id 相同且状态无变化。
- `ADJACENT_REPEATED_SHOT`：相邻段景别相同、角色位置相同且没有新动作层。
- `SEGMENT_OVERLOAD`：单段超过 3 次空间切换或超过 4 个主要动作，生成负载过高。
- `DURATION_DECOMPOSITION_INCONSISTENT`：3.2 段声明生成时长小于自身时段分解（对白/动作/空镜/转场）之和，时长算法或该拆未拆。
- `DURATION_DECOMPOSITION_DIALOGUE_MISMATCH`：3.2 段声明的对白秒数与对白登记自然语速之和不符，字数/语速可能算错。
- `RAW_EXCEEDS_TARGET_DURATION`：素材总时长明显高于目标成片，需剪辑重叠。

报告只输出代码与消息，不修改文件。CLI 见 `scripts/validate-production-plan.py`。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schema_contracts import SchemaContractError, validate_prompt_intent, validate_revision_evidence

from .artifacts import load_manifest
from .io import normalized_text_sha256, sha256_file

# 口播文本哈希的权威计算模式：计算前把行结束统一为 \n。
# 仓库内文本按 LF 规范提交，但干净检出（Windows core.autocrlf=true）会转成 CRLF，
# 原始字节哈希会跨环境误报 VOICEOVER_HASH_CHANGED；归一化后 LF/CRLF 内容等价。
VOICEOVER_HASH_MODE = "normalized_text_v1"

_LIPSYNC_RESIDUE_PATTERNS = (
    r"(?<!不)(?<!无)(?<!无需)(?<!禁止)(?<!不要)(?<!别)同步口型",
    r"(?<!不)(?<!无)(?<!无需)(?<!禁止)(?<!不要)(?<!别)生成角色语音",
    r"(?<!不)(?<!无)(?<!无需)(?<!禁止)(?<!不要)(?<!别)生成语音",
    r"(?<!不)(?<!无)(?<!无需)(?<!禁止)(?<!不要)(?<!别)说话",
    r"说(?:出|起)?\s*台词",
    r"说(?:出|起)?\s*对白",
    # 负向断言补词：即使提示词用“不用/无须/避免/停止/不要出现/不加”否定，
    # 提及口型/语音/说话本身在 post_dub_no_lipsync 下就是残留指令（配合口型词绑定，
    # 避免“避免画面闪烁”这类无关否定误报）。
    r"不用(?:生成|加|出现|制造|配音)?\s*(?:角色语音|语音|台词|对白|口型|说话)",
    r"无须(?:生成|加|出现|制造|配音)?\s*(?:角色语音|语音|台词|对白|口型|说话)",
    r"避免(?:生成|加|出现|制造|配音)?\s*(?:角色语音|语音|台词|对白|口型|说话)",
    r"停止(?:生成|加|出现|制造|配音)?\s*(?:角色语音|语音|台词|对白|口型|说话)",
    r"不要出现(?:角色语音|语音|台词|对白|口型|说话)",
    r"不加(?:角色语音|语音|台词|对白|口型|说话)",
)
_POST_DUB_NO_LIPSYNC = "post_dub_no_lipsync"
_ENDING_ONLY = "ending_only"


@dataclass
class PreflightIssue:
    code: str
    level: str  # "ERROR" | "WARN"
    message: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "level": self.level, "message": self.message, "detail": self.detail}


@dataclass
class ValidationReport:
    errors: list[PreflightIssue] = field(default_factory=list)
    warnings: list[PreflightIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def error_codes(self) -> list[str]:
        return [issue.code for issue in self.errors]

    @property
    def warning_codes(self) -> list[str]:
        return [issue.code for issue in self.warnings]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


def validate_production_plan(plan: dict[str, Any], project_root: Path, mode: str = "compat") -> ValidationReport:
    """对 production_plan.json 做语义预检；project_root 为桌面项目目录。"""
    project_root = Path(project_root)
    errors: list[PreflightIssue] = []
    warnings: list[PreflightIssue] = []

    if not isinstance(plan.get("segments"), list):
        errors.append(PreflightIssue("SEGMENTS_MUST_BE_ARRAY", "ERROR", "segments 必须是数组"))
        return ValidationReport(errors, warnings)

    schema_version = str(plan.get("schema_version") or "")
    if schema_version in {"3.2", "3.3"}:
        return _validate_v32(plan, project_root)

    voiceover = plan.get("voiceover_lock") or {}
    segments = list(plan["segments"])
    ordered = sorted(segments, key=lambda s: s.get("order", 0))

    # --- 硬错误 ---
    _check_voiceover_hash(voiceover, project_root, errors)
    audio_policy = plan.get("audio_policy", "")
    product_policy = plan.get("product_policy", "")
    _check_audio_policy_conflict(audio_policy, ordered, project_root, errors)
    _check_product_at_end(product_policy, ordered, project_root, errors)
    _check_reference_files(ordered, voiceover, project_root, errors)
    _check_voice_reference_bindings(plan, ordered, project_root, errors)
    _check_duplicate_current(ordered, errors)
    _check_manifest_duplicate_current(project_root, errors)
    _check_intent_and_revision_evidence(plan, project_root, errors, warnings, mode)
    _check_style_aware_contract(plan, ordered, errors, warnings, mode)

    # --- 警告 ---
    if not audio_policy:
        warnings.append(
            PreflightIssue(
                "AUDIO_POLICY_UNSPECIFIED",
                "WARN",
                "audio_policy 未指定，口型/语音门禁默认不生效，请显式声明（如 post_dub_no_lipsync）",
            )
        )
    _check_voiceover_line_coverage(voiceover, ordered, warnings)
    _check_adjacent_duplicate_event(ordered, warnings)
    _check_adjacent_repeated_shot(ordered, warnings)
    _check_segment_overload(ordered, warnings)
    _check_raw_duration(ordered, project_root, warnings)

    return ValidationReport(errors, warnings)


_STYLE_AWARE_VERSION = "3.1"
_MODES = {"narrative_dialogue", "talking_head", "product_demo", "mechanism_animation", "montage", "single_take", "documentary_observational", "experimental_graphic"}
_EDITING = {"internal_cuts", "continuous_take", "editorial_montage", "hybrid"}
_DIALOGUE = {"visible_dialogue", "offscreen_dialogue", "narration", "mixed", "none"}
_HARD_EXCEPTION_KINDS = {"fixed_camera", "long_take", "jump_cut", "space_break"}


def _read_v32_source(
    project_root: Path,
    ref: Any,
    label: str,
    errors: list[PreflightIssue],
    *,
    require_payload_confirmed: bool = True,
    require_payload_revision: bool = True,
) -> dict[str, Any] | None:
    if not isinstance(ref, dict):
        _issue(errors, "SOURCE_ARTIFACT_INVALID", f"{label} 必须是结构化产物引用")
        return None
    if ref.get("status") != "confirmed":
        _issue(errors, "SOURCE_ARTIFACT_UNCONFIRMED", f"{label} 必须明确为 confirmed", str(ref.get("status")))
        return None
    path = _resolve_project_ref(project_root, ref.get("path"))
    if path is None or not path.is_file():
        _issue(errors, "SOURCE_ARTIFACT_MISSING", f"{label} 路径不存在或越出项目目录", str(ref.get("path")))
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _issue(errors, "SOURCE_ARTIFACT_INVALID", f"{label} 不是可读 JSON", str(exc))
        return None
    if require_payload_revision and payload.get("revision") != ref.get("revision"):
        _issue(errors, "SOURCE_REVISION_MISMATCH", f"{label} revision 与引用不一致", f"{payload.get('revision')} != {ref.get('revision')}")
    if require_payload_confirmed and payload.get("status") != "confirmed":
        _issue(errors, "SOURCE_ARTIFACT_UNCONFIRMED", f"{label} 文件本身不是 confirmed", str(payload.get("status")))
    return payload


def _validate_v32(plan: dict[str, Any], project_root: Path) -> ValidationReport:
    """校验轻量 3.2：只分配来源镜头，不复制导演镜头合同。"""
    errors: list[PreflightIssue] = []
    warnings: list[PreflightIssue] = []
    storyboard = _read_v32_source(project_root, plan.get("source_storyboard"), "source_storyboard", errors)
    narrative = _read_v32_source(project_root, plan.get("source_narrative"), "source_narrative", errors)

    if plan.get("schema_version") == "3.3":
        from .schema_contracts import schema_errors
        from .story_sources import validate_story_sources
        for message in schema_errors(plan, "production-plan.schema.json"):
            _issue(errors, "PLAN_SCHEMA_INVALID", message)
        if narrative and storyboard:
            try:
                validate_story_sources(narrative, storyboard, project_root=project_root)
            except (ValueError, OSError) as exc:
                _issue(errors, "STORY_SOURCE_INVALID", str(exc))

    asset_ref = plan.get("source_assets")
    if asset_ref is not None:
        _read_v32_source(project_root, asset_ref, "source_assets", errors, require_payload_confirmed=False)
    voice_ref = plan.get("source_voice_manifest")
    if voice_ref is not None:
        _read_v32_source(
            project_root,
            voice_ref,
            "source_voice_manifest",
            errors,
            require_payload_confirmed=False,
            require_payload_revision=False,
        )
    for index, ref in enumerate(plan.get("source_visual_supplements") or [], start=1):
        _read_v32_source(project_root, ref, f"source_visual_supplements[{index}]", errors)

    source_shots: list[dict[str, Any]] = []
    if storyboard:
        source_shots = [shot for scene in storyboard.get("scenes") or [] for shot in scene.get("shots") or [] if isinstance(shot, dict)]
    source_shot_ids = [str(shot.get("shot_id")) for shot in source_shots if shot.get("shot_id")]
    shot_by_id = {str(shot.get("shot_id")): shot for shot in source_shots if shot.get("shot_id")}

    segments = sorted(plan.get("segments") or [], key=lambda item: item.get("order", 0))
    assigned: list[str] = []
    orders = [segment.get("order") for segment in segments]
    if orders != list(range(1, len(segments) + 1)):
        _issue(errors, "SEGMENT_ORDER_INVALID", "segments.order 必须从 1 连续递增")
    for segment in segments:
        segment_id = str(segment.get("segment_id") or "?")
        assignment = segment.get("segment_assignment") or {}
        shot_ids = [str(value) for value in assignment.get("source_shot_ids") or []]
        assigned.extend(shot_ids)
        unknown = [value for value in shot_ids if value not in shot_by_id]
        if unknown:
            _issue(errors, "SEGMENT_ASSIGNMENT_UNKNOWN_SHOT", f"{segment_id} 引用了导演包中不存在的镜头", ", ".join(unknown))
        paragraph_mode = plan.get("schema_version") == "3.3"
        source_key = "source_paragraph_ids" if paragraph_mode else "source_event_ids"
        expected = {str(value) for shot_id in shot_ids for value in (shot_by_id.get(shot_id, {}).get(source_key) or [])}
        actual = {str(value) for value in segment.get(source_key) or []}
        if shot_ids and (actual != expected or (paragraph_mode and not actual)):
            code = "SEGMENT_PARAGRAPH_TRACE_MISMATCH" if paragraph_mode else "SEGMENT_EVENT_TRACE_MISMATCH"
            _issue(errors, code, f"{segment_id} 的 {source_key} 必须等于所分配镜头的来源并集")
        expected_dialogues = {str(value) for shot_id in shot_ids for value in (shot_by_id.get(shot_id, {}).get("source_dialogue_ids") or [])}
        actual_dialogues = {str(value) for value in segment.get("source_dialogue_ids") or []}
        if actual_dialogues != expected_dialogues:
            _issue(errors, "SEGMENT_DIALOGUE_TRACE_MISMATCH", f"{segment_id} 的 source_dialogue_ids 必须等于所分配镜头的对白并集")
        for relation in segment.get("semantic_timing_relations") or []:
            if relation.get("dialogue_id") not in actual_dialogues:
                _issue(errors, "SEMANTIC_TIMING_DIALOGUE_OUT_OF_SCOPE", f"{segment_id} 的语义时间关系引用了本段以外对白", str(relation.get("dialogue_id")))
            if not str(relation.get("semantic_reason") or "").strip():
                _issue(errors, "SEMANTIC_TIMING_REASON_MISSING", f"{segment_id} 只有动作确实依赖台词语义时才能登记时间关系")
        for value in [*(segment.get("reference_inputs") or []), segment.get("prompt_path")]:
            path = _resolve_project_ref(project_root, value)
            if path is None or not path.is_file():
                _issue(errors, "REFERENCE_FILE_MISSING", f"{segment_id} 引用文件不存在或越出项目目录", str(value))

    if len(assigned) != len(set(assigned)):
        duplicates = sorted(value for value, count in Counter(assigned).items() if count > 1)
        _issue(errors, "SEGMENT_ASSIGNMENT_DUPLICATED", "同一导演镜头不得分配给多个生成段", ", ".join(duplicates))
    if source_shot_ids and assigned != source_shot_ids:
        missing = [value for value in source_shot_ids if value not in assigned]
        extra = [value for value in assigned if value not in source_shot_ids]
        _issue(errors, "SEGMENT_ASSIGNMENT_INCOMPLETE", "segment_assignment 必须按导演包顺序完整分配全部镜头", f"missing={missing}; extra={extra}")

    narrative_dialogues = {str(item.get("dialogue_id")): item for item in (narrative or {}).get("dialogues") or [] if isinstance(item, dict)}
    registered = {str(item.get("dialogue_id")): item for item in plan.get("dialogue_register") or [] if isinstance(item, dict)}
    required_dialogues = set(storyboard.get("source_dialogue_ids") or []) if storyboard else set()
    if set(registered) != required_dialogues:
        _issue(errors, "DIALOGUE_REGISTER_INCOMPLETE", "dialogue_register 必须且只能登记导演包引用的全部对白")
    for dialogue_id, item in registered.items():
        source = narrative_dialogues.get(dialogue_id)
        if not source:
            _issue(errors, "DIALOGUE_SOURCE_MISSING", f"{dialogue_id} 不存在于叙事包")
            continue
        if item.get("speaker_id") != source.get("speaker_id") or item.get("text") != source.get("text"):
            _issue(errors, "DIALOGUE_EXACTNESS_VIOLATION", f"{dialogue_id} 的说话者或对白文字与叙事包不一致")
        if source.get("status") not in {"locked", "confirmed"}:
            _issue(errors, "DIALOGUE_NOT_FINAL", f"{dialogue_id} 不是 locked/confirmed 最终对白")

    authorization = plan.get("authorization") or {}
    model_confirmed = authorization.get("model_selection_status") == "confirmed_by_user"
    if model_confirmed and (not authorization.get("model_id") or not authorization.get("model_selection_approval_ref")):
        _issue(errors, "MODEL_SELECTION_UNAUTHORIZED", "模型已标记确认但缺少用户选择及授权引用")
    if authorization.get("paid_generation_authorized") and not authorization.get("paid_generation_approval_ref"):
        _issue(errors, "PAID_GENERATION_UNAUTHORIZED", "付费生成必须有用户明确授权引用")
    if authorization.get("submission_authorized"):
        if not authorization.get("submission_approval_ref"):
            _issue(errors, "SUBMISSION_UNAUTHORIZED", "提交必须有用户明确授权引用")
        if not model_confirmed:
            _issue(errors, "MODEL_SELECTION_UNAUTHORIZED", "提交前必须先由用户明确选择模型")

    _check_v32_duration_decomposition(segments, registered, warnings)

    return ValidationReport(errors, warnings)


def _issue(items: list[PreflightIssue], code: str, message: str, detail: str = "") -> None:
    items.append(PreflightIssue(code, "ERROR", message, detail))


def _resolve_project_ref(project_root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    if value.startswith(("http://", "https://", "artifact://", "upload://")):
        return None
    path = Path(value)
    root = project_root.resolve()
    target = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _find_revision_artifact(
    project_root: Path, artifact_ref: str
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        manifest = load_manifest(project_root)
    except (OSError, ValueError) as exc:
        return None, f"artifact_manifest.json 不可读：{exc}"
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return None, "artifact_manifest.json.artifacts 必须是数组"
    artifact_id = artifact_ref.removeprefix("artifact://")
    matches = [
        item
        for item in artifacts
        if isinstance(item, dict) and item.get("artifact_id") == artifact_id
    ]
    if len(matches) != 1:
        return None, f"当前项目 artifact_manifest.json 中找不到唯一产物 {artifact_id}"
    return matches[0], None


def _check_intent_and_revision_evidence(
    plan: dict[str, Any],
    project_root: Path,
    errors: list[PreflightIssue],
    warnings: list[PreflightIssue],
    mode: str,
) -> None:
    """校验已提供的意图/返工合同；shadow 不因缺失新字段阻塞旧项目。"""
    intent_ref = plan.get("intent_contract_ref")
    if intent_ref:
        intent_path = _resolve_project_ref(project_root, intent_ref)
        if intent_path is None or not intent_path.is_file():
            _issue(errors, "INTENT_CONTRACT_REF_MISSING", "intent_contract_ref 不存在或不可验证", str(intent_ref))
        else:
            try:
                payload = json.loads(intent_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                _issue(errors, "INTENT_CONTRACT_INVALID", "意图合同不是可读 JSON", str(exc))
            else:
                intent = payload.get("prompt_intent") if isinstance(payload, dict) and "prompt_intent" in payload else payload
                try:
                    validate_prompt_intent(intent)
                except SchemaContractError as exc:
                    _issue(errors, "INTENT_CONTRACT_INVALID", "意图合同结构无效", str(exc))
                else:
                    if intent.get("intent_status") != "ready":
                        _issue(errors, "INTENT_CONTRACT_INVALID", "视频执行引用的 prompt_intent 必须为 ready")
    elif mode == "shadow" and plan.get("schema_version") == _STYLE_AWARE_VERSION:
        warnings.append(
            PreflightIssue(
                "INTENT_CONTRACT_NOT_ADOPTED",
                "WARN",
                "当前 3.1 计划尚未引用 prompt_intent；shadow 阶段仅提示，不阻塞提交",
            )
        )

    evidence_refs = plan.get("revision_evidence_refs", [])
    if not isinstance(evidence_refs, list):
        _issue(errors, "REVISION_EVIDENCE_INVALID", "revision_evidence_refs 必须是数组")
        return
    for raw_ref in evidence_refs:
        evidence_path = _resolve_project_ref(project_root, raw_ref)
        if evidence_path is None or not evidence_path.is_file():
            _issue(errors, "REVISION_EVIDENCE_REF_MISSING", "返工证据文件不存在或不可验证", str(raw_ref))
            continue
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _issue(errors, "REVISION_EVIDENCE_INVALID", "返工证据不是可读 JSON", str(exc))
            continue
        try:
            validate_revision_evidence(evidence)
        except SchemaContractError as exc:
            _issue(errors, "REVISION_EVIDENCE_INVALID", "返工证据结构无效", f"{raw_ref}: {exc}")
            continue
        if evidence.get("status") != "ready":
            _issue(errors, "REVISION_EVIDENCE_INVALID", "返工证据必须为 schema 1.0 且 status=ready", str(raw_ref))
        if evidence.get("base_revision") != plan.get("revision"):
            _issue(
                errors,
                "REVISION_EVIDENCE_STALE",
                "返工证据 base_revision 与当前 production_plan.revision 不一致",
                f"{evidence.get('base_revision')} != {plan.get('revision')}",
            )
        artifact, artifact_error = _find_revision_artifact(
            project_root, evidence["artifact_ref"]
        )
        if artifact_error:
            _issue(
                errors,
                "REVISION_EVIDENCE_ARTIFACT_MISSING",
                "返工证据未绑定当前项目的真实产物",
                artifact_error,
            )
            continue
        if artifact.get("status") not in {"current", "candidate"}:
            _issue(
                errors,
                "REVISION_EVIDENCE_ARTIFACT_INACTIVE",
                "返工证据引用的产物不是 current 或 candidate",
                f"{artifact.get('artifact_id')}: {artifact.get('status')}",
            )
        if artifact.get("revision") != evidence.get("base_revision"):
            _issue(
                errors,
                "REVISION_EVIDENCE_ARTIFACT_STALE",
                "返工证据引用的产物 revision 与 base_revision 不一致",
                f"{artifact.get('revision')} != {evidence.get('base_revision')}",
            )


def _check_style_aware_contract(plan: dict[str, Any], ordered: list[dict[str, Any]], errors: list[PreflightIssue], warnings: list[PreflightIssue], mode: str) -> None:
    """3.1 的类型化门禁；旧计划只迁移提示，绝不从文本猜测类型。"""
    if plan.get("schema_version") != _STYLE_AWARE_VERSION:
        if mode == "compat":
            return
        target = errors if mode == "enforce-hard" else warnings
        target.append(PreflightIssue("LEGACY_SCHEMA_MIGRATION_REQUIRED", "ERROR" if mode == "enforce-hard" else "WARN", "旧 production_plan 缺少 3.1 视频类型与无损镜头契约；不会静默猜测类型"))
        return
    production_mode, editing_model, dialogue_mode = plan.get("production_mode"), plan.get("editing_model"), plan.get("dialogue_mode")
    if production_mode not in _MODES: _issue(errors, "PRODUCTION_MODE_UNSPECIFIED", "production_mode 必须显式声明有效视频类型")
    if editing_model not in _EDITING: _issue(errors, "EDITING_MODEL_UNSPECIFIED", "editing_model 必须显式声明")
    if dialogue_mode not in _DIALOGUE: _issue(errors, "DIALOGUE_MODE_UNSPECIFIED", "dialogue_mode 必须显式声明")
    source_ids = {str(x.get("shot_id")) for x in plan.get("source_shots") or [] if isinstance(x, dict) and x.get("shot_id")}
    decisions: dict[str, dict[str, Any]] = {}
    contracts: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for seg in ordered:
        for decision in seg.get("coverage_decisions") or []:
            if isinstance(decision, dict) and decision.get("source_shot_id"):
                decisions[str(decision["source_shot_id"])] = decision
        for contract in seg.get("shot_contracts") or []:
            if isinstance(contract, dict): contracts.append((seg, contract))
        _check_segment_contract(seg, production_mode, editing_model, dialogue_mode, errors)
    _check_segment_registry(plan, ordered, errors)
    _check_same_scene_boundaries(plan, ordered, errors)
    for source_id in source_ids:
        decision = decisions.get(source_id)
        if not decision or decision.get("decision") not in {"retained", "merged", "dropped", "postproduction_only"} or not str(decision.get("reason") or "").strip():
            _issue(errors, "SOURCE_CONTRACT_LOST", f"来源镜头 {source_id} 未登记 retained/merged/dropped/postproduction_only 决策与理由", source_id)
        elif decision.get("decision") in {"retained", "merged"} and not any(c.get("source_shot_id") == source_id for _, c in contracts):
            _issue(errors, "SOURCE_CONTRACT_LOST", f"来源镜头 {source_id} 标为 {decision.get('decision')} 但没有下游 shot_contract", source_id)
    _check_profile_rules(plan, ordered, contracts, errors)


def _check_segment_contract(seg: dict[str, Any], production_mode: Any, editing_model: Any, dialogue_mode: Any, errors: list[PreflightIssue]) -> None:
    sid = str(seg.get("id") or "?")
    contracts = [x for x in seg.get("shot_contracts") or [] if isinstance(x, dict)]
    if not contracts:
        _issue(errors, "SOURCE_CONTRACT_LOST", f"段 {sid} 没有可执行 shot_contract")
        return
    budget = seg.get("time_budget")
    if not isinstance(budget, dict) or not isinstance(budget.get("total_seconds"), (int, float)):
        _issue(errors, "SEGMENT_TIME_BUDGET_EXCEEDED", f"段 {sid} 缺少按关键路径表达的 time_budget")
    else:
        # hand_prop_seconds 描述手/道具占用窗口，常与主体串行动作重合；关键路径取两者较大值，
        # 避免同一动作在 serial_action_seconds 与 hand_prop_seconds 中被重复计时。
        action_path = max(
            float(budget.get("serial_action_seconds", 0) or 0),
            float(budget.get("hand_prop_seconds", 0) or 0),
            float(budget.get("parallel_action_seconds", 0) or 0),
            float(budget.get("offscreen_dialogue_seconds", 0) or 0),
        )
        serial = float(budget.get("visible_dialogue_seconds", 0) or 0) + action_path + sum(float(budget.get(k, 0) or 0) for k in ("establishing_seconds", "transition_seconds", "result_hold_seconds", "edit_margin_seconds"))
        capacity = float(budget["total_seconds"])
        if serial > capacity + 0.01 or (seg.get("duration_seconds") is not None and capacity > float(seg["duration_seconds"]) + 0.01):
            _issue(errors, "SEGMENT_TIME_BUDGET_EXCEEDED", f"段 {sid} 串行动作/对白/转场预算 {serial:g}s 超出可用时长 {capacity:g}s")
    _check_internal_shot_sequence(sid, seg, contracts, errors)
    for contract in contracts:
        required = ("shot_id", "source_shot_id", "time_range", "purpose", "must_show", "start_framing", "end_framing", "camera_policy", "subject_actions", "entry_transition", "exit_transition", "continuity_requirements", "control_source", "qc_expectations")
        missing = [key for key in required if not contract.get(key)]
        if missing: _issue(errors, "SOURCE_CONTRACT_LOST", f"段 {sid} 镜头契约缺字段：{', '.join(missing)}", str(contract.get("shot_id") or ""))
        if not contract.get("control_source") or not contract.get("must_show"):
            _issue(errors, "REFERENCE_CONTROL_GAP", f"段 {sid} 镜头 {contract.get('shot_id')} 缺少 must_show 或 control_source")
        _check_transition_pair(sid, contract.get("entry_transition"), "entry", errors)
        _check_transition_pair(sid, contract.get("exit_transition"), "exit", errors)
        occupancy = contract.get("hand_prop_occupancy")
        if isinstance(occupancy, list) and len({str(x) for x in occupancy}) != len(occupancy):
            _issue(errors, "HAND_PROP_OCCUPANCY_CONFLICT", f"段 {sid} 镜头 {contract.get('shot_id')} 同一手/道具状态重复占用")
        if production_mode == "single_take":
            single_take_fields = ("camera_start", "camera_path", "camera_end", "subject_path", "focus_transitions", "internal_beats")
            missing_single_take = [key for key in single_take_fields if not contract.get(key)]
            if missing_single_take:
                _issue(errors, "CAMERA_PLAN_MISSING", f"一镜到底段 {sid} 缺少摄影机/人物路径、焦点迁移或内部节拍：{', '.join(missing_single_take)}")
        if dialogue_mode in {"visible_dialogue", "mixed"} and contract.get("dialogue_window") and contract.get("hand_prop_occupancy") and contract.get("subject_actions"):
            if "同时" in str(contract.get("dialogue_window")) and len(contract.get("subject_actions") or []) > 1:
                _issue(errors, "DIALOGUE_ACTION_COLLISION", f"段 {sid} 可见对白与多项主体动作同时发生，未证明可并行")
    visible = " ".join(str(x) for c in contracts for x in (c.get("must_show") or []))
    required_states = [item.strip() for item in re.split(r"[;；,，|]", str(seg.get("end_state") or "")) if item.strip()]
    missing_states = [state for state in required_states if state not in visible]
    if missing_states:
        _issue(errors, "REQUIRED_STATE_UNCOVERED", f"段 {sid} end_state 未被 shot_contract.must_show 覆盖：{', '.join(missing_states)}")


def _check_internal_shot_sequence(
    sid: str,
    seg: dict[str, Any],
    contracts: list[dict[str, Any]],
    errors: list[PreflightIssue],
) -> None:
    """多来源镜头合并到一个生成段时，内部时间轴和切换必须仍可执行。"""
    if len(contracts) < 2:
        return

    source_shot_ids = [str(value) for value in seg.get("source_shot_ids") or []]
    contract_source_ids = [str(value.get("source_shot_id") or "") for value in contracts]
    if len(set(contract_source_ids)) != len(contract_source_ids) or any(
        source_id not in source_shot_ids for source_id in contract_source_ids
    ):
        _issue(
            errors,
            "INTERNAL_SHOT_SOURCE_ORDER_INVALID",
            f"段 {sid} 的内部镜头必须唯一引用 source_shot_ids 中的来源镜头",
        )
    else:
        positions = [source_shot_ids.index(source_id) for source_id in contract_source_ids]
        if positions != sorted(positions):
            _issue(
                errors,
                "INTERNAL_SHOT_SOURCE_ORDER_INVALID",
                f"段 {sid} 的 shot_contracts 顺序与导演表 source_shot_ids 顺序不一致",
            )

    duration = seg.get("duration_seconds")
    segment_duration = float(duration) if isinstance(duration, (int, float)) else None
    previous_end: float | None = None
    for index, contract in enumerate(contracts):
        time_range = contract.get("time_range")
        if (
            not isinstance(time_range, list)
            or len(time_range) != 2
            or not all(isinstance(value, (int, float)) for value in time_range)
            or float(time_range[0]) < 0
            or float(time_range[1]) <= float(time_range[0])
        ):
            _issue(
                errors,
                "INTERNAL_SHOT_TIMELINE_INVALID",
                f"段 {sid} 内部镜头 {contract.get('shot_id')} 缺少有效递增 time_range",
            )
            continue
        start, end = float(time_range[0]), float(time_range[1])
        if segment_duration is not None and end > segment_duration + 0.01:
            _issue(
                errors,
                "INTERNAL_SHOT_TIMELINE_INVALID",
                f"段 {sid} 内部镜头 {contract.get('shot_id')} 结束时间 {end:g}s 超出生成时长 {segment_duration:g}s",
            )
        if previous_end is not None and start > previous_end + 0.01:
            _issue(
                errors,
                "INTERNAL_SHOT_TIMELINE_INVALID",
                f"段 {sid} 内部镜头时间轴在 {previous_end:g}s 到 {start:g}s 之间存在未解释空档",
            )
        if previous_end is not None and start < previous_end - 0.01:
            _issue(
                errors,
                "INTERNAL_SHOT_TIMELINE_INVALID",
                f"段 {sid} 内部镜头时间窗重叠，切点时刻不唯一",
            )
        previous_end = max(previous_end or end, end)

        if index == 0:
            continue
        previous = contracts[index - 1]
        exit_transition = previous.get("exit_transition")
        entry_transition = contract.get("entry_transition")
        if not isinstance(exit_transition, dict) or not isinstance(entry_transition, dict):
            _issue(
                errors,
                "INTERNAL_TRANSITION_INCOMPLETE",
                f"段 {sid} 的 {previous.get('shot_id')} → {contract.get('shot_id')} 必须成对登记段内切出与入画契约",
            )
            continue
        required = (
            "type",
            "out_framing",
            "in_framing",
            "direction",
            "speed",
            "composition",
            "action_state",
            "prop_state",
            "cut_condition",
            "arrival_state",
        )
        if any(not transition.get(key) for transition in (exit_transition, entry_transition) for key in required):
            _issue(
                errors,
                "INTERNAL_TRANSITION_INCOMPLETE",
                f"段 {sid} 的 {previous.get('shot_id')} → {contract.get('shot_id')} 缺少构图、方向、速度、动作/道具状态、切出条件或入画状态",
            )
            continue
        shared = ("type", "out_framing", "in_framing", "direction", "speed", "composition", "action_state", "prop_state")
        mismatched = [key for key in shared if exit_transition.get(key) != entry_transition.get(key)]
        if mismatched:
            _issue(
                errors,
                "INTERNAL_TRANSITION_MISMATCH",
                f"段 {sid} 的 {previous.get('shot_id')} → {contract.get('shot_id')} 段内转场两侧不一致：{', '.join(mismatched)}",
            )


def _check_transition_pair(sid: str, value: Any, label: str, errors: list[PreflightIssue]) -> None:
    if value in (None, "none", "无", ""):
        return
    if not isinstance(value, dict) or any(not value.get(k) for k in ("type", "out_framing", "in_framing", "direction", "cut_condition", "arrival_state")):
        _issue(errors, "TRANSITION_PAIR_INCOMPLETE", f"段 {sid} {label}_transition 必须成对写类型、出入画构图、方向、切出条件和入画状态")


def _check_segment_registry(
    plan: dict[str, Any],
    ordered: list[dict[str, Any]],
    errors: list[PreflightIssue],
) -> None:
    """3.1 在交付前必须一次性登记全部段，避免“下一段”指代漂移。"""
    registry = plan.get("segment_registry")
    if not isinstance(registry, dict):
        _issue(errors, "SEGMENT_REGISTRY_MISSING", "3.1 production_plan 缺少完整 segment_registry，不得边生成边决定下一段")
        return

    ordered_ids = [str(seg.get("id") or "") for seg in ordered]
    registered_ids = registry.get("ordered_segment_ids")
    if registered_ids != ordered_ids or not ordered_ids or len(set(ordered_ids)) != len(ordered_ids):
        _issue(
            errors,
            "SEGMENT_REGISTRY_INCONSISTENT",
            "segment_registry.ordered_segment_ids 必须与 segments 完整、唯一且顺序一致",
        )
        return

    current_id = registry.get("current_segment_id")
    next_id = registry.get("next_segment_id")
    final_id = registry.get("final_segment_id")
    if current_id not in ordered_ids or final_id != ordered_ids[-1]:
        _issue(errors, "SEGMENT_POSITION_AMBIGUOUS", "segment_registry 必须明确有效 current_segment_id 与最后一段 final_segment_id")
        return
    current_index = ordered_ids.index(str(current_id))
    expected_next = ordered_ids[current_index + 1] if current_index + 1 < len(ordered_ids) else None
    if next_id != expected_next:
        _issue(
            errors,
            "SEGMENT_POSITION_AMBIGUOUS",
            f"segment_registry.next_segment_id 必须是 current 后的相邻段；预期 {expected_next!r}，实际 {next_id!r}",
        )

    for seg in ordered:
        if not str(seg.get("scene_id") or "").strip():
            _issue(errors, "SEGMENT_SCENE_ID_MISSING", f"段 {seg.get('id')} 缺少 scene_id，无法判断同场景是否被误拆")


def _check_same_scene_boundaries(
    plan: dict[str, Any],
    ordered: list[dict[str, Any]],
    errors: list[PreflightIssue],
) -> None:
    """同一场景默认单段；例外拆段须有可回验理由与成对连续性契约。"""
    for index in range(len(ordered) - 1):
        left, right = ordered[index], ordered[index + 1]
        scene_id = str(left.get("scene_id") or "").strip()
        if not scene_id or scene_id != str(right.get("scene_id") or "").strip():
            continue

        left_id, right_id = str(left.get("id") or "?"), str(right.get("id") or "?")
        split = right.get("same_scene_split")
        if not isinstance(split, dict) or split.get("from_segment_id") != left_id:
            _issue(
                errors,
                "SAME_SCENE_SPLIT_UNJUSTIFIED",
                f"同场景 {scene_id} 的 {left_id}/{right_id} 默认应合并；拆段必须在后段登记 same_scene_split",
            )
            continue

        reason = split.get("reason")
        evidence = str(split.get("evidence") or "").strip()
        justified = False
        if reason == "model_duration_exceeded":
            model = plan.get("selected_video_model")
            estimate = split.get("estimated_scene_duration_seconds")
            if not isinstance(model, dict) or not model.get("id") or model.get("confirmed_by_user") is not True or not isinstance(model.get("max_duration_seconds"), (int, float)):
                _issue(errors, "SAME_SCENE_SPLIT_MODEL_UNCONFIRMED", f"同场景 {left_id}/{right_id} 以时长拆段，但缺少用户已确认模型及其最大时长")
            elif isinstance(estimate, (int, float)) and float(estimate) > float(model["max_duration_seconds"]) and evidence:
                justified = True
        if not justified:
            _issue(
                errors,
                "SAME_SCENE_SPLIT_UNJUSTIFIED",
                f"同场景 {left_id}/{right_id} 只能在完整场景估算时长超过用户已确认模型最大时长时拆分",
            )

        left_contracts = [item for item in left.get("shot_contracts") or [] if isinstance(item, dict)]
        right_contracts = [item for item in right.get("shot_contracts") or [] if isinstance(item, dict)]
        if not left_contracts or not right_contracts:
            continue  # _check_segment_contract 已报 SOURCE_CONTRACT_LOST
        exit_transition = left_contracts[-1].get("exit_transition")
        entry_transition = right_contracts[0].get("entry_transition")
        required = (
            "type",
            "out_framing",
            "in_framing",
            "direction",
            "speed",
            "composition",
            "action_state",
            "prop_state",
            "cut_condition",
            "arrival_state",
        )
        if not isinstance(exit_transition, dict) or not isinstance(entry_transition, dict) or any(
            not transition.get(key)
            for transition in (exit_transition, entry_transition)
            for key in required
        ):
            _issue(
                errors,
                "SAME_SCENE_TRANSITION_INCOMPLETE",
                f"同场景拆段 {left_id}/{right_id} 的 exit/entry 必须成对写方向、速度、构图、动作状态、道具状态与切入条件",
            )
            continue
        shared = ("type", "out_framing", "in_framing", "direction", "speed", "composition", "action_state", "prop_state")
        mismatches = [key for key in shared if exit_transition.get(key) != entry_transition.get(key)]
        if mismatches:
            _issue(
                errors,
                "SAME_SCENE_TRANSITION_MISMATCH",
                f"同场景拆段 {left_id}/{right_id} 的 exit/entry 契约不一致：{', '.join(mismatches)}",
            )


def _check_profile_rules(plan: dict[str, Any], ordered: list[dict[str, Any]], contracts: list[tuple[dict[str, Any], dict[str, Any]]], errors: list[PreflightIssue]) -> None:
    mode, editing = plan.get("production_mode"), plan.get("editing_model")
    exceptions = list(plan.get("intentional_exceptions") or []) + [x for s in ordered for x in (s.get("intentional_exceptions") or [])]
    for exc in exceptions:
        if not isinstance(exc, dict) or not all(str(exc.get(k) or "").strip() for k in ("kind", "reason", "scope", "acceptance_criteria", "approval_ref")):
            _issue(errors, "INTENTIONAL_EXCEPTION_UNJUSTIFIED", "主动例外必须有理由、范围、验收标准和用户/契约批准引用")
    has_exception = lambda kind: any(isinstance(x, dict) and x.get("kind") == kind and x.get("approval_ref") for x in exceptions)
    if editing in {"internal_cuts", "editorial_montage", "hybrid"} and mode not in {"talking_head", "documentary_observational"}:
        for seg in ordered:
            segment_contracts = [item for item in seg.get("shot_contracts") or [] if isinstance(item, dict)]
            segment_exceptions = list(plan.get("intentional_exceptions") or []) + list(seg.get("intentional_exceptions") or [])
            segment_has_long_take = any(isinstance(x, dict) and x.get("kind") == "long_take" and x.get("approval_ref") for x in segment_exceptions)
            if len(segment_contracts) < 2 and not segment_has_long_take:
                _issue(errors, "SHOT_VARIETY_INSUFFICIENT", f"段 {seg.get('id')} 的类型需要多景别/多镜覆盖；固定或长镜必须有批准例外")
    if mode == "narrative_dialogue" and "non_livestream" in (plan.get("style_requirements") or []):
        static = sum(1 for _, c in contracts if "固定" in str(c.get("camera_policy")))
        if static == len(contracts) and not has_exception("fixed_camera"):
            _issue(errors, "STATIC_TALKING_DEMO_RISK", "non_livestream 剧情全部固定正面镜头，缺少批准例外")
    if mode == "narrative_dialogue" and plan.get("dialogue_mode") in {"visible_dialogue", "mixed"}:
        if not any("反应" in " ".join(map(str, c.get("must_show") or [])) or "reaction" in " ".join(map(str, c.get("must_show") or [])).lower() for _, c in contracts):
            _issue(errors, "REACTION_COVERAGE_MISSING", "可见剧情对白缺少听者反应或反应镜覆盖")
    if mode == "product_demo" and not any("operation" in " ".join(map(str, c.get("must_show") or [])).lower() or "操作" in " ".join(map(str, c.get("must_show") or [])) for _, c in contracts):
        _issue(errors, "PRODUCT_OPERATION_STEP_MISSING", "产品演示缺少可见关键操作状态")
    if mode == "mechanism_animation" and not any("因果" in " ".join(map(str, c.get("qc_expectations") or [])) or "causal" in " ".join(map(str, c.get("qc_expectations") or [])).lower() for _, c in contracts):
        _issue(errors, "MECHANISM_CAUSAL_STEP_MISSING", "机制动画缺少可回验的因果步骤")


def _check_voiceover_hash(
    voiceover: dict[str, Any],
    project_root: Path,
    errors: list[PreflightIssue],
) -> None:
    source_path = voiceover.get("source_path")
    recorded_hash = voiceover.get("sha256")
    if not source_path or not recorded_hash:
        return
    current = _voiceover_source_hashes(project_root, str(source_path))
    if current is None:
        # 有 sha256 记录却找不到源文件：哈希门禁原本会被静默跳过，现在显式报错。
        errors.append(
            PreflightIssue(
                "REFERENCE_FILE_MISSING",
                "ERROR",
                f"口播源文件缺失，但 sha256 记录存在：{source_path}",
                detail=str(source_path),
            )
        )
        return
    if not _voiceover_hash_matches(recorded_hash, current, voiceover.get("hash_mode")):
        errors.append(
            PreflightIssue(
                "VOICEOVER_HASH_CHANGED",
                "ERROR",
                "原口播哈希与口播源当前内容不一致，revision 未吸收该变化",
                detail=str(source_path),
            )
        )


def _check_audio_policy_conflict(
    audio_policy: Any,
    ordered: list[dict[str, Any]],
    project_root: Path,
    errors: list[PreflightIssue],
) -> None:
    if audio_policy != _POST_DUB_NO_LIPSYNC:
        return
    for seg in ordered:
        text = _segment_prompt_text(seg, project_root)
        if not text:
            continue
        for pattern in _LIPSYNC_RESIDUE_PATTERNS:
            if re.search(pattern, text):
                errors.append(
                    PreflightIssue(
                        "AUDIO_POLICY_CONFLICT",
                        "ERROR",
                        f"post_dub_no_lipsync 段 {seg.get('id')} 提示词残留口型/台词指令",
                        detail=f"matched={pattern}",
                    )
                )
                break


def _check_product_at_end(
    product_policy: Any,
    ordered: list[dict[str, Any]],
    project_root: Path,
    errors: list[PreflightIssue],
) -> None:
    product_segs = [seg for seg in ordered if seg.get("product_appearance") is True]
    if product_policy != _ENDING_ONLY or not product_segs:
        return
    last_non_product_index = -1
    first_product_index = len(ordered)
    for index, seg in enumerate(ordered):
        if seg.get("product_appearance") is True:
            first_product_index = min(first_product_index, index)
        else:
            last_non_product_index = index
    if first_product_index <= last_non_product_index:
        errors.append(
            PreflightIssue(
                "PRODUCT_NOT_AT_END",
                "ERROR",
                "ending_only 模式下产品段必须在最后，但产品段之后仍有其他段",
            )
        )
    for seg in product_segs:
        refs = list(_iter_reference_paths(seg))
        if not any(_is_credible_product_reference(r, project_root) for r in refs):
            errors.append(
                PreflightIssue(
                    "PRODUCT_NOT_AT_END",
                    "ERROR",
                    f"产品段 {seg.get('id')} 缺少可信产品参考图（product-references、01_原始资料、04_资产，或旧项目参考目录下存在的文件）",
                    detail=str(seg.get("id") or ""),
                )
            )


def _is_credible_product_reference(ref: str, project_root: Path) -> bool:
    """产品段参考是否可信：产品参考目录，或项目原始资料、资产及旧版参考目录中的文件。

    Fix 5：临时素材目录下的参考帧同样承载产品视觉（比在 assets/ 之外另起路径），
    不应被误报为“缺真实产品参考图”。远端上传附件/URL 不算（handoff 另有规则）。
    """
    if not ref or not ref.strip():
        return False
    normalized = str(ref).replace("\\", "/")
    if "product-references" in normalized:
        return True
    if normalized.startswith("upload://") or normalized.startswith(("http://", "https://")):
        return False
    path = _resolve(project_root, str(ref))
    return path.is_file() and bool({"01_原始资料", "04_资产", "06_参考帧与临时素材"}.intersection(path.parts))


def _check_reference_files(
    ordered: list[dict[str, Any]],
    voiceover: dict[str, Any],
    project_root: Path,
    errors: list[PreflightIssue],
) -> None:
    for seg in ordered:
        for ref in _iter_reference_paths(seg):
            if not _resolve_exists(project_root, str(ref)):
                errors.append(
                    PreflightIssue(
                        "REFERENCE_FILE_MISSING",
                        "ERROR",
                        f"段 {seg.get('id')} 引用文件不存在：{ref}",
                        detail=str(seg.get("id") or ""),
                    )
                )
        prompt_file = seg.get("prompt_file")
        if prompt_file and not _resolve_exists(project_root, str(prompt_file)):
            errors.append(
                PreflightIssue(
                    "REFERENCE_FILE_MISSING",
                    "ERROR",
                    f"段 {seg.get('id')} 提示词文件不存在：{prompt_file}",
                    detail=str(seg.get("id") or ""),
                )
            )
    source_path = voiceover.get("source_path")
    if source_path and not _resolve_exists(project_root, str(source_path)):
        errors.append(
            PreflightIssue(
                "REFERENCE_FILE_MISSING",
                "ERROR",
                f"口播源文件不存在：{source_path}",
                detail=str(source_path),
            )
        )


def _iter_reference_paths(seg: dict[str, Any]):
    """遍历新多模态输入与旧 reference_images，去重但保留稳定顺序。"""
    seen: set[str] = set()
    for item in seg.get("reference_inputs") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path and path not in seen:
            seen.add(path)
            yield path
    for item in seg.get("reference_images") or []:
        path = str(item or "").strip()
        if path and path not in seen:
            seen.add(path)
            yield path


def _check_manifest_duplicate_current(
    project_root: Path,
    errors: list[PreflightIssue],
) -> None:
    """清单中同一 logical_id 多 current → DUPLICATE_CURRENT_LOGICAL_ID（Fix 6）。

    段级检查只看 production_plan 自身；清单是产物状态的事实权威，
    D2 的登记 promote 已在源头杜绝，此处作为存量数据的兜底。
    """
    manifest_path = project_root / "00_项目管理" / "artifact_manifest.json"
    if not manifest_path.exists():
        return
    try:
        manifest = load_manifest(project_root)
    except (OSError, ValueError):
        return
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or any(
        not isinstance(item, dict) for item in artifacts
    ):
        _issue(
            errors,
            "ARTIFACT_MANIFEST_INVALID",
            "artifact_manifest.json.artifacts 必须是对象数组",
        )
        return
    current_by_logical: dict[str, list[str]] = {}
    for item in artifacts:
        if item.get("status") != "current":
            continue
        logical_id = item.get("logical_id")
        if not logical_id:
            continue
        current_by_logical.setdefault(str(logical_id), []).append(str(item.get("artifact_id")))
    for logical_id, ids in sorted(current_by_logical.items()):
        if len(ids) > 1:
            errors.append(
                PreflightIssue(
                    "DUPLICATE_CURRENT_LOGICAL_ID",
                    "ERROR",
                    f"清单中同一 logical_id 存在多个 current：{logical_id}（{', '.join(ids)}）",
                    detail=str(logical_id),
                )
            )


def _check_voice_reference_bindings(
    plan: dict[str, Any],
    ordered: list[dict[str, Any]],
    project_root: Path,
    errors: list[PreflightIssue],
) -> None:
    used_characters = {
        str(item.get("character_id") or "")
        for segment in ordered
        for item in (segment.get("speaking_characters") or [])
        if isinstance(item, dict) and item.get("character_id")
    }
    if not used_characters:
        return
    if plan.get("audio_policy") != "voice_reference":
        errors.append(
            PreflightIssue(
                "VOICE_REFERENCE_POLICY_CONFLICT",
                "ERROR",
                "段落已登记 speaking_characters，但 audio_policy 不是 voice_reference",
            )
        )
    manifest_raw = plan.get("voice_reference_manifest_path")
    manifest_path = _resolve_project_ref(project_root, manifest_raw)
    if manifest_path is None or not manifest_path.is_file():
        errors.append(
            PreflightIssue(
                "VOICE_REFERENCE_MANIFEST_MISSING",
                "ERROR",
                "出声角色需要项目 voice_reference_manifest.json",
                detail=str(manifest_raw or ""),
            )
        )
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(PreflightIssue("VOICE_REFERENCE_MANIFEST_INVALID", "ERROR", "项目声音清单不可读", str(exc)))
        return
    assignments: dict[str, dict[str, Any]] = {}
    for item in manifest.get("assignments") or []:
        if not isinstance(item, dict) or item.get("status") != "current":
            continue
        character_id = str(item.get("character_id") or "")
        if character_id in assignments:
            errors.append(
                PreflightIssue(
                    "VOICE_REFERENCE_BINDING_CONFLICT",
                    "ERROR",
                    f"同一角色存在多个 current 声音绑定：{character_id}",
                )
            )
            continue
        assignments[character_id] = item
    for character_id in sorted(used_characters):
        assignment = assignments.get(character_id)
        if assignment is None:
            errors.append(
                PreflightIssue(
                    "VOICE_REFERENCE_BINDING_MISSING",
                    "ERROR",
                    f"出声角色没有声音母版绑定：{character_id}",
                )
            )
            continue
        if assignment.get("confirmed_by_user") is not True or not str(assignment.get("approval_ref") or "").strip():
            errors.append(
                PreflightIssue(
                    "VOICE_REFERENCE_APPROVAL_MISSING",
                    "ERROR",
                    f"角色声音母版缺少用户确认记录：{character_id}",
                )
            )
            continue
        snapshot = _resolve_project_ref(project_root, assignment.get("snapshot_path"))
        if snapshot is None or not snapshot.is_file() or snapshot.stat().st_size <= 0:
            errors.append(
                PreflightIssue(
                    "VOICE_REFERENCE_FILE_MISSING",
                    "ERROR",
                    f"角色声音快照不存在或为空：{character_id}",
                    detail=str(assignment.get("snapshot_path") or ""),
                )
            )
            continue
        expected = str(assignment.get("sha256") or "")
        if not expected or sha256_file(snapshot) != expected:
            errors.append(
                PreflightIssue(
                    "VOICE_REFERENCE_DRIFT",
                    "ERROR",
                    f"角色声音快照与项目锁定记录不一致：{character_id}",
                )
            )


def _check_duplicate_current(ordered: list[dict[str, Any]], errors: list[PreflightIssue]) -> None:
    current_ids = [seg.get("id") for seg in ordered if seg.get("status") == "current"]
    for duplicate in sorted({item for item in current_ids if current_ids.count(item) > 1}):
        errors.append(
            PreflightIssue(
                "DUPLICATE_CURRENT_LOGICAL_ID",
                "ERROR",
                f"同一 logical_id 存在多个 current：{duplicate}",
                detail=str(duplicate),
            )
        )


def _check_voiceover_line_coverage(
    voiceover: dict[str, Any],
    ordered: list[dict[str, Any]],
    warnings: list[PreflightIssue],
) -> None:
    all_lines = [line.get("id") for line in voiceover.get("lines") or [] if line.get("id")]
    covered: list[str] = []
    for seg in ordered:
        covered.extend(str(item) for item in seg.get("voiceover_line_ids") or [])
    counts = Counter(covered)
    for line_id in all_lines:
        if counts.get(line_id, 0) == 0:
            warnings.append(
                PreflightIssue("VOICEOVER_LINE_UNMAPPED", "WARN", f"口播行 {line_id} 未被任何段覆盖")
            )
        elif counts.get(line_id, 0) > 1:
            warnings.append(
                PreflightIssue(
                    "VOICEOVER_LINE_DUPLICATED",
                    "WARN",
                    f"口播行 {line_id} 被 {counts[line_id]} 个段覆盖",
                )
            )


def _check_adjacent_duplicate_event(
    ordered: list[dict[str, Any]],
    warnings: list[PreflightIssue],
) -> None:
    for index in range(len(ordered) - 1):
        left, right = ordered[index], ordered[index + 1]
        if (
            left.get("event_id")
            and left.get("event_id") == right.get("event_id")
            and left.get("start_state") == right.get("start_state")
            and left.get("end_state") == right.get("end_state")
        ):
            warnings.append(
                PreflightIssue(
                    "ADJACENT_DUPLICATE_EVENT",
                    "WARN",
                    f"相邻段 {left.get('id')}/{right.get('id')} event 相同且状态无变化",
                )
            )


def _check_adjacent_repeated_shot(
    ordered: list[dict[str, Any]],
    warnings: list[PreflightIssue],
) -> None:
    for index in range(len(ordered) - 1):
        left, right = ordered[index], ordered[index + 1]
        left_strategy = left.get("shot_strategy")
        if not left_strategy or left_strategy != right.get("shot_strategy"):
            continue
        if not right.get("new_actions"):
            warnings.append(
                PreflightIssue(
                    "ADJACENT_REPEATED_SHOT",
                    "WARN",
                    f"相邻段 {left.get('id')}/{right.get('id')} 景别相同且未声明新动作层",
                )
            )


def _check_segment_overload(
    ordered: list[dict[str, Any]],
    warnings: list[PreflightIssue],
) -> None:
    for seg in ordered:
        space_switches = int(seg.get("space_switches", 0) or 0)
        main_actions = seg.get("main_actions") or []
        if space_switches > 3 or len(main_actions) > 4:
            warnings.append(
                PreflightIssue(
                    "SEGMENT_OVERLOAD",
                    "WARN",
                    f"段 {seg.get('id')} 生成负载过高（空间切换 {space_switches} 次 / 主动作 {len(main_actions)} 个）",
                )
            )


def _check_v32_duration_decomposition(
    segments: list[dict[str, Any]],
    registered: dict[str, dict[str, Any]],
    warnings: list[PreflightIssue],
) -> None:
    """3.2 时长自洽：模型按内容语义填的时段分解须自洽，并与对白登记吻合。

    四项分解保留各自完整用时，只扣除有依据的对白与动作重叠秒数；缺省按串行。
    再查两处自洽 —— ① 声明生成时长覆盖扣除重叠后的用时；② 声明的对白秒数应与
    dialogue_register 中自然语速估时之和一致（防「字数/语速算错」那条）。只作 WARN。
    """
    tolerance = 1.0
    for segment in segments:
        decomp = segment.get("duration_decomposition")
        if not isinstance(decomp, dict):
            continue  # 可选字段，旧计划没有就跳过
        segment_id = str(segment.get("segment_id") or "?")
        components = sum(
            float(decomp.get(key) or 0) for key in ("dialogue_seconds", "action_seconds", "establishing_seconds", "transition_seconds")
        )
        overlap = float(decomp.get("dialogue_action_overlap_seconds") or 0)
        overlap_limit = min(float(decomp.get("dialogue_seconds") or 0), float(decomp.get("action_seconds") or 0))
        if not 0 <= overlap <= overlap_limit:
            warnings.append(PreflightIssue(
                "DURATION_OVERLAP_INVALID", "WARN",
                f"段 {segment_id} 对白与动作重叠 {overlap:g}s 必须在 0–{overlap_limit:g}s 内；不扣除无效重叠",
            ))
        else:
            components -= overlap
        declared = segment.get("planned_generation_duration_seconds")
        if isinstance(declared, (int, float)) and components > float(declared) + tolerance:
            warnings.append(
                PreflightIssue(
                    "DURATION_DECOMPOSITION_INCONSISTENT",
                    "WARN",
                    f"段 {segment_id} 声明生成时长 {declared}s 小于实际分解用时 {components:g}s"
                    f"（对白+动作+空镜+转场−有效对白动作重叠），请回读导演依据复核时长与承接，不自动拆段",
                )
            )
        stated_dialogue = decomp.get("dialogue_seconds")
        derived_dialogue = sum(
            float(registered.get(value, {}).get("natural_duration_seconds") or 0)
            for value in segment.get("source_dialogue_ids") or []
        )
        if isinstance(stated_dialogue, (int, float)) and derived_dialogue and abs(float(stated_dialogue) - derived_dialogue) > tolerance:
            warnings.append(
                PreflightIssue(
                    "DURATION_DECOMPOSITION_DIALOGUE_MISMATCH",
                    "WARN",
                    f"段 {segment_id} 声明的对白秒数 {stated_dialogue:g}s 与对白登记自然语速之和 {derived_dialogue:g}s 不符，请复核",
                )
            )


def _check_raw_duration(
    ordered: list[dict[str, Any]],
    project_root: Path,
    warnings: list[PreflightIssue],
) -> None:
    total = sum(float(seg.get("duration_seconds") or 0) for seg in ordered)
    target = _target_duration_seconds(project_root)
    if target and total > target * 1.5:
        warnings.append(
            PreflightIssue(
                "RAW_EXCEEDS_TARGET_DURATION",
                "WARN",
                f"素材总时长 {total:.1f}s 明显高于目标成片 {target}s，需剪辑重叠",
            )
        )


def _segment_prompt_text(seg: dict[str, Any], project_root: Path) -> str:
    parts: list[str] = [str(seg.get("prompt_text") or "")]
    prompt_file = seg.get("prompt_file")
    if prompt_file:
        path = _resolve(project_root, str(prompt_file))
        if path.exists():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _voiceover_source_hashes(project_root: Path, source_path: str) -> dict[str, str] | None:
    path = _resolve(project_root, source_path)
    if not path.exists() or not path.is_file():
        return None
    return {"normalized": normalized_text_sha256(path), "raw": sha256_file(path)}


def _voiceover_hash_matches(recorded_hash: str, current: dict[str, str], hash_mode: str | None) -> bool:
    """按记录模式比较口播哈希。

    - `normalized_text_v1`（当前权威）：只比较归一化哈希，LF/CRLF 等价。
    - 无 hash_mode 的 legacy 记录：接受归一化或原始任一匹配。原始字节完全一致时
      两值都匹配；仅换行差异时归一化匹配；真实内容变化时两者都失配——因此该
      放宽只抑制换行差异，不会放过真实修改（无假阴性）。
    """
    if hash_mode == VOICEOVER_HASH_MODE:
        return recorded_hash == current["normalized"]
    return recorded_hash in (current["normalized"], current["raw"])


def _target_duration_seconds(project_root: Path) -> float | None:
    request_path = project_root / "00_项目管理" / "project_request.json"
    if not request_path.exists():
        return None
    try:
        value = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", str(value.get("target_duration") or ""))
    return float(match.group(1)) if match else None


def _resolve(project_root: Path, ref: str) -> Path:
    candidate = Path(ref)
    return candidate if candidate.is_absolute() else project_root / candidate


def _resolve_exists(project_root: Path, ref: str) -> bool:
    if not ref or not ref.strip():
        return False
    path = _resolve(project_root, ref)
    return path.exists()
