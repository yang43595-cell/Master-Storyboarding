"""Pure lower-L2 agent-acceptance, authorization, confirmation, and dependency rules."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import confirmation_scope
from .confirmation_scope import SCOPE_ID_FIELDS, image_usage_shots
from .io import canonical_json_bytes, project_lock, read_json, sha256_file, sha256_json, utc_now
from .manifest_store import digest, load_manifest, project_path
from .project_state import load_project_state, write_project_state
from .quality_evidence import validate_image_evidence
from .revision_store import formal_adoption as _store_formal_adoption, require_production

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = "review-to-canvas"
SKILLS = {"creative-director", "storyboard-planner", "storyboard-director", "voiceover-to-video-script"}
# Assets use a dedicated per-output review path and authorization.
ASSET_ADOPTION_SKILL = "asset-image-assistant"
AUTHORIZATION_SKILLS = SKILLS | {ASSET_ADOPTION_SKILL}
CORE_CHECKS = {
    "creative-director": {"CREATIVE-FACTS", "CREATIVE-STORY", "CREATIVE-MANIFEST"},
    "storyboard-planner": {"PLAN-PACKAGE", "PLAN-TRACE", "PLAN-CONTINUITY"},
    "storyboard-director": {"VISUAL-TARGET", "VISUAL-QC"},
    "voiceover-to-video-script": {"VIDEO-SOURCE", "VIDEO-CONTINUITY", "VIDEO-COMPLETION"},
}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _valid_skills(skills, allowed=SKILLS):
    return (isinstance(skills, list) and bool(skills)
            and all(isinstance(item, str) and item in allowed for item in skills)
            and len(skills) == len(set(skills)))


def _file(project, raw):
    if not _text(raw):
        raise ValueError("agent 验收缺少准确项目文件路径")
    path = project_path(project, raw)
    if not path.is_relative_to(Path(project).resolve()) or not path.is_file():
        raise ValueError("agent 验收文件缺失或越出项目：" + str(raw))
    return path


def skill_for_artifact(entry):
    logical, kind = entry.get("logical_id"), entry.get("type")
    if logical == kind == "narrative.package":
        return "creative-director"
    if logical == kind == "storyboard.package":
        return "storyboard-planner"
    if logical == kind == "video.prompt":
        return "voiceover-to-video-script"
    if kind == "visual-supplement-image" and _text(logical) and logical not in {
            "narrative.package", "storyboard.package", "video.prompt", "visual-asset-manifest"}:
        return "storyboard-director"
    raise ValueError("该产物不允许 agent 自主采用；资产及声音仍需原人工依据")


def _require_authorization(project, authorization_id, skill, allowed, state, invalid_scope):
    project = Path(project).resolve()
    require_production(project)
    state = load_project_state(project) if state is None else state
    if not isinstance(skill, str) or skill not in allowed or not _text(authorization_id):
        raise ValueError("资产采用缺有效授权 ID" if skill == ASSET_ADOPTION_SKILL else "自主验收缺有效授权 ID 或阶段")
    matches = [item for item in state.get("workflow_authorizations", [])
               if isinstance(item, dict) and item.get("id") == authorization_id]
    if len(matches) != 1:
        raise ValueError("流程授权不存在或不唯一")
    authorization = matches[0]
    skills = authorization.get("skills")
    reasons = []
    if authorization.get("workflow") != WORKFLOW:
        reasons.append(f"流程类型是 {authorization.get('workflow')}，应为 {WORKFLOW}")
    if authorization.get("status") != "active":
        reasons.append(f"状态是 {authorization.get('status')}（已撤回或未生效）")
    if authorization.get("project_path") != str(project):
        reasons.append(f"登记的项目路径 {authorization.get('project_path')} 与当前项目 {project} 不同")
    if not _text(authorization.get("authorization_ref")) or not _text(authorization.get("created_at")):
        reasons.append("缺授权依据或登记时间")
    if not _valid_skills(skills, allowed) or skill not in skills:
        reasons.append(f"授权阶段 {skills} 不含 {skill}")
    if reasons:
        raise ValueError(invalid_scope + "：" + "；".join(reasons))
    return authorization


def require_workflow_authorization(project, authorization_id, skill, *, state=None):
    return _require_authorization(project, authorization_id, skill, SKILLS, state,
                                  "流程授权已撤回、项目/阶段不符或依据不完整")


def require_asset_workflow_authorization(project, authorization_id, *, state=None):
    return _require_authorization(project, authorization_id, ASSET_ADOPTION_SKILL, AUTHORIZATION_SKILLS, state,
                                  "流程授权已撤回、项目不符或未覆盖资产采用范围")


def _register_authorization(project, authorization_id, authorization_ref, skills):
    """Record an already-given user authorization; this command grants none."""
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError("项目目录不存在")
    if not _text(authorization_id) or not _text(authorization_ref):
        raise ValueError("流程授权需要准确 ID 与用户依据")
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


def register_asset_workflow_authorization(project, *, authorization_id, authorization_ref):
    return _register_authorization(project, authorization_id, authorization_ref, [ASSET_ADOPTION_SKILL])


def register_workflow_authorization(project, *, authorization_id, authorization_ref, skills):
    if not _valid_skills(skills):
        raise ValueError("流程授权需要准确 ID、用户依据与允许的下游阶段")
    return _register_authorization(project, authorization_id, authorization_ref, skills)


def _binding(project, entry, *, file_digest=None):
    path = _file(project, entry.get("path_or_url"))
    if (not all(_text(entry.get(key)) for key in ("artifact_id", "logical_id", "type", "revision"))
            or not path.stat().st_size):
        raise ValueError("agent 验收产物身份、修订或文件为空")
    checksum = sha256_file(path) if file_digest is None else file_digest(path)
    # Registration may precede the first checksum; adoption pins it before
    # validating. Existing current/upstream records must already carry one.
    if (entry.get("checksum") and entry["checksum"] != checksum
            or entry.get("status") != "candidate" and entry.get("checksum") != checksum):
        raise ValueError("agent 验收产物已偏离登记 hash")
    return {key: entry[key] for key in ("artifact_id", "logical_id", "type", "revision")} | {
        "path": path.relative_to(project).as_posix(), "sha256": checksum}


def acceptance_binding(project, entry, *, file_digest=None, read_json_fn=read_json):
    """Return exact local bytes/direct upstreams for an actual self-check report."""
    project = Path(project).resolve()
    manifest = load_manifest(project, read_json_fn=read_json_fn)
    own = _binding(project, entry, file_digest=file_digest)
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
        upstreams.append(_binding(project, matches[0], file_digest=file_digest))
    result = {"artifact": own, "upstreams": upstreams}
    if entry.get("type") == "visual-supplement-image":
        # Actual observations and packaging references live on the existing
        # manifest entry; freeze their content without creating a second copy.
        result.update(quality_evidence_sha256=sha256_json(entry.get("quality_evidence")),
                      product_reference_sha256=sha256_json(entry.get("product_reference")))
    return result


def _unchanged_image_director(project, entry, report, expected, state, *, file_digest=None, read_json_fn=read_json):
    before = report.get("upstreams")
    if not isinstance(before, list) or len(before) != len(expected):
        return False
    different = [(old, new) for old, new in zip(before, expected) if old != new]
    if len(different) != 1:
        return False
    old, new = different[0]
    if (not isinstance(old, dict) or old.get("logical_id") != "storyboard.package"
            or new["logical_id"] != "storyboard.package"):
        return False
    records = {
        item["artifact_id"]: item
        for item in load_manifest(project, read_json_fn=read_json_fn)["artifacts"]
        if item.get("artifact_id")
    }
    baseline, current = records.get(old.get("artifact_id")), records.get(new["artifact_id"])
    effective_digest = digest if file_digest is None else file_digest
    return (
        baseline is not None
        and baseline.get("status") == "superseded"
        and current is not None
        and artifact_is_adopted(current, state, project, file_digest=effective_digest, read_json_fn=read_json_fn)
        and _binding(project, baseline, file_digest=file_digest) == old
        and _binding(project, current, file_digest=file_digest) == new
        and unchanged_director_usage(
            project, baseline, current, supplement_director_shots(entry, report),
            file_digest=effective_digest, read_json_fn=read_json_fn,
        )
    )


def _validate_report(project, entry, report, state, pointer=None, *, file_digest=None, read_json_fn=read_json, repo_root=None):
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
                    or pointer.get("authorization_sha256") != sha256_json(authorization)):
        raise ValueError("自主验收所依据的流程授权已改变")
    binding = acceptance_binding(project, entry, file_digest=file_digest, read_json_fn=read_json_fn)
    if (pointer and entry.get("status") == "current" and skill == "storyboard-director"
            and report.get("upstreams") != binding["upstreams"]
            and _unchanged_image_director(
                project, entry, report, binding["upstreams"], state,
                file_digest=file_digest, read_json_fn=read_json_fn,
            )):
        # The old receipt remains intact. Only its unchanged director scope is reusable.
        binding["upstreams"] = report["upstreams"]
    if any(report.get(key) != value for key, value in binding.items()):
        raise ValueError("自查未绑定本版产物/修订/hash或当前准确上游")
    if skill != "storyboard-director":
        payload = json.loads(_file(project, entry["path_or_url"]).read_text(encoding="utf-8-sig"))
        if (not isinstance(payload, dict) or payload.get("open_questions")
                or payload.get("status") in ("blocked", "rejected", "superseded")
                or skill == "storyboard-planner" and payload.get("status") not in ("candidate", "confirmed")):
            raise ValueError("自主验收源稿仍有阻塞、未决问题或已失效状态；不能以自报通过掩盖")
    profile_path = Path(ROOT if repo_root is None else repo_root) / "skills" / skill / "review-profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile_digest = sha256_file(profile_path) if file_digest is None else file_digest(profile_path)
    if report.get("review_profile_sha256") != profile_digest:
        raise ValueError("自查未绑定现行检查清单字节")
    expected = {item["id"] for item in profile["production_artifact_checks"]}
    checks = report.get("checks")
    if (not isinstance(checks, list)
            or any(not isinstance(item, dict) or not _text(item.get("id")) for item in checks)
            or len(checks) != len(expected) or {item.get("id") for item in checks} != expected):
        raise ValueError("自查未覆盖准确清单，缺项不能报整稿通过")
    required_pass = CORE_CHECKS[skill]
    if skill == "voiceover-to-video-script" and entry.get("status") == "candidate":
        required_pass = required_pass | {"PROMPT-LANG", "VIDEO-PROMPT-NOISE"}
    for item in checks:
        if (not isinstance(item.get("result"), str) or item["result"] not in {"pass", "not_applicable"}
                or not _text(item.get("observation")) or not _text(item.get("locator"))
                or (item["result"] == "not_applicable" and (
                    item["id"] in required_pass or not _text(item.get("reason"))))):
            raise ValueError("自查存在失败/未知、缺观察，或核心检查被写为不适用：" + str(item.get("id")))
    if skill == "storyboard-director":
        shots = image_usage_shots(report.get("usage"), required=True)
        if "storyboard.package" not in {item["logical_id"] for item in binding["upstreams"]}:
            raise ValueError("分镜图片验收必须绑定准确导演上游")
        validate_image_evidence(project, _file(project, entry["path_or_url"]),
                                entry.get("quality_evidence"), shots,
                                product_reference=entry.get("product_reference"), artifact=entry, state=state,
                                file_digest=file_digest)
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


def build_agent_acceptance(project, entry, report_path, *, state=None, file_digest=None, read_json_fn=read_json, repo_root=None):
    """Validate the actual self-check and return only its immutable pointer."""
    return read_agent_acceptance(
        project, entry, report_path, state=state, file_digest=file_digest, read_json_fn=read_json_fn, repo_root=repo_root
    )[0]


def read_agent_acceptance(project, entry, report_path, *, state=None, file_digest=None, read_json_fn=read_json, repo_root=None):
    """Return the acceptance pointer and the report validated from those exact bytes."""
    project = Path(project).resolve()
    state = load_project_state(project) if state is None else state
    path = _file(project, str(report_path))
    raw = path.read_bytes()
    report = json.loads(raw.decode("utf-8-sig"))
    authorization = _validate_report(
        project, entry, report, state, file_digest=file_digest, read_json_fn=read_json_fn, repo_root=repo_root
    )
    return {"report_path": path.relative_to(project).as_posix(), "report_sha256": hashlib.sha256(raw).hexdigest(),
            "authorization_id": authorization["id"], "authorization_sha256": sha256_json(authorization)}, report


def validate_agent_acceptance(project, entry, *, state=None, file_digest=None, read_json_fn=read_json, repo_root=None):
    """Recheck immutable evidence for consumption; never produces confirmation."""
    project = Path(project).resolve()
    state = load_project_state(project) if state is None else state
    pointer = entry.get("agent_acceptance")
    if not isinstance(pointer, dict):
        raise ValueError("产物缺少独立的 agent 验收依据")
    path = _file(project, pointer.get("report_path"))
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != pointer.get("report_sha256"):
        raise ValueError("agent 自查报告已改变，旧验收失效")
    report = json.loads(raw.decode("utf-8-sig"))
    _validate_report(
        project, entry, report, state, pointer, file_digest=file_digest, read_json_fn=read_json_fn, repo_root=repo_root
    )
    return report


def unchanged_director_usage(project, baseline, replacement, shot_ids, *, file_digest=digest, read_json_fn=read_json) -> bool:
    """Prove a supplement's ordered scene context against two exact director files."""
    if (not shot_ids or baseline.get("status") not in {"current", "superseded"}
            or replacement.get("status") not in {"candidate", "current"}
            or baseline.get("depends_on") != replacement.get("depends_on")):
        return False
    project = Path(project).resolve()
    prefixes = []
    package_contexts = []
    try:
        for entry in (baseline, replacement):
            if entry.get("logical_id") != "storyboard.package" or entry.get("type") != "storyboard.package":
                return False
            path = project_path(project, entry["path_or_url"])
            if (not path.is_relative_to(project) or path.suffix.lower() != ".json"
                    or not entry.get("checksum") or file_digest(path) != entry["checksum"]):
                return False
            package = read_json_fn(path)
            if package.get("schema_version") not in ("4.0", "5.0") or package.get("revision") != entry.get("revision"):
                return False
            scope = {}
            scene_ids = set()
            scenes = package["scenes"]
            for index, scene in enumerate(scenes):
                scene_id = scene["scene_id"]
                if not scene_id or scene_id in scene_ids:
                    return False
                scene_ids.add(scene_id)
                for shot in scene["shots"]:
                    shot_id = shot["shot_id"]
                    if not shot_id or shot_id in scope:
                        return False
                    scope[shot_id] = index
            if not set(shot_ids) <= scope.keys():
                return False
            prefixes.append(canonical_json_bytes(scenes[:max(scope[shot] for shot in shot_ids) + 1]))
            context = {key: value for key, value in package.items() if key not in {
                "scenes", "revision", "status", "human_readable_path", "rebuild_provenance"}}
            timing = context.get("timing_basis")
            if isinstance(timing, dict) and timing.get("status") == "not_evaluable":
                context["timing_basis"] = {key: value for key, value in timing.items() if key != "source_type"}
            package_contexts.append(canonical_json_bytes(context))
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return False
    return prefixes[0] == prefixes[1] and package_contexts[0] == package_contexts[1]


