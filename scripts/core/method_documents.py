"""Resolve confirmed methods from commit-owned knowledge or an explicit Vault.

Task snapshots are reading evidence, never a new method authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from .errors import StageContextError
from .project_kickoff import RECREATION_ENTRY, RECREATION_METHOD, RECREATION_WORKFLOW, production_workflow
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
        method = _load_complete_method(vault, identifier, path, applies_when, test_only=test_only)
        basis = "legacy_unspecified"
        if strict_promotion and binding == "user_confirmed":
            basis = _validate_promotion_record(
                vault, entry.get("promotion_record"), identifier, method.version,
                method.checksum, path,
            )
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


def _safe_vault_file(vault: Path, relative: str, identifier: str, *, method=False) -> Path:
    kind = "专业方法" if method else "方法证据"
    if relative.startswith(("/", "\\")) or "\\" in relative or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise MethodDocumentError(f"{kind}路径无效：{identifier}={relative}")
    target = (vault / relative).resolve()
    try:
        target.relative_to(vault.resolve())
    except ValueError as exc:
        raise MethodDocumentError(f"{kind}路径越出知识库：{identifier}") from exc
    if not target.is_file():
        label = "专业方法" if method else "方法晋升证据"
        raise MethodDocumentError(f"{label}文件缺失：{identifier}={relative}")
    try:
        return verify_selected_file(vault, relative)
    except KnowledgeRuntimeError as exc:
        raise MethodDocumentError(str(exc)) from exc


def _load_complete_method(vault: Path, identifier: str, relative: str, applies_when: str, *, test_only: bool = False) -> MethodDocument:
    source = _safe_vault_file(vault, relative, identifier, method=True)
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


_CROSS_SKILL_READS: dict[tuple[str, str], str] = {
    ("storyboard-planner", "skills/knowledge-maintainer/modules/maintenance-method.md"):
        "导演阶段沿用知识维护方法，既有测试断言其在导演必读集内；2026-11-09 用户审定保留并登记（规范 Q1）。",
}

# Registered dependencies: when the trigger document is in the set, the
# listed foreign documents follow as leaves.
_CROSS_SKILL_DEPENDENCIES: dict[tuple[str, str], tuple[str, ...]] = {
    ("creative-director", "skills/creative-director/references/knowledge-candidates.md"):
        ("skills/knowledge-maintainer/modules/maintenance-method.md",),
}

_REDFRAME_IMAGE_STANDARDS = (
    "skills/_shared/redframe-operations.md",
    "skills/voiceover-to-video-script/references/redframe-canvas-usage.md",
    "skills/voiceover-to-video-script/references/redframe-canvas-layout.md",
)


def _redframe_image_batch(skill: str, request: dict[str, Any]) -> bool:
    if skill not in {"asset-image-assistant", "storyboard-director"} or not request.get("image_targets"):
        return False
    kickoff = request.get("kickoff") or {}
    grant = kickoff.get("image_generation") or {}
    return grant.get("provider") == "redframe"


def local_standard_documents(root, skill_dir, request=None, *, read_document=Path.read_bytes):
    """Resolve the local documents and registered cross-Skill leaves for one prepare."""
    root = root.resolve()
    skills_root = (root / "skills").resolve()
    rules_root = (root / "docs/rules").resolve()
    shared_root = (skills_root / "_shared").resolve()
    seedance = (skills_root / "voiceover-to-video-script/references/seedance-delivery-package.md").resolve()

    def local_path(path):
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise StageContextError(f"本地知识或规范路径越出仓库：{path}；请重新 prepare。")
        return resolved

    def _registered_read_path(reference: str) -> Path | None:
        """Resolve a registry target. Missing target file fails closed when the
        target Skill exists in this repository; a repository that does not carry
        the target Skill at all (synthetic test roots) simply has no such read."""
        path = (root / reference).resolve()
        if path.is_file():
            return path
        if (root / "skills" / Path(reference).parts[1]).is_dir():
            raise StageContextError(f"跨 Skill 登记共读目标缺失：{reference}")
        return None


    request = request or {}
    excluded: set[Path] = set()
    # Web-route modules are entry-specific by nature, not by calling stage:
    # no prepare set may contain them, whichever stage is being prepared (P6).
    excluded.update((root / "skills/asset-image-assistant/modules" / name).resolve() for name in
                    ("library-mode.md", "project-web-mode.md", "web-input-contract.md"))
    if skill_dir.name == "asset-image-assistant":
        scopes = request.get("asset_method_scopes", {})
        if not isinstance(scopes, dict) or any(key not in {"style", "character", "body", "scene", "object"}
                                             or type(value) is not bool for key, value in scopes.items()):
            raise StageContextError("asset_method_scopes 必须按现有类别填写布尔值。")
        if any(scopes.values()):
            conditional = {"style": ["01-style-master-guide.md"],
                           "character": ["02-character-master-guide.md"],
                           "body": ["02a-body-character-master-guide.md"],
                           "scene": ["03-scene-master-guide.md"],
                           "object": ["04-prop-product-master-guide.md"]}
            # Existing character requests did not distinguish anatomy. Keep
            # both guides until body is explicitly classified by this task.
            selected = {scope for scope, enabled in scopes.items() if enabled}
            if "body" not in scopes and scopes.get("character"):
                selected.add("body")
            excluded.update((skill_dir / "references" / name).resolve()
                            for scope, names in conditional.items() if scope not in selected
                            for name in names)
    local = [skill_dir / "SKILL.md", *sorted((skill_dir / "modules").glob("*.md")),
             *sorted((skill_dir / "references").glob("*.md"))]
    local = [local_path(path) for path in local if path.resolve() not in excluded]
    recreation_docs = [root / RECREATION_ENTRY, root / RECREATION_METHOD]
    if skill_dir.name in {"storyboard-planner", "storyboard-director"}:
        if production_workflow(request.get("kickoff")) == RECREATION_WORKFLOW:
            local.extend(local_path(path) for path in recreation_docs)
    extra = request.get("local_references", [])
    if not isinstance(extra, list):
        raise StageContextError("local_references 必须是本轮实际采用的仓库相对规范路径数组。")
    for reference in extra:
        if not isinstance(reference, str) or not reference or Path(reference).is_absolute() or PureWindowsPath(reference).drive:
            raise StageContextError("local_references 必须使用仓库相对路径。")
        path = (root / reference).resolve()
        if (not path.is_relative_to(root) or path.suffix not in {".md", ".json"}
                or not path.is_file() or not path.stat().st_size):
            raise StageContextError(f"本轮本地规范不存在、为空或越出仓库：{reference}")
        if path in excluded:
            raise StageContextError(f"本地规范与当前普通 prepare 入口或 asset_method_scopes 不一致：{reference}；先核对实际入口和类别。")
        # Nature-excluded contracts stay out through every channel (P4): the
        # Seedance delivery package is voiceover's internal contract, read
        # through its own direct-read entry; for any other stage an explicit
        # local_references selection does not admit it either.
        if (skill_dir.name != "voiceover-to-video-script"
                and path == seedance):
            raise StageContextError(f"本地规范是 voiceover 内部交付合同、不进其他阶段的 prepare 集：{reference}")
        local.append(path)
    if skill_dir.name in {"storyboard-planner", "storyboard-director"}:
        # A navigation link alone cannot select the local-rewrite branch.
        excluded.update(path.resolve() for path in recreation_docs if path.resolve() not in local)
    if skill_dir.name == "creative-director":
        legacy = (skill_dir / "references/dialogue-legacy-workflows.md").resolve()
        candidates = (skill_dir / "references/knowledge-candidates.md").resolve()
        maintenance = (root / "skills/knowledge-maintainer/modules/maintenance-method.md").resolve()
        selected_references = {(root / reference).resolve() for reference in extra}
        if legacy not in selected_references:
            excluded.add(legacy)
        if candidates not in selected_references:
            excluded.add(candidates)
            if maintenance not in selected_references:
                excluded.add(maintenance)
        local = [path for path in local if path not in excluded]
    # These branches require explicit selection, even if a compatibility note
    # elsewhere links to them. The root rule table is navigation, not opt-in.
    for name in ("legacy-review.md", "experiments.md", "project-layout.md"):
        path = (root / "docs/rules" / name).resolve()
        if path not in local:
            excluded.add(path)
    # These are the common redlines named collectively by Skill entries; their
    # presence must not depend on whether prose spells each link separately.
    # The platform contract is selected separately for Redframe image batches
    # (or explicitly by the caller); its video-only links do not expand the set.
    shared = sorted(local_path(path) for path in shared_root.glob("*.md")
                    if path.name != "redframe-operations.md")
    leaf_paths: set[Path] = set()
    is_repository_skill = skill_dir.resolve() == (root / "skills" / skill_dir.name).resolve()
    if is_repository_skill:
        references = [ref for skill, ref in _CROSS_SKILL_READS if skill == skill_dir.name]
        if _redframe_image_batch(skill_dir.name, request):
            references.extend(_REDFRAME_IMAGE_STANDARDS)
        leaf_paths.update(path for ref in references if (path := _registered_read_path(ref)))
    seen: set[Path] = set([*local, *shared, *leaf_paths])
    visited: set[Path] = set()
    pending = [local_path(root / "PROJECT_RULES.md"), *local, *shared]
    while pending:
        document = pending.pop()
        if document in visited:
            continue
        visited.add(document)
        if document.suffix != ".md":
            continue
        document_owner = (document.relative_to(skills_root).parts[0]
                          if document.is_relative_to(skills_root) else None)
        if document_owner is not None and document_owner not in {skill_dir.name, "_shared"}:
            continue
        text = read_document(local_path(document)).decode("utf-8")
        references = re.findall(r"(?:skills/|\.\./)*_shared/[A-Za-z0-9_-]+\.md", text)
        if document.parent == shared_root:
            references.extend(re.findall(r"\]\(([A-Za-z0-9_-]+\.md)(?=[#)])", text))
        # Redframe procedures are selected by the actual task, not by a link
        # mentioned in a general rule.
        references = [reference for reference in references
                      if Path(reference).name != "redframe-operations.md"]
        dependencies = []
        for reference in references:
            path = local_path(shared_root / Path(reference).name)
            if not path.is_file():
                raise StageContextError(f"必需共享规范缺失：{path}")
            dependencies.append(path)
        # Root-wide links describe other stages, not this task's contracts.
        # Follow explicit module/reference links from the selected contracts.
        if document != (root / "PROJECT_RULES.md").resolve():
            refs = re.findall(r"(?:`|\]\()((?:\.\./|\./|skills/|docs/rules/|references/|modules/|templates/|schemas/)[^`\s)#]+\.(?:md|json))", text)
            for reference in refs:
                choices = [(document.parent / reference).resolve(),
                           (skill_dir / reference).resolve(), (root / reference).resolve()]
                if document_owner:
                    choices.insert(1, (skills_root / document_owner / reference).resolve())
                target = next((path for path in choices if path.is_relative_to(root) and path.is_file()), None)
                # A rule may point to an optional or downstream Skill. Freeze
                # this stage's contracts, not that other Skill's entire tree.
                linked = target or choices[0]
                if (document.is_relative_to(rules_root)
                        and linked.is_relative_to(skills_root)
                        and not linked.is_relative_to(skill_dir.resolve())
                        and linked.parent != shared_root):
                    continue
                # Boundary gate (P2, spec §4.1 equivalent form): outbound
                # links are followed only from first-party documents (this
                # Skill's own documents and _shared). A first-party link into
                # another Skill declares that dependency; the foreign document
                # then enters as a leaf whose own links never expand the set.
                if (target is not None and target.is_relative_to(skills_root)
                        and target.relative_to(skills_root).parts[0] not in {skill_dir.name, "_shared"}
                        and document_owner not in {skill_dir.name, "_shared"}):
                    continue
                # The Seedance delivery package is voiceover's internal
                # contract, governed by its direct-read list; it never enters
                # a prepare set (P4), not even via a _shared reference.
                if target == seedance:
                    continue
                if target == (shared_root / "redframe-operations.md").resolve():
                    continue
                is_rule = any(path.is_relative_to(rules_root) for path in choices)
                if (target is None and not any(path in excluded for path in choices)
                        and (is_rule or {"references", "modules", "templates", "schemas"}.intersection(Path(reference).parts))):
                    raise StageContextError(f"明确引用的本地规范缺失：{document}: {reference}")
                if target and target not in excluded and (target.is_relative_to(rules_root) or target.parent == shared_root
                        or "references" in target.parts or "modules" in target.parts or "templates" in target.parts or "schemas" in target.parts):
                    dependencies.append(target)
        for path in dependencies:
            if path not in seen:
                seen.add(path)
                pending.append(path)
    for (dependency_skill, trigger_reference), dependency_references in _CROSS_SKILL_DEPENDENCIES.items():
        if dependency_skill == skill_dir.name and is_repository_skill and (root / trigger_reference).resolve() in seen:
            for dependency_reference in dependency_references:
                dependency_path = _registered_read_path(dependency_reference)
                if dependency_path is not None:
                    seen.add(dependency_path)
    return sorted(seen)
