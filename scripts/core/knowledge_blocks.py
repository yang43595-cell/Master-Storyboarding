from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RULE_BLOCK_RE = re.compile(
    r"<!--\s*msb-knowledge[ \t]*\r?\n"
    r"(?P<metadata>\{.*?\})[ \t]*\r?\n"
    r"-->\s*\r?\n?"
    r"(?P<content>.*?)"
    r"<!--\s*/msb-knowledge\s*-->",
    re.DOTALL,
)
_START_MARKER_RE = re.compile(r"<!--\s*msb-knowledge\b")
_END_MARKER_START_RE = re.compile(r"<!--\s*/msb-knowledge\b")
_END_MARKER_RE = re.compile(r"<!--\s*/msb-knowledge\s*-->")

_REQUIRED_FIELDS = {
    "id",
    "canonical_key",
    "kind",
    "status",
    "authority",
    "projects",
    "stages",
    "topics",
    "triggers",
    "conflicts_with",
    "priority",
    "sources",
    "applies_when",
}
_TUPLE_FIELDS = (
    "projects",
    "stages",
    "topics",
    "triggers",
    "conflicts_with",
    "sources",
)

@dataclass(frozen=True)
class KnowledgeBlock:
    id: str
    canonical_key: str
    kind: str
    status: str
    authority: str
    projects: tuple[str, ...]
    stages: tuple[str, ...]
    topics: tuple[str, ...]
    triggers: tuple[str, ...]
    conflicts_with: tuple[str, ...]
    priority: int
    sources: tuple[str, ...]
    applies_when: str
    body: str
    page_path: Path
    vault_relative_path: str
    anchor: str
    fingerprint: str
    # Source status retained when an enabled Wiki block is wrapped as a
    # candidate only inside isolated candidate-test.
    source_status: str | None = None
    source_page_sha256: str | None = None


class KnowledgeIndexError(ValueError):
    pass


def load_policy(repo_root: Path) -> dict[str, Any]:
    path = Path(repo_root) / "references/workflow/knowledge-policy.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeIndexError(f"cannot load knowledge policy: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") not in {"1.0", "2.0"}:
        raise KnowledgeIndexError("knowledge policy schema_version must be 1.0 or 2.0")
    return value


def parse_knowledge_blocks(path: Path, vault_root: Path) -> list[KnowledgeBlock]:
    path = Path(path).resolve()
    vault_root = Path(vault_root).resolve()
    try:
        relative = path.relative_to(vault_root).as_posix()
    except ValueError as exc:
        raise KnowledgeIndexError(f"knowledge page is outside vault: {path}") from exc

    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise KnowledgeIndexError(f"cannot read knowledge page: {path}: {exc}") from exc

    _validate_marker_integrity(text, relative)
    blocks: list[KnowledgeBlock] = []
    for match in RULE_BLOCK_RE.finditer(text):
        blocks.append(_block_from_match(match, path, vault_root, relative))
    return blocks


def build_knowledge_index(
    vault_root: Path, policy: dict[str, Any]
) -> list[KnowledgeBlock]:
    vault_root = Path(vault_root).resolve()
    blocks: list[KnowledgeBlock] = []
    discovered_blocks: list[KnowledgeBlock] = []
    for path in _discovered_markdown_files(vault_root, policy):
        parsed = parse_knowledge_blocks(path, vault_root)
        discovered_blocks.extend(parsed)
        for block in parsed:
            _validate_active_contract(block, policy)
            if block.authority == "wiki":
                blocks.append(block)
    _validate_unique_ids(discovered_blocks)
    _validate_unique(blocks)
    return blocks


