"""Byte-bound agent self-checks under an explicit review-to-canvas authorization.

These records are distinct from human confirmation. Validation checks declared
evidence, not the truth of a message, actual inspection, or creative quality.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .io import project_lock, utc_now

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = "review-to-canvas"
SKILLS = {"storyboard-planner", "storyboard-director", "voiceover-to-video-script"}
CORE_CHECKS = {
    "storyboard-planner": {"PLAN-PACKAGE", "PLAN-TRACE", "PLAN-CONTINUITY"},
    "storyboard-director": {"VISUAL-TARGET", "VISUAL-QC"},
    "voiceover-to-video-script": {"VIDEO-SOURCE", "VIDEO-CONTINUITY", "VIDEO-COMPLETION"},
}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _valid_skills(skills):
    return (isinstance(skills, list) and bool(skills)
            and all(isinstance(item, str) and item in SKILLS for item in skills)
            and len(skills) == len(set(skills)))


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _file(project, raw):
    from .artifacts import project_path
    if not _text(raw):
        raise ValueError("agent 验收缺少准确项目文件路径")
    path = project_path(project, raw)
    if not path.is_relative_to(Path(project).resolve()) or not path.is_file():
        raise ValueError("agent 验收文件缺失或越出项目：" + str(raw))
    return path


def skill_for_artifact(entry):
    logical, kind = entry.get("logical_id"), entry.get("type")
    if logical == kind == "storyboard.package":
        return "storyboard-planner"
    if logical == kind == "video.prompt":
        return "voiceover-to-video-script"
    if kind == "visual-supplement-image" and _text(logical) and logical not in {
            "narrative.package", "storyboard.package", "video.prompt", "visual-asset-manifest"}:
        return "storyboard-director"
    raise ValueError("该产物不允许 agent 自主采用；编导、资产、白模及声音仍需原人工依据")


def require_workflow_authorization(project, authorization_id, skill, *, state=None):
    from .candidate_test import require_production
    from .project_state import load_project_state
    project = Path(project).resolve()
    require_production(project)
    state = load_project_state(project) if state is None else state
    if not isinstance(skill, str) or skill not in SKILLS or not _text(authorization_id):
        raise ValueError("自主验收缺有效授权 ID 或阶段")
    matches = [item for item in state.get("workflow_authorizations", [])
               if isinstance(item, dict) and item.get("id") == authorization_id]
    if len(matches) != 1:
        raise ValueError("流程授权不存在或不唯一")
    authorization = matches[0]
    skills = authorization.get("skills")
    if (authorization.get("workflow") != WORKFLOW or authorization.get("status") != "active"
            or authorization.get("project_path") != str(project)
            or not _text(authorization.get("authorization_ref"))
            or not _text(authorization.get("created_at"))
            or not _valid_skills(skills) or skill not in skills):
        raise ValueError("流程授权已撤回、项目/阶段不符或依据不完整")
    return authorization


def register_workflow_authorization(project, *, authorization_id, authorization_ref, skills):
    """Record an already-given user authorization; this command grants none."""
    from .candidate_test import require_production
    from .project_state import load_project_state, write_project_state
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError("项目目录不存在")
    if (not _text(authorization_id) or not _text(authorization_ref)
            or not _valid_skills(skills)):
        raise ValueError("流程授权需要准确 ID、用户依据与允许的下游阶段")
    with project_lock(project):
        require_production(project)
        state = load_project_state(project)
        if any(item.get("id") == authorization_id for item in state.get("workflow_authorizations", [])):
            raise ValueError("流程授权 ID 已存在；不得覆盖旧授权")
        record = {"id": authorization_id, "workflow": WORKFLOW,
                  "project_path": str(project), "authorization_ref": authorization_ref,
                  "skills": list(skills), "status": "active", "created_at": utc_now()}
        state.setdefault("workflow_authorizations", []).append(record)
        write_project_state(project, state)
        return record


def _binding(project, entry):
    path = _file(project, entry.get("path_or_url"))
    if (not all(_text(entry.get(key)) for key in ("artifact_id", "logical_id", "type", "revision"))
            or not path.stat().st_size):
        raise ValueError("agent 验收产物身份、修订或文件为空")
    checksum = _digest(path)
    # Registration may precede the first checksum; adoption pins it before
    # validating. Existing current/upstream records must already carry one.
    if (entry.get("checksum") and entry["checksum"] != checksum
            or entry.get("status") != "candidate" and entry.get("checksum") != checksum):
        raise ValueError("agent 验收产物已偏离登记 hash")
    return {key: entry[key] for key in ("artifact_id", "logical_id", "type", "revision")} | {
        "path": path.relative_to(project).as_posix(), "sha256": checksum}


def acceptance_binding(project, entry):
    """Return exact local bytes/direct upstreams for an actual self-check report."""
    from .artifacts import load_manifest
    project = Path(project).resolve()
    manifest = load_manifest(project)
    own = _binding(project, entry)
    dependencies = entry.get("depends_on") or []
    if not isinstance(dependencies, list) or any(not _text(item) for item in dependencies):
        raise ValueError("agent 验收依赖必须是明确 logical_id 列表")
    required = set(dependencies)
    path = _file(project, entry.get("path_or_url"))
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            required.update(logical for logical, field in (
                ("narrative.package", "source_narrative"), ("storyboard.package", "source_storyboard"))
                if field in payload)
    upstreams = []
    for logical in sorted(required):
        matches = [item for item in manifest["artifacts"]
                   if item.get("logical_id") == logical and item.get("status") == "current"]
        if len(matches) != 1 or not matches[0].get("checksum"):
            raise ValueError("验收上游缺唯一有效 current/hash：" + str(logical))
        upstreams.append(_binding(project, matches[0]))
    result = {"artifact": own, "upstreams": upstreams}
    if entry.get("type") == "visual-supplement-image":
        # Actual observations and packaging references live on the existing
        # manifest entry; freeze their content without creating a second copy.
        result.update(quality_evidence_sha256=_json_digest(entry.get("quality_evidence")),
                      product_reference_sha256=_json_digest(entry.get("product_reference")))
    return result


def _validate_report(project, entry, report, state, pointer=None):
    from .candidate_test import require_production
    from .confirmation_scope import SCOPE_ID_FIELDS, image_usage_shots
    skill = skill_for_artifact(entry)
    require_production(project)
    if entry.get("candidate_test") or entry.get("production_blocked") or entry.get("user_approved") is True:
        raise ValueError("agent 验收不得借测试候选、阻塞项或人工批准字段生效")
    if (not isinstance(report, dict) or report.get("schema_version") != "1.0"
            or report.get("kind") != "agent-self-check" or report.get("scope") != "artifact"
            or report.get("verdict") != "pass" or not _text(report.get("agent_id"))
            or not _text(report.get("checked_at"))
            or any(report.get(key) not in (None, [], {}) for key in SCOPE_ID_FIELDS)):
        raise ValueError("自主验收必须有准确执行者、时间和整稿通过依据，不能用局部或未知结论")
    authorization = require_workflow_authorization(project, report.get("authorization_id"), skill, state=state)
    if pointer and (pointer.get("authorization_id") != authorization["id"]
                    or pointer.get("authorization_sha256") != _json_digest(authorization)):
        raise ValueError("自主验收所依据的流程授权已改变")
    binding = acceptance_binding(project, entry)
    if any(report.get(key) != value for key, value in binding.items()):
        raise ValueError("自查未绑定本版产物/修订/hash或当前准确上游")
    if skill != "storyboard-director":
        payload = json.loads(_file(project, entry["path_or_url"]).read_text(encoding="utf-8-sig"))
        if (not isinstance(payload, dict) or payload.get("open_questions")
                or payload.get("status") in ("blocked", "rejected", "superseded")
                or skill == "storyboard-planner" and payload.get("status") not in ("candidate", "confirmed")):
            raise ValueError("自主验收源稿仍有阻塞、未决问题或已失效状态；不能以自报通过掩盖")
    profile_path = ROOT / "skills" / skill / "review-profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if report.get("review_profile_sha256") != _digest(profile_path):
        raise ValueError("自查未绑定现行检查清单字节")
    expected = {item["id"] for item in profile["production_artifact_checks"]}
    checks = report.get("checks")
    if (not isinstance(checks, list)
            or any(not isinstance(item, dict) or not _text(item.get("id")) for item in checks)
            or len(checks) != len(expected) or {item.get("id") for item in checks} != expected):
        raise ValueError("自查未覆盖准确清单，缺项不能报整稿通过")
    for item in checks:
        if (not isinstance(item.get("result"), str) or item["result"] not in {"pass", "not_applicable"}
                or not _text(item.get("observation")) or not _text(item.get("locator"))
                or (item["result"] == "not_applicable" and (
                    item["id"] in CORE_CHECKS[skill] or not _text(item.get("reason"))))):
            raise ValueError("自查存在失败/未知、缺观察，或核心检查被写为不适用：" + str(item.get("id")))
    if skill == "storyboard-director":
        from .quality_evidence import validate_image_evidence
        shots = image_usage_shots(report.get("usage"), required=True)
        if "storyboard.package" not in {item["logical_id"] for item in binding["upstreams"]}:
            raise ValueError("分镜图片验收必须绑定准确导演上游")
        validate_image_evidence(project, _file(project, entry["path_or_url"]),
                                entry.get("quality_evidence"), shots,
                                product_reference=entry.get("product_reference"))
    elif report.get("usage") is not None:
        raise ValueError("图片用途仅允许独立分镜图片登记")
    if "reference_groups" in report:
        known = {item["artifact_id"]: item for item in [binding["artifact"], *binding["upstreams"]]}
        groups = report["reference_groups"]
        if not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups):
            raise ValueError("同态组验收必须是明确组数组")
        for group in groups:
            if not _text(group.get("group_id")) or not isinstance(group.get("members"), list) or not group["members"]:
                raise ValueError("同态组缺 ID 或准确成员")
            for member in group["members"]:
                bound = (known.get(member["artifact_id"]) if isinstance(member, dict)
                         and _text(member.get("artifact_id")) else None)
                if not bound or any(member.get(key) != bound[key] for key in ("revision", "sha256")):
                    raise ValueError("同态组成员未纳入本版验收或字节不匹配")
    return authorization


def build_agent_acceptance(project, entry, report_path, *, state=None):
    """Validate the actual self-check and return only its immutable pointer."""
    from .project_state import load_project_state
    project = Path(project).resolve()
    state = load_project_state(project) if state is None else state
    path = _file(project, str(report_path))
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    authorization = _validate_report(project, entry, report, state)
    return {"report_path": path.relative_to(project).as_posix(), "report_sha256": _digest(path),
            "authorization_id": authorization["id"], "authorization_sha256": _json_digest(authorization)}


def validate_agent_acceptance(project, entry, *, state=None):
    """Recheck immutable evidence for consumption; never produces confirmation."""
    from .project_state import load_project_state
    project = Path(project).resolve()
    state = load_project_state(project) if state is None else state
    pointer = entry.get("agent_acceptance")
    if not isinstance(pointer, dict):
        raise ValueError("产物缺少独立的 agent 验收依据")
    path = _file(project, pointer.get("report_path"))
    if _digest(path) != pointer.get("report_sha256"):
        raise ValueError("agent 自查报告已改变，旧验收失效")
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    _validate_report(project, entry, report, state, pointer)
    return report
