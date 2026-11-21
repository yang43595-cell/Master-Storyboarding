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
from .io import project_lock, project_locked, read_json, sha256_json, utc_now, write_json
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


class _Manifest(dict):
    """In-memory read version; it adds no fields to the persisted JSON contract."""
    def __init__(self, value):
        super().__init__(value)
        self._source_digest = sha256_json(value)


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


@project_locked
def save_manifest(project_root: Path, manifest: dict[str, Any]) -> None:
    # The manifest writer is used by many non-manuscript asset workflows. Keep
    # it available for history/staleness updates, but never let it mint a new
    # formal current version. Only the transactional adoption and revision
    # commit functions may perform that transition.
    _assert_unique_artifact_ids(manifest)
    previous = load_manifest(project_root)
    prior_protected = {item.get("artifact_id"): item for item in previous.get("artifacts", [])
                       if item.get("agent_acceptance") or
                       item.get("asset_review_sha256") and item.get("status") != "candidate" or
                       item.get("logical_id") in _FORMAL_PACKAGE_LOGICAL_IDS and item.get("status") == "current"}
    prior_current = {ident: item for ident, item in prior_protected.items() if item.get("status") == "current"}
    guarded_logical_ids = _FORMAL_PACKAGE_LOGICAL_IDS | {
        item.get("logical_id") for item in prior_protected.values() if item.get("agent_acceptance")}
    new_by_id = {item.get("artifact_id"): item for item in manifest.get("artifacts", [])}
    removed = set(prior_protected) - set(new_by_id)
    if removed:
        raise ValueError("save_manifest 不能删除 current 正式稿件历史记录")
    immutable = ("logical_id", "type", "revision", "path_or_url", "checksum", "depends_on",
                 "user_approved", "agent_acceptance")
    for artifact_id, existing in prior_protected.items():
        item = new_by_id[artifact_id]
        protected_fields = immutable + (("quality_evidence", "product_reference") if existing.get("agent_acceptance") else ())
        if existing.get("asset_review_sha256"):
            protected_fields += ("asset_review_sha256", "confirmation_ref")
        if any(item.get(key) != existing.get(key) for key in protected_fields):
            raise ValueError("save_manifest 不能改写 current 正式稿件身份、来源或确认 hash；请创建经校验的新修订")
    for item in manifest.get("artifacts", []):
        if (item.get("logical_id") not in guarded_logical_ids and not item.get("agent_acceptance")
                or item.get("status") != "current"):
            continue
        existing = prior_current.get(item.get("artifact_id"))
        if existing is None:
            raise ValueError("save_manifest 不能晋升或恢复正式稿件；请使用 adopt-package 或 revision_control.commit_change")
    source_digest = getattr(manifest, "_source_digest", None)
    if source_digest is not None and source_digest != sha256_json(previous):
        raise ValueError("产物清单已被其他任务更新；重新读取后重试，不能覆盖新记录")
    manifest["updated_at"] = utc_now()
    write_json(project_root / MANIFEST_PATH, manifest)
    # The manifest owns validity; project_state only selects which valid IDs
    # the current task uses. Never select additional artifacts while syncing.
    from .project_state import sync_current_artifacts
    sync_current_artifacts(project_root, manifest)
    if source_digest is not None:
        manifest._source_digest = sha256_json(manifest)


_FORMAL_PACKAGE_LOGICAL_IDS = {"narrative.package", "storyboard.package", "video.prompt"}


def exact_narrative_approval(receipt: Any, entry: dict[str, Any], sha256: str) -> bool:
    if not isinstance(receipt, dict) or receipt.get("scope") != confirmation_scope.SCOPE_ARTIFACT:
        return False
    if not confirmation_scope.is_whole_artifact(receipt) or receipt.get("sha256") != sha256:
        return False
    approval_ref = receipt.get("approval_ref")
    if entry.get("logical_id") != "narrative.package" or not isinstance(approval_ref, str) or not approval_ref.strip():
        return False
    if receipt.get("kind") == "package-adoption":
        return (receipt.get("artifact_ids") == [entry.get("artifact_id")]
                and receipt.get("logical_id") == "narrative.package"
                and receipt.get("revision") == entry.get("revision"))
    return (receipt.get("kind") == "revision-candidate"
            and receipt.get("artifact_id") == entry.get("artifact_id")
            and receipt.get("revision") == entry.get("revision"))


