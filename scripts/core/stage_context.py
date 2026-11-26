#!/usr/bin/env python3
"""Prepare and validate stage inputs, reading evidence and drift checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from contextlib import ExitStack
from contextvars import ContextVar
from functools import cache, wraps
from pathlib import Path, PureWindowsPath
from typing import Any

from .errors import StageContextError
from .agent_acceptance import accept_artifact, register_workflow_authorization, require_workflow_authorization
from .review_publication import adopt_chat_script, adopt_package, is_recorded_website_script_approval
from .method_documents import (
    _CROSS_SKILL_READS, _CROSS_SKILL_DEPENDENCIES, _REDFRAME_IMAGE_STANDARDS, _redframe_image_batch,
    local_standard_documents, MethodDocumentError, render_method_packet, resolve_method_documents,
)
from .candidate_test import (
    context as test_context, configure as configure_test, resolve_inputs as test_inputs,
    result_metadata as test_metadata, require_production, require_production_manifest, validate_stage as validate_test_stage,
    validate_usage_contract,
)
from .artifacts import (
    artifact_is_adopted, exact_narrative_approval, project_path, digest, cache_file_digests,
    validate_current_dependencies, load_manifest, pending_narrative_candidate, save_manifest,
    validate_candidate_submission,
)
from .asset_refs import check_output_plan, registered_output_snapshot
from .asset_batch_receipt import (
    DIRECT_RECEIPTS, DIRECT_REQUESTS, summarize_asset_batch_receipt, validate_asset_batch_targets,
)
from .character_layout import character_layout_references, character_prompt_templates, character_template_kinds
from .io import (
    write_text, write_text_if_changed, PROJECT_LOCK_RELATIVE, file_lock, read_stage_json as _read_json,
    write_json,
)
from .knowledge_runtime import (
    resolve_knowledge_root, resolve_knowledge_bundle, is_bundled_root, verify_bundle, verify_selected_files,
)
from .knowledge_blocks import build_knowledge_index, load_policy, parse_knowledge_blocks
from .knowledge_selector import (
    render_knowledge_bundle, render_knowledge_candidates, render_candidate_trial_bundle, select_knowledge,
)
from .candidate_source_snapshot import (
    CandidateSnapshotError, inspect_candidate_snapshot, load_candidate_snapshot_rules,
)
from .product_reference_pack import resolve_product_pack
from .project_state import load_project_state, normalize_project_state, write_project_state
from .production_handoff import (
    LEGACY_RULE, recreation_prompt_context, require_production_handoff, resolve_production_handoff,
)
from .project_kickoff import (
    REQUEST_PATH, RECREATION_METHOD, RECREATION_WORKFLOW, bind_stage_request, kickoff_project, load_kickoff,
    load_project_request, product_demo_timing_advisory, production_workflow,
)
from .revision_control import RevisionControlError, guard_action, inspect_revision_status, open_change, show_change
from .review_delta import fingerprint, revision_delta, lightweight_revision

try:
    from ..session_usage import checkpoint
except ImportError:  # direct script entry uses the top-level core package
    from session_usage import checkpoint


ROOT = Path(__file__).resolve().parents[2]
CONTEXT_DIR = Path("00_项目管理") / "执行上下文"
RECOVERY_DOCUMENT = Path("skills/_shared/knowledge-access.md")
RECOVERY_HEADING = "压缩后的主动恢复核对"
LIMITS = {"startup": 48 * 1024, "review": 12 * 1024, "handoff": 8 * 1024}
BANNED = {
    "图片载荷": re.compile(r"data:image|image_url|base64[,;]", re.I),
    "原始聊天": re.compile(r"(?:^|\n)#{1,4}\s*(?:原始聊天|完整对话|chat history)", re.I),
    "旧运行协议": re.compile("|".join(("required_context_" + "manifest", "dispatch_" + "envelope", "execution_" + "receipt", "visual_context_" + "gate")), re.I),
    "内部推理": re.compile(r"(?:chain.of.thought|内部推理|reasoning trace)", re.I),
}


_CONTEXT_ID: ContextVar[str | None] = ContextVar("stage_context_id", default=None)
IMAGE_SKILLS = {"asset-image-assistant", "storyboard-director"}
TASK_HANDOFF_SKILLS = {"creative-director", "storyboard-planner"}
_HANDOFF_DEFAULT_OBJECTIVE = "按目标 Skill 完成本轮已授权工作"
_ROUTED_HANDOFF_SCOPE = ("action", "artifact_ids", "asset_ids", "output_ids", "shot_ids", "segment_ids", "revision_candidate_ids")
_ROUTED_SKILLS = {
    "depth-guided-recreation": ("recreation",),
    "creative-director": ("creative",),
    "storyboard-planner": ("director",),
    "asset-image-assistant": ("assets",),
    "storyboard-director": ("visual",),
    "voiceover-to-video-script": ("video", "canvas"),
}


def _context_dir(project: Path, *parts: str) -> Path:
    """Resolve only the selected execution context; business state stays shared."""
    return _context_path(project, _CONTEXT_ID.get(), *parts)


def _context_path(project: Path, context_id: str | None, *parts: str) -> Path:
    path = project / CONTEXT_DIR
    if context_id is None:
        return path.joinpath(*parts)
    path = (path / "批次" / context_id).joinpath(*parts)
    # Do not let an existing parent or batch symlink redirect isolated writes.
    current = project
    for part in path.relative_to(project).parts:
        current = current / part
        if current.is_symlink():
            raise StageContextError(f"批次上下文路径不得含符号链接：{current}")
    if not path.resolve().is_relative_to(project.resolve()):
        raise StageContextError("批次上下文路径越出项目")
    return path


def _task_handoff_context(args: argparse.Namespace) -> str | None:
    """Use the recipient's own stage context when a task has a bound handoff."""
    target = getattr(args, "to_skill", None)
    skill = str(target or getattr(args, "skill", None) or "").strip()
    identity = _reading_identity(args)
    if not any(identity.values()):
        return None
    if target and (skill not in TASK_HANDOFF_SKILLS or not all(identity.values())):
        raise StageContextError("普通编导或导演的任务交接须同时指定 --actor-id 与 --task-id")
    if skill not in TASK_HANDOFF_SKILLS or not all(identity.values()):
        return None
    context_id = "task-" + _identity_hash({"skill": skill, **identity})[:32]
    if target:
        return context_id
    project = Path(args.project).expanduser().resolve()
    if _context_path(project, context_id, "next_stage_handoff.md").is_file():
        return context_id
    return None


