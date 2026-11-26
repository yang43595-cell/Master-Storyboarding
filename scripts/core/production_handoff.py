"""Derive production handoffs from the project records already used by each stage."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .agent_acceptance import (require_asset_workflow_authorization, require_workflow_authorization,
                               validate_agent_acceptance)
from .artifacts import (artifact_is_adopted, cache_file_digests, digest, exact_narrative_approval, load_manifest,
                        project_path, validate_current_dependencies)
from .asset_batch_receipt import (DIRECT_RECEIPTS, DIRECT_REQUESTS, summarize_asset_batch_receipt,
                                  validate_asset_batch_targets, validate_asset_review_targets)
from .asset_refs import _output_decisions, _returned_candidates, asset_index, candidate_was_returned, load_asset_list
from .candidate_test import require_production_manifest
from . import character_boards
from .character_assets import final_output_problems, scoped_output_requirements
from .io import read_json
from .draft_delivery import read_draft_delivery
from .project_kickoff import (CREATIVE_WORKFLOW, PENDING_WORKFLOW, RECREATION_ENTRY, RECREATION_WORKFLOW,
                              load_kickoff, load_project_request, production_workflow)
from .project_state import load_project_state
from .quality_evidence import source_shot_ids
from .product_reference_pack import product_catalog
from .review_publication import is_recorded_website_script_approval
from .revision_control import guard_action, inspect_revision_status
from .visual_readiness import require_visual_readiness
from .prompt_body import source_review_segments

NARRATIVE = 'narrative.package'
DIRECTOR = 'storyboard.package'
VIDEO = 'video.prompt'
ASSETS = 'visual.asset.manifest'
LEGACY_RULE = 'docs/rules/legacy-review.md'
EVIDENCE_ERRORS = (OSError, ValueError, KeyError, TypeError)
BATCH_CARD_PREFIX = '- 图片批次依据 `'
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


def _package(project, manifest, state, logical_id):
    matches = [entry for entry in manifest['artifacts'] if entry.get('logical_id') == logical_id
               and entry.get('status') == 'current']
    if not matches:
        return None, None
    if len(matches) != 1:
        raise ValueError(f'{logical_id} 缺唯一当前版本')
    entry = matches[0]
    path = project_path(project, entry['path_or_url'])
    if (entry['artifact_id'] not in state['current_artifacts'] or not path.is_relative_to(project)
            or not path.is_file()):
        raise ValueError(f'{entry["artifact_id"]} 缺当前产物登记或准确文件字节')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry.get('checksum'):
        raise ValueError(f'{entry["artifact_id"]} 缺当前产物登记或准确文件字节')
    if not artifact_is_adopted(entry, state, project):
        if entry.get('agent_acceptance'):
            try:
                validate_agent_acceptance(project, entry, state=state)
            except EVIDENCE_ERRORS as exc:
                raise ValueError(f'{entry["artifact_id"]} 自主采用依据无效：{exc}') from exc
        raise ValueError(f'{entry["artifact_id"]} 缺有效采用或确认依据')
    validate_current_dependencies(entry, manifest, state, project)
    payload = json.loads(raw.decode('utf-8'))
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


def _asset_readiness(project, narrative, asset_ids=None, *, file_digest=None):
    # Use the existing effective requirements, per-output human decisions and legacy compatibility.
    from .asset_refs import read_choices

    choices = read_choices(project)
    assets = asset_index(load_asset_list(project, narrative, choices, image_requests=asset_ids,
                                       file_digest=file_digest))
    requested = set(assets) if asset_ids is None else set(asset_ids)
    requirements = scoped_output_requirements(narrative, choices, requested)
    ready, waiting = {}, {}
    for asset_id in sorted(requested):
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
            item['required_board_ids'] = list(requirements[asset_id])
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
    return {shot['shot_id']: shot['source_asset_requirement_ids']
            for scene in storyboard['scenes'] for shot in scene['shots']}


def _shot_blockers(shots, waiting):
    return {shot: [issue for asset in assets for issue in waiting.get(asset, [])]
            for shot, assets in shots.items() if any(asset in waiting for asset in assets)}


def require_handoff_visual_inputs(project, storyboard, director, shot_ids, *, rows=None,
                                  manifest=None, state=None, require_review=False, file_digest=None):
    """Use the regular visual gate plus current shot-scoped assets on the default workflow."""
    project = Path(project).resolve()
    scope = list(shot_ids)
    file_digest = cache_file_digests(check_for_changes=True) if file_digest is None else file_digest
    assets = None
    modern = (storyboard.get('schema_version') == '4.0' and bool(storyboard.get('project_id'))
              and load_kickoff(project) is not None
              and LEGACY_RULE not in load_project_request(project).get('local_references', []))
    if modern:
        manifest = load_manifest(project) if manifest is None else manifest
        state = load_project_state(project) if state is None else state
        shots = {shot: assets for shot, assets in _shot_assets(storyboard).items() if shot in scope}
        required = {asset for assets in shots.values() for asset in assets}
        if required:
            _, narrative = _package(project, manifest, state, NARRATIVE)
            if narrative is None:
                raise ValueError('缺当前已采用编导，无法核对镜头的必要资产')
            assets, _, waiting = _asset_readiness(project, narrative, required, file_digest=file_digest)
            if problems := _shot_blockers(shots, waiting):
                raise ValueError('镜头必要资产未就绪：' + '；'.join(
                    f'{shot}：{"、".join(issues)}' for shot, issues in problems.items()))
    return require_visual_readiness(project, storyboard, director, scope, rows=rows,
                                    manifest=manifest, require_review=modern or require_review,
                                    assets=assets, file_digest=file_digest)


def _pending_asset_batches(project, narrative, relevant_assets, confirmed, *, active_request=None):
    """Read image work bound to the current story, including direct work after a restart."""
    output = {}
    returns = _returned_candidates(project)

    def collect(receipt, targets, binding, kinds):
        relevant = [target for target in validate_asset_batch_targets(targets)
                    if target.partition('@')[0] in relevant_assets and target not in confirmed]
        if not relevant:
            return
        path = project_path(project, receipt)
        if not path.is_relative_to(project):
            return
        try:
            document = read_json(path)
        except (OSError, ValueError):
            document = {}
        result = summarize_asset_batch_receipt(project, path, relevant, binding=binding, document=document)
        rows = document.get('items', []) if isinstance(document, dict) else []
        for item in result['items']:
            target = item['target']
            status = item['status']
            if status == 'ready_for_review':
                source = next(r for r in rows if r.get('target') == target and r.get('candidate_id') == item['candidate_id']
                              and str(r.get('sha256')).lower() == item['sha256'])
                if candidate_was_returned(project, source, returns):
                    continue
            record = output.setdefault(target, {'statuses': set(), 'receipts': set(), 'versions': set()})
            if status == 'ready_for_review':
                if not isinstance(source.get('purpose'), str) or not source['purpose'].strip():
                    status = 'pending'
                else:
                    kind = source.get('output_kind') or kinds.get(target)
                    final = source.get('final_output_id')
                    record['versions'].add((item['candidate_id'], item['sha256'], item['path'], kind, final))
                    record['candidate'] = {'target': target, 'output_kind': kind, 'final_output_id': final}
            record['statuses'].add(status)
            record['receipts'].add(result['receipt_path'])

    for index in (project / DIRECT_REQUESTS).glob('*.json'):
        try:
            if index.stem == active_request or not index.resolve().is_relative_to(project):
                continue
            batch = read_json(index)
            if (batch['skill'] != 'asset-image-assistant'
                    or batch['request_sha256'] != index.stem or batch['narrative_id'] != narrative['artifact_id']
                    or batch['narrative_sha256'] != narrative['checksum']):
                continue
            collect(DIRECT_RECEIPTS / index.name, batch['targets'], {'context_id': None, 'request_sha256': index.stem},
                    batch['image_output_kinds'])
        except (*EVIDENCE_ERRORS, AttributeError):
            continue

    context_root = project / '00_项目管理' / '执行上下文' / '批次'
    story_lines = (f'- artifact_id：`{narrative["artifact_id"]}`\n'
                   f'  - logical_id：`{NARRATIVE}`\n'
                   f'  - revision：`{narrative["revision"]}`\n')
    for card_path in sorted(context_root.glob('*/next_stage_handoff.md')):
        if not card_path.resolve().is_relative_to(project):
            continue
        try:
            card = card_path.read_text(encoding='utf-8')
            if '- 目标 Skill：`asset-image-assistant`' not in card or story_lines not in card:
                continue
            source = card.split(story_lines, 1)[1].split('\n- artifact_id：', 1)[0]
            if f'  - sha256：`{narrative["checksum"]}`' not in source.splitlines():
                continue
            entries = [line[len(BATCH_CARD_PREFIX):-1] for line in card.splitlines()
                       if line.startswith(BATCH_CARD_PREFIX) and line.endswith('`')]
            if len(entries) != 1:
                continue
            batch = json.loads(entries[0])
            if batch['context_id'] != card_path.parent.name:
                continue
            if not any(target.partition('@')[0] in relevant_assets and target not in confirmed for target in batch['targets']):
                continue
            request = project_path(project, batch['request'])
            if not request.is_relative_to(project):
                continue
            raw = request.read_bytes()
            if hashlib.sha256(raw).hexdigest() != batch['request_sha256']:
                continue
            planned = json.loads(raw)
            if planned.get('image_targets') == batch['targets']:
                collect(batch['receipt'], batch['targets'], batch, planned.get('image_output_kinds', {}))
        except (*EVIDENCE_ERRORS, AttributeError):
            continue
    return output


def unlisted_asset_work(project, assets, waiting, planned_assets):
    """An intentionally deferred decision or a board with confirmed sources needs no new review slot."""
    inventory = {ast for ast in waiting if ast not in planned_assets and assets.get(ast, {}).get('state') == 'needs_image'}
    boards = {ast: assets[ast] for ast in inventory if assets[ast].get('required_board_ids')}
    missing = {row['target'].partition('@')[0] for row in character_boards.round_board_source_gaps(project, boards, [])}
    composing = boards.keys() - missing
    return inventory - composing, composing


def review_source_assets(assets, targets):
    planned = set()
    for target in targets:
        ast, output = target.split('@')
        item = assets.get(ast, {})
        if item.get('state') != 'needs_image' or output in item.get('required_board_ids', []):
            raise ValueError('整轮待审清单须使用当前需要人工审核的源图槽：' + target)
        planned.add(ast)
    return planned


def _asset_steps(project, state, kickoff, narrative_entry, assets, ready, waiting, *, review_targets=None, active_request=None):
    targets = set(validate_asset_review_targets(review_targets)) if review_targets is not None else set()
    planned_assets = review_source_assets(assets, targets)
    needed = {asset for asset in waiting if assets.get(asset, {}).get('state') == 'needs_image'}
    confirmed = {f'{ast}@{output["output_id"]}' for ast in planned_assets | needed
                 for output in assets[ast]['image'].get('outputs', []) if output.get('sha256') and output['path_exists']}
    outstanding = targets - confirmed
    if not waiting and not outstanding:
        return [_step('assets', 'complete' if ready else 'not_applicable',
                      '所有必要资产已有可用确认输出' if ready else '本轮没有待制作资产',
                      output_ids=[value for values in ready.values() for value in values])]

    decisions, _ = _output_decisions(project)
    rejected = {ast + '@' + output for (ast, output), row in decisions.items() if row['status'] == 'rejected'}
    if rejected & targets:
        raise ValueError('整轮清单仍包含项目已拒用的输出：' + '、'.join(sorted(rejected & targets)))
    generation = (kickoff or {}).get('image_generation') or {}
    authorized = bool(generation or _authorization(project, state, 'asset-image-assistant'))
    batches = _pending_asset_batches(project, narrative_entry, needed | planned_assets, confirmed | rejected,
                                     active_request=active_request)
    reviewed = {target for target, row in batches.items()
                if row['statuses'] == {'ready_for_review'} and len(row['versions']) == 1}
    held = batches.keys() - reviewed
    inventory, composing = unlisted_asset_work(project, assets, waiting, {target.partition('@')[0] for target in outstanding})
    steps = []
    board_gaps = []
    if reviewed:
        all_ready = bool(outstanding) and outstanding <= reviewed and not inventory and not (batches.keys() - targets)
        if all_ready:
            board_gaps = character_boards.round_board_source_gaps(project, assets, [batches[t]['candidate'] for t in reviewed])
            if board_gaps:
                all_ready = False
                inventory.update(row['target'].partition('@')[0] for row in board_gaps)
        covered = outstanding if all_ready else reviewed
        steps.append(_step('asset-review' if all_ready else 'assets', 'waiting',
                           '本轮源图已齐，一次展示并等待整轮确认' if all_ready
                           else '已完成候选留在原批次；列齐整轮待审源图并继续收齐其余输出',
                           output_ids=sorted(covered), receipt_paths=sorted({receipt for target in covered
                                                                             for receipt in batches[target]['receipts']})))
    if held:
        steps.append(_step('assets', 'waiting', '沿现有图片批次等待结果或处理原阻塞',
                           output_ids=sorted(held), receipt_paths=sorted({receipt for target in held
                                                                          for receipt in batches[target]['receipts']})))

    scoped_grant = {f'{ast}@{output}' for ast, outputs in generation.get('outputs', {}).items()
                    if ast in needed | planned_assets for output in outputs}
    if targets:
        missing = outstanding - batches.keys()
        permitted_outputs = missing & scoped_grant if 'outputs' in generation else missing
        if restricted := missing - permitted_outputs:
            steps.append(_step('assets', 'waiting', '待补源图超出当前生图授权范围', output_ids=sorted(restricted)))
    else:
        permitted_outputs = scoped_grant - batches.keys() - confirmed - rejected
        if 'outputs' in generation:
            inventory = set()
    permitted = {target.partition('@')[0] for target in permitted_outputs} | inventory | composing
    if permitted:
        reason = ('按整轮清单并行补齐未派出的源图，沿当前批次收齐后一次交审' if targets
                  else '列全本轮人工交审的 asset_review_targets；复用已有图片和批次，继续制作已授权的缺图')
        if inventory and targets:
            reason += '；盘点尚未覆盖的需求，补入待审清单或登记已核实的复用图'
        if composing:
            reason += '；用 compose-boards 拼成已确认源图对应的人物板'
        route = dict(asset_ids=sorted(permitted))
        if authorized and (permitted_outputs or inventory and not targets and 'outputs' not in generation):
            route['output_ids'] = sorted(permitted_outputs)
        if not targets and confirmed:
            route['confirmed_output_ids'] = sorted(confirmed)
        if not targets and batches:
            route['batched_output_ids'] = sorted(batches)
        if not targets and rejected:
            route['rejected_output_ids'] = sorted(rejected)
        if board_gaps:
            route['missing_board_sources'] = board_gaps
        if composing:
            route['compose_asset_ids'] = sorted(composing)
        if targets and inventory:
            route['inventory_asset_ids'] = sorted(inventory)
        steps.append(_step('assets', 'ready', reason, skill='asset-image-assistant',
                           artifact_ids=[narrative_entry['artifact_id']], **route))
    covered_assets = {target.partition('@')[0] for target in batches} | planned_assets
    if remaining := set(waiting) - permitted - covered_assets:
        steps.append(_step('assets', 'waiting', '必要资产缺确认输出、制作授权或当前需求决定',
                           asset_ids=sorted(remaining), blockers={asset: waiting[asset] for asset in sorted(remaining)}))
    return steps


def _blocked_change(project, logical_ids):
    try:
        guard_action(project, logical_ids=logical_ids)
    except EVIDENCE_ERRORS as exc:
        return str(exc)
    return None


def _read_assets_for_handoff(project, narrative, asset_ids=None, *, file_digest):
    if asset_ids is not None and not asset_ids:
        return {}, {}, {}, None
    try:
        assets, ready, waiting = _asset_readiness(project, narrative, asset_ids, file_digest=file_digest)
        return assets, ready, waiting, None
    except EVIDENCE_ERRORS as exc:
        return {}, {}, {}, str(exc)


def _resolve(project, manifest, state, kickoff, *, actions=None, shot_scope=None, active_asset_request=None):
    if state.get('candidate_test'):
        return [_step('kickoff', 'waiting', '隔离候选项目不进入正式生产交接')]
    require_production_manifest(project)
    if state['project_status'] != 'active':
        return [_step('delivery', 'waiting', '项目当前不处于可交接的活动状态')]
    request = load_project_request(project)
    if LEGACY_RULE in request.get('local_references', []):
        return [_step('kickoff', 'waiting', '本轮选用兼容人工流程，沿原交接步骤执行')]
    narrative_entry, narrative = _package(project, manifest, state, NARRATIVE)
    if kickoff is None and narrative_entry is None:
        return [_step('kickoff', 'waiting', '登记来源和范围；请用户选择准确商品或明确无产品',
                      product_choices=product_catalog())]
    steps = [_step('kickoff', 'complete', '开工决定已登记' if kickoff else '沿已有正式采用记录续作')]
    if narrative_entry is None:
        if candidates := _candidates(manifest, NARRATIVE):
            revisions = [item['artifact_id'] for item in manifest['artifacts']
                         if item['artifact_id'] in candidates and item.get('revision_requested')]
            if revisions:
                steps.append(_step('creative', 'ready', '按用户改稿内容修订待审稿，再交准确新候选',
                                   skill='creative-director', revision_candidate_ids=revisions))
            waiting = [ident for ident in candidates if ident not in revisions]
            if waiting:
                steps.append(_step('creative-review', 'waiting', '等待准确版本的编导整稿确认及正式采用',
                                   artifact_ids=waiting))
        else:
            reason = ('先还原原片剧情、对白、说话人及动作，按统一入口交编导整稿'
                      if production_workflow(kickoff) != CREATIVE_WORKFLOW else '按开工依据制作编导稿')
            steps.append(_step('creative', 'ready', reason, skill='creative-director'))
        return steps
    steps.append(_step('creative-review', 'complete', '编导整稿已正式采用',
                       artifact_ids=[narrative_entry['artifact_id']]))
    if actions == {'creative'}:
        return steps
    # Already adopted legacy video packages keep their original canvas handoff.
    legacy_video = any(e['logical_id'] == VIDEO and e['status'] == 'current' for e in manifest['artifacts'])
    file_digest = cache_file_digests(check_for_changes=True)
    include_assets = actions is None or 'assets' in actions
    if include_assets:
        assets, ready, waiting, asset_error = _read_assets_for_handoff(project, narrative, file_digest=file_digest)
        try:
            if reason := asset_error or _blocked_change(project, [NARRATIVE, ASSETS]):
                steps.append(_step('assets', 'waiting', reason))
            else:
                steps.extend(_asset_steps(project, state, kickoff, narrative_entry, assets, ready, waiting,
                                         review_targets=request.get('asset_review_targets'), active_request=active_asset_request))
        except EVIDENCE_ERRORS as exc:
            asset_error = str(exc)
            assets, waiting = {}, {}
            steps.append(_step('assets', 'waiting', asset_error))
        if actions == {'assets'}:
            return steps

    if production_workflow(kickoff) == RECREATION_WORKFLOW and not legacy_video:
        from .recreation_segments import segment_steps
        if reason := _blocked_change(project, [NARRATIVE]):
            return steps + [_step('recreation', 'waiting', reason)]
        images = {ast: a['image'] for ast, a in assets.items()} if include_assets and not asset_error else None
        return steps + segment_steps(project, narrative, narrative_entry, kickoff=kickoff, asset_images=images,
                                     allow_pending_assets=True)

    if production_workflow(kickoff) == PENDING_WORKFLOW:
        steps.append(_step('director', 'waiting', '编导整稿已采用；在开工记录选择导演重新拍或按原片结构复刻'))
        return steps
    try:
        director, storyboard = _package(project, manifest, state, DIRECTOR)
    except EVIDENCE_ERRORS as exc:
        steps.append(_step('director', 'waiting', str(exc)))
        return steps
    if director is None:
        if reason := _blocked_change(project, [NARRATIVE, DIRECTOR]):
            steps.append(_step('director', 'waiting', reason))
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
    if actions == {'director'}:
        return steps

    shots = _shot_assets(storyboard)
    if not shots:
        steps.append(_step('visual', 'waiting', '导演稿缺准确镜号，无法确定视觉与视频范围'))
        return steps
    include_visual = actions is None or 'visual' in actions
    video_rows, video_shots, video_segments = None, set(), {}
    video_problem = None
    if actions != {'visual'}:
        try:
            video_entry, video = _package(project, manifest, state, VIDEO)
            if video:
                video_rows = source_review_segments(video)
                video_segments = {row['segment_id']: source_shot_ids(row) for row in video_rows}
                video_shots = {shot for scope in video_segments.values() for shot in scope}
                if any(not scope for scope in video_segments.values()) or video_shots - shots.keys():
                    raise ValueError('当前视频稿生成段缺准确的已采用导演镜号')
        except EVIDENCE_ERRORS as exc:
            video_problem = _step('video', 'waiting', str(exc))
            if not include_visual:
                return [*steps, video_problem]
            video_rows = None

    if shot_scope is not None:
        if set(shot_scope) - shots.keys():
            raise ValueError('交接镜号不属于本版已采用导演稿')
        shots = {shot: shots[shot] for shot in shot_scope}
    if not include_assets:
        required_assets = {asset for shot_assets in shots.values() for asset in shot_assets}
        assets, _, waiting, asset_error = _read_assets_for_handoff(
            project, narrative, required_assets, file_digest=file_digest)
    asset_blockers = _shot_blockers(shots, waiting)
    if asset_error:
        asset_blockers = {shot: [asset_error] for shot, asset_ids in shots.items() if asset_ids}
    try:
        visual = require_visual_readiness(project, storyboard, director, list(shots), manifest=manifest,
                                          rows=video_rows, require_review=True, collect_blockers=True,
                                          assets=assets, file_digest=file_digest)
        visual_blockers = visual['blocked_shot_ids']
        stage_blockers = visual['blockers_by_stage']
    except EVIDENCE_ERRORS as exc:
        steps.append(_step('visual', 'waiting', str(exc), shot_ids=list(shots)))
        return [*steps, video_problem] if video_problem else steps
    upstream_blockers = stage_blockers['upstream']
    review_blockers = stage_blockers['visual']
    visual_blocked_shots = [shot for shot in shots
                            if shot in review_blockers or shot in upstream_blockers or shot in asset_blockers]
    if include_visual and visual_blocked_shots:
        visual_ready = [shot for shot in visual_blocked_shots
                        if shot not in asset_blockers and shot not in upstream_blockers]
        asset_waiting = [shot for shot in asset_blockers if shot not in upstream_blockers]
        if reason := _blocked_change(project, [NARRATIVE, DIRECTOR]):
            steps.append(_step('visual', 'waiting', reason, shot_ids=visual_blocked_shots))
        else:
            if visual_ready:
                steps.append(_step('visual', 'ready', '进入分镜阶段，按镜号实看并记录复用、无需补图或实际缺口',
                                   skill='storyboard-director', artifact_ids=[director['artifact_id']],
                                   shot_ids=visual_ready,
                                   blockers={shot: review_blockers[shot] for shot in visual_ready}))
            if asset_waiting:
                steps.append(_step('visual', 'waiting', '必要资产的正式确认和交接仍未完成',
                                   shot_ids=asset_waiting,
                                   blockers={shot: asset_blockers[shot] for shot in asset_waiting}))
            if upstream_blockers:
                steps.append(_step('visual', 'waiting', '按已登记的退回原因等待上游处理',
                                   shot_ids=list(upstream_blockers),
                                   blockers={shot: reasons + asset_blockers.get(shot, [])
                                             for shot, reasons in upstream_blockers.items()}))
    elif include_visual:
        steps.append(_step('visual', 'complete', '当前导演版本的分镜检查已完成'))
    if video_problem:
        return [*steps, video_problem]
    if actions == {'visual'}:
        return steps

    remaining = [shot for shot in shots if shot not in video_shots]
    frozen = _blocked_change(project, [NARRATIVE, DIRECTOR, ASSETS, VIDEO])
    if actions is None or 'video' in actions:
        blocked = {shot: asset_blockers.get(shot, []) + visual_blockers.get(shot, [])
                   for shot in remaining if shot in asset_blockers or shot in visual_blockers}
        eligible = [shot for shot in remaining if shot not in blocked]
        authorization = _authorization(project, state, 'voiceover-to-video-script') if eligible else None
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
        existing_blockers = {shot: asset_blockers.get(shot, []) + visual_blockers.get(shot, [])
                             for shot in shots if shot in video_shots and (shot in asset_blockers or shot in visual_blockers)}
        if existing_blockers:
            steps.append(_step('video', 'waiting', '现有视频稿的素材或视觉依据需重新核对',
                               artifact_ids=[video_entry['artifact_id']], shot_ids=list(existing_blockers),
                               blockers=existing_blockers))
    if not video_entry or (actions is not None and 'canvas' not in actions):
        return steps
    if not remaining and not (asset_blockers or visual_blockers):
        steps.append(_step('video', 'complete', '已采用视频提示词覆盖本轮导演镜号',
                           artifact_ids=[video_entry['artifact_id']]))
    canvas = (kickoff or {}).get('canvas')
    try:
        delivery = read_draft_delivery(project, canvas, video_entry, set(video_segments))
    except EVIDENCE_ERRORS as exc:
        steps.append(_step('canvas', 'waiting', f'画布交付回执待核对：{exc}', segment_ids=list(video_segments)))
        return steps
    delivered = {segment for segment, item in delivery.items() if item['status'] == 'complete'}
    unknown = {segment for segment, item in delivery.items() if item['status'] == 'unknown'}
    if unknown:
        steps.append(_step('canvas', 'waiting', '创建结果未知，先核对原画布节点', segment_ids=sorted(unknown),
                           draft_evidence={segment: delivery[segment] for segment in sorted(unknown)}))
    pending = [segment for segment in video_segments if segment not in delivered]
    ready_segments = [segment for segment in pending if segment not in unknown and not set(video_segments[segment]) &
                      (asset_blockers.keys() | visual_blockers.keys())]
    if ready_segments and canvas and not frozen:
        steps.append(_step('canvas', 'ready', '按已登记画布继续草稿写入或原节点刷新回读',
                           skill='voiceover-to-video-script', artifact_ids=[video_entry['artifact_id']],
                           segment_ids=ready_segments, draft_evidence={segment: delivery[segment] for segment in ready_segments
                                                                      if segment in delivery}))
    if blocked_segments := [segment for segment in pending if segment not in unknown and
                            (not canvas or frozen or segment not in ready_segments)]:
        steps.append(_step('canvas', 'waiting', frozen or '缺已登记画布、本段视觉复核或有效资产确认',
                           segment_ids=blocked_segments))
    if delivered:
        current = not (remaining or pending or asset_blockers or visual_blockers)
        steps.append(_step('delivery', 'complete' if current else 'waiting',
                           '正式草稿字段回读完成，用户审查交接前核同批排版回执'
                           if not remaining and not pending else '部分正式草稿已完成字段回读',
                           segment_ids=sorted(delivered)))
    return steps


def resolve_production_handoff(project_root, *, manifest=None, state=None, actions=None, shot_scope=None,
                               revision_status=None, active_asset_request=None):
    """Report the full flow or check only the named actions and their prerequisites."""
    project = Path(project_root).resolve()
    workflow = CREATIVE_WORKFLOW
    try:
        active_change, _, blockers = (inspect_revision_status(project) if revision_status is None
                                      else revision_status)
        if blockers:
            return {'workflow': workflow, 'steps': [_step('revision', 'waiting', '；'.join(blockers))]}
        manifest = load_manifest(project) if manifest is None else manifest
        state = load_project_state(project) if state is None else state
        kickoff = None if state.get('candidate_test') else load_kickoff(project)
        workflow = production_workflow(kickoff)
        steps = _resolve(project, manifest, state, kickoff, actions=actions, shot_scope=shot_scope,
                         active_asset_request=active_asset_request)
        if isinstance(active_change, dict) and actions is None:
            steps.insert(0, _step('revision', 'waiting', '沿原变更案继续核对、返修或确认',
                                  change_id=active_change.get('change_id')))
    except EVIDENCE_ERRORS as exc:
        steps = [_step('kickoff', 'waiting', f'现有项目证据待核对：{exc}')]
    if workflow == RECREATION_WORKFLOW:
        for step in steps:
            if step["status"] == "ready" and step["action"] not in {"creative", "assets"}:
                step.update(workflow=workflow, workflow_entry=RECREATION_ENTRY)
    return {'workflow': workflow, 'steps': steps}


def recreation_prompt_context(project, segment_ids):
    """Read a current segment even when its existing prompt is already complete."""
    from .recreation_segments import prompt_context

    require_production_manifest(project)
    state = load_project_state(project)
    if state['project_status'] != 'active' or production_workflow(load_kickoff(project)) != RECREATION_WORKFLOW:
        raise ValueError('本入口适用于活动项目的深度复刻局部写词')
    _, _, blockers = inspect_revision_status(project)
    if blockers:
        raise ValueError('；'.join(blockers))
    guard_action(project, logical_ids=[NARRATIVE])
    entry, narrative = _package(project, load_manifest(project), state, NARRATIVE)
    if entry is None:
        raise ValueError('缺有效采用的编导叙事包')
    return prompt_context(project, narrative, entry, segment_ids)


def require_production_handoff(project_root, action, *, manifest=None, state=None,
                               shot_ids=None, segment_ids=None, active_asset_request=None):
    """Require the named normal transition to be ready for the exact requested scope."""
    shot_scope = list(dict.fromkeys(shot_ids)) if shot_ids is not None and action in {'visual', 'video'} else None
    if shot_scope == []:
        raise ValueError(f'{action} 超出已就绪的 shot_ids 范围')
    steps = resolve_production_handoff(project_root, manifest=manifest, state=state,
                                       actions={action}, shot_scope=shot_scope, active_asset_request=active_asset_request)['steps']
    for step in steps:
        if step['action'] != action or step['status'] != 'ready':
            continue
        requested = {}
        for field, ids in (('shot_ids', shot_ids), ('segment_ids', segment_ids)):
            if ids is None:
                continue
            scope = list(dict.fromkeys(ids))
            if not scope or not set(scope) <= set(step.get(field, [])):
                raise ValueError(f'{action} 超出已就绪的 {field} 范围')
            requested[field] = scope
        return {**step, **requested}
    waiting = [step['reason'] for step in steps if step['status'] == 'waiting']
    reason = '；'.join(waiting) or '等待前置就绪或本阶段已完成'
    if shot_scope is not None:
        raise ValueError(f'{action} 超出已就绪的 shot_ids 范围：{reason}')
    raise ValueError(f'{action} 当前未就绪：{reason}')
