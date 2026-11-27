"""Acyclic L1 storage, path-relocation, and digest primitives for artifact manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PureWindowsPath
from typing import Any

from .io import canonical_json_bytes, read_json

MANIFEST_PATH = Path("00_项目管理") / "artifact_manifest.json"

ARTIFACT_STATUSES = {"candidate", "current", "stale", "superseded", "rejected"}

_FORMAL_PACKAGE_LOGICAL_IDS = {"narrative.package", "storyboard.package", "video.prompt"}


class _Manifest(dict):
    """In-memory read version; it adds no fields to the persisted JSON contract."""

    def __init__(self, value):
        super().__init__(value)
        self._source_bytes = canonical_json_bytes(value)


def load_manifest(project_root: Path) -> dict[str, Any]:
    """读取桌面项目产物清单；不存在时返回空清单。"""
    path = project_root / MANIFEST_PATH
    if not path.exists():
        return _Manifest({"schema_version": "1.0", "artifacts": []})
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError("artifact_manifest.json 顶层必须是对象")
    value.setdefault("artifacts", [])
    _assert_unique_artifact_ids(value)
    return _Manifest(value)


def _assert_unique_artifact_ids(manifest: dict[str, Any]) -> None:
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise ValueError("artifact_manifest.json artifacts 必须是数组")
    seen: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        artifact_id = item.get("artifact_id")
        if isinstance(artifact_id, str) and artifact_id:
            if artifact_id in seen:
                raise ValueError("artifact_manifest.json 含重复 artifact_id：" + artifact_id)
            seen.add(artifact_id)


def current_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in manifest.get("artifacts", []) if item.get("status") == "current"]


def project_path(root, raw):
    raw = str(raw)
    if re.match(r"^/[A-Za-z]:[/\\]", raw):
        raw = raw[1:]
    root = Path(root).resolve()
    # Explicit relocation aliases preserve original paths and confirmation bytes.
    # Never infer an old project root from its basename or revive absent files.
    aliases = json.loads(os.environ.get("MSB_PROJECT_PATH_MAP") or "{}")
    if not isinstance(aliases, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in aliases.items()
    ):
        raise ValueError("MSB_PROJECT_PATH_MAP must map source roots to local project roots")
    for source, destination in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        destination_root = Path(destination).expanduser().resolve()
        original = PureWindowsPath(source)
        if not original.is_absolute():
            raise ValueError("MSB_PROJECT_PATH_MAP source must be an absolute Windows root")
        try:
            relative = PureWindowsPath(raw).relative_to(original)
        except ValueError:
            continue
        mapped = destination_root.joinpath(*relative.parts).resolve()
        if ".." in relative.parts or not mapped.is_relative_to(destination_root):
            raise ValueError("Relocated project path escapes its configured root")
        return mapped
    path = Path(raw.replace("\\", "/"))
    return (path if path.is_absolute() else root / path).resolve()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cache_file_reads(read_file, *, check_for_changes=True):
    """Reuse a read only while the file's identity and version still match."""
    reads = {}

    def file_version(path):
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def read(path):
        version = file_version(path) if check_for_changes else None
        cached = reads.get(path)
        if cached is not None and cached[0] == version:
            return cached[1]
        result = read_file(path)
        if not check_for_changes or version == file_version(path):
            reads[path] = (version, result)
        return result

    return read
