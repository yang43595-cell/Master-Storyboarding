"""Bind a script approval to the exact local candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import InitVar, dataclass
from datetime import datetime
from pathlib import Path

from .errors import StageContextError
from .agent_review_api import DEFAULT_BASE_URL, effective_approval
from .project_kickoff import load_kickoff


def project_file(project: Path, value: str | Path) -> Path:
    candidate = Path(value)
    candidate = (candidate if candidate.is_absolute() else project / candidate).resolve()
    if not candidate.is_relative_to(project):
        raise ValueError("Review input and output paths must stay inside the project.")
    return candidate


def read_publication(project, path, client, stage):
    saved = json.loads(project_file(project, path).read_text(encoding="utf-8"))
    if (saved.get("kind") != "agent-review-api-receipt" or saved.get("project_path") != str(project)
            or saved.get("base_url") != client.base_url
            or saved.get("command") != ("publish" if stage == "script" else "publish-asset")):
        raise ValueError("Publication receipt does not belong to this project, origin and stage.")
    response, request = saved.get("response"), saved["request"]["parameters"]
    if (not isinstance(response, dict) or response.get("ok") is not True or response.get("stage") != stage
            or response.get("scope") != "live" or response.get("caseId") != request.get("caseId")
            or response.get("operationId") != request.get("operationId")):
        raise ValueError("Publication receipt lacks the exact successful remote binding.")
    if "preflight" in saved and response.get("projectId") != saved["preflight"]["response"]["case"]["projectId"]:
        raise ValueError("Publication receipt belongs to a different website project than the preflight check.")
    source = saved["source_json"] if stage == "script" else saved["image"]
    original = project_file(project, source["path"])
    if hashlib.sha256(original.read_bytes()).hexdigest() != source["sha256"]:
        raise ValueError("The source changed after publication.")
    if stage == "assets" and response.get("sha256") != source["sha256"]:
        raise ValueError("The published image hash does not match the local image.")
    if stage == "assets" and "readback" in saved:
        readback = saved["readback"]
        if not isinstance(readback, dict) or readback.get("status") not in ("current", "historical", "unknown"):
            raise ValueError("Asset publication readback is invalid or found a different project or a stale or wrong source script.")
    return saved


def read_decision(client, publication):
    response = publication["response"]
    decision = client.decision(case_id=response["caseId"], stage=response["stage"], version=response["version"])
    if decision["case"]["projectId"] != response["projectId"]:
        raise ValueError("Website decision belongs to a different remote project.")
    return decision


def is_recorded_website_script_approval(project, entry, approval_ref):
    """Match an adopted script to its saved, exact website decision without another request."""
    try:
        receipt = json.loads(project_file(project, approval_ref).read_text(encoding="utf-8"))
        if (receipt["kind"] != "agent-review-api-receipt" or receipt["command"] != "decision"
                or receipt["base_url"] != DEFAULT_BASE_URL or receipt["project_path"] != str(project)):
            return False
        source = receipt["source_json"]
        if (source["revision"] != entry["revision"] or source["sha256"] != entry["checksum"]
                or project_file(project, source["path"]) != project_file(project, entry["path_or_url"])):
            return False
        decision = receipt["response"]
        target = {"caseId": decision["case"]["id"], "stage": "script", "version": decision["version"]}
        return (decision["stage"] == "script" and receipt["request"]["parameters"] == target
                and effective_approval(decision) and decision["approval"]["scope"] == {
                    **target, "scope": "live", "projectId": decision["case"]["projectId"]})
    except (OSError, ValueError, KeyError, TypeError):
        return False


_VERIFIED = object()


@dataclass(frozen=True)
class _VerifiedScriptApproval:
    project: Path
    artifact_id: str
    path: str
    revision: str
    sha256: str
    approval_ref: str
    verified: InitVar[object]

    def __post_init__(self, verified):
        if verified is not _VERIFIED:
            raise TypeError("Website approval must come from a fresh review decision.")


def read_script_approval(project, publication, client, *, artifact_id, receipt_path):
    """Read through the production API, using the original publication as the local byte binding."""
    from .story_sources import narrative_review_projections, validate_story_sources

    if (client.base_url != DEFAULT_BASE_URL or publication.get("base_url") != DEFAULT_BASE_URL
            or publication.get("project_path") != str(project) or publication.get("command") != "publish"
            or publication["response"].get("stage") != "script"):
        raise ValueError("编导正式采用须从正式审核站读取通过决定")

    source = publication["source_json"]
    source_path = project_file(project, source["path"])
    raw = source_path.read_bytes()
    package = json.loads(raw.decode("utf-8-sig"))
    if (len(raw) != source["bytes"] or hashlib.sha256(raw).hexdigest() != source["sha256"]
            or package["project_id"] != source["project_id"] or package["revision"] != source["revision"]):
        raise ValueError("已送审编导稿的项目、修订或文件字节已变化")
    # The server version is its own content-model hash. Verify the local
    # publication's source and text bindings without replaying its write.
    subtitle_ids = source.get("awaiting_subtitle_approval_ids", [])
    if subtitle_ids:
        current = validate_story_sources(package, project_root=project, review_ready=True)
        if subtitle_ids != current["awaiting_subtitle_approval_ids"]:
            raise ValueError("硬字幕待审范围与发布回执不一致")
    markdown, request = publication["markdown"], publication["request"]
    projections = narrative_review_projections(package, subtitle_review_ids=subtitle_ids)
    for projection in projections:
        sha256 = hashlib.sha256(projection).hexdigest()
        if (markdown["sha256"] == sha256 == request["text_sha256"]
                and markdown["bytes"] == len(projection) == request["text_bytes"]):
            break
    else:
        raise ValueError("发布回执中的阅读稿与本版编导 JSON 不一致")

    decision = read_decision(client, publication)
    if not effective_approval(decision):
        return decision, None
    return decision, _VerifiedScriptApproval(
        project, artifact_id, str(source_path), source["revision"], source["sha256"], str(receipt_path), _VERIFIED)


@dataclass(frozen=True)
class ChatScriptApproval:
    project: Path
    artifact_id: str
    path: str
    revision: str
    sha256: str
    quote: str
    user_confirmed_at: str
    approval_ref: str
    review_markdown_path: str | None = None
    review_markdown_sha256: str | None = None
    subtitle_review_ids: tuple[str, ...] = ()

    @classmethod
    def from_message(cls, project, *, artifact_id, path, revision, sha256, message):
        if not isinstance(message, dict) or message.get("scope") != "artifact":
            raise ValueError("对话确认必须覆盖编导整稿")
        quote, at, reference = (message.get(key) for key in ("quote", "at", "review_ref"))
        if not all(isinstance(value, str) and value.strip() for value in (quote, at, reference)):
            raise ValueError("对话确认须提供用户原话、时间和对话消息位置")
        if datetime.fromisoformat(at).utcoffset() is None:
            raise ValueError("对话确认时间须含时区")
        source = project_file(project, path)
        proof = message.get('review_markdown')
        if proof is None:
            from .artifacts import load_manifest
            proof = next((row.get('review_markdown') for row in load_manifest(project)['artifacts']
                          if row['artifact_id'] == artifact_id and row.get('checksum') == sha256
                          and row.get('revision') == revision), None)
        if proof is None:
            return cls(project, artifact_id, str(source), revision, sha256, quote, at, reference)
        if (not isinstance(proof, dict) or not isinstance(proof.get('path'), str)
                or not proof['path'].strip() or not isinstance(proof.get('sha256'), str)):
            raise ValueError('对话审阅稿须提供审阅时记录的路径和 SHA-256')
        reading = project_file(project, proof['path'])
        raw = reading.read_bytes()
        source_raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != proof['sha256'] or hashlib.sha256(source_raw).hexdigest() != sha256:
            raise ValueError('编导稿或对话审阅稿已偏离审阅时的文件字节')
        from .story_sources import SUBTITLE_REVIEW_DISCLOSURE, narrative_review_projections
        actual = raw.decode('utf-8-sig').replace('\r\n', '\n')
        match = re.search(r'^' + re.escape(SUBTITLE_REVIEW_DISCLOSURE) + r'\n\n([^\n]+)\n\n', actual, re.M)
        ids = tuple(match[1].split('、')) if match else ()
        if not ids or len(set(ids)) != len(ids) or any(not re.fullmatch(r'DLG-[A-Z0-9-]+', ident) for ident in ids):
            raise ValueError('对话审阅稿缺硬字幕披露或准确对白 ID')
        package = json.loads(source_raw.decode('utf-8-sig'))
        annex = '## 完整结构化来源（只读技术附录）\n'
        # Some exports add the environment table after the canonical reading body.
        actual_body, _, actual_appendix = actual.rpartition(annex)
        env_heading = '## 环境资产需求\n\n'
        for projection in narrative_review_projections(package, subtitle_review_ids=ids):
            expected = projection.decode('utf-8').replace('\r\n', '\n')
            if actual == expected:
                break
            body, _, source_appendix = expected.rpartition(annex)
            if not actual_body.startswith(body) or actual_appendix != source_appendix:
                continue
            extra = actual_body.removeprefix(body).lstrip('\n')
            if extra.startswith(env_heading) and '\n## ' not in extra[len(env_heading):]:
                break
        else:
            raise ValueError('对话审阅稿与本版 JSON 的阅读正文或技术附录不一致')
        return cls(project, artifact_id, str(source), revision, sha256, quote, at, reference,
                   reading.relative_to(project).as_posix(), proof['sha256'], ids)

    def receipt_fields(self):
        receipt = {"channel": "chat_confirmation", "quote": self.quote,
                "user_confirmed_at": self.user_confirmed_at, "review_ref": self.approval_ref,
                "path": Path(self.path).relative_to(self.project).as_posix()}
        if self.subtitle_review_ids:
            receipt.update(review_markdown={"path": self.review_markdown_path, "sha256": self.review_markdown_sha256},
                           subtitle_review_ids=list(self.subtitle_review_ids))
        return receipt


def require_script_approval(project, entry, approval_ref, website_approval, *, chat_approval=None):
    """Enforce the same policy at every formal narrative write boundary."""
    if chat_approval is not None:
        if (website_approval is not None or not isinstance(chat_approval, ChatScriptApproval)
                or entry.get("logical_id") != "narrative.package"
                or chat_approval.project != project
                or chat_approval.approval_ref != approval_ref
                or (entry.get("artifact_id"), str(project_file(project, entry.get("path_or_url", ""))),
                    entry.get("revision"), entry.get("checksum"))
                != (chat_approval.artifact_id, chat_approval.path, chat_approval.revision, chat_approval.sha256)):
            raise ValueError("编导稿的项目、产物、路径、修订或文件字节与对话确认范围不一致")
        return
    if website_approval is None:
        if entry.get("logical_id") == "narrative.package" and load_kickoff(project):
            raise ValueError("统一入口的编导稿须使用当前有效的网站通过或准确的整稿对话确认")
        return
    if (not isinstance(website_approval, _VerifiedScriptApproval)
            or entry.get("logical_id") != "narrative.package"
            or website_approval.project != project
            or website_approval.approval_ref != approval_ref
            or (entry.get("artifact_id"), str(project_file(project, entry.get("path_or_url", ""))),
                entry.get("revision"), entry.get("checksum"))
            != (website_approval.artifact_id, website_approval.path, website_approval.revision, website_approval.sha256)):
        raise ValueError("编导稿的项目、产物、路径、修订或文件字节与网站通过范围不一致")


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
            sha256=args.sha256, message=message)
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


def _same_website_asset(confirmation, review, source):
    if not confirmation or not review:
        return False
    previous = confirmation.get('website_review') or {}
    approval = previous.get('approval') or {}
    scope = review['approval']['scope']
    return (confirmation.get('source') == source
            and confirmation.get('asset_id') == review['candidate_id']
            and previous.get('asset_id') == review['asset_id']
            and previous.get('candidate_revision') == review.get('candidate_revision')
            and confirmation.get('image_sha256') == review['image_sha256']
            and previous.get('purpose', review['purpose']) == review['purpose']
            and all((approval.get('scope') or {}).get(key) == scope[key]
                    for key in ('projectId', 'scope', 'caseId', 'stage'))
            and (previous.get('binding') == review['binding']
                 or (not previous.get('binding') and approval == review['approval'])
                 or review.get('_verified_confirmation_id') == confirmation['id']))

def _same_chat_asset(confirmation, basis, source):
    """Match a step receipt to the exact chat-confirmation basis that produced it."""
    if not confirmation or not basis:
        return False
    previous = confirmation.get('chat_confirmation') or {}
    previous_confirmation = previous.get('confirmation') or {}
    current = basis.get('confirmation') or {}
    return (confirmation.get('source') == source
            and previous.get('channel') == 'chat_confirmation'
            and confirmation.get('asset_id') == previous.get('asset_id') == basis.get('asset_id')
            and confirmation.get('image_sha256') == previous.get('image_sha256') == basis.get('image_sha256')
            and previous.get('purpose') == basis.get('purpose')
            and previous.get('candidate_revision') == basis.get('candidate_revision')
            and all(key not in previous or previous[key] == basis.get(key) for key in ('ast_id', 'output_id'))
            and previous.get('authorization_id') == basis.get('authorization_id')
            and previous.get('authorization_ref') == basis.get('authorization_ref')
            and previous_confirmation.get('quote') == current.get('quote')
            and previous_confirmation.get('at') == current.get('at')
            and previous_confirmation.get('review_ref') == current.get('review_ref')
            and previous_confirmation.get('message_id') == current.get('message_id'))

def _chat_confirmation_basis(confirmation):
    """A chat confirmation basis is the user's recorded words, never a website receipt."""
    if not isinstance(confirmation, dict):
        raise ValueError('对话确认依据必须是包含用户原话、时间与复核结论位置的记录')
    basis = {key: confirmation.get(key) for key in ('quote', 'at', 'review_ref')}
    if any(not isinstance(value, str) or not value.strip() for value in basis.values()):
        raise ValueError('对话确认依据须包含用户原话、确认时间与协调方复核结论位置')
    if 'message_id' in confirmation:
        if not isinstance(confirmation['message_id'], str) or not confirmation['message_id'].strip():
            raise ValueError('对话消息 ID 不能为空')
        basis['message_id'] = confirmation['message_id']
    return basis