def _block_from_match(
    match: re.Match[str], path: Path, vault_root: Path, relative: str
) -> KnowledgeBlock:
    try:
        metadata = json.loads(match.group("metadata"))
    except json.JSONDecodeError as exc:
        raise KnowledgeIndexError(f"invalid rule metadata in {relative}: {exc}") from exc
    _validate_metadata(metadata, relative, vault_root)

    rule_id = metadata["id"]
    expected_anchor = rule_id.lower()
    content = match.group("content")
    anchor_match = re.search(
        rf"(?m)^\^(?P<anchor>{re.escape(expected_anchor)})[ \t]*\r?$",
        content,
    )
    if anchor_match is None or content[anchor_match.end() :].strip():
        raise KnowledgeIndexError(
            f"{rule_id} must end with matching lowercase anchor ^{expected_anchor}"
        )

    body = _normalize_body(content[: anchor_match.start()])
    fingerprint = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return KnowledgeBlock(
        id=rule_id,
        canonical_key=metadata["canonical_key"],
        kind=metadata["kind"],
        status=metadata["status"],
        authority=metadata["authority"],
        projects=tuple(metadata["projects"]),
        stages=tuple(metadata["stages"]),
        topics=tuple(metadata["topics"]),
        triggers=tuple(metadata["triggers"]),
        conflicts_with=tuple(metadata["conflicts_with"]),
        priority=metadata["priority"],
        sources=tuple(metadata["sources"]),
        applies_when=metadata["applies_when"],
        body=body,
        page_path=path,
        vault_relative_path=relative,
        anchor=expected_anchor,
        fingerprint=fingerprint,
    )


def _validate_metadata(metadata: Any, relative: str, vault_root: Path) -> None:
    if not isinstance(metadata, dict):
        raise KnowledgeIndexError(f"rule metadata must be an object in {relative}")
    if set(metadata) != _REQUIRED_FIELDS:
        missing = sorted(_REQUIRED_FIELDS - set(metadata))
        extra = sorted(set(metadata) - _REQUIRED_FIELDS)
        raise KnowledgeIndexError(
            f"rule metadata fields invalid in {relative}; missing={missing}, extra={extra}"
        )
    if not isinstance(metadata["id"], str) or not re.fullmatch(
        r"MSB-KB-[A-Z0-9-]+", metadata["id"]
    ):
        raise KnowledgeIndexError(f"invalid rule id in {relative}")
    for field in ("canonical_key", "kind", "status", "authority", "applies_when"):
        if not isinstance(metadata[field], str) or not metadata[field]:
            raise KnowledgeIndexError(f"{field} must be a non-empty string in {relative}")
    if metadata["authority"] not in {"wiki", "repo"}:
        raise KnowledgeIndexError(
            f"authority must be wiki or repo in {relative}: {metadata['authority']}"
        )
    for field in _TUPLE_FIELDS:
        value = metadata[field]
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise KnowledgeIndexError(f"{field} must be an array of strings in {relative}")
        if len(value) != len(set(value)):
            raise KnowledgeIndexError(f"{field} contains duplicates in {relative}")
    for field in ("projects", "stages", "topics"):
        if not metadata[field]:
            raise KnowledgeIndexError(f"{field} cannot be empty in {relative}")
    if not isinstance(metadata["priority"], int) or isinstance(
        metadata["priority"], bool
    ):
        raise KnowledgeIndexError(f"priority must be an integer in {relative}")
    _validate_sources(metadata["sources"], relative, vault_root)


def _validate_sources(sources: list[str], relative: str, vault_root: Path) -> None:
    wiki_root = (vault_root / "wiki").resolve()
    for source in sources:
        if (
            not source.startswith("sources/")
            or source.endswith(".md")
            or "\\" in source
            or "//" in source
            or source.endswith("/")
            or any(part in {"", ".", ".."} for part in source.split("/"))
        ):
            raise KnowledgeIndexError(f"unsafe sources target in {relative}: {source}")

        # Resolve against the declared wiki root and verify containment at runtime.
        target = (wiki_root / f"{source}.md").resolve()
        try:
            target.relative_to(wiki_root)
        except ValueError as exc:
            raise KnowledgeIndexError(
                f"sources target escapes wiki root in {relative}: {source}"
            ) from exc


