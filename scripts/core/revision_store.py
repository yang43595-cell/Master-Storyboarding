"""Acyclic L1 change-control storage, transaction, and production-isolation primitives."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .confirmation_scope import is_whole_artifact
from .errors import RevisionControlError
from .io import (
    PROJECT_LOCK_RELATIVE,
    TRANSACTION_ROOT,
    read_json,
    recover_transactions as _io_recover_transactions,
    sha256_file,
    utc_now,
    write_bytes,
    write_json,
)
from .manifest_store import digest, load_manifest, project_path
from .project_state import (
    STATE_KEYS,
    load_project_state,
    normalize_project_state,
    reconcile_current_artifacts,
    render_project_state_markdown,
    validate_candidate_test_context,
    validate_candidate_test_stage,
    validate_project_state,
)
from .prompt_body import SOURCE_SEGMENT_FIELDS, source_review_segments, source_shot_ids, submitted_prompt_body

MANAGE = Path("00_项目管理")
CONTROL_RELATIVE = MANAGE / "change_control.json"
LOCK_RELATIVE = PROJECT_LOCK_RELATIVE
CONTROL_SCHEMA_VERSION = "revision-control-1.0"
TIER_VALUES = {"T0", "T1", "T2"}

_PRODUCTION_BLOCKED = "候选测试禁止正式采用、生成提交或发布；仍为 unreviewed"


def candidate_test_result_metadata() -> dict[str, Any]:
    return {"execution_mode": "candidate_test", "review_status": "unreviewed", "production_blocked": True}


def candidate_test_context(project: Path | str) -> dict[str, Any] | None:
    project = Path(project).resolve()
    state = load_project_state(project)
    if "candidate_test" not in state:
        return None
    value = state["candidate_test"]
    validate_candidate_test_context(value, state)
    if value["project_path"] != str(project):
        raise ValueError("候选测试状态属于另一项目，不能复制授权运行身份")
    if state.get("current_stage"):
        validate_candidate_test_stage(state["current_stage"])
    return value


def require_production_manifest(project: Path | str) -> None:
    """Reject test artifacts after the caller has checked the project mode."""
    if any(
        a.get("candidate_test") or a.get("production_blocked") or a.get("type") == "preview-reference"
        for a in load_manifest(Path(project))["artifacts"]
    ):
        raise ValueError(_PRODUCTION_BLOCKED)


def require_production(project: Path | str) -> None:
    """Guard supported adoption/submission endpoints, including removed mode state."""
    if candidate_test_context(project):
        raise ValueError(_PRODUCTION_BLOCKED)
    require_production_manifest(project)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _control_default() -> dict[str, Any]:
    return {"schema_version": CONTROL_SCHEMA_VERSION, "active_change": None, "history": []}


def _load_control(project: Path, *, read_json_fn=read_json) -> dict[str, Any]:
    path = project / CONTROL_RELATIVE
    if not path.is_file():
        if path.exists() or path.is_symlink():
            raise RevisionControlError("change_control.json 不是可读取的普通文件")
        for intent_path in (project / TRANSACTION_ROOT).glob("*/commit_intent.json"):
            intent = read_json_fn(intent_path)
            files = intent.get("files") if isinstance(intent, dict) else None
            if not isinstance(files, list) or any(not isinstance(item, dict) for item in files):
                raise RevisionControlError(f"提交意图结构无效：{intent_path.parent.name}")
            if any(item.get("path") == CONTROL_RELATIVE.as_posix() for item in files):
                raise RevisionControlError("既有提交意图绑定的 change_control.json 缺失")
        return _control_default()
    value = read_json_fn(path)
    if not isinstance(value, dict) or value.get("schema_version") != CONTROL_SCHEMA_VERSION:
        raise RevisionControlError("change_control.json 结构无效")
    if "active_change" not in value or "history" not in value or not isinstance(value["history"], list):
        raise RevisionControlError("change_control.json 缺少活动变更案或历史字段")
    if value["active_change"] is not None and not isinstance(value["active_change"], dict):
        raise RevisionControlError("change_control.json 活动变更案结构无效")
    return value


def _transaction_dir(project: Path, transaction_id: str) -> Path:
    return project / TRANSACTION_ROOT / transaction_id


def _commit_json_transaction_unlocked(
    project: Path,
    updates: dict[Path, Any],
    *,
    reason: str,
    transaction_id: str | None = None,
    fail_after: int | None = None,
    preserve_state_extensions: bool = False,
    write_json_fn=write_json,
) -> dict[str, Any]:
    """Write a durable intent, then replace JSON targets and their state view."""
    if not updates:
        raise RevisionControlError("事务至少需要一个状态更新")
    updates = dict(updates)
    state_view = None
    for target, value in updates.items():
        if target.resolve() == (project / MANAGE / "project_state.json").resolve():
            manifest = updates.get(project / MANAGE / "artifact_manifest.json")
            if manifest is None:
                manifest = load_manifest(project)
            raw_state = value
            if preserve_state_extensions:
                value = normalize_project_state(raw_state)
                for key in STATE_KEYS:
                    if key in raw_state and key not in {"schema_version", "current_artifacts"}:
                        value[key] = raw_state[key]
                validate_project_state(value)
            value = reconcile_current_artifacts(value, manifest)
            validate_project_state(value)
            state_view = value
            updates[target] = (
                {**raw_state, "current_artifacts": value["current_artifacts"]}
                if preserve_state_extensions
                else value
            )
    state_value = updates.get(project / MANAGE / "project_state.json")
    if state_value is not None:
        updates[project / MANAGE / "project_state.md"] = render_project_state_markdown(
            state_view or state_value
        ).encode("utf-8")
    transaction_id = transaction_id or f"REV-TX-{uuid.uuid4().hex[:16]}"
    tx_dir = _transaction_dir(project, transaction_id)
    if tx_dir.exists():
        raise RevisionControlError(f"事务目录已存在：{transaction_id}")
    tx_dir.mkdir(parents=True)
    records: list[dict[str, Any]] = []
    for target, value in sorted(updates.items(), key=lambda pair: str(pair[0])):
        try:
            relative = target.resolve().relative_to(project.resolve())
        except ValueError as exc:
            raise RevisionControlError(f"事务目标必须位于项目内：{target}") from exc
        before = target.read_bytes() if target.is_file() else None
        after = value if isinstance(value, bytes) else _json_bytes(value)
        before_path = tx_dir / "before" / relative
        after_path = tx_dir / "after" / relative
        after_path.parent.mkdir(parents=True, exist_ok=True)
        after_path.write_bytes(after)
        if before is not None:
            before_path.parent.mkdir(parents=True, exist_ok=True)
            before_path.write_bytes(before)
        records.append({
            "path": relative.as_posix(),
            "before_exists": before is not None,
            "before_sha256": sha256_file(before_path) if before is not None else None,
            "after_sha256": sha256_file(after_path),
        })
    transaction = {
        "schema_version": "1.0",
        "transaction_id": transaction_id,
        "reason": reason,
        "prepared_at": utc_now(),
        "files": records,
    }
    write_json_fn(tx_dir / "transaction.json", transaction)
    write_json_fn(tx_dir / "commit_intent.json", {
        "transaction_id": transaction_id,
        "decided_at": utc_now(),
        "files": [{"path": item["path"], "after_sha256": item["after_sha256"]} for item in records],
    })
    for index, record in enumerate(records, start=1):
        after_path = tx_dir / "after" / Path(record["path"])
        if sha256_file(after_path) != record["after_sha256"]:
            raise RevisionControlError(f"事务暂存文件损坏：{record['path']}")
        write_bytes(project / Path(record["path"]), after_path.read_bytes())
        if fail_after is not None and index >= fail_after:
            raise RuntimeError("fault injection: transaction interrupted after commit intent")
    write_json_fn(tx_dir / "result.json", {"state": "committed", "committed_at": utc_now()})
    return {"transaction_id": transaction_id, "state": "committed", "files": [item["path"] for item in records]}


def recover_transactions(project: Path, *, read_json_fn=read_json) -> list[dict[str, Any]]:
    """Finish committed intents and safely close prepared-but-undecided work."""
    return _io_recover_transactions(project, read_json_fn=read_json_fn)


def guard_action(
    project: Path,
    *,
    logical_ids: list[str],
    change_id: str | None = None,
    read_json_fn=read_json,
) -> dict[str, Any]:
    """Fail closed when a requested action touches a frozen branch."""
    project = project.resolve()
    control = _load_control(project, read_json_fn=read_json_fn)
    active = control.get("active_change")
    if not isinstance(active, dict):
        return {"allowed": True, "reason": "no_active_change"}
    touched = sorted(set(logical_ids) & set(active.get("frozen_logical_ids") or []))
    if touched and change_id != active.get("change_id"):
        raise RevisionControlError(f"动作触及冻结分支：{', '.join(touched)}")
    return {"allowed": True, "change_id": active.get("change_id"), "frozen_intersection": touched}


def show_change(project: Path, *, read_json_fn=read_json) -> dict[str, Any]:
    project = project.resolve()
    return _load_control(project, read_json_fn=read_json_fn)


def inspect_revision_status(
    project: Path, *, read_json_fn=read_json
) -> tuple[dict[str, Any] | None | str, list[dict], list[str]]:
    """Read revision and transaction facts without recovering or writing state."""
    blockers: list[str] = []
    try:
        active_change = _load_control(project, read_json_fn=read_json_fn)["active_change"]
    except (OSError, ValueError) as exc:
        active_change = "unknown"
        blockers.append(f"活动变更状态未知：{exc}")
    transaction_rows = []
    tx_root = project / TRANSACTION_ROOT
    if tx_root.exists() and not tx_root.is_dir():
        blockers.append("revision_transactions 不是目录；事务状态未知")
    elif tx_root.is_dir():
        for folder in sorted(tx_root.iterdir()):
            if not folder.is_dir():
                continue
            row = {"transaction_id": folder.name, "state": "unknown", "anomalies": []}
            try:
                transaction, intent, result = (
                    read_json_fn(path) if path.is_file() else None
                    for path in (folder / name for name in ("transaction.json", "commit_intent.json", "result.json"))
                )
                if not isinstance(transaction, dict):
                    row["anomalies"].append("transaction.json 缺失/无效")
                if intent is not None and not isinstance(intent, dict):
                    row["anomalies"].append("commit_intent.json 无效")
                if isinstance(intent, dict) and not isinstance(result, dict):
                    row["anomalies"].append("已有 commit intent 但无 result；需显式恢复")
                if result is None and intent is None:
                    row["state"] = "prepared_without_intent"
                elif isinstance(result, dict) and result.get("state") == "aborted" and intent is None:
                    row["state"] = "aborted"
                elif isinstance(result, dict) and result.get("state") == "committed" and isinstance(intent, dict):
                    row["state"] = "committed"
                else:
                    row["anomalies"].append("事务结果与提交意图不一致")
            except (OSError, ValueError) as exc:
                row["anomalies"].append(f"事务记录不可读：{exc}")
            if row["anomalies"]:
                row["state"] = "anomalous"
                blockers.extend(f"事务 {folder.name}：{problem}" for problem in row["anomalies"])
            transaction_rows.append(row)
    return active_change, transaction_rows, blockers


def _generation_segments(payload: Any) -> list[dict[str, Any]] | None:
    """Return actual prompt segments, or None when this is not a segmented prompt."""
    if not isinstance(payload, dict) or not any(key in payload for key in SOURCE_SEGMENT_FIELDS):
        return None
    try:
        rows = source_review_segments(payload)
    except ValueError as exc:
        raise RevisionControlError("视频提示词生成段不完整：" + str(exc)) from exc
    try:
        for row in rows:
            if not submitted_prompt_body(row).strip():
                raise ValueError("empty prompt")
    except (ValueError, TypeError, KeyError) as exc:
        raise RevisionControlError("generation_segments 必须含真实提示词正文，不能把材料组登记为生成段") from exc
    return rows


def _validate_segment_reconciliation(
    project: Path,
    baseline: dict[str, Any],
    candidate: Any,
    *,
    read_json_fn=read_json,
) -> None:
    """Bind any stable segment-ID replacement to the actual baseline bytes and reasons."""
    if not isinstance(baseline, dict):
        return
    old_path = project_path(project, str(baseline.get("path_or_url") or ""))
    if not old_path.is_file():
        raise RevisionControlError("视频提示词旧版本缺失；生成段续跑基线未知")
    if not baseline.get("checksum") or sha256_file(old_path) != baseline.get("checksum"):
        raise RevisionControlError("视频提示词旧版本与活动确认 hash 不一致；生成段续跑基线已过期")
    try:
        old_payload = read_json_fn(old_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise RevisionControlError("视频提示词旧版本不可解析；生成段续跑基线未知") from exc
    old_rows = _generation_segments(old_payload)
    new_rows = _generation_segments(candidate)
    if old_rows is None and new_rows is None:
        return
    if new_rows is None:
        raise RevisionControlError("已有生成段不可被材料组或未分段稿静默替代")
    old_ids = [row["segment_id"] for row in old_rows or []]
    new_ids = [row["segment_id"] for row in new_rows]
    if not old_ids:
        return
    added, removed = sorted(set(new_ids) - set(old_ids)), sorted(set(old_ids) - set(new_ids))
    old_by_id = {row["segment_id"]: row for row in old_rows or []}
    new_by_id = {row["segment_id"]: row for row in new_rows}
    common = set(old_by_id) & set(new_by_id)
    old_common_order = [ident for ident in old_ids if ident in common]
    new_common_order = [ident for ident in new_ids if ident in common]
    order_changed = old_common_order != new_common_order
    coverage_fields = ("source_shot_ids", "source_dialogue_ids", "source_paragraph_ids")
    coverage_changes = []
    for segment_id in sorted(common):
        for field in coverage_fields:
            if field == "source_shot_ids":
                before = source_shot_ids(old_by_id[segment_id]) or None
                after = source_shot_ids(new_by_id[segment_id]) or None
            else:
                before, after = old_by_id[segment_id].get(field), new_by_id[segment_id].get(field)
            for value in (before, after):
                if value is not None and (
                    not isinstance(value, list)
                    or any(not isinstance(ident, str) or not ident for ident in value)
                    or len(value) != len(set(value))
                ):
                    raise RevisionControlError(f"{segment_id} 的 {field} 覆盖声明无效；不猜镜头/对白范围")
            if before != after:
                coverage_changes.append({"segment_id": segment_id, "field": field, "before": before, "after": after})

    def body_signature(row: dict[str, Any]) -> str:
        return " ".join(submitted_prompt_body(row).split())

    prompt_changes = []
    for segment_id in sorted(common):
        before, after = body_signature(old_by_id[segment_id]), body_signature(new_by_id[segment_id])
        if before != after:
            prompt_changes.append({
                "segment_id": segment_id,
                "before_sha256": hashlib.sha256(submitted_prompt_body(old_by_id[segment_id]).encode()).hexdigest(),
                "after_sha256": hashlib.sha256(submitted_prompt_body(new_by_id[segment_id]).encode()).hexdigest(),
            })
    if not added and not removed and not coverage_changes and not prompt_changes and not order_changed:
        return
    reconciliation = candidate.get("segment_reconciliation") if isinstance(candidate, dict) else None
    if not isinstance(reconciliation, dict):
        raise RevisionControlError("既有生成段 ID/正文/顺序/来源覆盖有变化；须在同一提示词 JSON 写明 segment_reconciliation 和调整理由")
    target = reconciliation.get("baseline")
    if (not isinstance(target, dict) or target.get("artifact_id") != baseline.get("artifact_id")
            or target.get("sha256") != baseline.get("checksum")):
        raise RevisionControlError("生成段对账未绑定活动变更案的准确旧 artifact/hash")
    changes = reconciliation.get("changes")
    if not isinstance(changes, list):
        raise RevisionControlError("segment_reconciliation.changes 必须逐项说明 ID 增删")
    seen_added, seen_removed, seen_coverage, seen_prompts, seen_order = [], [], [], [], []
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("reason"), str) or not change["reason"].strip():
            raise RevisionControlError("每项生成段新增/取消都必须有非空调整理由")
        if change.get("action") == "add" and change.get("segment_id") in added:
            seen_added.append(change["segment_id"])
        elif change.get("action") == "remove" and change.get("segment_id") in removed:
            seen_removed.append(change["segment_id"])
        elif change.get("action") == "coverage":
            actual = {key: change.get(key) for key in ("segment_id", "field", "before", "after")}
            if actual["field"] == "source_shots":
                actual["field"] = "source_shot_ids"
            if actual not in coverage_changes:
                raise RevisionControlError("生成段镜头/对白覆盖对账与旧稿/新稿字段不符")
            seen_coverage.append(actual)
        elif change.get("action") == "revise":
            actual = {key: change.get(key) for key in ("segment_id", "before_sha256", "after_sha256")}
            if actual not in prompt_changes:
                raise RevisionControlError("生成段正文调整 hash 与旧稿/新稿正文不符")
            seen_prompts.append(actual)
        elif change.get("action") == "reorder":
            if not order_changed or change.get("before") != old_ids or change.get("after") != new_ids or seen_order:
                raise RevisionControlError("生成段顺序调整必须精确绑定旧/新有序 ID")
            seen_order.append(True)
        else:
            raise RevisionControlError("生成段对账含未知或与真实 ID/正文/顺序/覆盖差异不符的调整项")
    if (sorted(seen_added) != added or sorted(seen_removed) != removed
            or len(seen_coverage) != len(coverage_changes)
            or any(seen_coverage.count(item) != 1 for item in coverage_changes)
            or len(seen_prompts) != len(prompt_changes)
            or any(seen_prompts.count(item) != 1 for item in prompt_changes)
            or bool(seen_order) != order_changed):
        raise RevisionControlError("生成段对账必须恰好覆盖全部增删、正文替换、顺序及源覆盖变化")


def formal_adoption(
    entry: dict[str, Any],
    root: Path | str,
    *,
    file_digest=digest,
    read_json_fn=read_json,
) -> dict[str, Any] | None:
    """Return the completed, hash-bound adoption evidence for a bundled artifact."""
    control = Path(root) / CONTROL_RELATIVE
    if not control.is_file():
        return None
    history = read_json_fn(control).get("history", [])
    for change in history:
        receipt = change.get("candidate_confirmation") or {}
        if not is_whole_artifact(receipt):
            continue
        bundle = (change.get("candidate_receipt") or {}).get("bundle") or []
        if (change.get("status") != "completed" or not receipt.get("approval_ref")
                or receipt.get("change_id") != change.get("change_id")):
            continue
        matched = next(
            (item for item in bundle
             if item.get("artifact_id") == entry.get("artifact_id") and item.get("sha256") == entry.get("checksum")),
            None,
        )
        if matched:
            path = project_path(root, matched["path"])
            if path.is_file() and file_digest(path) == matched["sha256"]:
                return {"change_id": change["change_id"], "approval_ref": receipt["approval_ref"], "sha256": matched["sha256"]}
    return None
