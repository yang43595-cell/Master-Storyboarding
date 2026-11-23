"""Derive production handoffs from the project records already used by each stage."""
from __future__ import annotations

from pathlib import Path

from .agent_acceptance import (require_asset_workflow_authorization, require_workflow_authorization,
                               validate_agent_acceptance)
from .artifacts import (artifact_is_adopted, cache_file_digests, digest, exact_narrative_approval,
                        load_manifest, project_path, validate_current_dependencies)
from .asset_batch_receipt import (CONTEXT_ROOT, load_asset_batch_binding,
                                  receive_bound_asset_batch)
from .asset_refs import asset_index, load_asset_list
from .candidate_test import require_production_manifest
from .character_assets import final_output_problems, scoped_output_requirements
from .draft_delivery import read_draft_delivery
from .io import read_json
from .project_kickoff import (CREATIVE_WORKFLOW, LEGACY_RULE, PENDING_WORKFLOW, RECREATION_ENTRY,
                              RECREATION_WORKFLOW, explicit_source_paths, load_project_request,
                              production_workflow, project_source_inputs, validate_kickoff)
from .project_state import load_project_state
from .quality_evidence import source_shot_ids
from .review_publication import is_recorded_website_script_approval
from .revision_control import check_frozen_branch, handoff_revision_status
from .visual_readiness import require_visual_readiness
from .web_content import source_review_segments

NARRATIVE = 'narrative.package'
DIRECTOR = 'storyboard.package'
VIDEO = 'video.prompt'
ASSETS = 'visual.asset.manifest'
EVIDENCE_ERRORS = (OSError, ValueError, KeyError, TypeError)
FROM_SKILLS = {
    'director': 'creative-director', 'assets': 'creative-director',
    'visual': 'storyboard-planner', 'video': 'storyboard-director',
    'canvas': 'voiceover-to-video-script',
}


def _step(action, status, reason, *, skill=None, **scope):
    result = {'action': action, 'status': status, 'reason': reason, **scope}
    if skill is not None:
        result['skill'] = skill
        if action in FROM_SKILLS:
            result['from_skill'] = FROM_SKILLS[action]
    return result


def _package(project, manifest, state, logical_id, *, file_digest=digest, checked=None):
    matches = [entry for entry in manifest['artifacts'] if entry.get('logical_id') == logical_id
               and entry.get('status') == 'current']
    if not matches:
        return None, None
    if len(matches) != 1:
        raise ValueError(f'{logical_id} 缺唯一当前版本')
    entry = matches[0]
    path = project_path(project, entry['path_or_url'])
    if (entry['artifact_id'] not in state['current_artifacts'] or not path.is_relative_to(project)
            or not path.is_file() or file_digest(path) != entry.get('checksum')):
        raise ValueError(f'{entry["artifact_id"]} 缺当前产物登记或准确文件字节')
    if not artifact_is_adopted(entry, state, project):
        if entry.get('agent_acceptance'):
            try:
                validate_agent_acceptance(project, entry, state=state)
            except EVIDENCE_ERRORS as exc:
                raise ValueError(f'{entry["artifact_id"]} 自主采用依据无效：{exc}') from exc
        raise ValueError(f'{entry["artifact_id"]} 缺有效采用或确认依据')
    validate_current_dependencies(entry, manifest, state, project, file_digest=file_digest, checked=checked)
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f'{entry["artifact_id"]} 正文不是 JSON 对象')
    return entry, payload


def _candidates(manifest, logical_id):
    return [entry['artifact_id'] for entry in manifest['artifacts']
            if entry.get('logical_id') == logical_id and entry.get('status') == 'candidate'
            and not entry.get('candidate_test')]


def _authorization(project, state, skill):
    validator = (require_asset_workflow_authorization if skill == 'asset-image-assistant'
                 else require_workflow_authorization)
    for record in state['workflow_authorizations']:
        try:
            return validator(project, record['id'], state=state, **(
                {} if skill == 'asset-image-assistant' else {'skill': skill}))['id']
        except (KeyError, ValueError):
            continue
    return None