def supplement_director_shots(entry, report) -> set[str]:
    permitted = confirmation_scope.image_usage_shots(report.get("usage"), required=True)
    evidence = entry.get("quality_evidence") or {}
    observed = confirmation_scope.image_usage_shots(
        {"source_shot_ids": evidence.get("source_shot_ids")}, required=True)
    return set(permitted) | set(observed)


def formal_adoption(entry, root, *, file_digest=digest, read_json_fn=read_json):
    """Return the completed, hash-bound adoption evidence for a bundled artifact."""
    return _store_formal_adoption(entry, root, file_digest=file_digest, read_json_fn=read_json_fn)


def _whole_artifact_receipt(receipt):
    """整稿确认判定只有 core.confirmation_scope 一个出口；本函数只做转发。"""
    return confirmation_scope.is_whole_artifact(receipt)


def artifact_is_confirmed(entry, state, root=None, *, file_digest=digest, read_json_fn=read_json):
    if entry.get("candidate_test") or state.get("candidate_test"):
        return False
    if entry.get("status") != "current" or entry.get("user_approved") is not True:
        return False
    artifact_id = entry.get("artifact_id")
    for receipt in state.get("user_confirmations", []):
        if not _whole_artifact_receipt(receipt):
            continue
        if artifact_id in receipt.get("artifact_ids", []):
            return True
        if (receipt.get("kind") == "revision-candidate"
                and receipt.get("artifact_id") == artifact_id
                and receipt.get("sha256") == entry.get("checksum")
                and receipt.get("approval_ref")):
            return True
    return bool(root and formal_adoption(entry, root, file_digest=file_digest, read_json_fn=read_json_fn))


