#!/usr/bin/env python3
"""Build and validate bounded stage context files without starting agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path, PureWindowsPath
from typing import Any

try:
    from .core.candidate_test import context as test_context, configure as configure_test, resolve_inputs as test_inputs, result_metadata as test_metadata, require_production
    from .core.artifacts import (artifact_is_confirmed, project_path, digest,
                                 validate_current_dependencies, load_manifest,
                                 register_workspace_artifact, adopt_initial_package,
                                 validate_candidate_submission)
    from .core.io import write_text, write_text_if_changed
    from .core.knowledge_blocks import build_knowledge_index, load_policy, parse_knowledge_blocks
    from .core.knowledge_selector import render_knowledge_bundle, render_knowledge_candidates, render_candidate_trial_bundle, select_knowledge
    from .core.candidate_source_snapshot import CandidateSnapshotError, inspect_candidate_snapshot, load_candidate_snapshot_rules
    from .core.method_documents import MethodDocumentError, render_method_packet, resolve_method_documents
    from .core.project_state import load_project_state, normalize_project_state
    from .core.revision_control import RevisionControlError, guard_action, show_change
    from .core.review_delta import fingerprint, revision_delta, lightweight_revision
    from .session_usage import measure, measure_boundaries, check_budget
except ImportError:  # direct CLI execution
    from core.candidate_test import context as test_context, configure as configure_test, resolve_inputs as test_inputs, result_metadata as test_metadata, require_production
    from core.artifacts import (artifact_is_confirmed, project_path, digest,
                                validate_current_dependencies, load_manifest,
                                register_workspace_artifact, adopt_initial_package,
                                validate_candidate_submission)
    from core.io import write_text, write_text_if_changed
    from core.knowledge_blocks import build_knowledge_index, load_policy, parse_knowledge_blocks
    from core.knowledge_selector import render_knowledge_bundle, render_knowledge_candidates, render_candidate_trial_bundle, select_knowledge
    from core.candidate_source_snapshot import CandidateSnapshotError, inspect_candidate_snapshot, load_candidate_snapshot_rules
    from core.method_documents import MethodDocumentError, render_method_packet, resolve_method_documents
    from core.project_state import load_project_state, normalize_project_state
    from core.revision_control import RevisionControlError, guard_action, show_change
    from core.review_delta import fingerprint, revision_delta, lightweight_revision
    from session_usage import measure, measure_boundaries, check_budget


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_DIR = Path("00_项目管理") / "执行上下文"
LIMITS = {"startup": 48 * 1024, "review": 12 * 1024, "handoff": 8 * 1024}
BANNED = {
    "图片载荷": re.compile(r"data:image|image_url|base64[,;]", re.I),
    "原始聊天": re.compile(r"(?:^|\n)#{1,4}\s*(?:原始聊天|完整对话|chat history)", re.I),
    "旧运行协议": re.compile("|".join(("required_context_" + "manifest", "dispatch_" + "envelope", "execution_" + "receipt", "visual_context_" + "gate")), re.I),
    "内部推理": re.compile(r"(?:chain.of.thought|内部推理|reasoning trace)", re.I),
}


class StageContextError(ValueError):
    pass


def _vault_root(value: str | None) -> Path:
    raw = value or os.environ.get("MSB_VAULT_ROOT")
    return Path(raw).expanduser().resolve() if raw else Path.home() / "Documents" / "Obsidian Vault"


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageContextError(f"无法读取 JSON：{path}: {exc}") from exc


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
            require_production(project)
        except ValueError as exc:
            raise StageContextError(str(exc)) from exc
    return testing


def _selected_inputs(project, state):
    if test_context(project):
        return state, test_inputs(project)
    return _confirmed_project_inputs(project, state)


def _state_summary(raw: dict[str, Any]) -> dict[str, Any]:
    state = normalize_project_state(raw)
    return {key: value for key, value in state.items() if key != "schema_version"}


def adopt_package(args: argparse.Namespace) -> dict[str, Any]:
    """Register and adopt an initial platform-neutral manuscript package."""
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise StageContextError(f"项目目录不存在：{project}")
    require_production(project)
    logical_id = args.logical_id
    if logical_id not in {"narrative.package", "storyboard.package", "video.prompt"}:
        raise StageContextError("仅支持正式叙事包、导演包或视频提示词包")
    if not args.approval_ref.strip():
        raise StageContextError("正式稿采用必须绑定准确用户确认依据 --approval-ref")
    source = project_path(project, args.path)
    if not source.is_file() or not source.is_relative_to(project) or source.suffix.lower() != ".json":
        raise StageContextError("正式稿件路径必须是项目内 JSON 文件；渠道导出不是核心稿件")
    try:
        package = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageContextError(f"正式稿件 JSON 不可读：{exc}") from exc
    if not isinstance(package, dict) or not isinstance(package.get("revision"), str):
        raise StageContextError("正式稿件必须含准确 revision")
    if package.get("revision") != args.revision:
        raise StageContextError("--revision 与正式稿件正文 revision 不一致")
    if logical_id == "video.prompt" and "target_url" in package and "drafts" in package:
        raise StageContextError("此命令只采用平台无关视频提示词；渠道冻结快照沿用渠道自己的检查")
    if logical_id == "narrative.package" and (
            package.get("schema_version") != "2.0" or "story_paragraphs" not in package):
        raise StageContextError("首次正式采用只接收当前 narrative.package 2.0；旧事件稿沿历史路径处理")
    if logical_id == "storyboard.package" and package.get("schema_version") != "4.0":
        raise StageContextError("首次正式采用只接收当前 storyboard.package 4.0；旧版本保持兼容读取")
    if logical_id == "video.prompt" and not isinstance(package.get("source_storyboard"), dict):
        raise StageContextError("首次正式采用视频提示词必须绑定已确认 source_storyboard")
    relative = source.relative_to(project).as_posix()
    try:
        manifest = load_manifest(project)
        existing = next((item for item in manifest.get("artifacts", [])
                         if item.get("artifact_id") == args.artifact_id), None)
        if existing is None:
            register_workspace_artifact(project, {
                "artifact_id": args.artifact_id, "logical_id": logical_id, "type": logical_id,
                "revision": args.revision, "path_or_url": relative,
                "depends_on": list(args.depends_on), "checksum": digest(source), "user_approved": False,
            })
        elif (existing.get("status") != "candidate" or not existing.get("checksum")
              or existing.get("logical_id") != logical_id
              or existing.get("revision") != args.revision or existing.get("path_or_url") != relative
              or existing.get("depends_on", []) != list(args.depends_on)):
            raise StageContextError("同 ID 已登记为不同稿件；保留现状并使用新 artifact_id")
        adoption = adopt_initial_package(project, args.artifact_id, args.approval_ref,
                                        scope_description=args.scope_description)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise StageContextError(f"稿件保持候选，正式采用未完成：{exc}") from exc
    return {"ok": True, **adoption,
            "notice": "平台无关稿件已正式登记；未执行渠道上传、生成、发布或阶段自动流转"}


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


def _tombstone_fingerprints(vault: Path, policy: dict[str, Any]) -> set[str]:
    relative = (policy.get("discovery") or {}).get("tombstones")
    path = vault / relative if isinstance(relative, str) else None
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
    profile_path = ROOT / "skills" / skill / "review-profile.json"
    profile = _read_json(profile_path, default=None)
    if not isinstance(profile, dict):
        raise StageContextError(f"Skill 审核 profile 不存在或无效：{profile_path}")
    if profile.get("schema_version") != "1.0" or profile.get("skill") != skill:
        raise StageContextError(f"Skill 审核 profile 身份无效：{profile_path}")
    for field in ("skill_contract_checks", "production_artifact_checks"):
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
    return project / CONTEXT_DIR / "next_stage_handoff.md"


def _confirmed_handoff_inputs(project: Path, target_skill: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    skill_path = ROOT / "skills" / target_skill / "SKILL.md"
    if not skill_path.is_file():
        raise StageContextError(f"目标 Skill 不存在：{target_skill}")
    state = _state(project)
    expected = str(state.get("next_stage") or "")
    if not expected:
        raise StageContextError("project_state.next_stage 为空；下一阶段未知，不能生成正式交接")
    if expected != target_skill:
        raise StageContextError(f"目标 Skill 与项目下一阶段不一致：state={expected}, requested={target_skill}")
    return _confirmed_project_inputs(project, state)


def status(args: argparse.Namespace) -> dict[str, Any]:
    """Read project/repository facts without recovering or synchronizing state."""
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise StageContextError(f"项目目录不存在：{project}")
    manage = project / "00_项目管理"
    state_path = manage / "project_state.json"
    manifest_path = manage / "artifact_manifest.json"
    control_path = manage / "change_control.json"
    state = None
    manifest = None
    blockers: list[str] = []
    if not state_path.is_file():
        blockers.append("project_state.json 缺失；当前阶段及下一阶段未知")
    else:
        try:
            state = _state(project)
        except (StageContextError, ValueError) as exc:
            blockers.append(f"project_state.json 无效；阶段状态未知：{exc}")
    candidate_mode = False
    candidate_context = None
    if state is not None and state.get("candidate_test") is not None:
        try:
            candidate_context = test_context(project)
            candidate_mode = candidate_context is not None
        except (StageContextError, ValueError) as exc:
            blockers.append(f"candidate_test 状态无效；隔离流程未知：{exc}")
    if not manifest_path.is_file():
        blockers.append("artifact_manifest.json 缺失；产物版本/失效状态未知")
    else:
        try:
            manifest = _read_json(manifest_path, default=None)
        except StageContextError as exc:
            manifest = None
            blockers.append(f"artifact_manifest.json 不可读；产物版本状态未知：{exc}")
        if not isinstance(manifest, dict) or not isinstance(manifest.get("artifacts"), list):
            blockers.append("artifact_manifest.json 结构无效；产物版本/失效状态未知")
            manifest = None
    artifacts = []
    if manifest is not None:
        for item in manifest["artifacts"]:
            if not isinstance(item, dict):
                blockers.append("manifest 含非对象产物记录")
                continue
            try:
                path = project_path(project, str(item.get("path_or_url") or ""))
                exists = path.is_file()
            except (OSError, ValueError) as exc:
                path = None
                exists = None
                blockers.append(f"产物路径无效，版本状态未知：{item.get('artifact_id', 'unknown')}: {exc}")
            checksum_ok = None
            if exists is True and item.get("checksum"):
                checksum_ok = digest(path) == item["checksum"]
            if item.get("status") == "current" and (exists is not True or checksum_ok is False):
                blockers.append(f"current 产物缺失或哈希漂移：{item.get('artifact_id', 'unknown')}")
            artifacts.append({"artifact_id": item.get("artifact_id"), "logical_id": item.get("logical_id"),
                "revision": item.get("revision"), "status": item.get("status"),
                "path": str(path) if path else None, "exists": exists, "checksum_ok": checksum_ok})
    active_change = "not_applicable_candidate_test" if candidate_mode else "unknown"
    if control_path.is_file():
        try:
            control = _read_json(control_path, default=None)
        except StageContextError as exc:
            control = None
            blockers.append(f"change_control.json 不可读；活动变更状态未知：{exc}")
        if (isinstance(control, dict) and control.get("schema_version") == "revision-control-1.0"
                and isinstance(control.get("history"), list)
                and (control.get("active_change") is None or isinstance(control.get("active_change"), dict))):
            active_change = control["active_change"]
        else:
            blockers.append("change_control.json 结构无效；活动变更状态未知")
    elif not candidate_mode:
        blockers.append("change_control.json 缺失；活动变更状态未知")
    transaction_rows = []
    tx_root = manage / "revision_transactions"
    if tx_root.exists() and not tx_root.is_dir():
        blockers.append("revision_transactions 不是目录；事务状态未知")
    elif tx_root.is_dir():
        for folder in sorted(tx_root.iterdir()):
            if not folder.is_dir():
                continue
            row = {"transaction_id": folder.name, "state": "unknown", "anomalies": []}
            try:
                transaction = _read_json(folder / "transaction.json", default=None)
                intent = _read_json(folder / "commit_intent.json", default=None)
                result = _read_json(folder / "result.json", default=None)
                if not isinstance(transaction, dict): row["anomalies"].append("transaction.json 缺失/无效")
                if intent is not None and not isinstance(intent, dict): row["anomalies"].append("commit_intent.json 无效")
                if isinstance(intent, dict) and not isinstance(result, dict): row["anomalies"].append("已有 commit intent 但无 result；需显式恢复")
                if isinstance(result, dict) and result.get("state") != "committed": row["anomalies"].append("事务结果不是 committed")
                if isinstance(result, dict) and result.get("state") == "committed" and not row["anomalies"]: row["state"] = "committed"
                elif isinstance(transaction, dict) and intent is None and result is None and not row["anomalies"]: row["state"] = "prepared_without_intent"
                else: row["state"] = "anomalous" if row["anomalies"] else "unknown"
            except (OSError, StageContextError, json.JSONDecodeError) as exc:
                row["anomalies"].append(f"事务记录不可读：{exc}")
            if row["anomalies"]:
                blockers.extend(f"事务 {folder.name}：{problem}" for problem in row["anomalies"])
            transaction_rows.append(row)
    repo = {"root": str(ROOT), "branch": None, "revision": None,
            "baseline_branch": "main", "baseline_revision": None, "working_tree_clean": None}
    try:
        branch = subprocess.run(["git", "-C", str(ROOT), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
        revision = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        baseline = subprocess.run(["git", "-C", str(ROOT), "merge-base", "HEAD", "main"], check=True, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
        repo.update(branch=branch or None, revision=revision or None, baseline_revision=baseline or None,
                    working_tree_clean=not bool(dirty.strip()))
    except (OSError, subprocess.CalledProcessError):
        blockers.append("代码仓库分支/当前 revision 无法读取；未知")
    next_stage = state.get("next_stage") if isinstance(state, dict) else None
    if isinstance(active_change, dict):
        blockers.append(f"活动变更案未完成：{active_change.get('change_id', 'unknown')} ({active_change.get('status', 'unknown')})")
        next_stage = None
    if not isinstance(next_stage, str) or not next_stage.strip():
        blockers.append("next_stage 为空；下一允许阶段未知")
        next_stage = None
    elif not (ROOT / "skills" / next_stage / "SKILL.md").is_file():
        blockers.append(f"next_stage 指向不存在的 Skill：{next_stage}")
        next_stage = None
    elif manifest is not None and state is not None:
        try:
            _confirmed_project_inputs(project, state)
        except (StageContextError, ValueError) as exc:
            blockers.append(f"上游确认/版本/依赖未通过：{exc}")
            next_stage = None
    else:
        next_stage = None
    if blockers:
        next_stage = None
    production_blockers = list(blockers)
    production_ok = not blockers and next_stage is not None
    candidate_test_blockers: list[str] = []
    if candidate_mode:
        # Candidate handoff deliberately accepts an explicit --to-skill when
        # next_stage is empty. Its isolated inputs, not production approvals or
        # revision-change records, are the authority for this mode.
        ignored_for_candidate = ("change_control.json 缺失；活动变更状态未知",
                                 "next_stage 为空；下一允许阶段未知")
        candidate_test_blockers.extend(message for message in blockers
                                       if not message.startswith(ignored_for_candidate))
        if not state or not isinstance(state.get("current_stage"), str) or not state["current_stage"].strip():
            candidate_test_blockers.append("候选测试 current_stage 缺失；不能生成隔离交接")
        try:
            test_inputs(project)
        except (StageContextError, ValueError, OSError) as exc:
            candidate_test_blockers.append(f"隔离候选输入无效：{exc}")
        if candidate_context is None:
            candidate_test_blockers.append("候选测试运行身份未知")
        candidate_test_ok = not candidate_test_blockers
        return {"read_only": True, "mode": "candidate_test", "project": str(project), "repo": repo,
            "current_stage": state.get("current_stage") if isinstance(state, dict) else None,
            "current_artifacts": state.get("current_artifacts") if isinstance(state, dict) else None,
            "artifacts": artifacts, "stale_artifacts": [a["artifact_id"] for a in artifacts if a["status"] == "stale"],
            "active_change": active_change, "transactions": transaction_rows,
            "next_allowed_stage": next_stage,
            "next_stage_note": ("候选测试隔离有效；next_stage 为空时可显式选择 --to-skill，候选流程不放行正式生产。"
                                if candidate_test_ok and next_stage is None else
                                "候选测试仅限隔离输入；正式生产仍被阻止。"),
            "production_allowed": False, "production_ok": production_ok,
            "production_blockers": production_blockers,
            "candidate_test_allowed": candidate_test_ok,
            "candidate_test": {"run_id": candidate_context.get("run_id") if candidate_context else None,
                               "input_artifact_ids": candidate_context.get("input_artifact_ids") if candidate_context else None},
            "blockers": candidate_test_blockers, "ok": candidate_test_ok}
    return {"read_only": True, "mode": "production", "project": str(project), "repo": repo,
        "current_stage": state.get("current_stage") if isinstance(state, dict) else None,
        "current_artifacts": state.get("current_artifacts") if isinstance(state, dict) else None,
        "artifacts": artifacts, "stale_artifacts": [a["artifact_id"] for a in artifacts if a["status"] == "stale"],
        "active_change": active_change, "transactions": transaction_rows,
        "next_allowed_stage": next_stage,
        "next_stage_note": "上游版本确认后仍需该 Skill 自身输入门禁；状态命令不会启动阶段。" if next_stage else "阻塞或状态未知，不猜下一阶段。",
        "production_allowed": production_ok, "production_ok": production_ok,
        "blockers": blockers, "ok": production_ok}


def _confirmed_project_inputs(project: Path, state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Resolve the explicitly selected confirmed input versions from their sources."""
    artifact_ids = state.get("current_artifacts") or []
    if not isinstance(artifact_ids, list) or not artifact_ids or any(not isinstance(item, str) or not item for item in artifact_ids):
        raise StageContextError("项目没有可交接的当前有效产物 ID")
    try:
        manifest = load_manifest(project)
    except (OSError, ValueError) as exc:
        raise StageContextError("artifact_manifest.json 不存在或无效：" + str(exc)) from exc
    indexed = {item.get("artifact_id"): item for item in manifest["artifacts"] if isinstance(item, dict)}
    inputs: list[dict[str, Any]] = []
    for artifact_id in artifact_ids:
        item = indexed.get(artifact_id)
        if not isinstance(item, dict) or item.get("status") != "current" or item.get("user_approved") is not True:
            raise StageContextError(f"交接产物不是已确认 current：{artifact_id}")
        if not artifact_is_confirmed(item, state, project):
            raise StageContextError(f"当前有效产物缺少用户确认：{artifact_id}")
        path = project_path(project, str(item.get("path_or_url") or ""))
        if not path.is_file():
            raise StageContextError(f"交接产物文件不存在：{artifact_id}: {path}")
        if not isinstance(item.get("checksum"), str) or not re.fullmatch(r"[a-f0-9]{64}", item["checksum"]):
            raise StageContextError(f"交接产物缺少有效确认 hash：{artifact_id}")
        if digest(path) != item["checksum"]:
            raise StageContextError(f"交接产物内容与确认版本不一致：{artifact_id}")
        try:
            validate_current_dependencies(item, manifest, state, project)
            if item.get("logical_id") in {"narrative.package", "storyboard.package", "video.prompt"}:
                if not validate_candidate_submission(project, item, manifest):
                    raise ValueError("旧稿件/未识别结构仅保留历史读取，不能作为正式交接输入")
        except ValueError as exc:
            raise StageContextError(str(exc)) from exc
        inputs.append(item)
    return state, inputs