def narrative_has_exact_approval(project_root: Path, source_path: Path, narrative: dict[str, Any],
                                 *, delegated=False, chat_subtitle_review_ids=()) -> bool:
    """Recognize the requested adoption basis for these exact narrative bytes."""
    root = Path(project_root).resolve()
    source_path = project_path(root, source_path)
    if not source_path.is_relative_to(root) or not source_path.is_file():
        return False
    state = load_project_state(root)
    for entry in load_manifest(root).get("artifacts", []):
        if (entry.get("logical_id") != "narrative.package" or entry.get("revision") != narrative.get("revision")
                or entry.get("status") != "current"):
            continue
        sha256 = entry.get("checksum")
        if not sha256:
            continue
        path = project_path(root, entry.get("path_or_url", ""))
        if path != source_path:
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha256 or json.loads(raw.decode("utf-8-sig")) != narrative:
            continue
        if delegated:
            if entry.get("agent_acceptance") and entry.get("user_approved") is False:
                try:
                    return _delegated_dialogue_passed(root, entry, state)
                except (ValueError, OSError, KeyError, TypeError):
                    return False
        elif artifact_is_confirmed(entry, state, root) and any(
                exact_narrative_approval(receipt, entry, sha256)
                and (not chat_subtitle_review_ids or (receipt.get('channel') == 'chat_confirmation'
                    and receipt.get('subtitle_review_ids') == list(chat_subtitle_review_ids)
                    and isinstance(receipt.get('review_markdown'), dict)))
                for receipt in state.get("user_confirmations", [])):
            return True
    return False


def _delegated_dialogue_passed(root, entry, state):
    from .agent_acceptance import validate_agent_acceptance
    report = validate_agent_acceptance(root, entry, state=state)
    return any(check["id"] == "CREATIVE-DIALOGUE" and check["result"] == "pass" for check in report["checks"])


def _video_revision_visual_rows(root, payload, segments, prior, director, known_shots, state):
    """Use only changed rows for the new visual check on a verified full revision."""
    from .quality_evidence import source_shot_ids
    from .revision_control import _generation_segments

    baseline_path = project_path(root, prior.get("path_or_url", ""))
    if (not baseline_path.is_relative_to(root) or baseline_path.suffix.lower() != ".json"
            or not baseline_path.is_file() or prior.get("artifact_id") not in state["current_artifacts"]
            or not artifact_is_adopted(prior, state, root)
            or payload["source_storyboard"].get("sha256") != director["checksum"]):
        return segments
    try:
        raw = baseline_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != prior.get("checksum"):
            return segments
        baseline = json.loads(raw.decode("utf-8"))
        previous = _generation_segments(baseline)
    except (OSError, ValueError):
        return segments
    if not previous or not isinstance(baseline.get("generation_segments"), list):
        return segments

    # Only the candidate identity, author self-check, and revision reconciliation
    # may differ outside of the explicit generation rows.
    ignored = {"generation_segments", "revision", "document", "self_check", "segment_reconciliation"}
    before = {key: value for key, value in baseline.items() if key not in ignored}
    after = {key: value for key, value in payload.items() if key not in ignored}
    if sha256_json(before) != sha256_json(after):
        return segments
    if ([row["segment_id"] for row in previous] != [row["segment_id"] for row in segments]
            or {shot for row in previous for shot in source_shot_ids(row)} != known_shots):
        return segments
    if any(source_shot_ids(old) != source_shot_ids(new)
           or any(old.get(key) != new.get(key) for key in ("source_dialogue_ids", "source_paragraph_ids"))
           for old, new in zip(previous, segments)):
        return segments
    return [new for old, new in zip(previous, segments) if sha256_json(old) != sha256_json(new)]


