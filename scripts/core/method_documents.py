"""Resolve user-confirmed, complete Vault method documents for one Skill.

The Vault remains the only editable source.  A stage packet may snapshot a
method for one running task, but that snapshot is evidence, never a second
method authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MethodDocumentError(ValueError):
    pass


# Historical compatibility is intentionally pinned to the one exact method
# already approved before promotion records existed. New entries cannot mint
# their own legacy exception by copying the shape of this record.
_LEGACY_PROMOTION_BASELINES = {
    (
        "CREATIVE-DIRECTION-METHOD",
        "projects/Master-Storyboarding/应用知识/编导/编导方法.md",
        "2026-10-23",
        "9dbaa9bda8fdc4f331b1de528d53f0e956570798c86a3da5236eed43a9a47114",
        "兼容现有 creative-director 编导方法绑定；历史晋升依据不重新分类。",
        "libfile_d4bb8cef26508191a0aa202b4ff481c7 v1；精确字节基准。",
    ),
}


@dataclass(frozen=True)
class MethodDocument:
    id: str
    vault_path: str
    title: str
    version: str
    checksum: str
    applies_when: str
    content: str
    promotion_basis: str = "legacy_unspecified"


@dataclass(frozen=True)
class MethodResolution:
    required: list[MethodDocument]
    pending: list[dict[str, str]]


def resolve_method_documents(
    vault: Path,
    profile: dict[str, Any],
    context: dict[str, Any] | None = None,
    *,
    test_proposed_method_ids: set[str] | None = None,
) -> MethodResolution:
    """Return confirmed bindings, with an explicit test-only proposed override.

    `binding_status` is deliberately separate from a document's own `status`:
    an enabled Vault page is not silently promoted to a confirmed Skill binding.
    The override exists only to exercise an isolated reading-packet experiment;
    callers must separately mark and block that packet from production use.
    """
    entries = profile.get("professional_methods", [])
    if not isinstance(entries, list):
        raise MethodDocumentError("professional_methods 必须是数组")
    required: list[MethodDocument] = []
    pending: list[dict[str, str]] = []
    seen: set[str] = set()
    test_proposed_method_ids = test_proposed_method_ids or set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise MethodDocumentError("professional_methods 的每项必须是对象")
        identifier = entry.get("id")
        path = entry.get("path")
        binding = entry.get("binding_status")
        applies_when = entry.get("applies_when")
        if not all(isinstance(value, str) and value.strip() for value in (identifier, path, binding, applies_when)):
            raise MethodDocumentError("专业方法必须包含非空 id、path、binding_status、applies_when")
        if identifier in seen:
            raise MethodDocumentError(f"专业方法 ID 重复：{identifier}")
        seen.add(identifier)
        if binding not in {"proposed", "user_confirmed"}:
            raise MethodDocumentError(f"专业方法绑定状态无效：{identifier}={binding}")
        test_only = binding == "proposed" and identifier in test_proposed_method_ids
        if binding != "user_confirmed" and not test_only:
            pending.append({"id": identifier, "path": path, "reason": "awaiting_user_confirmation"})
            continue
        requires = entry.get("requires")
        if not isinstance(requires, dict) or not requires:
            raise MethodDocumentError(f"专业方法缺少可执行的 requires 条件：{identifier}")
        if not _matches_requires(requires, context or {}):
            pending.append({"id": identifier, "path": path, "reason": "applies_when_not_met"})
            continue
        # Validate the exact bytes before a confirmed method enters this task.
        strict_promotion = profile.get("promotion_contract") == "required"
        if strict_promotion and binding == "user_confirmed":
            method = _load_complete_method(vault, identifier, path, applies_when)
            basis = _validate_promotion_record(
                vault, entry.get("promotion_record"), identifier, method.version,
                method.checksum, path,
            )
        else:
            method = None
            basis = "legacy_unspecified"
        if method is None:
            method = _load_complete_method(vault, identifier, path, applies_when, test_only=test_only)
        required.append(MethodDocument(
            method.id, method.vault_path, method.title, method.version,
            method.checksum, method.applies_when, method.content, basis,
        ))
    return MethodResolution(required=required, pending=pending)


def _validate_promotion_record(
    vault: Path, record: Any, identifier: str, version: str, checksum: str,
    relative_path: str,
) -> str:
    if not isinstance(record, dict):
        raise MethodDocumentError(f"正式方法缺少 promotion_record：{identifier}")
    basis = record.get("basis")
    if basis == "legacy_unclassified":
        exact_baseline = (
            identifier, relative_path, version, checksum,
            record.get("scope"), record.get("source_ref"),
        )
        if (exact_baseline not in _LEGACY_PROMOTION_BASELINES
                or record.get("method_version") != version
                or record.get("method_sha256") != checksum):
            raise MethodDocumentError(
                f"legacy_unclassified 仅兼容代码固定的既有批准基准；新方法须提供真实晋升依据：{identifier}"
            )
        return basis
    if basis not in {"user_directed", "experiment_validated"}:
        raise MethodDocumentError(f"方法晋升依据无效：{identifier}={basis!r}")
    if record.get("method_version") != version or record.get("method_sha256") != checksum:
        raise MethodDocumentError(f"方法晋升凭据未绑定当前版本/hash：{identifier}")
    _nonempty(record, ("scope", "source_ref", "conflict_resolution", "rollback"), identifier)
    supersedes = record.get("supersedes")
    if not isinstance(supersedes, list) or any(not isinstance(item, str) or not item.strip() for item in supersedes):
        raise MethodDocumentError(f"方法晋升 supersedes 必须是字符串数组：{identifier}")
    if basis == "user_directed":
        _nonempty(record, ("user_instruction_ref",), identifier)
        return basis
    evidence_path = record.get("evidence_path")
    evidence_sha256 = record.get("evidence_sha256")
    if not isinstance(evidence_path, str) or not evidence_path or not re.fullmatch(r"[0-9a-f]{64}", str(evidence_sha256)):
        raise MethodDocumentError(f"实验晋升必须绑定 evidence_path 与 SHA-256：{identifier}")
    evidence = _safe_vault_file(vault, evidence_path, identifier)
    raw = evidence.read_bytes()
    if hashlib.sha256(raw).hexdigest() != evidence_sha256:
        raise MethodDocumentError(f"实验晋升证据 hash 不匹配：{identifier}")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MethodDocumentError(f"实验晋升证据不是有效 JSON：{identifier}") from exc
    expected = {
        "schema_version": "1.0", "kind": "method-promotion-evidence",
        "method_id": identifier, "method_version": version,
        "method_sha256": checksum, "decision": "adopt",
    }
    if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
        raise MethodDocumentError(f"实验晋升证据未绑定方法版本或缺少 adopt 结论：{identifier}")
    if payload.get("scope") != record.get("scope"):
        raise MethodDocumentError(f"实验晋升证据适用范围不匹配：{identifier}")
    evaluation = payload.get("evaluation")
    if not isinstance(evaluation, dict) or any(
        not isinstance(evaluation.get(key), list) or not evaluation[key]
        for key in ("criteria", "observations", "limitations")
    ):
        raise MethodDocumentError(f"实验晋升证据缺少标准、观察结果或限制：{identifier}")
    return basis


def _nonempty(record: dict[str, Any], keys: tuple[str, ...], identifier: str) -> None:
    if any(not isinstance(record.get(key), str) or not record[key].strip() for key in keys):
        raise MethodDocumentError(f"方法晋升记录缺少字段：{identifier}: {', '.join(keys)}")


def _safe_vault_file(vault: Path, relative: str, identifier: str) -> Path:
    if relative.startswith(("/", "\\")) or "\\" in relative or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise MethodDocumentError(f"方法证据路径无效：{identifier}={relative}")
    target = (vault / relative).resolve()
    try:
        target.relative_to(vault.resolve())
    except ValueError as exc:
        raise MethodDocumentError(f"方法证据路径越出知识库：{identifier}") from exc
    if not target.is_file():
        raise MethodDocumentError(f"方法晋升证据文件缺失：{identifier}={relative}")
    return target


def _load_complete_method(vault: Path, identifier: str, relative: str, applies_when: str, *, test_only: bool = False) -> MethodDocument:
    if relative.startswith(("/", "\\")) or "\\" in relative or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise MethodDocumentError(f"专业方法路径无效：{identifier}={relative}")
    source = (vault / relative).resolve()
    try:
        source.relative_to(vault.resolve())
    except ValueError as exc:
        raise MethodDocumentError(f"专业方法路径越出知识库：{identifier}") from exc
    if not source.is_file():
        raise MethodDocumentError(f"专业方法文件缺失：{identifier}={relative}")
    raw = source.read_bytes()
    try:
        content = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise MethodDocumentError(f"专业方法不是 UTF-8 文本：{identifier}={relative}") from exc
    metadata = _frontmatter(content, identifier)
    # Reuse the Vault's established frontmatter.  A binding ID belongs to the
    # project profile, so older method pages are not forced to add a duplicate
    # method_id or a new page type merely to satisfy this loader.
    allowed_statuses = {"enabled", "confirmed"} | ({"candidate"} if test_only else set())
    allowed_lifecycle = {"current"} | ({"needs_review"} if test_only else set())
    if metadata.get("status") not in allowed_statuses:
        raise MethodDocumentError(f"专业方法状态不适用：{identifier}={metadata.get('status')!r}")
    if metadata.get("lifecycle.state") not in allowed_lifecycle:
        raise MethodDocumentError(f"专业方法生命周期不适用：{identifier}={metadata.get('lifecycle.state')!r}")
    title = metadata.get("title")
    version = metadata.get("version") or metadata.get("updated")
    if not isinstance(title, str) or not title.strip() or not isinstance(version, str) or not version.strip():
        raise MethodDocumentError(f"专业方法缺少 title 或 version/updated：{identifier}")
    return MethodDocument(identifier, relative, title.strip(), version.strip(), hashlib.sha256(raw).hexdigest(), applies_when.strip(), content)


def _matches_requires(requires: dict[str, Any], context: dict[str, Any]) -> bool:
    for path, expected in requires.items():
        if not isinstance(path, str) or not path or not isinstance(expected, (str, int, float, bool, list)):
            raise MethodDocumentError("专业方法 requires 必须是字段路径到标量或标量数组的映射")
        value: Any = context
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                return False
            value = value[part]
        values = expected if isinstance(expected, list) else [expected]
        if value not in values:
            return False
    return True


def _frontmatter(content: str, identifier: str) -> dict[str, str]:
    match = re.match(r"\A---\r?\n(?P<body>.*?)\r?\n---\r?\n", content, re.DOTALL)
    if match is None:
        raise MethodDocumentError(f"专业方法缺少 YAML frontmatter：{identifier}")
    result: dict[str, str] = {}
    parent = ""
    for line in match.group("body").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        top = re.match(r"^(?P<key>[A-Za-z_][\w-]*):\s*(?P<value>.*?)\s*$", line)
        nested = re.match(r"^\s{2}(?P<key>[A-Za-z_][\w-]*):\s*(?P<value>.*?)\s*$", line)
        if top:
            parent = top.group("key")
            value = top.group("value").strip().strip('"\'')
            if value:
                result[parent] = value
        elif nested and parent:
            result[f"{parent}.{nested.group('key')}"] = nested.group("value").strip().strip('"\'')
    return result


def render_method_packet(methods: list[MethodDocument]) -> str:
    lines = ["# 本轮必读专业方法快照", "", "> 自动生成的只读任务依据；Vault 原页是唯一可编辑主版本。", ""]
    for method in methods:
        lines.extend([
            f"## {method.id}", "",
            f"- 来源：`{method.vault_path}`",
            f"- 版本：`{method.version}`",
            f"- SHA-256：`{method.checksum}`",
            f"- 晋升依据状态：`{method.promotion_basis}`（仅记录分类，不由读取本身产生晋升）",
            f"- 适用条件：{method.applies_when}", "",
            "### 完整正文", "", method.content.rstrip(), "",
        ])
    return "\n".join(lines).rstrip() + "\n"
