"""Byte-bound agent self-checks under an explicit review-to-canvas authorization.

These records are distinct from human confirmation. Validation checks declared
evidence, not the truth of a message, actual inspection, or creative quality.
"""
from __future__ import annotations

import hashlib
import re
import json
from datetime import datetime
from pathlib import Path

from .errors import StageContextError
from .io import project_lock, sha256_file, sha256_json, utc_now

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
    from .artifacts import project_path
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
    from .candidate_test import require_production
    from .project_state import load_project_state
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
    if (authorization.get("workflow") != WORKFLOW or authorization.get("status") != "active"
            or authorization.get("project_path") != str(project)
            or not _text(authorization.get("authorization_ref"))
            or not _text(authorization.get("created_at"))
            or not _valid_skills(skills, allowed) or skill not in skills):
        raise ValueError(invalid_scope)
    return authorization


def require_workflow_authorization(project, authorization_id, skill, *, state=None):
    return _require_authorization(project, authorization_id, skill, SKILLS, state,
                                  "流程授权已撤回、项目/阶段不符或依据不完整")


def require_asset_workflow_authorization(project, authorization_id, *, state=None):
    return _require_authorization(project, authorization_id, ASSET_ADOPTION_SKILL, AUTHORIZATION_SKILLS, state,
                                  "流程授权已撤回、项目不符或未覆盖资产采用范围")


def _register_authorization(project, authorization_id, authorization_ref, skills):
    """Record an already-given user authorization; this command grants none."""
    from .candidate_test import require_production
    from .project_state import load_project_state, write_project_state
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


def acceptance_binding(project, entry, *, file_digest=None):
    """Return exact local bytes/direct upstreams for an actual self-check report."""
    from .artifacts import load_manifest
    project = Path(project).resolve()
    manifest = load_manifest(project)
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


def _unchanged_image_director(project, entry, report, expected, state):
    from .artifacts import (artifact_is_adopted, load_manifest, supplement_director_shots,
                            unchanged_director_usage)
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
    records = {item["artifact_id"]: item for item in load_manifest(project)["artifacts"] if item.get("artifact_id")}
    baseline, current = records.get(old.get("artifact_id")), records.get(new["artifact_id"])
    return (baseline is not None and baseline.get("status") == "superseded"
            and current is not None and artifact_is_adopted(current, state, project)
            and _binding(project, baseline) == old and _binding(project, current) == new
            and unchanged_director_usage(project, baseline, current, supplement_director_shots(entry, report)))


def _validate_report(project, entry, report, state, pointer=None, *, file_digest=None):
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
                    or pointer.get("authorization_sha256") != sha256_json(authorization)):
        raise ValueError("自主验收所依据的流程授权已改变")
    binding = acceptance_binding(project, entry, file_digest=file_digest)
    if (pointer and entry.get("status") == "current" and skill == "storyboard-director"
            and report.get("upstreams") != binding["upstreams"]
            and _unchanged_image_director(project, entry, report, binding["upstreams"], state)):
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
    profile_path = ROOT / "skills" / skill / "review-profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if report.get("review_profile_sha256") != sha256_file(profile_path):
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
        from .quality_evidence import validate_image_evidence
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


def build_agent_acceptance(project, entry, report_path, *, state=None):
    """Validate the actual self-check and return only its immutable pointer."""
    return read_agent_acceptance(project, entry, report_path, state=state)[0]


def read_agent_acceptance(project, entry, report_path, *, state=None):
    """Return the acceptance pointer and the report validated from those exact bytes."""
    from .project_state import load_project_state
    project = Path(project).resolve()
    state = load_project_state(project) if state is None else state
    path = _file(project, str(report_path))
    raw = path.read_bytes()
    report = json.loads(raw.decode("utf-8-sig"))
    authorization = _validate_report(project, entry, report, state)
    return {"report_path": path.relative_to(project).as_posix(), "report_sha256": hashlib.sha256(raw).hexdigest(),
            "authorization_id": authorization["id"], "authorization_sha256": sha256_json(authorization)}, report


def validate_agent_acceptance(project, entry, *, state=None, file_digest=None):
    """Recheck immutable evidence for consumption; never produces confirmation."""
    from .project_state import load_project_state
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
    _validate_report(project, entry, report, state, pointer, file_digest=file_digest)
    return report


