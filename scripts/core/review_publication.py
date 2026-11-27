"""Bind a script approval to the exact local candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .agent_review_api import DEFAULT_BASE_URL, effective_approval
from .errors import StageContextError
from .project_kickoff import load_kickoff
from .script_approval import (
    _VERIFIED,
    _VerifiedScriptApproval,
    ChatScriptApproval,
    is_recorded_website_script_approval,
    project_file,
    read_decision,
    read_publication,
    read_script_approval,
    require_script_approval,
)


def adopt_package(args, *, website_approval=None, chat_approval=None):
    """Register and adopt an initial platform-neutral manuscript package."""
    from .artifacts import adopt_initial_package, load_manifest, project_path, register_workspace_artifact
    from .candidate_test import require_production
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise StageContextError(f"项目目录不存在：{project}")
    require_production(project)
    logical_id = args.logical_id
    if logical_id not in {"narrative.package", "storyboard.package", "video.prompt"}:
        raise StageContextError("仅支持正式叙事包、导演包或视频提示词包")
    if logical_id != "narrative.package" and load_kickoff(project):
        raise StageContextError("统一入口的导演和视频提示词走自主采用 accept-artifact，无需逐稿人工确认")
    if not args.approval_ref.strip():
        raise StageContextError("正式稿采用必须绑定准确用户确认依据 --approval-ref")
    source = project_path(project, args.path)
    if not source.is_file() or not source.is_relative_to(project) or source.suffix.lower() != ".json":
        raise StageContextError("正式稿件路径必须是项目内 JSON 文件；渠道导出不是核心稿件")
    try:
        raw = source.read_bytes()
        package = json.loads(raw.decode("utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageContextError(f"正式稿件 JSON 不可读：{exc}") from exc
    if not isinstance(package, dict) or not isinstance(package.get("revision"), str):
        raise StageContextError("正式稿件必须含准确 revision")
    if package.get("revision") != args.revision:
        raise StageContextError("--revision 与正式稿件正文 revision 不一致")
    if logical_id == "video.prompt" and "target_url" in package and "drafts" in package:
        raise StageContextError("此命令只采用平台无关视频提示词；渠道冻结快照沿用渠道自己的检查")
    if logical_id == "narrative.package" and (
            package.get("schema_version") != "2.0" or "story_paragraphs" not in package):
        raise StageContextError("首次正式采用只接收当前 narrative.package 2.0；旧事件稿沿历史路径处理")
    if logical_id == "storyboard.package" and package.get("schema_version") not in ("4.0", "5.0"):
        raise StageContextError("首次正式采用只接收当前 storyboard.package 5.0（4.0 兼容）；旧版本保持兼容读取")
    if logical_id == "video.prompt" and not isinstance(package.get("source_storyboard"), dict):
        raise StageContextError("首次正式采用视频提示词必须绑定已确认 source_storyboard")
    relative = source.relative_to(project).as_posix()
    try:
        checksum = hashlib.sha256(raw).hexdigest()
        entry = {"artifact_id": args.artifact_id, "logical_id": logical_id, "type": logical_id,
                 "revision": args.revision, "path_or_url": relative, "checksum": checksum}
        require_script_approval(project, entry, args.approval_ref, website_approval, chat_approval=chat_approval)
        manifest = load_manifest(project)
        existing = next((item for item in manifest.get("artifacts", [])
                         if item.get("artifact_id") == args.artifact_id), None)
        if existing is None:
            register_workspace_artifact(project, {
                **entry, "depends_on": list(args.depends_on), "user_approved": False,
            })
        elif (existing.get("status") != "candidate" or existing.get("logical_id") != logical_id
              or existing.get("revision") != args.revision or existing.get("path_or_url") != relative
              or existing.get("depends_on", []) != list(args.depends_on)):
            raise StageContextError("同 ID 已登记为不同稿件；保留现状并使用新 artifact_id",
                                    code="CANDIDATE_ID_CONFLICT")
        elif (existing.get("checksum") != checksum
              and not ((website_approval is not None or chat_approval is not None)
                       and existing.get("checksum") is None)):
            raise StageContextError("候选稿件字节已偏离登记 hash；须另存版本并重新确认",
                                    code="CANDIDATE_HASH_MISMATCH")
        adoption = adopt_initial_package(project, args.artifact_id, args.approval_ref,
                                        scope_description=args.scope_description, website_approval=website_approval,
                                        chat_approval=chat_approval)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise StageContextError(f"稿件保持候选，正式采用未完成：{exc}",
                                code=exc.code if isinstance(exc, StageContextError) else None) from exc
    return {"ok": True, **adoption,
            "notice": "平台无关稿件已正式登记；未执行渠道上传、生成、发布或阶段自动流转"}


def adopt_chat_script(args, message):
    """Apply a genuine conversation decision through the formal adoption transaction."""
    project = Path(args.project).expanduser().resolve(strict=True)
    from .artifacts import artifact_is_adopted, digest, load_manifest
    from .candidate_test import require_production
    from .io import project_lock
    from .project_state import load_project_state
    from .revision_control import commit_change
    with project_lock(project):
        require_production(project)
        approval = ChatScriptApproval.from_message(
            project, artifact_id=args.artifact_id, path=args.path, revision=args.revision,
            sha256=args.sha256, message=message, load_manifest_fn=load_manifest)
        source = Path(approval.path)
        if not source.is_file() or digest(source) != approval.sha256:
            raise StageContextError("编导稿与对话审阅时登记的文件字节不一致")
        state = load_project_state(project)
        artifacts = load_manifest(project)["artifacts"]
        entry = next((row for row in artifacts if row.get("artifact_id") == args.artifact_id), None)
        if entry and entry.get("status") == "current":
            require_script_approval(project, entry, approval.approval_ref, None, chat_approval=approval)
            confirmation = next((receipt for receipt in state["user_confirmations"]
                                 if receipt.get("scope") == "artifact"
                                 and receipt.get("kind") == ("revision-candidate" if args.change_id else "package-adoption")
                                 and receipt.get("change_id") == args.change_id
                                 and (receipt.get("artifact_ids") == [args.artifact_id]
                                      or receipt.get("artifact_id") == args.artifact_id)
                                 and receipt.get("revision") == args.revision
                                 and receipt.get("sha256") == args.sha256
                                 and all(receipt.get(key) == value
                                         for key, value in approval.receipt_fields().items())), None)
            if confirmation and artifact_is_adopted(entry, state, project):
                return {"ok": True, "artifact": entry, "confirmation": confirmation, "already_current": True}
            raise StageContextError("此稿已正式采用，当前回执与本次对话消息或变更案不一致")
        if args.change_id:
            transaction = commit_change(project, change_id=args.change_id, chat_approval=approval)
            state = load_project_state(project)
            entry = next(row for row in load_manifest(project)["artifacts"]
                         if row["artifact_id"] == args.artifact_id)
            if not artifact_is_adopted(entry, state, project) or digest(source) != approval.sha256:
                raise StageContextError("采用回读未通过，须核对当前编导稿后再交接")
            return {"ok": True, "artifact": entry, "confirmation": state["user_confirmations"][-1],
                    "transaction": transaction}
        if any(row.get("logical_id") == "narrative.package" and row.get("status") in {"current", "stale"}
               for row in artifacts):
            raise StageContextError("编导稿已有正式版本，返修须使用已登记候选的活动变更案")
        return adopt_package(argparse.Namespace(
            project=project, logical_id="narrative.package", artifact_id=args.artifact_id,
            path=args.path, revision=args.revision, approval_ref=approval.approval_ref,
            depends_on=entry.get("depends_on", []) if entry else [],
            scope_description="对话中编导稿整版通过"), chat_approval=approval)