def _director_website_approval(project, entry, state):
    return any(exact_narrative_approval(receipt, entry, entry['checksum'])
               and is_recorded_website_script_approval(project, entry, receipt['approval_ref'])
               for receipt in state['user_confirmations'])


def _asset_readiness(project, narrative, asset_ids=None, *, manifest=None, state=None, file_digest=None):
    # Use the existing effective requirements, per-output human decisions and legacy compatibility.
    from .asset_refs import read_choices

    choices = read_choices(project)
    assets = asset_index(load_asset_list(project, narrative, choices, image_requests=asset_ids,
                                        manifest=manifest, state=state, file_digest=file_digest))
    requested = set(assets) if asset_ids is None else set(asset_ids)
    ready, waiting = {}, {}
    try:
        requirements = scoped_output_requirements(narrative, choices, requested)
    except ValueError:
        # An invalid specification for one AST cannot invalidate independent
        # assets. Recheck them individually only when the bulk check fails.
        requirements = {}
        for asset_id in sorted(requested):
            try:
                requirements.update(scoped_output_requirements(narrative, choices, [asset_id]))
            except ValueError as exc:
                waiting[asset_id] = [f'{asset_id}：{exc}']
                if asset_id in assets:
                    assets[asset_id]['requirement_error'] = str(exc)
    for asset_id in sorted(requested):
        if asset_id in waiting:
            continue
        item = assets.get(asset_id)
        if item is None or item['state'] in {'split_parent', 'pending'}:
            waiting[asset_id] = [f'{asset_id} 缺当前可用的图像需求决定']
            continue
        if item['state'] != 'needs_image':
            continue
        image = item['image']
        outputs = [output for output in image.get('outputs', [])
                   if output.get('sha256') and output.get('path_exists')]
        if asset_id in requirements:
            item['required_outputs'] = requirements[asset_id]
            problems = final_output_problems(requirements, [asset_id], {asset_id: outputs})
        else:
            problems = []
        if image['status'] != 'confirmed' or not outputs or problems:
            waiting[asset_id] = problems or [f'{asset_id} 缺有文件字节依据的已确认输出']
        else:
            ready[asset_id] = [f'{asset_id}@{output["output_id"]}' for output in outputs
                               if asset_id not in requirements or output['output_id'] in requirements[asset_id]]
    return assets, ready, waiting


def _shot_assets(storyboard):
    return {shot['shot_id']: shot.get('source_asset_requirement_ids', [])
            for scene in storyboard['scenes'] for shot in scene['shots']}


def _shot_blockers(shots, waiting):
    return {shot: [issue for asset in assets for issue in waiting.get(asset, [])]
            for shot, assets in shots.items() if any(asset in waiting for asset in assets)}


def require_handoff_visual_inputs(project, storyboard, director, shot_ids, *, rows=None,
                                  manifest=None, state=None, require_review=False,
                                  collect_blockers=False, request=None, asset_status=None,
                                  file_digest=None):
    """Apply the same selected-shot checks for routing, writing, adoption and delivery."""
    from .project_kickoff import uses_routed_handoff

    project = Path(project).resolve()
    scope = list(shot_ids)
    request = load_project_request(project) if request is None else request
    modern = (storyboard.get('schema_version') == '4.0' and bool(storyboard.get('project_id'))
              and uses_routed_handoff(request))
    if modern and asset_status is None and 'kickoff' in request:
        validate_kickoff(request['kickoff'])
    asset_blockers = None
    if modern or asset_status is not None:
        asset_blockers = {}
        shots = {shot: assets for shot, assets in _shot_assets(storyboard).items() if shot in scope}
        required = {asset for assets in shots.values() for asset in assets}
        if required:
            if asset_status is None:
                manifest = load_manifest(project) if manifest is None else manifest
                state = load_project_state(project) if state is None else state
                _, narrative = _package(project, manifest, state, NARRATIVE,
                                        file_digest=digest if file_digest is None else file_digest)
                if narrative is None:
                    raise ValueError('缺当前已采用编导，无法核对镜头的必要资产')
                _, _, waiting = _asset_readiness(project, narrative, required, manifest=manifest,
                                                 state=state, file_digest=file_digest)
                error = None
            else:
                waiting, error = asset_status
            asset_blockers = ({shot: [error] for shot, assets in shots.items() if assets}
                              if error else _shot_blockers(shots, waiting))
            if asset_blockers and not collect_blockers:
                raise ValueError('镜头必要资产未就绪：' + '；'.join(
                    f'{shot}：{"、".join(issues)}' for shot, issues in asset_blockers.items()))
    return require_visual_readiness(project, storyboard, director, scope, rows=rows,
                                    manifest=manifest, require_review=modern or require_review,
                                    collect_blockers=collect_blockers, asset_blockers=asset_blockers)


