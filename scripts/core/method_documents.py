"""Resolve confirmed methods from commit-owned knowledge or an explicit Vault.

Task snapshots are reading evidence, never a new method authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .knowledge_runtime import KnowledgeRuntimeError, verify_selected_file


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
    template_path: str = ""
    template_checksum: str = ""
    template_content: str = ""
    reading_scope: str = ""


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
        task_context = context or {}
        usage = _creative_mask_usage(identifier, task_context)
        if not _matches_requires(requires, task_context):
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
        template_path = entry.get("template_path", "")
        if "template_path" in entry and (not isinstance(template_path, str) or not template_path.strip()):
            raise MethodDocumentError(f"专业方法模板路径无效：{identifier}")
        template_checksum = template_content = ""
        if template_path:
            try:
                template_file = verify_selected_file(vault, template_path)
                template_raw = template_file.read_bytes()
                template_content = template_raw.decode("utf-8-sig")
                template_checksum = hashlib.sha256(template_raw).hexdigest()
            except (KnowledgeRuntimeError, OSError, UnicodeDecodeError) as exc:
                raise MethodDocumentError(f"专业方法适用模板不可用：{identifier}: {exc}") from exc
        content, reading_scope = _creative_mask_excerpt(method.content, usage) if usage else (method.content, "")
        required.append(MethodDocument(
            method.id, method.vault_path, method.title, method.version,
            method.checksum, method.applies_when, content, basis,
            template_path, template_checksum, template_content, reading_scope,
        ))
    return MethodResolution(required=required, pending=pending)


def _creative_mask_usage(identifier: str, context: dict[str, Any]) -> str:
    request = context.get("request")
    if (identifier != "VGEN-CREATOR-POLISHING-MASK-USE"
            or context.get("skill") != "creative-director"
            or not isinstance(request, dict)
            or request.get("product_id") != "creator-polishing-mask-2"):
        return ""
    usage = request.get("product_usage")
    if usage not in ("display", "real_use"):
        raise MethodDocumentError(
            "编导已指定造物者温泉酸抛光面膜 2.0，请在请求填写 product_usage："
            "仅展示用 display，涉及实际使用用 real_use。若只提到品牌，请移除 product_id。"
        )
    return usage


def _creative_mask_excerpt(content: str, usage: str) -> tuple[str, str]:
    """Select verbatim passages after the complete source has been validated."""
    parts = re.split(r"(?m)^## ([^\n]+)\n", content)
    sections = dict(zip(parts[1::2], parts[2::2]))
    excerpts = {
        "适用条件": [
            ("本轮明确选定", ""),
            ("开始前核对当前产品版本", "动作片段沿" if usage == "display" else ""),
        ],
        "可直接套用的制作模板": [
            ("实际使用的编导模板" if usage == "real_use" else "仅展示的编导模板", ""),
        ],
        "展示与时间分配": [
            ("- 给产品留可辨认的画面。", "出示、操作、退场分别"),
        ],
    }
    if usage == "real_use":
        excerpts["展示与时间分配"].append(
            ("- 40 秒搅拌和 15 分钟等待", "单段时长容不下时"),
        )
        excerpts["选择与引用动作视频"] = [
            ("项目包提供开盒", ""),
        ]
        excerpts["产品步骤与硬边界"] = [
            ("保持顺序", ""),
            ("本轮确有上脸时", ""),
            ("不得表现单支产品直接", "真实使用过程不拆成"),
        ]
    selected = []
    for heading, spans in excerpts.items():
        lines = []
        for prefix, stop_before in spans:
            matches = [line for line in sections.get(heading, "").splitlines() if line.startswith(prefix)]
            if len(matches) != 1 or (stop_before and stop_before not in matches[0]):
                raise MethodDocumentError(f"编导面膜专项原文无法定位必读段落：{heading} / {prefix}")
            lines.append(matches[0].split(stop_before, 1)[0].rstrip() if stop_before else matches[0])
        selected.append(f"## {heading}\n\n" + "\n\n".join(lines))
    return "\n\n".join(selected), "编导 · 实际使用" if usage == "real_use" else "编导 · 只展示"


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
    try:
        return verify_selected_file(vault, relative)
    except KnowledgeRuntimeError as exc:
        raise MethodDocumentError(str(exc)) from exc


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
    try:
        source = verify_selected_file(vault, relative)
    except KnowledgeRuntimeError as exc:
        raise MethodDocumentError(str(exc)) from exc
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
        if not any(value == item and isinstance(value, bool) == isinstance(item, bool) for item in values):
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
    lines = ["# 本轮必读专业方法快照", "", "> 自动生成的只读任务依据；内置知识由当前仓库提交定版；显式外部 Vault 为本轮指定来源。读取不自动同步或晋升。", ""]
    for method in methods:
        lines.extend([
            f"## {method.id}", "",
            f"- 来源：`{method.vault_path}`",
            f"- 版本：`{method.version}`",
            f"- {'原文 ' if method.reading_scope else ''}SHA-256：`{method.checksum}`",
            f"- 晋升依据状态：`{method.promotion_basis}`（仅记录分类，不由读取本身产生晋升）",
            f"- 适用条件：{method.applies_when}", "",
            f"### 本轮必读摘录（{method.reading_scope}）" if method.reading_scope else "### 完整正文",
            "", method.content.rstrip(), "",
        ])
        if method.template_path:
            lines.extend([
                "### 本方法适用模板", "",
                f"- 来源：`{method.template_path}`",
                f"- SHA-256：`{method.template_checksum}`", "",
                method.template_content.rstrip(), "",
            ])
    return "\n".join(lines).rstrip() + "\n"