def accept_artifact(args):
    """Adopt/select one exact artifact and optionally preflight its saved segment."""
    from . import artifacts as artifact_api, revision_control as revision_api
    from .artifacts import artifact_is_adopted, adopt_initial_agent_artifact, load_manifest, project_path
    from .candidate_test import require_production
    from .project_state import load_project_state
    from .io import read_stage_json
    project = Path(args.project).expanduser().resolve()
    report_path = project_path(project, args.self_check_json)
    change_id = getattr(args, 'change_id', None)
    frozen_path = getattr(args, 'preflight_json', None)
    with project_lock(project):
        require_production(project)
        manifest = load_manifest(project)
        matches = [item for item in manifest['artifacts'] if item['artifact_id'] == args.artifact_id]
        if len(matches) != 1:
            raise StageContextError('采用须指定唯一已登记产物 ID')
        entry = matches[0]
        frozen = None
        expected_checksum = None
        if frozen_path:
            frozen = read_stage_json(project_path(project, frozen_path), default=None)
            drafts = frozen.get('drafts') if isinstance(frozen, dict) else None
            sources = frozen.get('source_artifacts') if isinstance(frozen, dict) else None
            if not isinstance(drafts, list) or len(drafts) != 1 or not isinstance(sources, list):
                raise StageContextError('单段预检须有一个草稿及本次采用产物的准确来源')
            bound_sources = [ref for ref in sources if isinstance(ref, dict)
                             and ref.get('artifact_id') == entry['artifact_id']
                             and ref.get('revision') == entry.get('revision')]
            expected_checksum = entry.get('checksum')
            if (not expected_checksum and entry.get('status') == 'candidate'
                    and not change_id and len(bound_sources) == 1):
                # First adoption checks this against the actual bytes before pinning the hash.
                expected_checksum = bound_sources[0].get('sha256')
            if (not isinstance(expected_checksum, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_checksum)
                    or not any(ref.get('sha256') == expected_checksum for ref in bound_sources)):
                raise StageContextError('单段预检须有本次采用产物的准确 ID、revision、sha256 来源')
        if entry.get('status') == 'current':
            # Resuming is an explicit selection of this ID, never a blanket sync
            # of all manifest-current entries or a new approval.
            pointer = entry.get('agent_acceptance') or {}
            if entry.get('user_approved') is not False or project_path(project, pointer.get('report_path', '')) != report_path:
                raise StageContextError('已采用产物的自查报告与本次输入不同')
            if change_id:
                history = revision_api.show_change(project)['history']
                if not any(change.get('change_id') == change_id and change.get('status') == 'completed'
                           and (change.get('candidate_receipt') or {}).get('artifact_id') == entry['artifact_id']
                           for change in history):
                    raise StageContextError('续作变更案与该已采用产物不匹配')
            state = load_project_state(project)
            checked = validate_agent_acceptance(project, entry, state=state)
            artifact_api.validate_agent_submission(project, entry, manifest, checked)
            transaction = None
            if entry['artifact_id'] not in state['current_artifacts']:
                state['current_artifacts'].append(entry['artifact_id'])
                transaction = revision_api._commit_json_transaction_unlocked(project, {
                    project / '00_项目管理/project_state.json': state,
                }, reason='select exact previously agent-adopted artifact')
            adoption = {'resumed': True, 'transaction': transaction}
        elif change_id:
            change = revision_api.show_change(project)['active_change']
            if not change or change['change_id'] != change_id or (change.get('candidate_receipt') or {}).get('artifact_id') != entry['artifact_id']:
                raise StageContextError('修订采用须匹配原活动变更案及准确候选 ID')
            if change['status'] != 'commit_ready':
                revision_api.confirm_agent_candidate(project, change_id=change_id, report_path=str(report_path))
            else:
                acceptance = change.get('candidate_acceptance')
                stored_report = acceptance.get('report_path') if isinstance(acceptance, dict) else None
                if not isinstance(stored_report, str) or not stored_report.strip():
                    raise StageContextError('待提交修订缺少自主验收报告；人工确认请沿原入口提交')
                if project_path(project, stored_report) != report_path:
                    raise StageContextError('待提交修订的自查报告与本次输入不同')
            adoption = {'transaction': revision_api.commit_change(project, change_id=change_id)}
        else:
            adoption = adopt_initial_agent_artifact(
                project, args.artifact_id, report_path, expected_checksum=expected_checksum,
            )
        if 'artifact' not in adoption:
            selected = load_project_state(project)
            adopted = next(item for item in load_manifest(project)['artifacts'] if item['artifact_id'] == args.artifact_id)
            if adopted['artifact_id'] not in selected['current_artifacts'] or not artifact_is_adopted(adopted, selected, project):
                raise StageContextError('采用后 manifest/state 准确回读未通过，不得进入下游')
            adoption['artifact'] = adopted
        if frozen is not None:
            try:
                from ..canvas_readback import validate_submission
            except ImportError:
                from canvas_readback import validate_submission
            try:
                preflight = validate_submission(frozen, project, formal_delivery=True, preflight=True)
            except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
                return {'ok': False, 'adoption_completed': True, 'artifact_id': args.artifact_id,
                        'transaction': adoption.get('transaction'), 'preflight': {'ok': False, 'error': str(exc)},
                        'notice': '采用及状态登记已完成；修正原单段计划后续用同一命令，不重新采用或生成'}
            adoption['preflight'] = {key: preflight[key] for key in (
                'ok', 'mode', 'revision', 'frozen_sha256', 'source_segment_ids',
                'target_url', 'target_project_id', 'upload_mapping_pending_nodes', 'face_source_card_slots',
                'native_refresh_binding', 'generation_authorization', 'production_ready',
            ) if key in preflight}
    return {"ok": True, **adoption,
            "notice": "已按流程授权及准确自查采用；未新增逐稿人审，未上传、生成、发布或自动流转"}