def _context_scoped(function):
    """Scope both CLI and direct Python calls without mutable global paths."""
    @wraps(function)
    def scoped(args, *values, **kwargs):
        context_id = getattr(args, "context_id", None)
        if context_id is None:
            context_id = _task_handoff_context(args)
        if context_id is not None and (not isinstance(context_id, str)
                or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", context_id) is None):
            raise StageContextError("--context-id 须为 1–64 位规范小写 ID（小写字母、数字、下划线或连字符）；不接受大小写别名")
        token = _CONTEXT_ID.set(context_id)
        try:
            if context_id is not None:
                _context_dir(Path(args.project).expanduser().resolve())
            report = function(args, *values, **kwargs)
            if context_id is not None:
                report["context_id"] = context_id
            return report
        finally:
            _CONTEXT_ID.reset(token)
    return scoped


def _image_batch_targets(skill: str, request: dict[str, Any], *, routed=False) -> list[str] | None:
    if skill not in IMAGE_SKILLS:
        return None
    targets = request.get("image_targets")
    if targets is None:
        if _CONTEXT_ID.get() is None or routed:
            return None
        raise StageContextError("隔离生图批次的 image_targets 须包含 1..8 个准确输出身份")
    if skill == "asset-image-assistant":
        try:
            return validate_asset_batch_targets(targets)
        except ValueError as exc:
            raise StageContextError(str(exc)) from exc
    if not isinstance(targets, list) or not 1 <= len(targets) <= 8:
        raise StageContextError("隔离生图批次的 image_targets 须包含 1..8 个准确输出身份")
    for target in targets:
        if (not isinstance(target, str) or not target or target != target.strip()
                or any(ord(char) < 32 for char in target)
                or any(char in target for char in ("/", "\\", "*", "?"))):
            raise StageContextError("image_targets 必须为准确输出 ID，不得使用空值、目录或通配符")
    if len(targets) != len(set(targets)):
        raise StageContextError("image_targets 不得重复")
    return list(targets)


def _vault_root(value: str | None) -> Path:
    try:
        return resolve_knowledge_root(value, repo_root=ROOT)
    except ValueError as exc:
        raise StageContextError(str(exc)) from exc


def _recorded_knowledge_bundle(manifest: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    value = manifest.get("vault_root")
    if not isinstance(value, str) or not value:
        raise StageContextError("任务依据缺少本项目知识根；请重新 prepare。")
    try:
        return resolve_knowledge_bundle(value, repo_root=ROOT)
    except ValueError as exc:
        raise StageContextError(f"任务依据的知识根已失效；请用仓库内置知识重新 prepare：{exc}") from exc


def _local_knowledge_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise StageContextError(f"本地知识或规范路径越出仓库：{path}；请重新 prepare。")
    return resolved


def _request_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    request = json.loads(raw.decode("utf-8"))
    if not isinstance(request, dict):
        raise StageContextError("request JSON 必须是对象")
    return request, hashlib.sha256(raw).hexdigest()


def _state(project: Path) -> dict[str, Any]:
    try:
        return load_project_state(project)
    except (OSError, ValueError) as exc:
        raise StageContextError(f"无法读取项目状态：{exc}") from exc


def _test_request(args, project):
    testing = test_context(project)
    explicit = bool(getattr(args, "candidate_test", False))
    if bool(testing) != explicit:
        raise StageContextError("隔离测试须配置 candidate-test 并显式传 --candidate-test；生产入口不消费测试稿")
    if not testing:
        try:
            require_production_manifest(project)
        except ValueError as exc:
            raise StageContextError(str(exc)) from exc
    return testing


def _state_summary(raw: dict[str, Any]) -> dict[str, Any]:
    state = normalize_project_state(raw)
    return {key: value for key, value in state.items() if key != "schema_version"}


def authorize_workflow(args: argparse.Namespace) -> dict[str, Any]:
    """Record an existing project workflow authorization without approving artifacts."""
    project = Path(args.project).expanduser().resolve()
    authorization = register_workflow_authorization(
        project, authorization_id=args.authorization_id,
        authorization_ref=args.authorization_ref, skills=list(args.skill),
    )
    return {"ok": True, "authorization": authorization,
            "notice": "仅登记所引用户流程授权；不确认稿件，不上传、生成或发布"}


def confirm_script_in_chat(args):
    return adopt_chat_script(args, _json_input(args.confirmation_json))


def _json_input(path: str | Path):
    source = Path(path)
    text = sys.stdin.read().removeprefix("\ufeff") if source == Path("-") else source.read_text(encoding="utf-8-sig")
    return json.loads(text)


def _state_reading_view(summary: dict[str, Any]) -> dict[str, Any]:
    """Filter explicit unrelated artifact receipts for display only, never authorization."""
    current = set(summary.get('current_artifacts') or [])
    if not current:
        return dict(summary)
    def relevant(receipt):
        ids = receipt.get('artifact_ids')
        single = receipt.get('artifact_id')
        if ids is not None and not isinstance(ids, list):
            return True
        referenced = {i for i in (ids or []) if isinstance(i, str)}
        if isinstance(single, str) and single:
            referenced.add(single)
        return not referenced or bool(current & referenced)
    return {**summary, 'user_confirmations': [r for r in summary.get('user_confirmations', [])
                                              if not isinstance(r, dict) or relevant(r)]}


def _task_reading_view(basis: dict[str, Any]) -> dict[str, Any]:
    """Keep frozen requirements; omit machine catalogs and fingerprints, not decisions."""
    result = {k: v for k, v in basis.items() if k not in {'reading_manifest', 'standards', 'reading_reuse_key', 'input_snapshot', 'request_path', 'task_request', 'knowledge_profile'}}
    if kickoff := basis.get("task_request", {}).get("kickoff"):
        result["kickoff"] = kickoff
        if (timing := product_demo_timing_advisory(kickoff)) is not None:
            result["product_demo_timing"] = timing
    standards = dict(basis.get('standards') or {})
    standards.pop('local_documents', None)
    result['standards'] = standards
    return result


def _revision_summary(project: Path, change_id: str | None, target_ids: list[str] | None) -> dict[str, Any] | None:
    """Read bounded revision facts without becoming a dispatcher or state writer."""
    if not change_id:
        return None
    control = show_change(project)
    active = control.get("active_change")
    if not isinstance(active, dict) or active.get("change_id") != change_id:
        raise StageContextError(f"活动变更案不存在：{change_id}")
    targets = list(target_ids or [item.get("logical_id") for item in active.get("targets") or [] if item.get("logical_id")])
    try:
        guard_action(project, logical_ids=targets, change_id=change_id)
    except RevisionControlError as exc:
        raise StageContextError(str(exc)) from exc
    return {
        "change_id": change_id,
        "tier": active.get("tier"),
        "status": active.get("status"),
        "targets": targets,
        "frozen_logical_ids": active.get("frozen_logical_ids") or [],
        "evidence_refs": active.get("evidence_refs") or [],
        "reason": active.get("reason") or "",
    }


def _tombstone_fingerprints(vault: Path, policy: dict[str, Any], *, verified_manifest=None) -> set[str]:
    if is_bundled_root(vault):
        manifest = verify_bundle(vault) if verified_manifest is None else verified_manifest
        if not manifest["dynamic_rule_ids"]:
            return set()
    relative = (policy.get("discovery") or {}).get("tombstones")
    path = vault / relative if isinstance(relative, str) else None
    if is_bundled_root(vault) and path is not None:
        if relative not in {entry["path"] for entry in manifest["files"]}:
            return set()  # Not packaged; no assertion about upstream history.
        path = verify_selected_files(vault, (relative,), verified_manifest=manifest)[relative]
    if path is None or not path.is_file():
        return set()
    result: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StageContextError(f"墓碑文件第 {number} 行损坏：{exc}") from exc
        fingerprint = value.get("fingerprint") if isinstance(value, dict) else None
        if isinstance(fingerprint, str) and fingerprint:
            result.add(fingerprint)
    return result


def _load_review_profile(skill: str) -> tuple[Path, dict[str, Any]]:
    profile_path = _local_knowledge_path(ROOT / "skills" / skill / "review-profile.json")
    profile = _read_json(profile_path, default=None)
    if not isinstance(profile, dict):
        raise StageContextError(f"Skill 审核 profile 不存在或无效：{profile_path}")
    if profile.get("schema_version") != "1.0" or profile.get("skill") != skill:
        raise StageContextError(f"Skill 审核 profile 身份无效：{profile_path}")
    sections = ("skill_contract_checks",) if skill == "asset-image-assistant" else ("skill_contract_checks", "production_artifact_checks")
    for field in sections:
        items = profile.get(field)
        if not isinstance(items, list) or not items:
            raise StageContextError(f"Skill 审核 profile 缺少非空 {field}：{profile_path}")
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise StageContextError(f"Skill 审核 profile 的 {field} 项必须是对象：{profile_path}")
            check_id = item.get("id")
            text = item.get("text")
            if not isinstance(check_id, str) or not re.fullmatch(r"[A-Z][A-Z0-9-]{2,63}", check_id):
                raise StageContextError(f"Skill 审核检查项 ID 无效：{check_id!r}")
            if check_id in seen:
                raise StageContextError(f"Skill 审核检查项 ID 重复：{check_id}")
            if not isinstance(text, str) or not text.strip():
                raise StageContextError(f"Skill 审核检查项内容为空：{check_id}")
            seen.add(check_id)
    return profile_path, profile


def _handoff_path(project: Path) -> Path:
    return _context_dir(project, "next_stage_handoff.md")


def _confirmed_project_inputs(project: Path, state: dict[str, Any], *, manifest=None, file_digest=None,
                              artifact_ids=None, checked_dependencies=None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Resolve selected versions with valid human confirmation or agent acceptance."""
    if artifact_ids is None:
        artifact_ids = state.get("current_artifacts") or []
    if not isinstance(artifact_ids, list) or not artifact_ids or any(not isinstance(item, str) or not item for item in artifact_ids):
        raise StageContextError("项目没有可交接的当前有效产物 ID")
    try:
        manifest = load_manifest(project) if manifest is None else manifest
    except (OSError, ValueError) as exc:
        raise StageContextError("artifact_manifest.json 不存在或无效：" + str(exc)) from exc
    indexed = {item.get("artifact_id"): item for item in manifest["artifacts"] if isinstance(item, dict)}
    inputs: list[dict[str, Any]] = []
    file_digest = cache_file_digests(digest) if file_digest is None else file_digest
    checked_dependencies = set() if checked_dependencies is None else checked_dependencies
    for artifact_id in artifact_ids:
        item = indexed.get(artifact_id)
        if not isinstance(item, dict) or item.get("status") != "current":
            raise StageContextError(f"交接产物不是已确认 current 或有效自主验收 current：{artifact_id}")
        if not artifact_is_adopted(item, state, project):
            raise StageContextError(f"当前有效产物缺少用户确认或有效自主验收：{artifact_id}")
        path = project_path(project, str(item.get("path_or_url") or ""))
        if not path.is_file():
            raise StageContextError(f"交接产物文件不存在：{artifact_id}: {path}")
        if not isinstance(item.get("checksum"), str) or not re.fullmatch(r"[a-f0-9]{64}", item["checksum"]):
            raise StageContextError(f"交接产物缺少有效确认 hash：{artifact_id}")
        if file_digest(path) != item["checksum"]:
            raise StageContextError(f"交接产物内容与确认版本不一致：{artifact_id}")
        try:
            if item.get("logical_id") in {"narrative.package", "storyboard.package", "video.prompt"}:
                if not validate_candidate_submission(project, item, manifest, reading_existing=True, state=state,
                                                     checked=checked_dependencies, file_digest=file_digest):
                    raise ValueError("旧稿件/未识别结构仅保留历史读取，不能作为正式交接输入")
            else:
                validate_current_dependencies(item, manifest, state, project,
                                              checked=checked_dependencies, file_digest=file_digest)
                if item.get("logical_id") == "visual.asset.manifest" and registered_output_snapshot(
                        project, item, file_digest=file_digest) is None:
                    raise ValueError("资产交接缺同版用户确认或图片字节已变化：" + artifact_id)
        except ValueError as exc:
            raise StageContextError(str(exc)) from exc
        inputs.append(item)
    return state, inputs


def _current_handoff_text(project: Path, target_skill: str, snapshot: dict[str, Any] | None = None) -> str:
    """Consume a card only for its target and still-current input identities."""
    path = _handoff_path(project)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    validate_text("handoff", path, text)
    target = re.search(r"(?m)^- 目标 Skill：`([^`\n]+)`\s*$", text)
    if not target:
        raise StageContextError("已有交接卡缺少目标 Skill；先重新运行 handoff，不能作为本轮输入。")
    if target.group(1) != target_skill:
        return ""  # Another stage's card is not an input to this preparation.
    fields = ("artifact_id", "logical_id", "revision", "type", "path")
    pattern = r"(?m)^- artifact_id：`([^`\n]+)`\n" + "\n".join(
        rf"  - {field}：`([^`\n]*)`" for field in fields[1:]
    )
    if snapshot is None:
        snapshot = _input_snapshot(project, {}, _state(project))
    indexed = {fact["record"]["artifact_id"]: fact for fact in snapshot["artifacts"]}
    selected_ids = snapshot["selected_artifact_ids"]
    inputs = [indexed[ident] for ident in selected_ids]
    testing = bool(snapshot.get("candidate_test"))
    scoped = snapshot.get("artifact_scope") == "declared"
    routed = _routed_handoff_scope(text)
    initial_creative = routed and routed["action"] == "creative" and not routed.get("artifact_ids")
    if not testing and not inputs and not scoped and not initial_creative:
        raise StageContextError("项目没有可交接的当前有效产物 ID")
    if testing != ("execution_mode: candidate_test" in text):
        raise StageContextError("交接卡运行模式与项目不一致；重新 handoff")
    hash_pattern = r"(?m)^  - (?:candidate_)?sha256：`([a-f0-9]{64})`$"
    has_hashes = bool(re.search(hash_pattern, text))
    if scoped and not routed:
        # Existing cards list every current input. Consume and show only rows
        # relevant to the new task, leaving the original card untouched.
        selected = set(selected_ids)
        text = re.sub(r"(?m)^- artifact_id：`([^`\n]+)`\n(?:^  [^\n]*(?:\n|$))*",
                      lambda row: row[0] if row[1] in selected else "", text)
    records = [(*record[:-1], str(project_path(project, record[-1])))
               for record in re.findall(pattern, text)]
    if routed:
        declared = set(routed.get("artifact_ids", []))
        if declared != {record[0] for record in records} or not declared.issubset(selected_ids):
            raise StageContextError("本轮准备未绑定程序交接卡指定的全部产物；按原交接范围准备。")
        inputs = [fact for fact in inputs if fact["record"]["artifact_id"] in declared]
    expected = [tuple(str(fact["record"].get(field, "")) for field in fields[:-1])
                + (fact["file"]["path"],) for fact in inputs]
    if sorted(records) != sorted(expected):
        raise StageContextError("已有交接卡的产物、版本或路径已过期；先按当前确认输入重新运行 handoff。")
    if testing or has_hashes:
        ordered = [indexed[record[0]] for record in records] if scoped or routed else inputs
        if re.findall(hash_pattern, text) != [fact["file"]["sha256"] for fact in ordered]:
            raise StageContextError("交接卡来源字节已改变；重新核对并 handoff")
    return text


def _routed_handoff_scope(text: str) -> dict[str, Any] | None:
    match = re.search(r"(?m)^- 程序交接范围：(.+)$", text)
    if not match:
        return None
    try:
        scope = json.loads(match[1])
    except ValueError as exc:
        raise StageContextError("程序交接范围无法读取；重新运行 handoff。") from exc
    if (not isinstance(scope, dict) or not isinstance(scope.get("action"), str)
            or ("workflow" in scope and scope["workflow"] != RECREATION_WORKFLOW)
            or any(not isinstance(scope.get(key, []), list)
                   or any(not isinstance(ident, str) or not ident for ident in scope.get(key, []))
                   for key in _ROUTED_HANDOFF_SCOPE[1:])):
        raise StageContextError("程序交接范围无效；重新运行 handoff。")
    return scope


def _handoff_scope(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    match = re.search(r"(?ms)^- 本轮目标：(.*?)\n^- 本轮停止点：(.*?)\n^-(?: 交接状态：| execution_mode:)", text)
    if not match:
        return {"sha256": _identity_hash(text.strip())}  # Do not assume an unrecognized older card is unchanged.
    objective, stop_at = (value.strip() for value in match.groups())
    bindings = _asset_batch_binding_entries(text)
    routed = _routed_handoff_scope(text)
    return {"objective": objective, "stop_at": stop_at,
            **({"routed": routed} if routed else {}),
            **({"asset_batch_sha256": _identity_hash(sorted(bindings))} if bindings else {})}


def _check_preparation_route(project, skill, request, routed, *, repair=False, asset_batch=False,
                             active_request=None):
    """Keep formal startup on the live route, including after a stale card or a restart."""
    if "revise_candidate_id" in request:
        if skill != "creative-director" or repair or (routed and routed["action"] != "creative"):
            raise StageContextError("待审编导返修只适用于首次采用前的编导准备")
        manifest = load_manifest(project)
        ident = request["revise_candidate_id"]
        pending_narrative_candidate(manifest, ident)
        flow = resolve_production_handoff(project, manifest=manifest, actions={"creative"})
        if routed and routed.get("revision_candidate_ids") and ident not in routed["revision_candidate_ids"]:
            raise StageContextError("编导交接卡与本次待修候选不同")
        if not any((step["action"] == "creative-review" and ident in step.get("artifact_ids", [])) or
                   (step["action"] == "creative" and ident in step.get("revision_candidate_ids", []))
                   for step in flow["steps"]):
            raise StageContextError("当前未开放本版待审编导返修，请核对项目状态")
        return
    if repair or asset_batch or skill not in _ROUTED_SKILLS:
        return
    project_request = load_project_request(project)
    if LEGACY_RULE in project_request.get("local_references", []):
        return
    if not routed and project_request.get("project_mode") != "new":
        return
    action = _ROUTED_SKILLS[skill][0]
    if routed and routed["action"] != action:
        raise StageContextError("交接卡步骤与本轮准备不一致")
    requested_shots = request.get("source_shot_ids") if action == "visual" else None
    route = require_production_handoff(project, action, shot_ids=requested_shots,
                                       **({"active_asset_request": active_request} if active_request else {}))
    if route.get("revision_candidate_ids"):
        raise StageContextError("请在阶段请求的 revise_candidate_id 指定待修候选：" +
                                "、".join(route["revision_candidate_ids"]))
    image_targets = set(request.get("image_targets", [])) if action == "assets" else set()
    if routed:
        if action != "assets" and routed.get("workflow") != route.get("workflow"):
            raise StageContextError("制作路线已变化；重新运行 handoff")
        if not set(routed.get("artifact_ids", [])).issubset(route.get("artifact_ids", [])):
            raise StageContextError("程序交接输入已改版；重新运行 handoff")
        fields = ("shot_ids", "segment_ids") if image_targets else ("asset_ids", "output_ids", "shot_ids", "segment_ids")
        if (requested_shots is not None and not set(requested_shots).issubset(routed.get("shot_ids", []))
                or any(not set(routed.get(field, [])).issubset(route.get(field, []))
                       for field in fields if field != "shot_ids" or requested_shots is None)):
            raise StageContextError("程序交接范围已变化；重新运行 handoff 核对就绪范围")
    if image_targets:
        assets = {target.partition("@")[0] for target in image_targets}
        for scope in (routed, route) if routed else (route,):
            if "output_ids" not in scope:
                raise StageContextError(
                    "资产交接范围只有盘点和复用，不能出图：开工单缺 image_generation 生图授权。"
                    "用户已同意缺图生成时，用 stage_context_cli.py kickoff --answers-json 补登记 image_generation"
                    "（provider、model、scope、authorization_ref、reference_upload_authorized、fee_scope），再重新运行 handoff")
            reasons = []
            if missing := sorted(assets - set(scope.get("asset_ids", []))):
                reasons.append("不在本轮交接资产内：" + "、".join(missing))
            if scope["output_ids"] and (outside := sorted(image_targets - set(scope["output_ids"]))):
                reasons.append("不在本轮交接输出内：" + "、".join(outside))
            if composing := sorted(assets.intersection(scope.get("compose_asset_ids", []))):
                reasons.append("属于待拼版人物，应走 compose-boards：" + "、".join(composing))
            for field, label in (("confirmed_output_ids", "已确认，无需再出"),
                                 ("batched_output_ids", "已在其他批次中；同轮换图直接改原批次回执，不另开批次"),
                                 ("rejected_output_ids", "已被拒用")):
                if hit := sorted(image_targets.intersection(scope.get(field, []))):
                    reasons.append(label + "：" + "、".join(hit))
            if reasons:
                raise StageContextError("图片目标超出程序交接范围；" + "；".join(reasons))


def _coordinator_objective(skill: str, context_id, scope, testing=False) -> str | None:
    goal = (scope or {}).get("objective")
    return (goal if context_id and skill in TASK_HANDOFF_SKILLS and not testing and goal
            and goal != _HANDOFF_DEFAULT_OBJECTIVE else None)


def _startup_boundary_problems(text: str) -> list[str]:
    match = re.search(r"(?m)^- Skill：`([^`]+)`\s*$", text)
    if not match:
        return ["缺少可识别的 Skill 身份"]
    skill = match.group(1)
    profile_path = ROOT / "skills" / skill / "knowledge-profile.json"
    profile = _read_json(profile_path, default=None)
    if not isinstance(profile, dict) or profile.get("skill") != skill:
        return [f"Skill 知识 profile 不存在或身份无效：{profile_path}"]
    terms = profile.get("forbidden_knowledge_terms", [])
    if not isinstance(terms, list) or any(
        not isinstance(item, str) or not item.strip() for item in terms
    ):
        return [f"Skill 知识 profile 的 forbidden_knowledge_terms 无效：{profile_path}"]
    bundle_marker = f"# {skill} 当前知识包"
    if bundle_marker not in text:
        return [f"缺少 {skill} 当前知识包"]
    # 用 rfind 拿"最后一个 ## 选择记录"做知识包终点：prepare 会把选择记录追加在
    # 知识包末尾，而知识块的正文也可能合法包含"## 选择记录"字样（此前用 split 取
    # 第一个匹配会提前截断，导致其后的边界词漏检）。最后一个该标题必然是真正的
    # 选择记录，故以此截取知识包正文，避免漏放；选择记录只含 MSB-KB-* ID 与计数，
    # 不含边界词，无需纳入扫描。
    knowledge_text = text.split(bundle_marker, 1)[1]
    selection_end = knowledge_text.rfind("\n## 选择记录")
    if selection_end != -1:
        knowledge_text = knowledge_text[:selection_end]
    return [
        f"Skill 边界残留：{term.strip()}"
        for term in terms
        if term.strip() in knowledge_text
    ]


def _optional_experience_paths(vault: Path, profile: dict[str, Any]) -> list[tuple[str, bool]]:
    """Validate explicit optional experience paths against this run's Vault."""
    values = profile.get("optional_experience_scopes", [])
    if not isinstance(values, list):
        raise StageContextError("optional_experience_scopes 必须是明确的 Vault 相对 Markdown 文件路径数组。")
    result: list[tuple[str, bool]] = []
    for value in values:
        raw = Path(value.replace("\\", "/")) if isinstance(value, str) else None
        if raw is None or raw.is_absolute() or PureWindowsPath(value).drive or raw.suffix.lower() != ".md" or any(part == ".." for part in raw.parts):
            raise StageContextError("optional_experience_scopes 必须是明确的 Vault 相对 Markdown 文件路径数组。")
        target = (vault / raw).resolve()
        try:
            target.relative_to(vault.resolve())
        except ValueError as exc:
            raise StageContextError("optional_experience_scopes 路径越出本轮 Vault。") from exc
        result.append((raw.as_posix(), target.is_file()))
    return result


def validate_text(kind: str, path: Path, text: str | None = None) -> dict[str, Any]:
    if kind not in LIMITS:
        raise StageContextError(f"未知类型：{kind}")
    if text is None:
        if not path.is_file():
            raise StageContextError(f"文件不存在：{path}")
        text = path.read_text(encoding="utf-8")
    size = len(text.encode("utf-8"))
    problems = [name for name, pattern in BANNED.items() if pattern.search(text)]
    if kind == "startup":
        problems.extend(_startup_boundary_problems(text))
    if size > LIMITS[kind]:
        problems.append(f"超出 {LIMITS[kind]} 字节上限，当前 {size} 字节")
    if problems:
        raise StageContextError(f"{path.name} 校验失败：{'；'.join(problems)}")
    return {"kind": kind, "path": str(path), "bytes": size, "valid": True}


def candidate_source_index(args: argparse.Namespace) -> dict[str, Any]:
    """Read a pinned source archive and report trial-eligible items, never promote."""
    skill = str(args.skill or "").strip()
    profile_path = ROOT / "skills" / skill / "knowledge-profile.json"
    profile = _read_json(profile_path, default=None)
    if not isinstance(profile, dict) or profile.get("skill") != skill:
        raise StageContextError(f"Skill 知识 profile 无效：{profile_path}")
    try:
        result = inspect_candidate_snapshot(
            profile, Path(args.snapshot).expanduser().resolve(), args.source_id,
        )
    except CandidateSnapshotError as exc:
        raise StageContextError(str(exc)) from exc
    return {"ok": True, "read_only": True, "skill": skill, **result}


def _reading_identity(args: argparse.Namespace) -> dict[str, str]:
    return {
        "actor_id": str(getattr(args, "actor_id", "") or "").strip(),
        "task_id": str(getattr(args, "task_id", "") or "").strip(),
    }


def _candidate_record(block: Any) -> dict[str, Any]:
    record = {
        "id": block.id, "canonical_key": block.canonical_key, "kind": block.kind,
        "status": block.status, "authority": block.authority,
        "projects": list(block.projects), "stages": list(block.stages),
        "topics": list(block.topics), "triggers": list(block.triggers),
        "conflicts_with": list(block.conflicts_with), "priority": block.priority,
        "applies_when": block.applies_when, "vault_relative_path": block.vault_relative_path,
        "anchor": block.anchor, "sha256": block.fingerprint, "sources": list(block.sources),
    }
    if getattr(block, "source_status", None) is not None:
        record["source_status"] = block.source_status
    if getattr(block, "source_page_sha256", None) is not None:
        record["source_page_sha256"] = block.source_page_sha256
    return record


def _candidate_packet(candidates: list[Any], scopes: dict[str, str] | None = None) -> str:
    lines = ["## 已采用按需知识", ""]
    for block in candidates:
        if scopes and block.id in scopes:
            lines.extend([f"本职采用范围（执行者声明，仍须审核）：{scopes[block.id]}",
                          "以下为完整来源快照；范围外实现项不成为当前 Skill 的产出要求，职责边界不变。", ""])
        lines.extend([f"### {block.id} [{block.status}]", "", f"- Vault：`{block.vault_relative_path}`", f"- 锚点：`^{block.anchor}`", f"- SHA-256：`{block.fingerprint}`", "", block.body.strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def _evidence_current(project: Path, requested_skill: str | None = None, *, basis=None, reading_files=None) -> tuple[dict[str, Any], dict[str, Any], Path] | None:
    if basis is None:
        basis = _read_json(_task_basis_path(project), default={})
    if not isinstance(basis, dict) or basis.get("context_id") != _CONTEXT_ID.get():
        return None
    manifest = basis.get("reading_manifest")
    packet = _context_dir(project, "method_reading_packet.md")
    if reading_files is None:
        try:
            evidence = _read_json(_context_dir(project, "method_reading_evidence.json"), default=None)
        except StageContextError:
            return None  # Broken optional reading state is unread, not a preparation failure.
        if not isinstance(evidence, dict) or not packet.is_file():
            return None
    else:
        evidence, packet_bytes = reading_files
        if packet_bytes is None:
            return None
    if not isinstance(manifest, dict) or not isinstance(evidence, dict):
        return None
    key = basis.get('reading_reuse_key')
    if key and evidence.get('reading_reuse_key') != key:
        return None
    keys = ("skill", "project_path", "actor_id", "task_id", "context_id", "vault_root", "test_only_proposed_method_ids", "methods", "selected")
    if requested_skill and manifest.get("skill") != requested_skill:
        return None
    if any(evidence.get(key) != manifest.get(key) for key in keys):
        return None
    if not all(isinstance(manifest.get(key), str) and manifest[key] for key in ("skill", "project_path", "actor_id", "task_id", "vault_root")):
        return None
    if reading_files is None:
        packet_bytes = packet.read_bytes()
    packet_sha = hashlib.sha256(packet_bytes).hexdigest()
    base_length = manifest.get("method_packet_bytes")
    if evidence.get("packet_sha256") != packet_sha or not isinstance(base_length, int) or not 0 <= base_length <= len(packet_bytes):
        return None
    base_sha = packet_sha if base_length == len(packet_bytes) else hashlib.sha256(packet_bytes[:base_length]).hexdigest()
    if base_sha != manifest.get("method_packet_sha256"):
        return None
    if evidence.get("method_packet_sha256") != manifest.get("method_packet_sha256"):
        return None
    catalog = {item.get("id"): item for item in manifest.get("candidate_catalog", []) if isinstance(item, dict)}
    adopted = evidence.get("adopted_candidates", [])
    if not isinstance(adopted, list) or len({item.get("id") for item in adopted if isinstance(item, dict)}) != len(adopted):
        return None
    if any(catalog.get(item.get("id")) != item for item in adopted if isinstance(item, dict)):
        return None
    scopes = evidence.get("candidate_scopes", {})
    if (not isinstance(scopes, dict) or any(key not in {item["id"] for item in adopted}
            or not isinstance(value, str) or not value.strip() for key, value in scopes.items())):
        return None
    reasons = manifest.get("candidate_reasons", {})
    if any(str(reasons.get(item["id"], "")).startswith("skill_boundary:") and item["id"] not in scopes
           for item in adopted):
        return None
    return manifest, evidence, packet


def _reading_cache_path(project: Path, skill: str) -> Path:
    if not re.fullmatch(r'[a-z0-9-]+', skill):
        raise StageContextError('无效的缓存 Skill 名称')
    return _context_dir(project, 'stage_reading_cache', skill + '.json')


def _save_reading_cache(project: Path) -> None:
    current = _evidence_current(project)
    if current is None:
        return
    manifest, evidence, packet = current
    if not evidence.get('reading_reuse_key'):
        return
    value = {'evidence': evidence, 'packet': packet.read_text(encoding='utf-8')}
    write_text_if_changed(_reading_cache_path(project, manifest['skill']),
                          json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def _restore_reading_cache(project: Path, skill: str, key: str) -> bool:
    try:
        value = _read_json(_reading_cache_path(project, skill), default={})
        evidence, packet = value.get('evidence'), value.get('packet')
        if not isinstance(evidence, dict) or not isinstance(packet, str):
            return False
        if evidence.get('reading_reuse_key') != key or evidence.get('packet_sha256') != hashlib.sha256(packet.encode('utf-8')).hexdigest():
            return False
    except (StageContextError, AttributeError):
        return False  # An unusable cache never authorizes reading or blocks fresh preparation.
    write_text_if_changed(_context_dir(project, 'method_reading_packet.md'), packet)
    write_text_if_changed(_context_dir(project, 'method_reading_evidence.json'), json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    return _evidence_current(project, skill) is not None


def _identity_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _semantic_request(request: dict[str, Any], skill: str) -> dict[str, Any]:
    semantic = {key: value for key, value in request.items()
                if key not in {"history", "updated_at", "created_at"}}
    kickoff = semantic.get("kickoff")
    # The people writing the story and shot plan never submit image jobs.
    if skill in {"creative-director", "storyboard-planner"} and isinstance(kickoff, dict):
        semantic["kickoff"] = {key: value for key, value in kickoff.items() if key != "image_generation"}
    elif skill == "asset-image-assistant" and isinstance(kickoff, dict):
        creation = kickoff.get("creation")
        if isinstance(creation, dict) and "workflow" in creation:
            semantic["kickoff"] = {**kickoff, "creation": {key: value for key, value in creation.items()
                                                            if key != "workflow"}}
    return semantic


def _normalize_source_alias(inputs: dict[str, Any]) -> dict[str, Any]:
    """The request-level sources alias declares local files, not semantic sources.

    Normalize at the input boundary only; do not reinterpret arbitrary nested
    prose/metadata named sources or enumerate undeclared project files.
    """
    if "sources" not in inputs:
        return inputs
    sources = inputs["sources"]
    if (not isinstance(sources, list) or not sources
            or any(not isinstance(item, str) or not item.strip() for item in sources)):
        raise StageContextError("request.sources 必须是非空本地文件路径数组。")
    if "source_files" in inputs and inputs["source_files"] != sources:
        raise StageContextError("request.sources 与 source_files 声明不一致；请保留准确的同一来源列表。")
    return {**{key: value for key, value in inputs.items() if key != "sources"},
            "source_files": sources}


def _explicit_source_paths(project: Path, inputs: dict[str, Any]) -> list[Path]:
    """Resolve declared files only, never enumerate the project or guess ID paths."""
    paths: set[Path] = set()
    file_keys = {"path", "path_or_url", "file", "files", "source", "source_markdown",
                 "source_json", "source_image", "source_video", "source_audio", "source_document", "source_url"}
    def collect(value: Any, key: str = "") -> None:
        if key in file_keys and (value is None or value == "" or value == []):
            raise StageContextError(f"必需显式来源文件缺失：{key}")
        if isinstance(value, dict):
            for name, child in value.items():
                collect(child, name)
        elif isinstance(value, list):
            for child in value:
                collect(child, key)
        elif isinstance(value, str) and value:
            explicit = key in file_keys or key.endswith(("_path", "_paths", "_file", "_files"))
            path_like = "/" in value or "\\" in value or bool(Path(value).suffix)
            source_path = key.startswith("source_") and not key.endswith(("_id", "_ids", "_type", "_kind", "_format", "_version", "_description", "_note"))
            if explicit or ((key == "inputs" or source_path) and path_like):
                if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
                    raise StageContextError(f"显式来源不是可核验的本地文件：{value}；先提供准确本地快照。")
                path = project_path(project, value)
                if not path.is_file():
                    raise StageContextError(f"必需显式来源文件缺失：{value}")
                paths.add(path)
    collect(_normalize_source_alias(inputs))
    return sorted(paths)


def _input_snapshot(project: Path, inputs: dict[str, Any], state: dict[str, Any], *, file_digest=None,
                    prefer_declared_artifacts=False, source_roles=()) -> dict[str, Any]:
    """Bound current validity to explicit sources and state-selected upstream inputs."""
    sources = _explicit_source_paths(project, inputs)
    try:
        manifest = load_manifest(project)
    except (OSError, ValueError) as exc:
        raise StageContextError(f"artifact_manifest.json 无效：{exc}") from exc
    testing = test_context(project)
    current_ids = list(testing["input_artifact_ids"] if testing else (state.get("current_artifacts") or []))
    declared_ids = []
    def collect_ids(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                collect_ids(child, name)
        elif isinstance(value, list):
            for child in value:
                collect_ids(child, key)
        elif key in {"artifact_id", "artifact_ids", "source_artifact_id", "source_artifact_ids"}:
            if not isinstance(value, str) or not value:
                raise StageContextError("显式来源产物 ID 无效")
            if value not in declared_ids:
                declared_ids.append(value)
    collect_ids(inputs)
    # Explicit registered sources are authoritative too, even if absent from the
    # convenience selection. Keep ordinary unregistered raw sources supported.
    source_names = {str(path) for path in sources}
    for entry in manifest.get("artifacts", []):
        if not isinstance(entry, dict) or not entry.get("path_or_url"):
            continue
        try:
            path = project_path(project, entry["path_or_url"])
        except ValueError:
            continue
        if str(path) in source_names and entry.get("artifact_id") not in declared_ids:
            declared_ids.append(entry.get("artifact_id"))
    if prefer_declared_artifacts and not testing:
        for role in source_roles:
            matches = [entry["artifact_id"] for entry in manifest["artifacts"]
                       if entry.get("logical_id") == role and entry.get("status") == "current"]
            if len(matches) > 1:
                raise StageContextError(f"本轮上游须有唯一 current 产物：{role}")
            declared_ids.extend(ident for ident in matches if ident not in declared_ids)
    scoped = prefer_declared_artifacts and not testing and bool(declared_ids)
    selected = declared_ids if scoped else list(dict.fromkeys([*current_ids, *declared_ids]))
    records = []
    file_digest = cache_file_digests(digest) if file_digest is None else file_digest
    if selected:
        if testing:
            records = test_inputs(project, selected)
        elif scoped:
            checked = set()
            _, records = _confirmed_project_inputs(project, state, manifest=manifest, file_digest=file_digest,
                                                   artifact_ids=selected, checked_dependencies=checked)
            upstream = sorted(checked.difference(selected))
            if upstream:
                _, dependencies = _confirmed_project_inputs(project, state, manifest=manifest, file_digest=file_digest,
                                                            artifact_ids=upstream, checked_dependencies=checked)
                records.extend(dependencies)
            selected = [item["artifact_id"] for item in records]
        else:
            _, records = _confirmed_project_inputs(project, {**state, "current_artifacts": selected},
                                                   manifest=manifest, file_digest=file_digest)
    selected_set = set(selected)

    def file_fact(path: Path) -> dict[str, str]:
        return {"path": str(path), "sha256": file_digest(path)}

    def relevant(receipt: Any) -> bool:
        if not isinstance(receipt, dict):
            return True
        ids = receipt.get("artifact_ids", [])
        if not isinstance(ids, list):
            return True  # Unknown scope cannot be discarded as irrelevant.
        ids = [*ids, receipt.get("artifact_id")]
        referenced = {item for item in ids if isinstance(item, str) and item}
        return not referenced or bool(referenced & selected_set)
    accepted_issues = state.get("accepted_issues", [])
    if scoped and isinstance(accepted_issues, list):
        accepted_issues = [item for item in accepted_issues if relevant(item)]
    return {
        "sources": [file_fact(path) for path in sources],
        "selected_artifact_ids": sorted(selected_set.intersection(current_ids)) if scoped else current_ids,
        **({"artifact_scope": "declared"} if scoped else {}),
        **({"candidate_test": testing} if testing else {}),
        "artifacts": [{"record": {k: v for k, v in item.items() if k not in {"updated_at", "created_at"}},
                       "file": file_fact(project_path(project, item["path_or_url"])),
                       "confirmation_byte_binding": "recorded" if item.get("checksum") else "legacy_unhashed"}
                      for item in records],
        "authority": {"user_confirmations": [item for item in state.get("user_confirmations", []) if relevant(item)],
                      "accepted_issues": accepted_issues,
                      "project_status": state.get("project_status"), "finalization": state.get("finalization")},
    }


def _task_source_roles(skill: str, request: dict[str, Any]) -> list[str]:
    """Include upstream packages that the selected phase uses by contract."""
    roles = []
    if skill == "asset-image-assistant" or request.get("material_output_ids"):
        roles.append("narrative.package")
    if skill == "storyboard-director" and (request.get("source_shot_ids") or request.get("supplement_request_paths")):
        roles.append("storyboard.package")
    if request.get("material_output_ids"):
        roles.append("visual.asset.manifest")
    return roles


def _validate_redframe_supplement_targets(project, request, snapshot):
    from .schema_contracts import require_schema

    targets = request['image_targets']
    paths = request.get('supplement_request_paths')
    if (not isinstance(paths, list) or len(paths) != len(targets)
            or any(not isinstance(path, str) or not path.strip() for path in paths)):
        raise StageContextError('红帧补图批次须通过 supplement_request_paths 逐目标指向现有补图请求')
    source_digests = {item['path']: item['sha256'] for item in snapshot['sources']}
    directors = {item['record']['artifact_id']: item for item in snapshot['artifacts']
                 if item['record'].get('logical_id') == 'storyboard.package'}
    directors_by_path = {}
    for target, path in zip(targets, paths):
        source = project_path(project, path)
        if not source.is_relative_to(project):
            raise StageContextError('补图请求须位于当前项目内')
        decision, sha256 = _request_snapshot(source)
        if source_digests.get(str(source)) != sha256:
            raise StageContextError(f'{target} 的补图请求在准备期间变化；请重新 prepare')
        require_schema(decision, 'visual-supplement-request.schema.json', label='视觉补充请求')
        if (decision['request_id'] != target or decision['decision'] != 'request_supplement'
                or decision['status'] != 'ready'):
            raise StageContextError(f'{target} 须对应 ready 的实际缺图请求；复用或退回上游的条目不进入生图批次')
        reference = decision['source_storyboard']
        director = directors.get(reference['artifact_id'])
        if (director is None or director['record']['revision'] != reference['revision']
                or Path(director['file']['path']) != project_path(project, reference['path'])
                or reference['status'] != ('confirmed' if director['record'].get('user_approved') is True
                                          else 'agent_accepted')):
            raise StageContextError(f'{target} 的补图请求所引导演稿不是本轮选定的准确已采用版')
        director_path = Path(director['file']['path'])
        if director_path not in directors_by_path:
            board, board_sha256 = _request_snapshot(director_path)
            if board_sha256 != director['file']['sha256']:
                raise StageContextError('导演稿在准备期间变化；请重新 prepare')
            directors_by_path[director_path] = (board['project_id'], {shot['shot_id'] for scene in board['scenes']
                                                                     for shot in scene['shots']})
        project_id, available_shots = directors_by_path[director_path]
        if decision['project_id'] != project_id or not set(decision['source_shot_ids']) <= available_shots:
            raise StageContextError(f'{target} 的项目或镜号与已采用导演稿不一致')


def _local_standard_documents(skill_dir, request=None, *, read_document=Path.read_bytes):
    return local_standard_documents(ROOT, skill_dir, request, read_document=read_document)


def _review_identity(basis: dict[str, Any], adopted: list[dict[str, Any]] | None = None) -> str:
    # Rebuild the substantive projection, never trust a stored hash or include
    # actor IDs, reading cache/catalog metadata, request location or packet bytes.
    fields = ("schema_version", "skill", "objective", "deliverables", "non_goals", "input_versions", "handoff_scope",
              "scope", "delivery_contract", "standards", "review_acceptance", "revision", "task_request", "input_snapshot", "character_output_requirements", "character_layout_references", "character_prompt_templates")
    return _identity_hash({**{key: basis[key] for key in fields if key in basis}, "adopted_knowledge": adopted or []})


def _reading_reuse_key(basis):
    manifest = basis["reading_manifest"]
    identity = {key: manifest.get(key) for key in ("skill", "project_path", "actor_id", "task_id", "vault_root",
                                                 "test_only_proposed_method_ids", "methods", "selected", "method_packet_sha256")}
    if manifest.get("context_id") is not None:
        identity["context_id"] = manifest["context_id"]
    # A scoped card's presentation/stop point can change without changing the actual material read.
    reading_basis = {key: value for key, value in basis.items() if key != "handoff_scope"}
    if _coordinator_objective(basis["skill"], basis.get("context_id"), basis.get("handoff_scope"), basis.get("candidate_test")):
        reading_basis.pop("objective", None)
        if not basis["task_request"].get("deliverables"):
            reading_basis.pop("deliverables", None)
    return _identity_hash({"reading": identity, "task": _review_identity(reading_basis)})


def _validate_current_inputs(project: Path, basis: dict[str, Any], *, adopted_candidates=None, file_digest=None) -> dict[str, Any]:
    """Recheck known dependencies at consumption time; never grants execution authority."""
    manifest = basis.get("reading_manifest", {})
    if not isinstance(manifest, dict) or manifest.get("project_path") != str(project):
        raise StageContextError("任务依据属于另一项目；请在当前项目重新 prepare。")
    knowledge_root, verified_bundle = _recorded_knowledge_bundle(manifest)
    snapshot = basis.get("input_snapshot")
    if not isinstance(snapshot, dict):
        raise StageContextError("任务依据缺少当前输入版本证据；请重新 prepare。")
    request_path = basis.get("request_path")
    current_request = _read_json(Path(request_path), default=None) if request_path else {}
    if not isinstance(current_request, dict):
        raise StageContextError("当前阶段请求缺失；请重新 prepare。")
    kickoff = None if snapshot.get("candidate_test") else load_kickoff(project)
    skill = basis["skill"]
    frozen_request = basis.get("task_request")
    if (not isinstance(frozen_request, dict)
            or _semantic_request(bind_stage_request(current_request, kickoff), skill)
            != _semantic_request(frozen_request, skill)):
        raise StageContextError("开工决定或阶段请求已改变；请重新 prepare。")
    if basis.get("skill") == "asset-image-assistant":
        try:
            output_requirements = check_output_plan(project, basis.get("task_request", {}),
                                                    **({"candidate_test_mode": True} if snapshot.get("candidate_test") else {}))
        except ValueError as exc:
            raise StageContextError(str(exc)) from exc
        if (layout_refs := basis.get("character_layout_references")) and layout_refs != character_layout_references(layout_refs):
            raise StageContextError("本批人物灰模已变化；请重新 prepare。")
        if (templates := basis.get("character_prompt_templates")) and templates != character_prompt_templates(templates):
            raise StageContextError("本批人物生图提示词模板已变化；请重新 prepare。")
        if output_requirements != basis.get("character_output_requirements", {}):
            raise StageContextError("本批人物最终输出规格已变化；请重新 prepare 并核对受影响范围。")
    inputs = basis["input_versions"]
    if skill == 'storyboard-director' and _redframe_image_batch(skill, frozen_request) and not snapshot.get('candidate_test'):
        if 'supplement_request_paths' not in inputs:
            raise StageContextError('旧红帧补图批次缺少逐目标请求依据；请重新 prepare。')
    if "sources" in basis.get("task_request", {}):
        # Old preparations omitted this alias entirely. Reopen the declaration
        # at consumption too, so their empty snapshots cannot remain valid.
        inputs = {**inputs, "sources": basis["task_request"]["sources"]}
    scoped = snapshot.get("artifact_scope") == "declared"
    current = _input_snapshot(project, inputs, _state(project), file_digest=file_digest,
                              prefer_declared_artifacts=scoped, source_roles=_task_source_roles(skill, frozen_request))
    if current != snapshot:
        raise StageContextError("当前来源、选定产物或确认依据已改变；请重新 prepare 并核对变更。")
    scope = _handoff_scope(_current_handoff_text(project, basis["skill"], current))
    if "handoff_scope" in basis:
        previous_scope = basis["handoff_scope"]
    else:
        # Existing preparations already copied the card into the startup file.
        startup = _context_dir(project, "stage_startup.md")
        embedded = startup.read_text(encoding="utf-8").partition("\n## 上一阶段交接事实\n\n")[2] if startup.is_file() else ""
        previous_scope = _handoff_scope(embedded)
    coordinator_goal = _coordinator_objective(basis["skill"], basis.get("context_id"), scope, current.get("candidate_test"))
    if scope != previous_scope or (coordinator_goal is not None and basis.get("objective") != coordinator_goal):
        raise StageContextError("交接目标、停止点或图片批次依据已改变；请重新 prepare 核对当前范围。")
    if not current.get("candidate_test"):
        _check_preparation_route(project, skill, frozen_request, (scope or {}).get("routed"),
                                 repair=bool(basis.get("revision")),
                                 asset_batch=bool((scope or {}).get("asset_batch_sha256")),
                                 active_request=basis.get("direct_asset_request_sha256"))
    standards = basis["standards"]
    profile = basis.get("knowledge_profile", {})
    if not isinstance(profile, dict) or not profile.get("path") or not profile.get("sha256"):
        raise StageContextError("任务规范缺少版本证据；请重新 prepare。")
    profile_path = Path(profile["path"])
    if not manifest.get("test_only_proposed_method_ids"):
        profile_path = _local_knowledge_path(profile_path)
    if not profile_path.is_file() or digest(profile_path) != profile["sha256"]:
        raise StageContextError(f"当前方法、绑定或规范已改变：{profile_path}；请重新 prepare。")
    current_profile = _read_json(profile_path, default={})
    declared_methods = {entry.get("id"): entry for entry in current_profile.get("professional_methods", [])}
    local_documents = [*standards.get("local_documents", []), standards.get("review_profile", {})]
    if standards.get("root_rules_sha256"):
        local_documents.append({"path": standards["root_rules"], "sha256": standards["root_rules_sha256"]})
    documents = []
    for item in local_documents:
        if not item.get("path") or not item.get("sha256"):
            raise StageContextError("任务规范缺少版本证据；请重新 prepare。")
        documents.append((_local_knowledge_path(Path(item["path"])), item["sha256"]))
    bundled_documents = []
    for item in standards.get("professional_methods", []):
        bundled_documents.append((item["path"], item.get("sha256")))
        template_path = declared_methods.get(item["id"], {}).get("template_path")
        if template_path != item.get("template_path") or (template_path and not item.get("template_sha256")):
            raise StageContextError("适用方法模板缺少冻结版本；请重新 prepare。")
        if template_path:
            bundled_documents.append((template_path, item["template_sha256"]))
    bundled_paths = verify_selected_files(knowledge_root, (relative for relative, _ in bundled_documents),
                                          verified_manifest=verified_bundle)
    documents.extend((bundled_paths[relative], checksum) for relative, checksum in bundled_documents)
    document_digest = digest if file_digest is None else file_digest
    for path, checksum in documents:
        if not checksum:
            raise StageContextError("任务规范缺少版本证据；请重新 prepare。")
        if not path.is_file() or document_digest(path) != checksum:
            raise StageContextError(f"当前方法、绑定或规范已改变：{path}；请重新 prepare。")
    constraints = manifest.get("applicable_confirmed")
    if not isinstance(constraints, list):
        raise StageContextError("任务依据缺少预算外适用 confirmed 约束；请重新 prepare，不能沿用旧选择结果。")
    records = list(manifest.get("selected", [])) + constraints
    if adopted_candidates is None:
        evidence = _evidence_current(project, basis["skill"], basis=basis)
        adopted_candidates = evidence[1].get("adopted_candidates", []) if evidence else []
    records.extend(adopted_candidates)
    if records:
        tombstones = _tombstone_fingerprints(knowledge_root, load_policy(ROOT), verified_manifest=verified_bundle)
        for item in records:
            block = _read_candidate_exact(knowledge_root, item, verified_manifest=verified_bundle)
            if block.fingerprint in tombstones:
                raise StageContextError(f"当前知识已被禁用：{block.id}；请重新 prepare。")
    return {"current_input_validation": "matched_at_check", "execution_authorization": "not_granted",
            "legacy_unhashed_inputs": [item["record"]["artifact_id"] for item in current["artifacts"]
                                       if item["confirmation_byte_binding"] == "legacy_unhashed"]}


def _read_candidate_exact(vault: Path, record: dict[str, Any], *, verified_manifest=None) -> Any:
    if is_bundled_root(vault):
        manifest = verify_bundle(vault) if verified_manifest is None else verified_manifest
        if record.get("id") not in manifest["dynamic_rule_ids"]:
            raise StageContextError("动态规则未列入内置知识 manifest 允许列表")
        relative = record.get("vault_relative_path")
        verify_selected_files(vault, (relative,), verified_manifest=manifest)
    relative = record.get("vault_relative_path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise StageContextError("候选目录路径无效；请重新 prepare。")
    path = (vault / relative).resolve()
    try:
        path.relative_to(vault)
    except ValueError as exc:
        raise StageContextError("候选目录路径越出本轮 Vault；请重新 prepare。") from exc
    try:
        matches = [item for item in parse_knowledge_blocks(path, vault) if item.id == record.get("id")]
    except Exception as exc:
        raise StageContextError(f"候选知识无法按目录精确读取：{relative}: {exc}") from exc
    if len(matches) != 1 or _candidate_record(matches[0]) != record:
        raise StageContextError(f"候选知识版本、路径、状态或范围已变更：{record.get('id')}；请重新 prepare。")
    return matches[0]


def _check_run_budget(args: argparse.Namespace) -> dict[str, Any] | None:
    try:
        return checkpoint(args)
    except ValueError as exc:
        raise StageContextError(str(exc)) from exc


def _task_basis_path(project: Path) -> Path:
    """The one per-task source for scope and standards used by work and review."""
    return _context_dir(project, "task_basis.json")


def _review_basis_bytes(path: Path, *, optional=False) -> bytes | None:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        if optional and isinstance(exc, FileNotFoundError):
            return None
        raise StageContextError(f"无法读取本次审核的任务依据 {path}；请重新准备并打包：{exc}") from exc
    if not raw:
        raise StageContextError(f"审核依据为空：{path}；请重新准备并打包。")
    return raw


def _load_task_basis(project: Path, skill: str, *, raw=None) -> tuple[Path, dict[str, Any]]:
    path = _task_basis_path(project)
    try:
        basis = json.loads(raw.decode("utf-8")) if raw is not None else _read_json(path, default=None)
    except ValueError as exc:
        raise StageContextError(f"无法读取任务依据：{path}；请重新准备并打包。") from exc
    if not isinstance(basis, dict) or basis.get("schema_version") != "1.0" or basis.get("skill") != skill:
        raise StageContextError(f"缺少与当前 Skill 匹配的任务依据：{path}；先运行 prepare，不能由审核包另选标准。")
    if basis.get("context_id") != _CONTEXT_ID.get():
        raise StageContextError("任务依据属于另一批次上下文；请在本批次重新 prepare。")
    for key in ("objective", "deliverables", "non_goals", "input_versions", "standards"):
        if key not in basis:
            raise StageContextError(f"任务依据缺少 {key}：{path}；请重新 prepare。")
    return path, basis


def _review_target(project: Path, path: Path, review_kind: str) -> Path:
    """Production reviews are project-local; contracts are repository-local."""
    root = project if review_kind == "production" else ROOT
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        label = "项目" if review_kind == "production" else "仓库"
        raise StageContextError(f"{review_kind} 审核目标必须位于{label}内：{resolved}") from exc
    return resolved


def _delivery_contract(request: dict[str, Any]) -> dict[str, Any]:
    """Freeze the artifact intent for stages that still use preparation."""
    delivery_object = str(request.get("delivery_object") or "plan")
    if delivery_object not in {"prompt", "plan", "submission"}:
        raise StageContextError("request.delivery_object 只能是 prompt、plan 或 submission；未知类型不能静默跳过验收项。")
    return {"delivery_object": delivery_object}


def _require_preparation_skill(skill: str | None) -> None:
    if skill == "voiceover-to-video-script":
        raise StageContextError(
            "voiceover-to-video-script 准备包流程已退役；按 Skill 入口直接读取原始资料、"
            "编写与自查提示词，不创建或复用准备包、阅读凭据或审核包。"
        )


def _applicable_checks(items: list[dict[str, Any]], delivery_contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Profiles may narrow checks by artifact type."""
    delivery_object = delivery_contract["delivery_object"]
    return [
        item for item in items
        if (not item.get("applies_to") or delivery_object in item["applies_to"])
    ]


def _producer_response_value(raw: str) -> Path | dict[str, Any]:
    """Accept an inline producer reply; a JSON file is only an optional carrier."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return Path(raw).expanduser().resolve()
    if not isinstance(value, dict):
        raise StageContextError("--producer-response 的内联值必须是 JSON 对象。")
    return value


def _read_recovery_authority(path: Path) -> bytes:
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise StageContextError(f"必需共享规范缺失：{path}#{RECOVERY_HEADING}") from exc
    text = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    section = re.search(rf"(?ms)^## {RECOVERY_HEADING}\n(.*?)(?=^## |\Z)", text)
    if section is None or not section.group(1).strip():
        raise StageContextError(f"主动恢复核对正文缺失或为空：{path}#{RECOVERY_HEADING}")
    return content


@_context_scoped
def prepare(args: argparse.Namespace) -> dict[str, Any]:
    _require_preparation_skill(args.skill)
    recovery_path = _local_knowledge_path(ROOT / RECOVERY_DOCUMENT)
    recovery_bytes = _read_recovery_authority(recovery_path)
    usage = _check_run_budget(args)
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise StageContextError(f"项目目录不存在：{project}")
    supplied_vault = _vault_root(args.vault) if getattr(args, "vault", None) is not None else None
    with ExitStack() as stack:
        if args.skill == "creative-director":
            context = _CONTEXT_ID.get()
            name = f".prepare-batch-{context}.lock" if context else ".prepare-stage.lock"
            lock_path = project / CONTEXT_DIR.parent / name
            if lock_path.resolve() != lock_path:
                raise StageContextError(f"准备锁路径不得含符号链接：{lock_path}")
            try:
                stack.enter_context(file_lock(lock_path, timeout_seconds=0))
            except TimeoutError as exc:
                raise StageContextError("同一编导上下文正在准备，请继续等待原调用返回") from exc
        return _prepare(args, project, supplied_vault, usage, recovery_path, recovery_bytes)


def _direct_asset_binding(project, path, request_sha, targets, kinds, snapshot):
    """Keep each direct production request linked to the exact story after the next prepare."""
    stories = [item for item in snapshot['artifacts'] if item['record'].get('logical_id') == 'narrative.package'
               and item['record'].get('status') == 'current']
    if len(stories) != 1:
        return None
    story = stories[0]
    name = request_sha + '.json'
    binding = dict(skill='asset-image-assistant', request=path.relative_to(project).as_posix(),
                   request_sha256=request_sha, narrative_id=story['record']['artifact_id'],
                   narrative_sha256=story['file']['sha256'], targets=targets, image_output_kinds=kinds,
                   receipt=(DIRECT_RECEIPTS / name).as_posix())
    destination = (project / DIRECT_REQUESTS / name).resolve()
    if not destination.is_relative_to(project):
        raise StageContextError('直做资产请求冻结路径越出项目')
    with file_lock(project / PROJECT_LOCK_RELATIVE):
        if destination.is_file():
            if _read_json(destination, default=None) != binding:
                raise StageContextError('相同直做请求已绑定其他版本；请在请求中列明当前编导来源后重新准备')
        else:
            write_json(destination, binding)
    return binding


def _prepare(args: argparse.Namespace, project: Path, supplied_vault: Path | None, usage: dict | None,
             recovery_path: Path, recovery_bytes: bytes) -> dict[str, Any]:
    started = time.perf_counter()
    skill_dir = ROOT / "skills" / args.skill
    profile_path = skill_dir / "knowledge-profile.json"
    test_method_ids = set(getattr(args, "test_proposed_method", None) or [])
    test_profile_path = getattr(args, "test_profile_json", None)
    if test_profile_path and not test_method_ids:
        raise StageContextError("--test-profile-json 只能与 --test-proposed-method 一起用于隔离测试。")
    profile_path = (Path(test_profile_path).expanduser().resolve() if test_profile_path
                    else _local_knowledge_path(profile_path))
    profile = _read_json(profile_path, default=None)
    if not isinstance(profile, dict) or profile.get("skill") != args.skill:
        raise StageContextError(f"Skill 知识 profile 无效：{profile_path}")
    request = {}
    request_path = request_snapshot = None
    if args.request_json:
        request_path = Path(args.request_json).resolve()
        try:
            request_snapshot = _request_snapshot(request_path)
            request = request_snapshot[0]
        except FileNotFoundError as exc:
            raise StageContextError(f"请求文件不存在：{request_path}；请提供明确请求来源，不从项目入口或待审候选推断。") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StageContextError(f"无法读取请求 JSON：{request_path}: {exc}") from exc
    testing = _test_request(args, project)
    asset_batch = None
    if getattr(args, "batch_receipt", None):
        if testing:
            raise StageContextError("候选试跑不绑定正式图片批次回执")
        asset_batch = _asset_batch_handoff(project, args, args.skill, request_path=request_path,
                                          request_snapshot=request_snapshot)
    kickoff = None if testing else load_kickoff(project)
    request = bind_stage_request(request, kickoff)
    card = _handoff_path(project)
    routed = None
    if not testing and card.is_file():
        card_text = card.read_text(encoding="utf-8")
        if f"- 目标 Skill：`{args.skill}`\n" in card_text:
            routed = _routed_handoff_scope(card_text)
    product_pack = resolve_product_pack(kickoff["product"]) if kickoff and args.skill in {
        "creative-director", "storyboard-planner", "asset-image-assistant", "storyboard-director",
    } else None
    image_targets = _image_batch_targets(args.skill, request, routed=bool(routed and not asset_batch))
    if "revise_candidate_id" in request:
        if testing:
            raise StageContextError("隔离试跑不能发起正式待审稿返修")
        with file_lock(project / PROJECT_LOCK_RELATIVE):
            _check_preparation_route(project, args.skill, request, routed, repair=bool(getattr(args, "change_id", None)))
            manifest = load_manifest(project)
            pending = pending_narrative_candidate(manifest, request["revise_candidate_id"])
            if not pending.get("revision_requested"):
                pending["revision_requested"] = True
                save_manifest(project, manifest)
    elif not testing:
        _check_preparation_route(project, args.skill, request, routed,
                                 repair=bool(getattr(args, "change_id", None)), asset_batch=bool(asset_batch),
                                 active_request=(request_snapshot[1] if args.skill == 'asset-image-assistant'
                                                 and image_targets and _CONTEXT_ID.get() is None else None))
    try:
        output_requirements = check_output_plan(project, request, **({"candidate_test_mode": True} if testing else {})) if args.skill == "asset-image-assistant" else {}
    except ValueError as exc:
        raise StageContextError(str(exc)) from exc
    candidate_rule_ids = set(getattr(args, "candidate_rule", None) or [])
    candidate_source_id = getattr(args, "candidate_source_id", None)
    candidate_source_archive = getattr(args, "candidate_source_snapshot", None)
    if candidate_rule_ids or candidate_source_id or candidate_source_archive:
        if not testing:
            raise StageContextError("动态候选试用只允许在 candidate-test 项目中运行。")
        if not (candidate_rule_ids and candidate_source_id and candidate_source_archive):
            raise StageContextError("候选试用必须同时指定 --candidate-source-id、--candidate-source-snapshot 与至少一个 --candidate-rule。")
    delivery_contract = _delivery_contract(request)
    if testing and (delivery_contract["delivery_object"] == "submission" or request.get("generation_authorized")):
        raise StageContextError("候选测试只允许 plan/prompt，不允许生成提交")
    input_versions = _normalize_source_alias({
        key: value for key, value in request.items()
        if key.startswith("source_") or key in {"sources", "inputs", "material_output_ids", "supplement_request_paths"}
    })
    if routed and args.skill == "storyboard-planner" and routed["action"] == "director":
        bound_paths = re.findall(r"(?m)^- 导演已绑定输入：`([^`\n]+)`$", card_text)
        if bound_paths:
            input_versions["source_director_paths"] = bound_paths
    if routed and routed.get("artifact_ids"):
        ids = input_versions.get("source_artifact_ids", [])
        if not isinstance(ids, list):
            raise StageContextError("请求的 source_artifact_ids 必须是数组")
        input_versions["source_artifact_ids"] = list(dict.fromkeys([*routed["artifact_ids"], *ids]))
    if product_pack:
        input_versions["product_reference_files"] = [product_pack["manifest_path"],
                                                     *(item["path"] for item in product_pack["references"])]
    raw_state = _state(project)
    summary = _state_summary(raw_state)
    has_local_selection = args.skill == "asset-image-assistant" or any(request.get(key) for key in (
        "source_artifact_id", "source_artifact_ids", "source_shot_ids", "image_targets", "material_output_ids",
        "supplement_request_paths",
    )) or bool(getattr(args, "change_id", None) or (routed or {}).get("artifact_ids"))
    snapshot = _input_snapshot(project, input_versions, raw_state,
                               prefer_declared_artifacts=has_local_selection,
                               source_roles=_task_source_roles(args.skill, request))
    startup_state = _state_reading_view(summary)
    if snapshot.get("artifact_scope") == "declared":
        startup_state.update(current_artifacts=snapshot["selected_artifact_ids"],
                             user_confirmations=snapshot["authority"]["user_confirmations"],
                             accepted_issues=snapshot["authority"]["accepted_issues"])
    if args.skill == 'storyboard-director' and _redframe_image_batch(args.skill, request) and not testing:
        _validate_redframe_supplement_targets(project, request, snapshot)
    handoff_text = _current_handoff_text(project, args.skill, snapshot)
    handoff_scope = _handoff_scope(handoff_text)
    handoff_batch_bindings = _asset_batch_binding_entries(handoff_text)
    if handoff_batch_bindings:
        try:
            (binding,) = handoff_batch_bindings
            prior_batch = json.loads(binding)
            request_batch = _asset_batch_handoff(project, args, args.skill, request_path=request_path,
                request_snapshot=request_snapshot, receipt_ref=prior_batch["receipt"])
            if prior_batch != request_batch or (asset_batch is not None and prior_batch != asset_batch):
                raise ValueError("请求、目标或回执路径不一致")
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise StageContextError(f"本轮准备与已有图片批次交接不一致：{exc}") from exc
    revision = _revision_summary(
        project,
        getattr(args, "change_id", None),
        getattr(args, "target_logical_id", None),
    )
    policy = load_policy(ROOT)
    vault = supplied_vault or _vault_root(None)
    optional_experience = _optional_experience_paths(vault, profile)
    try:
        methods = resolve_method_documents(
            vault, profile, {"skill": args.skill, "request": request, "project_state": summary},
            test_proposed_method_ids=test_method_ids,
        )
    except MethodDocumentError as exc:
        raise StageContextError(str(exc)) from exc
    blocks = build_knowledge_index(vault, policy) if vault.is_dir() else []
    candidate_source_summary = None
    if candidate_source_id:
        try:
            candidate_trial_blocks = load_candidate_snapshot_rules(
                profile, Path(candidate_source_archive).expanduser().resolve(),
                candidate_source_id, candidate_rule_ids,
            )
            # The pinned candidate-test snapshot is the only copy of these
            # explicitly trialed IDs in this task. Do not let an enabled
            # local Vault copy of the same ID become formal selection input.
            blocks = [block for block in blocks if block.id not in candidate_rule_ids]
            blocks.extend(candidate_trial_blocks)
            candidate_source_summary = inspect_candidate_snapshot(
                profile, Path(candidate_source_archive).expanduser().resolve(), candidate_source_id,
            )
        except CandidateSnapshotError as exc:
            raise StageContextError(str(exc)) from exc
    result = select_knowledge(
        blocks, policy, args.skill, request, summary,
        _tombstone_fingerprints(vault, policy), profile=profile,
        candidate_trial_ids=candidate_rule_ids,
    )
    if any(item.get("reason") == "blocking_conflict" for item in result.blocking_conflicts):
        raise StageContextError(f"confirmed 知识冲突：{json.dumps(result.blocking_conflicts, ensure_ascii=False)}")
    objective = (args.objective or "").strip()
    if not objective:
        raise StageContextError("prepare 必须提供非空 --objective")
    objective = _coordinator_objective(args.skill, _CONTEXT_ID.get(), handoff_scope, testing) or objective
    bundle = render_knowledge_bundle(args.skill, result)
    candidate_index = render_knowledge_candidates(result)
    trial_bundle = render_candidate_trial_bundle(args.skill, result)
    method_lines = ["## 必读专业方法", ""]
    if methods.required:
        method_lines.append("- 以下必读内容已写入 `method_reading_packet.md`；生产者必须读完整个必读包后登记读取证据。")
        method_lines.extend(
            f"- `{item.id}`：`{item.vault_path}`；版本 `{item.version}`；SHA-256 `{item.checksum}`；适用条件：{item.applies_when}"
            for item in methods.required
        )
    else:
        not_applicable = [item for item in methods.pending if item["reason"] == "applies_when_not_met"]
        if not_applicable:
            method_lines.append("- 当前无适用的必读专业方法。")
            declared = {item.get("id"): item.get("applies_when", "") for item in profile.get("professional_methods", []) if isinstance(item, dict)}
            method_lines.extend(
                f"- `{item['id']}`：条件不满足，跳过；已知适用条件：{declared.get(item['id']) or '见 profile requires'}"
                for item in not_applicable
            )
        else:
            method_lines.append("- 无已获用户确认的必读专业方法。")
    unconfirmed = [item["id"] for item in methods.pending if item["reason"] == "awaiting_user_confirmation"]
    if unconfirmed:
        method_lines.append(f"- 待用户确认的候选方法：{', '.join(unconfirmed)}（不加载、不计入必读）。")
    if test_method_ids:
        method_lines.append("- 本启动包含显式测试候选方法；只能验证读取链路，`ensure-prepared` 会拒绝其用于生产。")
    if optional_experience:
        method_lines.extend([f"- 可选经验（根：`{vault}`；不自动加载；执行者按当前任务自行决定是否完整阅读，缺失或不适用不阻断）：", *(f"  - `{item}`" if exists else f"  - `{item}`（本轮 Vault 中缺失，跳过）" for item, exists in optional_experience)])
    method_lines.append("")
    revision_lines: list[str] = []
    if revision:
        revision_lines = [
            "## 前序修订约束", "", "```json",
            json.dumps(revision, ensure_ascii=False, indent=2), "```", "",
            "仅修改目标候选及其直接派生物；不得覆盖已确认版本、派发下一 Skill 或自行批准。", "",
        ]
    review_profile_path, review_profile = _load_review_profile(args.skill)
    request_checks = request.get("review_checks") or []
    if not isinstance(request_checks, list) or any(not isinstance(item, str) or not item.strip() for item in request_checks):
        raise StageContextError("request.review_checks 必须是非空文字检查项数组。")
    manual_checks = [{"id": f"MANUAL-{index:03d}", "text": text.strip()} for index, text in enumerate(request_checks, start=1)]
    methods_records = []
    for item in methods.required:
        record = {"id": item.id, "path": item.vault_path, "version": item.version,
                  "sha256": item.checksum, "promotion_basis": item.promotion_basis}
        if item.template_path:
            record.update(template_path=item.template_path, template_sha256=item.template_checksum)
        if item.reading_scope:
            record["reading_scope"] = item.reading_scope
        methods_records.append(record)
    base_packet = render_method_packet(methods.required) if methods.required else "# 本轮必读专业方法快照\n"
    trial_record = None
    if result.trial_candidates:
        trial_record = {
            "source_id": candidate_source_id,
            "archive_path": str(Path(candidate_source_archive).expanduser().resolve()),
            "archive_sha256": candidate_source_summary["archive_sha256"],
            "rule_ids": [item.id for item in result.trial_candidates],
            "rules": [_candidate_record(item) for item in result.trial_candidates],
            "packet_sha256": hashlib.sha256(trial_bundle.encode("utf-8")).hexdigest(),
        }
    reading_manifest = {"schema_version": "1.0", "skill": args.skill, "project_path": str(project), "vault_root": str(vault), **_reading_identity(args), "methods": methods_records, "method_packet_sha256": hashlib.sha256(base_packet.encode("utf-8")).hexdigest(), "method_packet_bytes": len(base_packet.encode("utf-8")), "test_only_proposed_method_ids": sorted(test_method_ids), "selected": [_candidate_record(item) for item in result.selected], "candidate_catalog": [_candidate_record(item) for item in result.candidates], "candidate_reasons": result.candidate_reasons, "applicable_confirmed": [_candidate_record(item) for item in result.applicable_confirmed], "candidate_trial": trial_record}

    @cache
    def read_document(path: Path) -> bytes:
        # Link discovery and the existing shared-contract hash use the same validated bytes.
        return recovery_bytes if path == recovery_path else path.read_bytes()

    local_documents = _local_standard_documents(skill_dir, request, read_document=read_document)
    rule_documents = [path for path in local_documents
                      if path.is_relative_to((ROOT / "docs/rules").resolve())]
    character_inputs = {}
    if args.skill == "asset-image-assistant" and image_targets is not None:
        kinds = request.get("image_output_kinds")
        template_kinds = None
        if isinstance(kinds, dict) and all(isinstance(kinds.get(target), str) and kinds[target].strip()
                                           for target in image_targets):
            template_kinds = character_template_kinds(kinds[target] for target in image_targets)
        if template_kinds is None or template_kinds:
            character_inputs = {"character_layout_references": character_layout_references(template_kinds),
                                "character_prompt_templates": character_prompt_templates(template_kinds)}
    task_basis = {
        "schema_version": "1.0",
        "skill": args.skill,
        "objective": objective,
        "deliverables": request.get("deliverables") or [objective],
        "non_goals": request.get("non_goals") or [],
        "input_versions": input_versions,
        "input_snapshot": snapshot,
        "handoff_scope": handoff_scope,
        "task_request": _semantic_request(request, args.skill),
        **({"character_output_requirements": output_requirements} if output_requirements else {}),
        **character_inputs,
        "request_path": str(Path(args.request_json).resolve()) if args.request_json else None,
        "knowledge_profile": fingerprint(profile_path),
        "scope": {key: request[key] for key in ("task_kind", "scope", "source_shot_ids", "generation_authorized", "model_selected", "image_targets") if key in request},
        "delivery_contract": delivery_contract,
        "standards": {
            "root_rules": str(ROOT / "PROJECT_RULES.md"),
            "root_rules_sha256": digest(ROOT / "PROJECT_RULES.md"),
            "skill_entry": str(skill_dir / "SKILL.md"),
            "professional_methods": [{"id": item.id, "path": item.vault_path, "version": item.version, "sha256": item.checksum, "promotion_basis": item.promotion_basis, **({"template_path": item.template_path, "template_sha256": item.template_checksum} if item.template_path else {})} for item in methods.required],
            "selected_knowledge": [_candidate_record(item) for item in result.selected],
            "applicable_confirmed": [_candidate_record(item) for item in result.applicable_confirmed],
            "review_profile": {"path": str(review_profile_path), "sha256": hashlib.sha256(review_profile_path.read_bytes()).hexdigest(), "schema_version": review_profile["schema_version"]},
            "local_documents": [{"path": str(path), "sha256": hashlib.sha256(read_document(path)).hexdigest()} for path in local_documents],
        },
        "review_acceptance": {"delivery_object": delivery_contract["delivery_object"], "delivery_contract": delivery_contract, "production": {"required_checks": _applicable_checks(review_profile.get("production_artifact_checks", []), delivery_contract), "manual_checks": manual_checks}, "skill_contract": {"required_checks": review_profile["skill_contract_checks"], "manual_checks": manual_checks}},
        "reading_manifest": reading_manifest,
        "candidate_trial": trial_record,
        "revision": revision,
    }
    if _CONTEXT_ID.get() is not None:
        task_basis["context_id"] = reading_manifest["context_id"] = _CONTEXT_ID.get()
    if testing:
        task_basis.update(test_metadata())
    task_basis['reading_reuse_key'] = _reading_reuse_key(task_basis)
    direct = None
    if (not testing and args.skill == 'asset-image-assistant' and image_targets and _CONTEXT_ID.get() is None
            and request_path and request_path.is_relative_to(project)):
        direct = _direct_asset_binding(project, request_path, request_snapshot[1], image_targets,
                                       request.get('image_output_kinds', {}), snapshot)
        if direct:
            task_basis['direct_asset_request_sha256'] = direct['request_sha256']
    content = "\n".join([
        "# 阶段启动包", "",
        f"- Skill：`{args.skill}`",
        f"- 目标：{objective}",
        *([f"- 本轮开工决定：`{project / REQUEST_PATH}` 的 `kickoff` 按本阶段适用范围纳入任务依据。"] if kickoff else []),
        *([f"- 复刻线按冻结的 `{ROOT / RECREATION_METHOD}` 执行，编导确认和本阶段采用依旧沿统一入口。"]
          if production_workflow(kickoff) == RECREATION_WORKFLOW and ROOT / RECREATION_METHOD in local_documents else []),
        *([f"- 产品参考包：`{product_pack['manifest_path']}`，用 `kickoff --project` 读取本轮用途的准确文件、职责与缺口；逐镜选用。"] if product_pack else []),
        *(["- execution_mode: candidate_test；review_status: unreviewed；production_blocked: true。仅候选文本/结构试跑，不生成、不发布、不正式采用。"] if testing else []),
        f"- 本轮唯一任务依据：`{_task_basis_path(project)}`（机器完整记录；通过 ensure-prepared --show-task-basis 读取目标、输入与冻结验收项，不整份打印后台目录）",
        f"- 交付意图：`{json.dumps(delivery_contract, ensure_ascii=False)}`；按本轮交付对象及适用检查项审核。",
        f"- 根规则：`{ROOT / 'PROJECT_RULES.md'}`",
        f"- Skill 入口：`{skill_dir / 'SKILL.md'}`",
        f"- 上下文压缩、会话恢复、工作区重置或需要找回任务状态时，首次续作前实读 `{recovery_path}#{RECOVERY_HEADING}`。"
        f"回读 `{_context_dir(project, 'stage_startup.md')}`，沿用本任务原有的上下文选择和执行者、任务身份运行 `ensure-prepared --show-task-basis` 核对当前版本与任务依据。",
        *(["- 本轮适用细则（准确版本已冻结在 task_basis.json）：",
           *[f"  - `{path}`" for path in rule_documents]] if rule_documents else []),
        "- 执行分工：遵守根规则的干净上下文启动规则；新一轮编导等创作生产或实质重跑由全新专属生产 agent 执行，不继承长主会话；本轮执行者读取、准备、产出并自查。其他任务按根规则执行。",
        "- 上下文边界：交接当前有效规则、原始素材、本轮约束与已确认决定及依据、现行版本、未决事项、范围和停止点；不得以摘要替代原片。已在本轮专属 agent 内的逐段推进和局部返修可继续；目标、来源或基线重大变化时新开，不按修订号机械重开。",
        f"- 等待及接收：执行前实读 `{ROOT / 'docs/rules/execution.md'}#等待与工具回传` 的宿主参数；沿原任务续等，完成后只交本轮身份、结论、差异及证据路径。原图由执行者实看，协调方按争议定向取图，不整份回传 JSON、日志或图组。",
        *([f"- 图片批次：`{_CONTEXT_ID.get()}`，本批冻结 {len(image_targets)} 个 image_targets；阶段负责人统筹整体方向、跨批连续性和正式交接，本批执行者读取真实参考、完成获准图片及实看自查。委派时只回传准确路径与检查摘要。批次目录不构成已生成、已检查、人工确认或正式采用。"] if image_targets is not None and _CONTEXT_ID.get() is not None else []),
        *([f"- 图片目标：本阶段请求冻结 {len(image_targets)} 个 image_targets；主对话直做批次沿用本阶段 task_basis，不另建批次目录，但仍须写回现有回执。"] if image_targets is not None and _CONTEXT_ID.get() is None else []),
        *([f"- 本批直做实看回执写入 `{direct['receipt']}`，顶层 request_sha256 为 `{direct['request_sha256']}`；整轮 show-round 可合并这些回执。"] if direct else []),
        *([f"- 红帧生图：读取 `{ROOT / _REDFRAME_IMAGE_STANDARDS[0]}#生图` 及同页连接入口。主对话从本图回执续作（resume_image_node / query）；有界等待用 exec_command 30000 ms、同 session 的 write_stdin 60000 ms，外层 yield 与内层对齐。首次先上传素材卡并当场归位，再接线和核正文；配套上传与排版规则同批冻结。"]
          if _redframe_image_batch(args.skill, request) else []),
        *(["- 导演写稿和返修：先实看影响拍法的原片连续窗，核关键对白指向、起幅、镜内轨迹、落幅与实际切出后的反应；然后才核逐镜字段和下游影响。沿现有自查记录写准确时间窗和镜号。"]
          if args.skill == 'storyboard-planner' else []),
        *(_asset_batch_delivery_lines(asset_batch) if asset_batch and not handoff_batch_bindings else []),
        *(["- 人物生图按输出类型读取 task_basis.json 的 character_prompt_templates 与 character_layout_references，使用对应模板正文，原参考在前、灰模在最后。按实际槽位绑定原生引用。"]
          if task_basis.get("character_layout_references") else []),
        "- prepare 只生成文件，不清空宿主对话。普通阶段按 skills/_shared/knowledge-access.md#普通阶段准备与选知 读取。仅在适用计量时看 docs/development/token-budget.md#普通准备的计量检查点。",
        "",
        "## 本轮范围", "",
        "- 交付对象、非目标、输入版本、范围字段和适用标准仅维护在上列 `task_basis.json`；本启动包不复制第二份范围记录。", "",
        "## 当前项目事实", "", "```json",
        json.dumps(startup_state, ensure_ascii=False, indent=2), "```", "",
        *( ["## 上一阶段交接事实", "", handoff_text.rstrip(), ""] if handoff_text else [] ),
        *revision_lines,
        *method_lines,
        bundle.rstrip(), "",
        *( ["## 按需知识候选", "", f"- 共 {len(result.candidates)} 条；完整可读目录：`knowledge_candidates.md`。仅按其中精确路径和锚点读取。", ""] if result.candidates else [] ),
        *( ["## 隔离候选知识试用", "", "- 仅 candidate-test 临时阅读，不进入正式选择集、确认或晋升；正文见 `candidate_trial_packet.md`。", ""] if result.trial_candidates else [] ),
        "## 选择记录", "",
        f"- 已选知识块：{', '.join(item.id for item in result.selected) or '无'}",
        f"- 按需知识候选：{len(result.candidates)}",
        f"- 延后知识块：{len(result.deferred)}",
        f"- 知识正文字符：{result.total_chars}", "",
    ])
    output = _context_dir(project, "stage_startup.md")
    report = validate_text("startup", output, content)
    _save_reading_cache(project)
    write_text_if_changed(output, content)
    write_text_if_changed(_task_basis_path(project), json.dumps(task_basis, ensure_ascii=False, indent=2) + "\n")
    if methods.required or result.candidates:
        current = _evidence_current(project, basis=task_basis)
        restored = current is None and _restore_reading_cache(project, args.skill, task_basis['reading_reuse_key'])
        report['stage_reading_restored'] = restored
        if current is None and not restored:
            write_text_if_changed(_context_dir(project, "method_reading_packet.md"), base_packet)
            stale = _context_dir(project, "method_reading_evidence.json")
            if stale.is_file(): stale.unlink()
        index_path = _context_dir(project, "knowledge_candidates.md")
        if result.candidates: write_text_if_changed(index_path, candidate_index)
        elif index_path.is_file(): index_path.unlink()
    else:
        for name in ("method_reading_packet.md", "method_reading_evidence.json", "knowledge_candidates.md"):
            stale = _context_dir(project, name)
            if stale.is_file(): stale.unlink()
    for name in ("method_reading_manifest.json", "candidate_reading_manifest.json", "candidate_reading_packet.md", "candidate_reading_evidence.json"):
        stale = _context_dir(project, name)
        if stale.is_file(): stale.unlink()
    trial_path = _context_dir(project, "candidate_trial_packet.md")
    if trial_record:
        write_text_if_changed(trial_path, trial_bundle)
    elif trial_path.is_file():
        trial_path.unlink()
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    report["required_method_count"] = len(methods.required)
    report["pending_method_count"] = len(methods.pending)
    report["optional_experience_count"] = len(optional_experience)
    report["method_reading_bytes"] = len(base_packet.encode("utf-8"))
    if testing:
        report.update(test_metadata())
        if trial_record:
            report["candidate_trial"] = {"rule_ids": trial_record["rule_ids"], "production_blocked": True}
    if usage is not None:
        report["run_usage"] = usage
    if direct:
        report["direct_asset_receipt"] = direct["receipt"]
        report["direct_request_sha256"] = direct["request_sha256"]
    if asset_batch:
        report.update(_asset_batch_dispatch(project, args, asset_batch, output, kickoff))
    return report


def _asset_batch_dispatch(project, args, batch, entry, kickoff):
    """Return host arguments for a bound built-in ImageGen batch; UI work stays with the coordinator."""
    result = {"asset_batch": batch}
    if ((kickoff or {}).get("image_generation") or {}).get("provider") != "imagegen":
        return result
    context = batch["context_id"]
    suffix = _identity_hash([str(project), context])[:8]
    identity = _reading_identity(args)
    identity_line = " ".join(f"{key}={value}" for key, value in identity.items() if value)
    result["spawn_agent"] = {
        "task_name": f"asset_{context.replace('-', '_')}_{suffix}",
        "fork_turns": "none",
        "message": (f"按 {ROOT / 'skills/asset-image-assistant/SKILL.md'} 执行项目 {project} 的图片批次 {context}。"
                    f"本轮入口 {entry}。在项目内读请求 {batch['request']}，按所列目标与本批授权制作。"
                    f"阅读身份 {identity_line or '沿用本批实际执行者与任务 ID'}。"
                    f"保存候选后写回 {batch['receipt']}，只回传结果或阻塞和回执位置。"),
    }
    return result


def _asset_batch_handoff(project, args, target_skill, *, request_path=None, request_snapshot=None, receipt_ref=None):
    request_ref = getattr(args, "request_json", None)
    receipt_ref = receipt_ref or getattr(args, "batch_receipt", None)
    if not request_ref and not receipt_ref:
        return None
    if (target_skill != "asset-image-assistant" or _CONTEXT_ID.get() is None
            or not request_ref or not receipt_ref or getattr(args, "candidate_test", False)):
        raise StageContextError("图片批次交接须指定资产 Skill、--context-id、--request-json 和 --batch-receipt")
    request = request_path or project_path(project, request_ref)
    receipt = project_path(project, receipt_ref)
    if (request == receipt or not all(path.is_relative_to(project) and path.suffix.lower() == ".json"
                                      for path in (request, receipt))):
        raise StageContextError("本批请求和回执须为项目内不同的 JSON 文件")
    request_payload, request_sha256 = request_snapshot if request_snapshot is not None else _request_snapshot(request)
    return {"context_id": _CONTEXT_ID.get(), "request": request.relative_to(project).as_posix(),
            "request_sha256": request_sha256, "receipt": receipt.relative_to(project).as_posix(),
            "targets": _image_batch_targets(target_skill, request_payload)}


_ASSET_BATCH_BINDING_PREFIX = "- 图片批次依据 `"


def _asset_batch_binding_line(batch):
    return _ASSET_BATCH_BINDING_PREFIX + json.dumps(batch, ensure_ascii=False, separators=(",", ":")) + "`"


def _asset_batch_binding_entries(text):
    return {line.removeprefix(_ASSET_BATCH_BINDING_PREFIX).removesuffix("`") for line in text.splitlines()
            if line.startswith(_ASSET_BATCH_BINDING_PREFIX) and line.endswith("`")}


def _asset_batch_delivery_lines(batch):
    return [
        "", "## 本批交回", "", _asset_batch_binding_line(batch),
        "- 本批执行者跟到准确候选成图落盘，或出现需交回的真实阻塞。提示词和提交清单是中间结果。",
        "- 完成时更新上述候选记录，顶层沿用本卡的 `context_id` 和 `request_sha256`。每项候选写准确的 `target`、`candidate_id`；所有已有成图均写 `purpose`、`path` 和 `sha256`，有偏差时附 `blocking_issue`。普通对话完成本批即回传实看回执。协调方按整轮目标用 chat_asset_review.py show-round 一次交审；清单就绪即发给用户，事后再统计性能。",
        "- 回执写齐后再通知协调方。完成通知只回批次、已完成目标、真实阻塞和回执路径；途中只通知可接续的新结果或需决策的阻塞。",
        "- 无成图可实看且确实需交回的阻塞，沿原回执 `items` 写准确 `target`、`status: blocked` 和简短 `blocking_issue`。",
        "- 主对话直接制作时核对现有回执。委派批次由协调方按 `docs/rules/execution.md#等待与工具回传` 续等。超时不叠加 `read_thread`、`list_agents`、目录/日志查询或提前调用 `--receive-batch`；沿原等待渠道和游标续等；收到需核验的可接续新结果时只核受影响项。超过本轮已约定总等待期限仍无终态时，只读回查原任务一次，不重新提交。收到终态后用 `handoff --receive-batch` 验收准确回执一次，缺项交原执行者补核。",
        "- 接收时保留返回的逐项 `issue`，据此修正原回执；原回执和候选未变时沿用这次结果，等执行者补齐后再验。",
        "- 回执列出本批候选，网站或明确授权的对话确认继续走原入口。", "",
    ]


def _ensure_prepared(args: argparse.Namespace, *, file_digest) -> dict[str, Any]:
    """产前硬门禁：生产某 stage 产物前，校验阶段启动包存在且 Skill 匹配。

    校验结构、已选知识正文和调用方声明的任务必需项。
    通过不证明 Agent 已阅读或完成知识合规审查。
    """
    usage = _check_run_budget(args)
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise StageContextError(f"项目目录不存在：{project}")
    requested = getattr(args, "skill", None)
    _require_preparation_skill(requested)
    startup = _context_dir(project, "stage_startup.md")
    if not startup.is_file():
        message = f"未发现阶段启动包：{startup}。生产前必须先 prepare 拿到本 Skill 知识包；请先运行 prepare。"
        if requested:
            message = f"未发现 {requested} 的阶段启动包：{startup}。生产前必须先 prepare 拿到本 Skill 知识包。"
        raise StageContextError(message)
    text = startup.read_text(encoding="utf-8")
    if "# 阶段启动包" not in text:
        raise StageContextError(f"阶段启动包无效（缺少 # 阶段启动包）：{startup}，请重新 prepare。")
    match = re.search(r"(?m)^- Skill：`([^`]+)`\s*$", text)
    if not match:
        raise StageContextError(f"阶段启动包缺少可识别的 Skill 身份：{startup}，请重新 prepare。")
    skill = match.group(1)
    _require_preparation_skill(skill)
    if requested and requested != skill:
        raise StageContextError(
            f"阶段启动包 Skill 与当前任务不符（启动包={skill}，请求={requested}）："
            f"请先为该 Skill 运行 prepare，不得沿用上一个 Skill 的启动包。"
        )
    validate_text("startup", startup, text)
    # Distinguish a complete selection record from merely having a heading.
    body, separator, record = text.rpartition("\n## 选择记录")
    selected = re.search(r"(?m)^- 已选知识块：(.+)$", record)
    if not separator or not selected:
        raise StageContextError("启动包缺少已选知识记录，请重新 prepare。")
    raw_ids = selected.group(1).strip()
    ids = [] if raw_ids == "无" else [item.strip() for item in raw_ids.split(",")]
    bundle = body.split(f"# {skill} 当前知识包", 1)[1]
    blocks = list(re.finditer(r"(?m)^## (MSB-KB-[\w-]+) \[[^\]\n]+\]\s*$", bundle))
    actual = [item.group(1) for item in blocks]
    if len(set(ids)) != len(ids) or ids != actual:
        raise StageContextError("已选知识记录与正文不一致，请重新 prepare。")
    for index, block in enumerate(blocks):
        end = blocks[index + 1].start() if index + 1 < len(blocks) else len(bundle)
        content = bundle[block.end():end]
        content = re.sub(r"(?m)^适用条件：.*$", "", content).strip()
        if not content:
            raise StageContextError(f"知识块正文为空：{block.group(1)}，请重新 prepare。")
    if not _task_basis_path(project).is_file():
        raise StageContextError("启动包缺少仓库内置知识来源证据（阅读任务清单缺失或无效）；请重新 prepare。")
    _, basis = _load_task_basis(project, skill)
    manifest = basis.get("reading_manifest")
    trial = manifest.get("candidate_trial") if isinstance(manifest, dict) else None
    trial_packet = _context_dir(project, "candidate_trial_packet.md")
    if trial is not None:
        if not test_context(project) or "execution_mode: candidate_test" not in text:
            raise StageContextError("隔离候选试用包只能保留在 candidate-test 项目中。")
        if not isinstance(trial, dict) or not trial_packet.is_file():
            raise StageContextError("隔离候选试用记录或正文缺失；请重新 prepare。")
        packet_bytes = trial_packet.read_bytes()
        if hashlib.sha256(packet_bytes).hexdigest() != trial.get("packet_sha256"):
            raise StageContextError("隔离候选试用正文已漂移；请重新 prepare。")
        source_snapshot = trial.get("archive_path")
        source_id = trial.get("source_id")
        rule_ids = trial.get("rule_ids")
        profile = _read_json(_local_knowledge_path(ROOT / "skills" / skill / "knowledge-profile.json"), default=None)
        if not isinstance(profile, dict) or not isinstance(source_snapshot, str) or not isinstance(source_id, str) or not isinstance(rule_ids, list):
            raise StageContextError("隔离候选来源绑定无效；请重新 prepare。")
        try:
            current_rules = load_candidate_snapshot_rules(
                profile, Path(source_snapshot), source_id, set(rule_ids),
            )
        except CandidateSnapshotError as exc:
            raise StageContextError(str(exc)) from exc
        if [_candidate_record(item) for item in current_rules] != trial.get("rules"):
            raise StageContextError("隔离候选来源正文、版本或 hash 已漂移；请重新 prepare。")
    elif trial_packet.exists():
        raise StageContextError("发现未登记的隔离候选试用包；请重新 prepare。")
    reading = _evidence_current(project, skill, basis=basis)
    adopted = reading[1].get("adopted_candidates", []) if reading else []
    adopted_ids = {item["id"] for item in adopted}
    for rule in getattr(args, "required_rule", None) or []:
        if rule not in ids and rule not in adopted_ids:
            raise StageContextError(f"当前任务必需动态规则未命中：{rule}")
    required_references = getattr(args, "required_reference", None) or []
    if required_references:
        frozen = {item["path"]: item["sha256"] for item in basis["standards"].get("local_documents", [])}
        frozen[basis["standards"]["root_rules"]] = basis["standards"]["root_rules_sha256"]
        for reference in required_references:
            path = (ROOT / reference).resolve()
            if not path.is_relative_to(ROOT.resolve()) or not path.is_file() or not path.stat().st_size:
                raise StageContextError(f"当前任务必需本地规范不存在、为空或越出仓库：{reference}")
            if str(path) not in frozen:
                raise StageContextError(f"必需本地规范未在本轮冻结：{reference}；在 prepare 请求 local_references 中列明并重新准备。")
            if file_digest(path) != frozen[str(path)]:
                raise StageContextError(f"必需本地规范已变更：{reference}；请重新 prepare。")
    result = {
        "kind": "startup",
        "path": str(startup),
        "skill": skill,
        "prepared": True,
        "knowledge_compliance": "requires_agent_review",
        "ok": True,
    }
    if usage is not None:
        result["run_usage"] = usage
    if not isinstance(manifest, dict):
        raise StageContextError("启动包缺少仓库内置知识来源证据（阅读任务清单缺失或无效）；请重新 prepare。")
    result.update(_validate_current_inputs(project, basis, adopted_candidates=adopted, file_digest=file_digest))
    if manifest.get("skill") != skill or manifest.get("project_path") != str(project) or not isinstance(manifest.get("methods"), list):
        raise StageContextError("阅读任务清单无效，请重新 prepare。")
    test_only = manifest.get("test_only_proposed_method_ids", [])
    if not isinstance(test_only, list) or not all(isinstance(item, str) and item for item in test_only):
        raise StageContextError("阅读任务清单的测试标记无效，请重新 prepare。")
    # A candidate directory is discoverability only. With no mandatory method it cannot
    # turn optional reading into a production gate.
    if not manifest["methods"]:
        if adopted:
            result["candidate_reading_evidence"] = "recorded"
        elif not reading:
            evidence = _read_json(_context_dir(project, "method_reading_evidence.json"), default=None)
            if isinstance(evidence, dict) and evidence.get("adopted_candidates"):
                result["candidate_reading_evidence"] = "invalid"
        if test_only:
            result["ok"] = False
    else:
        identity = _reading_identity(args)
        manifest_identity = {key: manifest.get(key) for key in identity}
        identity_matches = all(identity.values()) and all(isinstance(value, str) and value for value in manifest_identity.values()) and identity == manifest_identity
        complete = bool(reading and identity_matches)
        result["reading_identity"] = "matched" if identity_matches else ("required" if not all(identity.values()) or not all(manifest_identity.values()) else "mismatch")
        result["method_reading_evidence"] = "recorded" if complete else "required"
        result["required_method_count"] = len(manifest["methods"])
        result["test_only_proposed_method_ids"] = test_only
        result["ok"] = result["ok"] and complete and not test_only
    if test_only:
        result["production_blocked"] = "test_only_proposed_method_binding"
    if getattr(args, "show_task_basis", False) and result["ok"]:
        result["task_basis"] = _task_reading_view(basis)
    return result


@_context_scoped
def ensure_prepared(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(args.project).expanduser().resolve()
    testing = _test_request(args, project)
    recovery_path = _local_knowledge_path(ROOT / RECOVERY_DOCUMENT)

    def current_digest(path: Path) -> str:
        if path == recovery_path:
            return hashlib.sha256(_read_recovery_authority(path)).hexdigest()
        return digest(path)

    file_digest = cache_file_digests(current_digest, check_for_changes=True)
    if getattr(args, "mark_read", False):
        _record_method_reading(args, file_digest=file_digest)
    result = _ensure_prepared(args, file_digest=file_digest)
    if testing:
        result.update(test_metadata())
        result["candidate_checks_passed"] = result.get("ok") is True
    return result

@_context_scoped
def record_method_reading(args: argparse.Namespace) -> dict[str, Any]:
    return _record_method_reading(args)


def _record_method_reading(args: argparse.Namespace, *, file_digest=None) -> dict[str, Any]:
    project = Path(args.project).expanduser().resolve()
    skill = str(args.skill or "").strip()
    _require_preparation_skill(skill)
    _, basis = _load_task_basis(project, skill)
    manifest = basis.get("reading_manifest")
    if not skill or not isinstance(manifest, dict) or manifest.get("skill") != skill or manifest.get("project_path") != str(project) or not isinstance(manifest.get("methods"), list):
        raise StageContextError("没有与当前 Skill 匹配的阅读任务清单；请先 prepare。")
    _validate_current_inputs(project, basis, file_digest=file_digest)
    identity = _reading_identity(args)
    if not all(identity.values()) or any(manifest.get(key) != identity[key] for key in identity):
        raise StageContextError("登记读取证据必须使用 prepare 时记录的实际执行者 --actor-id 与稳定任务 --task-id。")
    requested = list(getattr(args, "method", None) or [])
    methods = [item.get("id") for item in manifest["methods"] if isinstance(item, dict)]
    if sorted(requested) != sorted(methods) or len(requested) != len(set(requested)):
        raise StageContextError("读取证据必须一次列出且仅列出本轮全部必读专业方法 ID。")
    requested_candidates = list(getattr(args, "candidate_rule", None) or [])
    supplied_root = getattr(args, "vault", None)
    vault = Path(manifest["vault_root"])
    if supplied_root is not None and _vault_root(supplied_root) != vault:
        raise StageContextError("读取知识根必须与 prepare 时的仓库内置根一致。")
    catalog = {item.get("id"): item for item in manifest.get("candidate_catalog", []) if isinstance(item, dict)}
    if len(requested_candidates) != len(set(requested_candidates)) or any(rule not in catalog for rule in requested_candidates):
        raise StageContextError("候选读取只能列出当前 `knowledge_candidates.md` 中的非重复规则 ID。")
    scopes = {}
    for value in getattr(args, "candidate_scope", None) or []:
        rule, separator, scope = value.partition("=")
        if not separator or rule not in requested_candidates or rule in scopes or not scope.strip():
            raise StageContextError("--candidate-scope 必须为本轮采用的非重复 ID=本职适用范围。")
        scopes[rule] = scope.strip()
    reasons = manifest.get("candidate_reasons", {})
    if any(str(reasons.get(rule, "")).startswith("skill_boundary:") and rule not in scopes for rule in requested_candidates):
        raise StageContextError("跨职责候选只能采用本职内容；须用 --candidate-scope ID=本职适用范围 声明，范围外实现项不成为产出要求。")
    verified_bundle = verify_bundle(vault) if requested_candidates and is_bundled_root(vault) else None
    adopted = [_read_candidate_exact(vault, catalog[rule], verified_manifest=verified_bundle)
               for rule in requested_candidates]
    if adopted:
        tombstones = _tombstone_fingerprints(vault, load_policy(ROOT), verified_manifest=verified_bundle)
        if any(item.fingerprint in tombstones for item in adopted):
            raise StageContextError("候选知识已被墓碑禁用；请重新 prepare。")
    records = [_candidate_record(item) for item in adopted]
    constraints = manifest.get("applicable_confirmed", [])
    all_records = list({item["id"]: item for item in [*manifest.get("selected", []), *constraints, *records]
                        if isinstance(item, dict)}.values())
    for index, left in enumerate(all_records):
        for right in all_records[index + 1:]:
            if left["canonical_key"] == right["canonical_key"]:
                raise StageContextError(f"候选知识与当前知识重复同一规范：{left['id']} / {right['id']}。")
            if right["canonical_key"] in left["conflicts_with"] or left["canonical_key"] in right["conflicts_with"]:
                raise StageContextError(f"候选知识与当前已选知识冲突：{left['id']} / {right['id']}。")
    packet = _context_dir(project, "method_reading_packet.md")
    if not packet.is_file():
        raise StageContextError("必读方法快照缺失；请重新 prepare，不能登记读取。")
    existing = packet.read_bytes()
    base_length = manifest.get("method_packet_bytes")
    if not isinstance(base_length, int) or base_length < 0 or len(existing) < base_length or hashlib.sha256(existing[:base_length]).hexdigest() != manifest.get("method_packet_sha256"):
        raise StageContextError("必读方法快照已被修改；请重新 prepare，不能登记读取。")
    packet_text = existing[:base_length].decode("utf-8") + (_candidate_packet(adopted, scopes) if adopted else "")
    write_text_if_changed(packet, packet_text)
    evidence = {"schema_version": "1.0", "skill": skill, "project_path": str(project), "vault_root": str(vault), **identity,
        "methods": manifest["methods"], "selected": manifest.get("selected", []), "method_packet_sha256": manifest["method_packet_sha256"],
        "test_only_proposed_method_ids": manifest.get("test_only_proposed_method_ids", []),
        "adopted_candidates": records, "candidate_scopes": scopes, "packet_sha256": hashlib.sha256(packet.read_bytes()).hexdigest(),
        "attestation": "agent_reported_complete_reading"}
    if _CONTEXT_ID.get() is not None:
        evidence["context_id"] = _CONTEXT_ID.get()
    evidence['reading_reuse_key'] = basis.get('reading_reuse_key')
    output = _context_dir(project, "method_reading_evidence.json")
    write_text_if_changed(output, json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    _save_reading_cache(project)
    return {"kind": "reading_evidence", "path": str(output), "packet_path": str(packet), "skill": skill, "method_count": len(methods), "candidate_count": len(records), "evidence": "agent_reported_not_program_verified", "ok": True}
