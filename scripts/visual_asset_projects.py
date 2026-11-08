"""Project AST workbench. Project files remain authoritative; the library stores work in progress."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from visual_asset_source import project_path, receive_table, prompt_context, resolve_reference
from core.artifacts import artifact_is_confirmed, digest, verify_image_confirmation
from visual_asset_choices import image_work_items, image_decision
import visual_asset_product_usage as product_usage
from visual_asset_characters import (character_route, prepare_character, choose_character_route, upstream_outfits,
                                     automatic_final, compose_character_finals)
from core.character_assets import uses_wardrobe, production_kinds, character_type
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
import visual_asset_backup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))
from core.artifacts import (load_manifest, save_manifest, register_workspace_artifact,
                            promote_candidate, mark_impacted_artifacts_stale)
from core.io import project_lock
from core.project_state import load_project_state, write_project_state
from core.schema_contracts import require_schema


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex[:16]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def safe(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:36] or '资产'


def validate(data, filename):
    require_schema(data, filename, label='项目资产数据')


def production_basis(narrative, ast_ids):
    context = prompt_context(narrative, ast_ids)
    context['asset_requirements'] = [
        {k: v for k, v in r.items() if k not in {'image_decision', 'image_reason'}}
        for r in context['asset_requirements']]
    return context


def validate_asset_requirement_granularity(narrative):
    """Reject ambiguous location records before the workbench can invent a split."""
    for req in narrative['asset_requirements']:
        if req['category'] != 'LOCATION':
            continue
        if req.get('spatial_scope') != 'SINGLE_CONTIGUOUS_SPACE':
            raise ValueError(
                f"场景需求 {req['asset_id']} 未声明单一连续空间；请在编导稿新版本中为独立地点分别登记 AST，网页不会自行拆分"
            )


def source_project(path):
    if not str(path or '').strip():
        raise ValueError('请填写项目文件夹，或选择已连接项目')
    root = Path(path).expanduser().resolve()
    if not (root / '00_项目管理/artifact_manifest.json').is_file():
        raise ValueError('所选目录没有项目产物登记表：' + str(root / '00_项目管理/artifact_manifest.json'))
    artifacts = load_manifest(root)['artifacts']
    entries = [a for a in artifacts if a.get('logical_id') == 'narrative.package' and a.get('status') == 'current']
    if len(entries) != 1 or entries[0].get('user_approved') is not True:
        raise ValueError('项目必须有唯一、当前有效且已由你确认的 narrative.package')
    entry = entries[0]
    state = load_project_state(root)
    if not artifact_is_confirmed(entry, state, root):
        raise ValueError('项目状态缺少叙事包的用户确认记录，请先在项目中完成确认')
    path = project_path(root, entry['path_or_url'])
    narrative = read(path)
    validate(narrative, 'narrative-package.schema.json')
    if narrative.get('schema_version') == '2.0':
        from core.story_sources import validate_story_sources
        validate_story_sources(narrative, project_root=root)
    validate_asset_requirement_granularity(narrative)
    if narrative['revision'] != entry['revision']:
        raise ValueError('叙事包正文状态或版本与项目登记不一致')
    ids = [r['asset_id'] for r in narrative['asset_requirements']]
    if len(ids) != len(set(ids)):
        raise ValueError('叙事包有重复 AST 编号')
    table = receive_table(root, artifacts, entry, state, narrative, path)
    if table:
        narrative['_asset_table'] = table
    from visual_asset_choices import apply_choices
    narrative = apply_choices(root, narrative)
    usage = product_usage.load_usage(root, narrative)
    if usage:
        narrative['_product_usage'] = usage
    effective_contract = {k: v for k, v in narrative.items() if not k.startswith('_')}
    validate(effective_contract, 'narrative-package.schema.json')
    validate_asset_requirement_granularity(narrative)
    return root, {'artifact_id': entry['artifact_id'], 'logical_id': 'narrative.package',
                  'revision': entry['revision'], 'path': str(path.resolve())}, narrative


BUSY = {'generating', 'submitting', 'submitted', 'querying'}


def reference_evidence(project, requirements, assets=()):
    """Read explicitly named briefs, plus the known brief beside a selected source image."""
    documents, unavailable, paths = [], [], []
    root = Path(project)
    artifacts = load_manifest(root)['artifacts']
    state = load_project_state(root)
    for req in requirements:
        for raw in req.get('reference_paths', []):
            try:
                path = Path(resolve_reference(root, raw, artifacts, state).partition('#')[0])
            except ValueError:
                unavailable.append(str(raw))
                continue
            if path.suffix.lower() in {'.md', '.json', '.txt'}:
                paths.append(path)
    for asset in assets:
        parent = Path(asset['source']).parent
        paths += [parent / name for name in ('PRODUCT_BRIEF.md', 'REFERENCE_MANIFEST.md') if (parent / name).is_file()]
    for path in dict.fromkeys(paths):
        if path.stat().st_size > 150000:
            unavailable.append(str(path) + '（正文过大，需要先在项目中整理直接参考）')
        else:
            documents.append({'path': str(path.resolve()), 'content': path.read_text(encoding='utf-8-sig')})
    return {'reference_documents': documents, 'unavailable_references': unavailable}


class Projects:
    def __init__(self, library, skill, bridge):
        self.lib, self.skill, self.bridge = library, skill, bridge
        self.root = library.root / 'projects'
        self.root.mkdir(exist_ok=True)
        self.batch_running = False

    def save(self, project):
        write(self.root / (project['id'] + '.json'), project)

    def get(self, ident):
        if not re.fullmatch('[a-f0-9]{16}', ident):
            raise ValueError('无效项目编号')
        return read(self.root / (ident + '.json'))

    def summaries(self):
        return [{k: p.get(k) for k in ('id', 'path', 'name', 'source', 'planning', 'export', 'batch')}
                for p in (read(f) for f in self.root.glob('*.json'))]

    def bind(self, path):
        root, source, narrative = source_project(path)
        if self.lib.vault and root.is_relative_to(self.lib.vault.resolve()):
            raise ValueError('知识库用于资产归档；生产项目请使用独立项目文件夹，避免绕过知识库写入门禁')
        with self.lib.lock:
            previous = next((p for p in self.summaries() if Path(p['path']) == root), None)
            if previous:
                return self.sync(previous['id'])
            p = {'id': uid(), 'path': str(root), 'name': narrative['project_id'], 'source': source,
                 'narrative': narrative, 'rows': [{'requirement': r, 'reference_ids': [], 'job_id': None} for r in image_work_items(narrative)],
                 'created_at': now()}
            self.save(p)
            product_usage.schedule(self, p)
            return self.detail(p['id'])

    def fresh(self, p):
        _, source, narrative = source_project(p['path'])
        if source != p['source'] or narrative != p['narrative']:
            raise ValueError('上游叙事包已变化，请点击“同步需求”查看差异并重新核对')

    def invalidate_export(self, p, reason):
        if not p.get('export'):
            return
        root = Path(p['path'])
        with project_lock(root):
            manifest = load_manifest(root)
            mark_impacted_artifacts_stale(manifest, {'visual.asset.manifest'}, reason, p['source']['revision'])
            save_manifest(root, manifest)
            p.setdefault('export_history', []).append(p.pop('export'))

    def sync(self, ident):
        with self.lib.lock:
            p = self.get(ident)
            if p.get('planning', {}).get('status') == 'running' or p.get('batch', {}).get('status') == 'running':
                raise ValueError('制作任务处理中，完成后再同步需求')
            _, source, narrative = source_project(p['path'])
            old = {r['requirement']['asset_id']: r for r in p['rows']}
            rows, changes = [], []
            context_changed = any(p['narrative'].get(k) != narrative.get(k)
                                  for k in ('locked_facts', 'events', 'objective'))
            old_background = (p['narrative'].get('_asset_table') or {}).get('background')
            new_background = (narrative.get('_asset_table') or {}).get('background')
            context_changed = context_changed or old_background != new_background
            for req in image_work_items(narrative):
                row = old.pop(req['asset_id'], None)
                paragraph_changed = narrative.get('schema_version') == '2.0' and row is not None and (
                    production_basis(p['narrative'], [req['asset_id']]) != production_basis(narrative, [req['asset_id']]))
                if row is None or {k: v for k, v in row['requirement'].items() if k not in {'image_decision', 'image_reason'}} != {k: v for k, v in req.items() if k not in {'image_decision', 'image_reason'}} or context_changed or paragraph_changed:
                    changes.append(req['asset_id'] + (' 新增' if row is None else ' 需求或叙事上下文变化，需重新核对'))
                    if row:
                        p.setdefault('history', []).append(copy.deepcopy(row))
                        if row.get('job_id'):
                            job = self.lib.get(row['job_id'], 'jobs')
                            if any(s['status'] in BUSY for s in job['steps']):
                                raise ValueError('受影响需求仍在生成，先核实结果后再同步')
                            job['project_stale'] = True
                            self.lib.save(job, 'jobs')
                    row = {'requirement': req, 'reference_ids': (row or {}).get('reference_ids', []), 'job_id': None}
                row['requirement'] = req
                rows.append(row)
            for row in old.values():
                changes.append(row['requirement']['asset_id'] + ' 已从需求表移除')
                p.setdefault('history', []).append(row)
            if source != p['source'] or narrative != p['narrative']:
                self.invalidate_export(p, '叙事包需求变化，网页资产表需重新交接')
                p.pop('export_pending', None)
            p.update(rows=rows, source=source, narrative=narrative, changes=changes, synced_at=now())
            self.save(p)
            product_usage.schedule(self, p)
            return self.detail(ident)

    @staticmethod
    def row_uses_wardrobe(row):
        return uses_wardrobe(row['requirement']) and row.get('character_output_mode') != 'single'

    @staticmethod
    def row_kinds(row):
        if uses_wardrobe(row['requirement']) and row.get('character_output_mode') == 'single':
            return ['人物单图']
        return production_kinds(row['requirement'])

    def character_output_mode(self, payload):
        """Choose implementation depth; preserve the original AST and prior images."""
        with self.lib.lock:
            p = self.get(payload['project_id'])
            self.fresh(p)
            row = self.row(p, payload['ast_id'])
            mode = payload.get('mode')
            if not uses_wardrobe(row['requirement']) or mode not in {'single', 'wardrobe'}:
                raise ValueError('请选择人物单图或身份与服装板路线')
            if row.get('character_output_mode') == mode:
                return self.detail(p['id'])
            job = self.lib.get(row['job_id'], 'jobs') if row.get('job_id') else None
            if p.get('planning', {}).get('status') == 'running' or p.get('batch', {}).get('status') == 'running' or (job and any(s['status'] in BUSY or s.get('audit', {}).get('status') in {'drafting', 'reviewing'} for s in job['steps'])):
                raise ValueError('人物图片正在处理，请完成后调整路线')
            if job:
                row.setdefault('job_history', []).append(job['id'])
                row['previous_character_images'] = list(dict.fromkeys(row.get('previous_character_images', []) + [s['selected'] for s in job['steps'] if s.get('selected')]))
            row.update(character_output_mode=mode, job_id=None)
            self.invalidate_export(p, '人物输出职责已调整；旧图和确认记录保留，当前路线重新选用确认')
            p.pop('export_pending', None)
            self.save(p)
            return self.detail(p['id'])

    def detail(self, ident):
        p = self.get(ident)
        try:
            self.fresh(p)
            p['source_error'] = ''
        except Exception as exc:
            p['source_error'] = str(exc)
        for row in p['rows']:
            row['job'] = self.lib.get(row['job_id'], 'jobs') if row.get('job_id') else None
            row['uses_wardrobe'] = self.row_uses_wardrobe(row)
            if row['requirement']['category'] == 'CHARACTER':
                row['character_type'] = character_type(row['requirement'])
            if row['uses_wardrobe']:
                try:
                    row['upstream_outfits'] = upstream_outfits(row['requirement'])
                    row['outfits_need_sync'] = (row['job'] or {}).get('character_workflow', {}).get('upstream_outfits') != row['upstream_outfits']
                except ValueError as exc:
                    row['outfit_error'] = str(exc)
            else:
                for key in ('outfit_error', 'upstream_outfits', 'outfits_need_sync'):
                    row.pop(key, None)
        p['next_stage'] = load_project_state(Path(p['path'])).get('next_stage')
        return p

    def row(self, p, ast):
        row = next((r for r in p['rows'] if r['requirement']['asset_id'] == ast), None)
        if row is None:
            raise ValueError('此 AST 不在当前项目白名单内')
        return row

    def references(self, payload):
        with self.lib.lock:
            p = self.get(payload['project_id'])
            self.fresh(p)
            row = self.row(p, payload['ast_id'])
            if p.get('planning', {}).get('status') == 'running':
                raise ValueError('Codex 正在盘点，完成后再更换参考')
            if row.get('job_id'):
                raise ValueError('已有制作项的参考保留原记录，请在制作格中选择参考')
            ids = list(dict.fromkeys(payload['ids']))
            for ident in ids:
                if self.lib.get(ident).get('trashed_at'):
                    raise ValueError('不能使用垃圾桶中的参考')
            row['reference_ids'] = ids
            self.save(p)
            return self.detail(p['id'])

    def production_route(self, payload):
        with self.lib.lock:
            p = self.get(payload['project_id'])
            self.fresh(p)
            row = self.row(p, payload['ast_id'])
            if payload.get('scene'):
                raise ValueError('网页不按名称或状态拆分 AST；独立空间请先在上游登记独立编号')
            if self.row_uses_wardrobe(row) or payload.get('route') not in {'reuse', 'build'}:
                raise ValueError('请选择对应需求的制作方式')
            if row.get('job_id'):
                job = self.lib.get(row['job_id'], 'jobs')
                if any(s['status'] in BUSY or s.get('audit', {}).get('status') in {'drafting', 'reviewing'} for s in job['steps']):
                    raise ValueError('图片正在处理，请稍后调整')
                for step in job['steps']:
                    step['generation_enabled'] = payload['route'] == 'build'
                self.lib.save(job, 'jobs')
            row['production_route'] = payload['route']
            self.save(p)
            return self.detail(p['id'])

    def scene_output_mode(self, payload):
        with self.lib.lock:
            p = self.get(payload['project_id'])
            self.fresh(p)
            row = self.row(p, payload['ast_id'])
            mode = payload.get('mode')
            if row['requirement']['category'] != 'LOCATION' or mode not in {'single', 'four'}:
                raise ValueError('请选择单图或四视图场景')
            if not row.get('job_id'):
                raise ValueError('请先选择制作方式')
            job = self.lib.get(row['job_id'], 'jobs')
            if mode == 'four' and len(job['steps']) == 1 and (job.get('direct_reuse') or job['steps'][0]['view'] == '任意视角'):
                source = job['steps'][0].get('selected')
                if source:
                    return self.scene_reference({**payload, 'asset_id': source})
            if any(s['status'] in BUSY or s.get('audit', {}).get('status') in {'drafting', 'reviewing'} for s in job['steps']):
                raise ValueError('场景正在处理，请完成后调整')
            # Older jobs did not store scene_output_mode.  Compare the effective
            # delivery contract instead of the saved mode label, so a repeated
            # choice cannot invalidate an otherwise identical export.
            before = [(s['key'], s.get('required', True), tuple(s.get('dependencies', []))) for s in job['steps']]
            groups = {}
            for step in job['steps']:
                groups.setdefault(step['state'], []).append(step)
            if mode == 'four':
                if len(groups) * 4 > 16:
                    raise ValueError('当前四图方案超过16张预算，请先核对状态与必要覆盖，不自动扩张')
                if any(len(group) != 1 and [s['view'] for s in group] != ['45°', '正面', '反面', '俯视'] for group in groups.values()):
                    raise ValueError('当前按覆盖方案制作；切换四图须先核对已有视角与所选四图职责，不能改写已确认图用途')
            invalid = set()
            for group in groups.values():
                first = group[0]
                if mode == 'four' and len(group) == 1:
                    if not first.get('selected'):
                        first.update(name='45°', view='45°', purpose=first['state'] + ' · 45°')
                    for n, view in enumerate(['正面', '反面', '俯视'], 1):
                        step = copy.deepcopy(first)
                        step.update(key=first['key'] + '_angle' + str(n), name=view, view=view,
                                    purpose=first['state'] + ' · ' + view, status='waiting', outputs=[],
                                    project_confirmation=None, reference_ids=[])
                        for key in ('selected', 'audit', 'prompt', 'prompt_approval', 'history',
                                    'generation_run_id', 'submit_id', 'authorization',
                                    'query_failures', 'query_blocked', 'query_retry_after'):
                            step.pop(key, None)
                        job['steps'].append(step)
                        group.append(step)
                for n, step in enumerate(group):
                    step['required'] = mode == 'four' or n == 0
                    # Delivery quantity does not change which confirmed images
                    # an angle was made from. Retain this graph even when hidden.
                    if not step.get('dependencies') and n and mode == 'four':
                        step['dependencies'] = [x['key'] for x in group[:n]]
                    receipt = step.get('project_confirmation')
                    if receipt and step['dependencies']:
                        basis = self.dependency_basis(job, step)
                        saved = receipt.get('dependency_confirmations')
                        # An old graph alone cannot prove which earlier image
                        # was actually reviewed; never invent that missing basis.
                        if saved != basis or not all(x['confirmation_id'] for x in basis):
                            invalid.add(step['key'])
            if invalid:
                self.reset_steps(job, self.dependent_keys(job, invalid))
            job['scene_output_mode'] = mode
            row['scene_output_mode'] = mode
            after = [(s['key'], s.get('required', True), tuple(s.get('dependencies', []))) for s in job['steps']]
            changed = before != after or bool(invalid)
            if changed:
                job.setdefault('scene_mode_history', []).append({'mode': mode, 'at': now(), 'reason': '用户选择场景交付数量；原图和确认记录保留'})
            self.lib.save(job, 'jobs')
            if changed:
                # The required output set is part of the delivery contract. Preserve
                # old files, but never leave an export current after it changes.
                self.invalidate_export(p, '场景交付从单图与四视图之间切换，需重新核对交接')
                p.pop('export_pending', None)
            self.save(p)
            return self.detail(p['id'])

    def prepare_reuse(self, payload):
        with self.lib.lock:
            p = self.get(payload['project_id'])
            self.fresh(p)
            row = self.row(p, payload['ast_id'])
            if self.row_uses_wardrobe(row):
                raise ValueError('人物请使用人物卡片')
            if row.get('job_id'):
                return self.detail(p['id'])
            req = row['requirement']
            kind = self.row_kinds(row)[0]
            recipe = self.skill.recipe(kind)
            states = ['外观母版'] if kind == '角色外观' else req['required_states'] or [req['name']]
            views = ['任意视角'] if kind == '环境' else ['主图']
            outputs = [{'key':f'state{n}_view{i}', 'kind':kind, 'state':s, 'view':v,
                        'ratio':recipe['ratio'], 'purpose':s + ' · ' + v,
                        'rationale':'按上游所需状态选用已有资产；项目适用性由用户确认',
                        'dependencies':[], 'must_not_inherit':[]} for n,s in enumerate(states) for i,v in enumerate(views)]
            planned_row = copy.deepcopy(row)
            planned_row['production_route'] = 'reuse'
            previous_planning = copy.deepcopy(p.get('planning'))
            self.apply_plan(p, [planned_row], {'rows':[{'ast_id':req['asset_id'],'outputs':outputs,'rationale':'从资产库选用上游要求的图片，不调用生成盘点','blockers':[]}]}, {'mode':'project','method':'existing-asset-selection'})
            p = self.get(p['id'])
            self.row(p, req['asset_id'])['production_route'] = 'reuse'
            if previous_planning is not None:
                p['planning'] = previous_planning
            self.save(p)
            job = self.lib.get(self.row(p, req['asset_id'])['job_id'], 'jobs')
            job['direct_reuse'] = True
            self.lib.save(job, 'jobs')
            return self.detail(p['id'])

    def scene_reference(self, payload):
        """Accept any camera angle as input, separately from delivered views."""
        with self.lib.lock:
            p = self.get(payload['project_id'])
            self.fresh(p)
            row = self.row(p, payload['ast_id'])
            if row['requirement']['category'] != 'LOCATION':
                raise ValueError('此入口只用于环境参考图')
            asset = self.lib.get(payload['asset_id'], require_file=True)
            if asset.get('trashed_at') or asset['category'] != '场景':
                raise ValueError('请选择未删除的场景参考图，视角不限')
            if image_decision(row['requirement']) != '需要制作':
                raise ValueError('此需求尚未列为需要制作，请先更新资产需求判断')
            if p.get('planning', {}).get('status') == 'running':
                raise ValueError('正在盘点，请完成后添加环境参考')
            job = self.lib.get(row['job_id'], 'jobs') if row.get('job_id') else None
            if job and any(s['status'] in BUSY or s.get('audit', {}).get('status') in {'drafting', 'reviewing'} for s in job['steps']):
                raise ValueError('环境正在处理，请完成后添加参考')
            self.copy_to_project(p, asset)
            row['reference_ids'] = [asset['id']]
            # Preserve arbitrary-angle reused images in their original job;
            # they must not count as a completed 45-degree output.
            replace = bool(payload.get('mode') == 'four' and job and len(job['steps']) == 1 and (job.get('direct_reuse') or job['steps'][0]['view'] == '任意视角'))
            if not job or replace:
                if job:
                    row.setdefault('job_history', []).append(job['id'])
                req = row['requirement']
                states = req['required_states'] or [req['name']]
                outputs = [{'key': f'state{n}_view{i}', 'kind': '环境', 'state': state,
                            'view': view, 'ratio': self.skill.recipe('环境')['ratio'],
                            'purpose': state + ' · ' + view, 'rationale': '按用户选择生成四张环境资产图',
                            'dependencies': [], 'must_not_inherit': []}
                           for n, state in enumerate(states) for i, view in enumerate(['45°', '正面', '反面', '俯视'])]
                row['production_route'] = 'build'
                self.apply_plan(p, [row], {'rows': [{'ast_id': req['asset_id'], 'outputs': outputs,
                                'rationale': '任意视角输入作为空间参考，四张输出分别确认', 'blockers': []}]},
                                {'mode': 'project', 'method': 'scene-reference'})
                p = self.get(p['id'])
                row = self.row(p, payload['ast_id'])
                job = self.lib.get(row['job_id'], 'jobs')
            else:
                self.save(p)
                if payload.get('mode') == 'four':
                    self.scene_output_mode({**payload, 'mode': 'four'})
                    p = self.get(p['id'])
                    row = self.row(p, payload['ast_id'])
                    job = self.lib.get(row['job_id'], 'jobs')
            for step in job['steps']:
                step['generation_enabled'] = True
                if step['status'] == 'waiting' and step.get('reference_ids') != [asset['id']]:
                    step.update(reference_ids=[asset['id']], audit={'status': 'stale'})
                    step.pop('prompt', None)
                    step.pop('prompt_approval', None)
            if not job.get('scene_output_mode'):
                job['scene_output_mode'] = 'four' if len(job['steps']) == 4 else 'coverage'
            self.lib.save(job, 'jobs')
            row['production_route'] = 'build'
            self.invalidate_export(p, '环境参考已更新，当前交付路线保持；需核对交接')
            p.pop('export_pending', None)
            self.save(p)
            return self.detail(p['id'])

    def plan(self, ident, ast=None):
        with self.lib.lock:
            p = self.get(ident)
            self.fresh(p)
            if p.get('planning', {}).get('status') == 'running':
                raise ValueError('Codex 正在盘点需求')
            todo = [r for r in p['rows'] if not r.get('job_id') and not self.row_uses_wardrobe(r) and (not ast or r['requirement']['asset_id'] == ast)]
            if not todo:
                raise ValueError('当前可盘点需求均已有制作项；人物可先在卡片选择单图或身份板路线')
            exe = self.bridge.executable()
            if not exe:
                raise ValueError('未找到已登录的 Codex CLI')
            p['planning'] = {'status': 'running', 'at': now(), 'run_id': uid()}
            self.save(p)
            threading.Thread(target=self._plan_worker, args=(exe, copy.deepcopy(p), todo), daemon=True).start()
            return {'message': 'Codex 正在按项目 Skill 盘点参考、判断实际覆盖与环境余量'}

    def _plan_worker(self, exe, p, rows):
        folder = self.lib.root / 'requests' / p['id'] / ('plan-' + p['planning']['run_id'])
        try:
            refs = []
            for row in rows:
                req = row['requirement']
                for path in req.get('reference_paths', []):
                    source = project_path(Path(p['path']), path)
                    if source.is_file() and source.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}:
                        refs.append(str(source))  # Project files stay in the project.
                refs += [str(self.copy_to_project(p, self.lib.get(i))) for i in row['reference_ids']]
            request = {'mode': 'project', 'project_path': p['path'], 'source_narrative': p['source'],
                       'narrative': prompt_context(p['narrative'], [r['requirement']['asset_id'] for r in rows]),
                       'kind': self.row_kinds(rows[0])[0], 'codex_model': 'gpt-6-luna', 'reference_paths': list(dict.fromkeys(refs)),
                       'rows': rows, 'inventory': [{k: a.get(k) for k in ('id', 'name', 'category', 'status', 'tags', 'project')}
                                                for a in self.lib.items() if not a.get('trashed_at')]}
            request.update(reference_evidence(p['path'], [r['requirement'] for r in rows],
                                              [self.lib.get(i) for r in rows for i in r['reference_ids']]))
            rules, evidence = self.skill.prepare(folder, request)
            result = self.bridge.plan_assets(exe, folder, request, rules)
            with self.lib.lock:
                current = self.get(p['id'])
                self.fresh(current)
                if current['source'] != p['source']:
                    raise ValueError('盘点期间上游变化，请重新盘点')
                self.apply_plan(current, rows, result, evidence)
        except Exception as exc:
            with self.lib.lock:
                current = self.get(p['id'])
                current['planning'] = {'status': 'error', 'message': str(exc)[:600]}
                self.save(current)

    def apply_plan(self, p, rows, result, evidence):
        """Validate the entire response before creating jobs. No model-generated AST IDs."""
        incoming = result['rows']
        if {r['ast_id'] for r in incoming} != {r['requirement']['asset_id'] for r in rows} or len(incoming) != len(rows):
            raise ValueError('Codex 返回的需求编号不完整或包含表外项')
        jobs = []
        for planned in incoming:
            row = next(r for r in rows if r['requirement']['asset_id'] == planned['ast_id'])
            if self.row(p, planned['ast_id']).get('job_id') != row.get('job_id'):
                raise ValueError('盘点期间已选择资产，保留现有选图；不覆盖制作项')
            req = row['requirement']
            slots = planned['outputs']
            if not slots or len(slots) > 16:
                raise ValueError('每项需求须有 1 至 16 个必要输出；不能静默省略需求')
            if req['category'] != 'PROP' and production_kinds(req) != ['角色外观'] and not set(req['required_states']).issubset({s['state'] for s in slots}):
                raise ValueError('制作项未完整覆盖 AST 所需状态，请重新盘点')
            seen = set()
            for s in slots:
                if s['key'] in seen or not re.fullmatch('[a-zA-Z0-9_-]{1,40}', s['key']):
                    raise ValueError('制作项编号无效或重复')
                if s['kind'] not in self.row_kinds(row) or not re.fullmatch(r'[1-9][0-9]?:[1-9][0-9]?', s['ratio']):
                    raise ValueError('制作项类别或比例与 AST 不符')
                if any(d not in seen for d in s['dependencies']):
                    raise ValueError('制作项前置关系必须按顺序、无环')
                if not s['rationale'].strip() or not s['purpose'].strip():
                    raise ValueError('制作项缺少用途或启用理由')
                if s['kind'] == '大头照':
                    s['ratio'] = '3:4'
                if s['kind'] == '物品':
                    s['view'] = '正面、左侧、背面、俯视四视图'
                if s['kind'] == '角色外观':
                    s['view'] = '宠物三视图 · 正面、左侧、背面'
                    s['purpose'] += '；同一完整角色的正面、左侧、背面三视图，包含固定服饰与配件'
                if s['kind'] in {'服装', '人物1x4', '物品', '角色外观'}:
                    s['ratio'] = '16:9'
                seen.add(s['key'])
            scenes = [s for s in slots if s['kind'] == '环境']
            if len(scenes) > 1 or (scenes and row.get('scene_output_mode') == 'four'):
                groups = {}
                for s in scenes:
                    groups.setdefault(s['state'], []).append(s)
                for group in groups.values():
                    if len(group) == 1 and row.get('scene_output_mode') != 'four':
                        continue
                    four_views = [s['view'] for s in group] == ['45°', '正面', '反面', '俯视']
                    if row.get('scene_output_mode') == 'four' and not four_views:
                        raise ValueError('已选择四图路线，不能静默减少所选场景视角')
                    if four_views and not any(s['dependencies'] for s in group):
                        for i, s in enumerate(group):
                            s['dependencies'] = [a['key'] for a in group[:i]]
            # Validate every output, including partially specified standard four-view
            # plans. Sparse explicit graphs stay intact; no source is inferred here.
            for scene in scenes:
                self.validate_scene_sources({'steps': slots}, scene)
            steps = []
            for s in slots:
                recipe = self.skill.recipe(s['kind'])
                steps.append({**s, 'name': (s['kind'] + ('四视图' if s['kind'] == '物品' else '主图') + (' · ' + s['state'] if s.get('state') else '')) if s['kind'] in {'物品', '产品'} else s['view'], 'status': 'waiting', 'outputs': [],
                              'generation_enabled': row.get('production_route') != 'reuse',
                              'reference_ids': row['reference_ids'][:], 'project_confirmation': None,
                              'template_source': recipe.get('template') or '依据分类指南编写：' + recipe['guide']})
            job = {'id': uid(), 'title': req['name'], 'kind': slots[0]['kind'], 'source_id': None,
                   'provider': 'imagegen', 'model': '5.0', 'notes': '', 'workflow_version': 4,
                   'created_at': now(), 'project_id': p['id'], 'project_name': p['name'], 'ast_id': req['asset_id'],
                   'source_narrative': p['source'],
                   'requirement': copy.deepcopy(req), 'steps': steps, 'planning_evidence': evidence,
                   'character_output_mode': row.get('character_output_mode'),
                   'scene_output_mode': row.get('scene_output_mode'),
                   'plan_rationale': planned['rationale'], 'blockers': planned['blockers']}
            jobs.append((row, job))
        for row, job in jobs:
            self.lib.save(job, 'jobs')
            target = self.row(p, job['ast_id'])
            target.update(job_id=job['id'], reference_ids=row['reference_ids'])
        p['planning'] = {'status': 'done', 'at': now(), 'evidence': evidence}
        self.save(p)

    def context(self, job, index, project_id=None):
        if project_id is not None and project_id != job.get('project_id'):
            raise ValueError('制作项不属于当前项目，请刷新后重试')
        p = self.get(job['project_id'])
        self.fresh(p)
        row = self.row(p, job['ast_id'])
        if row.get('job_id') != job['id'] or job.get('project_stale'):
            raise ValueError('此制作项属于旧版需求，请使用当前需求表')
        if not 0 <= index < len(job['steps']):
            raise ValueError('无效制作项')
        return p, row, job['steps'][index]

    def dependency_basis(self, job, step):
        basis = []
        for key in step['dependencies']:
            prior = next(s for s in job['steps'] if s['key'] == key)
            receipt = prior.get('project_confirmation') or {}
            basis.append({'output_key': key, 'asset_id': prior.get('selected'),
                          'confirmation_id': receipt.get('id') if prior['status'] == 'confirmed' else None})
        return basis

    def dependent_keys(self, job, keys):
        keys = set(keys)
        while True:
            expanded = keys | {s['key'] for s in job['steps'] if any(d in keys for d in s['dependencies'])}
            if expanded == keys:
                return keys
            keys = expanded

    def reset_steps(self, job, keys):
        for step in job['steps']:
            if step['key'] not in keys:
                continue
            step.setdefault('history', []).append({k: copy.deepcopy(step.get(k)) for k in (
                'selected', 'outputs', 'project_confirmation', 'prompt', 'audit',
                'generation_run_id', 'submit_id', 'authorization')})
            step.update(status='waiting', outputs=[], project_confirmation=None, audit={'status': 'stale'})
            step.pop('selected', None)
            for key in ('prompt_approval', 'generation_run_id', 'submit_id', 'authorization',
                        'query_failures', 'query_blocked', 'query_retry_after', 'composition_error', 'message'):
                step.pop(key, None)

    @staticmethod
    def validate_scene_sources(job, step):
        """Require a real earlier scene source for each supplemental view.

        Validate saved jobs as well as new plans. Trace topology for safety, but
        do not turn that lineage into additional model-upload references.
        """
        if step.get('kind') != '环境':
            return
        ordered = job['steps']
        by_key = {item['key']: (index, item) for index, item in enumerate(ordered)}
        if len(by_key) != len(ordered):
            raise ValueError('场景来源图谱编号重复，请核对原制作计划')
        pending, checked = [step], set()
        while pending:
            current = pending.pop()
            key = current.get('key')
            if key in checked:
                continue
            checked.add(key)
            if key not in by_key or current.get('kind') != '环境':
                raise ValueError('场景来源必须指向当前AST的环境制作项')
            index = by_key[key][0]
            deps = current.get('dependencies')
            if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
                raise ValueError('场景来源图谱字段无效，请核对原制作计划')
            earlier_same_state = any(item.get('kind') == '环境' and item.get('state') == current.get('state')
                                     for item in ordered[:index])
            if earlier_same_state and not deps:
                raise ValueError('补充场景视角缺少已确认空间来源；请核对母图或前置视图依赖，不能独立重设计空间')
            for dep in deps:
                prior = by_key.get(dep)
                if not prior or prior[0] >= index or prior[1].get('kind') != '环境':
                    raise ValueError('场景来源须绑定当前AST中更早的环境制作项，不得前向、循环或跨类别引用')
                pending.append(prior[1])

    def validate_dependency_confirmation(self, job, step):
        self.validate_scene_sources(job, step)
        receipt = step.get('project_confirmation') or {}
        if receipt:
            asset = self.lib.get(step['selected'], require_file=True)
            verify_image_confirmation(asset['original'], receipt)
        if (receipt and step.get('kind') == '环境' and step['dependencies'] and
                'dependency_confirmations' not in receipt):
            raise ValueError('旧角度确认缺少前置图片版本依据，请重新核对此制作项：' + step['name'])
        if ('dependency_confirmations' in receipt and
                receipt['dependency_confirmations'] != self.dependency_basis(job, step)):
            raise ValueError('前置图片的确认版本已变化，请重新核对此制作项：' + step['name'])

    def validate_export_basis(self, project):
        for row in project['rows']:
            if not row.get('job_id'):
                raise ValueError('尚未完成：' + row['requirement']['asset_id'])
            job = self.lib.get(row['job_id'], 'jobs')
            if job.get('project_id') != project['id'] or job.get('project_stale'):
                raise ValueError('此制作项属于旧版需求，请使用当前需求表')
            for step in job['steps']:
                if step.get('required') is False:
                    continue
                if step['status'] != 'confirmed' or not step.get('project_confirmation'):
                    raise ValueError('尚未逐项确认：' + job['ast_id'] + ' / ' + step['name'])
                self.dependencies(job, step)
                asset = self.lib.get(step['selected'], require_file=True)
                if asset.get('backup', {}).get('status') == 'saved':
                    verify_image_confirmation(asset['backup']['path'], step['project_confirmation'])
        if project.get('export'):
            # The project copy is independent of the Vault original. Check its
            # own receipt too before returning a previously cached handoff.
            from core.asset_refs import _confirmed_output
            root = Path(project['path'])
            exported = project['export']
            state = load_project_state(root)
            entries = [entry for entry in load_manifest(root)['artifacts']
                       if entry.get('artifact_id') == exported['artifact_id']]
            if (len(entries) != 1 or entries[0].get('logical_id') != 'visual.asset.manifest'
                    or entries[0]['artifact_id'] not in state['current_artifacts']
                    or not artifact_is_confirmed(entries[0], state, root)):
                raise ValueError('资产导出登记已失效或缺少整表确认，请重新核对交接')
            path = project_path(root, exported['manifest'])
            if (project_path(root, entries[0]['path_or_url']) != path or not path.is_file()
                    or (entries[0].get('checksum') and digest(path) != entries[0]['checksum'])):
                raise ValueError('已登记资产导出清单内容已变化或缺失，请保留原记录并重新核对交接')
            manifest = read(path)
            outputs = [(asset['asset_id'], output) for asset in manifest['assets']
                       for output in asset.get('confirmed_outputs', [])]
            if (len(outputs) != project['export']['count'] or any(
                    _confirmed_output(root, ast_id, output) is None for ast_id, output in outputs)):
                raise ValueError('已导出确认图片内容已变化或缺失，请保留原记录并重新核对交接')

    def dependencies(self, job, step):
        self.validate_dependency_confirmation(job, step)
        refs = []
        for key in step['dependencies']:
            prior = next(s for s in job['steps'] if s['key'] == key)
            if not prior.get('project_confirmation') or prior['status'] != 'confirmed':
                raise ValueError('先在项目表中确认前置制作项：' + prior['name'])
            self.validate_dependency_confirmation(job, prior)
            asset = self.lib.get(prior['selected'], require_file=True)
            if asset.get('trashed_at'):
                raise ValueError('前置图片已删除，请先恢复')
            refs.append(asset)
        return refs

    def copy_to_project(self, project, asset):
        """Give a task its own verified copy of a Vault-owned original."""
        source = Path(asset['original']).resolve()
        verify_image_confirmation(source, asset.get('confirmation') or {})
        vault_root = (self.lib.vault / '资产库').resolve()
        if asset.get('reference_only') and asset['provenance'].get('project_id') == project['id']:
            reference_root = Path(project['path']).resolve() / '04_资产' / '项目参考'
            if source.is_file() and source.is_relative_to(reference_root):
                return source
        if not source.is_file() or not source.is_relative_to(vault_root):
            raise ValueError('选图原文件不在知识库，无法交给项目：' + asset['name'])
        project_root = Path(project['path']).resolve()
        destination = project_root / '04_资产' / '知识库参考' / asset['id'] / source.name
        if not destination.resolve().is_relative_to(project_root):
            raise ValueError('项目取图目标路径越界')
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if not visual_asset_backup.same_file(source, destination):
                raise ValueError('项目参考副本与知识库原图不同，已保留两边文件：' + str(destination))
        else:
            shutil.copy2(source, destination)
            if not visual_asset_backup.same_file(source, destination):
                destination.unlink()
                raise ValueError('复制到项目后的文件校验失败：' + asset['name'])
        return destination

    def request(self, job, index):
        p, row, step = self.context(job, index)
        if automatic_final(job, step):
            raise ValueError('最终人物1×4由已确认大头照和服装三视图自动合成，无需模型生成')
        if image_decision(row['requirement']) != '需要制作':
            raise ValueError('此需求未列为需要制作；复用项只选图，需新制时先更新需求判断')
        if step.get('required') is False or step.get('generation_enabled') is False:
            raise ValueError('此项采用已有图片；如需生图，请先切换为新制作')
        if job.get('blockers'):
            raise ValueError('需求尚有缺口：' + '；'.join(job['blockers']))
        if job.get('character_workflow') and step['kind'] == '服装':
            head = next(s for s in job['steps'] if s['key'] == 'shared_head')
            if not head.get('project_confirmation') or head['status'] != 'confirmed':
                raise ValueError('请先选择或生成并确认共用大头照，再生成服装三视图')
        refs = self.dependencies(job, step)
        if job.get('character_workflow') and step['kind'] == '服装':
            refs.append(self.lib.get(head['selected']))
        refs += [self.lib.get(i) for i in step.get('reference_ids', []) if i not in {a['id'] for a in refs}]
        if any(a.get('trashed_at') for a in refs):
            raise ValueError('参考图片已删除')
        if not refs and row['requirement']['category'] in {'WARDROBE', 'REAL_PRODUCT'}:
            raise ValueError('此类资产须选择真实参考图，不能猜外观')
        if step['kind'] == '人物1x4':
            ordered = []
            for category in ['服装三视图', '大头照']:
                asset = next((a for a in refs if a['category'] == category and a['status'] == 'confirmed'), None)
                if asset is None:
                    raise ValueError('人物1×4需要已确认服装三视图和大头照，参考顺序为服装、脸')
                ordered.append(asset)
            refs = ordered
        recipe = self.skill.recipe(step['kind'])
        template = recipe.get('template') or '依据分类指南编写：' + recipe['guide']
        seed = '依据当前 AST 和分类指南，制作' + step['purpose'] + '；状态：' + step['state'] + '；视角：' + step['view']
        if step['kind'] == '环境' and (not refs or step['view'] not in {'45°', '正面', '反面', '俯视'}
                or len(step.get('dependencies', [])) != {'45°': 0, '正面': 1, '反面': 2, '俯视': 3}.get(step['view'], -1)):
            template = str(ROOT / 'skills/asset-image-assistant/references' / recipe['guide'])
        elif 'template' in recipe:
            # Reuse the same knowledge-base templates, without imposing unrelated recipes.
            view_index = {'45°': 0, '正面': 1, '反面': 2, '俯视': 3}.get(step['view'], 0) if step['kind'] == '环境' else 0
            seed, template = self.lib.project_template(step['kind'], job['title'], seed, view_index)
        if step['kind'] == '环境' and refs:
            seed += '；输入参考可能是俯视、平视或其他视角，不代表目标视角；保留已展示的布局与固定陈设，按本步骤目标视角制作，未展示部分仅作待确认的设计补全。'
        reference_records = []
        prerequisite_keys = set(step.get('dependencies', []))
        if job.get('character_workflow') and step['kind'] == '服装':
            prerequisite_keys.add('shared_head')
        provenance_steps = sorted(job['steps'], key=lambda s: s['key'] not in prerequisite_keys)
        for asset in refs:
            prior = next((s for s in provenance_steps if s.get('selected') == asset['id'] and
                          s.get('project_confirmation') and s['status'] == 'confirmed'), None)
            reference_records.append({**{k: asset.get(k) for k in ('id', 'category', 'status', 'width', 'height', 'bytes')},
                'original': str(self.copy_to_project(p, asset)), 'project_confirmed': bool(prior),
                'project_output_key': prior['key'] if prior else None,
                'project_confirmation_id': prior['project_confirmation'].get('id') if prior else None,
                'role': ('已确认大头照，唯一面部身份来源' if prior and prior['kind'] == '大头照' else
                         '已确认服装三视图，服装及无头身体来源' if prior and prior['kind'] == '服装' else
                         '本项目已确认前置图片' if prior else '用户提供的生成参考，按可见内容采用')})
        return {'mode': 'project', 'project_path': p['path'], 'project_id': p['id'], 'ast_id': job['ast_id'],
                'source_narrative': p['source'], 'narrative': prompt_context(p['narrative'], [job['ast_id']]), 'requirement': row['requirement'],
                'job_id': job['id'], 'index': index, 'kind': step['kind'], 'view': step['view'], 'purpose': step['purpose'],
                'state': step['state'], 'character_outfit': next((v for v in job.get('character_workflow', {}).get('outfits', []) if v['id'] == step.get('outfit_id')), None), 'ratio': step['ratio'], 'provider': job['provider'],
                'model': job['model'] if job['provider'] == 'dreamina' else 'Codex 内置 ImageGen（宿主管理模型）',
                'prompt': step.get('prompt', seed), 'reference_paths': [str(self.copy_to_project(p, a)) for a in refs],
                'reference_records': reference_records,
                'template_source': template, 'count': 1, 'resolution': '2k',
                'must_inherit': row['requirement']['identity_constraints'], 'must_not_inherit': step.get('must_not_inherit', []),
                'codex_model': 'gpt-6-luna',
                'generation_direction': step.get('generation_direction', ''),
                'allow_unreferenced_design': (row['requirement']['category'] == 'PROP' or step['kind'] == '角色外观'
                    or (row['requirement']['category'] == 'CHARACTER' and step['kind'] in {'人物单图', '大头照', '服装'})),
                'design_scope': '新制作所需的未锁定外观可作为设计候选；保留已确认身份与故事约束，明确要求还原真实外貌时仍须参考。',
                'gap_resolution': job.get('resolved_blockers'),
                **reference_evidence(p['path'], [row['requirement']], refs)}

    def select(self, payload):
        with self.lib.lock:
            job = self.lib.get(payload['job_id'], 'jobs')
            p, row, step = self.context(job, int(payload['index']), payload.get('project_id'))
            if automatic_final(job, step):
                raise ValueError('此人物1×4由已确认大头照和服装自动合成，无需另选成品')
            if step['status'] in BUSY or step.get('audit', {}).get('status') in {'drafting', 'reviewing'}:
                raise ValueError('制作项正在处理，完成后再选择')
            if step.get('required') is False:
                raise ValueError('此步骤当前不需要，请先切换为新制作')
            asset = self.lib.get(payload['asset_id'], require_file=True)
            if asset.get('reference_only'):
                if not payload.get('as_reference'):
                    raise ValueError('此图仅作生成参考，请选择制作结果作为成品')
                if asset['provenance'].get('project_id') != p['id']:
                    raise ValueError('此参考图属于其他项目')
            if asset.get('trashed_at'):
                raise ValueError('图片已删除')
            if step['kind'] == '角色外观' and not payload.get('as_reference') and asset['category'] != '宠物':
                raise ValueError('请从宠物分类选择正面、侧面、背面三视图')
            if job.get('character_workflow') and not payload.get('as_reference'):
                expected = {'大头照': '大头照', '服装': '服装三视图', '人物1x4': '人物1x4'}[step['kind']]
                if asset['category'] != expected:
                    raise ValueError('此步骤请选择' + expected + '资产')
            if payload.get('as_reference'):
                if step['status'] != 'waiting':
                    raise ValueError('已有结果不能更换生成参考，请先重做')
                step['reference_ids'] = list(dict.fromkeys(step.get('reference_ids', []) + [asset['id']]))
                step['audit'] = {'status': 'stale'}
                step.pop('prompt_approval', None)
            else:
                self.dependencies(job, step)
                if step.get('project_confirmation'):
                    raise ValueError('已确认图片保留原版本；先点击重新选图')
                step.update(selected=asset['id'], status='awaiting_review')
                if job.get('direct_reuse') and step.get('generation_enabled') is False:
                    step['ratio'] = str(asset['width']) + ':' + str(asset['height'])
                if asset['id'] not in step['outputs']:
                    step['outputs'].append(asset['id'])
            self.copy_to_project(p, asset)
            if step['kind'] == '人物1x4' and not payload.get('as_reference'):
                self.lib.auto_group_character(asset['id'], f'project:{p["id"]}:{job["ast_id"]}', job['title'])
            self.lib.save(job, 'jobs')
            return self.detail(p['id'])

    def resolve(self, payload):
        with self.lib.lock:
            job = self.lib.get(payload['job_id'], 'jobs')
            p, _, _ = self.context(job, 0, payload.get('project_id'))
            text = str(payload.get('explanation', '')).strip()
            if len(text) < 4:
                raise ValueError('请具体说明补充的事实或参考')
            if any(s['status'] != 'waiting' or s.get('audit', {}).get('status') in {'drafting', 'reviewing'} for s in job['steps']):
                raise ValueError('请在开始制作前补充缺口；已有结果时可核对图片后逐项确认')
            job['resolved_blockers'] = {'items': job['blockers'], 'explanation': text, 'at': now()}
            job['blockers'] = []
            for step in job['steps']:
                step['audit'] = {'status': 'stale'}
                step.pop('prompt_approval', None)
            self.lib.save(job, 'jobs')
            return self.detail(p['id'])

    def clear_references(self, payload):
        with self.lib.lock:
            job = self.lib.get(payload['job_id'], 'jobs')
            p, _, step = self.context(job, int(payload['index']), payload.get('project_id'))
            if step['status'] != 'waiting' or step.get('audit', {}).get('status') in {'drafting', 'reviewing'}:
                raise ValueError('此项正在处理或已有结果，不能清除参考')
            step.update(reference_ids=[], audit={'status': 'stale'})
            step.pop('prompt_approval', None)
            self.lib.save(job, 'jobs')
            return self.detail(p['id'])

    def confirm(self, payload):
        with self.lib.lock:
            job = self.lib.get(payload['job_id'], 'jobs')
            p, _, step = self.context(job, int(payload['index']), payload.get('project_id'))
            self.dependencies(job, step)
            ident = payload.get('asset_id') or step.get('selected')
            if ident not in step['outputs'] or payload.get('confirm') is not True:
                raise ValueError('请明确选择并确认本制作项的一张图片')
            if step.get('project_confirmation') or step['status'] == 'confirmed':
                if step.get('project_confirmation') and step['status'] == 'confirmed' and step.get('selected') == ident:
                    compose_character_finals(self, p, job)
                    return self.detail(p['id'])
                raise ValueError('已确认图片保留原版本；请先点击重新选图 / 返工，再确认其他图片')
            if step['status'] != 'awaiting_review':
                raise ValueError('只有待核对的图片可以确认，请刷新制作项状态')
            asset = self.lib.get(ident, require_file=True)
            if asset.get('reference_only'):
                raise ValueError('项目参考图不能确认为制作结果')
            if asset.get('trashed_at'):
                raise ValueError('图片已删除')
            check = self.skill.technical_check(asset['original'], step['ratio'])
            if not check['passed'] or check['format'] not in {'PNG', 'JPEG', 'WEBP'}:
                raise ValueError('技术检查不通过：' + check['message'])
            if job.get('blockers') and payload.get('resolve_blockers') is not True:
                raise ValueError('请逐项核对并确认需求缺口已解决：' + '；'.join(job['blockers']))
            if job.get('blockers'):
                job['resolved_blockers'] = {'items': job['blockers'], 'at': now(), 'method': '用户核对实际选图并确认缺口已解决'}
                job['blockers'] = []
            receipt = {'id': uid(), 'at': now(), 'asset_id': ident, 'source': p['source'], 'method': '项目制作格逐项视觉确认', 'technical_qc': check,
                       'image_sha256': digest(Path(asset['original'])),
                       'dependency_confirmations': self.dependency_basis(job, step)}
            step.update(selected=ident, status='confirmed', project_confirmation=receipt)
            asset.update(status='confirmed', confirmation=receipt)
            asset.setdefault('project_confirmations', []).append({'project_id': p['id'], 'ast_id': job['ast_id'], **receipt})
            asset['backup'] = {'status': 'pending'}
            self.lib.save(asset)
            self.lib.save(job, 'jobs')
            self.lib.backup_wakeup.set()
            compose_character_finals(self, p, job)
            return self.detail(p['id'])

    def compose_character(self, payload):
        with self.lib.lock:
            job = self.lib.get(payload['job_id'], 'jobs')
            p, _, step = self.context(job, int(payload['index']), payload.get('project_id'))
            if not automatic_final(job, step):
                raise ValueError('此步骤不是人物自动合成项')
            self.dependencies(job, step)
            compose_character_finals(self, p, job)
            return self.detail(p['id'])

    def reopen(self, payload):
        with self.lib.lock:
            job = self.lib.get(payload['job_id'], 'jobs')
            p, _, step = self.context(job, int(payload['index']), payload.get('project_id'))
            keys = self.dependent_keys(job, {step['key']})
            if any(s['status'] in BUSY or s.get('audit', {}).get('status') in {'drafting', 'reviewing'} for s in job['steps'] if s['key'] in keys):
                raise ValueError('关联图片仍在生成，请等待后再返工')
            self.reset_steps(job, keys)
            self.lib.save(job, 'jobs')
            self.invalidate_export(p, '用户重新选择资产，相关制作项待再次确认')
            p.pop('export_pending', None)
            self.save(p)
            return self.detail(p['id'])

    def batch(self, payload):
        with self.lib.lock:
            p = self.get(payload['project_id'])
            self.fresh(p)
            if self.batch_running:
                raise ValueError('已有批次正在执行，请等本批完成')
            mode = payload['mode']
            if mode not in {'prepare', 'write', 'review', 'generate'}:
                raise ValueError('无效批次类型')
            tasks = payload['tasks']
            if not tasks or len(tasks) > 100 or len({(t['job_id'], int(t['index'])) for t in tasks}) != len(tasks):
                raise ValueError('请勾选 1 至 100 个不同制作项')
            provider, model = payload.get('provider', 'imagegen'), payload.get('model', '5.0')
            if provider not in {'imagegen', 'dreamina'} or model not in ['5.0', '5.0Pro', '4.7', '4.6', '4.5', '4.1', '4.0']:
                raise ValueError('工具或模型无效')
            entries = []
            updates = []
            for t in tasks:
                job = self.lib.get(t['job_id'], 'jobs')
                if job.get('project_id') != p['id']:
                    raise ValueError('不能混用其他项目任务')
                index = int(t['index'])
                step = job['steps'][index]
                if automatic_final(job, step):
                    raise ValueError('最终人物1×4由已确认来源自动合成，不加入模型生成批次')
                if step['status'] != 'waiting' or step.get('audit', {}).get('status') in {'drafting', 'reviewing'}:
                    raise ValueError('只可提交待制作且未在处理的项目')
                direction = t.get('generation_direction', step.get('generation_direction', ''))
                if not isinstance(direction, str) or len(direction) > 2000:
                    raise ValueError('生成方向请控制在 2000 字以内')
                direction = direction.strip()
                changed = direction != step.get('generation_direction', '')
                if changed and mode == 'generate':
                    raise ValueError('生成方向已变化，请重新准备提示词')
                if changed:
                    step['generation_direction'] = direction
                    step['audit'] = {}
                    step.pop('prompt_approval', None)
                try:
                    request = self.request(job, index)
                except (ValueError, FileNotFoundError) as exc:
                    if mode == 'generate':
                        raise
                    step['audit'] = {'status': 'error', 'message': str(exc)[:500], 'finished_at': now()}
                    step.pop('prompt_approval', None)
                    self.lib.save(job, 'jobs')
                    entries.append({'job_id': job['id'], 'index': index, 'status': 'error',
                                    'message': str(exc)[:500]})
                    continue
                if changed:
                    updates.append((job['id'], index, direction))
                if mode == 'generate':
                    if payload.get('authorize') is not True or not self.skill.approved(step) or t.get('prompt') != step.get('prompt'):
                        raise ValueError('请审查通过并确认本批全部最终提示词、工具、模型和额度消耗')
                    if step.get('audit', {}).get('prompt') != step.get('prompt'):
                        raise ValueError('提示词修改后需要重新审查')
                operation = 'write' if changed or not step.get('prompt') else 'repair' if step.get('audit', {}).get('status') in {'needs_changes', 'error'} else 'review'
                entries.append({'operation': operation if mode == 'prepare' else mode, 'job_id': job['id'], 'index': index, 'prompt': step.get('prompt'),
                                'request': request, 'status': 'queued'})
            for job_id, index, direction in updates:
                job = self.lib.get(job_id, 'jobs')
                job['steps'][index]['generation_direction'] = direction
                job['steps'][index]['audit'] = {}
                job['steps'][index].pop('prompt_approval', None)
                self.lib.save(job, 'jobs')
            p['batch'] = {'id': uid(), 'mode': mode, 'status': 'running', 'entries': entries,
                          'provider': provider, 'model': model, 'started_at': now(),
                          'authorized_at': now() if mode == 'generate' else None}
            self.save(p)
            self.batch_running = True
            threading.Thread(target=self._batch_worker, args=(p['id'],), daemon=True).start()
            return {'message': f'已检查 {len(entries)} 项，最多 3 项并行；' + ('生成失败不重复提交' if mode == 'generate' else 'Luna自动修复提示词最多一次，缺失素材单独列明')}

    def _batch_worker(self, ident):
        try:
            batch = self.get(ident)['batch']
            def run_entry(item):
                n, entry = item
                if entry['status'] == 'error':
                    return
                try:
                    with self.lib.lock:
                        p = self.get(ident)
                        self.fresh(p)
                        job = self.lib.get(entry['job_id'], 'jobs')
                        step = job['steps'][entry['index']]
                        current_request = self.request(job, entry['index'])
                        if any(current_request.get(k) != v for k, v in entry['request'].items() if k not in {'model', 'provider'}):
                            raise ValueError('排队期间输入已变化，请重新检查并提交此项')
                        p['batch']['entries'][n].update(status='running', started_at=now())
                        self.save(p)
                        if batch['mode'] == 'generate':
                            job.update(provider=batch['provider'], model=batch['model'])
                            step['prompt_approval'] = {'at': batch['authorized_at'], 'prompt': step['prompt'], 'batch_id': batch['id']}
                            self.lib.save(job, 'jobs')
                            action = self.lib.submit_imagegen if batch['provider'] == 'imagegen' else self.lib.submit
                            action({'id': job['id'], 'index': entry['index'], 'authorize': True})
                        else:
                            self.lib.audit_prompt({'id': job['id'], 'index': entry['index'], 'mode': entry.get('operation', batch['mode'])})
                    deadline = time.monotonic() + 1800
                    next_query_at = 0.0
                    while True:
                        step = self.lib.get(entry['job_id'], 'jobs')['steps'][entry['index']]
                        if batch['mode'] == 'generate':
                            if step['status'] == 'submitted':
                                if step.get('query_blocked'):
                                    raise ValueError(step.get('message', '自动查询已暂停，请核实原任务'))
                                if time.monotonic() >= next_query_at:
                                    self.lib.query(entry['job_id'], entry['index'], automatic=True)
                                    next_query_at = time.monotonic() + step.get('query_retry_after', 8)
                            elif step['status'] not in BUSY:
                                if step['status'] not in {'awaiting_review', 'confirmed'}:
                                    raise ValueError(step.get('message', '生成未完成，请核实；未重试'))
                                break
                        elif step.get('audit', {}).get('status') not in {'drafting', 'reviewing'}:
                            if step['audit']['status'] != 'passed':
                                raise ValueError('；'.join(step['audit'].get('issues') or []) or step['audit'].get('message') or '提示词审查未通过，请修改')
                            break
                        if time.monotonic() > deadline:
                            raise ValueError('等待超时，当前项需核实，未重复提交')
                        time.sleep(1)
                    status, message = 'done', '已完成'
                except Exception as exc:
                    status, message = 'error', str(exc)[:500]
                with self.lib.lock:
                    p = self.get(ident)
                    p['batch']['entries'][n].update(status=status, message=message, finished_at=now())
                    self.save(p)
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix='asset-batch') as pool:
                list(pool.map(run_entry, enumerate(batch['entries'])))
            with self.lib.lock:
                p = self.get(ident)
                p['batch'].update(status='done', finished_at=now())
                self.save(p)
        except Exception as exc:
            with self.lib.lock:
                p = self.get(ident)
                p['batch'].update(status='error', message='本批处理已停止：' + str(exc)[:300], finished_at=now())
                for entry in p['batch']['entries']:
                    if entry['status'] in {'queued', 'running'}:
                        entry.update(status='error', message=p['batch']['message'], finished_at=now())
                self.save(p)
        finally:
            self.batch_running = False

    def recover(self):
        with self.lib.lock:
            for s in self.summaries():
                p = self.get(s['id'])
                if p.get('planning', {}).get('status') == 'running':
                    p['planning'] = {'status': 'error', 'message': '服务重启中断盘点，请重新发起'}
                if p.get('product_reference_check', {}).get('status') == 'running':
                    p['product_reference_check']['status'] = 'error'
                    p['product_reference_check']['message'] = '服务重启中断产品动作核对，请重新核对'
                if p.get('batch', {}).get('status') == 'running':
                    p['batch']['status'] = 'interrupted'
                    for entry in p['batch']['entries']:
                        if entry['status'] in {'running', 'queued'}:
                            entry.update(status='interrupted', message='服务重启，未自动重新生成；请核实结果')
                self.save(p)

    def export(self, payload):
        if payload.get('confirm') is not True:
            raise ValueError('请确认整张需求表后交接')
        with self.lib.lock:
            p = self.get(payload['project_id'])
            with project_lock(Path(p['path'])):
                return self._export_locked(payload)

    def _export_locked(self, payload):
        if payload.get('confirm') is not True:
            raise ValueError('请确认整张需求表后交接')
        with self.lib.lock:
            p = self.get(payload['project_id'])
            self.fresh(p)
            if p.get('export'):
                self.validate_export_basis(p)
                return p['export']
            requirements = p['narrative']['asset_requirements']
            if any(image_decision(r) == '待定' for r in requirements):
                raise ValueError('资产图需求仍有待定项，先补齐判断再交接')
            if not p['rows'] and not requirements and not p['narrative'].get('_asset_choices'):
                raise ValueError('项目没有资产需求，无需生成资产交接')
            collected = []
            for row in p['rows']:
                if not row.get('job_id'):
                    raise ValueError('尚未完成：' + row['requirement']['asset_id'])
                job = self.lib.get(row['job_id'], 'jobs')
                if self.row_uses_wardrobe(row):
                    expected = upstream_outfits(row['requirement'])
                    finals = [s for s in job['steps'] if s['kind'] == '人物1x4' and s.get('required') is not False]
                    if len(finals) != len(expected):
                        raise ValueError('人物服装套数与上游不一致，请打开卡片重新对应：' + job['title'])
                if job.get('blockers'):
                    raise ValueError('仍有需求缺口：' + job['ast_id'])
                for i, step in enumerate(job['steps']):
                    if step.get('required') is False:
                        continue
                    self.context(job, i)
                    self.dependencies(job, step)
                    if step['status'] != 'confirmed' or not step.get('project_confirmation'):
                        raise ValueError('尚未逐项确认：' + job['ast_id'] + ' / ' + step['name'])
                    asset = self.lib.get(step['selected'])
                    if asset.get('reference_only'):
                        raise ValueError('项目参考图不能作为正式交付：' + step['name'])
                    if asset.get('trashed_at') or not Path(asset['original']).is_file():
                        raise ValueError('选定图片已删除或丢失：' + step['name'])
                    if asset.get('backup', {}).get('status') != 'saved':
                        raise ValueError('图片仍待知识库备份：' + asset['name'] + '。' +
                                         asset.get('backup', {}).get('message', '请先重试知识库备份'))
                    backup_path = Path(asset['backup']['path'])
                    if not backup_path.is_file() or backup_path.read_bytes() != Path(asset['original']).read_bytes():
                        raise ValueError('知识库备份缺失或与选图不一致，请先修复备份')
                    check = self.skill.technical_check(asset['original'], step['ratio'])
                    if not check['passed']:
                        raise ValueError('图片技术检查未通过')
                    collected.append((row, job, step, asset, check))
            root = Path(p['path'])
            manifest = load_manifest(root)
            logical = 'visual.asset.manifest'
            previous = [a for a in manifest['artifacts'] if a.get('logical_id') == logical]
            if not p.get('export_pending'):
                serial = max([int(a['revision'][1:]) for a in previous if re.fullmatch(r'R\d+', a.get('revision', ''))] or [0]) + 1
                p['export_pending'] = {'id': uid(), 'revision': f'R{serial:02d}', 'at': now()}
                self.save(p)
            transaction = p['export_pending']
            revision = transaction['revision']
            asset_dir = '06_参考帧与临时素材/资产母版' if (root / '06_参考帧与临时素材').is_dir() and not (root / '04_资产').is_dir() else '04_资产/母版'
            folder = root / asset_dir / (revision + '_' + transaction['id'])
            folder.mkdir(parents=True, exist_ok=True)
            receipt_path = folder / '用户确认记录.json'
            path = folder / 'visual-asset-manifest.json'
            receipt = {'id': transaction['id'], 'at': transaction['at'],
                       'method': '逐项确认后整表确认交接',
                       'source_narrative': p['source'], 'items': []}
            if p['narrative'].get('_asset_table'):
                receipt['source_asset_table'] = p['narrative']['_asset_table']['source']
            paragraph_mode = p['narrative'].get('schema_version') == '2.0'
            out = {'schema_version': '2.0' if paragraph_mode else '1.1', 'project_id': p['name'], 'revision': revision,
                   'source_narrative': p['source'], 'source_asset_requirement_ids': [r['requirement']['asset_id'] for r in p['rows']],
                   'assets': [], 'scope_gaps': []}
            if paragraph_mode and p['narrative'].get('_product_usage'):
                out['product_usage'] = copy.deepcopy(p['narrative']['_product_usage'])
            if p['narrative'].get('_asset_choices'):
                out['asset_requirement_decisions'] = copy.deepcopy(p['narrative']['_asset_choices'])
                receipt['asset_requirement_decisions'] = copy.deepcopy(out['asset_requirement_decisions'])
            for row in p['rows']:
                req = row['requirement']
                record = {'asset_id': req['asset_id'], 'category': req['category'], 'name': req['name'],
                          'status': 'confirmed',
                          ('paragraph_scope' if paragraph_mode else 'event_scope'): req['source_paragraph_ids' if paragraph_mode else 'source_event_ids'], 'shot_scope': [],
                          'interactions': req['interactions'], 'display_surfaces': [], 'orientations': [],
                          'required_states': req['required_states'], 'must_inherit': req['identity_constraints'],
                          'must_not_inherit': [], 'reference_paths': [],
                          'candidates': [], 'confirmed_asset': None, 'confirmed_outputs': []}
                if req['category'] == 'CHARACTER':
                    record['character_type'] = character_type(req)
                for n, (_, job, step, asset, check) in enumerate([c for c in collected if c[1]['ast_id'] == req['asset_id']], 1):
                    target = folder / req['asset_id'] / (req['asset_id'] + '_' + safe(step['key']) + '_' + safe(step['view']) + '_' + revision + Path(asset['original']).suffix.lower())
                    target.parent.mkdir(exist_ok=True)
                    source = Path(asset['original'])
                    if target.exists() and target.read_bytes() != source.read_bytes():
                        raise ValueError('导出目标已有不同图片，保留现场：' + str(target))
                    if not target.exists():
                        shutil.copy2(source, target)
                    if source.read_bytes() != target.read_bytes():
                        raise ValueError('复制校验未通过')
                    verify_image_confirmation(target, step['project_confirmation'])
                    candidate_id = req['asset_id'] + f'-C{n:02d}'
                    confirm_ref = str(receipt_path) + '#' + step['project_confirmation']['id']
                    refs = list(dict.fromkeys(req['reference_paths'] + [self.lib.get(i)['original'] for i in step.get('reference_ids', [])]))
                    if not refs:
                        # The original import location is historical provenance;
                        # the verified project copy is the durable current input.
                        refs = [str(self.copy_to_project(p, asset))]
                    refs = list(dict.fromkeys(resolve_reference(root, r, manifest['artifacts'], load_project_state(root))
                                              for r in refs))
                    record['reference_paths'] = list(dict.fromkeys(record['reference_paths'] + refs))
                    provenance = asset.get('provenance') or {}
                    authorization = step.get('authorization') or {}
                    external = provenance.get('external_generation')
                    actual_provider = provenance.get('provider') or authorization.get('provider') or job['provider']
                    actual_model = (provenance.get('model') or authorization.get('model') or
                                    ('Codex 内置 ImageGen' if actual_provider == 'imagegen' else job['model']))
                    record['candidates'].append({'candidate_id': candidate_id, 'path': str(target),
                        'status': 'awaiting_user_review', 'source_references': refs or [asset['source']],
                        'technical_qc': {'path_exists': True, 'format': check['format'].lower(), 'width': check['width'],
                            'height': check['height'], 'naming_valid': True, 'reference_traceable': True,
                            'product_fact_check': 'passed' if req['category'] == 'REAL_PRODUCT' else 'not_applicable'},
                        'generation_record': dict(external) if external else ({'tool': actual_provider, 'model': actual_model,
                            'user_authorization_ref': str(receipt_path) + '#' + step['key']} if step.get('authorization') and provenance.get('kind') != 'manual_result_import' else None)})
                    confirmed = {'candidate_id': candidate_id, 'path': str(target), 'user_confirmation_ref': confirm_ref}
                    record['confirmed_outputs'].append({**confirmed, 'output_id': step['key'], 'purpose': step['purpose'],
                                                        'view': step['view'], 'state': step['state']})
                    prior_character_final = any(o['output_id'] in {s['key'] for s in job['steps'] if s['kind'] == '人物1x4'} for o in record['confirmed_outputs'][:-1])
                    if record['confirmed_asset'] is None or (job.get('character_workflow') and step['kind'] == '人物1x4' and not prior_character_final):
                        record['confirmed_asset'] = confirmed
                    record['orientations'].append(step['view'])
                    record['display_surfaces'].append(step['purpose'])
                    record['must_not_inherit'] += step.get('must_not_inherit', [])
                    receipt['items'].append({'ast_id': job['ast_id'], 'output_id': step['key'],
                                            'confirmation': step['project_confirmation'], 'library_asset_id': asset['id'],
                                            'prompt': step.get('prompt'), 'audit': step.get('audit'),
                                            'authorization': step.get('authorization'), 'provenance': provenance, 'backup': asset['backup']})
                out['assets'].append(record)
            validate(out, 'visual-asset-manifest.schema.json')
            # Everything is copied and validated before updating the project's official state.
            self.fresh(p)
            write(path, out)
            write(receipt_path, receipt)
            artifact_id = 'visual-assets-' + transaction['id']
            manifest = load_manifest(root)
            if not any(a['artifact_id'] == artifact_id for a in manifest['artifacts']):
                register_workspace_artifact(root, {'artifact_id': artifact_id, 'logical_id': logical,
                    'type': 'visual-asset-manifest', 'revision': revision, 'path_or_url': str(path),
                    'checksum': digest(path),
                    'depends_on': ['narrative.package'] + (['narrative.assets'] if p['narrative'].get('_asset_table') else []), 'user_approved': True, 'confirmation_ref': str(receipt_path)})
            manifest = load_manifest(root)
            mark_impacted_artifacts_stale(manifest, {logical}, '用户确认了新的资产表', revision)
            save_manifest(root, manifest)
            promote_candidate(root, logical, artifact_id)
            state = load_project_state(root)
            previous_ids = {a['artifact_id'] for a in previous}
            current = state.get('current_artifacts', [])
            state['current_artifacts'] = [a for a in current if a not in previous_ids | {artifact_id}]
            state['current_artifacts'].append(artifact_id)
            if not any(artifact_id in c.get('artifact_ids', []) for c in state['user_confirmations']):
                state['user_confirmations'].append({'at': transaction['at'], 'artifact_ids': [artifact_id],
                                                    'confirmation_ref': str(receipt_path), 'method': '资产网页整表确认'})
            # Asset work can run alongside planning; never overwrite its stage or next_stage.
            write_project_state(root, state)
            handoff = folder / '资产交接.md'
            handoff.write_text('# 项目资产交接\n\n- 正式清单：' + str(path) + '\n- 用户确认记录：' + str(receipt_path) +
                '\n- 上游版本：' + p['source']['revision'] + '\n\n仅使用清单中已确认的主图或 confirmed_outputs 对应用途、状态、视角的图片。\n'
                '下一阶段按项目状态读取；本文件不自动执行下一阶段。\n', encoding='utf-8')
            if out.get('asset_requirement_decisions'):
                choices = out['asset_requirement_decisions']
                with handoff.open('a', encoding='utf-8') as stream:
                    stream.write('\n## 用户资产图需求调整\n\n当前表：' + choices['path'] + '\n')
                    for ast, decision in choices['excluded'].items():
                        stream.write('- ' + ast + ' ' + decision['name'] + '：用户明确不需要这项资产图，不得作为缺图补回。\n')
                    for req in choices['added']:
                        if req['asset_id'] not in choices['excluded']:
                            stream.write('- ' + req['asset_id'] + ' ' + req['name'] + '：出现位置 ' + '、'.join(req.get('source_paragraph_ids', req.get('source_event_ids', []))) + '\n')
            p['export'] = {'folder': str(folder), 'manifest': str(path), 'handoff': str(handoff),
                           'artifact_id': artifact_id, 'revision': revision, 'at': now(), 'count': len(collected)}
            p.pop('export_pending', None)
            self.save(p)
            return p['export']

    def continue_project(self, ident):
        p = self.get(ident)
        self.fresh(p)
        if not p.get('export'):
            raise ValueError('请先完成整表确认和导出')
        self.validate_export_basis(p)
        state = load_project_state(Path(p['path']))
        target = state.get('next_stage')
        handoff = p['export']['handoff']
        warning = ''
        if target:
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/stage_context_cli.py'), 'handoff',
                '--project', p['path'], '--to-skill', target], capture_output=True, encoding='utf-8', errors='replace',
                timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                warning = '项目其他交接条件尚未满足：' + (result.stderr or result.stdout)[-500:]
            else:
                handoff = str(Path(p['path']) / '00_项目管理/执行上下文/next_stage_handoff.md')
        else:
            warning = '项目尚未指定下一阶段，请回到 Codex 核对项目状态后决定。'
        instruction = f'请继续项目「{p["path"]}」。先读取项目规则、项目状态和资产交接文件「{handoff}」。'
        instruction += ('按项目状态准备并校验 ' + target + ' 的启动包，通过输入门禁后继续这一阶段。') if target and not warning else '请先核对交接条件与下一阶段，不自行跳步。'
        instruction += '资产仅使用正式清单中的确认主图和用途匹配的 confirmed_outputs。'
        return {'handoff': handoff, 'content': Path(handoff).read_text(encoding='utf-8'), 'instruction': instruction, 'warning': warning}

    def upload_reference(self, payload):
        """Store a generation input in its project, preserving pixels and the source file."""
        import visual_asset_upload
        with self.lib.lock:
            p = self.get(payload['project_id'])
            self.fresh(p)
            if payload.get('job_id'):
                job = self.lib.get(payload['job_id'], 'jobs')
                current, row, step = self.context(job, int(payload['index']))
                if current['id'] != p['id'] or step['status'] != 'waiting':
                    raise ValueError('请在当前项目待制作步骤中上传参考图')
                if step.get('audit', {}).get('status') in {'drafting', 'reviewing'}:
                    raise ValueError('提示词正在处理，请完成后上传参考')
                category = {'环境': '场景', '服装': '服装三视图', '角色外观': '宠物'}.get(step['kind'], step['kind'])
            else:
                row = self.row(p, payload['ast_id'])
                if row['requirement']['category'] != 'LOCATION':
                    raise ValueError('此入口只接收环境参考')
                category = '场景'
            if p.get('planning', {}).get('status') == 'running' or p.get('batch', {}).get('status') == 'running':
                raise ValueError('项目正在处理，请完成后上传参考')
        paths = visual_asset_upload.pick_local_files(False)
        if not paths:
            return {'cancelled': True}
        source = Path(paths[0]).resolve()
        if source.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'} or source.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('只支持不超过 64 MB 的图片，电脑原文件保留')
        asset, _ = self.lib.import_file(source, project=p['name'], category=category, role='参考',
            provenance={'project_id': p['id'], 'ast_id': row['requirement']['asset_id'],
                        'confirmation_scope': 'reference_only'}, reference_project=p)
        if payload.get('job_id'):
            self.select({**payload, 'asset_id': asset['id'], 'as_reference': True})
        else:
            self.scene_reference({**payload, 'asset_id': asset['id']})
        return {'cancelled': False, 'asset_id': asset['id'],
                'message': '已放入项目参考，未加入资产收藏；电脑原文件保留'}

    def action(self, payload):
        if payload.get('job_id'):
            job = self.lib.get(payload['job_id'], 'jobs')
            if not payload.get('project_id') or payload['project_id'] != job.get('project_id'):
                raise ValueError('制作项不属于当前项目，请刷新后重试')
        if payload['action'] == 'upload-reference':
            return self.upload_reference(payload)
        if payload['action'] == 'relocate-reference':
            p = self.get(payload['project_id'])
            self.fresh(p)
            return self.lib.relocate_project_reference(payload['asset_id'], p)
        if payload['action'] == 'product-usage':
            with self.lib.lock:
                p = self.get(payload['project_id'])
                self.fresh(p)
                product_usage.schedule(self, p, force=True)
                return self.detail(p['id'])
        from visual_asset_choices import change
        actions = {'character-output-mode': lambda: self.character_output_mode(payload), 'scene-reference': lambda: self.scene_reference(payload), 'asset-choice': lambda: change(self, payload), 'scene-output-mode': lambda: self.scene_output_mode(payload), 'prepare-reuse': lambda: self.prepare_reuse(payload), 'prepare-character': lambda: prepare_character(self, payload), 'production-route': lambda: self.production_route(payload), 'character-route': lambda: choose_character_route(self, payload), 'bind': lambda: self.bind(payload['path']), 'detail': lambda: self.detail(payload['project_id']),
                   'sync': lambda: self.sync(payload['project_id']), 'references': lambda: self.references(payload),
                   'plan': lambda: self.plan(payload['project_id'], payload.get('ast_id')), 'select': lambda: self.select(payload),
                   'confirm': lambda: self.confirm(payload), 'reopen': lambda: self.reopen(payload),
                   'compose-character': lambda: self.compose_character(payload),
                   'clear-references': lambda: self.clear_references(payload), 'resolve': lambda: self.resolve(payload),
                   'batch': lambda: self.batch(payload), 'export': lambda: self.export(payload),
                   'continue': lambda: self.continue_project(payload['project_id'])}
        if payload.get('action') not in actions:
            raise ValueError('未知项目操作')
        return actions[payload['action']]()