def _pending_asset_batches(project, narrative, image_assets, approved):
    """Read ordinary delegated image batches bound to the current story and request."""
    output = {}
    context_root = project / CONTEXT_ROOT
    for directory in [context_root, *sorted((context_root / '批次').glob('*'))]:
        if not directory.is_dir() or directory.resolve() != directory:
            continue
        batch = load_asset_batch_binding(project, directory, narrative=narrative, validate_request=False)
        if batch is None:
            continue
        try:
            result = receive_bound_asset_batch(project, batch)
            items, receipt = result['items'], result['receipt_path']
        except EVIDENCE_ERRORS:
            # A bound request that is now unreadable still owns its targets.
            items = [{'target': target, 'status': 'blocked'} for target in batch['targets']]
            receipt = None
        for item in items:
            target = item['target']
            if target.partition('@')[0] not in image_assets or target in approved:
                continue
            record = output.setdefault(target, {'statuses': set(), 'receipts': set(), 'context_ids': set()})
            record['context_ids'].add(batch.get('context_id') or '')
            record['statuses'].add(item['status'])
            if receipt is not None:
                record['receipts'].add(receipt)
    return output


def _approved_asset_outputs(assets, image_assets, required):
    approved = set()
    for asset in image_assets:
        outputs = {}
        for output in assets[asset]['image'].get('outputs', []):
            if output.get('sha256') and output.get('path_exists'):
                outputs.setdefault(output['output_id'], []).append(output)
        for output_id, matches in outputs.items():
            kind = required.get(asset, {}).get(output_id)
            if len(matches) == 1 and (kind is None or matches[0].get('output_kind') == kind):
                approved.add(f'{asset}@{output_id}')
    return approved


