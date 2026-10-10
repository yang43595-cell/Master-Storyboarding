"""用户确认记录范围的唯一权威。

PROJECT_RULES「逐场创作与确认」只描述流程；字段语义、合法性判定和"能不能当整稿确认凭据"
都在本模块实现，规则与代码因此不会各写一套。

范围取值：

- `artifact`：本轮确认覆盖整份产物，可作为正式整稿交接凭据；
- `segment`：只确认某个创作段落，必须列出该段实际编号（镜头/对白/段落 id），
  不得作为正式整稿交接凭据；
- `dialogue`：只为对白核对，必须列出 dialogue_ids；
- 没有 `scope` 的老记录按整稿兼容，但一旦带任意局部 id 字段就不再算整稿；
- 其他字符串（含历史中文 scope）一律降级为 `legacy-text`，只作说明，不算整稿。

判定fail-closed：无法证明是整稿的，一律不算整稿。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCOPE_ARTIFACT = "artifact"
SCOPE_SEGMENT = "segment"
SCOPE_DIALOGUE = "dialogue"
SCOPE_VALUES = (SCOPE_ARTIFACT, SCOPE_SEGMENT, SCOPE_DIALOGUE)

KIND_ARTIFACT = "artifact"
KIND_SEGMENT = "segment"
KIND_DIALOGUE = "dialogue"
KIND_UNSCOPED = "unscoped"
KIND_LEGACY_TEXT = "legacy-text"

# 记录局部范围的实际编号；artifact_ids 是确认对象，不属于局部范围标记。
SCOPE_ID_FIELDS = (
    "dialogue_ids",
    "scene_ids",
    "shot_ids",
    "source_shot_ids",
    "paragraph_ids",
    "source_paragraph_ids",
    "segment_ids",
)

# 这些 kind 天生只覆盖对白，不能作为整稿确认。
DIALOGUE_RECEIPT_KINDS = {"dialogue-review", "dialogue-confirmation"}

# 没有 scope 的老记录要按整稿兼容，但至少得看出一条确认凭据；否则空对象、
# 事务回执之类的普通字典会被误当成整稿确认。
WHOLE_MARKER_FIELDS = ("artifact_ids", "artifact_id", "approval_ref", "confirmation_ref")


@dataclass(frozen=True)
class ScopeClassification:
    kind: str
    raw_scope: Any = None
    description: str = ""
    ids: dict[str, list[str]] = field(default_factory=dict)
    has_marker: bool = False
    scope_ids_present: bool = False

    @property
    def is_whole_artifact(self) -> bool:
        if self.scope_ids_present:
            return False
        if self.kind == KIND_ARTIFACT:
            return True
        if self.kind == KIND_UNSCOPED:
            return self.has_marker
        return False

    @property
    def scoped_ids(self) -> list[str]:
        return [item for values in self.ids.values() for item in values]


def _id_values(receipt: dict[str, Any]) -> dict[str, list[str]]:
    """收集合法的局部编号；值不合法时由 problems_for_write 报错。"""
    values: dict[str, list[str]] = {}
    for key in SCOPE_ID_FIELDS:
        raw = receipt.get(key)
        if isinstance(raw, list) and raw:
            values[key] = [str(item) for item in raw]
    return values


def _has_scope_ids(receipt: dict[str, Any]) -> bool:
    """只要出现局部编号字段（哪怕值写错），就不再允许当作整稿确认。"""
    return any(receipt.get(key) not in (None, "", [], {}) for key in SCOPE_ID_FIELDS)


def classify(receipt: Any) -> ScopeClassification:
    """按记录内容判断确认范围，不做任何猜测或自动升级。"""
    if not isinstance(receipt, dict):
        return ScopeClassification(KIND_LEGACY_TEXT)
    raw = receipt.get("scope")
    description = receipt.get("scope_description")
    description = description if isinstance(description, str) else ""
    ids = _id_values(receipt)
    local_ids = _has_scope_ids(receipt)
    marker = any(receipt.get(key) for key in WHOLE_MARKER_FIELDS)
    if receipt.get("kind") in DIALOGUE_RECEIPT_KINDS:
        return ScopeClassification(KIND_DIALOGUE, raw, description, ids, marker, local_ids)
    if raw is None:
        return ScopeClassification(KIND_UNSCOPED, raw, description, ids, marker, local_ids)
    if raw == SCOPE_ARTIFACT:
        return ScopeClassification(KIND_ARTIFACT, raw, description, ids, marker, local_ids)
    if raw == SCOPE_SEGMENT:
        return ScopeClassification(KIND_SEGMENT, raw, description, ids, marker, local_ids)
    if raw == SCOPE_DIALOGUE:
        return ScopeClassification(KIND_DIALOGUE, raw, description, ids, marker, local_ids)
    return ScopeClassification(KIND_LEGACY_TEXT, raw, description, ids, marker, local_ids)


def is_whole_artifact(receipt: Any) -> bool:
    """唯一判定入口：该记录能否代表整份产物已被用户确认。"""
    return classify(receipt).is_whole_artifact


def problems_for_write(receipt: Any) -> list[str]:
    """写出新记录前的严格校验；老记录不走这里（它们在读取侧 fail-closed）。"""
    if not isinstance(receipt, dict):
        return ["确认记录必须是对象"]
    problems: list[str] = []
    scope = receipt.get("scope")
    if scope not in SCOPE_VALUES:
        problems.append(f"scope 必须是 {list(SCOPE_VALUES)} 之一，当前为 {scope!r}")
    description = receipt.get("scope_description")
    if description is not None and (not isinstance(description, str) or not description.strip()):
        problems.append("scope_description 若存在必须是非空字符串")
    if receipt.get("kind") in DIALOGUE_RECEIPT_KINDS and scope != SCOPE_DIALOGUE:
        problems.append(f"kind={receipt.get('kind')} 的记录 scope 必须是 {SCOPE_DIALOGUE}")
    for key in SCOPE_ID_FIELDS:
        if key not in receipt:
            continue
        value = receipt[key]
        if not isinstance(value, list) or not value or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            problems.append(f"{key} 必须是非空的非空字符串数组")
    ids = _id_values(receipt)
    if scope == SCOPE_ARTIFACT and _has_scope_ids(receipt):
        present = sorted(key for key in SCOPE_ID_FIELDS if receipt.get(key) not in (None, "", [], {}))
        problems.append(f"整稿确认不得携带局部编号字段：{present}")
    if scope == SCOPE_SEGMENT and not ids:
        problems.append("段落确认必须列出该段实际编号（至少一个局部 id 字段）")
    if scope == SCOPE_DIALOGUE and not ids.get("dialogue_ids"):
        problems.append("对白确认必须列出 dialogue_ids")
    return problems


def new_receipt(
    scope: str,
    *,
    description: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造一条合法确认记录；字段不合法时直接报错，不写出半成品。"""
    receipt: dict[str, Any] = {"scope": scope}
    if description:
        receipt["scope_description"] = description
    if extra:
        receipt.update(extra)
    problems = problems_for_write(receipt)
    if problems:
        raise ValueError("；".join(problems))
    return receipt