def _current_handoff_text(project: Path, target_skill: str) -> str:
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
    records = re.findall(pattern, text)
    _, inputs = _selected_inputs(project, _state(project))
    if bool(test_context(project)) != ("execution_mode: candidate_test" in text):
        raise StageContextError("交接卡运行模式与项目不一致；重新 handoff")
    expected = [tuple(str(item.get(field, "")) for field in fields[:-1])
                + (str(project_path(project, item["path_or_url"])),) for item in inputs]
    if test_context(project):
        hashes = re.findall(r"(?m)^  - candidate_sha256：`([a-f0-9]{64})`$", text)
        if hashes != [digest(project_path(project, item["path_or_url"])) for item in inputs]:
            raise StageContextError("候选交接来源字节已改变；重新核对并 handoff")
    if sorted(records) != sorted(expected):
        raise StageContextError("已有交接卡的产物、版本或路径已过期；先按当前确认输入重新运行 handoff。")
    return text


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


def _candidate_manifest_matches(block: Any, record: dict[str, Any]) -> bool:
    return _candidate_record(block) == record


def _reading_manifest(project: Path) -> dict[str, Any] | None:
    basis = _read_json(_task_basis_path(project), default={})
    value = basis.get("reading_manifest") if isinstance(basis, dict) else None
    return value if isinstance(value, dict) else None