def _asset_steps(project, state, kickoff, narrative_entry, assets, ready, waiting, *,
                 requested_outputs=None, resume_context_id=None):
    generation = (kickoff or {}).get('image_generation') or {}
    authorization = _authorization(project, state, 'asset-image-assistant')
    image_assets = {asset for asset, item in assets.items()
                    if item['state'] == 'needs_image' and not item.get('requirement_error')}
    needed = set(waiting) & image_assets
    required = {asset: assets[asset]['required_outputs'] for asset in image_assets
                if assets[asset].get('required_outputs')}
    approved = _approved_asset_outputs(assets, image_assets, required)
    batches = _pending_asset_batches(project, narrative_entry, image_assets, approved)
    granted_outputs = {f'{asset}@{output}' for asset, outputs in generation.get('outputs', {}).items()
                       if asset in image_assets for output in outputs}
    if not waiting and not batches and not (granted_outputs - approved) and requested_outputs is None:
        return [_step('assets', 'complete' if ready else 'not_applicable',
                      '所有必要资产已有可用确认输出' if ready else '本轮没有待制作资产',
                      output_ids=[value for values in ready.values() for value in values])]
    steps = []
    reviewed = sorted(target for target, row in batches.items()
                      if row['statuses'] == {'ready_for_review'} and len(row['context_ids']) == 1)
    if reviewed:
        steps.append(_step('asset-review', 'waiting', '候选已完成实看，沿原目标等待准确图片确认及登记',
                           output_ids=reviewed, receipt_paths=sorted({receipt for target in reviewed
                                                                      for receipt in batches[target]['receipts']})))
    held = set(batches) - set(reviewed)
    if held:
        steps.append(_step('assets', 'waiting', '沿现有图片批次等待结果或处理原阻塞',
                           output_ids=sorted(held), receipt_paths=sorted({receipt for target in held
                                                                          for receipt in batches[target]['receipts']}),
                           context_ids=sorted({ident for target in held
                                               for ident in batches[target]['context_ids'] if ident})))
    batched_assets = {target.partition('@')[0] for target in batches}
    open_output_assets = set()
    if requested_outputs is not None:
        requested = set(requested_outputs)
        if 'outputs' in generation:
            permitted_outputs = requested & granted_outputs
        else:
            permitted_outputs = {target for target in requested if target.partition('@')[0] in image_assets
                                 and (generation or authorization)}
        if resume_context_id is None:
            permitted_outputs -= batches.keys()
        else:
            permitted_outputs &= {target for target, row in batches.items()
                                  if row['context_ids'] == {resume_context_id} and row['statuses'] == {'pending'}}
        permitted_outputs -= approved
        permitted = {target.partition('@')[0] for target in permitted_outputs}
    elif 'outputs' in generation:
        permitted_outputs = granted_outputs - batches.keys() - approved
        permitted = {target.partition('@')[0] for target in permitted_outputs}
    elif generation or authorization:
        known_outputs = {f'{asset}@{output}' for asset, outputs in required.items() for output in outputs}
        permitted_outputs = known_outputs - batches.keys() - approved
        open_output_assets = needed - batched_assets - required.keys()
        permitted = open_output_assets | {target.partition('@')[0] for target in permitted_outputs}
    else:
        permitted_outputs, permitted = set(), set()
    if permitted:
        steps.append(_step('assets', 'ready', '沿原资产任务续查已出候选、待审与待制作输出',
                           skill='asset-image-assistant', artifact_ids=[narrative_entry['artifact_id']],
                           asset_ids=sorted(permitted),
                           output_ids=sorted(permitted_outputs),
                           **({'open_output_asset_ids': sorted(open_output_assets)} if open_output_assets else {})))
    if remaining := set(waiting) - permitted - batched_assets:
        steps.append(_step('assets', 'waiting', '必要资产缺确认输出、制作授权或当前需求决定',
                           asset_ids=sorted(remaining), blockers={asset: waiting[asset] for asset in sorted(remaining)}))
    return steps


def _blocked_change(active_change, logical_ids):
    try:
        check_frozen_branch(active_change, logical_ids)
    except EVIDENCE_ERRORS as exc:
        return str(exc)
    return None


def _original_source_issue(project, request):
    try:
        explicit_source_paths(project, project_source_inputs(request))
    except EVIDENCE_ERRORS as exc:
        return str(exc)
    return None


