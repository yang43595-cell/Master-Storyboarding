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
from .io import file_lock, read_json, utc_now, write_json
from .project_state import load_project_state

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
    _assert_unique_artifact_ids(value)
    return value


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


def save_manifest(project_root: Path, manifest: dict[str, Any]) -> None:
    # The manifest writer is used by many non-manuscript asset workflows. Keep
    # it available for history/staleness updates, but never let it mint a new
    # formal current version. Only the transactional adoption and revision
    # commit functions may perform that transition.
    _assert_unique_artifact_ids(manifest)
    previous = load_manifest(project_root)
    prior_current = {item.get("artifact_id"): item for item in previous.get("artifacts", [])
                     if item.get("logical_id") in _FORMAL_PACKAGE_LOGICAL_IDS
                     and item.get("status") == "current"}
    new_by_id = {item.get("artifact_id"): item for item in manifest.get("artifacts", [])}
    removed = set(prior_current) - set(new_by_id)
    if removed:
        raise ValueError("save_manifest 不能删除 current 正式稿件历史记录")
    immutable = ("logical_id", "type", "revision", "path_or_url", "checksum", "depends_on", "user_approved")
    for artifact_id, existing in prior_current.items():
        item = new_by_id[artifact_id]
        if any(item.get(key) != existing.get(key) for key in immutable):
            raise ValueError("save_manifest 不能改写 current 正式稿件身份、来源或确认 hash；请创建经校验的新修订")
    for item in manifest.get("artifacts", []):
        if item.get("logical_id") not in _FORMAL_PACKAGE_LOGICAL_IDS or item.get("status") != "current":
            continue
        existing = prior_current.get(item.get("artifact_id"))
        if existing is None:
            raise ValueError("save_manifest 不能晋升或恢复正式稿件；请使用 adopt-package 或 revision_control.commit_change")
    manifest["updated_at"] = utc_now()
    write_json(project_root / MANIFEST_PATH, manifest)
    # The manifest owns validity; project_state only selects which valid IDs
    # the current task uses. Never select additional artifacts while syncing.
    from .project_state import sync_current_artifacts
    sync_current_artifacts(project_root, manifest)


_FORMAL_PACKAGE_LOGICAL_IDS = {"narrative.package", "storyboard.package", "video.prompt"}


