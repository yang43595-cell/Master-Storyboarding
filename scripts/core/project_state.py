"""Minimal stage state used by the lightweight execution flow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .io import write_json, write_text


SCHEMA_VERSION = "stage-1.0"
STATE_KEYS = (
    "schema_version", "current_stage", "project_status", "finalization",
    "current_artifacts", "user_confirmations", "review_report", "accepted_issues", "next_stage",
)


def new_project_state(*, current_stage: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "current_stage": current_stage,
        "project_status": "active",
        "finalization": None,
        "current_artifacts": [],
        "user_confirmations": [],
        "review_report": None,
        "accepted_issues": [],
        "next_stage": None,
    }


def _artifact_ids(value: Any, *, legacy: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("current_artifacts 必须是产物 ID 数组")
    ids = []
    for item in value:
        artifact_id = item.get("artifact_id") if legacy and isinstance(item, dict) else item
        if not isinstance(artifact_id, str) or not artifact_id.strip() or artifact_id != artifact_id.strip():
            raise ValueError("current_artifacts 每项必须是非空产物 ID；旧对象必须包含合法 artifact_id")
        if artifact_id in ids:
            raise ValueError(f"current_artifacts 产物 ID 重复：{artifact_id}")
        ids.append(artifact_id)
    return ids


def normalize_project_state(value: dict[str, Any]) -> dict[str, Any]:
    """Read old project facts without writing or carrying runtime fields forward."""
    if value.get("schema_version") == SCHEMA_VERSION:
        state = new_project_state(current_stage=str(value.get("current_stage") or ""))
        for key in STATE_KEYS:
            if key in value:
                state[key] = value[key]
        state["current_artifacts"] = _artifact_ids(state["current_artifacts"], legacy=True)
        return state
    state = new_project_state(current_stage=str(value.get("current_stage") or value.get("active_stage") or ""))
    state["current_artifacts"] = _artifact_ids(
        value["current_artifacts"] if "current_artifacts" in value else value.get("active_artifact_ids", []),
        legacy=True,
    )
    state["user_confirmations"] = value.get("user_confirmations") or []
    state["project_status"] = str(value.get("project_status") or "active")
    state["finalization"] = value.get("finalization") if isinstance(value.get("finalization"), dict) else None
    state["review_report"] = value.get("review_report")
    state["accepted_issues"] = value.get("accepted_issues") or []
    state["next_stage"] = value.get("next_stage")
    return state


def load_project_state(project_root: Path) -> dict[str, Any]:
    path = project_root / "00_项目管理" / "project_state.json"
    if not path.is_file():
        return new_project_state()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("project_state.json 必须是对象")
    return normalize_project_state(value)


def validate_project_state(state: dict[str, Any]) -> None:
    if set(state) != set(STATE_KEYS):
        extra = sorted(set(state) - set(STATE_KEYS))
        missing = sorted(set(STATE_KEYS) - set(state))
        raise ValueError(f"project_state 字段不符合最小契约：extra={extra}, missing={missing}")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version 必须为 {SCHEMA_VERSION}")
    if not isinstance(state.get("current_stage"), str):
        raise ValueError("current_stage 必须是字符串")
    if not isinstance(state.get("project_status"), str):
        raise ValueError("project_status 必须是字符串")
    if state.get("finalization") is not None and not isinstance(state["finalization"], dict):
        raise ValueError("finalization 必须是对象或 null")
    for field in ("current_artifacts", "user_confirmations", "accepted_issues"):
        if not isinstance(state.get(field), list):
            raise ValueError(f"{field} 必须是数组")
    _artifact_ids(state["current_artifacts"])
    if state.get("review_report") is not None and not isinstance(state["review_report"], dict):
        raise ValueError("review_report 必须是对象或 null")
    if state.get("next_stage") is not None and not isinstance(state["next_stage"], str):
        raise ValueError("next_stage 必须是字符串或 null")


def render_project_state_markdown(state: dict[str, Any]) -> str:
    validate_project_state(state)
    lines = [
        "# 项目状态", "",
        "> 本文件由 `project_state.json` 生成；JSON 是事实权威。", "",
        f"- 当前阶段：{state['current_stage'] or '未设置'}",
        f"- 项目状态：{state['project_status'] or '未设置'}",
        f"- 定稿记录：{(state['finalization'] or {}).get('record_path', '无')}",
        f"- 当前有效产物：{len(state['current_artifacts'])}",
        f"- 用户确认：{len(state['user_confirmations'])}",
        f"- 审核报告：{'有' if state['review_report'] else '无'}",
        f"- 已接受问题：{len(state['accepted_issues'])}",
        f"- 下一阶段：{state['next_stage'] or '未设置'}",
    ]
    final_edit = next((receipt for receipt in reversed(state["user_confirmations"])
                       if isinstance(receipt, dict)
                       and receipt.get("kind") == "final-edit-confirmation"
                       and receipt.get("status") == "user-confirmed"), None)
    if final_edit:
        lines.append("- 最终剪辑：用户已确认当前剪辑版；不等于各原始片段或上游稿重新确认")
        lines.append(f"- 最终剪辑确认记录：{final_edit.get('record_path') or '未提供'}")
        lines.append(f"- 最终剪辑文件：{final_edit.get('final_output_path') or '用户未提供准确路径；未独立核验成片'}")
    return "\n".join([*lines, ""])


def write_project_state(project_root: Path, state: dict[str, Any]) -> None:
    validate_project_state(state)
    from .artifacts import MANIFEST_PATH, load_manifest
    if (project_root / MANIFEST_PATH).is_file():
        state = reconcile_current_artifacts(state, load_manifest(project_root))
    manage = project_root / "00_项目管理"
    write_json(manage / "project_state.json", state)
    write_text(manage / "project_state.md", render_project_state_markdown(state))


def reconcile_current_artifacts(
    state: dict[str, Any],
    manifest: dict[str, Any],
    *,
    replacements: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Preserve the task's explicit selection within the manifest's valid IDs.

    A formally adopted revision can replace its selected baseline ID. Other
    current artifacts, confirmations and stage decisions are never added here.
    """
    from .artifacts import current_artifacts
    valid = {item["artifact_id"] for item in current_artifacts(manifest)}
    selected: list[str] = []
    for artifact_id in _artifact_ids(state["current_artifacts"]):
        replacement = (replacements or {}).get(artifact_id, artifact_id)
        if replacement in valid and replacement not in selected:
            selected.append(replacement)
    return {**state, "current_artifacts": selected}


def sync_current_artifacts(project_root: Path, manifest: dict[str, Any]) -> None:
    """Update an existing state after manifest validity changes; never create one."""
    path = project_root / "00_项目管理" / "project_state.json"
    if not path.is_file():
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("project_state.json 必须是对象")
    state = normalize_project_state(raw)
    synchronized = reconcile_current_artifacts(state, manifest)
    if synchronized != state:
        # Implicit validity synchronization is not a migration of legacy state.
        write_json(path, {**raw, "current_artifacts": synchronized["current_artifacts"]})
        write_text(project_root / "00_项目管理/project_state.md", render_project_state_markdown(synchronized))
