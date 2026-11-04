"""Deterministic, file-backed change control for local project revisions.

This module deliberately tracks project facts only: one active change, its
frozen artifact scope, candidate receipts, reviews, and user confirmations.
It never selects or starts an Agent, stores thread state, or decides a stage.
"""

from __future__ import annotations

import json
import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterable

from . import confirmation_scope
from .artifacts import (dependency_closure, load_manifest, mark_impacted_artifacts_stale,
                        project_path, validate_candidate_submission)
from .io import (
    PROJECT_LOCK_RELATIVE,
    file_lock,
    project_lock,
    normalized_text_sha256,
    read_json,
    sha256_file,
    sha256_json,
    utc_now,
    write_bytes,
    write_json,
)
from .project_state import (
    load_project_state, reconcile_current_artifacts, render_project_state_markdown,
    validate_project_state,
)


MANAGE = Path("00_项目管理")
CONTROL_RELATIVE = MANAGE / "change_control.json"
TRANSACTION_ROOT = MANAGE / "revision_transactions"
LOCK_RELATIVE = PROJECT_LOCK_RELATIVE
CONTROL_SCHEMA_VERSION = "revision-control-1.0"
TIER_VALUES = {"T0", "T1", "T2"}


class RevisionControlError(ValueError):
    """A revision request cannot safely advance."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _control_default() -> dict[str, Any]:
    return {"schema_version": CONTROL_SCHEMA_VERSION, "active_change": None, "history": []}


def _load_control(project: Path) -> dict[str, Any]:
    path = project / CONTROL_RELATIVE
    if not path.is_file():
        return _control_default()
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schema_version") != CONTROL_SCHEMA_VERSION:
        raise RevisionControlError("change_control.json 结构无效")
    if "active_change" not in value or "history" not in value or not isinstance(value["history"], list):
        raise RevisionControlError("change_control.json 缺少活动变更案或历史字段")
    return value


def _manifest_digest(manifest: dict[str, Any]) -> str:
    return sha256_json(manifest)


def _within_project(project: Path, value: str | Path) -> tuple[Path, str]:
    resolved = project_path(project, str(value)).resolve()
    try:
        relative = resolved.relative_to(project)
    except ValueError as exc:
        raise RevisionControlError(f"候选文件必须位于项目内：{resolved}") from exc
    if not resolved.is_file():
        raise RevisionControlError(f"候选文件不存在或不是普通文件：{resolved}")
    return resolved, relative.as_posix()


def _destination_within_project(project: Path, value: str | Path) -> tuple[Path, str]:
    resolved = project_path(project, str(value)).resolve()
    try:
        relative = resolved.relative_to(project)
    except ValueError as exc:
        raise RevisionControlError(f"候选目标必须位于项目内：{resolved}") from exc
    if resolved.exists():
        raise RevisionControlError(f"T0 候选目标已存在，拒绝覆盖：{resolved}")
    return resolved, relative.as_posix()


def _generation_segments(payload: Any) -> list[dict[str, Any]] | None:
    """Return actual prompt segments, or None when this is not a segmented prompt."""
    if not isinstance(payload, dict):
        return None
    if "generation_segments" in payload:
        rows = payload["generation_segments"]
    elif isinstance(payload.get("creative_sections"), list) and payload["creative_sections"]:
        sections = payload["creative_sections"]
        if any(not isinstance(section, dict) or not isinstance(section.get("generation_segments"), list)
               for section in sections):
            raise RevisionControlError("视频提示词创作段缺 generation_segments；材料分组不能代替生成段")
        rows = [row for section in sections for row in section["generation_segments"]]
    else:
        return None
    if (not isinstance(rows, list) or not rows
            or any(not isinstance(row, dict) or not isinstance(row.get("segment_id"), str)
                   or not row["segment_id"].strip() for row in rows)):
        raise RevisionControlError("视频提示词 generation_segments/segment_id 缺失或无效")
    ids = [row["segment_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise RevisionControlError("视频提示词 generation_segments 含重复 segment_id")
    # A materials/grouping object is not a video generation segment. Require its
    # submitted prompt body using the same body resolver as the existing adapter.
    try:
        from .prompt_body import submitted_prompt_body
        for row in rows:
            if not submitted_prompt_body(row).strip():
                raise ValueError("empty prompt")
    except (ValueError, TypeError, KeyError) as exc:
        raise RevisionControlError("generation_segments 必须含真实提示词正文，不能把材料组登记为生成段") from exc
    return rows


def _validate_segment_reconciliation(project: Path, baseline: dict[str, Any], candidate: Any) -> None:
    """Bind any stable segment-ID replacement to the actual baseline bytes and reasons."""
    if not isinstance(baseline, dict):
        return
    old_path = project_path(project, str(baseline.get("path_or_url") or ""))
    if not old_path.is_file():
        raise RevisionControlError("视频提示词旧版本缺失；生成段续跑基线未知")
    if not baseline.get("checksum") or sha256_file(old_path) != baseline.get("checksum"):
        raise RevisionControlError("视频提示词旧版本与活动确认 hash 不一致；生成段续跑基线已过期")
    try:
        old_payload = read_json(old_path)
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
    # A first segmentation has no prior generation IDs to resume or replace.
    if not old_ids:
        return
    added, removed = sorted(set(new_ids) - set(old_ids)), sorted(set(old_ids) - set(new_ids))
    old_by_id = {row["segment_id"]: row for row in old_rows or []}
    new_by_id = {row["segment_id"]: row for row in new_rows}
    common = set(old_by_id) & set(new_by_id)
    old_common_order = [ident for ident in old_ids if ident in common]
    new_common_order = [ident for ident in new_ids if ident in common]
    order_changed = old_common_order != new_common_order
    from .quality_evidence import source_shot_ids
    coverage_fields = ("source_shot_ids", "source_dialogue_ids", "source_paragraph_ids")
    coverage_changes = []
    for segment_id in sorted(common):
        for field in coverage_fields:
            if field == "source_shot_ids":
                before, after = source_shot_ids(old_by_id[segment_id]) or None, source_shot_ids(new_by_id[segment_id]) or None
            else:
                before, after = old_by_id[segment_id].get(field), new_by_id[segment_id].get(field)
            for value in (before, after):
                if value is not None and (not isinstance(value, list)
                        or any(not isinstance(ident, str) or not ident for ident in value)
                        or len(value) != len(set(value))):
                    raise RevisionControlError(f"{segment_id} 的 {field} 覆盖声明无效；不猜镜头/对白范围")
            if before != after:
                coverage_changes.append({"segment_id": segment_id, "field": field,
                                         "before": before, "after": after})
    from .prompt_body import submitted_prompt_body
    def body_signature(row: dict[str, Any]) -> str:
        # Only whitespace layout is normalized. Every punctuation mark remains
        # content because punctuation can reverse duration, direction, or negation.
        return " ".join(submitted_prompt_body(row).split())
    prompt_changes = []
    for segment_id in sorted(common):
        before, after = body_signature(old_by_id[segment_id]), body_signature(new_by_id[segment_id])
        if before != after:
            prompt_changes.append({"segment_id": segment_id,
                "before_sha256": hashlib.sha256(submitted_prompt_body(old_by_id[segment_id]).encode()).hexdigest(),
                "after_sha256": hashlib.sha256(submitted_prompt_body(new_by_id[segment_id]).encode()).hexdigest()})
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
            if (not order_changed or change.get("before") != old_ids
                    or change.get("after") != new_ids or seen_order):
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


def _current_by_logical(manifest: dict[str, Any], logical_id: str) -> dict[str, Any]:
    found = [
        item for item in manifest.get("artifacts", [])
        if item.get("logical_id") == logical_id and item.get("status") == "current"
    ]
    if len(found) != 1:
        raise RevisionControlError(f"{logical_id} 必须恰有一个 current 产物，实际 {len(found)} 个")
    return found[0]


def _dependency_closure(manifest: dict[str, Any], roots: Iterable[str]) -> list[str]:
    return sorted(dependency_closure(manifest, {str(item) for item in roots}))


def _assert_active(control: dict[str, Any], change_id: str) -> dict[str, Any]:
    active = control.get("active_change")
    if not isinstance(active, dict) or active.get("change_id") != change_id:
        raise RevisionControlError(f"活动变更案不存在：{change_id}")
    return active


def _assert_manifest_head(change: dict[str, Any], manifest: dict[str, Any]) -> None:
    expected = change.get("manifest_sha256")
    actual = _manifest_digest(manifest)
    if expected != actual:
        raise RevisionControlError("产物清单已变化，必须重新 inspect/open 变更案")


def _append_confirmation(state: dict[str, Any], receipt: dict[str, Any]) -> None:
    state.setdefault("user_confirmations", []).append(receipt)


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
) -> dict[str, Any]:
    """Write a durable intent, then replace JSON targets and their state view.

    `commit_intent.json` is the one-way decision point. Recovery always rolls
    forward when it exists; without it no authoritative file has been touched.
    `fail_after` is only used by fault-injection tests.
    """
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
                from .project_state import normalize_project_state, STATE_KEYS
                value = normalize_project_state(raw_state)
                # Legacy schema/ID-object decoding is read compatibility, not
                # permission to coerce malformed known business fields.
                for key in STATE_KEYS:
                    if key in raw_state and key not in {"schema_version", "current_artifacts"}:
                        value[key] = raw_state[key]
                validate_project_state(value)
            value = reconcile_current_artifacts(value, manifest)
            validate_project_state(value)
            state_view = value
            updates[target] = ({**raw_state, "current_artifacts": value["current_artifacts"]}
                               if preserve_state_extensions else value)
    # Keep the human view inside the same recoverable transaction as its JSON.
    state_value = updates.get(project / MANAGE / "project_state.json")
    if state_value is not None:
        updates[project / MANAGE / "project_state.md"] = render_project_state_markdown(state_view or state_value).encode("utf-8")
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
    write_json(tx_dir / "transaction.json", transaction)
    write_json(tx_dir / "commit_intent.json", {
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
    write_json(tx_dir / "result.json", {"state": "committed", "committed_at": utc_now()})
    return {"transaction_id": transaction_id, "state": "committed", "files": [item["path"] for item in records]}


def recover_transactions(project: Path) -> list[dict[str, Any]]:
    """Finish committed intents and safely close prepared-but-undecided work."""
    project = project.resolve()
    root = project / TRANSACTION_ROOT
    if not root.is_dir():
        return []
    outcomes: list[dict[str, Any]] = []
    with file_lock(project / LOCK_RELATIVE):
        for tx_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            transaction_path = tx_dir / "transaction.json"
            if not transaction_path.is_file():
                continue
            transaction = read_json(transaction_path)
            result_path = tx_dir / "result.json"
            if result_path.is_file() and read_json(result_path).get("state") == "committed":
                continue
            intent_path = tx_dir / "commit_intent.json"
            if not intent_path.is_file():
                write_json(result_path, {"state": "aborted", "recovered_at": utc_now()})
                outcomes.append({"transaction_id": transaction.get("transaction_id"), "state": "aborted"})
                continue
            # Check every target before replacing any: older code could accept
            # a later write while this intent was pending. Preserve that work.
            recovery_files = []
            for record in transaction.get("files") or []:
                relative = Path(str(record["path"]))
                target = project / relative
                after_path = tx_dir / "after" / relative
                if (relative.is_absolute() or not target.resolve().is_relative_to(project)
                        or not after_path.resolve().is_relative_to((tx_dir / "after").resolve())):
                    raise RevisionControlError(f"事务恢复路径越出项目：{relative}")
                if not after_path.is_file() or sha256_file(after_path) != record.get("after_sha256"):
                    raise RevisionControlError(f"事务恢复失败，暂存内容损坏：{relative}")
                current_digest = sha256_file(target) if target.is_file() else None
                allowed = {record.get("after_sha256")}
                allowed.add(record.get("before_sha256") if record.get("before_exists") else None)
                if (target.exists() and not target.is_file()) or current_digest not in allowed:
                    raise RevisionControlError(f"事务目标已被其他任务更新，拒绝覆盖；请保留现场核对：{relative}")
                recovery_files.append((target, after_path.read_bytes()))
            for target, content in recovery_files:
                write_bytes(target, content)
            write_json(result_path, {"state": "committed", "recovered_at": utc_now()})
            outcomes.append({"transaction_id": transaction.get("transaction_id"), "state": "committed"})
    return outcomes


def inspect_change(project: Path, logical_ids: list[str], candidate: str | None = None) -> dict[str, Any]:
    """Return a conservative tier floor. Only proven equivalence receives T0."""
    project = project.resolve()
    manifest = load_manifest(project)
    if not logical_ids:
        raise RevisionControlError("inspect 至少需要一个 --target-logical-id")
    bases = [_current_by_logical(manifest, item) for item in logical_ids]
    tier = "T2"
    reason = "semantic_review_required"
    if candidate is not None and len(bases) == 1:
        candidate_path, _ = _within_project(project, candidate)
        source_raw = str(bases[0].get("path_or_url") or "")
        source = project_path(project, source_raw).resolve()
        if source.is_file():
            same = False
            if source.suffix.lower() == ".json" and candidate_path.suffix.lower() == ".json":
                try:
                    same = sha256_json(read_json(source)) == sha256_json(read_json(candidate_path))
                except (OSError, json.JSONDecodeError):
                    same = False
            elif source.suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml"}:
                same = normalized_text_sha256(source) == normalized_text_sha256(candidate_path)
            else:
                same = sha256_file(source) == sha256_file(candidate_path)
            if same:
                tier, reason = "T0", "proven_equivalent_candidate"
    freeze = [] if tier == "T0" else _dependency_closure(manifest, logical_ids)
    return {
        "tier": tier,
        "reason": reason,
        "targets": [{"logical_id": item.get("logical_id"), "artifact_id": item.get("artifact_id"), "revision": item.get("revision")} for item in bases],
        "frozen_logical_ids": freeze,
        "manifest_sha256": _manifest_digest(manifest),
    }


def open_change(
    project: Path,
    *,
    change_id: str,
    tier: str,
    logical_ids: list[str],
    reason: str,
    evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    if tier not in TIER_VALUES:
        raise RevisionControlError(f"未知 tier：{tier}")
    if len(set(logical_ids)) != 1:
        raise RevisionControlError("最小返工机制一次只处理一个目标逻辑 ID；请拆分为独立变更案")
    project = project.resolve()
    with project_lock(project):
        control = _load_control(project)
        if control.get("active_change") is not None:
            raise RevisionControlError("每个项目同时只能有一个活动变更案")
        manifest = load_manifest(project)
        bases = [_current_by_logical(manifest, item) for item in logical_ids]
        change = {
            "change_id": change_id,
            "tier": tier,
            "reason": reason,
            "evidence_refs": list(evidence_refs or []),
            "targets": [
                {"logical_id": item["logical_id"], "artifact_id": item["artifact_id"], "revision": item.get("revision"), "checksum": item.get("checksum")}
                for item in bases
            ],
            "frozen_logical_ids": [] if tier == "T0" else _dependency_closure(manifest, logical_ids),
            "impact_gate": "not_required" if tier == "T0" else "awaiting_confirmation",
            "status": "awaiting_candidate" if tier == "T0" else "awaiting_impact_confirmation",
            "manifest_sha256": _manifest_digest(manifest),
            "opened_at": utc_now(),
            "candidate_receipt": None,
            "review": None,
            "paid_approvals": [],
        }
        control["active_change"] = change
        return _commit_json_transaction_unlocked(project, {project / CONTROL_RELATIVE: control}, reason="open revision change")


def materialize_t0(
    project: Path,
    *,
    change_id: str,
    source_path: str,
    destination_path: str,
    transform: str,
) -> dict[str, Any]:
    """Create a new mechanically equivalent candidate without invoking an Agent."""
    if transform not in {"canonical-json", "copy"}:
        raise RevisionControlError("T0 仅支持 canonical-json 或 copy")
    project = project.resolve()
    with project_lock(project):
        control = _load_control(project)
        change = _assert_active(control, change_id)
        if change.get("tier") != "T0" or change.get("status") != "awaiting_candidate":
            raise RevisionControlError("materialize-t0 仅适用于等待候选的 T0 变更案")
        source, _ = _within_project(project, source_path)
        destination, relative = _destination_within_project(project, destination_path)
        if transform == "canonical-json":
            if source.suffix.lower() != ".json":
                raise RevisionControlError("canonical-json 仅支持 JSON 文件")
            try:
                value = read_json(source)
            except (OSError, json.JSONDecodeError) as exc:
                raise RevisionControlError(f"T0 源 JSON 无法解析：{source}") from exc
            write_json(destination, value)
            if sha256_json(value) != sha256_json(read_json(destination)):
                raise RevisionControlError("T0 JSON 规范化后语义摘要不一致")
        else:
            write_bytes(destination, source.read_bytes())
            if sha256_file(source) != sha256_file(destination):
                raise RevisionControlError("T0 文件复制后摘要不一致")
        return {"path": relative, "transform": transform, "sha256": sha256_file(destination), "bytes": destination.stat().st_size}


def confirm_impact(project: Path, *, change_id: str, approval_ref: str) -> dict[str, Any]:
    if not approval_ref.strip():
        raise RevisionControlError("影响确认必须提供 approval_ref")
    project = project.resolve()
    with project_lock(project):
        control = _load_control(project)
        change = _assert_active(control, change_id)
        if change.get("status") != "awaiting_impact_confirmation":
            raise RevisionControlError("当前变更案不等待影响确认")
        manifest = load_manifest(project)
        _assert_manifest_head(change, manifest)
        receipt = {"kind": "revision-impact", "change_id": change_id, "approval_ref": approval_ref, "at": utc_now()}
        change["impact_gate"] = "confirmed"
        change["impact_confirmation"] = receipt
        change["status"] = "awaiting_candidate"
        state = load_project_state(project)
        _append_confirmation(state, receipt)
        return _commit_json_transaction_unlocked(project, {
            project / CONTROL_RELATIVE: control,
            project / MANAGE / "project_state.json": state,
        }, reason="confirm revision impact")


def record_candidate(
    project: Path,
    *,
    change_id: str,
    artifact_id: str,
    logical_id: str,
    artifact_type: str,
    candidate_path: str,
    revision: str,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    project = project.resolve()
    with project_lock(project):
        control = _load_control(project)
        change = _assert_active(control, change_id)
        if change.get("status") != "awaiting_candidate":
            raise RevisionControlError("当前变更案不接受候选")
        if logical_id not in {item["logical_id"] for item in change.get("targets") or []}:
            raise RevisionControlError("候选不属于活动变更案的目标产物")
        manifest = load_manifest(project)
        _assert_manifest_head(change, manifest)
        if any(item.get("artifact_id") == artifact_id for item in manifest.get("artifacts") or []):
            raise RevisionControlError(f"候选 artifact_id 已存在：{artifact_id}")
        source, relative = _within_project(project, candidate_path)
        baseline_ref = next(item for item in change["targets"] if item["logical_id"] == logical_id)
        prior_item = next(item for item in manifest["artifacts"] if item.get("artifact_id") == baseline_ref["artifact_id"])
        if logical_id == "video.prompt":
            _validate_segment_reconciliation(project, {**baseline_ref, "path_or_url": prior_item.get("path_or_url")},
                read_json(source) if source.suffix.lower() == ".json" else None)
        if source.suffix.lower() == '.json':
            payload = read_json(source)
            if isinstance(payload, dict) and 'revision' in payload and payload['revision'] != revision:
                raise RevisionControlError('候选 JSON revision 与登记修订不一致')
        checksum = sha256_file(source)
        receipt = {
            "artifact_id": artifact_id,
            "logical_id": logical_id,
            "path": relative,
            "revision": revision,
            "bytes": source.stat().st_size,
            "sha256": checksum,
            "base_manifest_sha256": change["manifest_sha256"],
            "recorded_at": utc_now(),
        }
        effective_depends_on = list(depends_on) if depends_on is not None else list(prior_item.get("depends_on", []))
        manifest.setdefault("artifacts", []).append({
            "artifact_id": artifact_id,
            "logical_id": logical_id,
            "type": artifact_type,
            "revision": revision,
            "status": "candidate",
            "path_or_url": relative,
            "depends_on": effective_depends_on,
            "checksum": checksum,
            "revision_managed": True,
            "user_approved": False,
            "created_at": utc_now(),
        })
        change["candidate_receipt"] = receipt
        change["manifest_sha256"] = _manifest_digest(manifest)
        change["status"] = "awaiting_candidate_confirmation" if change.get("tier") == "T0" else "awaiting_review"
        return _commit_json_transaction_unlocked(project, {
            project / CONTROL_RELATIVE: control,
            project / MANAGE / "artifact_manifest.json": manifest,
        }, reason="record revision candidate")


def _validate_delivery(project: Path, payload: dict) -> None:
    if __package__ == 'scripts.core':
        from ..canvas_readback import validate_submission
    else:
        from canvas_readback import validate_submission
    try:
        validate_submission(payload, project)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise RevisionControlError(f'交付快照当前参考检查失败：{exc}') from exc


def _assert_review_current(project: Path, change: dict[str, Any]) -> None:
    """Use the existing change receipt, never treat an old PASS as a new review."""
    candidate = change.get('candidate_receipt') or {}
    source, _ = _within_project(project, candidate['path'])
    if source.suffix.lower() == '.json':
        payload = read_json(source)
        if isinstance(payload, dict) and 'target_url' in payload and 'drafts' in payload:
            _validate_delivery(project, payload)
    # Equivalent copies waive repeated human review, never current input checks.
    if change.get('tier') == 'T0':
        return
    review = change.get('review') or {}
    fields = ('artifact_id', 'revision', 'sha256')
    if (review.get('verdict') != 'approved'
            or review.get('change_id') != change.get('change_id')
            or any(not candidate.get(k) or review.get(k) != candidate[k] for k in fields)):
        raise RevisionControlError('审核凭据未绑定当前候选 artifact/revision/hash；请重新审核，不复用旧 PASS')
    if review.get('report_sha256'):
        report, _ = _within_project(project, review['report_ref'])
        if not report.is_file() or sha256_file(report) != review['report_sha256']:
            raise RevisionControlError('审核报告内容漂移；不能复用旧 PASS')


def record_review(project: Path, *, change_id: str, verdict: str, report_ref: str) -> dict[str, Any]:
    if verdict not in {"approved", "blocked"}:
        raise RevisionControlError("审核结论必须为 approved 或 blocked")
    if not report_ref.strip():
        raise RevisionControlError("审核必须提供准确 report_ref")
    project = project.resolve()
    with project_lock(project):
        control = _load_control(project)
        change = _assert_active(control, change_id)
        if change.get("status") != "awaiting_review":
            raise RevisionControlError("当前变更案不等待审核")
        candidate = change.get('candidate_receipt') or {}
        source, _ = _within_project(project, str(candidate.get('path') or ''))
        if not candidate.get('revision') or sha256_file(source) != candidate.get('sha256'):
            raise RevisionControlError('候选版本缺失或审核前发生漂移，不能登记 PASS；请重新登记与审核')
        receipt = {"kind": "revision-review", "change_id": change_id, "verdict": verdict,
                   "report_ref": report_ref, "at": utc_now(),
                   **{k: candidate[k] for k in ('artifact_id', 'revision', 'sha256')}}
        if verdict == 'approved' and source.suffix.lower() == '.json':
            payload = read_json(source)
            if isinstance(payload, dict) and 'target_url' in payload and 'drafts' in payload:
                _validate_delivery(project, payload)
                report_path, relative = _within_project(project, report_ref)
                report = read_json(report_path)
                native = report.get('native_reference_checks', {})
                expected_nodes = {row['node_id'] for row in payload['drafts']}
                native_ok = isinstance(native, dict) and all(
                    native.get(row['node_id']) in ({'passed', 'not_applicable'}
                        if row.get('ordered_media') == [] else {'passed'})
                    for row in payload['drafts'])
                if (report.get('ok') is not True or not payload.get('revision')
                        or report.get('revision') != payload['revision']
                        or report.get('frozen_sha256') != sha256_json(payload)
                        or not isinstance(report.get('submission_checks'), dict)
                        or report['submission_checks'].get('ok') is not True
                        or not native_ok or set(native) != expected_nodes):
                    raise RevisionControlError('交付快照须绑定本修订/hash的完整参考检查和实际实体回读；旧 PASS 不能复用')
                receipt.update(report_ref=relative, report_sha256=sha256_file(report_path))
        change["review"] = receipt
        change["status"] = "awaiting_candidate_confirmation" if verdict == "approved" else "blocked"
        state = load_project_state(project)
        state["review_report"] = receipt
        return _commit_json_transaction_unlocked(project, {
            project / CONTROL_RELATIVE: control,
            project / MANAGE / "project_state.json": state,
        }, reason="record revision review")


def confirm_candidate(
    project: Path,
    *,
    change_id: str,
    approval_ref: str,
    scope_description: str = "",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not approval_ref.strip():
        raise RevisionControlError("候选确认必须提供 approval_ref")
    from .candidate_test import require_production
    project = project.resolve()
    with project_lock(project):
        require_production(project)
        control = _load_control(project)
        change = _assert_active(control, change_id)
        if change.get("status") != "awaiting_candidate_confirmation":
            raise RevisionControlError("当前变更案不等待候选确认")
        _assert_review_current(project, change)
        candidate = change.get("candidate_receipt") or {}
        source, _ = _within_project(project, str(candidate.get("path") or ""))
        if sha256_file(source) != candidate.get("sha256"):
            raise RevisionControlError("候选文件在审核后发生漂移，必须重新登记与审核")
        manifest = load_manifest(project)
        _assert_manifest_head(change, manifest)
        candidate_item = next((item for item in manifest.get("artifacts", [])
                               if item.get("artifact_id") == candidate.get("artifact_id")), None)
        if (candidate_item is None or candidate_item.get("status") != "candidate"
                or candidate_item.get("logical_id") != candidate.get("logical_id")
                or candidate_item.get("revision") != candidate.get("revision")
                or candidate_item.get("checksum") != candidate.get("sha256")
                or project_path(project, candidate_item.get("path_or_url", "")) != source):
            raise RevisionControlError("登记候选与当前审核凭据的 ID/逻辑产物/修订/路径/hash 不一致")
        try:
            submission_sha256 = validate_candidate_submission(project, candidate_item, manifest)
            if candidate_item.get("logical_id") in {"narrative.package", "storyboard.package", "video.prompt"} and not submission_sha256:
                raise ValueError("旧稿件/未识别结构不能正式确认；仅保留历史读取")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise RevisionControlError(f"正式稿件提交前校验失败：{exc}") from exc
        if candidate_item.get("type") == "visual-supplement-image":
            try:
                confirmation_scope.image_usage_shots(usage, required=True)
            except ValueError as exc:
                raise RevisionControlError(str(exc)) from exc
        elif usage is not None:
            raise RevisionControlError("usage 仅用于独立分镜图片确认；不扩展其他产物许可")
        receipt = confirmation_scope.new_receipt(
            confirmation_scope.SCOPE_ARTIFACT,
            description=scope_description,
            extra={
                "kind": "revision-candidate",
                "change_id": change_id,
                "artifact_id": candidate.get("artifact_id"),
                "sha256": candidate.get("sha256"),
                "approval_ref": approval_ref,
                "at": utc_now(),
            },
        )
        if usage is not None:
            receipt["usage"] = json.loads(json.dumps(usage))
        change["candidate_confirmation"] = receipt
        change["status"] = "commit_ready"
        state = load_project_state(project)
        _append_confirmation(state, receipt)
        return _commit_json_transaction_unlocked(project, {
            project / CONTROL_RELATIVE: control,
            project / MANAGE / "project_state.json": state,
        }, reason="confirm revision candidate")


def record_scoped_confirmation(
    project: Path,
    *,
    change_id: str,
    scope: str,
    approval_ref: str,
    scope_description: str = "",
    scoped_ids: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """登记段落/对白范围的用户确认，但不推进整稿采用。

    逐段审稿的确认必须单独留痕，且永远不能当作整稿凭据：本函数只写确认记录，
    不改变变更案状态，后续整稿仍须走 confirm_candidate。返回已登记的确认记录本身，
    事务凭据见 change_control.json 与 revision_transactions/。
    """
    if scope not in {confirmation_scope.SCOPE_SEGMENT, confirmation_scope.SCOPE_DIALOGUE}:
        raise RevisionControlError("局部确认的 scope 只能是 segment 或 dialogue")
    if not approval_ref.strip():
        raise RevisionControlError("确认记录必须提供 approval_ref")
    receipt = confirmation_scope.new_receipt(
        scope,
        description=scope_description,
        extra={
            "kind": "revision-scoped-confirmation",
            "change_id": change_id,
            "approval_ref": approval_ref,
            "at": utc_now(),
            **(scoped_ids or {}),
        },
    )
    from .candidate_test import require_production
    project = project.resolve()
    with project_lock(project):
        require_production(project)
        control = _load_control(project)
        change = _assert_active(control, change_id)
        change.setdefault("scoped_confirmations", []).append(receipt)
        state = load_project_state(project)
        _append_confirmation(state, receipt)
        _commit_json_transaction_unlocked(project, {
            project / CONTROL_RELATIVE: control,
            project / MANAGE / "project_state.json": state,
        }, reason="record scoped confirmation")
        return dict(receipt)


def commit_change(project: Path, *, change_id: str) -> dict[str, Any]:
    from .candidate_test import require_production
    project = project.resolve()
    with project_lock(project):
        require_production(project)
        control = _load_control(project)
        change = _assert_active(control, change_id)
        if change.get("status") != "commit_ready":
            raise RevisionControlError("当前变更案尚未具备提交条件")
        _assert_review_current(project, change)
        if not confirmation_scope.is_whole_artifact(change.get("candidate_confirmation")):
            raise RevisionControlError("只有整稿范围的用户确认才能提交采用")
        manifest = load_manifest(project)
        _assert_manifest_head(change, manifest)
        candidate = change.get("candidate_receipt") or {}
        source, _ = _within_project(project, str(candidate.get("path") or ""))
        if sha256_file(source) != candidate.get("sha256"):
            raise RevisionControlError("候选文件提交前发生漂移")
        candidate_id = candidate.get("artifact_id")
        candidate_item = next((item for item in manifest.get("artifacts") or [] if item.get("artifact_id") == candidate_id), None)
        if candidate_item is None or candidate_item.get("status") != "candidate":
            raise RevisionControlError("候选产物登记状态无效")
        try:
            submission_sha256 = validate_candidate_submission(project, candidate_item, manifest)
            if candidate_item.get("logical_id") in {"narrative.package", "storyboard.package", "video.prompt"} and not submission_sha256:
                raise ValueError("旧稿件/未识别结构不能正式提交；仅保留历史读取")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise RevisionControlError(f"正式稿件采用前校验失败：{exc}") from exc
        report = mark_impacted_artifacts_stale(
            manifest,
            {str(candidate.get("logical_id"))},
            reason=f"前序修订 {change_id}",
            revision=str(candidate_item.get("revision") or ""),
        )
        for item in manifest.get("artifacts") or []:
            if item.get("logical_id") == candidate.get("logical_id") and item.get("artifact_id") != candidate_id and item.get("status") in {"current", "stale"}:
                item["status"] = "superseded"
                item["superseded_at"] = utc_now()
        candidate_item["status"] = "current"
        candidate_item["user_approved"] = True
        stale_downstream = sorted(set(report.stale_ids) - {str(candidate.get("logical_id"))})
        state = load_project_state(project)
        state = reconcile_current_artifacts(state, manifest, replacements={
            item["artifact_id"]: candidate_id for item in change["targets"]
            if item["logical_id"] == candidate.get("logical_id")
        })
        completed = dict(change)
        completed["status"] = "completed"
        completed["committed_at"] = utc_now()
        completed["stale_downstream"] = stale_downstream
        control["history"].append(completed)
        control["active_change"] = None
        return _commit_json_transaction_unlocked(project, {
            project / CONTROL_RELATIVE: control,
            project / MANAGE / "artifact_manifest.json": manifest,
            project / MANAGE / "project_state.json": state,
        }, reason="commit revision candidate")


def cancel_change(project: Path, *, change_id: str, reason: str) -> dict[str, Any]:
    project = project.resolve()
    with project_lock(project):
        control = _load_control(project)
        change = _assert_active(control, change_id)
        if change.get("status") in {"commit_ready", "completed"}:
            raise RevisionControlError("确认提交后不能取消；请建立反向变更案")
        cancelled = dict(change)
        cancelled.update({"status": "cancelled", "cancel_reason": reason, "cancelled_at": utc_now()})
        control["history"].append(cancelled)
        control["active_change"] = None
        return _commit_json_transaction_unlocked(project, {project / CONTROL_RELATIVE: control}, reason="cancel revision change")


def record_paid_approval(
    project: Path,
    *,
    change_id: str,
    paid_action_id: str,
    approval_ref: str,
    targets: list[str],
    quantity: int,
) -> dict[str, Any]:
    # 项目裁定（2026-09-21）：资产图生成不挂变更案，因此本项目没有调用方，
    # 资产费用门槛仍在资产网页"用户点授权生成"那一下。机制保留给需要预算门禁的
    # 项目，删除会被当成缺陷重新提，接线会打断资产与导演规划的并行流程。
    if not approval_ref.strip() or quantity <= 0:
        raise RevisionControlError("付费授权需要 approval_ref 和正数量")
    from .candidate_test import require_production
    project = project.resolve()
    with project_lock(project):
        require_production(project)
        control = _load_control(project)
        change = _assert_active(control, change_id)
        approval = {
            "paid_action_id": paid_action_id,
            "approval_ref": approval_ref,
            "targets": sorted(set(targets)),
            "quantity": quantity,
            "remaining": quantity,
            "origin_verification": "policy_only",
            "recorded_at": utc_now(),
        }
        change.setdefault("paid_approvals", []).append(approval)
        state = load_project_state(project)
        _append_confirmation(state, {"kind": "revision-paid", "change_id": change_id, **approval})
        return _commit_json_transaction_unlocked(project, {
            project / CONTROL_RELATIVE: control,
            project / MANAGE / "project_state.json": state,
        }, reason="record paid approval")


def guard_action(
    project: Path,
    *,
    logical_ids: list[str],
    change_id: str | None = None,
    paid_action_id: str | None = None,
) -> dict[str, Any]:
    """Fail closed when a requested action touches a frozen branch."""
    if paid_action_id:
        from .candidate_test import require_production
        require_production(project)
    project = project.resolve()
    control = _load_control(project)
    active = control.get("active_change")
    if not isinstance(active, dict):
        if paid_action_id:
            raise RevisionControlError("付费动作必须属于活动变更案并有明确授权")
        return {"allowed": True, "reason": "no_active_change"}
    touched = sorted(set(logical_ids) & set(active.get("frozen_logical_ids") or []))
    if touched and change_id != active.get("change_id"):
        raise RevisionControlError(f"动作触及冻结分支：{', '.join(touched)}")
    if paid_action_id:
        approval = next((item for item in active.get("paid_approvals") or [] if item.get("paid_action_id") == paid_action_id), None)
        if approval is None or approval.get("remaining", 0) <= 0:
            raise RevisionControlError("付费动作缺少可用的用户授权记录")
    return {"allowed": True, "change_id": active.get("change_id"), "frozen_intersection": touched}


def show_change(project: Path) -> dict[str, Any]:
    project = project.resolve()
    return _load_control(project)


def discard_uncommitted_transaction(project: Path, transaction_id: str) -> None:
    """Test helper: remove only a transaction that never received commit intent."""
    tx_dir = _transaction_dir(project.resolve(), transaction_id)
    if (tx_dir / "commit_intent.json").exists():
        raise RevisionControlError("已决定的事务不能清理，只能 recover")
    shutil.rmtree(tx_dir)