def _iter_receipts(container: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(container, list):
        for index, item in enumerate(container):
            yield f"[{index}]", item


def audit(receipts: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    """只读审计：按范围分类计数，并区分结构错误与需要人工整理的旧记录。"""
    by_kind: dict[str, int] = {}
    whole_targets: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    total = 0
    for where, receipt in receipts:
        total += 1
        classification = classify(receipt)
        by_kind[classification.kind] = by_kind.get(classification.kind, 0) + 1
        if classification.is_whole_artifact:
            whole_targets.append({
                "where": where,
                "artifact_ids": list(receipt.get("artifact_ids") or []) if isinstance(receipt, dict) else [],
                "artifact_id": receipt.get("artifact_id") if isinstance(receipt, dict) else None,
            })
        if classification.kind == KIND_LEGACY_TEXT and isinstance(classification.raw_scope, str):
            notes.append({
                "where": where,
                "note": "旧记录的范围字段不是范围类别，已按非整稿处理；核对原确认依据后整理为范围字段",
                "scope": classification.raw_scope[:120],
            })
        if classification.kind == KIND_UNSCOPED and not classification.has_marker:
            notes.append({
                "where": where,
                "note": "无 scope 且看不出确认凭据（artifact_ids/approval_ref 等），按非整稿处理",
            })
        if not isinstance(receipt, dict):
            problems.append({"where": where, "message": "确认记录不是对象"})
            continue
        scope = receipt.get("scope")
        if scope is not None and not isinstance(scope, str):
            problems.append({"where": where, "message": "scope 不是字符串"})
        for key in SCOPE_ID_FIELDS:
            if key in receipt and not isinstance(receipt[key], list):
                problems.append({"where": where, "message": f"{key} 不是数组"})
    return {
        "total": total,
        "by_kind": by_kind,
        "whole_artifact_targets": whole_targets,
        "problems": problems,
        "notes": notes,
    }


def audit_project(project: Path) -> dict[str, Any]:
    """审计一个项目的 project_state 与变更案历史里的全部确认记录。"""
    project = Path(project).resolve()
    items: list[tuple[str, Any]] = []
    state_path = project / "00_项目管理" / "project_state.json"
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        for where, receipt in _iter_receipts(state.get("user_confirmations")):
            items.append((f"project_state.user_confirmations{where}", receipt))
    control_path = project / "00_项目管理" / "change_control.json"
    if control_path.is_file():
        control = json.loads(control_path.read_text(encoding="utf-8-sig"))
        for index, change in enumerate(control.get("history") or []):
            if not isinstance(change, dict):
                continue
            receipt = change.get("candidate_confirmation")
            if isinstance(receipt, dict):
                items.append((f"change_control.history[{index}].candidate_confirmation", receipt))
        active = control.get("active_change")
        if isinstance(active, dict) and isinstance(active.get("candidate_confirmation"), dict):
            items.append(("change_control.active_change.candidate_confirmation", active["candidate_confirmation"]))
    report = audit(items)
    report["project"] = str(project)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用户确认记录范围审计（只读）")
    parser.add_argument("--project", required=True, help="项目根目录")
    args = parser.parse_args(argv)
    report = audit_project(Path(args.project))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["problems"] else 2


if __name__ == "__main__":
    raise SystemExit(main())