def validate_candidate_submission(project_root: Path, entry: dict[str, Any],
                                 manifest: dict[str, Any] | None = None, *, reading_existing: bool = False,
                                 approval_receipt: dict[str, Any] | None = None,
                                 state=None, checked=None, file_digest=None) -> str | None:
    """Recheck platform-neutral source contracts at a formal adoption boundary.

    Candidate drafting/registration stays permissive. The supported adoption
    APIs call this immediately before making a manuscript current, so stale
    upstream approvals or a drifted file cannot pass on an earlier review.
    This validates local project contracts only; it does not certify meaning,
    quality, or an external delivery platform.

    Reading an already adopted baseline for upstream repair does not require
    newly introduced fields. Adoption and delivery keep the strict default.
    A caller reusing dependency checks must pass that operation's state.
    """
    logical_id = entry.get("logical_id")
    if logical_id not in _FORMAL_PACKAGE_LOGICAL_IDS:
        return
    root = Path(project_root).resolve()
    manifest = manifest if manifest is not None else load_manifest(root)
    state = load_project_state(root) if state is None else state
    path = project_path(root, entry.get("path_or_url", ""))
    if not path.is_file() or not path.is_relative_to(root):
        raise ValueError("正式稿件必须是项目内可读文件")
    # Older Markdown/event packages remain readable as history, but no longer
    # qualify for current formal status through a supported write API.
    if path.suffix.lower() != ".json":
        raise ValueError("旧 Markdown/事件包仅保留历史读取；正式稿采用须使用结构化 JSON 契约")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("正式稿件 JSON 不可读：" + str(path)) from exc
    candidate_sha256 = hashlib.sha256(raw).hexdigest()
    if entry.get("checksum") and candidate_sha256 != entry["checksum"]:
        raise ValueError("候选稿件字节已偏离登记 hash")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ValueError("正式稿件 JSON 不可读：" + str(path)) from exc
    if not isinstance(payload, dict):
        raise ValueError("正式稿件必须是 JSON 对象")
    if payload.get("revision") and payload["revision"] != entry.get("revision"):
        raise ValueError("正式稿件正文 revision 与登记版本不一致")

    # Preserve each package's real dependencies: narrative lineage/evidence,
    # storyboard's exact confirmed narrative, and video prompt's exact board.
    validate_current_dependencies(entry, manifest, state, root, checked=checked, file_digest=file_digest)
    if logical_id == "narrative.package":
        if "story_paragraphs" not in payload:
            raise ValueError("正式 narrative.package 必须包含 story_paragraphs 来源对账")
        from .story_sources import validate_story_sources
        delegated = bool(entry.get("agent_acceptance") and _delegated_dialogue_passed(root, entry, state))
        exact = exact_narrative_approval(approval_receipt, entry, candidate_sha256)
        validate_story_sources(payload, project_root=root, narrative_path=path, require_dialogue_evidence=True,
                               require_character_output=not reading_existing,
                               require_promotion_dialogue=not reading_existing,
                               exact_human_approval=exact,
                               chat_subtitle_review_ids=(approval_receipt.get('subtitle_review_ids', ())
                                   if exact and approval_receipt.get('channel') == 'chat_confirmation' else ()),
                               delegated_creative_approval=delegated)
    elif logical_id == "storyboard.package":
        if payload.get("schema_version") != "4.0":
            raise ValueError("旧导演事件包仅保留历史读取；正式 storyboard.package 须使用 4.0 JSON 契约")
        if not reading_existing:
            from .quality_evidence import required_visuals
            required_visuals(payload)
        narrative, narrative_path = _read_confirmed_package_source(root, manifest, state, payload.get("source_narrative"),
                                                                    "narrative.package")
        from .story_sources import validate_story_sources
        validate_story_sources(narrative, payload, project_root=root, narrative_path=narrative_path,
                               require_dialogue_evidence=True)
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
        narrative, narrative_path = _read_confirmed_package_source(root, manifest, state, narrative_ref, "narrative.package")
        from .story_sources import validate_story_sources
        validate_story_sources(narrative, storyboard, project_root=root, narrative_path=narrative_path,
                               require_dialogue_evidence=True)
        from . import revision_control
        from .quality_evidence import director_shot_audio, replay_source_audio, source_shot_ids
        segments = revision_control._generation_segments(payload)
        if segments is None:
            raise ValueError("正式视频提示词必须包含真实 generation_segments，材料分组不能代替生成段")
        known_shots = {shot["shot_id"] for scene in storyboard.get("scenes", [])
                       for shot in scene.get("shots", [])}
        prior = [item for item in manifest.get("artifacts", [])
                 if item.get("logical_id") == logical_id and item.get("status") == "current"]
        if len(prior) > 1:
            raise ValueError("视频提示词旧版不唯一；生成段续跑基线未知")
        content_segment_ids = None
        if not reading_existing:
            from .asset_refs import resolve_final_images
            from .delivery_files import declared_input_roots, discover_dependencies
            from .visual_readiness import require_visual_readiness
            scope = payload.get("scope")
            if (payload.get("production_blocked") is True
                    or isinstance(scope, dict) and scope.get("unwritten_shot_ids")):
                raise ValueError("视频稿本轮范围仍有阻塞或未写镜号")
            director = next(item for item in manifest["artifacts"]
                            if item.get("logical_id") == "storyboard.package" and item.get("status") == "current")
            visual_rows = segments
            if (prior and entry.get("status") == "candidate" and entry.get("artifact_id") != prior[0]["artifact_id"]
                    and entry.get("depends_on") == prior[0].get("depends_on")):
                visual_rows = _video_revision_visual_rows(root, payload, segments, prior[0],
                                                          director, known_shots, state)
            content_segment_ids = [row['segment_id'] for row in visual_rows]
            if visual_rows:
                require_visual_readiness(root, storyboard, director,
                                              {shot for row in visual_rows for shot in source_shot_ids(row)},
                                              rows=visual_rows, manifest=manifest)
            asset_references = []
            for row in segments:
                if (row.get("production_blocked") is True or row.get("submit_ready") is False
                        or row.get("blockers")):
                    raise ValueError(f"{row['segment_id']}: 视频段未就绪")
                refs = row.get("materials", row.get("candidate_upload_order", []))
                if isinstance(refs, list):
                    asset_references.extend(
                        (f"{ref['asset_id']}@{ref['output_id']}", ref)
                        for ref in refs if isinstance(ref, dict) and ref.get("asset_id") and ref.get("output_id"))
            accepted = resolve_final_images(root, list(dict.fromkeys(key for key, _ in asset_references)))
            for key, ref in asset_references:
                output = accepted[key]
                if (not ref.get("path") or project_path(root, ref["path"]) != Path(output["path"]).resolve()
                        or ref.get("sha256") != output["sha256"]):
                    raise ValueError(f"{key}: 视频素材与已确认输出的路径或字节不一致")
            dependencies = discover_dependencies(
                root, input_roots=declared_input_roots(payload),
                payload=payload, verify_hashes=True)
            if not dependencies["ok"]:
                raise ValueError("视频稿输入不可用：" + "；".join(dependencies["errors"] + dependencies["uncheckable"]))
        for row in segments:
            unknown = set(source_shot_ids(row)) - known_shots
            if unknown:
                raise ValueError("视频来源镜号不属于准确已确认导演包：" + ", ".join(sorted(unknown)))
        if prior:
            revision_control._validate_segment_reconciliation(root, prior[0], payload)
        from .generation_boundaries import validate_generation_boundaries
        validate_generation_boundaries(
            payload, storyboard, root, require_physical=not reading_existing,
            source_artifact={'artifact_id': entry['artifact_id'], 'revision': entry['revision'],
                             'sha256': candidate_sha256}, state=state,
            prompt_content_segment_ids=content_segment_ids)
        if not reading_existing:
            shot_audio = director_shot_audio([storyboard])
            if shot_audio or any("source_audio" in row for row in segments):
                replay_source_audio(segments, shot_audio)
    if digest(path) != candidate_sha256:
        raise ValueError("候选稿件在提交校验期间发生变化；重新登记并审核")
    return candidate_sha256