def _eligible_agent_adoption(entry, state, root):
    return (root is not None and entry.get("status") == "current" and entry.get("user_approved") is False
            and not (entry.get("candidate_test") or entry.get("production_blocked") or state.get("candidate_test")))


def require_current_agent_acceptance(entry, state, root, *, file_digest=None, read_json_fn=read_json):
    """Return the validated report for a currently adopted, agent-accepted artifact."""
    if (not _eligible_agent_adoption(entry, state, root) or entry.get("asset_review_sha256")
            or not entry.get("agent_acceptance")):
        raise ValueError("参考尚未确认、已返工失效或被新版替代")
    return validate_agent_acceptance(root, entry, state=state, file_digest=file_digest, read_json_fn=read_json_fn)


def artifact_is_adopted(entry, state, root=None, *, file_digest=digest, read_json_fn=read_json):
    """Read human adoption or separately authorized, byte-bound agent acceptance."""
    if artifact_is_confirmed(entry, state, root, file_digest=file_digest, read_json_fn=read_json_fn):
        return True
    if entry.get("logical_id") == "visual.asset.manifest":
        return False
    if not entry.get("agent_acceptance"):
        return False
    try:
        require_current_agent_acceptance(entry, state, root, file_digest=file_digest, read_json_fn=read_json_fn)
    except (ValueError, OSError, KeyError, TypeError):
        return False
    return True