def validate_candidate_submission(project_root: Path, entry: dict[str, Any],
                                 manifest: dict[str, Any] | None = None) -> str | None:
    """Recheck platform-neutral source contracts at a formal adoption boundary.

    Candidate drafting/registration stays permissive. The supported adoption
    APIs call this immediately before making a manuscript current, so stale
    upstream approvals or a drifted file cannot pass on an earlier review.
    This validates local project contracts only; it does not certify meaning,
    quality, or an external delivery platform.
    """
    logical_id = entry.get("logical_id")
    if logical_id not in _FORMAL_PACKAGE_LOGICAL_IDS:
        return
    root = Path(project_root).resolve()
    manifest = manifest if manifest is not None else load_manifest(root)
    state = load_project_state(root)
    path = project_path(root, entry.get("path_or_url", ""))
    if not path.is_file() or not path.is_relative_to(root):
        raise ValueError("正式稿件必须是项目内可读文件")
    # Older Markdown/event packages remain readable as history, but no longer
    # qualify for current formal status through a supported write API.
    if path.suffix.lower() != ".json":
        raise ValueError("旧 Markdown/事件包仅保留历史读取；正式稿采用须使用结构化 JSON 契约")
    candidate_sha256 = digest(path)
    if entry.get("checksum") and candidate_sha256 != entry["checksum"]:
        raise ValueError("候选稿件字节已偏离登记 hash")
    try:
        payload = read_json(path)
    except (OSError, ValueError) as exc:
        raise ValueError("正式稿件 JSON 不可读：" + str(path)) from exc
    if not isinstance(payload, dict):
        raise ValueError("正式稿件必须是 JSON 对象")
    if payload.get("revision") and payload["revision"] != entry.get("revision"):
        raise ValueError("正式稿件正文 revision 与登记版本不一致")

    # Preserve each package's real dependencies: narrative lineage/evidence,
    # storyboard's exact confirmed narrative, and video prompt's exact board.
    validate_current_dependencies(entry, manifest, state, root)
    if logical_id == "narrative.package":
        if "story_paragraphs" not in payload:
            raise ValueError("正式 narrative.package 必须包含 story_paragraphs 来源对账")
        from .story_sources import validate_story_sources
        validate_story_sources(payload, project_root=root)
    elif logical_id == "storyboard.package":
        if payload.get("schema_version") != "4.0":
            raise ValueError("旧导演事件包仅保留历史读取；正式 storyboard.package 须使用 4.0 JSON 契约")
        narrative = _confirmed_package_source(root, manifest, state, payload.get("source_narrative"),
                                              "narrative.package")
        from .story_sources import validate_story_sources
        validate_story_sources(narrative, payload, project_root=root)
    else:
        from .quality_evidence import validate_native_binding_claims
        validate_native_binding_claims(payload, root, entry.get('revision'))
        # Existing frozen delivery JSON is a channel adapter artifact. Its
        # native readback gate remains in _assert_review_current; it is not the
        # platform-neutral video manuscript contract.
        if "target_url" in payload and "drafts" in payload:
            raise ValueError("渠道冻结快照不是平台无关 video.prompt 正式稿件")
        source = payload.get("source_storyboard")
        if not isinstance(source, dict):
            if not any(key in payload for key in ("generation_segments", "creative_sections")):
                raise ValueError("旧视频提示包仅保留历史读取；正式 video.prompt 须包含 generation_segments")
            raise ValueError("正式视频提示词必须绑定准确 source_storyboard")
        storyboard = _confirmed_package_source(root, manifest, state, source, "storyboard.package")
        narrative_ref = storyboard.get("source_narrative")
        narrative = _confirmed_package_source(root, manifest, state, narrative_ref, "narrative.package")
        from .story_sources import validate_story_sources
        validate_story_sources(narrative, storyboard, project_root=root)
        from . import revision_control
        if revision_control._generation_segments(payload) is None:
            raise ValueError("正式视频提示词必须包含真实 generation_segments，材料分组不能代替生成段")
        try:
            from ..check_video_prompt_focal_intent import _json_segments
        except ImportError:
            from check_video_prompt_focal_intent import _json_segments
        try:
            _json_segments(json.dumps(payload, ensure_ascii=False))
        except ValueError as exc:
            raise ValueError("正式视频提示词生成段/创作段引用不一致：" + str(exc)) from exc
        from .quality_evidence import source_shot_ids
        known_shots = {shot["shot_id"] for scene in storyboard.get("scenes", [])
                       for shot in scene.get("shots", [])}
        for row in revision_control._generation_segments(payload):
            unknown = set(source_shot_ids(row)) - known_shots
            if unknown:
                raise ValueError("视频来源镜号不属于准确已确认导演包：" + ", ".join(sorted(unknown)))
        prior = [item for item in manifest.get("artifacts", [])
                 if item.get("logical_id") == logical_id and item.get("status") == "current"]
        if len(prior) > 1:
            raise ValueError("视频提示词旧版不唯一；生成段续跑基线未知")
        if prior:
            revision_control._validate_segment_reconciliation(root, prior[0], payload)
    if digest(path) != candidate_sha256:
        raise ValueError("候选稿件在提交校验期间发生变化；重新登记并审核")
    return candidate_sha256


def _confirmed_package_source(root: Path, manifest: dict[str, Any], state: dict[str, Any],
                              reference: Any, logical_id: str) -> dict[str, Any]:
    if not isinstance(reference, dict):
        raise ValueError("正式稿件缺少准确来源引用：" + logical_id)
    matches = [item for item in manifest.get("artifacts", [])
               if item.get("logical_id") == logical_id and item.get("status") == "current"]
    if len(matches) != 1:
        raise ValueError("正式稿件上游必须有唯一 current：" + logical_id)
    source = matches[0]
    if not artifact_is_confirmed(source, state, root):
        raise ValueError("正式稿件上游 current 缺少有效确认：" + logical_id)
    path = project_path(root, source.get("path_or_url", ""))
    if (not path.is_file() or not source.get("checksum")
            or digest(path) != source["checksum"]):
        raise ValueError("正式稿件上游文件缺失或 hash 已过期：" + logical_id)
    if (reference.get("revision") != source.get("revision")
            or (reference.get("artifact_id") and reference["artifact_id"] != source.get("artifact_id"))
            or (reference.get("logical_id") and reference["logical_id"] != logical_id)
            or (reference.get("path") and project_path(root, reference["path"]) != path)):
        raise ValueError("正式稿件来源引用与已确认 current 身份/修订/路径不一致：" + logical_id)
    try:
        value = read_json(path)
    except (OSError, ValueError) as exc:
        raise ValueError("正式稿件上游 JSON 不可读：" + logical_id) from exc
    if not isinstance(value, dict):
        raise ValueError("正式稿件上游必须是 JSON 对象：" + logical_id)
    if value.get("revision") != source.get("revision"):
        raise ValueError("正式稿件上游正文 revision 与登记版本不一致：" + logical_id)
    return value


