#!/usr/bin/env python3
"""Build and validate bounded stage context files without starting agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import nullcontext
from contextvars import ContextVar
from functools import wraps
from pathlib import Path, PureWindowsPath
from typing import Any

try:
    from .core.candidate_test import (context as test_context, configure as configure_test, resolve_inputs as test_inputs,
                                      result_metadata as test_metadata, require_production, require_production_manifest,
                                      validate_stage as validate_test_stage, validate_usage_contract)
    from .core.artifacts import (artifact_is_adopted, exact_narrative_approval, project_path, digest, cache_file_digests,
                                 validate_current_dependencies, load_manifest,
                                 register_workspace_artifact, adopt_initial_package,
                                 adopt_initial_agent_artifact, validate_candidate_submission)
    from .core.asset_refs import check_output_plan, registered_output_snapshot
    from .core.asset_batch_receipt import summarize_asset_batch_receipt, validate_asset_batch_targets
    from .core.character_layout import (character_layout_references, character_prompt_templates,
                                        character_template_kinds)
    from .core.agent_acceptance import register_workflow_authorization, require_workflow_authorization
    from .core.io import write_text, write_text_if_changed
    from .core.knowledge_runtime import (resolve_knowledge_root, resolve_knowledge_bundle, is_bundled_root,
                                          verify_bundle, verify_selected_files)
    from .core.knowledge_blocks import build_knowledge_index, load_policy, parse_knowledge_blocks
    from .core.knowledge_selector import render_knowledge_bundle, render_knowledge_candidates, render_candidate_trial_bundle, select_knowledge
    from .core.candidate_source_snapshot import CandidateSnapshotError, inspect_candidate_snapshot, load_candidate_snapshot_rules
    from .core.method_documents import MethodDocumentError, render_method_packet, resolve_method_documents
    from .core.product_reference_pack import resolve_product_pack
    from .core.project_state import load_project_state, normalize_project_state, write_project_state
    from .core.production_handoff import LEGACY_RULE, require_production_handoff, resolve_production_handoff
    from .core.project_kickoff import (REQUEST_PATH, RECREATION_ENTRY, RECREATION_METHOD, RECREATION_WORKFLOW,
                                       bind_stage_request, load_kickoff, load_project_request,
                                       product_demo_timing_advisory, production_workflow, validate_kickoff)
    from .core.review_publication import ChatScriptApproval, is_recorded_website_script_approval, require_script_approval
    from .core.io import PROJECT_LOCK_RELATIVE, file_lock, project_lock, write_json
    from .core.revision_control import RevisionControlError, commit_change, guard_action, inspect_revision_status, show_change
    from .core.review_delta import fingerprint, revision_delta, lightweight_revision
    from .session_usage import checkpoint
except ImportError:  # direct CLI execution
    from core.candidate_test import (context as test_context, configure as configure_test, resolve_inputs as test_inputs,
                                     result_metadata as test_metadata, require_production, require_production_manifest,
                                     validate_stage as validate_test_stage, validate_usage_contract)
    from core.artifacts import (artifact_is_adopted, exact_narrative_approval, project_path, digest, cache_file_digests,
                                validate_current_dependencies, load_manifest,
                                register_workspace_artifact, adopt_initial_package,
                                adopt_initial_agent_artifact, validate_candidate_submission)
    from core.asset_refs import check_output_plan, registered_output_snapshot
    from core.asset_batch_receipt import summarize_asset_batch_receipt, validate_asset_batch_targets
    from core.character_layout import (character_layout_references, character_prompt_templates,
                                       character_template_kinds)
    from core.agent_acceptance import register_workflow_authorization, require_workflow_authorization
    from core.io import write_text, write_text_if_changed
    from core.knowledge_runtime import (resolve_knowledge_root, resolve_knowledge_bundle, is_bundled_root,
                                         verify_bundle, verify_selected_files)
    from core.knowledge_blocks import build_knowledge_index, load_policy, parse_knowledge_blocks
    from core.knowledge_selector import render_knowledge_bundle, render_knowledge_candidates, render_candidate_trial_bundle, select_knowledge
    from core.candidate_source_snapshot import CandidateSnapshotError, inspect_candidate_snapshot, load_candidate_snapshot_rules
    from core.method_documents import MethodDocumentError, render_method_packet, resolve_method_documents
    from core.product_reference_pack import resolve_product_pack
    from core.project_state import load_project_state, normalize_project_state, write_project_state
    from core.production_handoff import LEGACY_RULE, require_production_handoff, resolve_production_handoff
    from core.project_kickoff import (REQUEST_PATH, RECREATION_ENTRY, RECREATION_METHOD, RECREATION_WORKFLOW,
                                      bind_stage_request, load_kickoff, load_project_request,
                                      product_demo_timing_advisory, production_workflow, validate_kickoff)
    from core.review_publication import ChatScriptApproval, is_recorded_website_script_approval, require_script_approval
    from core.io import PROJECT_LOCK_RELATIVE, file_lock, project_lock, write_json
    from core.revision_control import RevisionControlError, commit_change, guard_action, inspect_revision_status, show_change
    from core.review_delta import fingerprint, revision_delta, lightweight_revision
    from session_usage import checkpoint


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_DIR = Path("00_项目管理") / "执行上下文"
LIMITS = {"startup": 48 * 1024, "review": 12 * 1024, "handoff": 8 * 1024}
BANNED = {
    "图片载荷": re.compile(r"data:image|image_url|base64[,;]", re.I),
    "原始聊天": re.compile(r"(?:^|\n)#{1,4}\s*(?:原始聊天|完整对话|chat history)", re.I),
    "旧运行协议": re.compile("|".join(("required_context_" + "manifest", "dispatch_" + "envelope", "execution_" + "receipt", "visual_context_" + "gate")), re.I),
    "内部推理": re.compile(r"(?:chain.of.thought|内部推理|reasoning trace)", re.I),
}


class StageContextError(ValueError):
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code


_CONTEXT_ID: ContextVar[str | None] = ContextVar("stage_context_id", default=None)
IMAGE_SKILLS = {"asset-image-assistant", "storyboard-director"}
TASK_HANDOFF_SKILLS = {"creative-director", "storyboard-planner"}
_HANDOFF_DEFAULT_OBJECTIVE = "按目标 Skill 完成本轮已授权工作"
_ROUTED_HANDOFF_SCOPE = ("action", "artifact_ids", "asset_ids", "output_ids", "shot_ids", "segment_ids")
_ROUTED_SKILLS = {
    "creative-director": ("creative",),
    "storyboard-planner": ("director",),
    "asset-image-assistant": ("assets",),
    "storyboard-director": ("visual",),
    "voiceover-to-video-script": ("video", "canvas"),
}


def _context_dir(project: Path, *parts: str) -> Path:
    """Resolve only the selected execution context; business state stays shared."""
    return _context_path(project, _CONTEXT_ID.get(), *parts)


def _context_path(project: Path, context_id: str | None, *parts: str) -> Path:
    path = project / CONTEXT_DIR
    if context_id is None:
        return path.joinpath(*parts)
    path = (path / "批次" / context_id).joinpath(*parts)
    # Do not let an existing parent or batch symlink redirect isolated writes.
    current = project
    for part in path.relative_to(project).parts:
        current = current / part
        if current.is_symlink():
            raise StageContextError(f"批次上下文路径不得含符号链接：{current}")
    if not path.resolve().is_relative_to(project.resolve()):
        raise StageContextError("批次上下文路径越出项目")
    return path


def _task_handoff_context(args: argparse.Namespace) -> str | None:
    """Use the recipient's own stage context when a task has a bound handoff."""
    target = getattr(args, "to_skill", None)
    skill = str(target or getattr(args, "skill", None) or "").strip()
    identity = _reading_identity(args)
    if not any(identity.values()):
        return None
    if target and (skill not in TASK_HANDOFF_SKILLS or not all(identity.values())):
        raise StageContextError("普通编导或导演的任务交接须同时指定 --actor-id 与 --task-id")
    if skill not in TASK_HANDOFF_SKILLS or not all(identity.values()):
        return None
    context_id = "task-" + _identity_hash({"skill": skill, **identity})[:32]
    if target:
        return context_id
    project = Path(args.project).expanduser().resolve()
    if _context_path(project, context_id, "next_stage_handoff.md").is_file():
        return context_id
    return None


