"""Isolated, explicitly authorized candidate consumption; never production approval.

State and manifest stay the existing authorities. This is not a sandbox for direct
browser/provider calls: those remain forbidden by the test task's scope.
"""
from pathlib import Path


def validate_stage(skill):
    """Accept only an actual local Skill name, never paths or invented stages."""
    import re
    root = Path(__file__).resolve().parents[2] / "skills"
    if (not isinstance(skill, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", skill)
            or not (root / skill / "SKILL.md").is_file()):
        raise ValueError("候选测试 Skill 不存在或名称无效：" + str(skill))


def result_metadata():
    return {"execution_mode": "candidate_test", "review_status": "unreviewed",
            "production_blocked": True}


def validate_context(value, state):
    if not isinstance(value, dict) or set(value) != {"run_id", "authorization_ref", "project_path", "input_artifact_ids"}:
        raise ValueError("candidate_test 必须有准确运行、授权、项目路径和输入 ID")
    if any(not isinstance(value[k], str) or not value[k].strip()
           for k in ("run_id", "authorization_ref", "project_path")):
        raise ValueError("candidate_test 运行、授权与项目路径不能为空")
    ids = value["input_artifact_ids"]
    if (not isinstance(ids, list) or any(not isinstance(i, str) or not i.strip() for i in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("candidate_test 输入 ID 无效或重复")
    if state.get("current_artifacts") or state.get("user_confirmations") or state.get("finalization"):
        raise ValueError("候选测试必须独立于已确认生产状态")


def context(project):
    from .project_state import load_project_state
    project = Path(project).resolve()
    state = load_project_state(project)
    if "candidate_test" not in state:
        return None
    value = state["candidate_test"]
    validate_context(value, state)
    if value["project_path"] != str(project):
        raise ValueError("候选测试状态属于另一项目，不能复制授权运行身份")
    if state.get("current_stage"):
        validate_stage(state["current_stage"])
    return value


def require_production(project):
    """Guard supported adoption/submission endpoints, including removed mode state."""
    from .artifacts import load_manifest
    if context(project) or any(a.get("candidate_test") or a.get("production_blocked")
                               or a.get("type") == "preview-reference"
                               for a in load_manifest(Path(project))["artifacts"]):
        raise ValueError("候选测试禁止正式采用、生成提交或发布；仍为 unreviewed")


def validate_preview_registration(project, item, run_id):
    """Read-only bookkeeping check, NOT permission to consume or upload media.

    Exact evidence binds an external display file, not an external business-input
    root. Canvas submission still needs its own explicit scoped authorization.
    """
    import json
    from .artifacts import project_path, digest
    path = project_path(project, item.get("path_or_url", ""))
    origin = item.get("preview_origin", {})
    if (item.get("candidate_test") != run_id or item.get("review_status") != "unreviewed"
            or item.get("production_blocked") is not True
            or not item.get("logical_id", "").startswith("preview.reference.")
            or item.get("depends_on")
            or path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp4', '.mov', '.webm'}
            or not path.is_file() or not path.stat().st_size
            or not item.get("checksum") or digest(path) != item["checksum"]
            or origin.get("purpose") != "private_preview_only_not_generation_baseline"
            or origin.get("source_sha256") != item["checksum"]
            or not isinstance(origin.get("history_gaps"), list)):
        raise ValueError("preview-reference 登记身份、用途或真实字节无效")
    evidence = origin.get("historical_evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("preview-reference 缺少精确来源范围")
    def binds(value):
        if isinstance(value, dict):
            if (value.get('sha256') == item['checksum']
                    and any(value.get(k) == str(path) for k in ('path', 'resolved_path'))):
                return True
            return any(binds(v) for v in value.values())
        return isinstance(value, list) and any(binds(v) for v in value)
    bound = False
    for record in evidence:
        location = record.get('location', '') if isinstance(record, dict) else ''
        p = Path(location).resolve()
        if (not location or str(p) != location or not p.is_file()
                or digest(p) != record.get('sha256')):
            raise ValueError("preview-reference 来源证据缺失或哈希不符")
        bound |= binds(json.loads(p.read_text(encoding='utf-8-sig')))
    if not bound:
        raise ValueError("preview-reference 来源未绑定准确文件路径与哈希")


def configure(project, run_id, authorization_ref, input_artifact_ids, *, current_stage=None):
    """One existing-state marker; caller must have explicit user test authorization."""
    from .project_state import load_project_state, write_project_state
    from .artifacts import load_manifest, save_manifest, project_path
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError("隔离测试项目不存在")
    state = load_project_state(project)
    value = dict(run_id=run_id, authorization_ref=authorization_ref,
                 project_path=str(project), input_artifact_ids=input_artifact_ids)
    validate_context(value, state)
    previous = context(project)
    if previous and any(previous[k] != value[k] for k in ("run_id", "authorization_ref", "project_path")):
        raise ValueError("不能替换既有测试运行身份或授权")
    if state.get("current_stage"):
        validate_stage(state["current_stage"])
    if current_stage is not None:
        validate_stage(current_stage)
    manifest = load_manifest(project)
    for item in manifest["artifacts"]:
        if item.get("status") != "candidate" or item.get("user_approved", False) is not False:
            raise ValueError("隔离候选测试不能包含生产或历史正式产物")
        if item.get("candidate_test") not in (None, run_id):
            raise ValueError("不能混入另一测试运行产物")
        if item.get("type") == "preview-reference":
            if not previous or item['artifact_id'] in input_artifact_ids:
                raise ValueError("preview-reference 仅供专用预览，不能成为普通业务输入")
            validate_preview_registration(project, item, run_id)
            continue
        path = project_path(project, item.get("path_or_url", ""))
        if not path.is_relative_to(project) or not path.is_file():
            raise ValueError("测试候选文件不存在或越出隔离项目")
    known = {a["artifact_id"] for a in manifest["artifacts"]}
    if not set(input_artifact_ids).issubset(known):
        raise ValueError("测试输入必须是准确已登记候选 ID")
    state["candidate_test"] = value
    if current_stage is not None:
        state["current_stage"] = current_stage
    # Stamp before writing state: failure can only block production, never grant it.
    for item in manifest["artifacts"]:
        item.update(candidate_test=run_id, review_status="unreviewed", production_blocked=True)
    save_manifest(project, manifest)
    write_project_state(project, state)
    return {"ok": True, **result_metadata(), "run_id": run_id, "input_artifact_ids": input_artifact_ids}


def resolve_inputs(project, ids=None):
    """Resolve exact candidate identities and dependencies, never latest filenames."""
    import json
    from .artifacts import load_manifest, project_path, digest
    project = Path(project).resolve()
    testing = context(project)
    if not testing:
        raise ValueError("候选测试未显式配置")
    entries = load_manifest(project)["artifacts"]
    indexed = {a.get("artifact_id"): a for a in entries}
    if len(indexed) != len(entries):
        raise ValueError("候选产物 ID 重复")
    records, visiting, checked = [], set(), set()
    def visit(ident):
        if ident in visiting:
            raise ValueError("候选依赖存在循环")
        if ident in checked:
            return
        item = indexed.get(ident)
        if item and item.get("type") == "preview-reference":
            raise ValueError("preview-reference 仅供专用预览，不能成为普通业务输入或依赖")
        if (not item or item.get("status") != "candidate" or item.get("user_approved", False) is not False
                or item.get("candidate_test") != testing["run_id"]
                or item.get("review_status") != "unreviewed" or item.get("production_blocked") is not True):
            raise ValueError("测试输入不是本轮未确认候选：" + str(ident))
        path = project_path(project, item["path_or_url"])
        if not path.is_relative_to(project):
            raise ValueError("候选文件越出隔离项目")
        if not path.is_file() or (item.get("checksum") and digest(path) != item["checksum"]):
            raise ValueError("候选文件缺失或偏离登记字节：" + str(ident))
        payload = json.loads(path.read_text(encoding="utf-8-sig")) if path.suffix.lower() == '.json' else {}
        if not isinstance(payload, dict):
            raise ValueError("候选 JSON 须为对象")
        if item.get("type") == "visual-supplement-request":
            from .schema_contracts import validate_visual_supplement_request
            report = validate_visual_supplement_request(payload, project_root=project, candidate_test=True)
            # Return diagnostics, never mutate manifest status or the business JSON.
            item = {**item, "business_status": report["business_status"],
                    "return_upstream": payload["return_upstream"]}
        elif "status" in payload and payload["status"] not in (
                {"candidate", "candidate_unreviewed"} if item.get("type") == "source.evidence"
                else {"candidate"}):
            raise ValueError("测试候选正文不可宣称确认：" + str(ident))
        if "revision" in payload and payload["revision"] != item.get("revision"):
            raise ValueError("候选正文 revision 与登记不一致")
        visiting.add(ident)
        # Evidence locators describe historical sources, not candidate package dependencies.
        # Only the manifest type selects this exception; explicit depends_on still resolves.
        named = ({} if item.get("type") == "source.evidence" else
                 {"narrative.package": "source_narrative", "storyboard.package": "source_storyboard"})
        deps = item.get("depends_on") or []
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            raise ValueError("候选 depends_on 必须是 logical_id 列表")
        for logical_id in sorted(set(deps) | {lid for lid, field in named.items() if field in payload}):
            ref = payload.get(named.get(logical_id, ''))
            matches = [a for a in entries if a.get('logical_id') == logical_id
                       and a.get('artifact_id') in testing['input_artifact_ids']]
            if ref is not None:
                if not isinstance(ref, dict) or not ref.get('revision') or ref.get('logical_id', logical_id) != logical_id:
                    raise ValueError("候选来源必须指定准确 revision")
                matches = [a for a in matches if a.get('revision') == ref['revision']
                           and (not ref.get('artifact_id') or ref['artifact_id'] == a['artifact_id'])
                           and (not ref.get('path') or project_path(project, ref['path']) == project_path(project, a['path_or_url']))]
            if len(matches) != 1:
                raise ValueError("候选来源不唯一或版本/路径不一致：" + logical_id)
            visit(matches[0]['artifact_id'])
        visiting.remove(ident)
        checked.add(ident)
        records.append(item)
    selected = testing['input_artifact_ids'] if ids is None else ids
    if not set(selected).issubset(testing['input_artifact_ids']):
        raise ValueError("来源未列入本轮显式候选输入")
    for ident in selected:
        visit(ident)
    return records