def _confirmed_package_source(root: Path, manifest: dict[str, Any], state: dict[str, Any],
                              reference: Any, logical_id: str) -> dict[str, Any]:
    payload, _ = _read_confirmed_package_source(root, manifest, state, reference, logical_id)
    return payload


def _read_confirmed_package_source(root: Path, manifest: dict[str, Any], state: dict[str, Any],
                              reference: Any, logical_id: str) -> tuple[dict[str, Any], Path]:
    if not isinstance(reference, dict):
        raise ValueError("正式稿件缺少准确来源引用：" + logical_id)
    matches = [item for item in manifest.get("artifacts", [])
               if item.get("logical_id") == logical_id and item.get("status") == "current"]
    if len(matches) != 1:
        raise ValueError("正式稿件上游必须有唯一 current：" + logical_id)
    source = matches[0]
    if not artifact_is_adopted(source, state, root):
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
    return value, path


def adopt_initial_package(project_root: Path, artifact_id: str, approval_ref: str,
                          *, scope_description: str = "", website_approval=None, chat_approval=None) -> dict[str, Any]:
    """Atomically confirm and select a first formal package in an empty lineage."""
    from .candidate_test import require_production
    from .review_publication import require_script_approval
    if not approval_ref.strip():
        raise ValueError("正式稿采用必须提供准确 approval_ref")
    root = Path(project_root).resolve()
    from . import revision_control
    with project_lock(root):
        require_production(root)
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
        path = project_path(root, entry.get("path_or_url", ""))
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("正式稿件必须是项目内可读文件")
        current_sha256 = digest(path)
        require_script_approval(root, {**entry, "path_or_url": path.relative_to(root).as_posix(),
                                       "checksum": current_sha256}, approval_ref, website_approval,
                                chat_approval=chat_approval)
        receipt = confirmation_scope.new_receipt(
            confirmation_scope.SCOPE_ARTIFACT,
            description=scope_description,
            extra={"kind": "package-adoption", "artifact_ids": [artifact_id],
                   "logical_id": logical_id, "revision": entry.get("revision"),
                   "sha256": current_sha256, "approval_ref": approval_ref,
                   "at": utc_now(), **(chat_approval.receipt_fields() if chat_approval else {})},
        )
        submission_sha256 = validate_candidate_submission(root, entry, manifest, approval_receipt=receipt)
        if not submission_sha256:
            raise ValueError("正式采用入口只接受当前结构化 JSON 稿件")
        if digest(path) != submission_sha256 or receipt["sha256"] != submission_sha256:
            raise ValueError("候选稿件在整稿确认期间发生变化；重新登记并审核")
        entry["checksum"] = submission_sha256
        entry["status"] = "current"
        entry["user_approved"] = True
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


