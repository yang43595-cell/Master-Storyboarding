"""桌面项目产物清单与依赖失效图。

`00_项目管理/artifact_manifest.json` 是桌面项目的机器可读产物视图，只管理状态：
candidate / current / stale / superseded / rejected。本模块绝不移动或
删除用户文件；历史版本由 Git 归档承担。

失效采用两阶段替代：上游变化 → 直接或间接依赖它的 current 产物标为 stale（文件
保留）→ 生成并确认替代版本 → 新版本 current、旧版本 superseded。
"""

from __future__ import annotations

import hashlib
import json
import os
import re

from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from . import confirmation_scope
from .io import read_json, utc_now, write_json

MANIFEST_PATH = Path("00_项目管理") / "artifact_manifest.json"

ARTIFACT_STATUSES = {"candidate", "current", "stale", "superseded", "rejected"}


@dataclass(frozen=True)
class InvalidationReport:
    stale_ids: set[str]
    reason: str
    revision: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stale_ids": sorted(self.stale_ids),
            "reason": self.reason,
            "revision": self.revision,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PromotionReport:
    logical_id: str
    new_artifact_id: str
    superseded_artifact_ids: list[str]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_id": self.logical_id,
            "new_artifact_id": self.new_artifact_id,
            "superseded_artifact_ids": list(self.superseded_artifact_ids),
            "warnings": list(self.warnings),
        }


def load_manifest(project_root: Path) -> dict[str, Any]:
    """读取桌面项目产物清单；不存在时返回空清单。"""
    path = project_root / MANIFEST_PATH
    if not path.exists():
        return {"schema_version": "1.0", "artifacts": []}
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError("artifact_manifest.json 顶层必须是对象")
    value.setdefault("artifacts", [])
    return value


def save_manifest(project_root: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    write_json(project_root / MANIFEST_PATH, manifest)
    # The manifest owns validity; project_state only selects which valid IDs
    # the current task uses. Never select additional artifacts while syncing.
    from .project_state import sync_current_artifacts
    sync_current_artifacts(project_root, manifest)


def current_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in manifest.get("artifacts", []) if item.get("status") == "current"]


def dependency_closure(manifest: dict[str, Any], roots: set[str]) -> set[str]:
    """Follow the adopted branch, preferring current over stale for each ID.

    A stale version still carries the path to its current downstream dependents
    until it is replaced. Candidates and replaced/rejected versions are history,
    so their dependencies cannot invalidate the adopted branch.
    """
    active = current_artifacts(manifest)
    current_logical_ids = {item.get("logical_id") for item in active}
    active += [item for item in manifest.get("artifacts", [])
               if item.get("status") == "stale"
               and item.get("logical_id") not in current_logical_ids]
    dependents: dict[str, set[str]] = {}
    for item in active:
        logical_id = item.get("logical_id")
        if logical_id:
            for dependency in item.get("depends_on") or []:
                dependents.setdefault(str(dependency), set()).add(str(logical_id))
    impacted: set[str] = set()
    pending = set(roots)
    while pending:
        current = pending.pop()
        if current not in impacted:
            impacted.add(current)
            pending.update(dependents.get(current, set()))
    return impacted


def register_workspace_artifact(project_root: Path, artifact: dict[str, Any]) -> dict[str, Any]:
    """登记一个新产物（默认 candidate）。不覆盖用户已有确认，不删除任何文件。"""
    required = {"artifact_id", "logical_id", "type", "path_or_url"}
    missing = sorted(required - set(artifact))
    if missing:
        raise ValueError(f"产物缺少字段：{missing}")
    manifest = load_manifest(project_root)
    if any(item.get("artifact_id") == artifact.get("artifact_id") for item in manifest.get("artifacts", [])):
        raise ValueError(f"产物已存在：{artifact.get('artifact_id')}")
    entry = dict(artifact)
    entry.setdefault("status", "candidate")
    entry.setdefault("revision", "")
    entry.setdefault("depends_on", [])
    entry.setdefault("user_approved", False)
    entry.setdefault("created_at", utc_now())
    if entry["status"] not in ARTIFACT_STATUSES:
        raise ValueError(f"未知产物状态：{entry['status']}")
    manifest.setdefault("artifacts", []).append(entry)
    save_manifest(project_root, manifest)
    return entry


