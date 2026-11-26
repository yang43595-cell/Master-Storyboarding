"""Resolve project status, batch routing and stage handoff cards."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from . import stage_context as context


def _selected_inputs(project, state):
    if context.test_context(project):
        return state, context.test_inputs(project)
    return context._confirmed_project_inputs(project, state)


def _legacy_revision_blockers(active_change, blockers):
    if isinstance(active_change, dict):
        pending = f"活动变更案未完成：{active_change.get('change_id', 'unknown')} ({active_change.get('status', 'unknown')})"
        return [pending, *blockers]
    return blockers


def _confirmed_handoff_inputs(project: Path, target_skill: str, *, state=None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    skill_path = context.ROOT / "skills" / target_skill / "SKILL.md"
    if not skill_path.is_file():
        raise context.StageContextError(f"目标 Skill 不存在：{target_skill}")
    state = context._state(project) if state is None else state
    expected = str(state.get("next_stage") or "")
    if not expected:
        raise context.StageContextError("project_state.next_stage 为空；下一阶段未知，不能生成正式交接")
    if expected != target_skill:
        raise context.StageContextError(f"目标 Skill 与项目下一阶段不一致：state={expected}, requested={target_skill}")
    active_change, _, blockers = context.inspect_revision_status(project)
    if blockers := _legacy_revision_blockers(active_change, blockers):
        raise context.StageContextError("正式交接被修订状态阻止：" + "；".join(blockers))
    return context._confirmed_project_inputs(project, state)


def _first_director_handoff(project, state):
    receipts = state["user_confirmations"]
    manifest = context.load_manifest(project)
    if not receipts and not any(a.get("logical_id") == "narrative.package"
            and a.get("status") == "current" and a.get("agent_acceptance")
            and a.get("artifact_id") in state["current_artifacts"] for a in manifest["artifacts"]):
        raise context.StageContextError("project_state.next_stage 为空；没有编导整稿的正式采用记录")
    active_change, _, blockers = context.inspect_revision_status(project)
    if blockers := _legacy_revision_blockers(active_change, blockers):
        raise context.StageContextError("正式交接被修订状态阻止：" + "；".join(blockers))
    _, inputs = context._confirmed_project_inputs(project, state, manifest=manifest)
    for entry in inputs:
        if entry.get("logical_id") != "narrative.package":
            continue
        exact_receipts = [r for r in receipts if context.exact_narrative_approval(r, entry, entry["checksum"])]
        if any(context.is_recorded_website_script_approval(project, entry, r["approval_ref"]) for r in exact_receipts):
            return inputs
        if not exact_receipts and not entry.get("agent_acceptance"):
            continue
        for authorization in state["workflow_authorizations"]:
            if not isinstance(authorization, dict):
                continue
            try:
                context.require_workflow_authorization(project, authorization.get("id"), "storyboard-planner", state=state)
            except ValueError:
                continue
            return inputs
    raise context.StageContextError("project_state.next_stage 为空；缺少当前准确编导整稿的采用依据或已有导演流程授权")


def _uses_routed_handoff(project):
    request = context.load_project_request(project)
    return "kickoff" in request and context.LEGACY_RULE not in request.get("local_references", [])


def status(args: argparse.Namespace) -> dict[str, Any]:
    """Read project/repository facts without recovering or synchronizing state."""
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise context.StageContextError(f"项目目录不存在：{project}")
    manage = project / "00_项目管理"
    state_path = manage / "project_state.json"
    manifest_path = manage / "artifact_manifest.json"
    state = None
    manifest = None
    blockers: list[str] = []
    if not state_path.is_file():
        blockers.append("project_state.json 缺失；当前阶段及下一阶段未知")
    else:
        try:
            state = context._state(project)
        except (context.StageContextError, ValueError) as exc:
            blockers.append(f"project_state.json 无效；阶段状态未知：{exc}")
    candidate_mode = False
    candidate_context = None
    if state is not None and state.get("candidate_test") is not None:
        try:
            candidate_context = context.test_context(project)
            candidate_mode = candidate_context is not None
        except (context.StageContextError, ValueError) as exc:
            blockers.append(f"candidate_test 状态无效；隔离流程未知：{exc}")
    if not manifest_path.is_file():
        blockers.append("artifact_manifest.json 缺失；产物版本/失效状态未知")
    else:
        try:
            manifest = context.load_manifest(project)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            manifest = None
            blockers.append(f"artifact_manifest.json 不可读；产物版本状态未知：{exc}")
        if not isinstance(manifest, dict) or not isinstance(manifest.get("artifacts"), list):
            blockers.append("artifact_manifest.json 结构无效；产物版本/失效状态未知")
            manifest = None
    artifacts = []
    artifact_blockers = []
    if manifest is not None:
        for item in manifest["artifacts"]:
            if not isinstance(item, dict):
                blockers.append("manifest 含非对象产物记录")
                continue
            try:
                path = context.project_path(project, str(item.get("path_or_url") or ""))
                exists = path.is_file()
            except (OSError, ValueError) as exc:
                path = None
                exists = None
                artifact_blockers.append(f"产物路径无效，版本状态未知：{item.get('artifact_id', 'unknown')}: {exc}")
            checksum_ok = None
            if exists is True and item.get("checksum"):
                checksum_ok = context.digest(path) == item["checksum"]
            if item.get("status") == "current" and (exists is not True or checksum_ok is False):
                artifact_blockers.append(f"current 产物缺失或哈希漂移：{item.get('artifact_id', 'unknown')}")
            artifacts.append({"artifact_id": item.get("artifact_id"), "logical_id": item.get("logical_id"),
                "revision": item.get("revision"), "status": item.get("status"),
                "path": str(path) if path else None, "exists": exists, "checksum_ok": checksum_ok})
    revision_status = context.inspect_revision_status(project)
    active_change, transaction_rows, revision_blockers = revision_status
    if candidate_mode and active_change is None:
        active_change = "not_applicable_candidate_test"
    repo = {"root": str(context.ROOT), "branch": None, "revision": None,
            "baseline_branch": "main", "baseline_revision": None, "working_tree_clean": None}
    try:
        branch = subprocess.run(["git", "-C", str(context.ROOT), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
        revision = subprocess.run(["git", "-C", str(context.ROOT), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        baseline = subprocess.run(["git", "-C", str(context.ROOT), "merge-base", "HEAD", "main"], check=True, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(context.ROOT), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
        repo.update(branch=branch or None, revision=revision or None, baseline_revision=baseline or None,
                    working_tree_clean=not bool(dirty.strip()))
    except (OSError, subprocess.CalledProcessError):
        blockers.append("代码仓库分支/当前 revision 无法读取；未知")

    facts = {"read_only": True, "project": str(project), "repo": repo,
             "current_stage": state.get("current_stage") if state is not None else None,
             "current_artifacts": state.get("current_artifacts") if state is not None else None,
             "artifacts": artifacts, "stale_artifacts": [a["artifact_id"] for a in artifacts if a["status"] == "stale"],
             "active_change": active_change, "transactions": transaction_rows}
    try:
        routed = not candidate_mode and _uses_routed_handoff(project)
    except ValueError as exc:
        blockers.append(str(exc))
        routed = False
    if routed:
        flow = (context.resolve_production_handoff(project, manifest=manifest, state=state, revision_status=revision_status)
                if manifest is not None and state is not None else {"steps": []})
        if not flow["steps"]:
            blockers.extend(revision_blockers)
        next_actions = [step for step in flow["steps"] if step["status"] == "ready"] if not blockers else []
        waiting_for_user = (not blockers and not next_actions
                            and any(step["status"] == "waiting" for step in flow["steps"]))
        if not next_actions:
            blockers.extend(step["reason"] for step in flow["steps"] if step["status"] == "waiting")
        next_skills = {step["skill"] for step in next_actions if "skill" in step}
        production_ok = bool(next_actions)
        return {**facts, **flow, "mode": "production", "next_actions": next_actions,
                "next_allowed_stage": next(iter(next_skills)) if len(next_skills) == 1 else None,
                "next_stage_note": "当前可执行范围见 next_actions；等待项见 steps。",
                "production_allowed": production_ok, "production_ok": production_ok,
                "waiting_for_user": waiting_for_user,
                "blockers": blockers, "ok": production_ok}

    blockers.extend(artifact_blockers)
    blockers.extend(_legacy_revision_blockers(active_change, revision_blockers))
    next_stage = state.get("next_stage") if isinstance(state, dict) else None
    if not isinstance(next_stage, str) or not next_stage.strip():
        blockers.append("next_stage 为空；下一允许阶段未知")
        next_stage = None
    elif not (context.ROOT / "skills" / next_stage / "SKILL.md").is_file():
        blockers.append(f"next_stage 指向不存在的 Skill：{next_stage}")
        next_stage = None
    elif not state.get("current_stage"):
        blockers.append("current_stage 为空；不能生成正式交接")
        next_stage = None
    elif manifest is not None and state is not None:
        try:
            context._confirmed_project_inputs(project, state)
        except (context.StageContextError, ValueError) as exc:
            blockers.append(f"上游确认/版本/依赖未通过：{exc}")
            next_stage = None
    else:
        next_stage = None
    if blockers:
        next_stage = None
    production_blockers = list(blockers)
    production_ok = not blockers and next_stage is not None
    candidate_test_blockers: list[str] = []
    if candidate_mode:
        # Candidate handoff deliberately accepts an explicit --to-skill when
        # next_stage is empty. Its isolated inputs, not production approvals or
        # revision-change records, are the authority for this mode.
        candidate_test_blockers.extend(message for message in blockers
                                       if not message.startswith("next_stage 为空；下一允许阶段未知"))
        if not state or not isinstance(state.get("current_stage"), str) or not state["current_stage"].strip():
            candidate_test_blockers.append("候选测试 current_stage 缺失；不能生成隔离交接")
        try:
            context.test_inputs(project)
        except (context.StageContextError, ValueError, OSError) as exc:
            candidate_test_blockers.append(f"隔离候选输入无效：{exc}")
        if candidate_context is None:
            candidate_test_blockers.append("候选测试运行身份未知")
        candidate_test_ok = not candidate_test_blockers
        return {**facts, "mode": "candidate_test",
            "next_allowed_stage": next_stage,
            "next_stage_note": ("候选测试隔离有效；next_stage 为空时可显式选择 --to-skill，候选流程不放行正式生产。"
                                if candidate_test_ok and next_stage is None else
                                "候选测试仅限隔离输入；正式生产仍被阻止。"),
            "production_allowed": False, "production_ok": production_ok,
            "production_blockers": production_blockers,
            "candidate_test_allowed": candidate_test_ok,
            "candidate_test": {"run_id": candidate_context.get("run_id") if candidate_context else None,
                               "input_artifact_ids": candidate_context.get("input_artifact_ids") if candidate_context else None},
            "blockers": candidate_test_blockers, "ok": candidate_test_ok}
    return {**facts, "mode": "production",
        "next_allowed_stage": next_stage,
        "next_stage_note": "上游版本确认后仍需该 Skill 自身输入门禁；状态命令不会启动阶段。" if next_stage else "阻塞或状态未知，不猜下一阶段。",
        "production_allowed": production_ok, "production_ok": production_ok,
        "blockers": blockers, "ok": production_ok}


def _receive_asset_batch(project, args, target_skill, from_skill):
    context_id = context._CONTEXT_ID.get()
    if (not context_id or target_skill != "asset-image-assistant" or from_skill
            or args.candidate_test or args.request_json or args.batch_receipt):
        raise context.StageContextError("接收实看回执须指定资产 Skill 和 --context-id，路径沿用本批交接卡")
    cards = (context._handoff_path(project), context._context_dir(project, "stage_startup.md"))
    bindings = set().union(*(context._asset_batch_binding_entries(card.read_text(encoding="utf-8"))
                             for card in cards if card.is_file()))
    if len(bindings) != 1:
        raise context.StageContextError("本批交接卡缺少图片批次依据，或卡片之间的批次依据冲突")
    try:
        batch = json.loads(bindings.pop())
        if batch["context_id"] != context_id:
            raise ValueError("批次身份不一致")
        request = context.project_path(project, batch["request"])
        if not request.is_relative_to(project):
            raise ValueError("原请求路径越出项目")
        request_payload, request_sha256 = context._request_snapshot(request)
        if request_sha256 != batch["request_sha256"]:
            raise ValueError("原请求内容已改变")
        targets = context._image_batch_targets(target_skill, request_payload)
        if targets != batch["targets"]:
            raise ValueError("本批约定目标与准确请求不一致")
        receipt = context.project_path(project, batch["receipt"])
        result = context.summarize_asset_batch_receipt(project, receipt, targets,
                                              binding={"context_id": context_id, "request_sha256": request_sha256})
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
        raise context.StageContextError(f"无法按本批交接卡核对实看回执：{exc}") from exc
    return {"kind": "asset-batch-result", "context_id": context_id, **result}


def _route_existing_project(args, project):
    """Recalculate the next dispatch from committed business evidence on every run."""
    if context._test_request(args, project):
        raise context.StageContextError("隔离候选交接须指定 --to-skill")
    with context.file_lock(project / context.PROJECT_LOCK_RELATIVE):
        state = context._state(project)
        manifest = context.load_manifest(project)
        flow = context.resolve_production_handoff(project, state=state, manifest=manifest)
        next_actions = []
        file_digest = context.cache_file_digests(context.digest)
        checked_dependencies = set()
        for step in flow["steps"]:
            if step["status"] != "ready":
                continue
            next_action = dict(step)
            target_skill = step.get("skill")
            if target_skill:
                context_id = "handoff-" + step["action"]
                token = context._CONTEXT_ID.set(context_id)
                try:
                    ids = step.get("artifact_ids", [])
                    inputs = []
                    if ids:
                        _, inputs = context._confirmed_project_inputs(
                            project, state, manifest=manifest, artifact_ids=ids,
                            file_digest=file_digest, checked_dependencies=checked_dependencies,
                        )
                    card = _write_handoff_card(
                        args, project, target_skill,
                        step.get("from_skill") or state.get("current_stage") or "kickoff",
                        inputs, routed=step,
                    )
                    next_action.update(context_id=context_id, handoff_path=card["path"])
                except (OSError, ValueError) as exc:
                    step.update(status="waiting", reason=str(exc))
                    continue
                finally:
                    context._CONTEXT_ID.reset(token)
            next_actions.append(next_action)
        return {**flow, "ok": True, "mode": "production", "project": str(project), "next_actions": next_actions}


@context._context_scoped
def handoff(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise context.StageContextError(f"项目目录不存在：{project}")
    target_skill = str(getattr(args, "to_skill", None) or "").strip()
    from_skill = str(getattr(args, "from_skill", None) or "").strip()
    authorization_id = str(getattr(args, "authorization_id", None) or "").strip()
    if not target_skill:
        if from_skill or authorization_id or any(getattr(args, option, None) for option in (
                "receive_batch", "request_json", "batch_receipt", "actor_id", "task_id", "context_id", "objective", "stop_at")):
            raise context.StageContextError("程序交接只接收项目路径；已有明确目标的任务或批次绑定须提供 --to-skill")
        return _route_existing_project(args, project)
    if bool(from_skill) != bool(authorization_id):
        raise context.StageContextError("协调方须同时提供 --from-skill 与已有的 --authorization-id")
    if from_skill and getattr(args, "candidate_test", False):
        raise context.StageContextError("隔离候选交接不得登记生产阶段")
    if getattr(args, "receive_batch", False):
        return _receive_asset_batch(project, args, target_skill, from_skill)
    asset_batch = context._asset_batch_handoff(project, args, target_skill)
    default_flow = (target_skill in context._ROUTED_SKILLS and not getattr(args, "candidate_test", False)
                    and asset_batch is None and _uses_routed_handoff(project))
    if default_flow:
        with context.file_lock(project / context.PROJECT_LOCK_RELATIVE):
            try:
                return _bind_routed_handoff(args, project, target_skill, authorization_id)
            except context.StageContextError:
                raise
            except ValueError as exc:
                raise context.StageContextError(str(exc)) from exc
    # Declared transitions share the project writer lock. Inspect revision facts
    # before any write or recovery can change them.
    can_register_transition = from_skill or (
        target_skill == "storyboard-planner" and not getattr(args, "candidate_test", False)
    )
    with context.file_lock(project / context.PROJECT_LOCK_RELATIVE) if can_register_transition else nullcontext():
        return _write_handoff(args, project, target_skill, from_skill, authorization_id, asset_batch=asset_batch)


def _bind_routed_handoff(args, project, target_skill, authorization_id):
    """Bind a runner to a legal route; an explicit Skill never authorizes a transition."""
    context._test_request(args, project)
    state = context._state(project)
    if authorization_id:
        context.require_workflow_authorization(project, authorization_id, target_skill, state=state)
    actions = context._ROUTED_SKILLS[target_skill]
    if len(actions) == 1:
        step = context.require_production_handoff(project, actions[0], state=state)
    else:
        action = (context._CONTEXT_ID.get() or "").removeprefix("handoff-")
        requested = {action} if action in actions else set(actions)
        steps = context.resolve_production_handoff(project, state=state, actions=requested)["steps"]
        ready = [step for step in steps if step["action"] in requested and step["status"] == "ready"]
        if not ready:
            reason = "；".join(step["reason"] for step in steps if step["status"] == "waiting")
            raise context.StageContextError("视频或画布交接当前未就绪：" + reason)
        if len(ready) > 1:
            raise context.StageContextError("视频写稿和画布写入均有就绪范围；沿程序交接返回的对应 context_id 绑定")
        step = ready[0]
    from_skill = getattr(args, "from_skill", None)
    if from_skill and step.get("from_skill") != from_skill:
        raise context.StageContextError("交接来源不符合程序确定的阶段")
    ids = step.get("artifact_ids", [])
    inputs = context._confirmed_project_inputs(project, state, artifact_ids=ids)[1] if ids else []
    return _write_handoff_card(args, project, target_skill,
                               step.get("from_skill") or state.get("current_stage") or "kickoff", inputs, routed=step)


def _write_handoff(args, project, target_skill, from_skill, authorization_id, *, asset_batch=None):
    testing = context._test_request(args, project)
    register_transition = False
    if testing:
        context.validate_test_stage(target_skill)
        state, inputs = _selected_inputs(project, context._state(project))
        if state.get("next_stage") and state["next_stage"] != target_skill:
            raise context.StageContextError("目标 Skill 与项目下一阶段不一致")
    elif from_skill:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", from_skill) or not (context.ROOT / "skills" / from_skill / "SKILL.md").is_file():
            raise context.StageContextError(f"交接来源 Skill 不存在：{from_skill}")
        state = context._state(project)
        context.require_workflow_authorization(project, authorization_id, target_skill, state=state)
        if state.get("project_status") != "active":
            raise context.StageContextError("项目不处于可登记正式交接的活动状态")
        current, expected = state["current_stage"], state["next_stage"]
        if expected != from_skill and (current not in ("", from_skill) or expected not in (None, "", target_skill)):
            raise context.StageContextError("协调方提供的流转与已登记阶段冲突，请先核对当前阶段及已允许的下一步")
        active_change, _, blockers = context.inspect_revision_status(project)
        if blockers := _legacy_revision_blockers(active_change, blockers):
            raise context.StageContextError("正式交接被修订状态阻止：" + "；".join(blockers))
        state, inputs = context._confirmed_project_inputs(project, state)
        register_transition = (current, expected) != (from_skill, target_skill)
    else:
        state = context._state(project)
        if (target_skill == "storyboard-planner" and state["project_status"] == "active"
                and state["current_stage"] in ("", "creative-director") and state["next_stage"] in (None, "")):
            inputs = _first_director_handoff(project, state)
            from_skill = "creative-director"
            register_transition = True
        else:
            state, inputs = _confirmed_handoff_inputs(project, target_skill, state=state)
    current_stage = from_skill or str(state.get("current_stage") or "")
    if not current_stage:
        raise context.StageContextError("项目当前阶段为空，不能生成交接卡")
    report = _write_handoff_card(args, project, target_skill, current_stage, inputs,
                                 testing=testing, asset_batch=asset_batch)
    if register_transition:
        state.update(current_stage=from_skill, next_stage=target_skill)
        context.write_project_state(project, state)
    return report


def _write_handoff_card(args, project, target_skill, current_stage, inputs, *, testing=False,
                        asset_batch=None, routed=None):
    director_paths = []
    if target_skill == "storyboard-planner" and routed and routed["action"] == "director":
        from .director_inputs import dispatch, sources
        entry = next(item for item in inputs if item["logical_id"] == "narrative.package")
        if routed.get("segment_ids"):
            director_paths = dispatch(project, entry, routed["segment_ids"])
        else:
            path = context._context_dir(project, "导演编导来源.json")
            context.write_json(path, sources(project, entry))
            director_paths = [str(path.relative_to(project))]
    local_recreation_prompt = (target_skill == "voiceover-to-video-script" and routed
                               and routed.get("action") == "video"
                               and routed.get("workflow") == context.RECREATION_WORKFLOW)
    scope_fields = (*context._ROUTED_HANDOFF_SCOPE, "workflow")
    lines = [
        "# 下一阶段交接卡", "",
        *[f"- 导演已绑定输入：`{path}`" for path in director_paths],
        *(["- 导演写稿：用 `scripts/director_package.py` 渐进式读取并逐场落盘（outline → init → scene-source → put-scene → status → register → self-check-template），"
           "禁止一次吞入全片来源或一次输出整份导演包，见 `skills/storyboard-planner/SKILL.md#渐进式读取与逐场落盘必须用禁止一次读全片原文或输出整包`。"]
          if target_skill == "storyboard-planner" and routed and routed.get("action") == "director"
          and not routed.get("segment_ids") else []),
        f"- 来源阶段：`{current_stage}`",
        f"- 目标 Skill：`{target_skill}`",
        *([f"- 制作路线：复刻线。实读 `{context.ROOT / routed['workflow_entry']}`，沿本阶段现有交接和采用入口。",
           f"- 原片产品：按 `{context.ROOT / context.RECREATION_METHOD}#原片产品核对与制作分流` 核商品归属，"
           "沿开工记录使用本轮决定。自家产品尚未决定是否延续时，确认后再安排相关产品镜段。"]
          if routed and routed.get("workflow") == context.RECREATION_WORKFLOW else []),
        f"- 本轮目标：{getattr(args, 'objective', None) or (routed or {}).get('reason') or context._HANDOFF_DEFAULT_OBJECTIVE}",
        f"- 本轮停止点：{getattr(args, 'stop_at', None) or '完成本职自查并交回回执，遇缺来源、授权或预算停止相关动作'}",
        ("- execution_mode: candidate_test；review_status: unreviewed；production_blocked: true。只试写，不生成、不发布、不采用。" if testing else "- 交接状态：上游当前输入的有效采用依据与依赖已核对。"),
        *(["- 程序交接范围：" + json.dumps({key: routed[key] for key in scope_fields if key in routed},
                                              ensure_ascii=False, separators=(",", ":"))] if routed else []), "",
        ("## 本轮未确认候选输入" if testing else "## 当前有效上游产物"), "",
        "- 语义自查：本卡未读取结论。沿用当前修订有效的检查结论，核对未决项及本阶段新增用途。独立审核仅在用户明确要求时执行。", "",
    ]
    relative_paths = {}
    for item in inputs:
        source = context.project_path(project, item['path_or_url'])
        path_line = f"  - path：`{source.as_posix()}`"
        if source.is_relative_to(project):
            relative_paths[path_line] = f"  - path：`{source.relative_to(project).as_posix()}`"
        lines.extend([
            f"- artifact_id：`{item['artifact_id']}`",
            f"  - logical_id：`{item.get('logical_id', '')}`",
            f"  - revision：`{item.get('revision', '')}`",
            f"  - type：`{item.get('type', '')}`",
            *([f"  - business_status：`{item['business_status']}`；return_upstream：{json.dumps(item['return_upstream'], ensure_ascii=False)}"]
              if testing and "business_status" in item else []),
            path_line,
            f"  - sha256：`{context.digest(source) if testing else item['checksum']}`",
        ])
    startup_requirement = (
        "- `voiceover-to-video-script` 直接按其 Skill 的原始资料读取例外建立本段输入范围；不生成或校验 stage_startup.md。"
        if target_skill == "voiceover-to-video-script" else
        "- 按根规则为已授权的下一 Skill 建立执行者。统一入口在网站通过、编导整稿对话确认正式采用或有效自主采用后，按本轮范围继续；兼容人工流程沿其继续规则。创作新轮由专属生产 agent 准备并校验 stage_startup.md。"
    )
    kickoff = None if testing else context.load_kickoff(project)
    task_context = context._CONTEXT_ID.get()
    if target_skill == "creative-director" and not testing:
        python = context.ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        command = [str(python), str(context.ROOT / "scripts/stage_context_cli.py"), "prepare",
                   "--project", str(project), "--skill", target_skill]
        if task_context:
            command.extend(["--context-id", task_context])
        lines.append("- 编导开工命令参数：" + json.dumps(command, ensure_ascii=False) +
                     "。补齐本轮 objective、request-json、actor-id 和 task-id 后执行。")
    if local_recreation_prompt:
        if routed.get("pending_asset_bindings"):
            lines.append("- 待补资产退回资产阶段。本地提示词按计划用途先写，挂图并核对后交画布。")
        lines.append("- 本段写词输入：运行 `python scripts/stage_context_cli.py recreation-prompt-input --project <业务项目> "
                     "--segment-id <本次实际负责的段号>`。同批多段重复传参数。沿回包读取原文、对白、接缝和参考，旧词仅按本轮段号取用。")
    lines.extend([
        "", "## 本轮依据与停止点", "",
        f"- 项目路径：`{project}`。相对产物路径从这里解析。仓库入口：`{context.ROOT / 'AGENTS.md'}` 与目标 Skill。",
        ("- 原始证据：按本段回包核原片窗和实际参考；画布转换的深度沿复刻 Skill 核输入窗及引用。缺口只回查对应帧窗或对白依据。整份编导稿路径供版本定位。"
         if local_recreation_prompt else "- 原始证据：从以上准确产物的 source_artifacts / 来源引用按需实读，不用交接摘要替代。"),
        f"- 决定及授权：实读 `{project / context.REQUEST_PATH}` 的本轮需求、kickoff 和 `{project / '00_项目管理/project_state.json'}` 的确认及 workflow_authorizations；缺文件或范围保留未决。",
        "- 未决项：核以上当前产物的未决字段、现有 review_report 与 accepted_issues，已接受问题只覆盖原记录的准确范围。",
        "- 停止点：完成本职目标和自查后交回准确版本、检查结果、未完成项与证据路径。",
        *([f"- 视频默认交已保存并回读的草稿。若本轮包含已明确授权的生成，按 `{context.ROOT / 'skills/voiceover-to-video-script/references/seedance-delivery-package.md'}#已授权生成的取证` 接续，复用有效授权。"]
          if not testing and target_skill == "voiceover-to-video-script" else []),
        *(["- 草稿曾写入或写入结果不明时，先沿原计划和准确画布只读核对原节点；找到原草稿就接续其回读，确认该段未保存才新建。"]
          if routed and routed["action"] == "canvas" else []),
        f"- 工具报错：沿 `{context.ROOT / 'docs/rules/execution.md'}#工具报错与原处续作`，在现有任务记录交回原操作、失败位置、最后可信结果和待恢复步骤。",
        f"- 等待及接收：执行前实读 `{context.ROOT / 'docs/rules/execution.md'}#等待与工具回传` 的宿主参数；沿原任务续等，完成后只交本轮身份、结论、差异及证据路径。原图由执行者实看，协调方按争议定向取图，不整份回传 JSON、日志或图组。",
        "", "## 接收约束", "",
        *([f"- 从 `{project / context.REQUEST_PATH}` 直接读取本轮 `kickoff`；已有决定随项目沿用，缺项只影响依赖它的动作。"] if kickoff else []),
        ("- 仅按独立测试授权继续候选试写；保留所有未核对事实和素材阻塞。视频 Skill 仍直接读取，不重建准备包。" if testing else startup_requirement),
        *([f"- 准备、读取登记、产前检查和需要的审核包沿本任务 `--context-id {task_context}`；使用原执行者和任务 ID。"]
          if task_context and target_skill in context.TASK_HANDOFF_SKILLS else []),
        ("- 只按段号取编导源 JSON 的必要内容，复用上下文中已读的同版范围。编导 Markdown 留作人审。"
         if local_recreation_prompt else "- 编导交接须实际读准确正文、核来源和本轮自查结论；关键发现及未核范围复用现有任务记录，程序 ok 不代表叙事充分。"),
        "- 创作新轮使用干净的专属执行者，同轮返修可续用。核对仓库、规则和执行环境。用户明确要求时才单独审核。",
        "- 图片沿有效确认输出消费。同用途复用已有观察，版本或用途变化时定向实看。资产人审和下游补图自主采用各用原入口。",
        ("- 只消费以上未确认候选及准确稳定 ID；不得读取旧案例答案，不得写入确认记录。" if testing else "- 只消费以上有效采用产物及其稳定 ID；不得读取旧生产对话或未列入启动包的历史内容。"),
        "- 本交接不等于下一阶段已启动，也不替代下一阶段的输入门禁。", "",
    ])
    if asset_batch:
        lines.extend(context._asset_batch_delivery_lines(asset_batch))
    output = context._handoff_path(project)
    content = "\n".join(lines)
    if len(content.encode("utf-8")) > context.LIMITS["handoff"]:
        content = "\n".join(relative_paths.get(line, line) for line in lines)
    report = context.validate_text("handoff", output, content)
    context.write_text_if_changed(output, content)
    report["from_stage"] = current_stage
    report["to_skill"] = target_skill
    report["artifact_ids"] = [item["artifact_id"] for item in inputs]
    if asset_batch:
        report.update(context._asset_batch_dispatch(project, args, asset_batch, output, kickoff))
    if testing:
        report.update(context.test_metadata())
    return report