def validate_current_dependencies(
    entry, manifest, state, root, *, checked=None, file_digest=None, read_payload=None, is_adopted_fn=None
):
    """Read-only consumption check; never resurrect historical files as current."""
    visiting = set()
    checked = set() if checked is None else checked
    file_digest = digest if file_digest is None else file_digest
    read_payload = read_json if read_payload is None else read_payload
    if is_adopted_fn is None:
        def is_adopted_fn(src, st, rt):
            return artifact_is_adopted(src, st, rt, file_digest=file_digest, read_json_fn=read_payload)

    def visit(item):
        ident = item.get("artifact_id")
        if ident in visiting:
            raise ValueError("产物依赖存在循环：" + str(ident))
        if ident in checked:
            return
        visiting.add(ident)
        deps = item.get("depends_on") or []
        if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
            raise ValueError("depends_on 必须是 logical_id 列表")
        path = project_path(root, item.get("path_or_url", ""))
        payload = {}
        if path.is_file() and path.suffix.lower() == ".json":
            try:
                value = read_payload(path)
                if isinstance(value, dict):
                    payload = value
            except (OSError, ValueError) as exc:
                raise ValueError("依赖来源 JSON 不可读：" + str(path)) from exc
        named = {"narrative.package": "source_narrative", "storyboard.package": "source_storyboard"}
        required = set(deps) | {lid for lid, field in named.items() if field in payload}
        for logical_id in sorted(required):
            matches = [a for a in manifest.get("artifacts", [])
                       if a.get("logical_id") == logical_id and a.get("status") == "current"]
            if len(matches) != 1:
                raise ValueError("上游必须有唯一 current，不能回退历史：" + logical_id)
            source = matches[0]
            if not is_adopted_fn(source, state, root):
                raise ValueError("上游 current 缺少有效确认：" + logical_id)
            source_path = project_path(root, source.get("path_or_url", ""))
            if not source_path.is_file() or (source.get("checksum") and file_digest(source_path) != source["checksum"]):
                raise ValueError("上游文件缺失或已偏离登记字节：" + logical_id)
            ref = payload.get(named.get(logical_id, ""))
            if ref is not None:
                if not isinstance(ref, dict) or ref.get("revision") != source.get("revision"):
                    raise ValueError("来源修订与 current 上游不一致：" + logical_id)
                if any(key in ref and ref[key] != source.get(key) for key in ("artifact_id", "logical_id")):
                    raise ValueError("来源身份与 current 上游不一致：" + logical_id)
                if ref.get("path") and project_path(root, ref["path"]) != source_path:
                    raise ValueError("来源路径与 current 上游不一致：" + logical_id)
            visit(source)
        visiting.remove(ident)
        checked.add(ident)

    visit(entry)