def mark_impacted_artifacts_stale(
    manifest: dict[str, Any],
    changed_ids: set[str],
    reason: str,
    revision: str,
) -> InvalidationReport:
    """把直接或间接依赖变化项的 current 产物标为 stale。

    按 `depends_on` 反向遍历：从变化项出发，沿依赖边找到所有受影响的 logical_id，
    只修改清单状态，不移动或删除文件。
    """
    changed = set(changed_ids)
    if not changed:
        return InvalidationReport(set(), reason, revision)
    impacted = dependency_closure(manifest, changed)
    stale_ids: set[str] = set()
    now = utc_now()
    for item in manifest.get("artifacts", []):
        logical_id = item.get("logical_id")
        if item.get("status") == "current" and logical_id in impacted:
            stale_ids.add(str(logical_id))
            item["status"] = "stale"
            item.setdefault("stale_reasons", []).append({"reason": reason, "revision": revision, "at": now})
    return InvalidationReport(stale_ids, reason, revision)


def promote_candidate(project_root: Path, logical_id: str, artifact_id: str) -> PromotionReport:
    """确认新候选产物：新版本设 current，同一 logical_id 的旧版本变 superseded。"""
    manifest = load_manifest(project_root)
    new_item = next((item for item in manifest.get("artifacts", []) if item.get("artifact_id") == artifact_id), None)
    if new_item is None:
        raise ValueError(f"找不到产物：{artifact_id}")
    if new_item.get("logical_id") != logical_id:
        raise ValueError("候选的 logical_id 与替换目标不一致")
    active = [item for item in manifest.get("artifacts", [])
              if item.get("logical_id") == logical_id
              and item.get("artifact_id") != artifact_id
              and item.get("status") in {"current", "stale"}]
    if len(active) > 1:
        raise ValueError("同一 logical_id 对应多个有效/待返工产物，先明确独立替换对象，不能整组覆盖")
    superseded: list[str] = []
    now = utc_now()
    for item in manifest.get("artifacts", []):
        if item.get("logical_id") != logical_id or item.get("artifact_id") == artifact_id:
            continue
        if item.get("status") in {"current", "stale"}:
            superseded.append(item.get("artifact_id"))
            item["status"] = "superseded"
            item["superseded_at"] = now
    new_item["status"] = "current"
    save_manifest(project_root, manifest)
    return PromotionReport(logical_id, artifact_id, superseded)


def project_path(root, raw):
    raw = str(raw)
    if re.match(r'^/[A-Za-z]:[/\\]', raw):
        raw = raw[1:]
    root = Path(root).resolve()
    # Explicit relocation aliases preserve original paths and confirmation bytes.
    # Never infer an old project root from its basename or revive absent files.
    aliases = json.loads(os.environ.get("MSB_PROJECT_PATH_MAP") or "{}")
    if not isinstance(aliases, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                            for k, v in aliases.items()):
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


def verify_image_confirmation(path, receipt):
    """Verify new byte-bound receipts; do not invent hashes for older approvals."""
    if "image_sha256" not in receipt:
        return
    expected = receipt["image_sha256"]
    path = Path(path)
    if (not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)
            or not path.is_file() or digest(path) != expected):
        raise ValueError("已确认图片内容已变化或缺失，请保留原记录并重新选图确认：" + str(path))


def formal_adoption(entry, root):
    """Return the completed, hash-bound adoption evidence for a bundled artifact."""
    control = Path(root) / '00_项目管理' / 'change_control.json'
    if not control.is_file():
        return None
    history = read_json(control).get('history', [])
    for change in history:
        receipt = change.get('candidate_confirmation') or {}
        if not _whole_artifact_receipt(receipt):
            continue
        bundle = (change.get('candidate_receipt') or {}).get('bundle') or []
        if (change.get('status') != 'completed' or not receipt.get('approval_ref')
                or receipt.get('change_id') != change.get('change_id')):
            continue
        matched = next((item for item in bundle if item.get('artifact_id') == entry.get('artifact_id')
                        and item.get('sha256') == entry.get('checksum')), None)
        if matched:
            path = project_path(root, matched['path'])
            if path.is_file() and digest(path) == matched['sha256']:
                return {'change_id': change['change_id'], 'approval_ref': receipt['approval_ref'],
                        'sha256': matched['sha256']}
    return None