def validate_agent_submission(project_root: Path, entry: dict[str, Any], manifest: dict[str, Any],
                              report: dict[str, Any]) -> str:
    """Reuse manuscript/source checks; supplemental images retain exact shot use."""
    from .agent_acceptance import skill_for_artifact
    skill_for_artifact(entry)
    root = Path(project_root).resolve()
    if entry.get("logical_id") in {"narrative.package", "storyboard.package", "video.prompt"}:
        return validate_candidate_submission(root, entry, manifest)
    path = project_path(root, entry.get("path_or_url", ""))
    if (entry.get("type") != "visual-supplement-image" or not path.is_relative_to(root)
            or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
            or not path.is_file() or not path.stat().st_size
            or not entry.get("revision") or digest(path) != entry.get("checksum")):
        raise ValueError("自主验收补图须是项目内准确修订、hash 的真实图片")
    if "storyboard.package" not in (entry.get("depends_on") or []):
        raise ValueError("自主验收补图须绑定当前导演包依赖")
    state = load_project_state(root)
    validate_current_dependencies(entry, manifest, state, root)
    boards = [item for item in manifest.get("artifacts", [])
              if item.get("logical_id") == "storyboard.package" and item.get("status") == "current"]
    if len(boards) != 1:
        raise ValueError("自主验收补图须有唯一有效导演包")
    validate_candidate_submission(root, boards[0], manifest)
    board = read_json(project_path(root, boards[0]["path_or_url"]))
    shots = {shot["shot_id"] for scene in board.get("scenes", []) for shot in scene.get("shots", [])}
    usage = confirmation_scope.image_usage_shots(report.get("usage"), required=True)
    if not set(usage) <= shots:
        raise ValueError("自主验收补图用途镜号不属于准确当前导演包")
    from .quality_evidence import validate_image_evidence
    validate_image_evidence(root, path, entry.get("quality_evidence"), usage,
                            product_reference=entry.get("product_reference"), artifact=entry, state=state)
    return digest(path)


def adopt_initial_agent_artifact(project_root: Path, artifact_id: str, report_path: str,
                                 *, expected_checksum: str | None = None) -> dict[str, Any]:
    """Adopt authorized downstream work without minting a human confirmation."""
    from .candidate_test import require_production
    from .agent_acceptance import build_agent_acceptance, validate_agent_acceptance, skill_for_artifact
    from . import revision_control
    root = Path(project_root).resolve()
    with project_lock(root):
        require_production(root)
        manifest = load_manifest(root)
        entry = next((item for item in manifest.get("artifacts", [])
                      if item.get("artifact_id") == artifact_id), None)
        if entry is None or entry.get("status") != "candidate" or entry.get("user_approved") is not False:
            raise ValueError("自主首次采用只能从未获人审的准确 candidate 开始")
        skill_for_artifact(entry)
        if any(item.get("logical_id") == entry["logical_id"] and item.get("status") in {"current", "stale"}
               for item in manifest.get("artifacts", [])):
            raise ValueError("该逻辑产物已有 current/stale 版本；返修须使用 revision_control")
        path = project_path(root, entry.get("path_or_url", ""))
        if not path.is_file() or not path.is_relative_to(root):
            raise ValueError("自主采用候选必须是项目内真实文件")
        actual = digest(path)
        if entry.get("checksum") and entry["checksum"] != actual:
            raise ValueError("自主采用候选已偏离登记 hash")
        if expected_checksum is not None and expected_checksum != actual:
            raise ValueError("自主采用候选实际字节与单段预检来源的 sha256 不符")
        entry["checksum"] = actual
        state = load_project_state(root)
        entry["agent_acceptance"] = build_agent_acceptance(root, entry, report_path, state=state)
        report = validate_agent_acceptance(root, entry, state=state)
        if validate_agent_submission(root, entry, manifest, report) != actual or digest(path) != actual:
            raise ValueError("候选在自主采用检查期间发生变化")
        validate_agent_acceptance(root, entry, state=state)
        entry["status"] = "current"
        entry["user_approved"] = False
        state["current_artifacts"] = list(dict.fromkeys([*state.get("current_artifacts", []), artifact_id]))
        transaction = revision_control._commit_json_transaction_unlocked(root, {
            root / MANIFEST_PATH: manifest,
            root / "00_项目管理" / "project_state.json": state,
        }, reason="adopt initial agent-accepted artifact")
        adopted = next(item for item in load_manifest(root)["artifacts"] if item.get("artifact_id") == artifact_id)
        if not artifact_is_adopted(adopted, load_project_state(root), root):
            raise ValueError("自主采用事务已提交但验收回读未通过；不得交付或进入下游")
        return {"artifact": adopted, "agent_acceptance": entry["agent_acceptance"], "transaction": transaction}


def current_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in manifest.get("artifacts", []) if item.get("status") == "current"]