def _resolve(project, manifest, state, request, kickoff, active_change, file_digest, checked, *,
             asset_output_ids=None, asset_batch_context_id=None):
    if state.get('candidate_test'):
        return [_step('kickoff', 'waiting', '隔离候选项目不进入正式生产交接')]
    require_production_manifest(project, manifest=manifest)
    if state['project_status'] != 'active':
        return [_step('delivery', 'waiting', '项目当前不处于可交接的活动状态')]
    if LEGACY_RULE in request.get('local_references', []):
        return [_step('kickoff', 'waiting', '本轮选用兼容人工流程，沿原交接步骤执行')]
    narrative_entry, narrative = _package(project, manifest, state, NARRATIVE,
                                          file_digest=file_digest, checked=checked)
    if kickoff is None and narrative_entry is None:
        return [_step('kickoff', 'waiting', '缺当前开工决定；登记确认的来源、范围和产品安排')]
    steps = [_step('kickoff', 'complete', '开工决定已登记' if kickoff else '沿已有正式采用记录续作')]
    if narrative_entry is None:
        if candidates := _candidates(manifest, NARRATIVE):
            steps.append(_step('creative-review', 'waiting', '等待准确版本的编导整稿确认及正式采用',
                               artifact_ids=candidates))
        elif source_issue := _original_source_issue(project, request):
            steps.append(_step('creative', 'waiting', source_issue))
        else:
            reason = ('先还原原片剧情、对白、说话人及动作，按统一入口交编导整稿'
                      if production_workflow(kickoff) != CREATIVE_WORKFLOW else '按开工依据制作编导稿')
            steps.append(_step('creative', 'ready', reason, skill='creative-director'))
        return steps
    steps.append(_step('creative-review', 'complete', '编导整稿已正式采用',
                       artifact_ids=[narrative_entry['artifact_id']]))
    if production_workflow(kickoff) == PENDING_WORKFLOW:
        steps.append(_step('director', 'waiting', '编导整稿已采用；在开工记录选择导演重新拍或按原片结构复刻'))
        return steps
    asset_error, waiting = None, {}
    try:
        assets, ready, waiting = _asset_readiness(project, narrative, manifest=manifest, state=state,
                                                  file_digest=file_digest)
    except EVIDENCE_ERRORS as exc:
        asset_error = str(exc)
        steps.append(_step('assets', 'waiting', asset_error))
    else:
        try:
            if reason := _blocked_change(active_change, [NARRATIVE, ASSETS]):
                steps.append(_step('assets', 'waiting', reason))
            else:
                steps.extend(_asset_steps(project, state, kickoff, narrative_entry, assets, ready, waiting,
                                          requested_outputs=asset_output_ids, resume_context_id=asset_batch_context_id))
        except EVIDENCE_ERRORS as exc:
            steps.append(_step('assets', 'waiting', str(exc)))

    try:
        director, storyboard = _package(project, manifest, state, DIRECTOR,
                                       file_digest=file_digest, checked=checked)
    except EVIDENCE_ERRORS as exc:
        steps.append(_step('director', 'waiting', str(exc)))
        return steps
    if director is None:
        if reason := _blocked_change(active_change, [NARRATIVE, DIRECTOR]):
            steps.append(_step('director', 'waiting', reason))
            return steps
        if source_issue := _original_source_issue(project, request):
            steps.append(_step('director', 'waiting', source_issue))
            return steps
        authorization = _authorization(project, state, 'storyboard-planner')
        if authorization or _director_website_approval(project, narrative_entry, state):
            candidates = _candidates(manifest, DIRECTOR)
            steps.append(_step('director', 'ready', '完成本版导演自查及采用' if candidates else '接委派制作导演稿',
                               skill='storyboard-planner', artifact_ids=[narrative_entry['artifact_id']],
                               candidate_ids=candidates, authorization_id=authorization))
        else:
            steps.append(_step('director', 'waiting', '缺有效导演流程授权或本版整稿的网站通过依据'))
        return steps
    steps.append(_step('director', 'complete', '准确导演稿已正式采用', artifact_ids=[director['artifact_id']]))

    shots = _shot_assets(storyboard)
    if not shots:
        steps.append(_step('visual', 'waiting', '导演稿缺准确镜号，无法确定视觉与视频范围'))
        return steps
    video_rows, video_shots, video_segments = None, set(), {}
    try:
        video_entry, video = _package(project, manifest, state, VIDEO,
                                       file_digest=file_digest, checked=checked)
        if video:
            video_rows = source_review_segments(video)
            video_segments = {row['segment_id']: source_shot_ids(row) for row in video_rows}
            video_shots = {shot for scope in video_segments.values() for shot in scope}
            if any(not scope for scope in video_segments.values()) or video_shots - shots.keys():
                raise ValueError('当前视频稿生成段缺准确的已采用导演镜号')
    except EVIDENCE_ERRORS as exc:
        steps.append(_step('video', 'waiting', str(exc)))
        return steps

    remaining = [shot for shot in shots if shot not in video_shots]
    try:
        visual = require_handoff_visual_inputs(project, storyboard, director, shots, rows=video_rows,
                                               manifest=manifest, state=state, require_review=True,
                                               request=request, collect_blockers=True,
                                               asset_status=(waiting, asset_error))
    except EVIDENCE_ERRORS as exc:
        steps.append(_step('visual', 'waiting', str(exc), shot_ids=list(shots)))
        return steps
    visual_blockers = visual['availability_blockers']
    asset_blockers = visual['asset_blockers']
    if visual_blockers or asset_blockers:
        visual_ready = [shot for shot in visual_blockers if shot not in asset_blockers]
        if reason := _blocked_change(active_change, [NARRATIVE, DIRECTOR]):
            steps.append(_step('visual', 'waiting', reason,
                               shot_ids=[shot for shot in shots if shot in visual_blockers or shot in asset_blockers]))
        else:
            if visual_ready:
                steps.append(_step('visual', 'ready', '进入分镜阶段，按镜号实看并记录复用、无需补图或实际缺口',
                                   skill='storyboard-director', artifact_ids=[director['artifact_id']],
                                   shot_ids=visual_ready,
                                   blockers={shot: visual_blockers[shot] for shot in visual_ready}))
            if asset_blockers:
                steps.append(_step('visual', 'waiting', '必要资产的正式确认和交接仍未完成',
                                   shot_ids=list(asset_blockers), blockers=asset_blockers))
    else:
        steps.append(_step('visual', 'complete', '当前导演版本的分镜检查已完成'))

    blocked = {shot: visual['blocked_shot_ids'][shot]
               for shot in remaining if shot in visual['blocked_shot_ids']}
    eligible = [shot for shot in remaining if shot not in blocked]
    authorization = _authorization(project, state, 'voiceover-to-video-script') if eligible else None
    frozen = _blocked_change(active_change, [NARRATIVE, DIRECTOR, ASSETS, VIDEO])
    if eligible and authorization and not frozen:
        steps.append(_step('video', 'ready', '已确认资产及视觉依赖就绪，可接续原稿或写本轮镜号',
                           skill='voiceover-to-video-script',
                           artifact_ids=[director['artifact_id']] + ([video_entry['artifact_id']] if video_entry else []),
                           authorization_id=authorization, shot_ids=eligible,
                           candidate_ids=_candidates(manifest, VIDEO)))
    if blocked or eligible and (not authorization or frozen):
        steps.append(_step('video', 'waiting', frozen or '等待所需资产确认、视觉复核或有效视频流程授权',
                           shot_ids=[shot for shot in remaining if shot in blocked or not authorization or frozen],
                           blockers=blocked))
    if not video_entry:
        return steps
    if not remaining:
        steps.append(_step('video', 'complete', '已采用视频提示词覆盖本轮导演镜号',
                           artifact_ids=[video_entry['artifact_id']]))
    canvas = (kickoff or {}).get('canvas')
    try:
        delivery = read_draft_delivery(project, canvas, video_entry, set(video_segments))
    except EVIDENCE_ERRORS as exc:
        steps.append(_step('canvas', 'waiting', f'画布交付回执待核对：{exc}', segment_ids=list(video_segments)))
        return steps
    delivered = {segment for segment, item in delivery.items() if item['status'] == 'complete'}
    pending = [segment for segment in video_segments if segment not in delivered]
    unknown = [segment for segment in pending if delivery.get(segment, {}).get('status') == 'unknown']
    if unknown:
        steps.append(_step('canvas', 'waiting', '创建结果未知，沿原画布和交付记录核对节点',
                           segment_ids=unknown, draft_evidence={segment: delivery[segment] for segment in unknown}))
    blocked_segments, ready_segments = [], []
    unavailable_shots = asset_blockers.keys() | visual_blockers.keys()
    unbound_segments = set(visual['binding_segment_ids'])
    for segment in pending:
        if segment in unknown:
            continue
        if (frozen or not canvas or not canvas.get('target_url')
                or segment in unbound_segments or unavailable_shots.intersection(video_segments[segment])):
            blocked_segments.append(segment)
        else:
            ready_segments.append(segment)
    if ready_segments:
        steps.append(_step('canvas', 'ready', '按本段交付记录新建或续用原草稿节点',
                           skill='voiceover-to-video-script', artifact_ids=[video_entry['artifact_id']],
                           segment_ids=ready_segments,
                           draft_evidence={segment: delivery[segment] for segment in ready_segments
                                           if segment in delivery}))
    if blocked_segments:
        steps.append(_step('canvas', 'waiting', frozen or '缺已登记画布、本段视觉复核或有效资产确认',
                           segment_ids=blocked_segments))
    if delivered:
        steps.append(_step('delivery', 'complete' if not remaining and not pending else 'waiting',
                           '交付终点为正式草稿保存及回读' if not remaining and not pending else '部分草稿已保存及回读',
                           segment_ids=sorted(delivered)))
    return steps