def _normalize_body(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in value.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _validate_marker_integrity(text: str, relative: str) -> None:
    events = [
        *((match.start(), "start") for match in _START_MARKER_RE.finditer(text)),
        *((match.start(), "end") for match in _END_MARKER_START_RE.finditer(text)),
    ]
    events.sort()
    active_start: int | None = None
    previous_end: int | None = None
    for position, kind in events:
        if kind == "start":
            if active_start is not None:
                raise KnowledgeIndexError(
                    f"overlapping knowledge marker in {relative} at {position}"
                )
            if previous_end is not None:
                gap = text[previous_end:position]
                if not gap or (not gap.strip() and "\n" not in gap and "\r" not in gap):
                    raise KnowledgeIndexError(
                        f"invalid adjacent knowledge marker in {relative} at {position}"
                    )
            active_start = position
            continue

        if active_start is None:
            raise KnowledgeIndexError(
                f"orphan closing knowledge marker in {relative} at {position}"
            )
        end_match = _END_MARKER_RE.match(text, position)
        block_match = RULE_BLOCK_RE.match(text, active_start)
        if (
            end_match is None
            or block_match is None
            or block_match.end() != end_match.end()
        ):
            raise KnowledgeIndexError(
                f"damaged knowledge marker structure in {relative} at {active_start}"
            )
        previous_end = end_match.end()
        active_start = None
    if active_start is not None:
        raise KnowledgeIndexError(
            f"unclosed knowledge marker in {relative} at {active_start}"
        )


def _validate_active_contract(
    block: KnowledgeBlock, policy: dict[str, Any]
) -> None:
    if len(block.body) > policy["budgets"]["max_block_chars"]:
        raise KnowledgeIndexError(
            f"{block.id} exceeds max_block_chars "
            f"({len(block.body)} > {policy['budgets']['max_block_chars']})"
        )
    # Candidate blocks are parseable but inert. The ordinary selector ignores
    # them; only an explicit candidate-test call may return them separately.
    if block.status == "candidate":
        if block.kind not in policy["discovery"]["allowed_kinds"]:
            raise KnowledgeIndexError(f"unsupported candidate knowledge kind: {block.kind}")
        return
    if block.kind not in policy["discovery"]["allowed_kinds"]:
        raise KnowledgeIndexError(f"unsupported knowledge kind: {block.kind}")
    if block.status not in policy["discovery"]["allowed_statuses"]:
        raise KnowledgeIndexError(f"unsupported knowledge status: {block.status}")


def _discovered_markdown_files(
    vault_root: Path, policy: dict[str, Any]
) -> list[Path]:
    files: set[Path] = set()
    for configured_root in policy["discovery"]["roots"]:
        root = (vault_root / configured_root).resolve()
        try:
            root.relative_to(vault_root)
        except ValueError as exc:
            raise KnowledgeIndexError(
                f"discovery root escapes vault: {configured_root}"
            ) from exc
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            if not path.is_file():
                continue
            relative = path.resolve().relative_to(vault_root).as_posix()
            if not _is_excluded(relative, policy["discovery"]["excluded_globs"]):
                files.add(path.resolve())
    return sorted(files, key=lambda item: item.relative_to(vault_root).as_posix())


def _is_excluded(relative: str, patterns: list[str]) -> bool:
    folded_relative = relative.casefold()
    return any(
        fnmatch.fnmatchcase(folded_relative, pattern.casefold())
        for pattern in patterns
    )


def _validate_unique(blocks: list[KnowledgeBlock]) -> None:
    _validate_unique_ids(blocks)
    seen_keys: dict[str, KnowledgeBlock] = {}
    for block in blocks:
        if block.canonical_key in seen_keys:
            raise KnowledgeIndexError(
                f"duplicate canonical_key {block.canonical_key}: "
                f"{seen_keys[block.canonical_key].id}, {block.id}"
            )
        seen_keys[block.canonical_key] = block


def _validate_unique_ids(blocks: list[KnowledgeBlock]) -> None:
    seen_ids: dict[str, KnowledgeBlock] = {}
    for block in blocks:
        if block.id in seen_ids:
            raise KnowledgeIndexError(
                f"duplicate id {block.id}: "
                f"{seen_ids[block.id].vault_relative_path}, {block.vault_relative_path}"
            )
        seen_ids[block.id] = block