def unchanged_director_usage(project, baseline, replacement, shot_ids) -> bool:
    """Prove a supplement's ordered scene context against two exact director files."""
    if (not shot_ids or baseline.get('status') not in {'current', 'superseded'}
            or replacement.get('status') not in {'candidate', 'current'}
            or baseline.get('depends_on') != replacement.get('depends_on')):
        return False
    project = Path(project).resolve()
    prefixes = []
    package_contexts = []
    try:
        for entry in (baseline, replacement):
            if entry.get('logical_id') != 'storyboard.package' or entry.get('type') != 'storyboard.package':
                return False
            path = project_path(project, entry['path_or_url'])
            if (not path.is_relative_to(project) or path.suffix.lower() != '.json'
                    or not entry.get('checksum') or digest(path) != entry['checksum']):
                return False
            package = read_json(path)
            if package.get('schema_version') != '4.0' or package.get('revision') != entry.get('revision'):
                return False
            scope = {}
            scene_ids = set()
            scenes = package['scenes']
            for index, scene in enumerate(scenes):
                scene_id = scene['scene_id']
                if not scene_id or scene_id in scene_ids:
                    return False
                scene_ids.add(scene_id)
                for shot in scene['shots']:
                    shot_id = shot['shot_id']
                    if not shot_id or shot_id in scope:
                        return False
                    scope[shot_id] = index
            if not set(shot_ids) <= scope.keys():
                return False
            # Retain the scene's shared context and all preceding scenes.
            prefixes.append(sha256_json(scenes[:max(scope[shot] for shot in shot_ids) + 1]))
            context = {key: value for key, value in package.items() if key not in {
                'scenes', 'revision', 'status', 'human_readable_path', 'rebuild_provenance'}}
            timing = context.get('timing_basis')
            if isinstance(timing, dict) and timing.get('status') == 'not_evaluable':
                # The descriptive summary may change with a later scene's budget.
                # Timing status and exact source identity remain bound.
                context['timing_basis'] = {key: value for key, value in timing.items() if key != 'source_type'}
            package_contexts.append(sha256_json(context))
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return False
    return prefixes[0] == prefixes[1] and package_contexts[0] == package_contexts[1]


def supplement_director_shots(entry, report) -> set[str]:
    permitted = confirmation_scope.image_usage_shots(report.get('usage'), required=True)
    evidence = entry.get('quality_evidence') or {}
    observed = confirmation_scope.image_usage_shots(
        {'source_shot_ids': evidence.get('source_shot_ids')}, required=True)
    return set(permitted) | set(observed)


def unchanged_director_edges(project, manifest, state, baseline, replacement) -> set[tuple[str, str]]:
    """Cut only direct edges with already adopted, unchanged image usage."""
    if (baseline.get('logical_id') != 'storyboard.package'
            or baseline.get('status') != 'current'
            or not artifact_is_adopted(baseline, state, project)):
        return set()
    from .agent_acceptance import validate_agent_acceptance
    kept = set()
    current = current_artifacts(manifest)
    logical_ids = [item.get('logical_id') for item in current]
    for item in current:
        if (item.get('type') != 'visual-supplement-image' or not item.get('agent_acceptance')
                or logical_ids.count(item.get('logical_id')) != 1
                or 'storyboard.package' not in (item.get('depends_on') or [])):
            continue
        try:
            report = validate_agent_acceptance(project, item, state=state)
            validate_current_dependencies(item, manifest, state, project)
            if unchanged_director_usage(project, baseline, replacement, supplement_director_shots(item, report)):
                kept.add(('storyboard.package', item['logical_id']))
        except (KeyError, OSError, TypeError, ValueError):
            continue
    return kept


def asset_export_snapshot(project, entry, previous=None, *, file_digest=None):
    """Read a registered export once per locked operation, bound to its exact entry."""
    from .asset_refs import registered_output_snapshot

    identity = (project.resolve(), *(entry.get(key) for key in ('artifact_id', 'path_or_url', 'checksum')))
    if previous is not None and previous[0] == identity:
        return previous
    return identity, registered_output_snapshot(project, entry, file_digest=file_digest)


