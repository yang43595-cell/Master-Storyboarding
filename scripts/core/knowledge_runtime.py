"""Portable, repository-owned runtime knowledge.

This module only resolves and verifies bytes. It never synchronizes upstream
content, changes lifecycle state, or approves a method or rule.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


FILE_ROLES = {"method", "reference_only", "adoption_index", "required_dynamic_source", "template", "tombstone"}


class KnowledgeRuntimeError(ValueError):
    pass


def bundled_root(repo_root: Path | None = None) -> Path:
    repo = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    root = repo / "knowledge" / "runtime"
    if not root.resolve().is_relative_to(repo.resolve()):
        raise KnowledgeRuntimeError("bundled knowledge root escapes repository")
    return root.resolve()


def resolve_knowledge_root(explicit: str | Path | None = None, *, repo_root: Path | None = None) -> Path:
    root = bundled_root(repo_root)
    if explicit is not None and (
        not str(explicit).strip() or Path(explicit).expanduser().resolve() != root
    ):
        raise KnowledgeRuntimeError("production knowledge must use the repository's knowledge/runtime bundle")
    verify_bundle(root)
    return root


def is_bundled_root(root: Path) -> bool:
    root = Path(root).absolute()
    # Include a missing manifest: deleting it must never enable legacy discovery.
    return (root.name == "runtime" and root.parent.name == "knowledge") or root.resolve() == bundled_root()


def safe_knowledge_file(root: Path, relative: str) -> Path:
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or ":" in relative or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise KnowledgeRuntimeError(f"unsafe knowledge path: {relative!r}")
    target = (Path(root) / relative).resolve()
    if not target.is_relative_to(Path(root).resolve()):
        raise KnowledgeRuntimeError(f"knowledge path escapes root: {relative}")
    if not target.is_file():
        raise KnowledgeRuntimeError(f"knowledge file missing: {relative}")
    return target


def verify_bundle(root: Path | None = None) -> dict[str, Any]:
    root = Path(root) if root is not None else bundled_root()
    if not root.resolve().is_relative_to(root.parent.resolve()):
        raise KnowledgeRuntimeError("knowledge runtime root escapes bundle")
    manifest_path = root.parent / "manifest.json"
    if not manifest_path.resolve().is_relative_to(root.parent.resolve()):
        raise KnowledgeRuntimeError("knowledge manifest escapes bundle")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KnowledgeRuntimeError(f"cannot read knowledge manifest: {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise KnowledgeRuntimeError("knowledge manifest files must be an array")
    ids = manifest.get("dynamic_rule_ids")
    if (not isinstance(ids, list) or any(not isinstance(item, str) or not item for item in ids)
            or len(ids) != len(set(ids))):
        raise KnowledgeRuntimeError("knowledge manifest dynamic_rule_ids must be a unique string array")
    seen: set[str] = set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise KnowledgeRuntimeError("knowledge manifest file entry must be an object")
        relative, checksum, role = entry.get("path"), entry.get("sha256"), entry.get("role")
        target = safe_knowledge_file(root, relative)
        if relative in seen:
            raise KnowledgeRuntimeError(f"duplicate knowledge manifest path: {relative}")
        seen.add(relative)
        if not isinstance(role, str) or role not in FILE_ROLES or not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise KnowledgeRuntimeError(f"invalid knowledge manifest role/hash: {relative}")
        if hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
            raise KnowledgeRuntimeError(f"knowledge file hash mismatch: {relative}")
    return manifest


def verify_selected_file(root: Path, relative: str) -> Path:
    target = safe_knowledge_file(root, relative)
    if is_bundled_root(root):
        manifest = verify_bundle(root)
        if relative not in {entry["path"] for entry in manifest["files"]}:
            raise KnowledgeRuntimeError(f"knowledge file not selected in manifest: {relative}")
    return target


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Verify the repository-owned runtime knowledge bundle")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    try:
        result = verify_bundle(args.root)
    except KnowledgeRuntimeError as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps({"ok": True, "files": len(result["files"]), "dynamic_rule_ids": result["dynamic_rule_ids"]}, ensure_ascii=False))
