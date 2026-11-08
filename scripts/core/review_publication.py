"""Bind a current website decision to its locally recorded publication."""
from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import InitVar, dataclass
from pathlib import Path

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
    from .story_sources import export_markdown

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
    with tempfile.TemporaryDirectory(prefix=".review-adopt-", dir=project) as folder:
        projection = Path(folder) / "reading.md"
        export_markdown(package, projection)
        text = projection.read_bytes()
    markdown, request = publication["markdown"], publication["request"]
    sha256 = hashlib.sha256(text).hexdigest()
    if (markdown["sha256"] != sha256 or request["text_sha256"] != sha256
            or markdown["bytes"] != len(text) or request["text_bytes"] != len(text)):
        raise ValueError("发布回执中的阅读稿与本版编导 JSON 不一致")

    decision = read_decision(client, publication)
    if not effective_approval(decision):
        return decision, None
    return decision, _VerifiedScriptApproval(
        project, artifact_id, str(source_path), source["revision"], source["sha256"], str(receipt_path), _VERIFIED)


def require_script_approval(project, entry, approval_ref, website_approval):
    """Enforce the same policy at every formal narrative write boundary."""
    if website_approval is None:
        if entry.get("logical_id") == "narrative.package" and load_kickoff(project):
            raise ValueError("统一入口的编导稿须经 review_text_cli.py decision 读取当前有效网站通过")
        return
    if (not isinstance(website_approval, _VerifiedScriptApproval)
            or entry.get("logical_id") != "narrative.package"
            or website_approval.project != project
            or website_approval.approval_ref != approval_ref
            or (entry.get("artifact_id"), str(project_file(project, entry.get("path_or_url", ""))),
                entry.get("revision"), entry.get("checksum"))
            != (website_approval.artifact_id, website_approval.path, website_approval.revision, website_approval.sha256)):
        raise ValueError("编导稿的项目、产物、路径、修订或文件字节与网站通过范围不一致")