def asset_output_impact(project, manifest, baseline, replacement):
    """Find exact video segment impact against two verified asset output sets."""
    if baseline is None or replacement is None:
        return set(), {}
    from .revision_control import _generation_segments

    fields = ('path', 'sha256', 'purpose', 'view', 'state', 'output_kind')
    unchanged = {key for key, original in baseline.items() if key in replacement
                 and all(original.get(field) == replacement[key].get(field) for field in fields)}
    videos = [item for item in current_artifacts(manifest) if item.get('logical_id') == 'video.prompt']
    if not videos:
        videos = [item for item in manifest.get('artifacts', [])
                  if item.get('logical_id') == 'video.prompt' and item.get('status') == 'stale']
    if (len(videos) != 1 or videos[0].get('type') != 'video.prompt'
            or 'visual.asset.manifest' not in (videos[0].get('depends_on') or [])):
        return set(), {}
    item = videos[0]
    try:
        path = project_path(project, item['path_or_url'])
        if not path.is_relative_to(project.resolve()) or digest(path) != item.get('checksum'):
            return set(), {}
        segments = _generation_segments(read_json(path)) or []
        master_paths = {Path(selected['path']) for selected in baseline.values()}
        bound, affected = set(), []
        for row in segments:
            refs = row.get('materials', row.get('candidate_upload_order', []))
            if not isinstance(refs, list):
                return set(), {}
            row_keys = set()
            for ref in refs:
                if not isinstance(ref, dict):
                    return set(), {}
                if not (ref.get('asset_id') or ref.get('output_id')):
                    if ref.get('path') and project_path(project, ref['path']) in master_paths:
                        return set(), {}
                    continue
                key = (ref.get('asset_id'), ref.get('output_id'))
                selected = baseline.get(key)
                if (not selected or not ref.get('path') or project_path(project, ref['path']) != Path(selected['path'])
                        or ref.get('sha256') != selected['sha256']):
                    return set(), {}
                row_keys.add(key)
            bound.update(row_keys)
            if row_keys - unchanged:
                affected.append(row['segment_id'])
        if affected:
            return set(), {item['artifact_id']: affected}
        if bound:
            return {('visual.asset.manifest', item['logical_id'])}, {}
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        pass
    return set(), {}


def dependency_closure(manifest: dict[str, Any], roots: set[str], *, unchanged_edges=()) -> set[str]:
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
                edge = str(dependency), str(logical_id)
                if edge not in unchanged_edges:
                    dependents.setdefault(edge[0], set()).add(edge[1])
    impacted: set[str] = set()
    pending = set(roots)
    while pending:
        current = pending.pop()
        if current not in impacted:
            impacted.add(current)
            pending.update(dependents.get(current, set()))
    return impacted


def stamp_user_approval(entry: dict[str, Any], approval_ref: str) -> dict[str, Any]:
    """给非正式稿件条目盖用户批准戳：旗与凭据必须同写，缺凭据不许盖旗。"""
    if not str(approval_ref or "").strip():
        raise ValueError("user_approved 盖戳必须提供 approval_ref 凭据")
    entry["user_approved"] = True
    entry["confirmation_ref"] = approval_ref
    return entry


@project_locked
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
    *,
    unchanged_edges=(),
    asset_output_segments=None,
) -> InvalidationReport:
    """把直接或间接依赖变化项的 current 产物标为 stale。

    按 `depends_on` 反向遍历：从变化项出发，沿依赖边找到所有受影响的 logical_id，
    只修改清单状态，不移动或删除文件。
    """
    changed = set(changed_ids)
    if not changed:
        return InvalidationReport(set(), reason, revision)
    impacted = dependency_closure(manifest, changed, unchanged_edges=unchanged_edges)
    stale_ids: set[str] = set()
    now = utc_now()
    for item in manifest.get("artifacts", []):
        logical_id = item.get("logical_id")
        exact_asset_event = asset_output_segments is not None and logical_id == 'video.prompt'
        scope = (asset_output_segments or {}).get(item.get('artifact_id'))
        if scope and any(dep in impacted for dep in (item.get('depends_on') or []) if dep != 'visual.asset.manifest'):
            scope = None
        scope_fields = ({'asset_output_segment_ids': scope} if scope else {'asset_output_scope': 'unknown'}) if exact_asset_event else {}
        stale_reason = {"reason": reason, "revision": revision, "at": now, **scope_fields}
        if item.get("status") == "current" and logical_id in impacted:
            stale_ids.add(str(logical_id))
            item["status"] = "stale"
            item.setdefault("stale_reasons", []).append(stale_reason)
        elif exact_asset_event and item.get('status') == 'stale' and logical_id in impacted:
            reasons = item.setdefault('stale_reasons', [])
            prior = next((entry for entry in reversed(reasons)
                          if 'asset_output_segment_ids' in entry or 'asset_output_scope' in entry), {})
            if any(prior.get(key) != value for key, value in scope_fields.items()):
                reasons.append(stale_reason)
    return InvalidationReport(stale_ids, reason, revision)


