#!/usr/bin/env python3
"""Lean stage lifecycle, project input validation, and handoff helpers (reading check-in ceremony retired)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any

from .agent_acceptance import accept_artifact, register_workflow_authorization, require_workflow_authorization
from .artifacts import (
    artifact_is_adopted, cache_file_digests, digest, exact_narrative_approval, load_manifest,
    pending_narrative_candidate, project_path, save_manifest, validate_candidate_submission,
    validate_current_dependencies,
)
from .asset_batch_receipt import (
    DIRECT_RECEIPTS, DIRECT_REQUESTS, summarize_asset_batch_receipt, validate_asset_batch_targets,
)
from .asset_refs import registered_output_snapshot
from .candidate_test import (
    configure as configure_test, context as test_context, require_production,
    require_production_manifest, resolve_inputs as test_inputs, result_metadata as test_metadata,
    validate_stage as validate_test_stage, validate_usage_contract,
)
from .errors import StageContextError
from .io import (
    PROJECT_LOCK_RELATIVE, file_lock, read_stage_json as _read_json,
    write_json, write_text, write_text_if_changed,
)
from .product_reference_pack import resolve_product_pack
from .production_handoff import (
    LEGACY_RULE, recreation_prompt_context, require_production_handoff, resolve_production_handoff,
)
from .project_kickoff import (
    RECREATION_METHOD, RECREATION_WORKFLOW, REQUEST_PATH, bind_stage_request,
    kickoff_project, load_kickoff, load_project_request, production_workflow,
)
from .project_state import load_project_state, normalize_project_state, write_project_state
from .review_publication import adopt_chat_script, adopt_package, is_recorded_website_script_approval
from .revision_control import RevisionControlError, guard_action, inspect_revision_status, open_change, show_change

ROOT = Path(__file__).resolve().parents[2]
CONTEXT_DIR = Path("00_项目管理") / "执行上下文"
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


def _identity_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _context_dir(project: Path, *parts: str) -> Path:
    return _context_path(project, _CONTEXT_ID.get(), *parts)


def _context_path(project: Path, context_id: str | None, *parts: str) -> Path:
    path = project / CONTEXT_DIR
    if context_id is not None:
        path = path / "批次" / context_id
    path = path.joinpath(*parts)
    current = project
    for part in path.relative_to(project).parts:
        current = current / part
        if current.is_symlink():
            raise StageContextError(f"批次上下文路径不得含符号链接：{current}")
    if not path.resolve().is_relative_to(project.resolve()):
        raise StageContextError("批次上下文路径越出项目")
    return path


def _context_scoped(function):
    @wraps(function)
    def scoped(args, *values, **kwargs):
        context_id = getattr(args, "context_id", None)
        if context_id is not None and (not isinstance(context_id, str)
                or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", context_id) is None):
            raise StageContextError("--context-id 须为 1–64 位规范小写 ID（小写字母、数字、下划线或连字符）")
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


def _image_batch_targets(skill: str, request: dict[str, Any], *, routed: bool = False) -> list[str] | None:
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


def _test_request(args, project: Path):
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


def authorize_workflow(args: argparse.Namespace) -> dict[str, Any]:
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


def _handoff_path(project: Path) -> Path:
    return _context_dir(project, "next_stage_handoff.md")


def _confirmed_project_inputs(project: Path, state: dict[str, Any], *, manifest=None, file_digest=None,
                              artifact_ids=None, checked_dependencies=None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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


def validate_text(kind: str, path: Path, text: str | None = None) -> dict[str, Any]:
    if text is None:
        text = path.read_text(encoding="utf-8")
    raw = text.encode("utf-8")
    limit = LIMITS[kind]
    if len(raw) > limit:
        raise StageContextError(f"{kind} 文件超出上限 {limit} bytes：{len(raw)} bytes ({path})")
    for label, pattern in BANNED.items():
        if pattern.search(text):
            raise StageContextError(f"{kind} 文件包含禁止内容（{label}）：{path}")
    return {"ok": True, "kind": kind, "path": str(path), "bytes": len(raw), "limit": limit}


def _asset_batch_dispatch(project: Path, args, batch: dict[str, Any], entry: Path, kickoff):
    result: dict[str, Any] = {"asset_batch": batch}
    if ((kickoff or {}).get("image_generation") or {}).get("provider") != "imagegen":
        return result
    context = batch["context_id"]
    suffix = _identity_hash([str(project), context])[:8]
    result["spawn_agent"] = {
        "task_name": f"asset_{context.replace('-', '_')}_{suffix}",
        "fork_turns": "none",
        "message": (f"按 {ROOT / 'skills/asset-image-assistant/SKILL.md'} 执行项目 {project} 的图片批次 {context}。"
                    f"本轮入口 {entry}。在项目内读请求 {batch['request']}，按所列目标与本批授权制作。"
                    f"保存候选并实看原图后写回 {batch['receipt']}，只回传结果或阻塞和回执位置。"),
    }
    return result


def _asset_batch_handoff(project: Path, args, target_skill: str, *, request_path=None, request_snapshot=None, receipt_ref=None):
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


def _asset_batch_binding_line(batch: dict[str, Any]) -> str:
    return _ASSET_BATCH_BINDING_PREFIX + json.dumps(batch, ensure_ascii=False, separators=(",", ":")) + "`"


def _asset_batch_binding_entries(text: str) -> set[str]:
    return {line.removeprefix(_ASSET_BATCH_BINDING_PREFIX).removesuffix("`") for line in text.splitlines()
            if line.startswith(_ASSET_BATCH_BINDING_PREFIX) and line.endswith("`")}


def _asset_batch_delivery_lines(batch: dict[str, Any]) -> list[str]:
    return [
        "", "## 本批交回", "", _asset_batch_binding_line(batch),
        "- 本批执行者跟到准确候选成图落盘并原图实看，或出现需交回的真实阻塞。",
        "- 完成时更新上述候选记录，顶层沿用本卡的 `context_id` 和 `request_sha256`。每项候选写准确的 `target`、`candidate_id`、`purpose`、`path` 和 `sha256`。",
        "",
    ]