def resolve_production_handoff(project_root, *, manifest=None, state=None, request=None,
                               revision_status=None, asset_output_ids=None, asset_batch_context_id=None):
    """Evaluate current production evidence, without writing cards or touching the platform."""
    project = Path(project_root).resolve()
    workflow = CREATIVE_WORKFLOW
    try:
        active_change, _, blockers = (handoff_revision_status(project)
                                      if revision_status is None else revision_status)
        if blockers:
            return {'workflow': workflow, 'steps': [_step('revision', 'waiting', '；'.join(blockers))]}
        manifest = load_manifest(project) if manifest is None else manifest
        state = load_project_state(project) if state is None else state
        request = load_project_request(project) if request is None else request
        kickoff = validate_kickoff(request['kickoff']) if not state.get('candidate_test') and 'kickoff' in request else None
        workflow = production_workflow(kickoff)
        file_digest = cache_file_digests(digest, check_for_changes=True)
        checked = set()
        steps = _resolve(project, manifest, state, request, kickoff, active_change, file_digest, checked,
                         asset_output_ids=asset_output_ids, asset_batch_context_id=asset_batch_context_id)
        if isinstance(active_change, dict):
            steps.insert(0, _step('revision', 'waiting', '沿原变更案继续核对、返修或确认',
                                  change_id=active_change.get('change_id')))
    except EVIDENCE_ERRORS as exc:
        steps = [_step('kickoff', 'waiting', f'现有项目证据待核对：{exc}')]
    if workflow == RECREATION_WORKFLOW:
        for step in steps:
            if step["status"] == "ready" and step["action"] != "creative":
                step.update(workflow=workflow, workflow_entry=RECREATION_ENTRY)
    return {'workflow': workflow, 'steps': steps}