@project_locked
def promote_candidate(project_root: Path, logical_id: str, artifact_id: str, *,
                      previous_asset_snapshot=None, file_digest=None) -> PromotionReport:
    """确认新候选产物：新版本设 current，同一 logical_id 的旧版本变 superseded。"""
    from .candidate_test import require_production
    require_production(project_root)
    manifest = load_manifest(project_root)
    new_item = next((item for item in manifest.get("artifacts", []) if item.get("artifact_id") == artifact_id), None)
    if new_item is None:
        raise ValueError(f"找不到产物：{artifact_id}")
    if new_item.get("logical_id") != logical_id:
        raise ValueError("候选的 logical_id 与替换目标不一致")
    if (new_item.get("agent_acceptance") or
            new_item.get("type") == "visual-supplement-image" and new_item.get("user_approved") is not True):
        raise ValueError("通用 promote_candidate 不能自主采用；请使用自主采用或变更提交入口")
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
    if any(item.get("agent_acceptance") for item in active):
        raise ValueError("通用 promote_candidate 不能替换已自主采用产物；请使用 revision_control")
    if len(active) > 1:
        raise ValueError("同一 logical_id 对应多个有效/待返工产物，先明确独立替换对象，不能整组覆盖")
    # Compute on the adopted graph before replacement; existing manual callers
    # may already have invalidated it, so stale rows must remain idempotent.
    if logical_id == 'visual.asset.manifest':
        delegated = new_item.get('user_approved') is False
        if delegated and not _asset_review_receipt_valid(new_item, project_root):
            raise ValueError('代审资产导出缺少有效的逐图采用记录')
        replacement = asset_export_snapshot(project_root, new_item, file_digest=file_digest)[1]
        if delegated and not replacement:
            raise ValueError('代审资产导出缺少有效的逐图采用记录')
        baseline = (asset_export_snapshot(project_root, active[0], previous_asset_snapshot, file_digest=file_digest)[1]
                    if active else None)
        unchanged_edges, segments = asset_output_impact(project_root, manifest, baseline, replacement)
        mark_impacted_artifacts_stale(manifest, {logical_id}, '采用了新的资产表',
                                     new_item.get('revision', ''), unchanged_edges=unchanged_edges,
                                     asset_output_segments=segments)
    elif active:
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


def cache_file_digests(read_digest=None, *, check_for_changes=False):
    """Reuse file digests within one operation, optionally rechecking file identity."""
    if read_digest is None:
        read_digest = digest
    hashes = {}

    def file_version(path):
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def file_digest(path):
        version = file_version(path) if check_for_changes else None
        cached = hashes.get(path)
        if cached is not None and cached[0] == version:
            return cached[1]
        checksum = read_digest(path)
        if not check_for_changes or version == file_version(path):
            hashes[path] = (version, checksum)
        return checksum

    return file_digest


def verify_image_confirmation(path, receipt, *, file_digest=None):
    """Verify new byte-bound receipts; do not invent hashes for older approvals."""
    if "image_sha256" not in receipt:
        return
    expected = receipt["image_sha256"]
    path = Path(path)
    file_digest = digest if file_digest is None else file_digest
    if (not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)
            or not path.is_file() or file_digest(path) != expected):
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


def _asset_review_receipt_valid(entry, root):
    if (entry.get('logical_id') != 'visual.asset.manifest' or entry.get('type') != 'visual-asset-manifest'
            or entry.get('user_approved') is not False or not entry.get('asset_review_sha256')
            or not entry.get('checksum')):
        return False
    try:
        path = project_path(root, entry['path_or_url'])
        receipt = project_path(root, entry['confirmation_ref'])
        if (not path.is_relative_to(Path(root).resolve()) or not receipt.is_relative_to(Path(root).resolve())
                or digest(path) != entry['checksum']):
            return False
        raw = receipt.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['asset_review_sha256']:
            return False
        record = json.loads(raw)
        return (record.get('kind') == 'delegated-asset-export' and record.get('revision') == entry['revision']
                and 'visual-assets-' + record['id'] == entry['artifact_id']
                and any(item['confirmation'].get('agent_review') for item in record['items']))
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return False


def artifact_is_adopted(entry, state, root=None):
    """Read human adoption or separately authorized, byte-bound agent acceptance."""
    if artifact_is_confirmed(entry, state, root):
        return True
    if (root is None or entry.get('status') != 'current' or entry.get('user_approved') is not False
            or entry.get('candidate_test') or entry.get('production_blocked') or state.get('candidate_test')):
        return False
    if entry.get('asset_review_sha256'):
        return _asset_review_receipt_valid(entry, root)
    if not entry.get('agent_acceptance'):
        return False
    from .agent_acceptance import validate_agent_acceptance
    try:
        validate_agent_acceptance(root, entry, state=state)
    except (ValueError, OSError, KeyError, TypeError):
        return False
    return True


def validate_current_dependencies(entry, manifest, state, root, *, checked=None, file_digest=None):
    """Read-only consumption check; never resurrect historical files as current.

    Logical edges establish availability, not version equality. Existing explicit
    source_narrative/source_storyboard refs establish exact revision/path identity.
    Reuse checks only within one operation against the same manifest and state.
    """
    visiting = set()
    checked = set() if checked is None else checked
    file_digest = digest if file_digest is None else file_digest
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
            if not artifact_is_adopted(source, state, root):
                raise ValueError('上游 current 缺少有效确认：' + logical_id)
            source_path = project_path(root, source.get('path_or_url', ''))
            if not source_path.is_file() or (source.get('checksum') and file_digest(source_path) != source['checksum']):
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