def _evidence_current(project: Path, requested_skill: str | None = None) -> tuple[dict[str, Any], dict[str, Any], Path] | None:
    manifest = _reading_manifest(project)
    try:
        evidence = _read_json(project / CONTEXT_DIR / "method_reading_evidence.json", default=None)
    except StageContextError:
        return None  # Broken optional reading state is unread, not a preparation failure.
    packet = project / CONTEXT_DIR / "method_reading_packet.md"
    if not isinstance(manifest, dict) or not isinstance(evidence, dict) or not packet.is_file():
        return None
    key = _read_json(_task_basis_path(project), default={}).get('reading_reuse_key')
    if key and evidence.get('reading_reuse_key') != key:
        return None
    keys = ("skill", "project_path", "actor_id", "task_id", "vault_root", "test_only_proposed_method_ids", "methods", "selected")
    if requested_skill and manifest.get("skill") != requested_skill:
        return None
    if any(evidence.get(key) != manifest.get(key) for key in keys):
        return None
    if not all(isinstance(manifest.get(key), str) and manifest[key] for key in ("skill", "project_path", "actor_id", "task_id", "vault_root")):
        return None
    packet_bytes = packet.read_bytes()
    if evidence.get("packet_sha256") != hashlib.sha256(packet_bytes).hexdigest():
        return None
    base_length = manifest.get("method_packet_bytes")
    if not isinstance(base_length, int) or base_length < 0 or hashlib.sha256(packet_bytes[:base_length]).hexdigest() != manifest.get("method_packet_sha256"):
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
    return project / CONTEXT_DIR / 'stage_reading_cache' / (skill + '.json')


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
    write_text_if_changed(project / CONTEXT_DIR / 'method_reading_packet.md', packet)
    write_text_if_changed(project / CONTEXT_DIR / 'method_reading_evidence.json', json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    return _evidence_current(project, skill) is not None


def _identity_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _semantic_request(request: dict[str, Any]) -> dict[str, Any]:
    # History is explicitly non-operative. Do not discard arbitrary nested fields.
    return {key: value for key, value in request.items() if key not in {"history", "updated_at", "created_at"}}


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
    collect(inputs)
    return sorted(paths)


def _input_snapshot(project: Path, inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Bound current validity to explicit sources and state-selected upstream inputs."""
    sources = _explicit_source_paths(project, inputs)
    manifest = _read_json(project / "00_项目管理/artifact_manifest.json", default={})
    if not isinstance(manifest, dict) or not isinstance(manifest.get("artifacts", []), list):
        raise StageContextError("artifact_manifest.json 无效")
    testing = test_context(project)
    selected = list(testing["input_artifact_ids"] if testing else (state.get("current_artifacts") or []))
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
            if value not in selected:
                selected.append(value)
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
        if str(path) in source_names and entry.get("artifact_id") not in selected:
            selected.append(entry.get("artifact_id"))
    records = []
    if selected:
        if testing:
            records = test_inputs(project, selected)
        else:
            _, records = _confirmed_project_inputs(project, {**state, "current_artifacts": selected})
    selected_set = set(selected)
    def relevant(receipt: Any) -> bool:
        if not isinstance(receipt, dict):
            return True
        ids = receipt.get("artifact_ids", [])
        if not isinstance(ids, list):
            return True  # Unknown scope cannot be discarded as irrelevant.
        ids = [*ids, receipt.get("artifact_id")]
        referenced = {item for item in ids if isinstance(item, str) and item}
        return not referenced or bool(referenced & selected_set)
    return {
        "sources": [fingerprint(path) for path in sources],
        "selected_artifact_ids": list(testing["input_artifact_ids"] if testing else (state.get("current_artifacts") or [])),
        **({"candidate_test": testing} if testing else {}),
        "artifacts": [{"record": {k: v for k, v in item.items() if k not in {"updated_at", "created_at"}},
                       "file": fingerprint(project_path(project, item["path_or_url"])),
                       "confirmation_byte_binding": "recorded" if item.get("checksum") else "legacy_unhashed"}
                      for item in records],
        "authority": {"user_confirmations": [item for item in state.get("user_confirmations", []) if relevant(item)],
                      "accepted_issues": state.get("accepted_issues", []),
                      "project_status": state.get("project_status"), "finalization": state.get("finalization")},
    }


def _local_standard_documents(skill_dir: Path, request: dict[str, Any] | None = None) -> list[Path]:
    """Freeze applicable contracts plus exact linked dependencies, never a skip list.

    Unknown conditions retain the historical conservative local set. The asset
    scopes already used by method resolution are the only category exclusions.
    Explicit local_references can add dependencies, never remove required ones.
    """
    request = request or {}
    excluded: set[Path] = set()
    if skill_dir.name == "asset-image-assistant":
        # The Skill explicitly replaces prepare with bounded Web reading in
        # both Web modes. Those route-only modules cannot govern this entry.
        excluded.update((skill_dir / "modules" / name).resolve() for name in
                        ("library-mode.md", "project-web-mode.md", "web-input-contract.md"))
        scopes = request.get("asset_method_scopes", {})
        if not isinstance(scopes, dict) or any(key not in {"style", "character", "scene", "object"}
                                             or type(value) is not bool for key, value in scopes.items()):
            raise StageContextError("asset_method_scopes 必须按现有类别填写布尔值。")
        if any(scopes.values()):
            conditional = {"style": ["01-style-master-guide.md"],
                           "character": ["02-character-master-guide.md", "02a-body-character-master-guide.md"],
                           "scene": ["03-scene-master-guide.md"],
                           "object": ["04-prop-product-master-guide.md"]}
            excluded.update((skill_dir / "references" / name).resolve()
                            for scope, names in conditional.items() if not scopes.get(scope, False)
                            for name in names)
    local = [skill_dir / "SKILL.md", *sorted((skill_dir / "modules").glob("*.md")),
             *sorted((skill_dir / "references").glob("*.md"))]
    local = [path.resolve() for path in local if path.resolve() not in excluded]
    extra = request.get("local_references", [])
    if not isinstance(extra, list):
        raise StageContextError("local_references 必须是本轮实际采用的仓库相对规范路径数组。")
    for reference in extra:
        if not isinstance(reference, str) or not reference or Path(reference).is_absolute() or PureWindowsPath(reference).drive:
            raise StageContextError("local_references 必须使用仓库相对路径。")
        path = (ROOT / reference).resolve()
        if (not path.is_relative_to(ROOT.resolve()) or path.suffix not in {".md", ".json"}
                or not path.is_file() or not path.stat().st_size):
            raise StageContextError(f"本轮本地规范不存在、为空或越出仓库：{reference}")
        if path in excluded:
            raise StageContextError(f"本地规范与当前普通 prepare 入口或 asset_method_scopes 不一致：{reference}；先核对实际入口和类别。")
        local.append(path)
    shared_root = (ROOT / "skills/_shared").resolve()
    # These are the common redlines named collectively by Skill entries; their
    # presence must not depend on whether prose spells each link separately.
    shared = sorted(shared_root.glob("*.md"))
    seen: set[Path] = set([*local, *shared])
    visited: set[Path] = set()
    pending = [(ROOT / "PROJECT_RULES.md").resolve(), *local, *shared]
    while pending:
        document = pending.pop()
        if document in visited:
            continue
        visited.add(document)
        if document.suffix != ".md":
            continue
        text = document.read_text(encoding="utf-8")
        references = re.findall(r"(?:skills/|\.\./)*_shared/[A-Za-z0-9_-]+\.md", text)
        if document.parent == shared_root:
            references.extend(re.findall(r"\]\(([A-Za-z0-9_-]+\.md)(?=[#)])", text))
        dependencies = []
        for reference in references:
            path = shared_root / Path(reference).name
            if not path.is_file():
                raise StageContextError(f"必需共享规范缺失：{path}")
            dependencies.append(path)
        # Root-wide links describe other stages, not this task's contracts.
        # Follow explicit module/reference links from the selected contracts.
        if document != (ROOT / "PROJECT_RULES.md").resolve():
            refs = re.findall(r"(?:`|\]\()((?:\.\./|\./|skills/|references/|modules/|templates/|schemas/)[^`\s)#]+\.(?:md|json))", text)
            for reference in refs:
                choices = [(document.parent / reference).resolve(),
                           (skill_dir / reference).resolve(), (ROOT / reference).resolve()]
                target = next((path for path in choices if path.is_relative_to(ROOT.resolve()) and path.is_file()), None)
                if (target is None and not any(path in excluded for path in choices)
                        and {"references", "modules", "templates", "schemas"}.intersection(Path(reference).parts)):
                    raise StageContextError(f"明确引用的本地规范缺失：{document}: {reference}")
                if target and target not in excluded and (target.parent == shared_root
                        or "references" in target.parts or "modules" in target.parts or "templates" in target.parts or "schemas" in target.parts):
                    dependencies.append(target)
        for path in dependencies:
            if path not in seen:
                seen.add(path)
                pending.append(path)
    return sorted(seen)


def _review_identity(basis: dict[str, Any], adopted: list[dict[str, Any]] | None = None) -> str:
    # Rebuild the substantive projection, never trust a stored hash or include
    # actor IDs, reading cache/catalog metadata, request location or packet bytes.
    fields = ("schema_version", "skill", "objective", "deliverables", "non_goals", "input_versions",
              "scope", "delivery_contract", "standards", "review_acceptance", "revision", "task_request", "input_snapshot")
    return _identity_hash({**{key: basis[key] for key in fields if key in basis}, "adopted_knowledge": adopted or []})


def _reading_reuse_key(project, basis, request, state, handoff):
    manifest = basis["reading_manifest"]
    identity = {key: manifest.get(key) for key in ("skill", "project_path", "actor_id", "task_id", "vault_root",
                                                 "test_only_proposed_method_ids", "methods", "selected", "method_packet_sha256")}
    return _identity_hash({"reading": identity, "task": _review_identity(basis)})


def _validate_current_inputs(project: Path, basis: dict[str, Any]) -> dict[str, Any]:
    """Recheck known dependencies at consumption time; never grants execution authority."""
    if basis.get("reading_manifest", {}).get("project_path") != str(project):
        raise StageContextError("任务依据属于另一项目；请在当前项目重新 prepare。")
    snapshot = basis.get("input_snapshot")
    if not isinstance(snapshot, dict):
        raise StageContextError("任务依据缺少当前输入版本证据；请重新 prepare。")
    request_path = basis.get("request_path")
    if request_path:
        current_request = _read_json(Path(request_path), default=None)
        if not isinstance(current_request, dict) or _semantic_request(current_request) != basis.get("task_request"):
            raise StageContextError("当前请求缺失或实质范围已改变；请重新 prepare。")
    current = _input_snapshot(project, basis["input_versions"], _state(project))
    if current != snapshot:
        raise StageContextError("当前来源、选定产物或确认依据已改变；请重新 prepare 并核对变更。")
    _current_handoff_text(project, basis["skill"])
    standards = basis["standards"]
    documents = [*standards.get("local_documents", []), standards.get("review_profile", {}), basis.get("knowledge_profile", {})]
    if standards.get("root_rules_sha256"):
        documents.append({"path": standards["root_rules"], "sha256": standards["root_rules_sha256"]})
    documents.extend({"path": str(Path(basis["reading_manifest"]["vault_root"]) / item["path"]), "sha256": item["sha256"]}
                     for item in standards.get("professional_methods", []))
    for item in documents:
        if not item.get("path") or not item.get("sha256"):
            raise StageContextError("任务规范缺少版本证据；请重新 prepare。")
        path = Path(item["path"])
        if not path.is_file() or digest(path) != item["sha256"]:
            raise StageContextError(f"当前方法、绑定或规范已改变：{path}；请重新 prepare。")
    vault = Path(basis["reading_manifest"]["vault_root"])
    constraints = basis["reading_manifest"].get("applicable_confirmed")
    if not isinstance(constraints, list):
        raise StageContextError("任务依据缺少预算外适用 confirmed 约束；请重新 prepare，不能沿用旧选择结果。")
    records = list(basis["reading_manifest"].get("selected", [])) + constraints
    evidence = _evidence_current(project, basis["skill"])
    if evidence:
        records.extend(evidence[1].get("adopted_candidates", []))
    if records:
        tombstones = _tombstone_fingerprints(vault, load_policy(ROOT))
        for item in records:
            block = _read_candidate_exact(vault, item)
            if block.fingerprint in tombstones:
                raise StageContextError(f"当前知识已被禁用：{block.id}；请重新 prepare。")
    return {"current_input_validation": "matched_at_check", "execution_authorization": "not_granted",
            "legacy_unhashed_inputs": [item["record"]["artifact_id"] for item in current["artifacts"]
                                       if item["confirmation_byte_binding"] == "legacy_unhashed"]}


def _read_candidate_exact(vault: Path, record: dict[str, Any]) -> Any:
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
    if len(matches) != 1 or not _candidate_manifest_matches(matches[0], record):
        raise StageContextError(f"候选知识版本、路径、状态或范围已变更：{record.get('id')}；请重新 prepare。")
    return matches[0]


def _check_run_budget(args: argparse.Namespace) -> dict[str, Any] | None:
    logs = getattr(args, "usage_log", None)
    turn = getattr(args, "usage_turn_id", None)
    boundaries = getattr(args, "usage_boundaries_json", None)
    limits = {key: getattr(args, key, None) for key in ("max_input", "max_total", "max_requests")}
    if not logs and not turn and not boundaries and not any(value is not None for value in limits.values()):
        return None
    if not any(value is not None for value in limits.values()):
        raise StageContextError("计量门禁须至少给出一个预算。")
    if boundaries:
        if logs or turn:
            raise StageContextError("--usage-boundaries-json 不能与单轮用量参数同用。")
        report = measure_boundaries(_read_json(Path(boundaries), default=None))
    elif logs and turn:
        report = measure([Path(p) for p in logs], turn)
    else:
        raise StageContextError("计量门禁须给出单轮日志+turn，或显式边界集合。")
    budget = check_budget(report, **limits)
    if not budget["ok"]:
        raise StageContextError(f"执行预算停止：{'; '.join(budget['stop_reasons'])}；保留当前产物，报告已知用量与下一步。")
    return {"requests": report["requests"], "totals": report["totals"], "budget": budget}


def _task_basis_path(project: Path) -> Path:
    """The one per-task source for scope and standards used by work and review."""
    return project / CONTEXT_DIR / "task_basis.json"


def _load_task_basis(project: Path, skill: str) -> tuple[Path, dict[str, Any]]:
    path = _task_basis_path(project)
    basis = _read_json(path, default=None)
    if not isinstance(basis, dict) or basis.get("schema_version") != "1.0" or basis.get("skill") != skill:
        raise StageContextError(f"缺少与当前 Skill 匹配的任务依据：{path}；先运行 prepare，不能由审核包另选标准。")
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


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    _require_preparation_skill(args.skill)
    usage = _check_run_budget(args)
    started = time.perf_counter()
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise StageContextError(f"项目目录不存在：{project}")
    skill_dir = ROOT / "skills" / args.skill
    profile_path = skill_dir / "knowledge-profile.json"
    test_method_ids = set(getattr(args, "test_proposed_method", None) or [])
    test_profile_path = getattr(args, "test_profile_json", None)
    if test_profile_path and not test_method_ids:
        raise StageContextError("--test-profile-json 只能与 --test-proposed-method 一起用于隔离测试。")
    if test_profile_path:
        profile_path = Path(test_profile_path).expanduser().resolve()
    profile = _read_json(profile_path, default=None)
    if not isinstance(profile, dict) or profile.get("skill") != args.skill:
        raise StageContextError(f"Skill 知识 profile 无效：{profile_path}")
    request = {}
    if args.request_json:
        request_path = Path(args.request_json).resolve()
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StageContextError(f"请求文件不存在：{request_path}；请提供明确请求来源，不从项目入口或待审候选推断。") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise StageContextError(f"无法读取请求 JSON：{request_path}: {exc}") from exc
    if not isinstance(request, dict):
        raise StageContextError("request JSON 必须是对象")
    testing = _test_request(args, project)
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
    raw_state = _state(project)
    summary = _state_summary(raw_state)
    handoff_text = _current_handoff_text(project, args.skill)
    revision = _revision_summary(
        project,
        getattr(args, "change_id", None),
        getattr(args, "target_logical_id", None),
    )
    policy = load_policy(ROOT)
    vault = _vault_root(args.vault)
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
    bundle = render_knowledge_bundle(args.skill, result)
    candidate_index = render_knowledge_candidates(result)
    trial_bundle = render_candidate_trial_bundle(args.skill, result)
    method_lines = ["## 必读专业方法", ""]
    if methods.required:
        method_lines.append("- 以下全文已写入 `method_reading_packet.md`；生产者必须逐份完整读取后登记读取证据。")
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
    input_versions = {
        key: value for key, value in request.items()
        if key.startswith("source_") or key in {"inputs", "material_output_ids"}
    }
    review_profile_path, review_profile = _load_review_profile(args.skill)
    request_checks = request.get("review_checks") or []
    if not isinstance(request_checks, list) or any(not isinstance(item, str) or not item.strip() for item in request_checks):
        raise StageContextError("request.review_checks 必须是非空文字检查项数组。")
    manual_checks = [{"id": f"MANUAL-{index:03d}", "text": text.strip()} for index, text in enumerate(request_checks, start=1)]
    methods_records = [{"id": item.id, "path": item.vault_path, "version": item.version, "sha256": item.checksum, "promotion_basis": item.promotion_basis} for item in methods.required]
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
    local_documents = _local_standard_documents(skill_dir, request)
    task_basis = {
        "schema_version": "1.0",
        "skill": args.skill,
        "objective": objective,
        "deliverables": request.get("deliverables") or [objective],
        "non_goals": request.get("non_goals") or [],
        "input_versions": input_versions,
        "input_snapshot": _input_snapshot(project, input_versions, raw_state),
        "task_request": _semantic_request(request),
        "request_path": str(Path(args.request_json).resolve()) if args.request_json else None,
        "knowledge_profile": fingerprint(profile_path),
        "scope": {key: request[key] for key in ("task_kind", "scope", "source_shot_ids", "generation_authorized", "model_selected") if key in request},
        "delivery_contract": delivery_contract,
        "standards": {
            "root_rules": str(ROOT / "PROJECT_RULES.md"),
            "root_rules_sha256": digest(ROOT / "PROJECT_RULES.md"),
            "skill_entry": str(skill_dir / "SKILL.md"),
            "professional_methods": [{"id": item.id, "path": item.vault_path, "version": item.version, "sha256": item.checksum, "promotion_basis": item.promotion_basis} for item in methods.required],
            "selected_knowledge": [_candidate_record(item) for item in result.selected],
            "applicable_confirmed": [_candidate_record(item) for item in result.applicable_confirmed],
            "review_profile": {"path": str(review_profile_path), "sha256": hashlib.sha256(review_profile_path.read_bytes()).hexdigest(), "schema_version": review_profile["schema_version"]},
            "local_documents": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in local_documents],
        },
        "review_acceptance": {"delivery_object": delivery_contract["delivery_object"], "delivery_contract": delivery_contract, "production": {"required_checks": _applicable_checks(review_profile["production_artifact_checks"], delivery_contract), "manual_checks": manual_checks}, "skill_contract": {"required_checks": review_profile["skill_contract_checks"], "manual_checks": manual_checks}},
        "reading_manifest": reading_manifest,
        "candidate_trial": trial_record,
        "revision": revision,
    }
    if testing:
        task_basis.update(test_metadata())
    task_basis['reading_reuse_key'] = _reading_reuse_key(project, task_basis, request, raw_state, handoff_text)
    content = "\n".join([
        "# 阶段启动包", "",
        f"- Skill：`{args.skill}`",
        f"- 目标：{objective}",
        *(["- execution_mode: candidate_test；review_status: unreviewed；production_blocked: true。仅候选文本/结构试跑，不生成、不发布、不正式采用。"] if testing else []),
        f"- 本轮唯一任务依据：`{_task_basis_path(project)}`（机器完整记录；通过 ensure-prepared --show-task-basis 读取目标、输入与冻结验收项，不整份打印后台目录）",
        f"- 交付意图：`{json.dumps(delivery_contract, ensure_ascii=False)}`；按本轮交付对象及适用检查项审核。",
        f"- 根规则：`{ROOT / 'PROJECT_RULES.md'}`",
        f"- Skill 入口：`{skill_dir / 'SKILL.md'}`",
        "- 执行分工：遵守根规则的 Skill 执行分工；当前主 agent 直接读取、prepare、产出并自检，只有用户要求或明确独立并行收益时委派。",
        "- 上下文边界：按本次启动包和准确源证据执行；返修保留有效读取并核对当前版本，不因换修订号新建执行者。",
        "- prepare 只生成文件，不清空宿主对话。读取规则见共享 knowledge-access.md；计量和停止条件见 docs/development/token-budget.md。",
        "## 本轮范围", "",
        "- 交付对象、非目标、输入版本、范围字段和适用标准仅维护在上列 `task_basis.json`；本启动包不复制第二份范围记录。", "",
        "## 当前项目事实", "", "```json",
        json.dumps(_state_reading_view(summary), ensure_ascii=False, indent=2), "```", "",
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
    output = project / CONTEXT_DIR / "stage_startup.md"
    report = validate_text("startup", output, content)
    _save_reading_cache(project)
    write_text_if_changed(output, content)
    context = project / CONTEXT_DIR
    write_text_if_changed(_task_basis_path(project), json.dumps(task_basis, ensure_ascii=False, indent=2) + "\n")
    if methods.required or result.candidates:
        restored = False
        if _evidence_current(project) is None:
            restored = _restore_reading_cache(project, args.skill, task_basis['reading_reuse_key'])
        report['stage_reading_restored'] = restored
        if _evidence_current(project) is None:
            write_text_if_changed(context / "method_reading_packet.md", base_packet)
            stale = context / "method_reading_evidence.json"
            if stale.is_file(): stale.unlink()
        index_path = context / "knowledge_candidates.md"
        if result.candidates: write_text_if_changed(index_path, candidate_index)
        elif index_path.is_file(): index_path.unlink()
    else:
        for name in ("method_reading_packet.md", "method_reading_evidence.json", "knowledge_candidates.md"):
            stale = context / name
            if stale.is_file(): stale.unlink()
    for name in ("method_reading_manifest.json", "candidate_reading_manifest.json", "candidate_reading_packet.md", "candidate_reading_evidence.json"):
        stale = context / name
        if stale.is_file(): stale.unlink()
    trial_path = context / "candidate_trial_packet.md"
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
    return report


def handoff(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise StageContextError(f"项目目录不存在：{project}")
    target_skill = str(args.to_skill or "").strip()
    if not target_skill:
        raise StageContextError("handoff 必须提供非空 --to-skill")
    testing = _test_request(args, project)
    if testing:
        state, inputs = _selected_inputs(project, _state(project))
        if state.get("next_stage") and state["next_stage"] != target_skill:
            raise StageContextError("目标 Skill 与项目下一阶段不一致")
    else:
        state, inputs = _confirmed_handoff_inputs(project, target_skill)
    current_stage = str(state.get("current_stage") or "")
    if not current_stage:
        raise StageContextError("项目当前阶段为空，不能生成交接卡")
    lines = [
        "# 下一阶段交接卡", "",
        f"- 来源阶段：`{current_stage}`",
        f"- 目标 Skill：`{target_skill}`",
        ("- execution_mode: candidate_test；review_status: unreviewed；production_blocked: true。只试写，不生成、不发布、不采用。" if testing else "- 交接状态：上游当前输入的用户确认与依赖已核对；本卡不证明语义审核通过，不启动、路由或批准下一阶段。"), "",
        ("## 本轮未确认候选输入" if testing else "## 当前有效上游产物"), "",
        "- 语义自查：本卡未读取结论；执行者须核当前修订的实际自查、未决问题及适用的独立审核，不能从用户确认推定通过。", "",
    ]
    for item in inputs:
        lines.extend([
            f"- artifact_id：`{item['artifact_id']}`",
            f"  - logical_id：`{item.get('logical_id', '')}`",
            f"  - revision：`{item.get('revision', '')}`",
            f"  - type：`{item.get('type', '')}`",
            *([f"  - business_status：`{item['business_status']}`；return_upstream：{json.dumps(item['return_upstream'], ensure_ascii=False)}"]
              if testing and "business_status" in item else []),
            f"  - path：`{project_path(project, item['path_or_url'])}`",
            *([f"  - candidate_sha256：`{digest(project_path(project, item['path_or_url']))}`"] if testing else []),
        ])
    startup_requirement = (
        "- `voiceover-to-video-script` 直接按其 Skill 的原始资料读取例外建立本段输入范围；不生成或校验 stage_startup.md。"
        if target_skill == "voiceover-to-video-script" else
        "- 用户已确认上游且明确要求继续时，当前主 agent 生成并校验下一 Skill 的 stage_startup.md，在该 Skill 边界内直接执行和自检；不固定新增生产子 agent。"
    )
    lines.extend([
        "", "## 接收约束", "",
        ("- 仅按独立测试授权继续候选试写；保留所有未核对事实和素材阻塞。视频 Skill 仍直接读取，不重建准备包。" if testing else startup_requirement),
        "- 编导交接须实际读准确正文、核来源和本轮自查结论；关键发现及未核范围复用现有任务记录，程序 ok 不代表叙事充分。",
        "- 默认在当前对话完成制作、自查和修正；仅用户明确要求独立审核或明确委派时才交接，并核对执行仓库与当前规则。",
        ("- 只消费以上未确认候选及准确稳定 ID；不得读取旧案例答案，不得写入确认记录。" if testing else "- 只消费以上已确认产物及其稳定 ID；不得读取旧生产对话或未列入启动包的历史内容。"),
        "- 本交接不等于下一阶段已启动，也不替代下一阶段的输入门禁。", "",
    ])
    output = _handoff_path(project)
    content = "\n".join(lines)
    report = validate_text("handoff", output, content)
    write_text_if_changed(output, content)
    report["from_stage"] = current_stage
    report["to_skill"] = target_skill
    report["artifact_ids"] = [item["artifact_id"] for item in inputs]
    if testing:
        report.update(test_metadata())
    return report


def review_pack(args: argparse.Namespace) -> dict[str, Any]:
    _require_preparation_skill(args.skill)
    project = Path(args.project).expanduser().resolve()
    require_production(project)
    review_kind = getattr(args, "review_kind", "production")
    if review_kind not in {"production", "skill_contract"}:
        raise StageContextError(f"未知审核类型：{review_kind}")
    artifacts = [_review_target(project, Path(item), review_kind) for item in args.artifact]
    external_evidence = [Path(item).expanduser().resolve() for item in (getattr(args, "evidence", None) or [])]
    external_evidence = list(dict.fromkeys(external_evidence))
    manifest_path = project / "00_项目管理" / "artifact_manifest.json"
    state_path = project / "00_项目管理" / "project_state.json"
    missing = [str(item) for item in artifacts if not item.is_file()]
    if missing:
        raise StageContextError(f"被审产物不存在：{missing}")
    missing_evidence = [str(item) for item in external_evidence if not item.is_file()]
    if missing_evidence:
        raise StageContextError(f"外部审核证据不存在或不是文件：{missing_evidence}")
    basis_path, task_basis = _load_task_basis(project, args.skill)
    input_validation = _validate_current_inputs(project, task_basis)
    delivery_contract = task_basis.get("delivery_contract")
    if not isinstance(delivery_contract, dict):
        raise StageContextError(f"任务依据缺少交付意图：{basis_path}；先重新 prepare。")
    usage = _check_run_budget(args)
    profile_path, profile = _load_review_profile(args.skill)
    profile_standard = task_basis["standards"].get("review_profile", {})
    if profile_standard.get("sha256") != hashlib.sha256(profile_path.read_bytes()).hexdigest():
        raise StageContextError("审核 profile 已在 prepare 后变化；先显式更新任务依据，不能用 live profile 重选标准。")
    acceptance = task_basis.get("review_acceptance", {}).get(review_kind)
    if not isinstance(acceptance, dict) or not isinstance(acceptance.get("required_checks"), list) or not isinstance(acceptance.get("manual_checks"), list):
        raise StageContextError("任务依据缺少冻结审核清单；请重新 prepare。")
    required_checks = acceptance["required_checks"]
    manual_texts = [item.strip() for item in (getattr(args, "check", None) or []) if item.strip()]
    manual_checks = acceptance["manual_checks"]
    if manual_texts and manual_texts != [item["text"] for item in manual_checks]:
        raise StageContextError("--check 不能在审核时另选标准；请在 prepare 的 request.review_checks 中冻结后重跑。")
    checks = [*required_checks, *manual_checks]
    revision = _revision_summary(
        project,
        getattr(args, "change_id", None),
        getattr(args, "target_logical_id", None),
    )
    review_mode = getattr(args, "review_mode", "initial")
    if review_mode not in {"initial", "revision"}:
        raise StageContextError(f"未知审核模式：{review_mode}")
    previous = getattr(args, "previous_review", None)
    response = getattr(args, "producer_response", None)
    baselines = getattr(args, "baseline", None) or []
    # Supplying the complete historical archival inputs remains a supported
    # invocation. Partial archival arguments must not silently downgrade.
    formal = bool(getattr(args, "formal_archive", False) or response or baselines)
    delta = None
    if review_mode == "revision":
        if not previous:
            raise StageContextError("revision 必须提供 --previous-review；保留上轮未决发现，不能把遗漏当已解决。")
        try:
            if formal:
                if not response or not baselines:
                    raise ValueError("正式留档复审必须提供 --previous-review、--producer-response 和 --baseline。")
                delta, manual_checks = revision_delta(
                    Path(previous).expanduser().resolve(), [Path(p).expanduser().resolve() for p in baselines],
                    artifacts, _producer_response_value(response), args.skill, required_checks, manual_checks, external_evidence,
                )
            else:
                delta = lightweight_revision(
                    Path(previous).expanduser().resolve(),
                    _read_json(project / CONTEXT_DIR / "review_scope.json", default={}), artifacts,
                    args.skill, review_kind, required_checks, manual_checks,
                    getattr(args, "change_summary", None) or [], external_evidence,
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise StageContextError(f"复审输入无效：{exc}") from exc
        checks = [*required_checks, *manual_checks]
    reading = _evidence_current(project, args.skill)
    adopted_knowledge = [{**item, **({"adoption_scope": reading[1]["candidate_scopes"][item["id"]]}
                          if item["id"] in reading[1].get("candidate_scopes", {}) else {})}
                         for item in reading[1].get("adopted_candidates", [])] if reading else []
    scope = {"skill": args.skill, "review_kind": review_kind, "task_basis_path": str(basis_path),
             "task_basis_sha256": hashlib.sha256(basis_path.read_bytes()).hexdigest(),
             "task_basis_identity": _review_identity(task_basis, adopted_knowledge),
             "artifacts": [fingerprint(p) for p in artifacts],
             "direct_evidence": [fingerprint(p) for p in external_evidence]}
    if formal:
        scope.update(input_issue_ids=[i["id"] for i in delta["issue_updates"]] if delta else [],
                     issue_id_high_watermark=delta["issue_id_high_watermark"] if delta else 0)
    elif delta:
        scope["input_findings"] = delta["unresolved_findings"]
    if delta is not None:
        identity_key = "task_basis_identity" if "task_basis_identity" in delta else "task_basis_sha256"
        if delta["review_kind"] != review_kind or delta[identity_key] != scope[identity_key]:
            raise StageContextError("任务依据或审核类型已改变；这不是普通复审，先显式变更任务依据并重新首审。")
    if delta is not None and formal and any(change['after_sha256'] != item['sha256']
                                           for change, item in zip(delta['actual_diff'], scope['artifacts'])):
        raise StageContextError('候选在差异计算与审核范围登记之间发生变化；重新生成审核包，不混用版本。')
    if delta is not None and not formal:
        if delta["current_artifacts"] != scope["artifacts"]:
            raise StageContextError("候选在轻复审打包中变化；重新生成，不混用版本。")
    if delta is not None and delta["current_direct_evidence"] != scope["direct_evidence"]:
        raise StageContextError("直接证据在增量计算与审核范围登记之间变化；重新生成，不混用版本。")
    round_key = hashlib.sha256(json.dumps([scope, delta], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    round_dir = project / CONTEXT_DIR / "review_rounds" / round_key if formal else project / CONTEXT_DIR
    scope_path, delta_path = round_dir / "review_scope.json", round_dir / "review_delta.json"
    review_boundary = (
        "本包仅用于用户明确要求的独立审核；生成文件本身不构成派发授权。默认自查在当前对话完成，不新建审核agent。已获独立审核授权时只读检查，不继承生产对话，不修改产物。"
        if review_mode == "initial"
        else "修订复审：必须回原审核子 agent，用 `followup_task` 交回最新审核包；下游打回上游也回该上游原审核者，换修订号不换审核者；原 agent 无法恢复时报告限制并暂停，不自动另建空白 agent；只读；仅复核修订项、原阻塞项及其直接影响；不修改产物。"
    )
    lines = [
        "# 阶段审核包", "",
        f"- Skill：`{args.skill}`",
        f"- 审核模式：`{review_mode}`",
        f"- 审核边界：{review_boundary}", "",
        "## 审核配置", "",
        f"- Profile：`{profile_path}`",
        f"- Profile 版本：`{profile['schema_version']}`",
        f"- 审核层：`{review_kind}`",
        "- 自动必检项不可由命令行删除；`--check` 只能复述 prepare 已冻结的手动项。", "",
        "## 本轮任务依据", "",
        f"- 唯一依据：`{basis_path}`（实质任务身份：`{scope['task_basis_identity']}`；文件 SHA-256 仅供追溯）",
        f"- 交付对象：{json.dumps(task_basis['deliverables'], ensure_ascii=False)}",
        f"- 非目标：{json.dumps(task_basis['non_goals'], ensure_ascii=False)}",
        f"- 输入版本：{json.dumps(task_basis['input_versions'], ensure_ascii=False)}",
        "- 适用标准、方法全文和版本见该依据及下列明确证据；不得在审核中另加标准。", "",
        "## 当前候选产物", "",
    ]
    lines.extend(f"- `{item}`" for item in artifacts)
    lines.extend(["", "## 本轮版本与冻结标准", "", f"- `{scope_path}`（当前候选、来源与验收身份；{'原样作为 review.json 的 scope' if formal else '仅保留当前范围，不要求复制进发现记录'}）"])
    if delta is not None and formal:
        lines.extend(["", "## 本轮增量", "", f"- `{delta_path}`（仅上轮未决项、逐 ID 修改说明、实算 diff）",
                      f"- 原审核 agent：`{delta['reviewer_id']}`",
                      "- 不读全部上轮 review、历史候选或对话；baseline 仅用于核对删除内容及旧行号，不是全文读取清单。",
                      "- 先读实际 diff、changed_sections、deleted_sections、file_level_changes；旧行号对应 baseline，新行号对应 current。空 changed_sections 不代表没有影响。",
                      "- diff_format=unicode-edits-v1时读取text_edits：removed/added是完整改动，context仅为附近定位，不是完整审查范围。位置按统一换行后的Unicode字符计数，不手工拼稿；程序已验证可还原。沿用同版已有上下文，必要时按before_line/after_line补读受影响段及直接关系；不得因切片短就缩小内容审查。",
                      "- continuity_neighbors 是同文件连续性定位；file_level_impact_review_required 要求判断文件级决定影响了哪些段。cross_file_context_review_required 提醒核对边界的直接关联候选或源证据，不按 artifact 列表顺序猜时间顺序，不全量重读。标记为 false 也不证明没有语义关联。",
                      "- 上轮仍有效的检查覆盖继续有效；下面冻结清单不是要求全量重审。",
                      "- `--evidence` 只能列本轮直接受影响的材料；登记、确认、知识依据有变化时仅显式提供有关片段及源位置。"])
    elif delta is not None:
        lines.extend(["", "## 本轮轻复审", "", f"- `{delta_path}`（上轮未决发现、声明改动与直接影响；不是程序实算 diff）",
                      "- 必须回原审核者，逐条回应输入未决发现及修改位置；解决项保留 check_id、target 并标记 status=resolved，遗漏不能当作已解决。",
                      "- 不要求 baseline 全文、生产者逐 ID 回复、编号水位或审核谱系；必要时只补读准确源位置。"])
    elif review_kind == "production":
        lines.extend(["", "## 产物登记证据", ""])
        lines.append(f"- {'缺少：' if not manifest_path.is_file() else ''}`{manifest_path}`")
        lines.append("- 只核对 task_basis 已列当前输入与相关确认；不读取全部候选或历史登记。")
        lines.extend(["", "## 项目状态与用户确认事实", ""])
        lines.append(f"- {'缺少：' if not state_path.is_file() else ''}`{state_path}`")
        candidate_evidence = _evidence_current(project, args.skill)
        lines.extend(["", "## 本轮按需知识依据", ""])
        raw_candidate_evidence = _read_json(project / CONTEXT_DIR / "method_reading_evidence.json", default=None)
        if candidate_evidence:
            _, _, candidate_packet = candidate_evidence
            lines.extend([f"- 完整方法/已采用知识快照：`{candidate_packet}`", f"- 读取声明与版本证据：`{project / CONTEXT_DIR / 'method_reading_evidence.json'}`"])
        elif isinstance(raw_candidate_evidence, dict) and raw_candidate_evidence.get("skill") == args.skill and raw_candidate_evidence.get("adopted_candidates"):
            lines.append("- 已登记候选证据无效：`method_reading_evidence.json`（不得作为本轮依据）")
        else:
            lines.append("- 无有效读取快照；若任务依据列有必读方法，先完成读取登记再打包审核。")
    else:
        lines.extend(["", "## 合同审核边界", "", "- 仅审核当前 Skill 合同候选的触发、职责、输入输出、引用和硬禁区；不把真实项目产物结论混入本轮。"])
    if delta is not None:
        lines.extend(["", "## 直接证据身份变化", "",
                      "- 增量中的 direct_evidence_changes 明确新增、撤回、同路径换版及前后 SHA-256；它只证明字节身份，不裁定语义影响。",
                      "- 原审核者对相关未决及既有通过项判断保留、重开或无法判断，列准确依据与受影响对象；不得用旧证据关闭问题，也不因证据换版自动全量首审。"])
    lines.extend(["", "## 外部只读审核证据", ""])
    if external_evidence:
        lines.extend(f"- `{item}`" for item in external_evidence)
    else:
        lines.append("- 无（未声明外部审核证据）")
    if revision and review_mode == "initial":
        lines.extend(["", "## 前序修订约束", "", "```json", json.dumps(revision, ensure_ascii=False, indent=2), "```"])
    lines.extend(["", "## 检查清单", ""])
    lines.extend(f"- [ ] `{item['id']}`：{item['text']}" for item in checks)
    lines.extend(["", "## 输出要求", ""])
    if formal:
        lines.extend([f"- 本轮为正式留档：review.json / review.md 写入 `{round_dir}`；结构见审核方法的正式留档节。",
                      "- 问题使用稳定 B-012 等 ID，逐项输出 resolved / unresolved / new-blocker；建议为 suggestion。"])
    else:
        lines.extend(["- 默认轻审核：只回结论和每条 check_id、target（对象）、evidence（证据）、impact（影响）。",
                      "- 若需 CLI 复审，将这些发现记在同一 issues 数组；不要求 review.json / review.md 双文件、基线快照或问题编号。"])
    lines.extend(["- 只传本轮结论、未决发现和必要证据，不传完整聊天。",
                  "- 结论只允许“通过”“有条件通过”“不通过”。没有新证据不新增审美标准；图片视觉质量由用户终审。", ""])
    content = "\n".join(lines)
    output = project / CONTEXT_DIR / "review_packet.md"
    report = validate_text("review", output, content)
    write_text(scope_path, json.dumps(scope, ensure_ascii=False, indent=2) + "\n")
    if delta is not None:
        write_text(delta_path, json.dumps(delta, ensure_ascii=False, indent=2) + "\n")
    if formal:
        write_text(round_dir / "review_packet.md", content)
    write_text(output, content)
    report["profile"] = str(profile_path)
    report["required_check_count"] = len(required_checks)
    report["manual_check_count"] = len(manual_checks)
    report["manifest_evidence_path"] = str(manifest_path)
    report["manifest_evidence_exists"] = manifest_path.is_file()
    report["project_state_evidence_path"] = str(state_path)
    report["project_state_evidence_exists"] = state_path.is_file()
    report["review_mode"] = review_mode
    report["review_kind"] = review_kind
    report["formal_archive"] = formal
    report.update(input_validation)
    report["task_basis_path"] = str(basis_path)
    report["review_scope_path"] = str(scope_path)
    report["review_delta_path"] = str(delta_path) if delta is not None else None
    report["external_evidence_paths"] = [str(item) for item in external_evidence]
    if usage is not None:
        report["review_usage"] = {"phase": getattr(args, "usage_phase", None) or ("initial_review" if review_mode == "initial" else "revision_review"), **usage}
    return report


def _ensure_prepared_base(args: argparse.Namespace) -> dict[str, Any]:
    """产前硬门禁：生产某 stage 产物前，校验阶段启动包存在且 Skill 匹配。

    校验结构、已选知识正文和调用方声明的任务必需项。
    通过不证明 Agent 已阅读或完成知识合规审查。
    """
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise StageContextError(f"项目目录不存在：{project}")
    requested = getattr(args, "skill", None)
    _require_preparation_skill(requested)
    startup = project / CONTEXT_DIR / "stage_startup.md"
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
    reading_manifest = _reading_manifest(project)
    trial = reading_manifest.get("candidate_trial") if isinstance(reading_manifest, dict) else None
    trial_packet = project / CONTEXT_DIR / "candidate_trial_packet.md"
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
        profile = _read_json(ROOT / "skills" / skill / "knowledge-profile.json", default=None)
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
    reading = _evidence_current(project, skill)
    adopted_ids = {item["id"] for item in reading[1].get("adopted_candidates", [])} if reading else set()
    for rule in getattr(args, "required_rule", None) or []:
        if rule not in ids and rule not in adopted_ids:
            raise StageContextError(f"当前任务必需动态规则未命中：{rule}")
    for reference in getattr(args, "required_reference", None) or []:
        path = (ROOT / reference).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file() or not path.stat().st_size:
            raise StageContextError(f"当前任务必需本地规范不存在、为空或越出仓库：{reference}")
        _, basis = _load_task_basis(project, skill)
        frozen = {item["path"]: item["sha256"] for item in basis["standards"].get("local_documents", [])}
        frozen[basis["standards"]["root_rules"]] = basis["standards"]["root_rules_sha256"]
        if str(path) not in frozen:
            raise StageContextError(f"必需本地规范未在本轮冻结：{reference}；在 prepare 请求 local_references 中列明并重新准备。")
        if digest(path) != frozen[str(path)]:
            raise StageContextError(f"必需本地规范已变更：{reference}；请重新 prepare。")
    return {
        "kind": "startup",
        "path": str(startup),
        "skill": skill,
        "prepared": True,
        "knowledge_compliance": "requires_agent_review",
        "ok": True,
    }


def ensure_prepared(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(args.project).expanduser().resolve()
    testing = _test_request(args, project)
    result = _ensure_prepared(args)
    if testing:
        result.update(test_metadata())
        result["candidate_checks_passed"] = result.get("ok") is True
    return result


def _ensure_prepared(args: argparse.Namespace) -> dict[str, Any]:
    usage = _check_run_budget(args)
    result = _ensure_prepared_base(args)
    project = Path(args.project).expanduser().resolve()
    if usage is not None:
        result["run_usage"] = usage
    startup_text = (project / CONTEXT_DIR / "stage_startup.md").read_text(encoding="utf-8")
    manifest = _reading_manifest(project)
    if not isinstance(manifest, dict):
        startup_text = (project / CONTEXT_DIR / "stage_startup.md").read_text(encoding="utf-8")
        if "以下全文已写入 `method_reading_packet.md`" in startup_text:
            raise StageContextError("必读专业方法任务清单缺失或无效，请重新 prepare。")
        if "本启动包含显式测试候选方法" in startup_text:
            result["ok"] = False
            result["production_blocked"] = "test_only_proposed_method_binding"
        return result
    skill = result["skill"]
    _, basis = _load_task_basis(project, skill)
    result.update(_validate_current_inputs(project, basis))
    if manifest.get("skill") != skill or manifest.get("project_path") != str(project) or not isinstance(manifest.get("methods"), list):
        raise StageContextError("阅读任务清单无效，请重新 prepare。")
    test_only = manifest.get("test_only_proposed_method_ids", [])
    if not isinstance(test_only, list) or not all(isinstance(item, str) and item for item in test_only):
        raise StageContextError("阅读任务清单的测试标记无效，请重新 prepare。")
    # A candidate directory is discoverability only. With no mandatory method it cannot
    # turn optional reading into a production gate.
    if not manifest["methods"]:
        evidence = _read_json(project / CONTEXT_DIR / "method_reading_evidence.json", default=None)
        adopted = evidence.get("adopted_candidates") if isinstance(evidence, dict) else []
        if adopted:
            result["candidate_reading_evidence"] = "recorded" if _evidence_current(project, skill) else "invalid"
        if test_only:
            result["ok"] = False
            result["production_blocked"] = "test_only_proposed_method_binding"
        return result
    identity = _reading_identity(args)
    manifest_identity = {key: manifest.get(key) for key in identity}
    identity_matches = all(identity.values()) and all(isinstance(value, str) and value for value in manifest_identity.values()) and identity == manifest_identity
    current = _evidence_current(project, skill)
    complete = bool(current and identity_matches)
    result["reading_identity"] = "matched" if identity_matches else ("required" if not all(identity.values()) or not all(manifest_identity.values()) else "mismatch")
    result["method_reading_evidence"] = "recorded" if complete else "required"
    result["required_method_count"] = len(manifest["methods"])
    result["test_only_proposed_method_ids"] = test_only
    result["ok"] = bool(result.get("ok")) and complete and not test_only
    if test_only:
        result["production_blocked"] = "test_only_proposed_method_binding"
    return result

def record_method_reading(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(args.project).expanduser().resolve()
    skill = str(args.skill or "").strip()
    _require_preparation_skill(skill)
    manifest = _reading_manifest(project)
    if not skill or not isinstance(manifest, dict) or manifest.get("skill") != skill or manifest.get("project_path") != str(project) or not isinstance(manifest.get("methods"), list):
        raise StageContextError("没有与当前 Skill 匹配的阅读任务清单；请先 prepare。")
    _, basis = _load_task_basis(project, skill)
    _validate_current_inputs(project, basis)
    identity = _reading_identity(args)
    if not all(identity.values()) or any(manifest.get(key) != identity[key] for key in identity):
        raise StageContextError("登记读取证据必须使用 prepare 时记录的实际执行者 --actor-id 与稳定任务 --task-id。")
    requested = list(getattr(args, "method", None) or [])
    methods = [item.get("id") for item in manifest["methods"] if isinstance(item, dict)]
    if sorted(requested) != sorted(methods) or len(requested) != len(set(requested)):
        raise StageContextError("读取证据必须一次列出且仅列出本轮全部必读专业方法 ID。")
    requested_candidates = list(getattr(args, "candidate_rule", None) or [])
    vault = _vault_root(getattr(args, "vault", None)) if requested_candidates else Path(str(manifest.get("vault_root") or ""))
    if requested_candidates and manifest.get("vault_root") != str(vault):
        raise StageContextError("读取 Vault 根必须与 prepare 时解析的 Vault 根一致。")
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
    adopted = [_read_candidate_exact(vault, catalog[rule]) for rule in requested_candidates]
    tombstones = _tombstone_fingerprints(vault, load_policy(ROOT))
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
    packet = project / CONTEXT_DIR / "method_reading_packet.md"
    if not packet.is_file():
        raise StageContextError("必读方法全文快照缺失；请重新 prepare，不能登记读取。")
    existing = packet.read_bytes()
    base_length = manifest.get("method_packet_bytes")
    if not isinstance(base_length, int) or base_length < 0 or len(existing) < base_length or hashlib.sha256(existing[:base_length]).hexdigest() != manifest.get("method_packet_sha256"):
        raise StageContextError("必读方法全文快照已被修改；请重新 prepare，不能登记读取。")
    packet_text = existing[:base_length].decode("utf-8") + (_candidate_packet(adopted, scopes) if adopted else "")
    write_text_if_changed(packet, packet_text)
    evidence = {"schema_version": "1.0", "skill": skill, "project_path": str(project), "vault_root": str(vault), **identity,
        "methods": manifest["methods"], "selected": manifest.get("selected", []), "method_packet_sha256": manifest["method_packet_sha256"],
        "test_only_proposed_method_ids": manifest.get("test_only_proposed_method_ids", []),
        "adopted_candidates": records, "candidate_scopes": scopes, "packet_sha256": hashlib.sha256(packet.read_bytes()).hexdigest(),
        "attestation": "agent_reported_complete_reading"}
    evidence['reading_reuse_key'] = _read_json(_task_basis_path(project), default={}).get('reading_reuse_key')
    output = project / CONTEXT_DIR / "method_reading_evidence.json"
    write_text_if_changed(output, json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    _save_reading_cache(project)
    return {"kind": "reading_evidence", "path": str(output), "packet_path": str(packet), "skill": skill, "method_count": len(methods), "candidate_count": len(records), "evidence": "agent_reported_not_program_verified", "ok": True}


def record_candidate_reading(args: argparse.Namespace) -> dict[str, Any]:
    """Retired command retained only to direct callers to the unified receipt."""
    raise StageContextError("record-candidate-reading 已退役；请使用 record-method-reading --candidate-rule，并显式列出全部 --method。")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    state_cmd = commands.add_parser("status", help="只读查看项目状态与续跑阻塞；不执行事务恢复")
    state_cmd.add_argument("--project", required=True)
    adopt = commands.add_parser("adopt-package", help="登记并确认初次平台无关正式稿件；不上传或发布")
    adopt.add_argument("--project", required=True)
    adopt.add_argument("--artifact-id", required=True)
    adopt.add_argument("--logical-id", choices=("narrative.package", "storyboard.package", "video.prompt"), required=True)
    adopt.add_argument("--path", required=True, help="项目内的正式 JSON 相对路径")
    adopt.add_argument("--revision", required=True)
    adopt.add_argument("--approval-ref", required=True, help="准确用户整稿确认依据")
    adopt.add_argument("--scope-description", default="", help="本次整稿确认范围说明")
    adopt.add_argument("--depends-on", action="append", default=[], help="准确上游逻辑产物，可重复")
    testing = commands.add_parser("candidate-test", help="显式配置独立候选试跑；绝不登记用户产物确认")
    testing.add_argument("--project", required=True)
    testing.add_argument("--run-id", required=True)
    testing.add_argument("--authorization-ref", required=True)
    testing.add_argument("--input-artifact", action="append", default=[])
    source_index = commands.add_parser("candidate-source-index", help="只读核验 profile 固定的候选来源快照并列出可试用规则")
    source_index.add_argument("--skill", required=True)
    source_index.add_argument("--source-id", required=True)
    source_index.add_argument("--snapshot", required=True, help="显式提供已核验 Library 快照的本地物化路径")
    start = commands.add_parser("prepare")
    start.add_argument("--project", required=True)
    start.add_argument("--skill", required=True)
    start.add_argument("--objective", required=True)
    start.add_argument("--request-json")
    start.add_argument("--vault")
    start.add_argument("--actor-id", help="实际执行此任务的 agent 标识；缺失时阅读声明不能放行")
    start.add_argument("--task-id", help="稳定任务标识；同一任务重 prepare 可复用同版阅读声明")
    start.add_argument("--change-id")
    start.add_argument("--target-logical-id", action="append")
    start.add_argument("--test-proposed-method", action="append", default=[], help="仅隔离测试：显式加载一个 proposed 方法；其启动包会被生产前校验拒绝，可重复")
    start.add_argument("--test-profile-json", help="仅隔离测试：包含 proposed 方法绑定的临时 profile；必须同时给出 --test-proposed-method")
    gate = commands.add_parser("ensure-prepared")
    gate.add_argument("--project", required=True)
    gate.add_argument("--skill", required=True, help="当前生产 Skill；启动包 Skill 必须与之匹配")
    gate.add_argument('--show-task-basis', action='store_true', help='通过门禁后返回不含后台目录的任务阅读视图')
    gate.add_argument("--actor-id", help="实际执行此任务的 agent 标识")
    gate.add_argument("--task-id", help="稳定任务标识")
    gate.add_argument("--required-rule", action="append", default=[], help="本任务必需的动态规则 ID，可重复")
    gate.add_argument("--required-reference", action="append", default=[], help="本任务必需的本地规范，仓库相对路径，可重复")
    for command in (start, gate):
        command.add_argument("--candidate-test", action="store_true", help="仅隔离候选试跑；不构成生产放行")
        command.add_argument("--candidate-source-id", help="profile 中固定的候选来源 ID，仅 candidate-test 使用")
        command.add_argument("--candidate-source-snapshot", help="显式提供来源快照物化文件，仅 candidate-test 使用")
        command.add_argument("--candidate-rule", action="append", default=[], help="指定进入隔离试用包的 candidate 状态规则，可重复")
        command.add_argument("--usage-log", action="append", help="本次 Codex 原始日志，可重复提供窗口分片")
        command.add_argument("--usage-turn-id", help="准确的本轮 turn_id；不以文件名代替边界")
        command.add_argument("--usage-boundaries-json", help="显式累计边界 JSON；不自动发现宿主历史")
        command.add_argument("--max-input", type=int, help="任一次已观测输入的停止阈值（含缓存）")
        command.add_argument("--max-total", type=int, help="本轮累计 token 停止阈值")
        command.add_argument("--max-requests", type=int, help="本轮已记录模型请求次数停止阈值")
    receipt = commands.add_parser("record-method-reading")
    receipt.add_argument("--project", required=True)
    receipt.add_argument("--skill", required=True)
    receipt.add_argument("--actor-id", help="实际执行此任务的 agent 标识")
    receipt.add_argument("--task-id", help="稳定任务标识")
    receipt.add_argument("--method", action="append", default=[], help="已完整阅读的必读专业方法 ID，可重复")
    receipt.add_argument("--candidate-rule", action="append", default=[], help="实际采用并按精确目录读取的候选规则 ID，可重复")
    receipt.add_argument("--candidate-scope", action="append", default=[], help="跨职责候选的本职采用范围：ID=范围及适用理由；范围外内容不成为产出要求")
    receipt.add_argument("--vault", help="必须与 prepare 时的 Vault 根相同")
    candidate_receipt = commands.add_parser("record-candidate-reading")
    candidate_receipt.add_argument("--project", required=True)
    candidate_receipt.add_argument("--skill", required=True)
    candidate_receipt.add_argument("--actor-id", required=True, help="实际执行此任务的 agent 标识")
    candidate_receipt.add_argument("--task-id", required=True, help="稳定任务标识")
    candidate_receipt.add_argument("--candidate-rule", action="append", default=[], help="已按候选目录精确路径完整阅读并采用的规则 ID，可重复")
    candidate_receipt.add_argument("--candidate-scope", action="append", default=[])
    candidate_receipt.add_argument("--vault")
    review = commands.add_parser("review-pack")
    review.add_argument("--project", required=True)
    review.add_argument("--skill", required=True)
    review.add_argument("--artifact", action="append", required=True)
    review.add_argument("--review-kind", choices=("production", "skill_contract"), default="production", help="生产候选或 Skill 合同审查；两者不混用")
    review.add_argument("--check", action="append", default=[])
    review.add_argument("--evidence", action="append", default=[], help="明确列入审核包的项目外只读文件证据")
    review.add_argument("--review-mode", choices=("initial", "revision"), default="initial")
    review.add_argument("--formal-archive", action="store_true", help="仅用户要求正式留档时启用版本目录、稳定问题 ID 及真实基线对比")
    review.add_argument("--change-summary", action="append", default=[], help="轻复审实际改动的位置、内容与直接影响；候选字节改变时必须提供，可重复")
    review.add_argument("--previous-review", help="上轮发现 JSON（issues 数组）；轻流程只要求四项发现，正式留档沿用旧结构")
    review.add_argument("--baseline", action="append", help="上轮被审文件快照；顺序与上轮 scope 和本轮 artifact 一致")
    review.add_argument("--producer-response", help="本轮生产者逐问题回复 JSON：{\"responses\":[{id,summary,evidence}]}；不另建 change-log")
    review.add_argument("--change-id")
    review.add_argument("--target-logical-id", action="append")
    review.add_argument("--usage-phase", choices=("production", "self_check", "initial_review", "revision_review"), help="本次用量记录所属阶段；只标注已观测计量，不声称控制宿主请求")
    for option, kwargs in (("--usage-log", {"action": "append"}), ("--usage-turn-id", {}), ("--max-input", {"type": int}), ("--max-total", {"type": int}), ("--max-requests", {"type": int})):
        review.add_argument(option, **kwargs)
    review.add_argument("--usage-boundaries-json", help="显式累计边界 JSON；与单轮参数二选一")
    handoff_cmd = commands.add_parser("handoff")
    handoff_cmd.add_argument("--candidate-test", action="store_true")
    handoff_cmd.add_argument("--project", required=True)
    handoff_cmd.add_argument("--to-skill", required=True)
    check = commands.add_parser("validate")
    check.add_argument("--kind", choices=sorted(LIMITS), required=True)
    check.add_argument("--path", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "status":
        result = status(args)
    elif args.command == "adopt-package":
        result = adopt_package(args)
    elif args.command == "candidate-test":
        result = configure_test(Path(args.project), args.run_id, args.authorization_ref, args.input_artifact)
    elif args.command == "candidate-source-index":
        result = candidate_source_index(args)
    elif args.command == "prepare":
        result = prepare(args)
    elif args.command == "ensure-prepared":
        result = ensure_prepared(args)
        if args.show_task_basis and result.get('ok') is True:
            _, basis = _load_task_basis(Path(args.project).expanduser().resolve(), args.skill)
            result['task_basis'] = _task_reading_view(basis)
    elif args.command == "record-method-reading":
        result = record_method_reading(args)
    elif args.command == "record-candidate-reading":
        result = record_candidate_reading(args)
    elif args.command == "review-pack":
        result = review_pack(args)
    elif args.command == "handoff":
        result = handoff(args)
    else:
        result = validate_text(args.kind, Path(args.path).expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get("ok") is False else 0


if __name__ == "__main__":
    # The shell may select GBK while the host decodes UTF-8. Configure both
    # channels before producing any non-ASCII JSON or diagnostic output.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    try:
        raise SystemExit(main())
    except (OSError, StageContextError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