def adopt_initial_package(project_root: Path, artifact_id: str, approval_ref: str,
                          *, scope_description: str = "") -> dict[str, Any]:
    """Atomically confirm and select a first formal package in an empty lineage."""
    from .candidate_test import require_production
    require_production(project_root)
    if not approval_ref.strip():
        raise ValueError("正式稿采用必须提供准确 approval_ref")
    root = Path(project_root).resolve()
    from . import revision_control
    with file_lock(root / revision_control.LOCK_RELATIVE):
        manifest = load_manifest(root)
        entry = next((item for item in manifest.get("artifacts", [])
                      if item.get("artifact_id") == artifact_id), None)
        if entry is None or entry.get("logical_id") not in _FORMAL_PACKAGE_LOGICAL_IDS:
            raise ValueError("找不到待正式采用的叙事/导演/视频稿件候选")
        if entry.get("status") != "candidate":
            raise ValueError("首次正式采用只能从 candidate 开始")
        logical_id = entry["logical_id"]
        if any(item.get("logical_id") == logical_id and item.get("status") in {"current", "stale"}
               for item in manifest.get("artifacts", [])):
            raise ValueError("该逻辑产物已有 current/stale 版本；返修须使用 revision_control")
        submission_sha256 = validate_candidate_submission(root, entry, manifest)
        if not submission_sha256:
            raise ValueError("正式采用入口只接受当前结构化 JSON 稿件")
        path = project_path(root, entry.get("path_or_url", ""))
        if digest(path) != submission_sha256:
            raise ValueError("候选稿件在整稿确认期间发生变化；重新登记并审核")
        entry["checksum"] = submission_sha256
        entry["status"] = "current"
        entry["user_approved"] = True
        receipt = confirmation_scope.new_receipt(
            confirmation_scope.SCOPE_ARTIFACT,
            description=scope_description,
            extra={"kind": "package-adoption", "artifact_ids": [artifact_id],
                   "logical_id": logical_id, "revision": entry.get("revision"),
                   "sha256": submission_sha256, "approval_ref": approval_ref,
                   "at": utc_now()},
        )
        state = load_project_state(root)
        state.setdefault("user_confirmations", []).append(receipt)
        state["current_artifacts"] = list(dict.fromkeys([*state.get("current_artifacts", []), artifact_id]))
        transaction = revision_control._commit_json_transaction_unlocked(root, {
            root / MANIFEST_PATH: manifest,
            root / "00_项目管理" / "project_state.json": state,
        }, reason="adopt initial formal package")
        adopted = next(item for item in load_manifest(root)["artifacts"]
                       if item.get("artifact_id") == artifact_id)
        if not artifact_is_confirmed(adopted, load_project_state(root), root):
            raise ValueError("登记事务已提交但确认回读未通过；不得交付或进入下游")
        return {"artifact": adopted, "confirmation": receipt, "transaction": transaction}


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
    if (entry.get("logical_id") in _FORMAL_PACKAGE_LOGICAL_IDS
            and entry.get("status", "candidate") != "candidate"):
        raise ValueError("正式稿件只能先登记为 candidate；须经采用或变更提交入口完成来源/分段校验")
    from .candidate_test import context
    testing = context(project_root)
    if testing:
        if entry.get("status", "candidate") != "candidate" or entry.get("user_approved", False) is not False:
            raise ValueError("候选测试不能登记正式产物")
        entry.update(candidate_test=testing["run_id"], review_status="unreviewed", production_blocked=True)
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
    from .candidate_test import require_production
    require_production(project_root)
    manifest = load_manifest(project_root)
    new_item = next((item for item in manifest.get("artifacts", []) if item.get("artifact_id") == artifact_id), None)
    if new_item is None:
        raise ValueError(f"找不到产物：{artifact_id}")
    if new_item.get("logical_id") != logical_id:
        raise ValueError("候选的 logical_id 与替换目标不一致")
    if logical_id in _FORMAL_PACKAGE_LOGICAL_IDS and new_item.get("status") != "candidate":
        raise ValueError("只能正式采用 candidate 产物")
    submission_sha256 = validate_candidate_submission(project_root, new_item, manifest)
    if logical_id in _FORMAL_PACKAGE_LOGICAL_IDS and not submission_sha256:
        raise ValueError("旧稿件/未识别结构不能晋升为 current；仅保留历史读取")
    if logical_id in _FORMAL_PACKAGE_LOGICAL_IDS:
        raise ValueError("通用 promote_candidate 不能正式采用稿件；请使用 adopt-package 或 revision_control.commit_change")
    active = [item for item in manifest.get("artifacts", [])
              if item.get("logical_id") == logical_id
              and item.get("artifact_id") != artifact_id
              and item.get("status") in {"current", "stale"}]
    if len(active) > 1:
        raise ValueError("同一 logical_id 对应多个有效/待返工产物，先明确独立替换对象，不能整组覆盖")
    # Compute on the adopted graph before replacement; existing manual callers
    # may already have invalidated it, so stale rows must remain idempotent.
    if active:
        mark_impacted_artifacts_stale(manifest, {logical_id},
                                     "上游产物已采用新版本，需复核准确下游依赖", new_item.get("revision", ""))
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
    if submission_sha256 and not new_item.get("checksum"):
        new_item["checksum"] = submission_sha256
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
    if entry.get('candidate_test') or state.get('candidate_test'):
        return False
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