def _verify_previous_website_member(previous, review, read_receipt):
    """Recover the exact source and member for receipts saved before binding was recorded."""
    scope = (previous.get('approval') or {}).get('scope') or {}
    if not previous.get('publication') or previous.get('asset_id') != review['asset_id']:
        return False
    original = read_receipt(previous['publication'])
    remote = original['response']
    binding = review['binding']
    script = read_receipt(original['script_publication'], 'script')
    if (original['script_version'] != binding['script_version']
            or script['response']['version'] != binding['script_version']
            or script['source_json']['sha256'] != binding['script_sha256']
            or any(script['response'][key] != scope.get(key) for key in ('projectId', 'scope', 'caseId'))
            or any(remote[key] != scope.get(key)
                   for key in ('projectId', 'scope', 'caseId', 'stage', 'version'))):
        return False
    if remote['assetId'] != review['asset_id']:
        refs = original['request']['parameters']['references']
        if review['asset_id'] not in refs or len(refs) != len(original['reference_publications']):
            return False
        original = read_receipt(original['reference_publications'][refs.index(review['asset_id'])])
        remote = original['response']
    parameters = original['request']['parameters']
    return (remote['assetId'] == review['asset_id']
            and original['script_version'] == binding['script_version']
            and all(remote[key] == scope.get(key) for key in ('projectId', 'scope', 'caseId', 'stage'))
            and parameters['astId'] == binding['ast_id']
            and parameters['outputId'] == binding['output_id']
            and parameters['purpose'] == review['purpose']
            and original['image']['sha256'] == review['image_sha256'])