def _context_scoped(function):
    """Scope both CLI and direct Python calls without mutable global paths."""
    @wraps(function)
    def scoped(args, *values, **kwargs):
        context_id = getattr(args, "context_id", None)
        if context_id is None:
            context_id = _task_handoff_context(args)
        if context_id is not None and (not isinstance(context_id, str)
                or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", context_id) is None):
            raise StageContextError("--context-id 须为 1–64 位规范小写 ID（小写字母、数字、下划线或连字符）；不接受大小写别名")
        token = _CONTEXT_ID.set(context_id)
        try:
            if context_id is not None:
                _context_dir(Path(args.project).expanduser().resolve())
            report = function(args, *values, **kwargs)
            if context_id is not None:
                report["context_id"] = context_id
            return report
        finally:
            _CONTEXT_ID.reset(token)
    return scoped


def _image_batch_targets(skill: str, request: dict[str, Any], *, routed=False) -> list[str] | None:
    if skill not in IMAGE_SKILLS:
        return None
    targets = request.get("image_targets")
    if targets is None:
        if _CONTEXT_ID.get() is None or routed:
            return None
        raise StageContextError("隔离生图批次的 image_targets 须包含 1..8 个准确输出身份")
    if skill == "asset-image-assistant":
        try:
            return validate_asset_batch_targets(targets)
        except ValueError as exc:
            raise StageContextError(str(exc)) from exc
    if not isinstance(targets, list) or not 1 <= len(targets) <= 8:
        raise StageContextError("隔离生图批次的 image_targets 须包含 1..8 个准确输出身份")
    for target in targets:
        if (not isinstance(target, str) or not target or target != target.strip()
                or any(ord(char) < 32 for char in target)
                or any(char in target for char in ("/", "\\", "*", "?"))):
            raise StageContextError("image_targets 必须为准确输出 ID，不得使用空值、目录或通配符")
    if len(targets) != len(set(targets)):
        raise StageContextError("image_targets 不得重复")
    return list(targets)


def _vault_root(value: str | None) -> Path:
    try:
        return resolve_knowledge_root(value, repo_root=ROOT)
    except ValueError as exc:
        raise StageContextError(str(exc)) from exc


def _recorded_knowledge_bundle(manifest: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    value = manifest.get("vault_root")
    if not isinstance(value, str) or not value:
        raise StageContextError("任务依据缺少本项目知识根；请重新 prepare。")
    try:
        return resolve_knowledge_bundle(value, repo_root=ROOT)
    except ValueError as exc:
        raise StageContextError(f"任务依据的知识根已失效；请用仓库内置知识重新 prepare：{exc}") from exc


def _local_knowledge_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise StageContextError(f"本地知识或规范路径越出仓库：{path}；请重新 prepare。")
    return resolved


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageContextError(f"无法读取 JSON：{path}: {exc}") from exc


def _request_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    request = json.loads(raw.decode("utf-8"))
    if not isinstance(request, dict):
        raise StageContextError("request JSON 必须是对象")
    return request, hashlib.sha256(raw).hexdigest()


def _state(project: Path) -> dict[str, Any]:
    try:
        return load_project_state(project)
    except (OSError, ValueError) as exc:
        raise StageContextError(f"无法读取项目状态：{exc}") from exc


def _test_request(args, project):
    testing = test_context(project)
    explicit = bool(getattr(args, "candidate_test", False))
    if bool(testing) != explicit:
        raise StageContextError("隔离测试须配置 candidate-test 并显式传 --candidate-test；生产入口不消费测试稿")
    if not testing:
        try:
            require_production_manifest(project)
        except ValueError as exc:
            raise StageContextError(str(exc)) from exc
    return testing


def _selected_inputs(project, state):
    if test_context(project):
        return state, test_inputs(project)
    return _confirmed_project_inputs(project, state)


def _state_summary(raw: dict[str, Any]) -> dict[str, Any]:
    state = normalize_project_state(raw)
    return {key: value for key, value in state.items() if key != "schema_version"}


def authorize_workflow(args: argparse.Namespace) -> dict[str, Any]:
    """Record an existing project workflow authorization without approving artifacts."""
    project = Path(args.project).expanduser().resolve()
    authorization = register_workflow_authorization(
        project, authorization_id=args.authorization_id,
        authorization_ref=args.authorization_ref, skills=list(args.skill),
    )
    return {"ok": True, "authorization": authorization,
            "notice": "仅登记所引用户流程授权；不确认稿件，不上传、生成或发布"}


def accept_artifact(args: argparse.Namespace) -> dict[str, Any]:
    """Adopt/select one exact artifact and optionally preflight its saved segment."""
    try:
        from .core import artifacts as artifact_api, revision_control as revision_api
        from .core.agent_acceptance import validate_agent_acceptance
    except ImportError:  # direct CLI execution
        from core import artifacts as artifact_api, revision_control as revision_api
        from core.agent_acceptance import validate_agent_acceptance
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
            frozen = _read_json(project_path(project, frozen_path), default=None)
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
                from .canvas_readback import validate_submission
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


def adopt_package(args: argparse.Namespace, *, website_approval=None, chat_approval=None) -> dict[str, Any]:
    """Register and adopt an initial platform-neutral manuscript package."""
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
        package = json.loads(source.read_text(encoding="utf-8-sig"))
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
    if logical_id == "storyboard.package" and package.get("schema_version") != "4.0":
        raise StageContextError("首次正式采用只接收当前 storyboard.package 4.0；旧版本保持兼容读取")
    if logical_id == "video.prompt" and not isinstance(package.get("source_storyboard"), dict):
        raise StageContextError("首次正式采用视频提示词必须绑定已确认 source_storyboard")
    relative = source.relative_to(project).as_posix()
    try:
        checksum = digest(source)
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


def confirm_script_in_chat(args: argparse.Namespace) -> dict[str, Any]:
    """Apply a genuine conversation decision through the formal adoption transaction."""
    project = Path(args.project).expanduser().resolve(strict=True)
    with project_lock(project):
        require_production(project)
        message = json.loads(Path(args.confirmation_json).read_text(encoding="utf-8-sig"))
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


def _state_reading_view(summary: dict[str, Any]) -> dict[str, Any]:
    """Filter explicit unrelated artifact receipts for display only, never authorization."""
    current = set(summary.get('current_artifacts') or [])
    if not current:
        return dict(summary)
    def relevant(receipt):
        ids = receipt.get('artifact_ids')
        single = receipt.get('artifact_id')
        if ids is not None and not isinstance(ids, list):
            return True
        referenced = {i for i in (ids or []) if isinstance(i, str)}
        if isinstance(single, str) and single:
            referenced.add(single)
        return not referenced or bool(current & referenced)
    return {**summary, 'user_confirmations': [r for r in summary.get('user_confirmations', [])
                                              if not isinstance(r, dict) or relevant(r)]}


def _task_reading_view(basis: dict[str, Any]) -> dict[str, Any]:
    """Keep frozen requirements; omit machine catalogs and fingerprints, not decisions."""
    result = {k: v for k, v in basis.items() if k not in {'reading_manifest', 'standards', 'reading_reuse_key', 'input_snapshot', 'request_path', 'task_request', 'knowledge_profile'}}
    if kickoff := basis.get("task_request", {}).get("kickoff"):
        result["kickoff"] = kickoff
        if (timing := product_demo_timing_advisory(kickoff)) is not None:
            result["product_demo_timing"] = timing
    standards = dict(basis.get('standards') or {})
    standards.pop('local_documents', None)
    result['standards'] = standards
    return result


def _revision_summary(project: Path, change_id: str | None, target_ids: list[str] | None) -> dict[str, Any] | None:
    """Read bounded revision facts without becoming a dispatcher or state writer."""
    if not change_id:
        return None
    control = show_change(project)
    active = control.get("active_change")
    if not isinstance(active, dict) or active.get("change_id") != change_id:
        raise StageContextError(f"活动变更案不存在：{change_id}")
    targets = list(target_ids or [item.get("logical_id") for item in active.get("targets") or [] if item.get("logical_id")])
    try:
        guard_action(project, logical_ids=targets, change_id=change_id)
    except RevisionControlError as exc:
        raise StageContextError(str(exc)) from exc
    return {
        "change_id": change_id,
        "tier": active.get("tier"),
        "status": active.get("status"),
        "targets": targets,
        "frozen_logical_ids": active.get("frozen_logical_ids") or [],
        "evidence_refs": active.get("evidence_refs") or [],
        "reason": active.get("reason") or "",
    }


def _tombstone_fingerprints(vault: Path, policy: dict[str, Any], *, verified_manifest=None) -> set[str]:
    if is_bundled_root(vault):
        manifest = verify_bundle(vault) if verified_manifest is None else verified_manifest
        if not manifest["dynamic_rule_ids"]:
            return set()
    relative = (policy.get("discovery") or {}).get("tombstones")
    path = vault / relative if isinstance(relative, str) else None
    if is_bundled_root(vault) and path is not None:
        if relative not in {entry["path"] for entry in manifest["files"]}:
            return set()  # Not packaged; no assertion about upstream history.
        path = verify_selected_files(vault, (relative,), verified_manifest=manifest)[relative]
    if path is None or not path.is_file():
        return set()
    result: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StageContextError(f"墓碑文件第 {number} 行损坏：{exc}") from exc
        fingerprint = value.get("fingerprint") if isinstance(value, dict) else None
        if isinstance(fingerprint, str) and fingerprint:
            result.add(fingerprint)
    return result


def _load_review_profile(skill: str) -> tuple[Path, dict[str, Any]]:
    profile_path = _local_knowledge_path(ROOT / "skills" / skill / "review-profile.json")
    profile = _read_json(profile_path, default=None)
    if not isinstance(profile, dict):
        raise StageContextError(f"Skill 审核 profile 不存在或无效：{profile_path}")
    if profile.get("schema_version") != "1.0" or profile.get("skill") != skill:
        raise StageContextError(f"Skill 审核 profile 身份无效：{profile_path}")
    for field in ("skill_contract_checks", "production_artifact_checks"):
        items = profile.get(field)
        if not isinstance(items, list) or not items:
            raise StageContextError(f"Skill 审核 profile 缺少非空 {field}：{profile_path}")
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise StageContextError(f"Skill 审核 profile 的 {field} 项必须是对象：{profile_path}")
            check_id = item.get("id")
            text = item.get("text")
            if not isinstance(check_id, str) or not re.fullmatch(r"[A-Z][A-Z0-9-]{2,63}", check_id):
                raise StageContextError(f"Skill 审核检查项 ID 无效：{check_id!r}")
            if check_id in seen:
                raise StageContextError(f"Skill 审核检查项 ID 重复：{check_id}")
            if not isinstance(text, str) or not text.strip():
                raise StageContextError(f"Skill 审核检查项内容为空：{check_id}")
            seen.add(check_id)
    return profile_path, profile


def _handoff_path(project: Path) -> Path:
    return _context_dir(project, "next_stage_handoff.md")


def _legacy_revision_blockers(active_change, blockers):
    if isinstance(active_change, dict):
        pending = f"活动变更案未完成：{active_change.get('change_id', 'unknown')} ({active_change.get('status', 'unknown')})"
        return [pending, *blockers]
    return blockers


def _confirmed_handoff_inputs(project: Path, target_skill: str, *, state=None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    skill_path = ROOT / "skills" / target_skill / "SKILL.md"
    if not skill_path.is_file():
        raise StageContextError(f"目标 Skill 不存在：{target_skill}")
    state = _state(project) if state is None else state
    expected = str(state.get("next_stage") or "")
    if not expected:
        raise StageContextError("project_state.next_stage 为空；下一阶段未知，不能生成正式交接")
    if expected != target_skill:
        raise StageContextError(f"目标 Skill 与项目下一阶段不一致：state={expected}, requested={target_skill}")
    active_change, _, blockers = inspect_revision_status(project)
    if blockers := _legacy_revision_blockers(active_change, blockers):
        raise StageContextError("正式交接被修订状态阻止：" + "；".join(blockers))
    return _confirmed_project_inputs(project, state)


def _first_director_handoff(project, state):
    receipts = state["user_confirmations"]
    manifest = load_manifest(project)
    if not receipts and not any(a.get("logical_id") == "narrative.package"
            and a.get("status") == "current" and a.get("agent_acceptance")
            and a.get("artifact_id") in state["current_artifacts"] for a in manifest["artifacts"]):
        raise StageContextError("project_state.next_stage 为空；没有编导整稿的正式采用记录")
    active_change, _, blockers = inspect_revision_status(project)
    if blockers := _legacy_revision_blockers(active_change, blockers):
        raise StageContextError("正式交接被修订状态阻止：" + "；".join(blockers))
    _, inputs = _confirmed_project_inputs(project, state, manifest=manifest)
    for entry in inputs:
        if entry.get("logical_id") != "narrative.package":
            continue
        exact_receipts = [r for r in receipts if exact_narrative_approval(r, entry, entry["checksum"])]
        if any(is_recorded_website_script_approval(project, entry, r["approval_ref"]) for r in exact_receipts):
            return inputs
        if not exact_receipts and not entry.get("agent_acceptance"):
            continue
        for authorization in state["workflow_authorizations"]:
            if not isinstance(authorization, dict):
                continue
            try:
                require_workflow_authorization(project, authorization.get("id"), "storyboard-planner", state=state)
            except ValueError:
                continue
            return inputs
    raise StageContextError("project_state.next_stage 为空；缺少当前准确编导整稿的采用依据或已有导演流程授权")


def _uses_routed_handoff(project):
    request = load_project_request(project)
    return "kickoff" in request and LEGACY_RULE not in request.get("local_references", [])


def status(args: argparse.Namespace) -> dict[str, Any]:
    """Read project/repository facts without recovering or synchronizing state."""
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise StageContextError(f"项目目录不存在：{project}")
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
            state = _state(project)
        except (StageContextError, ValueError) as exc:
            blockers.append(f"project_state.json 无效；阶段状态未知：{exc}")
    candidate_mode = False
    candidate_context = None
    if state is not None and state.get("candidate_test") is not None:
        try:
            candidate_context = test_context(project)
            candidate_mode = candidate_context is not None
        except (StageContextError, ValueError) as exc:
            blockers.append(f"candidate_test 状态无效；隔离流程未知：{exc}")
    if not manifest_path.is_file():
        blockers.append("artifact_manifest.json 缺失；产物版本/失效状态未知")
    else:
        try:
            manifest = load_manifest(project)
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
                path = project_path(project, str(item.get("path_or_url") or ""))
                exists = path.is_file()
            except (OSError, ValueError) as exc:
                path = None
                exists = None
                artifact_blockers.append(f"产物路径无效，版本状态未知：{item.get('artifact_id', 'unknown')}: {exc}")
            checksum_ok = None
            if exists is True and item.get("checksum"):
                checksum_ok = digest(path) == item["checksum"]
            if item.get("status") == "current" and (exists is not True or checksum_ok is False):
                artifact_blockers.append(f"current 产物缺失或哈希漂移：{item.get('artifact_id', 'unknown')}")
            artifacts.append({"artifact_id": item.get("artifact_id"), "logical_id": item.get("logical_id"),
                "revision": item.get("revision"), "status": item.get("status"),
                "path": str(path) if path else None, "exists": exists, "checksum_ok": checksum_ok})
    revision_status = inspect_revision_status(project)
    active_change, transaction_rows, revision_blockers = revision_status
    if candidate_mode and active_change is None:
        active_change = "not_applicable_candidate_test"
    repo = {"root": str(ROOT), "branch": None, "revision": None,
            "baseline_branch": "main", "baseline_revision": None, "working_tree_clean": None}
    try:
        branch = subprocess.run(["git", "-C", str(ROOT), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
        revision = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        baseline = subprocess.run(["git", "-C", str(ROOT), "merge-base", "HEAD", "main"], check=True, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
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
        flow = (resolve_production_handoff(project, manifest=manifest, state=state, revision_status=revision_status)
                if manifest is not None and state is not None else {"steps": []})
        if not flow["steps"]:
            blockers.extend(revision_blockers)
        next_actions = [step for step in flow["steps"] if step["status"] == "ready"] if not blockers else []
        if not next_actions:
            blockers.extend(step["reason"] for step in flow["steps"] if step["status"] == "waiting")
        next_skills = {step["skill"] for step in next_actions if "skill" in step}
        production_ok = bool(next_actions)
        return {**facts, **flow, "mode": "production", "next_actions": next_actions,
                "next_allowed_stage": next(iter(next_skills)) if len(next_skills) == 1 else None,
                "next_stage_note": "当前可执行范围见 next_actions；等待项见 steps。",
                "production_allowed": production_ok, "production_ok": production_ok,
                "blockers": blockers, "ok": production_ok}

    blockers.extend(artifact_blockers)
    blockers.extend(_legacy_revision_blockers(active_change, revision_blockers))
    next_stage = state.get("next_stage") if isinstance(state, dict) else None
    if not isinstance(next_stage, str) or not next_stage.strip():
        blockers.append("next_stage 为空；下一允许阶段未知")
        next_stage = None
    elif not (ROOT / "skills" / next_stage / "SKILL.md").is_file():
        blockers.append(f"next_stage 指向不存在的 Skill：{next_stage}")
        next_stage = None
    elif not state.get("current_stage"):
        blockers.append("current_stage 为空；不能生成正式交接")
        next_stage = None
    elif manifest is not None and state is not None:
        try:
            _confirmed_project_inputs(project, state)
        except (StageContextError, ValueError) as exc:
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
            test_inputs(project)
        except (StageContextError, ValueError, OSError) as exc:
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


def _confirmed_project_inputs(project: Path, state: dict[str, Any], *, manifest=None, file_digest=None,
                              artifact_ids=None, checked_dependencies=None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Resolve selected versions with valid human confirmation or agent acceptance."""
    if artifact_ids is None:
        artifact_ids = state.get("current_artifacts") or []
    if not isinstance(artifact_ids, list) or not artifact_ids or any(not isinstance(item, str) or not item for item in artifact_ids):
        raise StageContextError("项目没有可交接的当前有效产物 ID")
    try:
        manifest = load_manifest(project) if manifest is None else manifest
    except (OSError, ValueError) as exc:
        raise StageContextError("artifact_manifest.json 不存在或无效：" + str(exc)) from exc
    indexed = {item.get("artifact_id"): item for item in manifest["artifacts"] if isinstance(item, dict)}
    inputs: list[dict[str, Any]] = []
    file_digest = cache_file_digests(digest) if file_digest is None else file_digest
    checked_dependencies = set() if checked_dependencies is None else checked_dependencies
    for artifact_id in artifact_ids:
        item = indexed.get(artifact_id)
        if not isinstance(item, dict) or item.get("status") != "current":
            raise StageContextError(f"交接产物不是已确认 current 或有效自主验收 current：{artifact_id}")
        if not artifact_is_adopted(item, state, project):
            raise StageContextError(f"当前有效产物缺少用户确认或有效自主验收：{artifact_id}")
        path = project_path(project, str(item.get("path_or_url") or ""))
        if not path.is_file():
            raise StageContextError(f"交接产物文件不存在：{artifact_id}: {path}")
        if not isinstance(item.get("checksum"), str) or not re.fullmatch(r"[a-f0-9]{64}", item["checksum"]):
            raise StageContextError(f"交接产物缺少有效确认 hash：{artifact_id}")
        if file_digest(path) != item["checksum"]:
            raise StageContextError(f"交接产物内容与确认版本不一致：{artifact_id}")
        try:
            if item.get("logical_id") in {"narrative.package", "storyboard.package", "video.prompt"}:
                if not validate_candidate_submission(project, item, manifest, reading_existing=True, state=state,
                                                     checked=checked_dependencies, file_digest=file_digest):
                    raise ValueError("旧稿件/未识别结构仅保留历史读取，不能作为正式交接输入")
            else:
                validate_current_dependencies(item, manifest, state, project,
                                              checked=checked_dependencies, file_digest=file_digest)
                if item.get("asset_review_sha256"):
                    if not registered_output_snapshot(project, item):
                        raise ValueError("资产交接存在无效的逐图代审或图片字节依据：" + artifact_id)
        except ValueError as exc:
            raise StageContextError(str(exc)) from exc
        inputs.append(item)
    return state, inputs


def _current_handoff_text(project: Path, target_skill: str, snapshot: dict[str, Any] | None = None) -> str:
    """Consume a card only for its target and still-current input identities."""
    path = _handoff_path(project)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    validate_text("handoff", path, text)
    target = re.search(r"(?m)^- 目标 Skill：`([^`\n]+)`\s*$", text)
    if not target:
        raise StageContextError("已有交接卡缺少目标 Skill；先重新运行 handoff，不能作为本轮输入。")
    if target.group(1) != target_skill:
        return ""  # Another stage's card is not an input to this preparation.
    fields = ("artifact_id", "logical_id", "revision", "type", "path")
    pattern = r"(?m)^- artifact_id：`([^`\n]+)`\n" + "\n".join(
        rf"  - {field}：`([^`\n]*)`" for field in fields[1:]
    )
    if snapshot is None:
        snapshot = _input_snapshot(project, {}, _state(project))
    indexed = {fact["record"]["artifact_id"]: fact for fact in snapshot["artifacts"]}
    selected_ids = snapshot["selected_artifact_ids"]
    inputs = [indexed[ident] for ident in selected_ids]
    testing = bool(snapshot.get("candidate_test"))
    scoped = snapshot.get("artifact_scope") == "declared"
    routed = _routed_handoff_scope(text)
    initial_creative = routed and routed["action"] == "creative" and not routed.get("artifact_ids")
    if not testing and not inputs and not scoped and not initial_creative:
        raise StageContextError("项目没有可交接的当前有效产物 ID")
    if testing != ("execution_mode: candidate_test" in text):
        raise StageContextError("交接卡运行模式与项目不一致；重新 handoff")
    hash_pattern = r"(?m)^  - (?:candidate_)?sha256：`([a-f0-9]{64})`$"
    has_hashes = bool(re.search(hash_pattern, text))
    if scoped and not routed:
        # Existing cards list every current input. Consume and show only rows
        # relevant to the new task, leaving the original card untouched.
        selected = set(selected_ids)
        text = re.sub(r"(?m)^- artifact_id：`([^`\n]+)`\n(?:^  [^\n]*(?:\n|$))*",
                      lambda row: row[0] if row[1] in selected else "", text)
    records = [(*record[:-1], str(project_path(project, record[-1])))
               for record in re.findall(pattern, text)]
    if routed:
        declared = set(routed.get("artifact_ids", []))
        if declared != {record[0] for record in records} or not declared.issubset(selected_ids):
            raise StageContextError("本轮准备未绑定程序交接卡指定的全部产物；按原交接范围准备。")
        inputs = [fact for fact in inputs if fact["record"]["artifact_id"] in declared]
    expected = [tuple(str(fact["record"].get(field, "")) for field in fields[:-1])
                + (fact["file"]["path"],) for fact in inputs]
    if sorted(records) != sorted(expected):
        raise StageContextError("已有交接卡的产物、版本或路径已过期；先按当前确认输入重新运行 handoff。")
    if testing or has_hashes:
        ordered = [indexed[record[0]] for record in records] if scoped or routed else inputs
        if re.findall(hash_pattern, text) != [fact["file"]["sha256"] for fact in ordered]:
            raise StageContextError("交接卡来源字节已改变；重新核对并 handoff")
    return text


def _routed_handoff_scope(text: str) -> dict[str, Any] | None:
    match = re.search(r"(?m)^- 程序交接范围：(.+)$", text)
    if not match:
        return None
    try:
        scope = json.loads(match[1])
    except ValueError as exc:
        raise StageContextError("程序交接范围无法读取；重新运行 handoff。") from exc
    if (not isinstance(scope, dict) or not isinstance(scope.get("action"), str)
            or ("workflow" in scope and scope["workflow"] != RECREATION_WORKFLOW)
            or any(not isinstance(scope.get(key, []), list)
                   or any(not isinstance(ident, str) or not ident for ident in scope.get(key, []))
                   for key in _ROUTED_HANDOFF_SCOPE[1:])):
        raise StageContextError("程序交接范围无效；重新运行 handoff。")
    return scope


def _handoff_scope(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    match = re.search(r"(?ms)^- 本轮目标：(.*?)\n^- 本轮停止点：(.*?)\n^-(?: 交接状态：| execution_mode:)", text)
    if not match:
        return {"sha256": _identity_hash(text.strip())}  # Do not assume an unrecognized older card is unchanged.
    objective, stop_at = (value.strip() for value in match.groups())
    bindings = _asset_batch_binding_entries(text)
    routed = _routed_handoff_scope(text)
    return {"objective": objective, "stop_at": stop_at,
            **({"routed": routed} if routed else {}),
            **({"asset_batch_sha256": _identity_hash(sorted(bindings))} if bindings else {})}


def _check_preparation_route(project, skill, request, routed, *, repair=False, asset_batch=False):
    """Keep formal startup on the live route, including after a stale card or a restart."""
    if repair or asset_batch or skill not in _ROUTED_SKILLS:
        return
    project_request = load_project_request(project)
    if LEGACY_RULE in project_request.get("local_references", []):
        return
    if not routed and project_request.get("project_mode") != "new":
        return
    action = _ROUTED_SKILLS[skill][0]
    if routed and routed["action"] != action:
        raise StageContextError("交接卡步骤与本轮准备不一致")
    requested_shots = request.get("source_shot_ids") if action == "visual" else None
    route = require_production_handoff(project, action, shot_ids=requested_shots)
    if routed:
        if action != "assets" and routed.get("workflow") != route.get("workflow"):
            raise StageContextError("制作路线已变化；重新运行 handoff")
        if not set(routed.get("artifact_ids", [])).issubset(route.get("artifact_ids", [])):
            raise StageContextError("程序交接输入已改版；重新运行 handoff")
        if (requested_shots is not None and not set(requested_shots).issubset(routed.get("shot_ids", []))
                or any(not set(routed.get(field, [])).issubset(route.get(field, []))
                       for field in ("asset_ids", "output_ids", "shot_ids")
                       if field != "shot_ids" or requested_shots is None)):
            raise StageContextError("程序交接范围已变化；重新运行 handoff 核对就绪范围")
    if action == "assets" and (targets := request.get("image_targets")):
        if "output_ids" not in route:
            raise StageContextError("资产交接范围为盘点和复用；出图目标须先登记当前制作授权")
        if (not {target.partition("@")[0] for target in targets}.issubset(route.get("asset_ids", []))
                or route.get("output_ids") and not set(targets).issubset(route["output_ids"])):
            raise StageContextError("图片目标超出程序交接范围")


def _coordinator_objective(skill: str, context_id, scope, testing=False) -> str | None:
    goal = (scope or {}).get("objective")
    return (goal if context_id and skill in TASK_HANDOFF_SKILLS and not testing and goal
            and goal != _HANDOFF_DEFAULT_OBJECTIVE else None)


def _startup_boundary_problems(text: str) -> list[str]:
    match = re.search(r"(?m)^- Skill：`([^`]+)`\s*$", text)
    if not match:
        return ["缺少可识别的 Skill 身份"]
    skill = match.group(1)
    profile_path = ROOT / "skills" / skill / "knowledge-profile.json"
    profile = _read_json(profile_path, default=None)
    if not isinstance(profile, dict) or profile.get("skill") != skill:
        return [f"Skill 知识 profile 不存在或身份无效：{profile_path}"]
    terms = profile.get("forbidden_knowledge_terms", [])
    if not isinstance(terms, list) or any(
        not isinstance(item, str) or not item.strip() for item in terms
    ):
        return [f"Skill 知识 profile 的 forbidden_knowledge_terms 无效：{profile_path}"]
    bundle_marker = f"# {skill} 当前知识包"
    if bundle_marker not in text:
        return [f"缺少 {skill} 当前知识包"]
    # 用 rfind 拿"最后一个 ## 选择记录"做知识包终点：prepare 会把选择记录追加在
    # 知识包末尾，而知识块的正文也可能合法包含"## 选择记录"字样（此前用 split 取
    # 第一个匹配会提前截断，导致其后的边界词漏检）。最后一个该标题必然是真正的
    # 选择记录，故以此截取知识包正文，避免漏放；选择记录只含 MSB-KB-* ID 与计数，
    # 不含边界词，无需纳入扫描。
    knowledge_text = text.split(bundle_marker, 1)[1]
    selection_end = knowledge_text.rfind("\n## 选择记录")
    if selection_end != -1:
        knowledge_text = knowledge_text[:selection_end]
    return [
        f"Skill 边界残留：{term.strip()}"
        for term in terms
        if term.strip() in knowledge_text
    ]


def _optional_experience_paths(vault: Path, profile: dict[str, Any]) -> list[tuple[str, bool]]:
    """Validate explicit optional experience paths against this run's Vault."""
    values = profile.get("optional_experience_scopes", [])
    if not isinstance(values, list):
        raise StageContextError("optional_experience_scopes 必须是明确的 Vault 相对 Markdown 文件路径数组。")
    result: list[tuple[str, bool]] = []
    for value in values:
        raw = Path(value.replace("\\", "/")) if isinstance(value, str) else None
        if raw is None or raw.is_absolute() or PureWindowsPath(value).drive or raw.suffix.lower() != ".md" or any(part == ".." for part in raw.parts):
            raise StageContextError("optional_experience_scopes 必须是明确的 Vault 相对 Markdown 文件路径数组。")
        target = (vault / raw).resolve()
        try:
            target.relative_to(vault.resolve())
        except ValueError as exc:
            raise StageContextError("optional_experience_scopes 路径越出本轮 Vault。") from exc
        result.append((raw.as_posix(), target.is_file()))
    return result


def validate_text(kind: str, path: Path, text: str | None = None) -> dict[str, Any]:
    if kind not in LIMITS:
        raise StageContextError(f"未知类型：{kind}")
    if text is None:
        if not path.is_file():
            raise StageContextError(f"文件不存在：{path}")
        text = path.read_text(encoding="utf-8")
    size = len(text.encode("utf-8"))
    problems = [name for name, pattern in BANNED.items() if pattern.search(text)]
    if kind == "startup":
        problems.extend(_startup_boundary_problems(text))
    if size > LIMITS[kind]:
        problems.append(f"超出 {LIMITS[kind]} 字节上限，当前 {size} 字节")
    if problems:
        raise StageContextError(f"{path.name} 校验失败：{'；'.join(problems)}")
    return {"kind": kind, "path": str(path), "bytes": size, "valid": True}


def candidate_source_index(args: argparse.Namespace) -> dict[str, Any]:
    """Read a pinned source archive and report trial-eligible items, never promote."""
    skill = str(args.skill or "").strip()
    profile_path = ROOT / "skills" / skill / "knowledge-profile.json"
    profile = _read_json(profile_path, default=None)
    if not isinstance(profile, dict) or profile.get("skill") != skill:
        raise StageContextError(f"Skill 知识 profile 无效：{profile_path}")
    try:
        result = inspect_candidate_snapshot(
            profile, Path(args.snapshot).expanduser().resolve(), args.source_id,
        )
    except CandidateSnapshotError as exc:
        raise StageContextError(str(exc)) from exc
    return {"ok": True, "read_only": True, "skill": skill, **result}


def _reading_identity(args: argparse.Namespace) -> dict[str, str]:
    return {
        "actor_id": str(getattr(args, "actor_id", "") or "").strip(),
        "task_id": str(getattr(args, "task_id", "") or "").strip(),
    }


def _candidate_record(block: Any) -> dict[str, Any]:
    record = {
        "id": block.id, "canonical_key": block.canonical_key, "kind": block.kind,
        "status": block.status, "authority": block.authority,
        "projects": list(block.projects), "stages": list(block.stages),
        "topics": list(block.topics), "triggers": list(block.triggers),
        "conflicts_with": list(block.conflicts_with), "priority": block.priority,
        "applies_when": block.applies_when, "vault_relative_path": block.vault_relative_path,
        "anchor": block.anchor, "sha256": block.fingerprint, "sources": list(block.sources),
    }
    if getattr(block, "source_status", None) is not None:
        record["source_status"] = block.source_status
    if getattr(block, "source_page_sha256", None) is not None:
        record["source_page_sha256"] = block.source_page_sha256
    return record


def _candidate_packet(candidates: list[Any], scopes: dict[str, str] | None = None) -> str:
    lines = ["## 已采用按需知识", ""]
    for block in candidates:
        if scopes and block.id in scopes:
            lines.extend([f"本职采用范围（执行者声明，仍须审核）：{scopes[block.id]}",
                          "以下为完整来源快照；范围外实现项不成为当前 Skill 的产出要求，职责边界不变。", ""])
        lines.extend([f"### {block.id} [{block.status}]", "", f"- Vault：`{block.vault_relative_path}`", f"- 锚点：`^{block.anchor}`", f"- SHA-256：`{block.fingerprint}`", "", block.body.strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def _candidate_manifest_matches(block: Any, record: dict[str, Any]) -> bool:
    return _candidate_record(block) == record


def _evidence_current(project: Path, requested_skill: str | None = None, *, basis=None) -> tuple[dict[str, Any], dict[str, Any], Path] | None:
    if basis is None:
        basis = _read_json(_task_basis_path(project), default={})
    if not isinstance(basis, dict) or basis.get("context_id") != _CONTEXT_ID.get():
        return None
    manifest = basis.get("reading_manifest")
    try:
        evidence = _read_json(_context_dir(project, "method_reading_evidence.json"), default=None)
    except StageContextError:
        return None  # Broken optional reading state is unread, not a preparation failure.
    packet = _context_dir(project, "method_reading_packet.md")
    if not isinstance(manifest, dict) or not isinstance(evidence, dict) or not packet.is_file():
        return None
    key = basis.get('reading_reuse_key')
    if key and evidence.get('reading_reuse_key') != key:
        return None
    keys = ("skill", "project_path", "actor_id", "task_id", "context_id", "vault_root", "test_only_proposed_method_ids", "methods", "selected")
    if requested_skill and manifest.get("skill") != requested_skill:
        return None
    if any(evidence.get(key) != manifest.get(key) for key in keys):
        return None
    if not all(isinstance(manifest.get(key), str) and manifest[key] for key in ("skill", "project_path", "actor_id", "task_id", "vault_root")):
        return None
    packet_bytes = packet.read_bytes()
    if evidence.get("packet_sha256") != hashlib.sha256(packet_bytes).hexdigest():
        return None
    base_length = manifest.get("method_packet_bytes")
    if not isinstance(base_length, int) or base_length < 0 or hashlib.sha256(packet_bytes[:base_length]).hexdigest() != manifest.get("method_packet_sha256"):
        return None
    if evidence.get("method_packet_sha256") != manifest.get("method_packet_sha256"):
        return None
    catalog = {item.get("id"): item for item in manifest.get("candidate_catalog", []) if isinstance(item, dict)}
    adopted = evidence.get("adopted_candidates", [])
    if not isinstance(adopted, list) or len({item.get("id") for item in adopted if isinstance(item, dict)}) != len(adopted):
        return None
    if any(catalog.get(item.get("id")) != item for item in adopted if isinstance(item, dict)):
        return None
    scopes = evidence.get("candidate_scopes", {})
    if (not isinstance(scopes, dict) or any(key not in {item["id"] for item in adopted}
            or not isinstance(value, str) or not value.strip() for key, value in scopes.items())):
        return None
    reasons = manifest.get("candidate_reasons", {})
    if any(str(reasons.get(item["id"], "")).startswith("skill_boundary:") and item["id"] not in scopes
           for item in adopted):
        return None
    return manifest, evidence, packet


def _reading_cache_path(project: Path, skill: str) -> Path:
    if not re.fullmatch(r'[a-z0-9-]+', skill):
        raise StageContextError('无效的缓存 Skill 名称')
    return _context_dir(project, 'stage_reading_cache', skill + '.json')


def _save_reading_cache(project: Path) -> None:
    current = _evidence_current(project)
    if current is None:
        return
    manifest, evidence, packet = current
    if not evidence.get('reading_reuse_key'):
        return
    value = {'evidence': evidence, 'packet': packet.read_text(encoding='utf-8')}
    write_text_if_changed(_reading_cache_path(project, manifest['skill']),
                          json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def _restore_reading_cache(project: Path, skill: str, key: str) -> bool:
    try:
        value = _read_json(_reading_cache_path(project, skill), default={})
        evidence, packet = value.get('evidence'), value.get('packet')
        if not isinstance(evidence, dict) or not isinstance(packet, str):
            return False
        if evidence.get('reading_reuse_key') != key or evidence.get('packet_sha256') != hashlib.sha256(packet.encode('utf-8')).hexdigest():
            return False
    except (StageContextError, AttributeError):
        return False  # An unusable cache never authorizes reading or blocks fresh preparation.
    write_text_if_changed(_context_dir(project, 'method_reading_packet.md'), packet)
    write_text_if_changed(_context_dir(project, 'method_reading_evidence.json'), json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    return _evidence_current(project, skill) is not None


def _identity_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _semantic_request(request: dict[str, Any], skill: str) -> dict[str, Any]:
    semantic = {key: value for key, value in request.items()
                if key not in {"history", "updated_at", "created_at"}}
    kickoff = semantic.get("kickoff")
    # The people writing the story and shot plan never submit image jobs.
    if skill in {"creative-director", "storyboard-planner"} and isinstance(kickoff, dict):
        semantic["kickoff"] = {key: value for key, value in kickoff.items() if key != "image_generation"}
    elif skill == "asset-image-assistant" and isinstance(kickoff, dict):
        creation = kickoff.get("creation")
        if isinstance(creation, dict) and "workflow" in creation:
            semantic["kickoff"] = {**kickoff, "creation": {key: value for key, value in creation.items()
                                                            if key != "workflow"}}
    return semantic


def _normalize_source_alias(inputs: dict[str, Any]) -> dict[str, Any]:
    """The request-level sources alias declares local files, not semantic sources.

    Normalize at the input boundary only; do not reinterpret arbitrary nested
    prose/metadata named sources or enumerate undeclared project files.
    """
    if "sources" not in inputs:
        return inputs
    sources = inputs["sources"]
    if (not isinstance(sources, list) or not sources
            or any(not isinstance(item, str) or not item.strip() for item in sources)):
        raise StageContextError("request.sources 必须是非空本地文件路径数组。")
    if "source_files" in inputs and inputs["source_files"] != sources:
        raise StageContextError("request.sources 与 source_files 声明不一致；请保留准确的同一来源列表。")
    return {**{key: value for key, value in inputs.items() if key != "sources"},
            "source_files": sources}


def _explicit_source_paths(project: Path, inputs: dict[str, Any]) -> list[Path]:
    """Resolve declared files only, never enumerate the project or guess ID paths."""
    paths: set[Path] = set()
    file_keys = {"path", "path_or_url", "file", "files", "source", "source_markdown",
                 "source_json", "source_image", "source_video", "source_audio", "source_document", "source_url"}
    def collect(value: Any, key: str = "") -> None:
        if key in file_keys and (value is None or value == "" or value == []):
            raise StageContextError(f"必需显式来源文件缺失：{key}")
        if isinstance(value, dict):
            for name, child in value.items():
                collect(child, name)
        elif isinstance(value, list):
            for child in value:
                collect(child, key)
        elif isinstance(value, str) and value:
            explicit = key in file_keys or key.endswith(("_path", "_paths", "_file", "_files"))
            path_like = "/" in value or "\\" in value or bool(Path(value).suffix)
            source_path = key.startswith("source_") and not key.endswith(("_id", "_ids", "_type", "_kind", "_format", "_version", "_description", "_note"))
            if explicit or ((key == "inputs" or source_path) and path_like):
                if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
                    raise StageContextError(f"显式来源不是可核验的本地文件：{value}；先提供准确本地快照。")
                path = project_path(project, value)
                if not path.is_file():
                    raise StageContextError(f"必需显式来源文件缺失：{value}")
                paths.add(path)
    collect(_normalize_source_alias(inputs))
    return sorted(paths)


def _input_snapshot(project: Path, inputs: dict[str, Any], state: dict[str, Any], *, file_digest=None,
                    prefer_declared_artifacts=False, source_roles=()) -> dict[str, Any]:
    """Bound current validity to explicit sources and state-selected upstream inputs."""
    sources = _explicit_source_paths(project, inputs)
    try:
        manifest = load_manifest(project)
    except (OSError, ValueError) as exc:
        raise StageContextError(f"artifact_manifest.json 无效：{exc}") from exc
    testing = test_context(project)
    current_ids = list(testing["input_artifact_ids"] if testing else (state.get("current_artifacts") or []))
    declared_ids = []
    def collect_ids(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                collect_ids(child, name)
        elif isinstance(value, list):
            for child in value:
                collect_ids(child, key)
        elif key in {"artifact_id", "artifact_ids", "source_artifact_id", "source_artifact_ids"}:
            if not isinstance(value, str) or not value:
                raise StageContextError("显式来源产物 ID 无效")
            if value not in declared_ids:
                declared_ids.append(value)
    collect_ids(inputs)
    # Explicit registered sources are authoritative too, even if absent from the
    # convenience selection. Keep ordinary unregistered raw sources supported.
    source_names = {str(path) for path in sources}
    for entry in manifest.get("artifacts", []):
        if not isinstance(entry, dict) or not entry.get("path_or_url"):
            continue
        try:
            path = project_path(project, entry["path_or_url"])
        except ValueError:
            continue
        if str(path) in source_names and entry.get("artifact_id") not in declared_ids:
            declared_ids.append(entry.get("artifact_id"))
    if prefer_declared_artifacts and not testing:
        for role in source_roles:
            matches = [entry["artifact_id"] for entry in manifest["artifacts"]
                       if entry.get("logical_id") == role and entry.get("status") == "current"]
            if len(matches) > 1:
                raise StageContextError(f"本轮上游须有唯一 current 产物：{role}")
            declared_ids.extend(ident for ident in matches if ident not in declared_ids)
    scoped = prefer_declared_artifacts and not testing and bool(declared_ids)
    selected = declared_ids if scoped else list(dict.fromkeys([*current_ids, *declared_ids]))
    records = []
    file_digest = cache_file_digests(digest) if file_digest is None else file_digest
    if selected:
        if testing:
            records = test_inputs(project, selected)
        elif scoped:
            checked = set()
            _, records = _confirmed_project_inputs(project, state, manifest=manifest, file_digest=file_digest,
                                                   artifact_ids=selected, checked_dependencies=checked)
            upstream = sorted(checked.difference(selected))
            if upstream:
                _, dependencies = _confirmed_project_inputs(project, state, manifest=manifest, file_digest=file_digest,
                                                            artifact_ids=upstream, checked_dependencies=checked)
                records.extend(dependencies)
            selected = [item["artifact_id"] for item in records]
        else:
            _, records = _confirmed_project_inputs(project, {**state, "current_artifacts": selected},
                                                   manifest=manifest, file_digest=file_digest)
    selected_set = set(selected)

    def file_fact(path: Path) -> dict[str, str]:
        return {"path": str(path), "sha256": file_digest(path)}

    def relevant(receipt: Any) -> bool:
        if not isinstance(receipt, dict):
            return True
        ids = receipt.get("artifact_ids", [])
        if not isinstance(ids, list):
            return True  # Unknown scope cannot be discarded as irrelevant.
        ids = [*ids, receipt.get("artifact_id")]
        referenced = {item for item in ids if isinstance(item, str) and item}
        return not referenced or bool(referenced & selected_set)
    accepted_issues = state.get("accepted_issues", [])
    if scoped and isinstance(accepted_issues, list):
        accepted_issues = [item for item in accepted_issues if relevant(item)]
    return {
        "sources": [file_fact(path) for path in sources],
        "selected_artifact_ids": sorted(selected_set.intersection(current_ids)) if scoped else current_ids,
        **({"artifact_scope": "declared"} if scoped else {}),
        **({"candidate_test": testing} if testing else {}),
        "artifacts": [{"record": {k: v for k, v in item.items() if k not in {"updated_at", "created_at"}},
                       "file": file_fact(project_path(project, item["path_or_url"])),
                       "confirmation_byte_binding": "recorded" if item.get("checksum") else "legacy_unhashed"}
                      for item in records],
        "authority": {"user_confirmations": [item for item in state.get("user_confirmations", []) if relevant(item)],
                      "accepted_issues": accepted_issues,
                      "project_status": state.get("project_status"), "finalization": state.get("finalization")},
    }


def _task_source_roles(skill: str, request: dict[str, Any]) -> list[str]:
    """Include upstream packages that the selected phase uses by contract."""
    roles = []
    if skill == "asset-image-assistant" or request.get("material_output_ids"):
        roles.append("narrative.package")
    if skill == "storyboard-director" and (request.get("source_shot_ids") or request.get("supplement_request_paths")):
        roles.append("storyboard.package")
    if request.get("material_output_ids"):
        roles.append("visual.asset.manifest")
    return roles


def _recovery_check_text() -> str:
    """Project one authoritative section; do not maintain a second checklist."""
    path = _local_knowledge_path(ROOT / "skills/_shared/knowledge-access.md")
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^## 压缩后的主动恢复核对\n.*?(?=^## |\Z)", text)
    if not match:
        raise StageContextError(f"主动恢复核对正文缺失：{path}")
    return match.group(0).rstrip()


# Cross-Skill reading registry (必读集边界规范 P5; approved by the user on
# 2026-11-09 after the BUG-3 review). A document owned by another Skill may
# enter a stage's standard set only through an entry here, and only as a
# leaf: its own outbound links never expand the set. Unregistered cross-Skill
# links are skipped silently (blocked is not missing, P7); a registered
# target that does not exist fails closed like any required standard.
_CROSS_SKILL_READS: dict[tuple[str, str], str] = {
    ("storyboard-planner", "skills/knowledge-maintainer/modules/maintenance-method.md"):
        "导演阶段沿用知识维护方法，既有测试断言其在导演必读集内；2026-11-09 用户审定保留并登记（规范 Q1）。",
}

# Registered dependencies: when the trigger document is in the set, the
# listed foreign documents follow as leaves.
_CROSS_SKILL_DEPENDENCIES: dict[tuple[str, str], tuple[str, ...]] = {
    ("creative-director", "skills/creative-director/references/knowledge-candidates.md"):
        ("skills/knowledge-maintainer/modules/maintenance-method.md",),
}

_REDFRAME_IMAGE_STANDARDS = (
    "skills/_shared/redframe-operations.md",
    "skills/voiceover-to-video-script/references/redframe-canvas-usage.md",
    "skills/voiceover-to-video-script/references/redframe-canvas-layout.md",
)


def _redframe_image_batch(skill: str, request: dict[str, Any]) -> bool:
    if skill not in IMAGE_SKILLS or not request.get("image_targets"):
        return False
    kickoff = request.get("kickoff") or {}
    grant = kickoff.get("image_generation") or {}
    return grant.get("provider") == "redframe"


def _validate_redframe_supplement_targets(project, request, snapshot):
    try:
        from .core.schema_contracts import require_schema
    except ImportError:
        from core.schema_contracts import require_schema

    targets = request['image_targets']
    paths = request.get('supplement_request_paths')
    if (not isinstance(paths, list) or len(paths) != len(targets)
            or any(not isinstance(path, str) or not path.strip() for path in paths)):
        raise StageContextError('红帧补图批次须通过 supplement_request_paths 逐目标指向现有补图请求')
    source_digests = {item['path']: item['sha256'] for item in snapshot['sources']}
    directors = {item['record']['artifact_id']: item for item in snapshot['artifacts']
                 if item['record'].get('logical_id') == 'storyboard.package'}
    directors_by_path = {}
    for target, path in zip(targets, paths):
        source = project_path(project, path)
        if not source.is_relative_to(project):
            raise StageContextError('补图请求须位于当前项目内')
        decision, sha256 = _request_snapshot(source)
        if source_digests.get(str(source)) != sha256:
            raise StageContextError(f'{target} 的补图请求在准备期间变化；请重新 prepare')
        require_schema(decision, 'visual-supplement-request.schema.json', label='视觉补充请求')
        if (decision['request_id'] != target or decision['decision'] != 'request_supplement'
                or decision['status'] != 'ready'):
            raise StageContextError(f'{target} 须对应 ready 的实际缺图请求；复用或退回上游的条目不进入生图批次')
        reference = decision['source_storyboard']
        director = directors.get(reference['artifact_id'])
        if (director is None or director['record']['revision'] != reference['revision']
                or Path(director['file']['path']) != project_path(project, reference['path'])
                or reference['status'] != ('confirmed' if director['record'].get('user_approved') is True
                                          else 'agent_accepted')):
            raise StageContextError(f'{target} 的补图请求所引导演稿不是本轮选定的准确已采用版')
        director_path = Path(director['file']['path'])
        if director_path not in directors_by_path:
            board, board_sha256 = _request_snapshot(director_path)
            if board_sha256 != director['file']['sha256']:
                raise StageContextError('导演稿在准备期间变化；请重新 prepare')
            directors_by_path[director_path] = (board['project_id'], {shot['shot_id'] for scene in board['scenes']
                                                                     for shot in scene['shots']})
        project_id, available_shots = directors_by_path[director_path]
        if decision['project_id'] != project_id or not set(decision['source_shot_ids']) <= available_shots:
            raise StageContextError(f'{target} 的项目或镜号与已采用导演稿不一致')


def _registered_read_path(reference: str) -> Path | None:
    """Resolve a registry target. Missing target file fails closed when the
    target Skill exists in this repository; a repository that does not carry
    the target Skill at all (synthetic test roots) simply has no such read."""
    path = (ROOT / reference).resolve()
    if path.is_file():
        return path
    if (ROOT / "skills" / Path(reference).parts[1]).is_dir():
        raise StageContextError(f"跨 Skill 登记共读目标缺失：{reference}")
    return None


def _local_standard_documents(skill_dir: Path, request: dict[str, Any] | None = None) -> list[Path]:
    """Freeze applicable contracts plus exact linked dependencies.

    Unknown conditions retain the conservative local set. References for
    explicitly selected branches do not become mandatory through links alone.
    Link traversal never crosses a Skill boundary (P2): a document owned by
    another Skill enters only via _CROSS_SKILL_READS, _CROSS_SKILL_DEPENDENCIES
    or an explicit local_references selection, and then only as a leaf.
    """
    request = request or {}
    excluded: set[Path] = set()
    # Web-route modules are entry-specific by nature, not by calling stage:
    # no prepare set may contain them, whichever stage is being prepared (P6).
    excluded.update((ROOT / "skills/asset-image-assistant/modules" / name).resolve() for name in
                    ("library-mode.md", "project-web-mode.md", "web-input-contract.md"))
    if skill_dir.name == "asset-image-assistant":
        # The Skill explicitly replaces prepare with bounded Web reading in
        # both Web modes. Those route-only modules cannot govern this entry.
        excluded.update((skill_dir / "modules" / name).resolve() for name in
                        ("library-mode.md", "project-web-mode.md", "web-input-contract.md"))
        scopes = request.get("asset_method_scopes", {})
        if not isinstance(scopes, dict) or any(key not in {"style", "character", "body", "scene", "object"}
                                             or type(value) is not bool for key, value in scopes.items()):
            raise StageContextError("asset_method_scopes 必须按现有类别填写布尔值。")
        if any(scopes.values()):
            conditional = {"style": ["01-style-master-guide.md"],
                           "character": ["02-character-master-guide.md"],
                           "body": ["02a-body-character-master-guide.md"],
                           "scene": ["03-scene-master-guide.md"],
                           "object": ["04-prop-product-master-guide.md"]}
            # Existing character requests did not distinguish anatomy. Keep
            # both guides until body is explicitly classified by this task.
            selected = {scope for scope, enabled in scopes.items() if enabled}
            if "body" not in scopes and scopes.get("character"):
                selected.add("body")
            excluded.update((skill_dir / "references" / name).resolve()
                            for scope, names in conditional.items() if scope not in selected
                            for name in names)
    local = [skill_dir / "SKILL.md", *sorted((skill_dir / "modules").glob("*.md")),
             *sorted((skill_dir / "references").glob("*.md"))]
    local = [_local_knowledge_path(path) for path in local if path.resolve() not in excluded]
    if (skill_dir.name in {"storyboard-planner", "storyboard-director"}
            and production_workflow(request.get("kickoff")) == RECREATION_WORKFLOW):
        local.extend(_local_knowledge_path(ROOT / path) for path in (RECREATION_ENTRY, RECREATION_METHOD))
    extra = request.get("local_references", [])
    if not isinstance(extra, list):
        raise StageContextError("local_references 必须是本轮实际采用的仓库相对规范路径数组。")
    for reference in extra:
        if not isinstance(reference, str) or not reference or Path(reference).is_absolute() or PureWindowsPath(reference).drive:
            raise StageContextError("local_references 必须使用仓库相对路径。")
        path = (ROOT / reference).resolve()
        if (not path.is_relative_to(ROOT.resolve()) or path.suffix not in {".md", ".json"}
                or not path.is_file() or not path.stat().st_size):
            raise StageContextError(f"本轮本地规范不存在、为空或越出仓库：{reference}")
        if path in excluded:
            raise StageContextError(f"本地规范与当前普通 prepare 入口或 asset_method_scopes 不一致：{reference}；先核对实际入口和类别。")
        # Nature-excluded contracts stay out through every channel (P4): the
        # Seedance delivery package is voiceover's internal contract, read
        # through its own direct-read entry; for any other stage an explicit
        # local_references selection does not admit it either.
        if (skill_dir.name != "voiceover-to-video-script"
                and path == (ROOT / "skills/voiceover-to-video-script/references/seedance-delivery-package.md").resolve()):
            raise StageContextError(f"本地规范是 voiceover 内部交付合同、不进其他阶段的 prepare 集：{reference}")
        local.append(path)
    if skill_dir.name == "creative-director":
        legacy = (skill_dir / "references/dialogue-legacy-workflows.md").resolve()
        candidates = (skill_dir / "references/knowledge-candidates.md").resolve()
        maintenance = (ROOT / "skills/knowledge-maintainer/modules/maintenance-method.md").resolve()
        selected_references = {(ROOT / reference).resolve() for reference in extra}
        if legacy not in selected_references:
            excluded.add(legacy)
        if candidates not in selected_references:
            excluded.add(candidates)
            if maintenance not in selected_references:
                excluded.add(maintenance)
        local = [path for path in local if path not in excluded]
    # These branches require explicit selection, even if a compatibility note
    # elsewhere links to them. The root rule table is navigation, not opt-in.
    for name in ("legacy-review.md", "experiments.md", "project-layout.md"):
        path = (ROOT / "docs/rules" / name).resolve()
        if path not in local:
            excluded.add(path)
    shared_root = (ROOT / "skills/_shared").resolve()
    # These are the common redlines named collectively by Skill entries; their
    # presence must not depend on whether prose spells each link separately.
    # The platform contract is selected separately for Redframe image batches
    # (or explicitly by the caller); its video-only links do not expand the set.
    shared = sorted(_local_knowledge_path(path) for path in shared_root.glob("*.md")
                    if path.name != "redframe-operations.md")
    leaf_paths: set[Path] = set()
    is_repository_skill = skill_dir.resolve() == (ROOT / "skills" / skill_dir.name).resolve()
    if is_repository_skill and _redframe_image_batch(skill_dir.name, request):
        for reference in _REDFRAME_IMAGE_STANDARDS:
            path = _registered_read_path(reference)
            if path is not None:
                leaf_paths.add(path)
    for (registered_skill, registered_reference) in _CROSS_SKILL_READS:
        if registered_skill == skill_dir.name and is_repository_skill:
            registered_path = _registered_read_path(registered_reference)
            if registered_path is not None:
                leaf_paths.add(registered_path)
    seen: set[Path] = set([*local, *shared, *leaf_paths])
    visited: set[Path] = set()
    pending = [_local_knowledge_path(ROOT / "PROJECT_RULES.md"), *local, *shared]
    while pending:
        document = pending.pop()
        if document in visited:
            continue
        visited.add(document)
        if document.suffix != ".md":
            continue
        skills_root = (ROOT / "skills").resolve()
        document_owner = (document.relative_to(skills_root).parts[0]
                          if document.is_relative_to(skills_root) else None)
        if document_owner is not None and document_owner not in {skill_dir.name, "_shared"}:
            continue
        text = _local_knowledge_path(document).read_text(encoding="utf-8")
        references = re.findall(r"(?:skills/|\.\./)*_shared/[A-Za-z0-9_-]+\.md", text)
        if document.parent == shared_root:
            references.extend(re.findall(r"\]\(([A-Za-z0-9_-]+\.md)(?=[#)])", text))
        # Redframe procedures are selected by the actual task, not by a link
        # mentioned in a general rule.
        references = [reference for reference in references
                      if Path(reference).name != "redframe-operations.md"]
        dependencies = []
        for reference in references:
            path = _local_knowledge_path(shared_root / Path(reference).name)
            if not path.is_file():
                raise StageContextError(f"必需共享规范缺失：{path}")
            dependencies.append(path)
        # Root-wide links describe other stages, not this task's contracts.
        # Follow explicit module/reference links from the selected contracts.
        if document != (ROOT / "PROJECT_RULES.md").resolve():
            refs = re.findall(r"(?:`|\]\()((?:\.\./|\./|skills/|docs/rules/|references/|modules/|templates/|schemas/)[^`\s)#]+\.(?:md|json))", text)
            for reference in refs:
                choices = [(document.parent / reference).resolve(),
                           (skill_dir / reference).resolve(), (ROOT / reference).resolve()]
                if document.is_relative_to((ROOT / "skills").resolve()):
                    owner = document.relative_to((ROOT / "skills").resolve()).parts[0]
                    choices.insert(1, (ROOT / "skills" / owner / reference).resolve())
                target = next((path for path in choices if path.is_relative_to(ROOT.resolve()) and path.is_file()), None)
                # A rule may point to an optional or downstream Skill. Freeze
                # this stage's contracts, not that other Skill's entire tree.
                rules_root = (ROOT / "docs/rules").resolve()
                linked = target or choices[0]
                if (document.is_relative_to(rules_root)
                        and linked.is_relative_to((ROOT / "skills").resolve())
                        and not linked.is_relative_to(skill_dir.resolve())
                        and linked.parent != shared_root):
                    continue
                # Boundary gate (P2, spec §4.1 equivalent form): outbound
                # links are followed only from first-party documents (this
                # Skill's own documents and _shared). A first-party link into
                # another Skill declares that dependency; the foreign document
                # then enters as a leaf whose own links never expand the set.
                if (target is not None and target.is_relative_to(skills_root)
                        and target.relative_to(skills_root).parts[0] not in {skill_dir.name, "_shared"}
                        and document_owner not in {skill_dir.name, "_shared"}):
                    continue
                # The Seedance delivery package is voiceover's internal
                # contract, governed by its direct-read list; it never enters
                # a prepare set (P4), not even via a _shared reference.
                if target == (ROOT / "skills/voiceover-to-video-script/references/seedance-delivery-package.md").resolve():
                    continue
                if target == (shared_root / "redframe-operations.md").resolve():
                    continue
                is_rule = any(path.is_relative_to((ROOT / "docs/rules").resolve()) for path in choices)
                if (target is None and not any(path in excluded for path in choices)
                        and (is_rule or {"references", "modules", "templates", "schemas"}.intersection(Path(reference).parts))):
                    raise StageContextError(f"明确引用的本地规范缺失：{document}: {reference}")
                if target and target not in excluded and (target.is_relative_to((ROOT / "docs/rules").resolve()) or target.parent == shared_root
                        or "references" in target.parts or "modules" in target.parts or "templates" in target.parts or "schemas" in target.parts):
                    dependencies.append(target)
        for path in dependencies:
            if path not in seen:
                seen.add(path)
                pending.append(path)
    for (dependency_skill, trigger_reference), dependency_references in _CROSS_SKILL_DEPENDENCIES.items():
        if dependency_skill == skill_dir.name and is_repository_skill and (ROOT / trigger_reference).resolve() in seen:
            for dependency_reference in dependency_references:
                dependency_path = _registered_read_path(dependency_reference)
                if dependency_path is not None:
                    seen.add(dependency_path)
    return sorted(seen)


def _review_identity(basis: dict[str, Any], adopted: list[dict[str, Any]] | None = None) -> str:
    # Rebuild the substantive projection, never trust a stored hash or include
    # actor IDs, reading cache/catalog metadata, request location or packet bytes.
    fields = ("schema_version", "skill", "objective", "deliverables", "non_goals", "input_versions", "handoff_scope",
              "scope", "delivery_contract", "standards", "review_acceptance", "revision", "task_request", "input_snapshot", "character_output_requirements", "character_layout_references", "character_prompt_templates")
    return _identity_hash({**{key: basis[key] for key in fields if key in basis}, "adopted_knowledge": adopted or []})


def _reading_reuse_key(basis):
    manifest = basis["reading_manifest"]
    identity = {key: manifest.get(key) for key in ("skill", "project_path", "actor_id", "task_id", "vault_root",
                                                 "test_only_proposed_method_ids", "methods", "selected", "method_packet_sha256")}
    if manifest.get("context_id") is not None:
        identity["context_id"] = manifest["context_id"]
    # A scoped card's presentation/stop point can change without changing the actual material read.
    reading_basis = {key: value for key, value in basis.items() if key != "handoff_scope"}
    if _coordinator_objective(basis["skill"], basis.get("context_id"), basis.get("handoff_scope"), basis.get("candidate_test")):
        reading_basis.pop("objective", None)
        if not basis["task_request"].get("deliverables"):
            reading_basis.pop("deliverables", None)
    return _identity_hash({"reading": identity, "task": _review_identity(reading_basis)})


def _validate_current_inputs(project: Path, basis: dict[str, Any], *, adopted_candidates=None, file_digest=None) -> dict[str, Any]:
    """Recheck known dependencies at consumption time; never grants execution authority."""
    manifest = basis.get("reading_manifest", {})
    if not isinstance(manifest, dict) or manifest.get("project_path") != str(project):
        raise StageContextError("任务依据属于另一项目；请在当前项目重新 prepare。")
    knowledge_root, verified_bundle = _recorded_knowledge_bundle(manifest)
    snapshot = basis.get("input_snapshot")
    if not isinstance(snapshot, dict):
        raise StageContextError("任务依据缺少当前输入版本证据；请重新 prepare。")
    request_path = basis.get("request_path")
    current_request = _read_json(Path(request_path), default=None) if request_path else {}
    if not isinstance(current_request, dict):
        raise StageContextError("当前阶段请求缺失；请重新 prepare。")
    kickoff = None if snapshot.get("candidate_test") else load_kickoff(project)
    skill = basis["skill"]
    frozen_request = basis.get("task_request")
    if (not isinstance(frozen_request, dict)
            or _semantic_request(bind_stage_request(current_request, kickoff), skill)
            != _semantic_request(frozen_request, skill)):
        raise StageContextError("开工决定或阶段请求已改变；请重新 prepare。")
    if basis.get("skill") == "asset-image-assistant":
        try:
            output_requirements = check_output_plan(project, basis.get("task_request", {}),
                                                    **({"candidate_test_mode": True} if snapshot.get("candidate_test") else {}))
        except ValueError as exc:
            raise StageContextError(str(exc)) from exc
        if (layout_refs := basis.get("character_layout_references")) and layout_refs != character_layout_references(layout_refs):
            raise StageContextError("本批人物灰模已变化；请重新 prepare。")
        if (templates := basis.get("character_prompt_templates")) and templates != character_prompt_templates(templates):
            raise StageContextError("本批人物生图提示词模板已变化；请重新 prepare。")
        if output_requirements != basis.get("character_output_requirements", {}):
            raise StageContextError("本批人物最终输出规格已变化；请重新 prepare 并核对受影响范围。")
    inputs = basis["input_versions"]
    if skill == 'storyboard-director' and _redframe_image_batch(skill, frozen_request) and not snapshot.get('candidate_test'):
        if 'supplement_request_paths' not in inputs:
            raise StageContextError('旧红帧补图批次缺少逐目标请求依据；请重新 prepare。')
    if "sources" in basis.get("task_request", {}):
        # Old preparations omitted this alias entirely. Reopen the declaration
        # at consumption too, so their empty snapshots cannot remain valid.
        inputs = {**inputs, "sources": basis["task_request"]["sources"]}
    scoped = snapshot.get("artifact_scope") == "declared"
    current = _input_snapshot(project, inputs, _state(project), file_digest=file_digest,
                              prefer_declared_artifacts=scoped, source_roles=_task_source_roles(skill, frozen_request))
    if current != snapshot:
        raise StageContextError("当前来源、选定产物或确认依据已改变；请重新 prepare 并核对变更。")
    scope = _handoff_scope(_current_handoff_text(project, basis["skill"], current))
    if "handoff_scope" in basis:
        previous_scope = basis["handoff_scope"]
    else:
        # Existing preparations already copied the card into the startup file.
        startup = _context_dir(project, "stage_startup.md")
        embedded = startup.read_text(encoding="utf-8").partition("\n## 上一阶段交接事实\n\n")[2] if startup.is_file() else ""
        previous_scope = _handoff_scope(embedded)
    coordinator_goal = _coordinator_objective(basis["skill"], basis.get("context_id"), scope, current.get("candidate_test"))
    if scope != previous_scope or (coordinator_goal is not None and basis.get("objective") != coordinator_goal):
        raise StageContextError("交接目标、停止点或图片批次依据已改变；请重新 prepare 核对当前范围。")
    if not current.get("candidate_test"):
        _check_preparation_route(project, skill, frozen_request, (scope or {}).get("routed"),
                                 repair=bool(basis.get("revision")),
                                 asset_batch=bool((scope or {}).get("asset_batch_sha256")))
    standards = basis["standards"]
    profile = basis.get("knowledge_profile", {})
    if not isinstance(profile, dict) or not profile.get("path") or not profile.get("sha256"):
        raise StageContextError("任务规范缺少版本证据；请重新 prepare。")
    profile_path = Path(profile["path"])
    if not manifest.get("test_only_proposed_method_ids"):
        profile_path = _local_knowledge_path(profile_path)
    if not profile_path.is_file() or digest(profile_path) != profile["sha256"]:
        raise StageContextError(f"当前方法、绑定或规范已改变：{profile_path}；请重新 prepare。")
    current_profile = _read_json(profile_path, default={})
    declared_methods = {entry.get("id"): entry for entry in current_profile.get("professional_methods", [])}
    local_documents = [*standards.get("local_documents", []), standards.get("review_profile", {})]
    if standards.get("root_rules_sha256"):
        local_documents.append({"path": standards["root_rules"], "sha256": standards["root_rules_sha256"]})
    documents = []
    for item in local_documents:
        if not item.get("path") or not item.get("sha256"):
            raise StageContextError("任务规范缺少版本证据；请重新 prepare。")
        documents.append((_local_knowledge_path(Path(item["path"])), item["sha256"]))
    bundled_documents = []
    for item in standards.get("professional_methods", []):
        bundled_documents.append((item["path"], item.get("sha256")))
        template_path = declared_methods.get(item["id"], {}).get("template_path")
        if template_path != item.get("template_path") or (template_path and not item.get("template_sha256")):
            raise StageContextError("适用方法模板缺少冻结版本；请重新 prepare。")
        if template_path:
            bundled_documents.append((template_path, item["template_sha256"]))
    bundled_paths = verify_selected_files(knowledge_root, (relative for relative, _ in bundled_documents),
                                          verified_manifest=verified_bundle)
    documents.extend((bundled_paths[relative], checksum) for relative, checksum in bundled_documents)
    document_digest = digest if file_digest is None else file_digest
    for path, checksum in documents:
        if not checksum:
            raise StageContextError("任务规范缺少版本证据；请重新 prepare。")
        if not path.is_file() or document_digest(path) != checksum:
            raise StageContextError(f"当前方法、绑定或规范已改变：{path}；请重新 prepare。")
    constraints = manifest.get("applicable_confirmed")
    if not isinstance(constraints, list):
        raise StageContextError("任务依据缺少预算外适用 confirmed 约束；请重新 prepare，不能沿用旧选择结果。")
    records = list(manifest.get("selected", [])) + constraints
    if adopted_candidates is None:
        evidence = _evidence_current(project, basis["skill"], basis=basis)
        adopted_candidates = evidence[1].get("adopted_candidates", []) if evidence else []
    records.extend(adopted_candidates)
    if records:
        tombstones = _tombstone_fingerprints(knowledge_root, load_policy(ROOT), verified_manifest=verified_bundle)
        for item in records:
            block = _read_candidate_exact(knowledge_root, item, verified_manifest=verified_bundle)
            if block.fingerprint in tombstones:
                raise StageContextError(f"当前知识已被禁用：{block.id}；请重新 prepare。")
    return {"current_input_validation": "matched_at_check", "execution_authorization": "not_granted",
            "legacy_unhashed_inputs": [item["record"]["artifact_id"] for item in current["artifacts"]
                                       if item["confirmation_byte_binding"] == "legacy_unhashed"]}


def _read_candidate_exact(vault: Path, record: dict[str, Any], *, verified_manifest=None) -> Any:
    if is_bundled_root(vault):
        manifest = verify_bundle(vault) if verified_manifest is None else verified_manifest
        if record.get("id") not in manifest["dynamic_rule_ids"]:
            raise StageContextError("动态规则未列入内置知识 manifest 允许列表")
        relative = record.get("vault_relative_path")
        verify_selected_files(vault, (relative,), verified_manifest=manifest)
    relative = record.get("vault_relative_path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise StageContextError("候选目录路径无效；请重新 prepare。")
    path = (vault / relative).resolve()
    try:
        path.relative_to(vault)
    except ValueError as exc:
        raise StageContextError("候选目录路径越出本轮 Vault；请重新 prepare。") from exc
    try:
        matches = [item for item in parse_knowledge_blocks(path, vault) if item.id == record.get("id")]
    except Exception as exc:
        raise StageContextError(f"候选知识无法按目录精确读取：{relative}: {exc}") from exc
    if len(matches) != 1 or not _candidate_manifest_matches(matches[0], record):
        raise StageContextError(f"候选知识版本、路径、状态或范围已变更：{record.get('id')}；请重新 prepare。")
    return matches[0]


def _check_run_budget(args: argparse.Namespace) -> dict[str, Any] | None:
    try:
        return checkpoint(args)
    except ValueError as exc:
        raise StageContextError(str(exc)) from exc


def _task_basis_path(project: Path) -> Path:
    """The one per-task source for scope and standards used by work and review."""
    return _context_dir(project, "task_basis.json")


def _load_task_basis(project: Path, skill: str) -> tuple[Path, dict[str, Any]]:
    path = _task_basis_path(project)
    basis = _read_json(path, default=None)
    if not isinstance(basis, dict) or basis.get("schema_version") != "1.0" or basis.get("skill") != skill:
        raise StageContextError(f"缺少与当前 Skill 匹配的任务依据：{path}；先运行 prepare，不能由审核包另选标准。")
    if basis.get("context_id") != _CONTEXT_ID.get():
        raise StageContextError("任务依据属于另一批次上下文；请在本批次重新 prepare。")
    for key in ("objective", "deliverables", "non_goals", "input_versions", "standards"):
        if key not in basis:
            raise StageContextError(f"任务依据缺少 {key}：{path}；请重新 prepare。")
    return path, basis


def _review_target(project: Path, path: Path, review_kind: str) -> Path:
    """Production reviews are project-local; contracts are repository-local."""
    root = project if review_kind == "production" else ROOT
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        label = "项目" if review_kind == "production" else "仓库"
        raise StageContextError(f"{review_kind} 审核目标必须位于{label}内：{resolved}") from exc
    return resolved


def _delivery_contract(request: dict[str, Any]) -> dict[str, Any]:
    """Freeze the artifact intent for stages that still use preparation."""
    delivery_object = str(request.get("delivery_object") or "plan")
    if delivery_object not in {"prompt", "plan", "submission"}:
        raise StageContextError("request.delivery_object 只能是 prompt、plan 或 submission；未知类型不能静默跳过验收项。")
    return {"delivery_object": delivery_object}


def _require_preparation_skill(skill: str | None) -> None:
    if skill == "voiceover-to-video-script":
        raise StageContextError(
            "voiceover-to-video-script 准备包流程已退役；按 Skill 入口直接读取原始资料、"
            "编写与自查提示词，不创建或复用准备包、阅读凭据或审核包。"
        )


def _applicable_checks(items: list[dict[str, Any]], delivery_contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Profiles may narrow checks by artifact type."""
    delivery_object = delivery_contract["delivery_object"]
    return [
        item for item in items
        if (not item.get("applies_to") or delivery_object in item["applies_to"])
    ]


def _producer_response_value(raw: str) -> Path | dict[str, Any]:
    """Accept an inline producer reply; a JSON file is only an optional carrier."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return Path(raw).expanduser().resolve()
    if not isinstance(value, dict):
        raise StageContextError("--producer-response 的内联值必须是 JSON 对象。")
    return value


@_context_scoped
def prepare(args: argparse.Namespace) -> dict[str, Any]:
    _require_preparation_skill(args.skill)
    usage = _check_run_budget(args)
    started = time.perf_counter()
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise StageContextError(f"项目目录不存在：{project}")
    skill_dir = ROOT / "skills" / args.skill
    profile_path = skill_dir / "knowledge-profile.json"
    test_method_ids = set(getattr(args, "test_proposed_method", None) or [])
    test_profile_path = getattr(args, "test_profile_json", None)
    if test_profile_path and not test_method_ids:
        raise StageContextError("--test-profile-json 只能与 --test-proposed-method 一起用于隔离测试。")
    profile_path = (Path(test_profile_path).expanduser().resolve() if test_profile_path
                    else _local_knowledge_path(profile_path))
    profile = _read_json(profile_path, default=None)
    if not isinstance(profile, dict) or profile.get("skill") != args.skill:
        raise StageContextError(f"Skill 知识 profile 无效：{profile_path}")
    request = {}
    request_path = request_snapshot = None
    if args.request_json:
        request_path = Path(args.request_json).resolve()
        try:
            request_snapshot = _request_snapshot(request_path)
            request = request_snapshot[0]
        except FileNotFoundError as exc:
            raise StageContextError(f"请求文件不存在：{request_path}；请提供明确请求来源，不从项目入口或待审候选推断。") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StageContextError(f"无法读取请求 JSON：{request_path}: {exc}") from exc
    testing = _test_request(args, project)
    asset_batch = None
    if getattr(args, "batch_receipt", None):
        if testing:
            raise StageContextError("候选试跑不绑定正式图片批次回执")
        asset_batch = _asset_batch_handoff(project, args, args.skill, request_path=request_path,
                                          request_snapshot=request_snapshot)
    kickoff = None if testing else load_kickoff(project)
    request = bind_stage_request(request, kickoff)
    card = _handoff_path(project)
    routed = None
    if not testing and card.is_file():
        card_text = card.read_text(encoding="utf-8")
        if f"- 目标 Skill：`{args.skill}`\n" in card_text:
            routed = _routed_handoff_scope(card_text)
    product_pack = resolve_product_pack(kickoff["product"]) if kickoff and args.skill in {
        "creative-director", "storyboard-planner", "asset-image-assistant", "storyboard-director",
    } else None
    image_targets = _image_batch_targets(args.skill, request, routed=bool(routed and not asset_batch))
    if not testing:
        _check_preparation_route(project, args.skill, request, routed,
                                 repair=bool(getattr(args, "change_id", None)), asset_batch=bool(asset_batch))
    try:
        output_requirements = check_output_plan(project, request, **({"candidate_test_mode": True} if testing else {})) if args.skill == "asset-image-assistant" else {}
    except ValueError as exc:
        raise StageContextError(str(exc)) from exc
    candidate_rule_ids = set(getattr(args, "candidate_rule", None) or [])
    candidate_source_id = getattr(args, "candidate_source_id", None)
    candidate_source_archive = getattr(args, "candidate_source_snapshot", None)
    if candidate_rule_ids or candidate_source_id or candidate_source_archive:
        if not testing:
            raise StageContextError("动态候选试用只允许在 candidate-test 项目中运行。")
        if not (candidate_rule_ids and candidate_source_id and candidate_source_archive):
            raise StageContextError("候选试用必须同时指定 --candidate-source-id、--candidate-source-snapshot 与至少一个 --candidate-rule。")
    delivery_contract = _delivery_contract(request)
    if testing and (delivery_contract["delivery_object"] == "submission" or request.get("generation_authorized")):
        raise StageContextError("候选测试只允许 plan/prompt，不允许生成提交")
    input_versions = _normalize_source_alias({
        key: value for key, value in request.items()
        if key.startswith("source_") or key in {"sources", "inputs", "material_output_ids", "supplement_request_paths"}
    })
    if routed and routed.get("artifact_ids"):
        ids = input_versions.get("source_artifact_ids", [])
        if not isinstance(ids, list):
            raise StageContextError("请求的 source_artifact_ids 必须是数组")
        input_versions["source_artifact_ids"] = list(dict.fromkeys([*routed["artifact_ids"], *ids]))
    if product_pack:
        input_versions["product_reference_files"] = [product_pack["manifest_path"],
                                                     *(item["path"] for item in product_pack["references"])]
    raw_state = _state(project)
    summary = _state_summary(raw_state)
    has_local_selection = args.skill == "asset-image-assistant" or any(request.get(key) for key in (
        "source_artifact_id", "source_artifact_ids", "source_shot_ids", "image_targets", "material_output_ids",
        "supplement_request_paths",
    )) or bool(getattr(args, "change_id", None) or (routed or {}).get("artifact_ids"))
    snapshot = _input_snapshot(project, input_versions, raw_state,
                               prefer_declared_artifacts=has_local_selection,
                               source_roles=_task_source_roles(args.skill, request))
    startup_state = _state_reading_view(summary)
    if snapshot.get("artifact_scope") == "declared":
        startup_state.update(current_artifacts=snapshot["selected_artifact_ids"],
                             user_confirmations=snapshot["authority"]["user_confirmations"],
                             accepted_issues=snapshot["authority"]["accepted_issues"])
    if args.skill == 'storyboard-director' and _redframe_image_batch(args.skill, request) and not testing:
        _validate_redframe_supplement_targets(project, request, snapshot)
    handoff_text = _current_handoff_text(project, args.skill, snapshot)
    handoff_scope = _handoff_scope(handoff_text)
    handoff_batch_bindings = _asset_batch_binding_entries(handoff_text)
    if handoff_batch_bindings:
        try:
            (binding,) = handoff_batch_bindings
            prior_batch = json.loads(binding)
            request_batch = _asset_batch_handoff(project, args, args.skill, request_path=request_path,
                request_snapshot=request_snapshot, receipt_ref=prior_batch["receipt"])
            if prior_batch != request_batch or (asset_batch is not None and prior_batch != asset_batch):
                raise ValueError("请求、目标或回执路径不一致")
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise StageContextError(f"本轮准备与已有图片批次交接不一致：{exc}") from exc
    revision = _revision_summary(
        project,
        getattr(args, "change_id", None),
        getattr(args, "target_logical_id", None),
    )
    policy = load_policy(ROOT)
    vault = _vault_root(args.vault)
    optional_experience = _optional_experience_paths(vault, profile)
    try:
        methods = resolve_method_documents(
            vault, profile, {"skill": args.skill, "request": request, "project_state": summary},
            test_proposed_method_ids=test_method_ids,
        )
    except MethodDocumentError as exc:
        raise StageContextError(str(exc)) from exc
    blocks = build_knowledge_index(vault, policy) if vault.is_dir() else []
    candidate_source_summary = None
    if candidate_source_id:
        try:
            candidate_trial_blocks = load_candidate_snapshot_rules(
                profile, Path(candidate_source_archive).expanduser().resolve(),
                candidate_source_id, candidate_rule_ids,
            )
            # The pinned candidate-test snapshot is the only copy of these
            # explicitly trialed IDs in this task. Do not let an enabled
            # local Vault copy of the same ID become formal selection input.
            blocks = [block for block in blocks if block.id not in candidate_rule_ids]
            blocks.extend(candidate_trial_blocks)
            candidate_source_summary = inspect_candidate_snapshot(
                profile, Path(candidate_source_archive).expanduser().resolve(), candidate_source_id,
            )
        except CandidateSnapshotError as exc:
            raise StageContextError(str(exc)) from exc
    result = select_knowledge(
        blocks, policy, args.skill, request, summary,
        _tombstone_fingerprints(vault, policy), profile=profile,
        candidate_trial_ids=candidate_rule_ids,
    )
    if any(item.get("reason") == "blocking_conflict" for item in result.blocking_conflicts):
        raise StageContextError(f"confirmed 知识冲突：{json.dumps(result.blocking_conflicts, ensure_ascii=False)}")
    objective = (args.objective or "").strip()
    if not objective:
        raise StageContextError("prepare 必须提供非空 --objective")
    objective = _coordinator_objective(args.skill, _CONTEXT_ID.get(), handoff_scope, testing) or objective
    bundle = render_knowledge_bundle(args.skill, result)
    candidate_index = render_knowledge_candidates(result)
    trial_bundle = render_candidate_trial_bundle(args.skill, result)
    method_lines = ["## 必读专业方法", ""]
    if methods.required:
        method_lines.append("- 以下必读内容已写入 `method_reading_packet.md`；生产者必须读完整个必读包后登记读取证据。")
        method_lines.extend(
            f"- `{item.id}`：`{item.vault_path}`；版本 `{item.version}`；SHA-256 `{item.checksum}`；适用条件：{item.applies_when}"
            for item in methods.required
        )
    else:
        not_applicable = [item for item in methods.pending if item["reason"] == "applies_when_not_met"]
        if not_applicable:
            method_lines.append("- 当前无适用的必读专业方法。")
            declared = {item.get("id"): item.get("applies_when", "") for item in profile.get("professional_methods", []) if isinstance(item, dict)}
            method_lines.extend(
                f"- `{item['id']}`：条件不满足，跳过；已知适用条件：{declared.get(item['id']) or '见 profile requires'}"
                for item in not_applicable
            )
        else:
            method_lines.append("- 无已获用户确认的必读专业方法。")
    unconfirmed = [item["id"] for item in methods.pending if item["reason"] == "awaiting_user_confirmation"]
    if unconfirmed:
        method_lines.append(f"- 待用户确认的候选方法：{', '.join(unconfirmed)}（不加载、不计入必读）。")
    if test_method_ids:
        method_lines.append("- 本启动包含显式测试候选方法；只能验证读取链路，`ensure-prepared` 会拒绝其用于生产。")
    if optional_experience:
        method_lines.extend([f"- 可选经验（根：`{vault}`；不自动加载；执行者按当前任务自行决定是否完整阅读，缺失或不适用不阻断）：", *(f"  - `{item}`" if exists else f"  - `{item}`（本轮 Vault 中缺失，跳过）" for item, exists in optional_experience)])
    method_lines.append("")
    revision_lines: list[str] = []
    if revision:
        revision_lines = [
            "## 前序修订约束", "", "```json",
            json.dumps(revision, ensure_ascii=False, indent=2), "```", "",
            "仅修改目标候选及其直接派生物；不得覆盖已确认版本、派发下一 Skill 或自行批准。", "",
        ]
    review_profile_path, review_profile = _load_review_profile(args.skill)
    request_checks = request.get("review_checks") or []
    if not isinstance(request_checks, list) or any(not isinstance(item, str) or not item.strip() for item in request_checks):
        raise StageContextError("request.review_checks 必须是非空文字检查项数组。")
    manual_checks = [{"id": f"MANUAL-{index:03d}", "text": text.strip()} for index, text in enumerate(request_checks, start=1)]
    methods_records = []
    for item in methods.required:
        record = {"id": item.id, "path": item.vault_path, "version": item.version,
                  "sha256": item.checksum, "promotion_basis": item.promotion_basis}
        if item.template_path:
            record.update(template_path=item.template_path, template_sha256=item.template_checksum)
        if item.reading_scope:
            record["reading_scope"] = item.reading_scope
        methods_records.append(record)
    base_packet = render_method_packet(methods.required) if methods.required else "# 本轮必读专业方法快照\n"
    trial_record = None
    if result.trial_candidates:
        trial_record = {
            "source_id": candidate_source_id,
            "archive_path": str(Path(candidate_source_archive).expanduser().resolve()),
            "archive_sha256": candidate_source_summary["archive_sha256"],
            "rule_ids": [item.id for item in result.trial_candidates],
            "rules": [_candidate_record(item) for item in result.trial_candidates],
            "packet_sha256": hashlib.sha256(trial_bundle.encode("utf-8")).hexdigest(),
        }
    reading_manifest = {"schema_version": "1.0", "skill": args.skill, "project_path": str(project), "vault_root": str(vault), **_reading_identity(args), "methods": methods_records, "method_packet_sha256": hashlib.sha256(base_packet.encode("utf-8")).hexdigest(), "method_packet_bytes": len(base_packet.encode("utf-8")), "test_only_proposed_method_ids": sorted(test_method_ids), "selected": [_candidate_record(item) for item in result.selected], "candidate_catalog": [_candidate_record(item) for item in result.candidates], "candidate_reasons": result.candidate_reasons, "applicable_confirmed": [_candidate_record(item) for item in result.applicable_confirmed], "candidate_trial": trial_record}
    local_documents = _local_standard_documents(skill_dir, request)
    rule_documents = [path for path in local_documents
                      if path.is_relative_to((ROOT / "docs/rules").resolve())]
    character_inputs = {}
    if args.skill == "asset-image-assistant" and image_targets is not None:
        kinds = request.get("image_output_kinds")
        template_kinds = None
        if isinstance(kinds, dict) and all(isinstance(kinds.get(target), str) and kinds[target].strip()
                                           for target in image_targets):
            template_kinds = character_template_kinds(kinds[target] for target in image_targets)
        if template_kinds is None or template_kinds:
            character_inputs = {"character_layout_references": character_layout_references(template_kinds),
                                "character_prompt_templates": character_prompt_templates(template_kinds)}
    task_basis = {
        "schema_version": "1.0",
        "skill": args.skill,
        "objective": objective,
        "deliverables": request.get("deliverables") or [objective],
        "non_goals": request.get("non_goals") or [],
        "input_versions": input_versions,
        "input_snapshot": snapshot,
        "handoff_scope": handoff_scope,
        "task_request": _semantic_request(request, args.skill),
        **({"character_output_requirements": output_requirements} if output_requirements else {}),
        **character_inputs,
        "request_path": str(Path(args.request_json).resolve()) if args.request_json else None,
        "knowledge_profile": fingerprint(profile_path),
        "scope": {key: request[key] for key in ("task_kind", "scope", "source_shot_ids", "generation_authorized", "model_selected", "image_targets") if key in request},
        "delivery_contract": delivery_contract,
        "standards": {
            "root_rules": str(ROOT / "PROJECT_RULES.md"),
            "root_rules_sha256": digest(ROOT / "PROJECT_RULES.md"),
            "skill_entry": str(skill_dir / "SKILL.md"),
            "professional_methods": [{"id": item.id, "path": item.vault_path, "version": item.version, "sha256": item.checksum, "promotion_basis": item.promotion_basis, **({"template_path": item.template_path, "template_sha256": item.template_checksum} if item.template_path else {})} for item in methods.required],
            "selected_knowledge": [_candidate_record(item) for item in result.selected],
            "applicable_confirmed": [_candidate_record(item) for item in result.applicable_confirmed],
            "review_profile": {"path": str(review_profile_path), "sha256": hashlib.sha256(review_profile_path.read_bytes()).hexdigest(), "schema_version": review_profile["schema_version"]},
            "local_documents": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in local_documents],
        },
        "review_acceptance": {"delivery_object": delivery_contract["delivery_object"], "delivery_contract": delivery_contract, "production": {"required_checks": _applicable_checks(review_profile["production_artifact_checks"], delivery_contract), "manual_checks": manual_checks}, "skill_contract": {"required_checks": review_profile["skill_contract_checks"], "manual_checks": manual_checks}},
        "reading_manifest": reading_manifest,
        "candidate_trial": trial_record,
        "revision": revision,
    }
    if _CONTEXT_ID.get() is not None:
        task_basis["context_id"] = reading_manifest["context_id"] = _CONTEXT_ID.get()
    if testing:
        task_basis.update(test_metadata())
    task_basis['reading_reuse_key'] = _reading_reuse_key(task_basis)
    content = "\n".join([
        "# 阶段启动包", "",
        f"- Skill：`{args.skill}`",
        f"- 目标：{objective}",
        *([f"- 本轮开工决定：`{project / REQUEST_PATH}` 的 `kickoff` 按本阶段适用范围纳入任务依据。"] if kickoff else []),
        *([f"- 复刻线按冻结的 `{ROOT / RECREATION_METHOD}` 执行，编导确认和本阶段采用依旧沿统一入口。"]
          if ROOT / RECREATION_METHOD in local_documents else []),
        *([f"- 产品参考包：`{product_pack['manifest_path']}`，用 `kickoff --project` 读取本轮用途的准确文件、职责与缺口；逐镜选用。"] if product_pack else []),
        *(["- execution_mode: candidate_test；review_status: unreviewed；production_blocked: true。仅候选文本/结构试跑，不生成、不发布、不正式采用。"] if testing else []),
        f"- 本轮唯一任务依据：`{_task_basis_path(project)}`（机器完整记录；通过 ensure-prepared --show-task-basis 读取目标、输入与冻结验收项，不整份打印后台目录）",
        f"- 交付意图：`{json.dumps(delivery_contract, ensure_ascii=False)}`；按本轮交付对象及适用检查项审核。",
        f"- 根规则：`{ROOT / 'PROJECT_RULES.md'}`",
        f"- Skill 入口：`{skill_dir / 'SKILL.md'}`",
        "- 上下文较长或压缩恢复后：重读根规则与当前 Skill；以下细则原文已不可用时重新读取。冻结与阅读记录不证明内容仍在上下文。",
        *(["- 本轮适用细则（准确版本已冻结在 task_basis.json）：",
           *[f"  - `{path}`" for path in rule_documents]] if rule_documents else []),
        "- 执行分工：遵守根规则的干净上下文启动规则；新一轮编导等创作生产或实质重跑由全新专属生产 agent 执行，不继承长主会话；本轮执行者读取、准备、产出并自查。其他任务按根规则执行。",
        "- 上下文边界：交接当前有效规则、原始素材、本轮约束与已确认决定及依据、现行版本、未决事项、范围和停止点；不得以摘要替代原片。已在本轮专属 agent 内的逐段推进和局部返修可继续；目标、来源或基线重大变化时新开，不按修订号机械重开。",
        f"- 等待及接收：执行前实读 `{ROOT / 'docs/rules/execution.md'}#等待与工具回传` 的宿主参数；沿原任务续等，完成后只交本轮身份、结论、差异及证据路径。原图由执行者实看，协调方按争议定向取图，不整份回传 JSON、日志或图组。",
        *([f"- 图片批次：`{_CONTEXT_ID.get()}`，本批冻结 {len(image_targets)} 个 image_targets；阶段负责人统筹整体方向、跨批连续性和正式交接，本批执行者读取真实参考、完成获准图片及实看自查。委派时只回传准确路径与检查摘要。批次目录不构成已生成、已检查、人工确认或正式采用。"] if image_targets is not None and _CONTEXT_ID.get() is not None else []),
        *([f"- 图片目标：本阶段请求冻结 {len(image_targets)} 个 image_targets；主对话直做批次沿用本阶段 task_basis，不另建批次目录，但仍须写回现有回执。"] if image_targets is not None and _CONTEXT_ID.get() is None else []),
        *([f"- 红帧生图：读取 `{ROOT / _REDFRAME_IMAGE_STANDARDS[0]}#生图` 及同页连接入口。由主对话直接操作，先上传素材卡并当场归位，再接线和核正文；配套上传与排版规则同批冻结。"]
          if _redframe_image_batch(args.skill, request) else []),
        *(["- 导演写稿和返修：先实看影响拍法的原片连续窗，核关键对白指向、起幅、镜内轨迹、落幅与实际切出后的反应；然后才核逐镜字段和下游影响。沿现有自查记录写准确时间窗和镜号。"]
          if args.skill == 'storyboard-planner' else []),
        *(_asset_batch_delivery_lines(asset_batch) if asset_batch and not handoff_batch_bindings else []),
        *(["- 人物生图按输出类型读取 task_basis.json 的 character_prompt_templates 与 character_layout_references，使用对应模板正文，原参考在前、灰模在最后。按实际槽位绑定原生引用。"]
          if task_basis.get("character_layout_references") else []),
        "- prepare 只生成文件，不清空宿主对话。普通阶段按 skills/_shared/knowledge-access.md#普通阶段准备与选知 读取。仅在适用计量时看 docs/development/token-budget.md#普通准备的计量检查点。",
        _recovery_check_text(), "",
        "## 本轮范围", "",
        "- 交付对象、非目标、输入版本、范围字段和适用标准仅维护在上列 `task_basis.json`；本启动包不复制第二份范围记录。", "",
        "## 当前项目事实", "", "```json",
        json.dumps(startup_state, ensure_ascii=False, indent=2), "```", "",
        *( ["## 上一阶段交接事实", "", handoff_text.rstrip(), ""] if handoff_text else [] ),
        *revision_lines,
        *method_lines,
        bundle.rstrip(), "",
        *( ["## 按需知识候选", "", f"- 共 {len(result.candidates)} 条；完整可读目录：`knowledge_candidates.md`。仅按其中精确路径和锚点读取。", ""] if result.candidates else [] ),
        *( ["## 隔离候选知识试用", "", "- 仅 candidate-test 临时阅读，不进入正式选择集、确认或晋升；正文见 `candidate_trial_packet.md`。", ""] if result.trial_candidates else [] ),
        "## 选择记录", "",
        f"- 已选知识块：{', '.join(item.id for item in result.selected) or '无'}",
        f"- 按需知识候选：{len(result.candidates)}",
        f"- 延后知识块：{len(result.deferred)}",
        f"- 知识正文字符：{result.total_chars}", "",
    ])
    output = _context_dir(project, "stage_startup.md")
    report = validate_text("startup", output, content)
    _save_reading_cache(project)
    write_text_if_changed(output, content)
    write_text_if_changed(_task_basis_path(project), json.dumps(task_basis, ensure_ascii=False, indent=2) + "\n")
    if methods.required or result.candidates:
        current = _evidence_current(project, basis=task_basis)
        restored = current is None and _restore_reading_cache(project, args.skill, task_basis['reading_reuse_key'])
        report['stage_reading_restored'] = restored
        if current is None and not restored:
            write_text_if_changed(_context_dir(project, "method_reading_packet.md"), base_packet)
            stale = _context_dir(project, "method_reading_evidence.json")
            if stale.is_file(): stale.unlink()
        index_path = _context_dir(project, "knowledge_candidates.md")
        if result.candidates: write_text_if_changed(index_path, candidate_index)
        elif index_path.is_file(): index_path.unlink()
    else:
        for name in ("method_reading_packet.md", "method_reading_evidence.json", "knowledge_candidates.md"):
            stale = _context_dir(project, name)
            if stale.is_file(): stale.unlink()
    for name in ("method_reading_manifest.json", "candidate_reading_manifest.json", "candidate_reading_packet.md", "candidate_reading_evidence.json"):
        stale = _context_dir(project, name)
        if stale.is_file(): stale.unlink()
    trial_path = _context_dir(project, "candidate_trial_packet.md")
    if trial_record:
        write_text_if_changed(trial_path, trial_bundle)
    elif trial_path.is_file():
        trial_path.unlink()
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    report["required_method_count"] = len(methods.required)
    report["pending_method_count"] = len(methods.pending)
    report["optional_experience_count"] = len(optional_experience)
    report["method_reading_bytes"] = len(base_packet.encode("utf-8"))
    if testing:
        report.update(test_metadata())
        if trial_record:
            report["candidate_trial"] = {"rule_ids": trial_record["rule_ids"], "production_blocked": True}
    if usage is not None:
        report["run_usage"] = usage
    if asset_batch:
        report["asset_batch"] = asset_batch
    return report


def _asset_batch_handoff(project, args, target_skill, *, request_path=None, request_snapshot=None, receipt_ref=None):
    request_ref = getattr(args, "request_json", None)
    receipt_ref = receipt_ref or getattr(args, "batch_receipt", None)
    if not request_ref and not receipt_ref:
        return None
    if (target_skill != "asset-image-assistant" or _CONTEXT_ID.get() is None
            or not request_ref or not receipt_ref or getattr(args, "candidate_test", False)):
        raise StageContextError("图片批次交接须指定资产 Skill、--context-id、--request-json 和 --batch-receipt")
    request = request_path or project_path(project, request_ref)
    receipt = project_path(project, receipt_ref)
    if (request == receipt or not all(path.is_relative_to(project) and path.suffix.lower() == ".json"
                                      for path in (request, receipt))):
        raise StageContextError("本批请求和回执须为项目内不同的 JSON 文件")
    request_payload, request_sha256 = request_snapshot if request_snapshot is not None else _request_snapshot(request)
    return {"context_id": _CONTEXT_ID.get(), "request": request.relative_to(project).as_posix(),
            "request_sha256": request_sha256, "receipt": receipt.relative_to(project).as_posix(),
            "targets": _image_batch_targets(target_skill, request_payload)}


_ASSET_BATCH_BINDING_PREFIX = "- 图片批次依据 `"


def _asset_batch_binding_line(batch):
    return _ASSET_BATCH_BINDING_PREFIX + json.dumps(batch, ensure_ascii=False, separators=(",", ":")) + "`"


def _asset_batch_binding_entries(text):
    return {line.removeprefix(_ASSET_BATCH_BINDING_PREFIX).removesuffix("`") for line in text.splitlines()
            if line.startswith(_ASSET_BATCH_BINDING_PREFIX) and line.endswith("`")}


def _asset_batch_delivery_lines(batch):
    return [
        "", "## 本批交回", "", _asset_batch_binding_line(batch),
        "- 本批执行者跟到准确成图落盘和实看自查，或出现需交回的真实阻塞。提示词和提交清单是中间结果。",
        "- 完成时更新上述现有实看回执，顶层沿用本卡的 `context_id` 和 `request_sha256`。每项实看写准确的 `target`、`candidate_id`、`observer`、`visual_status: passed|failed`、`observations`；通过项另写 `path` 和 `sha256`。",
        "- 回执写齐后再通知协调方。完成通知只回批次、已完成目标、真实阻塞和回执路径；途中只通知可接续的新结果或需决策的阻塞。",
        "- 无成图可实看且确实需交回的阻塞，沿原回执 `items` 写准确 `target`、`observer`、`status: blocked` 和简短 `blocking_issue`。",
        "- 主对话直接制作时核对现有回执。委派批次由协调方按 `docs/rules/execution.md#等待与工具回传` 续等。超时不叠加 `read_thread`、`list_agents`、目录/日志查询或提前调用 `--receive-batch`；沿原等待渠道和游标续等；收到需核验的可接续新结果时只核受影响项。超过本轮已约定总等待期限仍无终态时，只读回查原任务一次，不重新提交。收到终态后用 `handoff --receive-batch` 验收准确回执一次，缺项交原执行者补核。",
        "- 接收时保留返回的逐项 `issue`，据此修正原回执；原回执和候选未变时沿用这次结果，等执行者补齐后再验。",
        "- 回执只证明本批候选自查，网站或明确授权的对话确认继续走原入口。", "",
    ]


def _receive_asset_batch(project, args, target_skill, from_skill):
    context_id = _CONTEXT_ID.get()
    if (not context_id or target_skill != "asset-image-assistant" or from_skill
            or args.candidate_test or args.request_json or args.batch_receipt):
        raise StageContextError("接收实看回执须指定资产 Skill 和 --context-id，路径沿用本批交接卡")
    cards = (_handoff_path(project), _context_dir(project, "stage_startup.md"))
    bindings = set().union(*(_asset_batch_binding_entries(card.read_text(encoding="utf-8"))
                             for card in cards if card.is_file()))
    if len(bindings) != 1:
        raise StageContextError("本批交接卡缺少图片批次依据，或卡片之间的批次依据冲突")
    try:
        batch = json.loads(bindings.pop())
        if batch["context_id"] != context_id:
            raise ValueError("批次身份不一致")
        request = project_path(project, batch["request"])
        if not request.is_relative_to(project):
            raise ValueError("原请求路径越出项目")
        request_payload, request_sha256 = _request_snapshot(request)
        if request_sha256 != batch["request_sha256"]:
            raise ValueError("原请求内容已改变")
        targets = _image_batch_targets(target_skill, request_payload)
        if targets != batch["targets"]:
            raise ValueError("本批约定目标与准确请求不一致")
        receipt = project_path(project, batch["receipt"])
        result = summarize_asset_batch_receipt(project, receipt, targets,
                                              binding={"context_id": context_id, "request_sha256": request_sha256})
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
        raise StageContextError(f"无法按本批交接卡核对实看回执：{exc}") from exc
    return {"kind": "asset-batch-result", "context_id": context_id, **result}


def _route_existing_project(args, project):
    """Recalculate the next dispatch from committed business evidence on every run."""
    if _test_request(args, project):
        raise StageContextError("隔离候选交接须指定 --to-skill")
    with file_lock(project / PROJECT_LOCK_RELATIVE):
        state = _state(project)
        manifest = load_manifest(project)
        flow = resolve_production_handoff(project, state=state, manifest=manifest)
        next_actions = []
        file_digest = cache_file_digests(digest)
        checked_dependencies = set()
        for step in flow["steps"]:
            if step["status"] != "ready":
                continue
            next_action = dict(step)
            target_skill = step.get("skill")
            if target_skill:
                context_id = "handoff-" + step["action"]
                token = _CONTEXT_ID.set(context_id)
                try:
                    ids = step.get("artifact_ids", [])
                    inputs = []
                    if ids:
                        _, inputs = _confirmed_project_inputs(
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
                    _CONTEXT_ID.reset(token)
            next_actions.append(next_action)
        return {**flow, "ok": True, "mode": "production", "project": str(project), "next_actions": next_actions}


@_context_scoped
def handoff(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise StageContextError(f"项目目录不存在：{project}")
    target_skill = str(getattr(args, "to_skill", None) or "").strip()
    from_skill = str(getattr(args, "from_skill", None) or "").strip()
    authorization_id = str(getattr(args, "authorization_id", None) or "").strip()
    if not target_skill:
        if from_skill or authorization_id or any(getattr(args, option, None) for option in (
                "receive_batch", "request_json", "batch_receipt", "actor_id", "task_id", "context_id", "objective", "stop_at")):
            raise StageContextError("程序交接只接收项目路径；已有明确目标的任务或批次绑定须提供 --to-skill")
        return _route_existing_project(args, project)
    if bool(from_skill) != bool(authorization_id):
        raise StageContextError("协调方须同时提供 --from-skill 与已有的 --authorization-id")
    if from_skill and getattr(args, "candidate_test", False):
        raise StageContextError("隔离候选交接不得登记生产阶段")
    if getattr(args, "receive_batch", False):
        return _receive_asset_batch(project, args, target_skill, from_skill)
    asset_batch = _asset_batch_handoff(project, args, target_skill)
    default_flow = (target_skill in _ROUTED_SKILLS and not getattr(args, "candidate_test", False)
                    and asset_batch is None and _uses_routed_handoff(project))
    if default_flow:
        with file_lock(project / PROJECT_LOCK_RELATIVE):
            try:
                return _bind_routed_handoff(args, project, target_skill, authorization_id)
            except StageContextError:
                raise
            except ValueError as exc:
                raise StageContextError(str(exc)) from exc
    # Declared transitions share the project writer lock. Inspect revision facts
    # before any write or recovery can change them.
    can_register_transition = from_skill or (
        target_skill == "storyboard-planner" and not getattr(args, "candidate_test", False)
    )
    with file_lock(project / PROJECT_LOCK_RELATIVE) if can_register_transition else nullcontext():
        return _write_handoff(args, project, target_skill, from_skill, authorization_id, asset_batch=asset_batch)


def _bind_routed_handoff(args, project, target_skill, authorization_id):
    """Bind a runner to a legal route; an explicit Skill never authorizes a transition."""
    _test_request(args, project)
    state = _state(project)
    if authorization_id:
        require_workflow_authorization(project, authorization_id, target_skill, state=state)
    actions = _ROUTED_SKILLS[target_skill]
    if len(actions) == 1:
        step = require_production_handoff(project, actions[0], state=state)
    else:
        action = (_CONTEXT_ID.get() or "").removeprefix("handoff-")
        requested = {action} if action in actions else set(actions)
        steps = resolve_production_handoff(project, state=state, actions=requested)["steps"]
        ready = [step for step in steps if step["action"] in requested and step["status"] == "ready"]
        if not ready:
            reason = "；".join(step["reason"] for step in steps if step["status"] == "waiting")
            raise StageContextError("视频或画布交接当前未就绪：" + reason)
        if len(ready) > 1:
            raise StageContextError("视频写稿和画布写入均有就绪范围；沿程序交接返回的对应 context_id 绑定")
        step = ready[0]
    from_skill = getattr(args, "from_skill", None)
    if from_skill and step.get("from_skill") != from_skill:
        raise StageContextError("交接来源不符合程序确定的阶段")
    ids = step.get("artifact_ids", [])
    inputs = _confirmed_project_inputs(project, state, artifact_ids=ids)[1] if ids else []
    return _write_handoff_card(args, project, target_skill,
                               step.get("from_skill") or state.get("current_stage") or "kickoff", inputs, routed=step)


def _write_handoff(args, project, target_skill, from_skill, authorization_id, *, asset_batch=None):
    testing = _test_request(args, project)
    register_transition = False
    if testing:
        validate_test_stage(target_skill)
        state, inputs = _selected_inputs(project, _state(project))
        if state.get("next_stage") and state["next_stage"] != target_skill:
            raise StageContextError("目标 Skill 与项目下一阶段不一致")
    elif from_skill:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", from_skill) or not (ROOT / "skills" / from_skill / "SKILL.md").is_file():
            raise StageContextError(f"交接来源 Skill 不存在：{from_skill}")
        state = _state(project)
        require_workflow_authorization(project, authorization_id, target_skill, state=state)
        if state.get("project_status") != "active":
            raise StageContextError("项目不处于可登记正式交接的活动状态")
        current, expected = state["current_stage"], state["next_stage"]
        if expected != from_skill and (current not in ("", from_skill) or expected not in (None, "", target_skill)):
            raise StageContextError("协调方提供的流转与已登记阶段冲突，请先核对当前阶段及已允许的下一步")
        active_change, _, blockers = inspect_revision_status(project)
        if blockers := _legacy_revision_blockers(active_change, blockers):
            raise StageContextError("正式交接被修订状态阻止：" + "；".join(blockers))
        state, inputs = _confirmed_project_inputs(project, state)
        register_transition = (current, expected) != (from_skill, target_skill)
    else:
        state = _state(project)
        if (target_skill == "storyboard-planner" and state["project_status"] == "active"
                and state["current_stage"] in ("", "creative-director") and state["next_stage"] in (None, "")):
            inputs = _first_director_handoff(project, state)
            from_skill = "creative-director"
            register_transition = True
        else:
            state, inputs = _confirmed_handoff_inputs(project, target_skill, state=state)
    current_stage = from_skill or str(state.get("current_stage") or "")
    if not current_stage:
        raise StageContextError("项目当前阶段为空，不能生成交接卡")
    report = _write_handoff_card(args, project, target_skill, current_stage, inputs,
                                 testing=testing, asset_batch=asset_batch)
    if register_transition:
        state.update(current_stage=from_skill, next_stage=target_skill)
        write_project_state(project, state)
    return report


def _write_handoff_card(args, project, target_skill, current_stage, inputs, *, testing=False,
                        asset_batch=None, routed=None):
    scope_fields = (*_ROUTED_HANDOFF_SCOPE, "workflow")
    lines = [
        "# 下一阶段交接卡", "",
        f"- 来源阶段：`{current_stage}`",
        f"- 目标 Skill：`{target_skill}`",
        *([f"- 制作路线：复刻线。实读 `{ROOT / routed['workflow_entry']}`，沿本阶段现有交接和采用入口。"]
          if routed and routed.get("workflow") == RECREATION_WORKFLOW else []),
        f"- 本轮目标：{getattr(args, 'objective', None) or (routed or {}).get('reason') or _HANDOFF_DEFAULT_OBJECTIVE}",
        f"- 本轮停止点：{getattr(args, 'stop_at', None) or '完成本职自查并交回回执，遇缺来源、授权或预算停止相关动作'}",
        ("- execution_mode: candidate_test；review_status: unreviewed；production_blocked: true。只试写，不生成、不发布、不采用。" if testing else "- 交接状态：上游当前输入的有效采用依据与依赖已核对。"),
        *(["- 程序交接范围：" + json.dumps({key: routed[key] for key in scope_fields if key in routed},
                                              ensure_ascii=False, separators=(",", ":"))] if routed else []), "",
        ("## 本轮未确认候选输入" if testing else "## 当前有效上游产物"), "",
        "- 语义自查：本卡未读取结论。沿用当前修订有效的检查结论，核对未决项及本阶段新增用途。独立审核仅在用户明确要求时执行。", "",
    ]
    relative_paths = {}
    for item in inputs:
        source = project_path(project, item['path_or_url'])
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
            f"  - sha256：`{digest(source) if testing else item['checksum']}`",
        ])
    startup_requirement = (
        "- `voiceover-to-video-script` 直接按其 Skill 的原始资料读取例外建立本段输入范围；不生成或校验 stage_startup.md。"
        if target_skill == "voiceover-to-video-script" else
        "- 按根规则为已授权的下一 Skill 建立执行者。统一入口在网站通过、编导整稿对话确认正式采用或有效自主采用后，按本轮范围继续；兼容人工流程沿其继续规则。创作新轮由专属生产 agent 准备并校验 stage_startup.md。"
    )
    task_context = _CONTEXT_ID.get()
    lines.extend([
        "", "## 本轮依据与停止点", "",
        f"- 项目路径：`{project}`。相对产物路径从这里解析。仓库入口：`{ROOT / 'AGENTS.md'}` 与目标 Skill。",
        "- 原始证据：从以上准确产物的 source_artifacts / 来源引用按需实读，不用交接摘要替代。",
        f"- 决定及授权：实读 `{project / REQUEST_PATH}` 的本轮需求、kickoff 和 `{project / '00_项目管理/project_state.json'}` 的确认及 workflow_authorizations；缺文件或范围保留未决。",
        "- 未决项：核以上当前产物的未决字段、现有 review_report 与 accepted_issues，已接受问题只覆盖原记录的准确范围。",
        "- 停止点：完成本职目标和自查后交回准确版本、检查结果、未完成项与证据路径。",
        *([f"- 视频默认交已保存并回读的草稿。若本轮包含已明确授权的生成，按 `{ROOT / 'skills/voiceover-to-video-script/references/seedance-delivery-package.md'}#已授权生成的取证` 接续，复用有效授权。"]
          if not testing and target_skill == "voiceover-to-video-script" else []),
        *(["- 草稿曾写入或写入结果不明时，先沿原计划和准确画布只读核对原节点；找到原草稿就接续其回读，确认该段未保存才新建。"]
          if routed and routed["action"] == "canvas" else []),
        f"- 工具报错：沿 `{ROOT / 'docs/rules/execution.md'}#工具报错与原处续作`，在现有任务记录交回原操作、失败位置、最后可信结果和待恢复步骤。",
        f"- 等待及接收：执行前实读 `{ROOT / 'docs/rules/execution.md'}#等待与工具回传` 的宿主参数；沿原任务续等，完成后只交本轮身份、结论、差异及证据路径。原图由执行者实看，协调方按争议定向取图，不整份回传 JSON、日志或图组。",
        "", "## 接收约束", "",
        *([f"- 从 `{project / REQUEST_PATH}` 直接读取本轮 `kickoff`；已有决定随项目沿用，缺项只影响依赖它的动作。"] if not testing and load_kickoff(project) else []),
        ("- 仅按独立测试授权继续候选试写；保留所有未核对事实和素材阻塞。视频 Skill 仍直接读取，不重建准备包。" if testing else startup_requirement),
        *([f"- 准备、读取登记、产前检查和需要的审核包沿本任务 `--context-id {task_context}`；使用原执行者和任务 ID。"]
          if task_context and target_skill in TASK_HANDOFF_SKILLS else []),
        "- 编导交接须实际读准确正文、核来源和本轮自查结论；关键发现及未核范围复用现有任务记录，程序 ok 不代表叙事充分。",
        "- 创作新轮使用干净的专属执行者，同轮返修可续用。核对仓库、规则和执行环境。用户明确要求时才单独审核。",
        "- 图片沿有效确认输出消费。同用途复用已有观察，版本或用途变化时定向实看。资产人审和下游补图自主采用各用原入口。",
        ("- 只消费以上未确认候选及准确稳定 ID；不得读取旧案例答案，不得写入确认记录。" if testing else "- 只消费以上有效采用产物及其稳定 ID；不得读取旧生产对话或未列入启动包的历史内容。"),
        "- 本交接不等于下一阶段已启动，也不替代下一阶段的输入门禁。", "",
    ])
    if asset_batch:
        lines.extend(_asset_batch_delivery_lines(asset_batch))
    output = _handoff_path(project)
    content = "\n".join(lines)
    if len(content.encode("utf-8")) > LIMITS["handoff"]:
        content = "\n".join(relative_paths.get(line, line) for line in lines)
    report = validate_text("handoff", output, content)
    write_text_if_changed(output, content)
    report["from_stage"] = current_stage
    report["to_skill"] = target_skill
    report["artifact_ids"] = [item["artifact_id"] for item in inputs]
    if asset_batch:
        report["asset_batch"] = asset_batch
    if testing:
        report.update(test_metadata())
    return report


@_context_scoped
def review_pack(args: argparse.Namespace) -> dict[str, Any]:
    _require_preparation_skill(args.skill)
    project = Path(args.project).expanduser().resolve()
    require_production(project)
    review_kind = getattr(args, "review_kind", "production")
    if review_kind not in {"production", "skill_contract"}:
        raise StageContextError(f"未知审核类型：{review_kind}")
    artifacts = [_review_target(project, Path(item), review_kind) for item in args.artifact]
    external_evidence = [Path(item).expanduser().resolve() for item in (getattr(args, "evidence", None) or [])]
    external_evidence = list(dict.fromkeys(external_evidence))
    manifest_path = project / "00_项目管理" / "artifact_manifest.json"
    state_path = project / "00_项目管理" / "project_state.json"
    missing = [str(item) for item in artifacts if not item.is_file()]
    if missing:
        raise StageContextError(f"被审产物不存在：{missing}")
    missing_evidence = [str(item) for item in external_evidence if not item.is_file()]
    if missing_evidence:
        raise StageContextError(f"外部审核证据不存在或不是文件：{missing_evidence}")
    basis_path, task_basis = _load_task_basis(project, args.skill)
    manifest = task_basis.get("reading_manifest")
    if isinstance(manifest, dict) and manifest.get("test_only_proposed_method_ids"):
        raise StageContextError("隔离测试方法不能用于正式审核；请重新 prepare。")
    input_validation = _validate_current_inputs(project, task_basis)
    delivery_contract = task_basis.get("delivery_contract")
    if not isinstance(delivery_contract, dict):
        raise StageContextError(f"任务依据缺少交付意图：{basis_path}；先重新 prepare。")
    usage = _check_run_budget(args)
    profile_path, profile = _load_review_profile(args.skill)
    profile_standard = task_basis["standards"].get("review_profile", {})
    if profile_standard.get("sha256") != hashlib.sha256(profile_path.read_bytes()).hexdigest():
        raise StageContextError("审核 profile 已在 prepare 后变化；先显式更新任务依据，不能用 live profile 重选标准。")
    acceptance = task_basis.get("review_acceptance", {}).get(review_kind)
    if not isinstance(acceptance, dict) or not isinstance(acceptance.get("required_checks"), list) or not isinstance(acceptance.get("manual_checks"), list):
        raise StageContextError("任务依据缺少冻结审核清单；请重新 prepare。")
    required_checks = acceptance["required_checks"]
    manual_texts = [item.strip() for item in (getattr(args, "check", None) or []) if item.strip()]
    manual_checks = acceptance["manual_checks"]
    if manual_texts and manual_texts != [item["text"] for item in manual_checks]:
        raise StageContextError("--check 不能在审核时另选标准；请在 prepare 的 request.review_checks 中冻结后重跑。")
    checks = [*required_checks, *manual_checks]
    revision = _revision_summary(
        project,
        getattr(args, "change_id", None),
        getattr(args, "target_logical_id", None),
    )
    review_mode = getattr(args, "review_mode", "initial")
    if review_mode not in {"initial", "revision"}:
        raise StageContextError(f"未知审核模式：{review_mode}")
    previous = getattr(args, "previous_review", None)
    response = getattr(args, "producer_response", None)
    baselines = getattr(args, "baseline", None) or []
    # Supplying the complete historical archival inputs remains a supported
    # invocation. Partial archival arguments must not silently downgrade.
    formal = bool(getattr(args, "formal_archive", False) or response or baselines)
    delta = None
    if review_mode == "revision":
        if not previous:
            raise StageContextError("revision 必须提供 --previous-review；保留上轮未决发现，不能把遗漏当已解决。")
        try:
            if formal:
                if not response or not baselines:
                    raise ValueError("正式留档复审必须提供 --previous-review、--producer-response 和 --baseline。")
                delta, manual_checks = revision_delta(
                    Path(previous).expanduser().resolve(), [Path(p).expanduser().resolve() for p in baselines],
                    artifacts, _producer_response_value(response), args.skill, required_checks, manual_checks, external_evidence,
                )
            else:
                delta = lightweight_revision(
                    Path(previous).expanduser().resolve(),
                    _read_json(_context_dir(project, "review_scope.json"), default={}), artifacts,
                    args.skill, review_kind, required_checks, manual_checks,
                    getattr(args, "change_summary", None) or [], external_evidence,
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise StageContextError(f"复审输入无效：{exc}") from exc
        checks = [*required_checks, *manual_checks]
    reading = _evidence_current(project, args.skill)
    adopted_knowledge = [{**item, **({"adoption_scope": reading[1]["candidate_scopes"][item["id"]]}
                          if item["id"] in reading[1].get("candidate_scopes", {}) else {})}
                         for item in reading[1].get("adopted_candidates", [])] if reading else []
    scope = {"skill": args.skill, "review_kind": review_kind, "task_basis_path": str(basis_path),
             "task_basis_sha256": hashlib.sha256(basis_path.read_bytes()).hexdigest(),
             "task_basis_identity": _review_identity(task_basis, adopted_knowledge),
             "artifacts": [fingerprint(p) for p in artifacts],
             "direct_evidence": [fingerprint(p) for p in external_evidence]}
    if formal:
        scope.update(input_issue_ids=[i["id"] for i in delta["issue_updates"]] if delta else [],
                     issue_id_high_watermark=delta["issue_id_high_watermark"] if delta else 0)
    elif delta:
        scope["input_findings"] = delta["unresolved_findings"]
    if delta is not None:
        identity_key = "task_basis_identity" if "task_basis_identity" in delta else "task_basis_sha256"
        if delta["review_kind"] != review_kind or delta[identity_key] != scope[identity_key]:
            raise StageContextError("任务依据或审核类型已改变；这不是普通复审，先显式变更任务依据并重新首审。")
    if delta is not None and formal and any(change['after_sha256'] != item['sha256']
                                           for change, item in zip(delta['actual_diff'], scope['artifacts'])):
        raise StageContextError('候选在差异计算与审核范围登记之间发生变化；重新生成审核包，不混用版本。')
    if delta is not None and not formal:
        if delta["current_artifacts"] != scope["artifacts"]:
            raise StageContextError("候选在轻复审打包中变化；重新生成，不混用版本。")
    if delta is not None and delta["current_direct_evidence"] != scope["direct_evidence"]:
        raise StageContextError("直接证据在增量计算与审核范围登记之间变化；重新生成，不混用版本。")
    round_key = hashlib.sha256(json.dumps([scope, delta], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    round_dir = _context_dir(project, "review_rounds", round_key) if formal else _context_dir(project)
    scope_path, delta_path = round_dir / "review_scope.json", round_dir / "review_delta.json"
    review_boundary = (
        "本包仅用于用户明确要求的独立审核；生成文件本身不构成派发授权。默认自查在当前对话完成，不新建审核agent。已获独立审核授权时只读检查，不继承生产对话，不修改产物。"
        if review_mode == "initial"
        else "修订复审：必须回原审核子 agent，用 `followup_task` 交回最新审核包；下游打回上游也回该上游原审核者，换修订号不换审核者；原 agent 无法恢复时报告限制并暂停，不自动另建空白 agent；只读；仅复核修订项、原阻塞项及其直接影响；不修改产物。"
    )
    lines = [
        "# 阶段审核包", "",
        f"- Skill：`{args.skill}`",
        f"- 审核模式：`{review_mode}`",
        f"- 审核边界：{review_boundary}", "",
        "## 审核配置", "",
        f"- Profile：`{profile_path}`",
        f"- Profile 版本：`{profile['schema_version']}`",
        f"- 审核层：`{review_kind}`",
        "- 自动必检项不可由命令行删除；`--check` 只能复述 prepare 已冻结的手动项。", "",
        "## 本轮任务依据", "",
        f"- 唯一依据：`{basis_path}`（实质任务身份：`{scope['task_basis_identity']}`；文件 SHA-256 仅供追溯）",
        f"- 交付对象：{json.dumps(task_basis['deliverables'], ensure_ascii=False)}",
        f"- 非目标：{json.dumps(task_basis['non_goals'], ensure_ascii=False)}",
        f"- 输入版本：{json.dumps(task_basis['input_versions'], ensure_ascii=False)}",
        "- 适用标准、方法全文和版本见该依据及下列明确证据；不得在审核中另加标准。", "",
        "## 当前候选产物", "",
    ]
    lines.extend(f"- `{item}`" for item in artifacts)
    lines.extend(["", "## 本轮版本与冻结标准", "", f"- `{scope_path}`（当前候选、来源与验收身份；{'原样作为 review.json 的 scope' if formal else '仅保留当前范围，不要求复制进发现记录'}）"])
    if delta is not None and formal:
        lines.extend(["", "## 本轮增量", "", f"- `{delta_path}`（仅上轮未决项、逐 ID 修改说明、实算 diff）",
                      f"- 原审核 agent：`{delta['reviewer_id']}`",
                      "- 不读全部上轮 review、历史候选或对话；baseline 仅用于核对删除内容及旧行号，不是全文读取清单。",
                      "- 先读实际 diff、changed_sections、deleted_sections、file_level_changes；旧行号对应 baseline，新行号对应 current。空 changed_sections 不代表没有影响。",
                      "- diff_format=unicode-edits-v1时读取text_edits：removed/added是完整改动，context仅为附近定位，不是完整审查范围。位置按统一换行后的Unicode字符计数，不手工拼稿；程序已验证可还原。沿用同版已有上下文，必要时按before_line/after_line补读受影响段及直接关系；不得因切片短就缩小内容审查。",
                      "- continuity_neighbors 是同文件连续性定位；file_level_impact_review_required 要求判断文件级决定影响了哪些段。cross_file_context_review_required 提醒核对边界的直接关联候选或源证据，不按 artifact 列表顺序猜时间顺序，不全量重读。标记为 false 也不证明没有语义关联。",
                      "- 上轮仍有效的检查覆盖继续有效；下面冻结清单不是要求全量重审。",
                      "- `--evidence` 只能列本轮直接受影响的材料；登记、确认、知识依据有变化时仅显式提供有关片段及源位置。"])
    elif delta is not None:
        lines.extend(["", "## 本轮轻复审", "", f"- `{delta_path}`（上轮未决发现、声明改动与直接影响；不是程序实算 diff）",
                      "- 必须回原审核者，逐条回应输入未决发现及修改位置；解决项保留 check_id、target 并标记 status=resolved，遗漏不能当作已解决。",
                      "- 不要求 baseline 全文、生产者逐 ID 回复、编号水位或审核谱系；必要时只补读准确源位置。"])
    elif review_kind == "production":
        lines.extend(["", "## 产物登记证据", ""])
        lines.append(f"- {'缺少：' if not manifest_path.is_file() else ''}`{manifest_path}`")
        lines.append("- 只核对 task_basis 已列当前输入与相关确认；不读取全部候选或历史登记。")
        lines.extend(["", "## 项目状态与用户确认事实", ""])
        lines.append(f"- {'缺少：' if not state_path.is_file() else ''}`{state_path}`")
        candidate_evidence = _evidence_current(project, args.skill)
        lines.extend(["", "## 本轮按需知识依据", ""])
        raw_candidate_evidence = _read_json(_context_dir(project, "method_reading_evidence.json"), default=None)
        if candidate_evidence:
            _, _, candidate_packet = candidate_evidence
            lines.extend([f"- 完整方法/已采用知识快照：`{candidate_packet}`", f"- 读取声明与版本证据：`{_context_dir(project, 'method_reading_evidence.json')}`"])
        elif isinstance(raw_candidate_evidence, dict) and raw_candidate_evidence.get("skill") == args.skill and raw_candidate_evidence.get("adopted_candidates"):
            lines.append("- 已登记候选证据无效：`method_reading_evidence.json`（不得作为本轮依据）")
        else:
            lines.append("- 无有效读取快照；若任务依据列有必读方法，先完成读取登记再打包审核。")
    else:
        lines.extend(["", "## 合同审核边界", "", "- 仅审核当前 Skill 合同候选的触发、职责、输入输出、引用和硬禁区；不把真实项目产物结论混入本轮。"])
    if delta is not None:
        lines.extend(["", "## 直接证据身份变化", "",
                      "- 增量中的 direct_evidence_changes 明确新增、撤回、同路径换版及前后 SHA-256；它只证明字节身份，不裁定语义影响。",
                      "- 原审核者对相关未决及既有通过项判断保留、重开或无法判断，列准确依据与受影响对象；不得用旧证据关闭问题，也不因证据换版自动全量首审。"])
    lines.extend(["", "## 外部只读审核证据", ""])
    if external_evidence:
        lines.extend(f"- `{item}`" for item in external_evidence)
    else:
        lines.append("- 无（未声明外部审核证据）")
    if revision and review_mode == "initial":
        lines.extend(["", "## 前序修订约束", "", "```json", json.dumps(revision, ensure_ascii=False, indent=2), "```"])
    lines.extend(["", "## 检查清单", ""])
    lines.extend(f"- [ ] `{item['id']}`：{item['text']}" for item in checks)
    lines.extend(["", "## 输出要求", ""])
    if formal:
        lines.extend([f"- 本轮为正式留档：review.json / review.md 写入 `{round_dir}`；结构见审核方法的正式留档节。",
                      "- 问题使用稳定 B-012 等 ID，逐项输出 resolved / unresolved / new-blocker；建议为 suggestion。"])
    else:
        lines.extend(["- 默认轻审核：只回结论和每条 check_id、target（对象）、evidence（证据）、impact（影响）。",
                      "- 若需 CLI 复审，将这些发现记在同一 issues 数组；不要求 review.json / review.md 双文件、基线快照或问题编号。"])
    lines.extend(["- 只传本轮结论、未决发现和必要证据，不传完整聊天。",
                  "- 结论只允许“通过”“有条件通过”“不通过”。没有新证据不新增审美标准；图片视觉质量由用户终审。", ""])
    content = "\n".join(lines)
    output = _context_dir(project, "review_packet.md")
    report = validate_text("review", output, content)
    write_text(scope_path, json.dumps(scope, ensure_ascii=False, indent=2) + "\n")
    if delta is not None:
        write_text(delta_path, json.dumps(delta, ensure_ascii=False, indent=2) + "\n")
    if formal:
        write_text(round_dir / "review_packet.md", content)
    write_text(output, content)
    report["profile"] = str(profile_path)
    report["required_check_count"] = len(required_checks)
    report["manual_check_count"] = len(manual_checks)
    report["manifest_evidence_path"] = str(manifest_path)
    report["manifest_evidence_exists"] = manifest_path.is_file()
    report["project_state_evidence_path"] = str(state_path)
    report["project_state_evidence_exists"] = state_path.is_file()
    report["review_mode"] = review_mode
    report["review_kind"] = review_kind
    report["formal_archive"] = formal
    report.update(input_validation)
    report["task_basis_path"] = str(basis_path)
    report["review_scope_path"] = str(scope_path)
    report["review_delta_path"] = str(delta_path) if delta is not None else None
    report["external_evidence_paths"] = [str(item) for item in external_evidence]
    if usage is not None:
        report["review_usage"] = {"phase": getattr(args, "usage_phase", None) or ("initial_review" if review_mode == "initial" else "revision_review"), **usage}
    return report


def _ensure_prepared(args: argparse.Namespace, *, file_digest=None) -> dict[str, Any]:
    """产前硬门禁：生产某 stage 产物前，校验阶段启动包存在且 Skill 匹配。

    校验结构、已选知识正文和调用方声明的任务必需项。
    通过不证明 Agent 已阅读或完成知识合规审查。
    """
    usage = _check_run_budget(args)
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise StageContextError(f"项目目录不存在：{project}")
    requested = getattr(args, "skill", None)
    _require_preparation_skill(requested)
    startup = _context_dir(project, "stage_startup.md")
    if not startup.is_file():
        message = f"未发现阶段启动包：{startup}。生产前必须先 prepare 拿到本 Skill 知识包；请先运行 prepare。"
        if requested:
            message = f"未发现 {requested} 的阶段启动包：{startup}。生产前必须先 prepare 拿到本 Skill 知识包。"
        raise StageContextError(message)
    text = startup.read_text(encoding="utf-8")
    if "# 阶段启动包" not in text:
        raise StageContextError(f"阶段启动包无效（缺少 # 阶段启动包）：{startup}，请重新 prepare。")
    match = re.search(r"(?m)^- Skill：`([^`]+)`\s*$", text)
    if not match:
        raise StageContextError(f"阶段启动包缺少可识别的 Skill 身份：{startup}，请重新 prepare。")
    skill = match.group(1)
    _require_preparation_skill(skill)
    if requested and requested != skill:
        raise StageContextError(
            f"阶段启动包 Skill 与当前任务不符（启动包={skill}，请求={requested}）："
            f"请先为该 Skill 运行 prepare，不得沿用上一个 Skill 的启动包。"
        )
    validate_text("startup", startup, text)
    # Distinguish a complete selection record from merely having a heading.
    body, separator, record = text.rpartition("\n## 选择记录")
    selected = re.search(r"(?m)^- 已选知识块：(.+)$", record)
    if not separator or not selected:
        raise StageContextError("启动包缺少已选知识记录，请重新 prepare。")
    raw_ids = selected.group(1).strip()
    ids = [] if raw_ids == "无" else [item.strip() for item in raw_ids.split(",")]
    bundle = body.split(f"# {skill} 当前知识包", 1)[1]
    blocks = list(re.finditer(r"(?m)^## (MSB-KB-[\w-]+) \[[^\]\n]+\]\s*$", bundle))
    actual = [item.group(1) for item in blocks]
    if len(set(ids)) != len(ids) or ids != actual:
        raise StageContextError("已选知识记录与正文不一致，请重新 prepare。")
    for index, block in enumerate(blocks):
        end = blocks[index + 1].start() if index + 1 < len(blocks) else len(bundle)
        content = bundle[block.end():end]
        content = re.sub(r"(?m)^适用条件：.*$", "", content).strip()
        if not content:
            raise StageContextError(f"知识块正文为空：{block.group(1)}，请重新 prepare。")
    if not _task_basis_path(project).is_file():
        raise StageContextError("启动包缺少仓库内置知识来源证据（阅读任务清单缺失或无效）；请重新 prepare。")
    _, basis = _load_task_basis(project, skill)
    manifest = basis.get("reading_manifest")
    trial = manifest.get("candidate_trial") if isinstance(manifest, dict) else None
    trial_packet = _context_dir(project, "candidate_trial_packet.md")
    if trial is not None:
        if not test_context(project) or "execution_mode: candidate_test" not in text:
            raise StageContextError("隔离候选试用包只能保留在 candidate-test 项目中。")
        if not isinstance(trial, dict) or not trial_packet.is_file():
            raise StageContextError("隔离候选试用记录或正文缺失；请重新 prepare。")
        packet_bytes = trial_packet.read_bytes()
        if hashlib.sha256(packet_bytes).hexdigest() != trial.get("packet_sha256"):
            raise StageContextError("隔离候选试用正文已漂移；请重新 prepare。")
        source_snapshot = trial.get("archive_path")
        source_id = trial.get("source_id")
        rule_ids = trial.get("rule_ids")
        profile = _read_json(_local_knowledge_path(ROOT / "skills" / skill / "knowledge-profile.json"), default=None)
        if not isinstance(profile, dict) or not isinstance(source_snapshot, str) or not isinstance(source_id, str) or not isinstance(rule_ids, list):
            raise StageContextError("隔离候选来源绑定无效；请重新 prepare。")
        try:
            current_rules = load_candidate_snapshot_rules(
                profile, Path(source_snapshot), source_id, set(rule_ids),
            )
        except CandidateSnapshotError as exc:
            raise StageContextError(str(exc)) from exc
        if [_candidate_record(item) for item in current_rules] != trial.get("rules"):
            raise StageContextError("隔离候选来源正文、版本或 hash 已漂移；请重新 prepare。")
    elif trial_packet.exists():
        raise StageContextError("发现未登记的隔离候选试用包；请重新 prepare。")
    reading = _evidence_current(project, skill, basis=basis)
    adopted = reading[1].get("adopted_candidates", []) if reading else []
    adopted_ids = {item["id"] for item in adopted}
    for rule in getattr(args, "required_rule", None) or []:
        if rule not in ids and rule not in adopted_ids:
            raise StageContextError(f"当前任务必需动态规则未命中：{rule}")
    required_references = getattr(args, "required_reference", None) or []
    if required_references:
        if file_digest is None:
            file_digest = cache_file_digests(digest, check_for_changes=True)
        frozen = {item["path"]: item["sha256"] for item in basis["standards"].get("local_documents", [])}
        frozen[basis["standards"]["root_rules"]] = basis["standards"]["root_rules_sha256"]
        for reference in required_references:
            path = (ROOT / reference).resolve()
            if not path.is_relative_to(ROOT.resolve()) or not path.is_file() or not path.stat().st_size:
                raise StageContextError(f"当前任务必需本地规范不存在、为空或越出仓库：{reference}")
            if str(path) not in frozen:
                raise StageContextError(f"必需本地规范未在本轮冻结：{reference}；在 prepare 请求 local_references 中列明并重新准备。")
            if file_digest(path) != frozen[str(path)]:
                raise StageContextError(f"必需本地规范已变更：{reference}；请重新 prepare。")
    result = {
        "kind": "startup",
        "path": str(startup),
        "skill": skill,
        "prepared": True,
        "knowledge_compliance": "requires_agent_review",
        "ok": True,
    }
    if usage is not None:
        result["run_usage"] = usage
    if not isinstance(manifest, dict):
        raise StageContextError("启动包缺少仓库内置知识来源证据（阅读任务清单缺失或无效）；请重新 prepare。")
    result.update(_validate_current_inputs(project, basis, adopted_candidates=adopted, file_digest=file_digest))
    if manifest.get("skill") != skill or manifest.get("project_path") != str(project) or not isinstance(manifest.get("methods"), list):
        raise StageContextError("阅读任务清单无效，请重新 prepare。")
    test_only = manifest.get("test_only_proposed_method_ids", [])
    if not isinstance(test_only, list) or not all(isinstance(item, str) and item for item in test_only):
        raise StageContextError("阅读任务清单的测试标记无效，请重新 prepare。")
    # A candidate directory is discoverability only. With no mandatory method it cannot
    # turn optional reading into a production gate.
    if not manifest["methods"]:
        if adopted:
            result["candidate_reading_evidence"] = "recorded"
        elif not reading:
            evidence = _read_json(_context_dir(project, "method_reading_evidence.json"), default=None)
            if isinstance(evidence, dict) and evidence.get("adopted_candidates"):
                result["candidate_reading_evidence"] = "invalid"
        if test_only:
            result["ok"] = False
    else:
        identity = _reading_identity(args)
        manifest_identity = {key: manifest.get(key) for key in identity}
        identity_matches = all(identity.values()) and all(isinstance(value, str) and value for value in manifest_identity.values()) and identity == manifest_identity
        complete = bool(reading and identity_matches)
        result["reading_identity"] = "matched" if identity_matches else ("required" if not all(identity.values()) or not all(manifest_identity.values()) else "mismatch")
        result["method_reading_evidence"] = "recorded" if complete else "required"
        result["required_method_count"] = len(manifest["methods"])
        result["test_only_proposed_method_ids"] = test_only
        result["ok"] = result["ok"] and complete and not test_only
    if test_only:
        result["production_blocked"] = "test_only_proposed_method_binding"
    if getattr(args, "show_task_basis", False) and result["ok"]:
        result["task_basis"] = _task_reading_view(basis)
    return result


@_context_scoped
def ensure_prepared(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(args.project).expanduser().resolve()
    testing = _test_request(args, project)
    file_digest = None
    if getattr(args, "mark_read", False):
        # Recheck mutable inputs after writing reading evidence, but reuse the
        # byte digests for unchanged source files within this invocation.
        file_digest = cache_file_digests(digest, check_for_changes=True)
        _record_method_reading(args, file_digest=file_digest)
    result = _ensure_prepared(args, file_digest=file_digest)
    if testing:
        result.update(test_metadata())
        result["candidate_checks_passed"] = result.get("ok") is True
    return result

@_context_scoped
def record_method_reading(args: argparse.Namespace) -> dict[str, Any]:
    return _record_method_reading(args)


def _record_method_reading(args: argparse.Namespace, *, file_digest=None) -> dict[str, Any]:
    project = Path(args.project).expanduser().resolve()
    skill = str(args.skill or "").strip()
    _require_preparation_skill(skill)
    _, basis = _load_task_basis(project, skill)
    manifest = basis.get("reading_manifest")
    if not skill or not isinstance(manifest, dict) or manifest.get("skill") != skill or manifest.get("project_path") != str(project) or not isinstance(manifest.get("methods"), list):
        raise StageContextError("没有与当前 Skill 匹配的阅读任务清单；请先 prepare。")
    _validate_current_inputs(project, basis, file_digest=file_digest)
    identity = _reading_identity(args)
    if not all(identity.values()) or any(manifest.get(key) != identity[key] for key in identity):
        raise StageContextError("登记读取证据必须使用 prepare 时记录的实际执行者 --actor-id 与稳定任务 --task-id。")
    requested = list(getattr(args, "method", None) or [])
    methods = [item.get("id") for item in manifest["methods"] if isinstance(item, dict)]
    if sorted(requested) != sorted(methods) or len(requested) != len(set(requested)):
        raise StageContextError("读取证据必须一次列出且仅列出本轮全部必读专业方法 ID。")
    requested_candidates = list(getattr(args, "candidate_rule", None) or [])
    supplied_root = getattr(args, "vault", None)
    vault = Path(manifest["vault_root"])
    if supplied_root is not None and _vault_root(supplied_root) != vault:
        raise StageContextError("读取知识根必须与 prepare 时的仓库内置根一致。")
    catalog = {item.get("id"): item for item in manifest.get("candidate_catalog", []) if isinstance(item, dict)}
    if len(requested_candidates) != len(set(requested_candidates)) or any(rule not in catalog for rule in requested_candidates):
        raise StageContextError("候选读取只能列出当前 `knowledge_candidates.md` 中的非重复规则 ID。")
    scopes = {}
    for value in getattr(args, "candidate_scope", None) or []:
        rule, separator, scope = value.partition("=")
        if not separator or rule not in requested_candidates or rule in scopes or not scope.strip():
            raise StageContextError("--candidate-scope 必须为本轮采用的非重复 ID=本职适用范围。")
        scopes[rule] = scope.strip()
    reasons = manifest.get("candidate_reasons", {})
    if any(str(reasons.get(rule, "")).startswith("skill_boundary:") and rule not in scopes for rule in requested_candidates):
        raise StageContextError("跨职责候选只能采用本职内容；须用 --candidate-scope ID=本职适用范围 声明，范围外实现项不成为产出要求。")
    verified_bundle = verify_bundle(vault) if requested_candidates and is_bundled_root(vault) else None
    adopted = [_read_candidate_exact(vault, catalog[rule], verified_manifest=verified_bundle)
               for rule in requested_candidates]
    if adopted:
        tombstones = _tombstone_fingerprints(vault, load_policy(ROOT), verified_manifest=verified_bundle)
        if any(item.fingerprint in tombstones for item in adopted):
            raise StageContextError("候选知识已被墓碑禁用；请重新 prepare。")
    records = [_candidate_record(item) for item in adopted]
    constraints = manifest.get("applicable_confirmed", [])
    all_records = list({item["id"]: item for item in [*manifest.get("selected", []), *constraints, *records]
                        if isinstance(item, dict)}.values())
    for index, left in enumerate(all_records):
        for right in all_records[index + 1:]:
            if left["canonical_key"] == right["canonical_key"]:
                raise StageContextError(f"候选知识与当前知识重复同一规范：{left['id']} / {right['id']}。")
            if right["canonical_key"] in left["conflicts_with"] or left["canonical_key"] in right["conflicts_with"]:
                raise StageContextError(f"候选知识与当前已选知识冲突：{left['id']} / {right['id']}。")
    packet = _context_dir(project, "method_reading_packet.md")
    if not packet.is_file():
        raise StageContextError("必读方法快照缺失；请重新 prepare，不能登记读取。")
    existing = packet.read_bytes()
    base_length = manifest.get("method_packet_bytes")
    if not isinstance(base_length, int) or base_length < 0 or len(existing) < base_length or hashlib.sha256(existing[:base_length]).hexdigest() != manifest.get("method_packet_sha256"):
        raise StageContextError("必读方法快照已被修改；请重新 prepare，不能登记读取。")
    packet_text = existing[:base_length].decode("utf-8") + (_candidate_packet(adopted, scopes) if adopted else "")
    write_text_if_changed(packet, packet_text)
    evidence = {"schema_version": "1.0", "skill": skill, "project_path": str(project), "vault_root": str(vault), **identity,
        "methods": manifest["methods"], "selected": manifest.get("selected", []), "method_packet_sha256": manifest["method_packet_sha256"],
        "test_only_proposed_method_ids": manifest.get("test_only_proposed_method_ids", []),
        "adopted_candidates": records, "candidate_scopes": scopes, "packet_sha256": hashlib.sha256(packet.read_bytes()).hexdigest(),
        "attestation": "agent_reported_complete_reading"}
    if _CONTEXT_ID.get() is not None:
        evidence["context_id"] = _CONTEXT_ID.get()
    evidence['reading_reuse_key'] = basis.get('reading_reuse_key')
    output = _context_dir(project, "method_reading_evidence.json")
    write_text_if_changed(output, json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    _save_reading_cache(project)
    return {"kind": "reading_evidence", "path": str(output), "packet_path": str(packet), "skill": skill, "method_count": len(methods), "candidate_count": len(records), "evidence": "agent_reported_not_program_verified", "ok": True}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    state_cmd = commands.add_parser("status", help="只读查看项目状态与续跑阻塞；不执行事务恢复")
    state_cmd.add_argument("--project", required=True)
    authorization = commands.add_parser("authorize-workflow", help="登记已有准确业务流程授权；不确认产物或生成")
    authorization.add_argument("--project", required=True)
    authorization.add_argument("--authorization-id", required=True)
    authorization.add_argument("--authorization-ref", required=True, help="用户对本项目及列明阶段的准确流程授权依据")
    authorization.add_argument("--skill", action="append", required=True, help="授权自行验收的阶段，可重复；范围由核心入口核验")
    acceptance = commands.add_parser("accept-artifact", help="凭流程授权及准确自查采用已登记候选；不新增人工确认")
    acceptance.add_argument("--project", required=True)
    acceptance.add_argument("--artifact-id", required=True)
    acceptance.add_argument("--self-check-json", required=True, help="项目内自查 JSON 的相对路径或绝对路径")
    acceptance.add_argument("--change-id", help="自主修订的原活动变更案；与候选准确匹配后验收并提交")
    acceptance.add_argument("--preflight-json", help="采用回读后只读预检的单段 frozen JSON；失败时沿同一入口续作")
    adopt = commands.add_parser("adopt-package", help="登记并确认初次平台无关正式稿件；不上传或发布")
    adopt.add_argument("--project", required=True)
    adopt.add_argument("--artifact-id", required=True)
    adopt.add_argument("--logical-id", choices=("narrative.package", "storyboard.package", "video.prompt"), required=True)
    adopt.add_argument("--path", required=True, help="项目内的正式 JSON 相对路径")
    adopt.add_argument("--revision", required=True)
    adopt.add_argument("--approval-ref", required=True, help="准确用户整稿确认依据")
    adopt.add_argument("--scope-description", default="", help="本次整稿确认范围说明")
    adopt.add_argument("--depends-on", action="append", default=[], help="准确上游逻辑产物，可重复")
    chat = commands.add_parser("confirm-script-in-chat", help="以用户对准确版本的整稿对话确认正式采用编导稿")
    chat.add_argument("--project", required=True)
    chat.add_argument("--artifact-id", required=True)
    chat.add_argument("--path", required=True, help="项目内准确叙事包 JSON 路径")
    chat.add_argument("--revision", required=True)
    chat.add_argument("--sha256", required=True, help="交给用户审阅的 JSON 文件原始字节 SHA-256")
    chat.add_argument("--confirmation-json", required=True, help="用户原话、时区时间、消息位置及整稿范围")
    chat.add_argument("--change-id", help="返修时对应已登记候选的活动变更案")
    testing = commands.add_parser("candidate-test", help="显式配置独立候选试跑；绝不登记用户产物确认")
    testing.add_argument("--project", required=True)
    testing.add_argument("--run-id", required=True)
    testing.add_argument("--authorization-ref", required=True)
    testing.add_argument("--input-artifact", action="append", default=[])
    testing.add_argument("--usage-contract-json", help="明确受控试跑的持久计量要求；缺真实 turn 可登记 null 并阻止准备")
    testing.add_argument("--skill", help="显式设置本轮候选阶段；须为现有 Skill，不设置生产 next_stage 或确认")
    source_index = commands.add_parser("candidate-source-index", help="只读核验 profile 固定的候选来源快照并列出可试用规则")
    source_index.add_argument("--skill", required=True)
    source_index.add_argument("--source-id", required=True)
    source_index.add_argument("--snapshot", required=True, help="显式提供已核验 Library 快照的本地物化路径")
    kickoff = commands.add_parser("kickoff", help="核对或登记现有项目需求中的开工决定")
    kickoff.add_argument("--project", required=True, type=Path)
    kickoff.add_argument("--answers-json", type=Path, help="已确认的开工决定 JSON 文件；- 从标准输入读取；省略则只读")
    start = commands.add_parser("prepare")
    start.add_argument("--project", required=True)
    start.add_argument("--skill", required=True)
    start.add_argument("--objective", required=True)
    start.add_argument("--request-json")
    start.add_argument("--batch-receipt", help="固定本批现有成图实看回执的项目内路径")
    start.add_argument("--vault", help="兼容旧命令；如提供，必须是本仓库 knowledge/runtime")
    start.add_argument("--actor-id", help="实际执行此任务的 agent 标识；缺失时阅读声明不能放行")
    start.add_argument("--task-id", help="稳定任务标识；同一任务重 prepare 可复用同版阅读声明")
    start.add_argument("--change-id")
    start.add_argument("--target-logical-id", action="append")
    start.add_argument("--test-proposed-method", action="append", default=[], help="仅隔离测试：显式加载一个 proposed 方法；其启动包会被生产前校验拒绝，可重复")
    start.add_argument("--test-profile-json", help="仅隔离测试：包含 proposed 方法绑定的临时 profile；必须同时给出 --test-proposed-method")
    gate = commands.add_parser("ensure-prepared")
    gate.add_argument("--project", required=True)
    gate.add_argument("--skill", required=True, help="当前生产 Skill；启动包 Skill 必须与之匹配")
    gate.add_argument('--show-task-basis', action='store_true', help='通过门禁后返回不含后台目录的任务阅读视图')
    gate.add_argument("--actor-id", help="实际执行此任务的 agent 标识")
    gate.add_argument("--task-id", help="稳定任务标识")
    gate.add_argument("--required-rule", action="append", default=[], help="本任务必需的动态规则 ID，可重复")
    gate.add_argument("--required-reference", action="append", default=[], help="本任务必需的本地规范，仓库相对路径，可重复")
    gate.add_argument("--mark-read", action="store_true", help="读完后在同一调用登记本轮必读方法，再执行产前门禁")
    gate.add_argument("--method", action="append", default=[], help="已完整阅读的必读专业方法 ID；随 --mark-read 使用，可重复")
    gate.add_argument("--candidate-scope", action="append", default=[], help="跨职责候选的本职采用范围：ID=范围与理由；随 --mark-read 使用")
    usage_check = commands.add_parser("usage-check", help="直接读取或画布批次的只读计量检查，不创建准备包")
    usage_check.add_argument("--project", required=True)
    for command in (start, gate):
        command.add_argument("--candidate-test", action="store_true", help="仅隔离候选试跑；不构成生产放行")
        command.add_argument("--candidate-source-id", help="profile 中固定的候选来源 ID，仅 candidate-test 使用")
        command.add_argument("--candidate-source-snapshot", help="显式提供来源快照物化文件，仅 candidate-test 使用")
        command.add_argument("--candidate-rule", action="append", default=[], help="指定进入隔离试用包的 candidate 状态规则，可重复")
    for command in (start, gate, usage_check):
        command.add_argument("--usage-log", action="append", help="本次 Codex 原始日志，可重复提供窗口分片")
        command.add_argument("--usage-turn-id", help="准确的本轮 turn_id；不以文件名代替边界")
        command.add_argument("--usage-boundaries-json", help="显式累计边界 JSON；不自动发现宿主历史")
        command.add_argument("--max-input", type=int, help="任一次已观测输入的停止阈值（含缓存）")
        command.add_argument("--max-total", type=int, help="本轮累计 token 停止阈值")
        command.add_argument("--max-requests", type=int, help="本轮已记录模型请求次数停止阈值")
    receipt = commands.add_parser("record-method-reading")
    receipt.add_argument("--project", required=True)
    receipt.add_argument("--skill", required=True)
    receipt.add_argument("--actor-id", help="实际执行此任务的 agent 标识")
    receipt.add_argument("--task-id", help="稳定任务标识")
    receipt.add_argument("--method", action="append", default=[], help="已完整阅读的必读专业方法 ID，可重复")
    receipt.add_argument("--candidate-rule", action="append", default=[], help="实际采用并按精确目录读取的候选规则 ID，可重复")
    receipt.add_argument("--candidate-scope", action="append", default=[], help="跨职责候选的本职采用范围：ID=范围及适用理由；范围外内容不成为产出要求")
    receipt.add_argument("--vault", help="兼容旧命令；如提供，必须是本仓库 knowledge/runtime")
    review = commands.add_parser("review-pack")
    review.add_argument("--project", required=True)
    review.add_argument("--skill", required=True)
    review.add_argument("--artifact", action="append", required=True)
    review.add_argument("--review-kind", choices=("production", "skill_contract"), default="production", help="生产候选或 Skill 合同审查；两者不混用")
    review.add_argument("--check", action="append", default=[])
    review.add_argument("--evidence", action="append", default=[], help="明确列入审核包的项目外只读文件证据")
    review.add_argument("--review-mode", choices=("initial", "revision"), default="initial")
    review.add_argument("--formal-archive", action="store_true", help="仅用户要求正式留档时启用版本目录、稳定问题 ID 及真实基线对比")
    review.add_argument("--change-summary", action="append", default=[], help="轻复审实际改动的位置、内容与直接影响；候选字节改变时必须提供，可重复")
    review.add_argument("--previous-review", help="上轮发现 JSON（issues 数组）；轻流程只要求四项发现，正式留档沿用旧结构")
    review.add_argument("--baseline", action="append", help="上轮被审文件快照；顺序与上轮 scope 和本轮 artifact 一致")
    review.add_argument("--producer-response", help="本轮生产者逐问题回复 JSON：{\"responses\":[{id,summary,evidence}]}；不另建 change-log")
    review.add_argument("--change-id")
    review.add_argument("--target-logical-id", action="append")
    review.add_argument("--usage-phase", choices=("production", "self_check", "initial_review", "revision_review"), help="本次用量记录所属阶段；只标注已观测计量，不声称控制宿主请求")
    for option, kwargs in (("--usage-log", {"action": "append"}), ("--usage-turn-id", {}), ("--max-input", {"type": int}), ("--max-total", {"type": int}), ("--max-requests", {"type": int})):
        review.add_argument(option, **kwargs)
    review.add_argument("--usage-boundaries-json", help="显式累计边界 JSON；与单轮参数二选一")
    handoff_cmd = commands.add_parser("handoff")
    handoff_cmd.add_argument("--candidate-test", action="store_true")
    handoff_cmd.add_argument("--project", required=True)
    handoff_cmd.add_argument("--to-skill", help="绑定已有目标的具体执行者、批次或兼容交接；默认由程序解析")
    handoff_cmd.add_argument("--from-skill", help="协调方显式登记已到达的来源阶段，须同时引用现有流程授权")
    handoff_cmd.add_argument("--authorization-id", help="显式登记正式流转时使用的现有流程授权 ID")
    handoff_cmd.add_argument("--objective", help="本轮准确目标，写入现有交接卡")
    handoff_cmd.add_argument("--stop-at", help="本轮完成条件与停止点，不能扩展已有授权")
    handoff_cmd.add_argument("--actor-id", help="普通编导或导演交接的实际接收执行者，与 --task-id 同用")
    handoff_cmd.add_argument("--task-id", help="接收方的稳定任务 ID，与 --actor-id 同用")
    handoff_cmd.add_argument("--request-json", help="资产批次沿用含 image_targets 的现有请求")
    handoff_cmd.add_argument("--batch-receipt", help="本批实际实看回执的准确项目内路径")
    handoff_cmd.add_argument("--receive-batch", action="store_true", help="只读接收本批的紧凑结果，不更新交接或项目状态")
    for command in (start, gate, receipt, review, handoff_cmd):
        command.add_argument("--context-id", help="可选独立批次上下文 ID；仅隔离准备/阅读/交接文件，不隔离项目状态或批准")
    check = commands.add_parser("validate")
    check.add_argument("--kind", choices=sorted(LIMITS), required=True)
    check.add_argument("--path", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "status":
        result = status(args)
    elif args.command == "authorize-workflow":
        result = authorize_workflow(args)
    elif args.command == "accept-artifact":
        result = accept_artifact(args)
    elif args.command == "adopt-package":
        result = adopt_package(args)
    elif args.command == "confirm-script-in-chat":
        result = confirm_script_in_chat(args)
    elif args.command == "candidate-test":
        usage_contract = None
        if args.usage_contract_json:
            usage_contract = _read_json(Path(args.usage_contract_json), default=None)
            validate_usage_contract(usage_contract)
        result = configure_test(Path(args.project), args.run_id, args.authorization_ref, args.input_artifact, current_stage=args.skill, usage_contract=usage_contract)
    elif args.command == "candidate-source-index":
        result = candidate_source_index(args)
    elif args.command == "kickoff":
        project = args.project.resolve(strict=True)
        if not project.is_dir():
            raise StageContextError("开工决定必须从业务项目目录读取")
        if args.answers_json:
            raw_answers = (sys.stdin.read() if args.answers_json == Path("-")
                           else args.answers_json.read_text(encoding="utf-8-sig"))
            answers = validate_kickoff(json.loads(raw_answers), for_registration=True)
            with project_lock(project):
                require_production(project)
                path = project / REQUEST_PATH
                request = _read_json(path, default={})
                if not isinstance(request, dict):
                    raise StageContextError("项目需求必须是 JSON 对象")
                previous = request.get("kickoff")
                existing = previous.get("product") if isinstance(previous, dict) else None
                selected = answers["product"]
                if (isinstance(existing, dict) and existing.get("reference_pack_sha256")
                        and selected.get("reference_pack_sha256")
                        and (existing.get("product_id"), existing.get("reference_pack_revision"))
                        == (selected["product_id"], selected["reference_pack_revision"])
                        and existing["reference_pack_sha256"] != selected["reference_pack_sha256"]):
                    raise StageContextError("已有项目锁定的同一参考包版本字节已变化，请还原文件或选择新版本")
                write_json(path, {**request, "kickoff": answers})
        kickoff = load_kickoff(project)
        result = {"ok": kickoff is not None, "path": str(project / REQUEST_PATH), "kickoff": kickoff,
                  "workflow": production_workflow(kickoff)}
        if (timing := product_demo_timing_advisory(kickoff)) is not None:
            result["product_demo_timing"] = timing
        if kickoff and (pack := resolve_product_pack(kickoff["product"])):
            result["product_reference_pack"] = pack
    elif args.command == "usage-check":
        usage = _check_run_budget(args)
        if usage is None:
            raise StageContextError("usage-check 必须提供本轮日志边界和预算")
        result = {"ok": True, "run_usage": usage}
    elif args.command == "prepare":
        result = prepare(args)
    elif args.command == "ensure-prepared":
        result = ensure_prepared(args)
    elif args.command == "record-method-reading":
        result = record_method_reading(args)
    elif args.command == "review-pack":
        result = review_pack(args)
    elif args.command == "handoff":
        result = handoff(args)
    else:
        result = validate_text(args.kind, Path(args.path).expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get("ok") is False else 0


if __name__ == "__main__":
    # The shell may select GBK while the host decodes UTF-8. Configure both
    # channels before producing any non-ASCII JSON or diagnostic output.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    try:
        raise SystemExit(main())
    except (OSError, StageContextError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