def _whole_artifact_receipt(receipt):
    """整稿确认判定只有 core.confirmation_scope 一个出口；本函数只做转发。"""
    return confirmation_scope.is_whole_artifact(receipt)


def artifact_is_confirmed(entry, state, root=None):
    if entry.get('status') != 'current' or entry.get('user_approved') is not True:
        return False
    artifact_id = entry.get('artifact_id')
    for receipt in state.get('user_confirmations', []):
        if not _whole_artifact_receipt(receipt):
            continue
        # Legacy workbench confirmations carry a bundle.  Revision control's
        # formal candidate confirmation carries the adopted artifact directly.
        # Both are explicit user-confirmation receipts; neither is inferred
        # merely from a current artifact or an automated review.
        if artifact_id in receipt.get('artifact_ids', []):
            return True
        if (receipt.get('kind') == 'revision-candidate'
                and receipt.get('artifact_id') == artifact_id
                and receipt.get('sha256') == entry.get('checksum')
                and receipt.get('approval_ref')):
            return True
    # Bundled revision control retains its signed candidate receipt in the
    # completed change history.  Read that canonical evidence directly rather
    # than manufacturing a duplicate project-state receipt for every member.
    return bool(root and formal_adoption(entry, root))


def validate_current_dependencies(entry, manifest, state, root):
    """Read-only consumption check; never resurrect historical files as current.

    Logical edges establish availability, not version equality. Existing explicit
    source_narrative/source_storyboard refs establish exact revision/path identity.
    """
    visiting, checked = set(), set()
    def visit(item):
        ident = item.get('artifact_id')
        if ident in visiting:
            raise ValueError('产物依赖存在循环：' + str(ident))
        if ident in checked:
            return
        visiting.add(ident)
        deps = item.get('depends_on') or []
        if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
            raise ValueError('depends_on 必须是 logical_id 列表')
        path = project_path(root, item.get('path_or_url', ''))
        payload = {}
        if path.is_file() and path.suffix.lower() == '.json':
            try:
                value = read_json(path)
                if isinstance(value, dict):
                    payload = value
            except (OSError, ValueError) as exc:
                raise ValueError('依赖来源 JSON 不可读：' + str(path)) from exc
        named = {'narrative.package': 'source_narrative', 'storyboard.package': 'source_storyboard'}
        # An explicit reference is also a dependency even when a legacy edge was omitted.
        required = set(deps) | {lid for lid, field in named.items() if field in payload}
        for logical_id in sorted(required):
            matches = [a for a in manifest.get('artifacts', [])
                       if a.get('logical_id') == logical_id and a.get('status') == 'current']
            if len(matches) != 1:
                raise ValueError('上游必须有唯一 current，不能回退历史：' + logical_id)
            source = matches[0]
            if not artifact_is_confirmed(source, state, root):
                raise ValueError('上游 current 缺少有效确认：' + logical_id)
            source_path = project_path(root, source.get('path_or_url', ''))
            if not source_path.is_file() or (source.get('checksum') and digest(source_path) != source['checksum']):
                raise ValueError('上游文件缺失或已偏离登记字节：' + logical_id)
            ref = payload.get(named.get(logical_id, ''))
            if ref is not None:
                if not isinstance(ref, dict) or ref.get('revision') != source.get('revision'):
                    raise ValueError('来源修订与 current 上游不一致：' + logical_id)
                if any(key in ref and ref[key] != source.get(key) for key in ('artifact_id', 'logical_id')):
                    raise ValueError('来源身份与 current 上游不一致：' + logical_id)
                if ref.get('path') and project_path(root, ref['path']) != source_path:
                    raise ValueError('来源路径与 current 上游不一致：' + logical_id)
            visit(source)
        visiting.remove(ident)
        checked.add(ident)
    visit(entry)
