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
from .io import project_lock, read_json, sha256_file, sha256_json, utc_now


from .acceptance_rules import (
    ASSET_ADOPTION_SKILL,
    AUTHORIZATION_SKILLS,
    CORE_CHECKS,
    ROOT,
    SKILLS,
    WORKFLOW,
    _binding,
    _file,
    _register_authorization,
    _require_authorization,
    _text,
    _unchanged_image_director,
    _valid_skills,
    _validate_report,
    acceptance_binding as _rules_acceptance_binding,
    build_agent_acceptance as _rules_build_agent_acceptance,
    read_agent_acceptance as _rules_read_agent_acceptance,
    register_asset_workflow_authorization,
    register_workflow_authorization,
    require_asset_workflow_authorization,
    require_workflow_authorization,
    skill_for_artifact,
    validate_agent_acceptance as _rules_validate_agent_acceptance,
)


def acceptance_binding(project, entry, *, file_digest=None, read_json_fn=None):
    """Return exact local bytes/direct upstreams for an actual self-check report."""
    return _rules_acceptance_binding(
        project,
        entry,
        file_digest=sha256_file if file_digest is None else file_digest,
        read_json_fn=read_json if read_json_fn is None else read_json_fn,
    )


def build_agent_acceptance(project, entry, report_path, *, state=None, file_digest=None, read_json_fn=None):
    """Validate the actual self-check and return only its immutable pointer."""
    return _rules_build_agent_acceptance(
        project,
        entry,
        report_path,
        state=state,
        file_digest=sha256_file if file_digest is None else file_digest,
        read_json_fn=read_json if read_json_fn is None else read_json_fn,
        repo_root=ROOT,
    )


def read_agent_acceptance(project, entry, report_path, *, state=None, file_digest=None, read_json_fn=None):
    """Return the acceptance pointer and the report validated from those exact bytes."""
    return _rules_read_agent_acceptance(
        project,
        entry,
        report_path,
        state=state,
        file_digest=sha256_file if file_digest is None else file_digest,
        read_json_fn=read_json if read_json_fn is None else read_json_fn,
        repo_root=ROOT,
    )


def validate_agent_acceptance(project, entry, *, state=None, file_digest=None, read_json_fn=None):
    """Recheck immutable evidence for consumption; never produces confirmation."""
    return _rules_validate_agent_acceptance(
        project,
        entry,
        state=state,
        file_digest=sha256_file if file_digest is None else file_digest,
        read_json_fn=read_json if read_json_fn is None else read_json_fn,
        repo_root=ROOT,
    )


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
            from .canvas_readback import validate_submission
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