def select_ready_handoff(flow, action, **scope):
    """Project the requested work from the same current readiness result."""
    for step in flow['steps']:
        if step['action'] != action or step['status'] != 'ready':
            continue
        requested = {}
        for field, ids in scope.items():
            if ids is None:
                continue
            selected = list(dict.fromkeys(ids))
            if not selected or not set(selected) <= set(step.get(field, [])):
                raise ValueError(f'{action} 超出已就绪的 {field} 范围')
            requested[field] = selected
        return {**step, **requested}
    waiting = [step['reason'] for step in flow['steps']
               if step['status'] == 'waiting' and step['action'] in (action, 'kickoff', 'revision')]
    raise ValueError(f'{action} 当前未就绪：' + ('；'.join(waiting) or '等待前置就绪或本阶段已完成'))


def require_production_handoff(project_root, action, *, manifest=None, state=None,
                               shot_ids=None, segment_ids=None, output_ids=None, asset_batch_context_id=None):
    """Require the normal transition for the exact requested scope."""
    flow = resolve_production_handoff(project_root, manifest=manifest, state=state,
                                     asset_output_ids=output_ids, asset_batch_context_id=asset_batch_context_id)
    return select_ready_handoff(flow, action, shot_ids=shot_ids, segment_ids=segment_ids, output_ids=output_ids)
