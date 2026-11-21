"""Project AST workbench. Project files remain authoritative; the library stores work in progress."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import errno
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from visual_asset_source import (asset_project_root, project_path, source_project, prompt_context, resolve_reference,
                                 local_reference_document, TEXT_REFERENCE_SUFFIXES)
from core.artifacts import artifact_is_adopted, cache_file_digests, digest, verify_image_confirmation
from visual_asset_choices import (image_work_items, image_decision, retained_specification,
                                  scene_state_coverage)
import visual_asset_product_usage as product_usage
from visual_asset_characters import (character_route, prepare_character, choose_character_route, upstream_outfits,
                                     character_final_ids, final_output_updates, retain_character_labels)
from core.character_assets import uses_wardrobe, production_kinds, character_type, final_output_problems, step_output_id
from core.asset_refs import PROP_BUILD_VIEW, PROP_REUSE_VIEW, character_output_contract, legacy_prop_reuse_label
from core.character_layout import BOARD_IMPORT_ONLY, character_layout_reference
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
import visual_asset_backup

CHARACTER_OUTPUT_MODES = {'人物单图': 'single', '人物1x4': 'wardrobe'}


def set_prop_layout(step, *, build):
    step['view'] = PROP_BUILD_VIEW if build else PROP_REUSE_VIEW
    step['name'] = '物品' + ('四视图' if build else '原图') + (' · ' + step['state'] if step.get('state') else '')
    if build:
        step['ratio'] = '16:9'

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))
from core.artifacts import (load_manifest, save_manifest, register_workspace_artifact,
                            promote_candidate, mark_impacted_artifacts_stale, asset_export_snapshot,
                            asset_output_impact)
from core.io import project_lock
from core.project_state import load_project_state, write_project_state
from core.project_kickoff import grant_covers_asset, load_kickoff, load_project_request, validate_kickoff
from core.product_reference_pack import resolve_product_pack
from core.agent_acceptance import (asset_authorization_digest, require_asset_workflow_authorization,
                                   validate_asset_review)
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


def workbench_stamp(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def safe(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:36] or '资产'


def validate(data, filename):
    require_schema(data, filename, label='项目资产数据')


def production_basis(narrative, ast_ids):
    context = prompt_context(narrative, ast_ids)
    context.pop('revision', None)
    context['asset_requirements'] = [
        {k: v for k, v in r.items() if k not in {'image_decision', 'image_reason'}}
        for r in context['asset_requirements']]
    return context


BUSY = {'generating', 'submitting', 'submitted', 'querying'}
ASSET_KICKOFF_REQUIRED = '新项目请先登记开工决定，再按项目选定的通道审核资产'


def asset_review_workflow(root):
    request = load_project_request(root)
    if 'kickoff' in request:
        return 'website', validate_kickoff(request['kickoff'])
    return ('awaiting_kickoff' if request.get('project_mode') == 'new' else 'legacy'), None


def reference_evidence(project, requirements, assets=()):
    """Resolve declared originals and briefs through the same project/repository route."""
    documents, unavailable, paths, images = [], [], [], []
    root = Path(project)
    artifacts = load_manifest(root)['artifacts']
    state = load_project_state(root)
    for req in requirements:
        for raw in req.get('reference_paths', []):
            try:
                path = Path(resolve_reference(root, raw, artifacts, state).partition('#')[0])
            except ValueError as exc:
                unavailable.append(str(exc))
                continue
            if path.suffix.lower() in TEXT_REFERENCE_SUFFIXES:
                paths.append(path)
            elif path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}:
                images.append(str(path))
    for asset in assets:
        parent = Path(asset['source']).parent
        if not local_reference_document(root, parent):
            continue
        for name in ('PRODUCT_BRIEF.md', 'REFERENCE_MANIFEST.md'):
            path = parent / name
            if local_reference_document(root, path) and path.is_file():
                paths.append(path.resolve())
    for path in dict.fromkeys(paths):
        if path.stat().st_size > 150000:
            unavailable.append(str(path) + '（正文过大，需要先在项目中整理直接参考）')
        else:
            documents.append({'path': str(path.resolve()), 'content': path.read_text(encoding='utf-8-sig')})
    return {'reference_documents': documents, 'unavailable_references': unavailable,
            'reference_images': list(dict.fromkeys(images))}


class _ExportImages:
    """Reuse verified digests within the locked export, without retaining image bytes."""

    def __init__(self):
        self._digest = cache_file_digests(self._read_digest, check_for_changes=True)
        self._copies = {}

    @staticmethod
    def _version(stat):
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    @classmethod
    def _read_digest(cls, path):
        checksum = sha256()
        with path.open('rb') as stream:
            version = cls._version(os.fstat(stream.fileno()))
            while block := stream.read(1024 * 1024):
                checksum.update(block)
            if version != cls._version(path.stat()):
                raise ValueError('导出期间图片文件已变化，请重新核对：' + str(path))
        return checksum.hexdigest()

    def digest(self, path):
        return self._digest(Path(path).resolve())

    @staticmethod
    def _remove_own_copy(target, identity):
        if identity is None:
            return
        try:
            current = target.lstat()
            if (current.st_dev, current.st_ino) == identity:
                target.unlink()
        except FileNotFoundError:
            pass

    @classmethod
    def _install(cls, staged, target):
        """Install only a complete staged image and preserve an existing target."""
        try:
            os.link(staged, target)
        except OSError as exc:
            if exc.errno not in {errno.EPERM, errno.EACCES, errno.ENOTSUP, errno.EOPNOTSUPP,
                                 errno.EXDEV, errno.ENOSYS}:
                raise
            # Some network filesystems cannot link. Keep the destination exclusive
            # and remove only our own incomplete file on a failed write.
            identity = None
            try:
                with target.open('xb') as output:
                    stat = os.fstat(output.fileno())
                    identity = stat.st_dev, stat.st_ino
                    with staged.open('rb') as stream:
                        shutil.copyfileobj(stream, output)
                shutil.copystat(staged, target)
            except (OSError, KeyboardInterrupt):
                cls._remove_own_copy(target, identity)
                raise
            return identity
        stat = staged.stat()
        return stat.st_dev, stat.st_ino

    def copy(self, source, target, *, expected=None):
        """Place the reviewed bytes, even when one original feeds multiple outputs."""
        checksum = self.digest(source)
        if expected is not None and checksum != expected:
            raise ValueError('导出期间选图字节已变化，请重新核对：' + str(source))
        if target.exists():
            return False, self.digest(target) == checksum
        origin = self._copies.get(checksum, source)
        identity = None
        try:
            with tempfile.TemporaryDirectory(prefix='.asset-copy-', dir=target.parent) as folder:
                staged = Path(folder) / target.name
                shutil.copy2(origin, staged)
                if self.digest(staged) != checksum or self.digest(source) != checksum:
                    raise ValueError('复制期间图片文件已变化，请重新核对：' + str(source))
                identity = self._install(staged, target)
        except FileExistsError:
            return False, self.digest(target) == checksum
        try:
            matches = self.digest(target) == checksum
        except (OSError, ValueError, KeyboardInterrupt):
            self._remove_own_copy(target, identity)
            raise
        if matches:
            self._copies[checksum] = target
        else:
            self._remove_own_copy(target, identity)
        return True, matches


class Projects:
    def __init__(self, library, skill, bridge):
        self.lib, self.skill, self.bridge = library, skill, bridge
        self.root = library.root / 'projects'
        self.root.mkdir(exist_ok=True)
        self.batch_running = False

    def save(self, project):
        write(self.root / (project['id'] + '.json'), project)

    def _record_path(self, ident):
        if not re.fullmatch('[a-f0-9]{16}', ident):
            raise ValueError('无效项目编号')
        return self.root / (ident + '.json')

    def get(self, ident):
        return read(self._record_path(ident))

    def summaries(self):
        return [{k: p.get(k) for k in ('id', 'path', 'name', 'source', 'planning', 'export', 'batch')}
                for p in (read(f) for f in self.root.glob('*.json'))]

    def bind(self, path):
        root = asset_project_root(path)
        if root.is_relative_to(self.lib.vault):
            raise ValueError('生产项目请使用素材库之外的独立项目文件夹')
        with self.lib.lock:
            previous = next((p for p in self.summaries() if Path(p['path']) == root), None)
            if previous:
                return self.sync(previous['id'])
            _, source, narrative = source_project(root)
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

    def invalidate_export(self, p, reason, *, output_keys=None):
        exported = p.get('export') or p.get('partial_export')
        pending = p.get('export_pending')
        if not exported and (output_keys is None or not pending):
            return False, None
        root = Path(p['path'])
        with project_lock(root):
            manifest = load_manifest(root)
            unchanged_edges, segments = set(), {}
            previous_snapshot = None
            if output_keys is not None:
                lineage = [entry for entry in manifest['artifacts']
                           if entry.get('logical_id') == 'visual.asset.manifest']
                entries = [entry for entry in lineage if entry.get('status') == 'current']
                if not entries and exported:
                    # A previous interruption can leave the old export stale already.
                    entries = [entry for entry in lineage if entry.get('status') == 'stale'
                               and entry['artifact_id'] == exported['artifact_id']]
                ids = ({exported['artifact_id']} if exported else set())
                if pending:
                    ids.add('visual-assets-' + pending['id'])
                if not exported and not any(entry['artifact_id'] in ids for entry in entries):
                    return False, None
                if len(entries) == 1 and entries[0]['artifact_id'] in ids:
                    previous_snapshot = asset_export_snapshot(root, entries[0])
                    outputs = previous_snapshot[1]
                    if outputs is not None:
                        if output_keys.isdisjoint(outputs):
                            return False, previous_snapshot
                        retained = {key: selected for key, selected in outputs.items() if key not in output_keys}
                        unchanged_edges, segments = asset_output_impact(root, manifest, outputs, retained)
            mark_impacted_artifacts_stale(manifest, {'visual.asset.manifest'}, reason, p['source']['revision'],
                                         unchanged_edges=unchanged_edges, asset_output_segments=segments)
            save_manifest(root, manifest)
            if exported:
                p.setdefault('export_history', []).append(exported)
            p.pop('export', None)
            p.pop('partial_export', None)
            return True, previous_snapshot

    def sync(self, ident):
        with self.lib.lock:
            p = self.get(ident)
            if p.get('planning', {}).get('status') == 'running' or p.get('batch', {}).get('status') == 'running':
                raise ValueError('制作任务处理中，完成后再同步需求')
            _, source, narrative = source_project(p['path'])
            old = {r['requirement']['asset_id']: r for r in p['rows']}
            rows, changes = [], []
            jobs_to_save = []
            context_changed = any(p['narrative'].get(k) != narrative.get(k)
                                  for k in ('locked_facts', 'events', 'objective'))
            old_background = (p['narrative'].get('_asset_table') or {}).get('background')
            new_background = (narrative.get('_asset_table') or {}).get('background')
            context_changed = context_changed or old_background != new_background
            for req in image_work_items(narrative):
                row = old.pop(req['asset_id'], None)
                retained = row is not None and retained_specification(row['requirement'], req, narrative, p['narrative'])
                job = self.lib.get(row['job_id'], 'jobs') if row and row.get('job_id') else None
                updated_job = copy.deepcopy(job) if job and (retained or req['category'] == 'LOCATION') else None
                if retained and job:
                    retained = retain_character_labels(updated_job, req, narrative)
                previous_narrative = p['narrative']
                if retained:
                    previous_narrative = copy.deepcopy(previous_narrative)
                    previous_narrative['asset_requirements'] = [
                        copy.deepcopy(req) if r['asset_id'] == req['asset_id'] else r
                        for r in previous_narrative['asset_requirements']]
                paragraph_changed = narrative.get('schema_version') == '2.0' and row is not None and (
                    production_basis(previous_narrative, [req['asset_id']]) != production_basis(narrative, [req['asset_id']]))
                requirement_changed = row is not None and not retained and (
                    {k: v for k, v in row['requirement'].items() if k not in {'image_decision', 'image_reason'}} !=
                    {k: v for k, v in req.items() if k not in {'image_decision', 'image_reason'}})
                if row is None or requirement_changed or context_changed or paragraph_changed:
                    changes.append(req['asset_id'] + (' 新增' if row is None else ' 需求或叙事上下文变化，需重新核对'))
                    if row:
                        p.setdefault('history', []).append(copy.deepcopy(row))
                        if job:
                            if any(s['status'] in BUSY for s in job['steps']):
                                raise ValueError('受影响需求仍在生成，先核实结果后再同步')
                            if not job.get('project_stale'):
                                jobs_to_save.append({**job, 'project_stale': True})
                    row = {'requirement': req, 'reference_ids': (row or {}).get('reference_ids', []), 'job_id': None}
                elif updated_job is not None:
                    if retained:
                        updated_job['requirement'] = copy.deepcopy(req)
                    if req['category'] == 'LOCATION':
                        self.apply_scene_coverage(updated_job, scene_state_coverage(narrative, req))
                    if updated_job != job:
                        jobs_to_save.append(updated_job)
                row['requirement'] = req
                rows.append(row)
            for row in old.values():
                changes.append(row['requirement']['asset_id'] + ' 已从需求表移除')
                p.setdefault('history', []).append(row)
            if source != p['source'] or narrative != p['narrative']:
                self.invalidate_export(p, '叙事包需求变化，网页资产表需重新交接')
                p.pop('export_pending', None)
            for job in jobs_to_save:
                self.lib.save(job, 'jobs')
            p.update(rows=rows, source=source, narrative=narrative, changes=changes, synced_at=now())
            self.save(p)
            product_usage.schedule(self, p)
            return self.detail(ident)

    @staticmethod
    def row_character_mode(row):
        return CHARACTER_OUTPUT_MODES.get(row['requirement'].get('character_output_kind'),
                                          row.get('character_output_mode'))

    @classmethod
    def row_uses_wardrobe(cls, row):
        return uses_wardrobe(row['requirement']) and cls.row_character_mode(row) != 'single'

    @classmethod
    def row_kinds(cls, row):
        requirement = row['requirement']
        if uses_wardrobe(requirement):
            if cls.row_character_mode(row) == 'single':
                return ['人物单图']
            if requirement.get('character_output_kind') == '人物1x4':
                return ['大头照', '服装', '人物1x4']
        return production_kinds(requirement)

    def character_output_mode(self, payload):
        """Choose implementation depth; preserve the original AST and prior images."""
        with self.lib.lock:
            p = self.get(payload['project_id'])
            self.fresh(p)
            row = self.row(p, payload['ast_id'])
            mode = payload.get('mode')
            if not uses_wardrobe(row['requirement']) or mode not in {'single', 'wardrobe'}:
                raise ValueError('请选择人物单图或身份与服装板路线')
            fixed_mode = CHARACTER_OUTPUT_MODES.get(row['requirement'].get('character_output_kind'))
            if mode == 'single' and (fixed_mode == 'wardrobe'
                    or character_output_contract(Path(p['path']), [row['requirement']['asset_id']])):
                raise ValueError('此人物已明确要求最终人物1×4，请选择身份与服装板')
            if fixed_mode == 'single' and mode != fixed_mode:
                raise ValueError('上游已确认使用人物单图，变更交付规格须先调整上游')
            if self.row_character_mode(row) == mode:
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
            p['review_workflow'], kickoff = asset_review_workflow(p['path'])
            generation = kickoff.get('image_generation') if kickoff else None
            p['kickoff_generation'] = ({key: generation[key] for key in ('provider', 'model', 'outputs', 'scope')
                                        if key in generation} if generation else None)
            p['source_error'] = ''
        except Exception as exc:
            p['source_error'] = str(exc)
        for row in p['rows']:
            with self.lib.lock:
                row['job'] = self.lib.get(row['job_id'], 'jobs') if row.get('job_id') else None
            for step in (row['job'] or {}).get('steps', []):
                if step['kind'] == '人物1x4' and step['status'] == 'waiting':
                    step['generation_enabled'] = False
                if legacy_prop_reuse_label(step, step.get('project_confirmation') or {}):
                    set_prop_layout(step, build=False)
            row['uses_wardrobe'] = self.row_uses_wardrobe(row)
            if row['requirement']['category'] == 'CHARACTER':
                row['character_type'] = character_type(row['requirement'])
                row['character_output_mode'] = self.row_character_mode(row)
            if row['uses_wardrobe']:
                try:
                    row['upstream_outfits'] = upstream_outfits(row['requirement'])
                    job = row['job']
                    final_ids = character_final_ids(p, row['requirement'], row['upstream_outfits'], job)
                    row['outfits_need_sync'] = (
                        (job or {}).get('character_workflow', {}).get('upstream_outfits') != row['upstream_outfits']
                        or bool(job and final_output_updates(job, final_ids)))
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
                    step['generation_enabled'] = payload['route'] == 'build' and (
                        step['kind'] != '物品' or image_decision(row['requirement']) == '需要制作')
                    if step['kind'] == '物品' and not step.get('selected'):
                        set_prop_layout(step, build=step['generation_enabled'])
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
            coverage = scene_state_coverage(p['narrative'], row['requirement'])
            aliases = {state for states in coverage.values() for state in states} - set(coverage)
            for step in job['steps']:
                if step['state'] not in aliases:
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
                                    'visual_self_check', 'generation_history', 'result_history', 'message',
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
            self.apply_scene_coverage(job, coverage)
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

    @staticmethod
    def apply_scene_coverage(job, coverage):
        # A coverage decision changes output quantity, never image bytes or receipts.
        for step in job['steps']:
            if step['kind'] != '环境':
                continue
            if '_required_before_scene_coverage' in step:
                step['required'] = step.pop('_required_before_scene_coverage')
            step.pop('covered_states', None)
            if step['state'] in coverage:
                step['covered_states'] = list(coverage[step['state']])
            elif any(step['state'] in states for states in coverage.values()):
                step['_required_before_scene_coverage'] = step.get('required', True)
                step['required'] = False

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
            states = (list(scene_state_coverage(p['narrative'], req)) if kind == '环境' else
                      ['外观母版'] if kind == '角色外观' else req['required_states']) or [req['name']]
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
                states = list(scene_state_coverage(p['narrative'], req)) or [req['name']]
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
            evidence_inputs = reference_evidence(p['path'], [r['requirement'] for r in rows],
                [self.lib.get(i) for r in rows for i in r['reference_ids']])
            refs = evidence_inputs.pop('reference_images')
            for row in rows:
                refs += [str(self.copy_to_project(p, self.lib.get(i))) for i in row['reference_ids']]
            request = {'mode': 'project', 'project_path': p['path'], 'source_narrative': p['source'],
                       'narrative': prompt_context(p['narrative'], [r['requirement']['asset_id'] for r in rows]),
                       'kind': self.row_kinds(rows[0])[0], 'codex_model': 'gpt-6-luna', 'reference_paths': list(dict.fromkeys(refs)),
                       'rows': [{**r, 'character_output_mode': self.row_character_mode(r)} for r in rows],
                       'inventory': [{k: a.get(k) for k in ('id', 'name', 'category', 'status', 'tags', 'project')}
                                                for a in self.lib.items() if not a.get('trashed_at')]}
            request.update(evidence_inputs)
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
            coverage = scene_state_coverage(p['narrative'], req) if req['category'] == 'LOCATION' else {}
            covered = {state for slot in slots for state in coverage.get(slot['state'], [slot['state']])}
            if req['category'] != 'PROP' and production_kinds(req) != ['角色外观'] and not set(req['required_states']).issubset(covered):
                raise ValueError('制作项未完整覆盖 AST 所需状态，请重新盘点')
            seen = set()
            for s in slots:
                if s['key'] in seen or not re.fullmatch('[a-zA-Z0-9_-]{1,40}', s['key']):
                    raise ValueError('制作项编号无效或重复')
                if s['kind'] not in self.row_kinds(row) or (
                        s['kind'] != '人物1x4' and not re.fullmatch(r'[1-9][0-9]?:[1-9][0-9]?', s['ratio'])):
                    raise ValueError('制作项类别或比例与 AST 不符')
                if any(d not in seen for d in s['dependencies']):
                    raise ValueError('制作项前置关系必须按顺序、无环')
                if not s['rationale'].strip() or not s['purpose'].strip():
                    raise ValueError('制作项缺少用途或启用理由')
                if s['kind'] == '大头照':
                    s['ratio'] = '3:4'
                if s['kind'] == '角色外观':
                    s['view'] = '宠物三视图 · 正面、左侧、背面'
                    s['purpose'] += '；同一完整角色的正面、左侧、背面三视图，包含固定服饰与配件'
                if s['kind'] == '服装':
                    s['ratio'] = '4:3'
                elif s['kind'] == '角色外观':
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
                generation_enabled = s['kind'] != '人物1x4' and row.get('production_route') != 'reuse' and (
                    s['kind'] != '物品' or image_decision(req) == '需要制作')
                step = {**s, 'name': ('产品主图' + (' · ' + s['state'] if s.get('state') else '')) if s['kind'] == '产品' else s['view'],
                        'ratio': recipe['ratio'] if s['kind'] == '人物1x4' else s['ratio'],
                        'status': 'waiting', 'outputs': [], 'generation_enabled': generation_enabled,
                        'reference_ids': row['reference_ids'][:], 'project_confirmation': None,
                        'template_source': recipe.get('template') or '依据分类指南编写：' + recipe['guide']}
                if step['kind'] == '物品':
                    set_prop_layout(step, build=step['generation_enabled'])
                steps.append(step)
            job = {'id': uid(), 'title': req['name'], 'kind': slots[0]['kind'], 'source_id': None,
                   'provider': 'imagegen', 'model': '5.0', 'notes': '', 'workflow_version': 4,
                   'created_at': now(), 'project_id': p['id'], 'project_name': p['name'], 'ast_id': req['asset_id'],
                   'source_narrative': p['source'],
                   'requirement': copy.deepcopy(req), 'steps': steps, 'planning_evidence': evidence,
                   'character_output_mode': self.row_character_mode(row),
                   'scene_output_mode': row.get('scene_output_mode'),
                   'plan_rationale': planned['rationale'], 'blockers': planned['blockers']}
            if req['category'] == 'LOCATION':
                self.apply_scene_coverage(job, coverage)
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
        row, step = self._project_step(p, job, index)
        return p, row, step

    def _project_step(self, p, job, index, *, required_finals=None):
        if job.get('project_id') != p['id']:
            raise ValueError('制作项不属于当前项目，请刷新后重试')
        row = self.row(p, job['ast_id'])
        if row.get('job_id') != job['id'] or job.get('project_stale'):
            raise ValueError('此制作项属于旧版需求，请使用当前需求表')
        if not 0 <= index < len(job['steps']):
            raise ValueError('无效制作项')
        step = job['steps'][index]
        required_kind = row['requirement'].get('character_output_kind')
        if required_kind == '人物单图' and step['kind'] != '人物单图':
            raise ValueError('当前上游要求人物单图，请同步需求后重新选图')
        if required_kind == '人物1x4' and (not job.get('character_workflow') or step['kind'] == '人物单图'):
            raise ValueError('此人物已明确要求最终人物1×4，请在人物卡片准备身份与服装板')
        if step['kind'] in {'人物1x4', '人物单图'}:
            ast_id = row['requirement']['asset_id']
            if required_finals is None:
                required_finals = character_output_contract(Path(p['path']), [ast_id])
            outputs = required_finals.get(ast_id)
            if outputs:
                if step['kind'] == '人物单图' or not job.get('character_workflow'):
                    raise ValueError('此人物已明确要求最终人物1×4，请选择身份与服装板')
                ids = character_final_ids(p, row['requirement'], job=job)
                if any(candidate is step for candidate, _ in final_output_updates(job, ids)):
                    raise ValueError('人物最终输出 ID 已变化，请先打开人物卡片同步服装与输出 ID')
        return row, step

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

    def reset_steps(self, job, keys, *, review_change=None):
        for step in job['steps']:
            if step['key'] not in keys:
                continue
            previous = {k: copy.deepcopy(step.get(k)) for k in (
                'selected', 'outputs', 'project_confirmation', 'prompt', 'audit',
                'generation_run_id', 'submit_id', 'authorization', 'visual_self_check', 'output_id')}
            step.setdefault('history', []).append(previous)
            if review_change is not None:
                previous['website_review_change'] = review_change
                if previous['selected']:
                    step.update(status='awaiting_review', project_confirmation=None)
                    continue
            step.update(status='waiting', outputs=[], project_confirmation=None, audit={'status': 'stale'})
            step.pop('selected', None)
            if step['kind'] == '物品':
                set_prop_layout(step, build=step.get('generation_enabled') is not False)
            for key in ('prompt_approval', 'generation_run_id', 'submit_id', 'authorization',
                        'query_failures', 'query_blocked', 'query_retry_after', 'composition_error', 'message', 'visual_self_check'):
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

    def validate_dependency_confirmation(self, job, step, *, file_digest=None):
        self.validate_scene_sources(job, step)
        receipt = step.get('project_confirmation') or {}
        if receipt:
            asset = self.lib.get(step['selected'], require_file=True, file_digest=file_digest)
            verify_image_confirmation(asset['original'], receipt, file_digest=file_digest)
            if receipt.get('agent_review'):
                validate_asset_review(self.get(job['project_id'])['path'], receipt['agent_review'])
        if (receipt and step.get('kind') == '环境' and step['dependencies'] and
                'dependency_confirmations' not in receipt):
            raise ValueError('旧角度确认缺少前置图片版本依据，请重新核对此制作项：' + step['name'])
        if ('dependency_confirmations' in receipt and
                receipt['dependency_confirmations'] != self.dependency_basis(job, step)):
            raise ValueError('前置图片的确认版本已变化，请重新核对此制作项：' + step['name'])

    def validate_export_basis(self, project, *, file_digest=None):
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
                self.dependencies(job, step, file_digest=file_digest)
                asset = self.lib.get(step['selected'], require_file=True, file_digest=file_digest)
                if asset.get('backup', {}).get('status') == 'saved':
                    verify_image_confirmation(asset['backup']['path'], step['project_confirmation'],
                                              file_digest=file_digest)
        if project.get('export'):
            self.validate_export_snapshot(project, project['export'], complete=True, file_digest=file_digest)

    def validate_export_snapshot(self, project, exported, *, complete, file_digest=None):
        # The project copy is independent of the Vault original. Check its
        # own receipt too before returning a previously cached handoff.
        from core.asset_refs import _confirmed_output
        root = Path(project['path'])
        state = load_project_state(root)
        entries = [entry for entry in load_manifest(root)['artifacts']
                   if entry.get('artifact_id') == exported['artifact_id']]
        if (len(entries) != 1 or entries[0].get('logical_id') != 'visual.asset.manifest'
                or entries[0]['artifact_id'] not in state['current_artifacts']
                or not artifact_is_adopted(entries[0], state, root)):
            raise ValueError('资产导出登记已失效或缺少整表确认，请重新核对交接')
        path = project_path(root, exported['manifest'])
        if (project_path(root, entries[0]['path_or_url']) != path or not path.is_file()
                or (entries[0].get('checksum') and digest(path) != entries[0]['checksum'])):
            raise ValueError('已登记资产导出清单内容已变化或缺失，请保留原记录并重新核对交接')
        manifest = read(path)
        outputs = [(asset['asset_id'], output) for asset in manifest['assets']
                   for output in asset.get('confirmed_outputs', [])]
        available = {}
        for ast_id, output in outputs:
            available.setdefault(ast_id, []).append(output)
        requirements = {row['requirement']['asset_id']: row['requirement'] for row in project['rows']}
        ast_ids = list(requirements) if complete else []
        required_finals = character_output_contract(root, ast_ids)
        problems = final_output_problems(required_finals, ast_ids, available)
        for ast_id in ast_ids:
            requirement = requirements[ast_id]
            if requirement.get('character_output_kind') != '人物单图':
                continue
            images = available.get(ast_id, [])
            if not images or any(output.get('output_kind') != '人物单图' for output in images):
                problems.append(ast_id + '：当前上游要求人物单图，请重新核对交接')
        if problems:
            raise ValueError(' / '.join(problems))
        confirmation_cache = {}
        if (len(outputs) != exported['count'] or any(
                _confirmed_output(root, ast_id, output, confirmation_cache=confirmation_cache,
                                  file_digest=file_digest) is None
                for ast_id, output in outputs)):
            raise ValueError('已导出确认图片内容已变化或缺失，请保留原记录并重新核对交接')

    def dependencies(self, job, step, *, file_digest=None):
        self.validate_dependency_confirmation(job, step, file_digest=file_digest)
        refs = []
        for key in step['dependencies']:
            prior = next(s for s in job['steps'] if s['key'] == key)
            if not prior.get('project_confirmation') or prior['status'] != 'confirmed':
                raise ValueError('先在项目表中确认前置制作项：' + prior['name'])
            self.validate_dependency_confirmation(job, prior, file_digest=file_digest)
            asset = self.lib.get(prior['selected'], require_file=True, file_digest=file_digest)
            if asset.get('trashed_at'):
                raise ValueError('前置图片已删除，请先恢复')
            refs.append(asset)
        return refs

    def copy_to_project(self, project, asset, *, images=None, expected=None):
        """Give a task its own verified copy of a Vault-owned original."""
        source = Path(asset['original']).resolve()
        verify_image_confirmation(source, asset.get('confirmation') or {},
                                  file_digest=images.digest if images else None)
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
        if images is not None:
            created, matches = images.copy(source, destination, expected=expected)
            if not matches:
                if created:
                    raise ValueError('复制到项目后的文件校验失败：' + asset['name'])
                raise ValueError('项目参考副本与知识库原图不同，已保留两边文件：' + str(destination))
        elif destination.exists():
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
        if step.get('kind') == '人物1x4':
            raise ValueError(BOARD_IMPORT_ONLY)
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
        evidence_inputs = reference_evidence(p['path'], [row['requirement']], refs)
        declared_images = evidence_inputs.pop('reference_images')
        if not refs and not declared_images and row['requirement']['category'] in {'WARDROBE', 'REAL_PRODUCT'}:
            raise ValueError('此类资产须选择真实参考图，不能猜外观')
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
        existing_paths = {record['original'] for record in reference_records}
        for original in declared_images:
            if original not in existing_paths:
                reference_records.append({'original': original, 'sha256': digest(Path(original)),
                    'project_confirmed': False, 'role': '当前 AST 登记的原始参考，按可见内容与本次要求采用'})
        allow_design = (row['requirement']['category'] == 'PROP' or step['kind'] == '角色外观'
                        or (row['requirement']['category'] == 'CHARACTER' and step['kind'] in {'人物单图', '大头照', '服装'}))
        if layout := character_layout_reference(step['kind']):
            reference_records.append(layout)
        recipe = self.skill.recipe(step['kind'])
        template = recipe.get('template') or '依据分类指南编写：' + recipe['guide']
        template_notes = '依据当前 AST 和分类指南，制作' + step['purpose'] + '；状态：' + step['state'] + '；视角：' + step['view']
        if step['kind'] == '环境' and (refs or declared_images):
            template_notes += '；输入参考可能是俯视、平视或其他视角，不代表目标视角；保留已展示的布局与固定陈设，按本步骤目标视角制作，未展示部分仅作待确认的设计补全。'
        seed = template_notes
        if step['kind'] == '环境' and (not (refs or declared_images) or step['view'] not in {'45°', '正面', '反面', '俯视'}
                or len(step.get('dependencies', [])) != {'45°': 0, '正面': 1, '反面': 2, '俯视': 3}.get(step['view'], -1)):
            template = str(ROOT / 'skills/asset-image-assistant/references' / recipe['guide'])
        elif 'template' in recipe:
            # The seed and task-package template share the same executable rendering.
            view_index = {'45°': 0, '正面': 1, '反面': 2, '俯视': 3}.get(step['view'], 0) if step['kind'] == '环境' else 0
            seed, template = self.lib.project_template(step['kind'], job['title'], template_notes, view_index,
                allow_unreferenced_design=allow_design, reference_records=reference_records,
                has_references=bool(refs or declared_images))
        return {'mode': 'project', 'project_path': p['path'], 'project_id': p['id'], 'ast_id': job['ast_id'],
                'source_narrative': p['source'], 'narrative': prompt_context(p['narrative'], [job['ast_id']]), 'requirement': row['requirement'],
                'job_id': job['id'], 'subject': job['title'], 'template_notes': template_notes,
                'index': index, 'output_id': step_output_id(step), 'kind': step['kind'], 'view': step['view'], 'purpose': step['purpose'],
                'state': step['state'], 'character_outfit': next((v for v in job.get('character_workflow', {}).get('outfits', []) if v['id'] == step.get('outfit_id')), None), 'ratio': step['ratio'], 'provider': job['provider'],
                'model': job['model'] if job['provider'] == 'dreamina' else 'Codex 内置 ImageGen（宿主管理模型）',
                'prompt': step.get('prompt', seed), 'reference_paths': [record['original'] for record in reference_records],
                'reference_records': reference_records,
                'template_source': template, 'count': 1, 'resolution': '2k',
                'must_inherit': row['requirement']['identity_constraints'], 'must_not_inherit': step.get('must_not_inherit', []),
                'codex_model': 'gpt-6-luna',
                'generation_direction': step.get('generation_direction', ''),
                'allow_unreferenced_design': allow_design,
                'design_scope': '新制作所需的未锁定外观可作为设计候选；保留已确认身份与故事约束，明确要求还原真实外貌时仍须参考。',
                'gap_resolution': job.get('resolved_blockers'),
                **evidence_inputs}

    def select(self, payload):
        with self.lib.lock:
            job = self.lib.get(payload['job_id'], 'jobs')
            p, row, step = self.context(job, int(payload['index']), payload.get('project_id'))
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
            if (row['requirement'].get('character_output_kind') == '人物单图'
                    and not payload.get('as_reference') and asset['category'] != '人物单图'):
                raise ValueError('上游已确认使用人物单图，请从人物单图分类选择综合参考')
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
                self.lib.sync_visual_self_check(step, asset)
                if step.get('visual_self_check'):
                    step['message'] = self.lib.visual_self_check_message(step['visual_self_check'])
                else:
                    step.pop('message', None)
                if step.get('generation_enabled') is False and (job.get('direct_reuse') or step['kind'] == '物品'):
                    if step['kind'] == '物品':
                        set_prop_layout(step, build=False)
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

    @staticmethod
    def _same_website_asset(confirmation, review, source):
        if not confirmation or not review:
            return False
        previous = confirmation.get('website_review') or {}
        approval = previous.get('approval') or {}
        scope = review['approval']['scope']
        return (confirmation.get('source') == source
                and previous.get('asset_id') == review['asset_id']
                and confirmation.get('image_sha256') == review['image_sha256']
                and previous.get('purpose', review['purpose']) == review['purpose']
                and all((approval.get('scope') or {}).get(key) == scope[key]
                        for key in ('projectId', 'scope', 'caseId', 'stage'))
                and (previous.get('binding') == review['binding']
                     or (not previous.get('binding') and approval == review['approval'])
                     or review.get('_verified_confirmation_id') == confirmation['id']))

    @staticmethod
    def _same_chat_asset(confirmation, basis, source):
        """Match a step receipt to the exact chat-confirmation basis that produced it."""
        if not confirmation or not basis:
            return False
        previous = confirmation.get('chat_confirmation') or {}
        previous_confirmation = previous.get('confirmation') or {}
        current = basis.get('confirmation') or {}
        return (confirmation.get('source') == source
                and previous.get('channel') == 'chat_confirmation'
                and confirmation.get('asset_id') == previous.get('asset_id') == basis.get('asset_id')
                and confirmation.get('image_sha256') == previous.get('image_sha256') == basis.get('image_sha256')
                and previous.get('purpose') == basis.get('purpose')
                and all(key not in previous or previous[key] == basis.get(key) for key in ('ast_id', 'output_id'))
                and previous.get('authorization_id') == basis.get('authorization_id')
                and previous.get('authorization_ref') == basis.get('authorization_ref')
                and previous_confirmation.get('quote') == current.get('quote')
                and previous_confirmation.get('at') == current.get('at')
                and previous_confirmation.get('review_ref') == current.get('review_ref'))

    @staticmethod
    def _same_scoped_asset(confirmation, basis, source):
        if basis.get('channel') != 'agent_review':
            return Projects._same_chat_asset(confirmation, basis, source)
        stored = (confirmation or {}).get('agent_review') or {}
        return ((confirmation or {}).get('source') == source
                and (confirmation or {}).get('asset_id') == basis.get('asset_id')
                and (confirmation or {}).get('image_sha256') == basis.get('image_sha256')
                and all(stored.get(key) == value for key, value in basis.items()))

    @staticmethod
    def _chat_confirmation_basis(confirmation):
        """A chat confirmation basis is the user's recorded words, never a website receipt."""
        if not isinstance(confirmation, dict):
            raise ValueError('对话确认依据必须是包含用户原话、时间与复核结论位置的记录')
        basis = {key: confirmation.get(key) for key in ('quote', 'at', 'review_ref')}
        if any(not isinstance(value, str) or not value.strip() for value in basis.values()):
            raise ValueError('对话确认依据须包含用户原话、确认时间与协调方复核结论位置')
        return basis

    @staticmethod
    def _verify_previous_website_member(previous, review, read_receipt):
        """Recover the exact source and member for receipts saved before binding was recorded."""
        scope = (previous.get('approval') or {}).get('scope') or {}
        if not previous.get('publication') or previous.get('asset_id') != review['asset_id']:
            return False
        original = read_receipt(previous['publication'])
        remote = original['response']
        binding = review['binding']
        script = read_receipt(original['script_publication'], 'script')
        if (original['script_version'] != binding['script_version']
                or script['response']['version'] != binding['script_version']
                or script['source_json']['sha256'] != binding['script_sha256']
                or any(script['response'][key] != scope.get(key) for key in ('projectId', 'scope', 'caseId'))
                or any(remote[key] != scope.get(key)
                       for key in ('projectId', 'scope', 'caseId', 'stage', 'version'))):
            return False
        if remote['assetId'] != review['asset_id']:
            refs = original['request']['parameters']['references']
            if review['asset_id'] not in refs or len(refs) != len(original['reference_publications']):
                return False
            original = read_receipt(original['reference_publications'][refs.index(review['asset_id'])])
            remote = original['response']
        parameters = original['request']['parameters']
        return (remote['assetId'] == review['asset_id']
                and original['script_version'] == binding['script_version']
                and all(remote[key] == scope.get(key) for key in ('projectId', 'scope', 'caseId', 'stage'))
                and parameters['astId'] == binding['ast_id']
                and parameters['outputId'] == binding['output_id']
                and parameters['purpose'] == review['purpose']
                and original['image']['sha256'] == review['image_sha256'])

    def confirm(self, payload, *, website_review=None, chat_review=None):
        with self.lib.lock:
            job = self.lib.get(payload['job_id'], 'jobs')
            p, _, step = self.context(job, int(payload['index']), payload.get('project_id'))
            self._confirm_step(p, job, step, payload, website_review=website_review, chat_review=chat_review)
            return self.detail(p['id'])

    def _confirm_step(self, p, job, step, payload, *, website_review=None, chat_review=None):
        if website_review is None and chat_review is None:
            workflow, _ = asset_review_workflow(p['path'])
            if workflow == 'awaiting_kickoff':
                raise ValueError(ASSET_KICKOFF_REQUIRED)
            if workflow == 'website':
                raise ValueError('本项目资产采用需要当前版审核站真实通过回执，或获准的对话确认、代审依据')
        self.dependencies(job, step)
        ident = payload.get('asset_id') or step.get('selected')
        if ident not in step['outputs'] or payload.get('confirm') is not True:
            raise ValueError('请明确选择并确认本制作项的一张图片')
        if website_review is not None and (
                step.get('selected') != ident or step['purpose'] != website_review['purpose']):
            raise ValueError('当前选图或用途已变化，请核对审核包')
        if chat_review is not None and (
                step.get('selected') != ident or step['purpose'] != chat_review['purpose']):
            raise ValueError('当前选图或用途已变化，请核对对话确认依据')
        if step.get('project_confirmation') or step['status'] == 'confirmed':
            if step.get('project_confirmation') and step['status'] == 'confirmed' and step.get('selected') == ident:
                if website_review is None and chat_review is None:
                    return
                if website_review is not None and self._same_website_asset(
                        step['project_confirmation'], website_review, p['source']):
                    return
                if chat_review is not None and self._same_scoped_asset(
                        step['project_confirmation'], chat_review, p['source']):
                    return
            raise ValueError('已确认图片保留原版本；请先点击重新选图 / 返工，再确认其他图片')
        if step['status'] != 'awaiting_review':
            raise ValueError('只有待核对的图片可以确认，请刷新制作项状态')
        asset = self.lib.get(ident, require_file=True)
        if asset.get('reference_only'):
            raise ValueError('项目参考图不能确认为制作结果')
        if asset.get('trashed_at'):
            raise ValueError('图片已删除')
        check = self.skill.technical_check(asset['original'], None if step['kind'] == '人物1x4' else step['ratio'])
        if not check['passed'] or check['format'] not in {'PNG', 'JPEG', 'WEBP'}:
            raise ValueError('技术检查不通过：' + check['message'])
        if job.get('blockers') and payload.get('resolve_blockers') is not True:
            raise ValueError('请逐项核对并确认需求缺口已解决：' + '；'.join(job['blockers']))
        if job.get('blockers'):
            job['resolved_blockers'] = {'items': job['blockers'], 'at': now(), 'method': '用户核对实际选图并确认缺口已解决'}
            job['blockers'] = []
        image_sha256 = digest(Path(asset['original']))
        if website_review is not None and image_sha256 != website_review['image_sha256']:
            raise ValueError('当前选图字节已变化，请核对审核包')
        if chat_review is not None and image_sha256 != chat_review['image_sha256']:
            raise ValueError('当前选图字节已变化，请核对对话确认依据')
        receipt = {'id': uid(), 'at': now(), 'asset_id': ident, 'source': p['source'], 'method': '项目制作格逐项视觉确认', 'technical_qc': check,
                   'image_sha256': image_sha256,
                   'dependency_confirmations': self.dependency_basis(job, step)}
        if website_review is not None:
            receipt.update(method='审核站资产图通过', website_review={
                key: value for key, value in website_review.items() if key != '_verified_confirmation_id'})
        if chat_review is not None:
            if chat_review.get('channel') == 'agent_review':
                receipt.update(method='授权逐图代审', agent_review=dict(chat_review))
            else:
                receipt.update(method='用户对话整批确认', chat_confirmation=dict(chat_review))
        step.update(selected=ident, status='confirmed', project_confirmation=receipt)
        self.lib.sync_visual_self_check(step, asset)
        asset.update(status='confirmed', confirmation=receipt)
        asset.setdefault('project_confirmations', []).append({'project_id': p['id'], 'ast_id': job['ast_id'], **receipt})
        asset['backup'] = {'status': 'pending'}
        self.lib.save(asset)
        self.lib.save(job, 'jobs')
        self.lib.backup_wakeup.set()

    def _confirm_reviewed_outputs(self, p, targets, *, channel, required_finals):
        if not targets:
            return
        # The batch owns the project lock. Refresh after review/preflight,
        # then apply each output against the same checked upstream.
        self.fresh(p)
        for job_id, index, asset_id, review in targets:
            # Earlier confirmations can unlock another step on this job.
            job = self.lib.get(job_id, 'jobs')
            current = self._current_workbench(p)
            _, step = self._project_step(current, job, index, required_finals=required_finals)
            binding = review['binding'] if channel == 'website_review' else review
            if (job['ast_id'], step_output_id(step)) != (binding['ast_id'], binding['output_id']):
                raise ValueError('当前制作项的输出身份已变化，请核对审核依据')
            self._confirm_step(current, job, step, {'asset_id': asset_id, 'confirm': True}, **{channel: review})

    def _current_workbench(self, p):
        # CLI and Web can run in different processes. Read the current row
        # membership without reparsing the source package for every output.
        current = self.get(p['id'])
        if current['source'] != p['source'] or current['narrative'] != p['narrative']:
            raise ValueError('上游叙事包已变化，请点击“同步需求”查看差异并重新核对')
        return current

    def _reviewed_character_outputs(self, p):
        return character_output_contract(Path(p['path']), [
            row['requirement']['asset_id'] for row in p['rows']
            if row['requirement']['category'] == 'CHARACTER'])

    def _website_members(self, publication, read_receipt):
        members = [publication, *(read_receipt(path) for path in publication['reference_publications'])]
        response = publication['response']
        if ([member['response']['assetId'] for member in members[1:]] != publication['request']['parameters']['references']
                or any(member['script_version'] != publication['script_version']
                       or any(member['response'][key] != response[key] for key in ('projectId', 'caseId'))
                       for member in members)):
            raise ValueError('审核包的引用和上游版本不一致')
        by_output = {}
        for member in members:
            parameters = member['request']['parameters']
            key = (parameters['astId'], parameters['outputId'])
            if key in by_output:
                raise ValueError('审核包包含重复的 AST / 输出身份')
            by_output[key] = member
        return by_output

    def _withdraw_website_approval(self, project, publication, publication_path, script_sha256, decision):
        """Withdraw only confirmations whose actual approval is this reviewed version."""
        root = Path(project['path'])
        published = publication['response']
        scope = {key: published[key] for key in ('projectId', 'scope', 'caseId', 'stage', 'version')}
        references = publication['request']['parameters']['references']
        if not isinstance(references, list) or any(not isinstance(asset_id, str) or not asset_id for asset_id in references):
            raise ValueError('审核回执的引用范围无效，请核对原发布回执')
        member_ids = {published['assetId'], *references}
        evidence = {'publication': str(publication_path), 'website_scope': scope,
                    'decision': {key: decision[key] for key in ('decisionStatus', 'validity', 'latestEvent')
                                 if key in decision}}
        changes, withdrawn = [], []
        retry_keys = set()
        retained_assets = set()
        for row in project['rows']:
            if not row.get('job_id'):
                continue
            job = self.lib.get(row['job_id'], 'jobs')
            affected = set()
            for step in job['steps']:
                key = (job['ast_id'], step_output_id(step))
                confirmation = step.get('project_confirmation') or {}
                history = step.get('history') or []
                if not confirmation and history:
                    last = history[-1].get('website_review_change') or {}
                    if (last.get('website_scope') == scope and last.get('publication') == str(publication_path)
                            and step.get('selected') == history[-1].get('selected')):
                        retry_keys.add(key)
                previous = confirmation.get('website_review') or {}
                previous_file = previous.get('publication')
                binding = previous.get('binding')
                if (step.get('status') != 'confirmed'
                        or confirmation.get('source') != project['source']
                        or step.get('selected') != confirmation.get('asset_id')
                        or not previous_file or project_path(root, previous_file) != publication_path
                        or (previous.get('approval') or {}).get('scope') != scope
                        or previous.get('asset_id') not in member_ids
                        or not confirmation.get('image_sha256')
                        or previous.get('image_sha256') != confirmation['image_sha256']
                        or previous.get('purpose') != step.get('purpose')
                        or (binding and (binding.get('ast_id'), binding.get('output_id'), binding.get('base_url'),
                                         binding.get('script_version'), binding.get('script_sha256')) != (
                                             *key, publication['base_url'], publication['script_version'], script_sha256))):
                    continue
                affected.add(step['key'])
            if affected:
                # In-flight work retains its platform record. It may not be
                # confirmed while its prerequisite is awaiting review.
                dependent = self.dependent_keys(job, affected)
                affected = {step['key'] for step in job['steps']
                            if step['key'] in dependent and step.get('status') == 'confirmed'}
                withdrawn.extend(job['ast_id'] + '@' + step_output_id(step) for step in job['steps']
                                 if step['key'] in affected)
                self.reset_steps(job, affected, review_change=evidence)
                changes.append((job, affected))
            retained_assets.update(step['selected'] for step in job['steps']
                                   if step.get('required') is not False and step.get('selected')
                                   and step.get('status') == 'confirmed' and step.get('project_confirmation'))
        resume = retry_keys and (project.get('export') or project.get('partial_export') or project.get('export_pending')
                                or (project.get('export_history') and any(
                                    self.lib.get(asset).get('backup', {}).get('status') == 'saved'
                                    for asset in retained_assets)))
        if changes or resume:
            reason = ('原网站资产版已退回' if decision['decisionStatus'] == 'changes' else '原网站资产版通过已失效')
            self._save_output_changes(project, changes, reason + '，暂停使用依赖该版的确认输出',
                                      retry_keys=retry_keys, reexport=bool(retained_assets))
        return {'withdrawn_outputs': withdrawn,
                'feedback_scope_unverified': bool(references) and decision['decisionStatus'] == 'changes'}

    @staticmethod
    def _finish_review_backup(result, backup, resume):
        pending = result.get('pending_backup_ids')
        if backup is None or not pending:
            return result
        saved = backup(pending)
        if saved['status'] == 'saved' or saved['count']:
            result = resume()
        return {**result, 'backup': saved}

    def apply_website_review(self, ident, publication_path, client, *, backup=None):
        """Confirm exactly the reviewed outputs and export the backed current selections."""
        result, resume = self._apply_website_review(ident, publication_path, client)
        return self._finish_review_backup(result, backup, resume)

    def _apply_website_review(self, ident, publication_path, client):
        from review_text_cli import read_decision, read_publication
        from core.agent_review_api import effective_approval
        with self.lib.lock:
            p = self.get(ident)
            root = Path(p['path'])
            with project_lock(root):
                self.fresh(p)
                if not load_kickoff(root):
                    raise ValueError('审核站资产采用只适用于已登记开工决定的项目')
                local_publications = {}

                def read_receipt(path, stage='assets'):
                    path = (root / path).resolve()
                    key = (path, stage)
                    if key not in local_publications:
                        local_publications[key] = read_publication(root, path, client, stage)
                    return local_publications[key]

                publication = read_receipt(publication_path)
                script = read_receipt(publication['script_publication'], 'script')
                source_json = script['source_json']
                script_path = Path(p['source']['path'])
                if (project_path(root, source_json['path']) != script_path
                        or digest(script_path) != source_json['sha256']
                        or script['response']['version'] != publication['script_version']
                        or any(script['response'][key] != publication['response'][key]
                               for key in ('projectId', 'caseId'))):
                    raise ValueError('资产送审与本项目当前已采用编导稿不一致')
                decision = read_decision(client, publication)
                if not effective_approval(decision):
                    result = {'adopted': False}
                    # A new pending package does not revoke the outputs already
                    # confirmed under an earlier exact review of this script.
                    decision_status = decision['decisionStatus']
                    if decision_status == 'changes' and decision['validity']['isCurrent']:
                        result.update(self._withdraw_website_approval(p, publication, (root / publication_path).resolve(),
                                                                     source_json['sha256'], decision))
                    earlier_approval = decision_status == 'approved' and not decision['validity']['isCurrent']
                    if ((decision_status == 'pending' or earlier_approval)
                            and effective_approval(read_decision(client, script))):
                        result = self._export_locked({'project_id': ident}, website_package={})
                    result = {**result, 'decision_status': decision['decisionStatus'], 'validity': decision['validity']}
                    # There may be no new confirmations yet. A later decision
                    # can open the normal adoption path.
                    return result, lambda: self._apply_website_review(ident, publication_path, client)[0]
                if not effective_approval(read_decision(client, script)):
                    raise ValueError('上游编导稿的网站通过已经失效')
                by_output = self._website_members(publication, read_receipt)
                required_finals = self._reviewed_character_outputs(p)
                approval = decision['approval']
                package = {'approval': approval, 'publication': str((root / publication_path).resolve()), 'assets': {}}
                targets = []
                for row in p['rows']:
                    if not row.get('job_id'):
                        continue
                    job = self.lib.get(row['job_id'], 'jobs')
                    for index, step in enumerate(job['steps']):
                        if step.get('required') is False:
                            continue
                        key = (job['ast_id'], step_output_id(step))
                        member = by_output.pop(key, None)
                        if member is None:
                            continue
                        label = ' / '.join(key)
                        if not step.get('selected') or step['purpose'] != member['request']['parameters']['purpose']:
                            raise ValueError('网站通过的审核包没有覆盖准确制作项及用途：' + label)
                        _, step = self._project_step(p, job, index, required_finals=required_finals)
                        self.dependencies(job, step)
                        asset = self.lib.get(step['selected'], require_file=True)
                        check = self.skill.technical_check(asset['original'], None if step['kind'] == '人物1x4' else step['ratio'])
                        if (digest(Path(asset['original'])) != member['image']['sha256']
                                or step['status'] not in {'awaiting_review', 'confirmed'} or job.get('blockers')
                                or asset.get('reference_only') or asset.get('trashed_at')
                                or not check['passed'] or check['format'] not in {'PNG', 'JPEG', 'WEBP'}):
                            raise ValueError('当前选图已变化或制作项尚未就绪：' + label)
                        review = {'approval': approval, 'asset_id': member['response']['assetId'],
                                  'publication': package['publication'], 'purpose': step['purpose'],
                                  'image_sha256': member['image']['sha256'],
                                  'binding': {'base_url': client.base_url, 'script_version': publication['script_version'],
                                              'script_sha256': source_json['sha256'], 'ast_id': key[0], 'output_id': key[1]}}
                        confirmation = step.get('project_confirmation')
                        if step['status'] == 'confirmed' or confirmation:
                            previous = (confirmation or {}).get('website_review') or {}
                            if (previous and not previous.get('binding') and previous.get('approval') != approval
                                    and self._verify_previous_website_member(previous, review, read_receipt)):
                                review['_verified_confirmation_id'] = confirmation['id']
                            if step['status'] != 'confirmed' or not self._same_website_asset(
                                    confirmation, review, p['source']):
                                raise ValueError('网站审核包未引用准确的已确认图片：' + label)
                        package['assets'][key] = review
                        targets.append((job['id'], index, asset['id'], review))
                if by_output or not targets:
                    raise ValueError('网站审核包与项目当前必需资产输出不一致')
                self._confirm_reviewed_outputs(p, targets, channel='website_review', required_finals=required_finals)
                result = self._export_locked({'project_id': ident}, website_package=package)
        return result, lambda: self._resume_website_export(
            p, client, publication, script, local_publications, package, targets)

    def _resume_website_export(self, project, client, publication, script, receipts, package, targets):
        from review_text_cli import read_decision, read_publication
        from core.agent_review_api import effective_approval
        root = Path(project['path'])
        with self.lib.lock, project_lock(root):
            p = self._current_workbench(project)
            # Recheck publication files and their original uploaded bytes.
            # The already-checked member bindings can be reused if they agree.
            for (path, stage), receipt in receipts.items():
                if read_publication(root, path, client, stage) != receipt:
                    raise ValueError('备份期间发布回执已变化，请重新核对审核包')
            decision = read_decision(client, publication)
            if not effective_approval(decision):
                result = {'adopted': False, 'decision_status': decision['decisionStatus'],
                          'validity': decision['validity']}
                if decision['validity']['isCurrent']:
                    self.fresh(p)
                    result.update(self._withdraw_website_approval(
                        p, publication, Path(package['publication']), script['source_json']['sha256'], decision))
                return result
            if not effective_approval(read_decision(client, script)):
                raise ValueError('上游编导稿的网站通过已经失效')
            if decision['approval'] != package['approval']:
                return self.apply_website_review(p['id'], Path(package['publication']), client)
            self._validate_reviewed_export(p, targets, self._same_website_asset)
            return self._export_locked({'project_id': p['id']}, website_package=package)

    def _validate_reviewed_export(self, p, targets, matches):
        for job_id, index, asset_id, review in targets:
            job = self.lib.get(job_id, 'jobs')
            row = self.row(p, job['ast_id'])
            if (job.get('project_id') != p['id'] or job.get('project_stale') or job.get('blockers')
                    or row.get('job_id') != job_id or not 0 <= index < len(job['steps'])):
                raise ValueError('备份期间制作项已变化，请重新核对审核范围')
            step = job['steps'][index]
            binding = review.get('binding', review)
            receipt = step.get('project_confirmation')
            if (step.get('required') is False or step['status'] != 'confirmed'
                    or step.get('selected') != asset_id or asset_id not in step['outputs']
                    or (receipt or {}).get('asset_id') != asset_id
                    or step['purpose'] != review['purpose']
                    or (job['ast_id'], step_output_id(step)) != (binding['ast_id'], binding['output_id'])
                    or not matches(receipt, review, p['source'])):
                raise ValueError('备份期间已确认的选图、用途或审核依据已变化')
            if p.get('export'):
                asset = self.lib.get(asset_id, require_file=True)
                if asset.get('reference_only') or asset.get('trashed_at'):
                    raise ValueError('备份期间选图已失效，请重新核对审核范围')

    @staticmethod
    def _chat_output_scope(confirmation):
        if not isinstance(confirmation, dict):
            raise ValueError('对话确认依据必须是包含用户原话、时间与复核结论位置的记录')
        if 'outputs' not in confirmation:
            return None
        raw = confirmation['outputs']
        fields = {'ast_id', 'output_id', 'asset_id', 'image_sha256', 'purpose'}
        if (not isinstance(raw, list) or any(not isinstance(row, dict) or set(row) != fields
                or not all(isinstance(row[key], str) and row[key].strip() for key in fields)
                or not re.fullmatch(r'[a-f0-9]{64}', row['image_sha256']) for row in raw)):
            raise ValueError('对话确认输出须指定准确 AST、输出 ID、选图 ID、图片 SHA-256 及用途')
        scope = {(row['ast_id'], row['output_id']): row for row in raw}
        if len(scope) != len(raw):
            raise ValueError('对话确认包含重复制作项')
        return scope

    def apply_chat_confirmation(self, ident, *, authorization_id, confirmation, backup=None):
        """Apply the exact new approval and carry forward independently confirmed outputs."""
        result, resume = self._apply_scoped_review(ident, authorization_id, confirmation)
        return self._finish_review_backup(result, backup, resume)

    def apply_agent_review(self, ident, *, authorization_id, review, backup=None):
        result, resume = self._apply_scoped_review(ident, authorization_id, review, delegated=True)
        return self._finish_review_backup(result, backup, resume)

    def _delegated_scope(self, project, authorization, report):
        if not isinstance(report, dict) or not isinstance(report.get('outputs'), list):
            raise ValueError('逐图代审须列出准确 outputs')
        basis = {'channel': 'agent_review', 'authorization_id': authorization['id'],
                 'authorization_sha256': asset_authorization_digest(authorization)}
        if not report['outputs']:
            return basis, {}, {}
        source = {**project['source'], 'sha256': digest(Path(project['source']['path']))}
        if report.get('source_narrative') != source:
            raise ValueError('逐图代审须绑定当前编导身份、修订和字节')
        basis.update(source_narrative=source, **{key: report.get(key) for key in ('agent_id', 'checked_at', 'report_ref')})
        fields = {'ast_id', 'output_id', 'asset_id', 'image_sha256', 'purpose'}
        scope = self._chat_output_scope({'outputs': [
            {key: row.get(key) for key in fields} if isinstance(row, dict) else row
            for row in report['outputs']]})
        observations = {(row['ast_id'], row['output_id']): {
            'result': row.get('result'), 'observation': row.get('observation'),
            'locator': row.get('locator'), 'product_check': row.get('product_check')}
            for row in report['outputs']}
        return basis, scope, observations

    def _validate_delegated_product(self, project, job, step, review):
        requirement = self.row(project, job['ast_id'])['requirement']
        if requirement['category'] != 'REAL_PRODUCT':
            if review['product_check'] is not None:
                raise ValueError('产品专项核对仅适用于真实产品资产')
            return
        root = Path(project['path'])
        manifest = load_manifest(root)
        state = load_project_state(root)
        references = {resolve_reference(root, raw, manifest['artifacts'], state)
                      for raw in requirement['reference_paths']}
        references.update(asset['original'] for asset in (
            self.lib.get(ident, require_file=True) for ident in step.get('reference_ids', []))
                          if asset.get('product_reference') and not asset.get('trashed_at'))
        pack = resolve_product_pack(load_kickoff(root)['product'])
        if pack and references.intersection({pack['manifest_path'], pack.get('current_manifest_path')}):
            references.update(item['path'] for item in pack['references']
                              if item['role'] in {'packaging', 'material', 'state'})
        check = review.get('product_check')
        if (not isinstance(check, dict) or not isinstance(check.get('references'), list)
                or not check['references'] or any(not isinstance(ref, dict) or ref.get('path') not in references
                    or Path(ref['path']).suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}
                    for ref in check['references'])):
            raise ValueError('真实产品代审须核对本项实际登记的产品参考图')

    def _apply_scoped_review(self, ident, authorization_id, confirmation, *, delegated=False):
        with self.lib.lock:
            p = self.get(ident)
            root = Path(p['path'])
            with project_lock(root):
                self.fresh(p)
                if not load_kickoff(root):
                    raise ValueError('资产按范围采用只适用于已登记开工决定的项目')
                authorization = require_asset_workflow_authorization(root, authorization_id, delegated=delegated)
                channel = 'agent_review' if delegated else 'chat_confirmation'
                if delegated:
                    basis, scope, observations = self._delegated_scope(p, authorization, confirmation)
                else:
                    scope = self._chat_output_scope(confirmation)
                    basis = {'channel': channel, 'authorization_id': authorization['id'],
                             'authorization_ref': authorization['authorization_ref']}
                    if scope != {}:
                        basis['confirmation'] = self._chat_confirmation_basis(confirmation)
                if scope == {}:
                    resume_basis = {'channel': channel, 'authorization_id': authorization['id']} if delegated else {}
                    result = self._export_locked({'project_id': ident}, chat_package=resume_basis)
                    return result, lambda: self._resume_chat_export(p, authorization, resume_basis, [])
                slots = {}
                for row in p['rows']:
                    if not row.get('job_id'):
                        continue
                    job = self.lib.get(row['job_id'], 'jobs')
                    for index, step in enumerate(job['steps']):
                        if step.get('required') is False:
                            continue
                        key = (job['ast_id'], step_output_id(step))
                        if key in slots:
                            raise ValueError('当前制作项含有重复的 AST / 输出身份')
                        slots[key] = (job, index, step)
                def matching_basis(step):
                    stored = (step.get('project_confirmation') or {}).get(channel) or {}
                    return stored if all(stored.get(key) == value for key, value in basis.items()) else None

                current_approvals = {key: stored for key, (_, _, step) in slots.items()
                                     if (stored := matching_basis(step))}
                historical_steps = [old for _, _, step in slots.values() for old in step.get('history', [])]
                old_jobs = {row['job_id'] for row in p.get('history', []) if row.get('job_id')}
                old_jobs.update(job_id for row in p['rows'] for job_id in row.get('job_history', []))
                for job_id in old_jobs:
                    try:
                        old_job = self.lib.get(job_id, 'jobs')
                    except ValueError:
                        continue  # Obsolete job records may have been deleted.
                    historical_steps.extend(old_job['steps'])
                historical_approvals = [stored for step in historical_steps if (stored := matching_basis(step))]
                prior_approvals = [*current_approvals.values(), *historical_approvals]
                if scope is None:
                    # Legacy requests can repeat the original receipt or identify
                    # the only selected output. An old approval cannot select a new one.
                    targets = list(current_approvals)
                    if not targets:
                        if historical_approvals:
                            raise ValueError('此对话只确认历史输出；新图须提供新的确认依据')
                        targets = [key for key, (_, _, step) in slots.items()
                                   if step.get('selected') and not step.get('project_confirmation')]
                        if len(targets) != 1:
                            raise ValueError('对话确认须列出本批准确 outputs；无新确认时可传空列表续跑导出')
                else:
                    if set(scope) - slots.keys():
                        raise ValueError('对话确认输出不在当前必需制作项范围')
                    declared = [output for prior in prior_approvals for output in prior.get('approved_outputs', [])]
                    if prior_approvals and any(key not in current_approvals and output not in declared
                                               for key, output in scope.items()):
                        raise ValueError('此对话的原确认范围不能扩大到新输出')
                    targets = list(scope)
                pending = {}
                reviewed = []
                required_finals = self._reviewed_character_outputs(p)
                for key in targets:
                    job, index, _ = slots[key]
                    label = ' / '.join(key)
                    _, step = self._project_step(p, job, index, required_finals=required_finals)
                    if step_output_id(step) != key[1]:
                        raise ValueError('当前制作项的输出身份已变化，请核对对话确认依据：' + label)
                    if not step.get('selected'):
                        raise ValueError('对话确认的制作项尚未选图：' + label)
                    self.validate_dependency_confirmation(job, step)
                    asset = self.lib.get(step['selected'], require_file=True)
                    actual = {'ast_id': key[0], 'output_id': key[1], 'asset_id': asset['id'],
                              'purpose': step['purpose'], 'image_sha256': digest(Path(asset['original']))}
                    if scope is not None and scope[key] != actual:
                        raise ValueError('对话确认的选图、用途或字节与当前制作项不符：' + label)
                    review = {**basis, **actual}
                    if delegated:
                        review.update(observations[key])
                        self._validate_delegated_product(p, job, step, review)
                        validate_asset_review(root, review)
                    reviewed.append((job['id'], index, asset['id'], review))
                    existing = step.get('project_confirmation')
                    if existing:
                        self.dependencies(job, step)
                        if not self._has_current_confirmation(p, job, step, asset):
                            raise ValueError('已有确认的来源、图片或用途已变化：' + label)
                        if delegated and not self._same_scoped_asset(existing, review, p['source']):
                            raise ValueError('已采用图片保留原代审依据；请先重新选图或返工：' + label)
                        continue
                    check = self.skill.technical_check(asset['original'], None if step['kind'] == '人物1x4' else step['ratio'])
                    if (step['status'] != 'awaiting_review' or job.get('blockers')
                            or asset.get('reference_only') or asset.get('trashed_at') or not check['passed']
                            or check['format'] not in {'PNG', 'JPEG', 'WEBP'}):
                        raise ValueError('当前选图已变化或制作项尚未就绪：' + label)
                    pending[(job['id'], step['key'])] = (job, index, {**review,
                        'approved_outputs': list(scope.values()) if scope is not None else [actual]})
                ordered = []
                while pending:
                    # Topologically order and validate all selected outputs before mutation.
                    ready = [key for key, (job, index, _) in pending.items() if all(
                        (job['id'], dep) not in pending for dep in job['steps'][index]['dependencies'])]
                    if not ready:
                        raise ValueError('对话确认的制作项存在循环依赖')
                    for key in ready:
                        job, index, review = pending.pop(key)
                        for dep in job['steps'][index]['dependencies']:
                            prior = next(step for step in job['steps'] if step['key'] == dep)
                            if prior['status'] == 'confirmed' and prior.get('project_confirmation'):
                                self.validate_dependency_confirmation(job, prior)
                            elif (job['id'], dep) not in {item[:2] for item in ordered}:
                                raise ValueError('先在项目表中确认前置制作项：' + prior['name'])
                        ordered.append((*key, index, review))
                self._confirm_reviewed_outputs(p, [
                    (job_id, index, review['asset_id'], review) for job_id, _, index, review in ordered
                ], channel='chat_review', required_finals=required_finals)
                result = self._export_locked({'project_id': ident}, chat_package=basis)
        return result, lambda: self._resume_chat_export(p, authorization, basis, reviewed)

    def _resume_chat_export(self, project, authorization, basis, targets):
        root = Path(project['path'])
        with self.lib.lock, project_lock(root):
            p = self._current_workbench(project)
            if require_asset_workflow_authorization(root, authorization['id']) != authorization:
                raise ValueError('备份期间资产确认授权已变化，请重新核对')
            self._validate_reviewed_export(p, targets, self._same_scoped_asset)
            return self._export_locked({'project_id': p['id']}, chat_package=basis)

    def _has_current_confirmation(self, project, job, step, asset, *, file_digest=digest):
        receipt = step.get('project_confirmation') or {}
        if (step['status'] != 'confirmed' or not receipt.get('id') or receipt.get('source') != project['source']
                or receipt.get('asset_id') != asset['id'] or not receipt.get('image_sha256')):
            return False
        review = receipt.get('agent_review')
        if review:
            validate_asset_review(project['path'], review)
            return self._same_scoped_asset(receipt, {**review, 'ast_id': job['ast_id'],
                'output_id': step_output_id(step), 'asset_id': asset['id'], 'purpose': step['purpose'],
                'image_sha256': file_digest(Path(asset['original']))}, project['source'])
        chat = receipt.get('chat_confirmation')
        if chat:
            authorization = require_asset_workflow_authorization(project['path'], chat.get('authorization_id'))
            return (chat.get('authorization_ref') == authorization['authorization_ref']
                    and self._chat_confirmation_basis(chat.get('confirmation'))
                    and self._same_chat_asset(receipt, {**chat, 'ast_id': job['ast_id'],
                        'output_id': step_output_id(step), 'asset_id': asset['id'], 'purpose': step['purpose'],
                        'image_sha256': receipt['image_sha256']}, project['source']))
        web = receipt.get('website_review')
        if web:
            binding = web.get('binding') or {}
            scope = (web.get('approval') or {}).get('scope') or {}
            return (scope.get('stage') == 'assets' and scope.get('caseId') and scope.get('version')
                    and web.get('purpose') == step['purpose']
                    and web.get('image_sha256') == receipt['image_sha256']
                    and (not binding or (binding.get('ast_id'), binding.get('output_id'),
                         binding.get('script_sha256')) == (job['ast_id'], step_output_id(step),
                         file_digest(Path(project['source']['path'])))))
        return False

    def reopen(self, payload):
        with self.lib.lock:
            job = self.lib.get(payload['job_id'], 'jobs')
            p, _, step = self.context(job, int(payload['index']), payload.get('project_id'))
            keys = self.dependent_keys(job, {step['key']})
            if any(s['status'] in BUSY or s.get('audit', {}).get('status') in {'drafting', 'reviewing'} for s in job['steps'] if s['key'] in keys):
                raise ValueError('关联图片仍在生成，请等待后再返工')
            self.reset_steps(job, keys)
            self._save_output_changes(p, [(job, keys)], '用户重新选择资产，相关制作项待再次确认')
            return self.detail(p['id'])

    def _save_output_changes(self, p, changes, reason, *, retry_keys=(), reexport=True):
        output_keys = {(job['ast_id'], step_output_id(step)) for job, keys in changes
                       for step in job['steps'] if step['key'] in keys} | set(retry_keys)
        with project_lock(Path(p['path'])):
            # A previous completed partial export already excludes these outputs.
            partial = p.get('partial_export')
            if (not changes and partial and 'items' in partial and not p.get('export_pending')
                    and output_keys.isdisjoint(tuple(item[:2]) for item in partial['items'])):
                return
            invalidated, previous_snapshot = self.invalidate_export(p, reason, output_keys=output_keys)
            for job, _ in changes:
                self.lib.save(job, 'jobs')
            pending = p.get('export_pending')
            clear_pending = pending and (invalidated or 'items' not in pending
                                         or not output_keys.isdisjoint(tuple(item[:2]) for item in pending['items']))
            if clear_pending:
                p.pop('export_pending')
            if invalidated or clear_pending:
                self.save(p)
            interrupted = (retry_keys and (pending or p.get('export_history'))
                           and not (p.get('export') or p.get('partial_export')))
            if reexport and (invalidated or interrupted) and load_kickoff(p['path']):
                self._export_locked({'project_id': p['id']}, chat_package={}, previous_asset_snapshot=previous_snapshot)

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
            kickoff = load_kickoff(p['path']) if mode == 'generate' else None
            grant = kickoff.get('image_generation') if kickoff else None
            if kickoff:
                if not grant:
                    raise ValueError('本轮开工记录尚无生图授权')
                if any(key in payload and payload[key] != grant[key] for key in ('provider', 'model')):
                    raise ValueError('本批工具或模型与开工授权不符')
                provider, model = grant['provider'], grant['model']
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
                if step['kind'] == '人物1x4':
                    raise ValueError(BOARD_IMPORT_ONLY)
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
                    output_id = step_output_id(step)
                    if grant and not grant_covers_asset(grant, job['ast_id'], output_id):
                        raise ValueError('制作项不在开工生图授权范围：' + job['ast_id'] + ' / ' + output_id)
                    if ((grant is None and payload.get('authorize') is not True)
                            or not self.skill.approved(step) or t.get('prompt') != step.get('prompt')):
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
            if grant:
                p['batch']['kickoff_generation'] = grant
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
                            kickoff = load_kickoff(p['path'])
                            current_grant = kickoff.get('image_generation') if kickoff else None
                            if ((kickoff or 'kickoff_generation' in batch)
                                    and (not current_grant or current_grant != batch.get('kickoff_generation'))):
                                raise ValueError('开工生图授权在排队期间已改变，停止提交本项')
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
        with self.lib.lock:
            p = self.get(payload['project_id'])
            with project_lock(Path(p['path'])):
                return self._export_locked(payload)

    def _export_locked(self, payload, *, website_package=None, chat_package=None, previous_asset_snapshot=None):
        with self.lib.lock:
            record_path = self._record_path(payload['project_id'])
            stamp = workbench_stamp(record_path)
            p = self.get(payload['project_id'])
            current = p
            self.fresh(p)
            website = website_package is not None
            scoped = website or chat_package is not None
            result = ({'decision_status': 'approved'} if website else
                      {'confirmation_channel': (chat_package or {}).get('channel', 'chat_confirmation'),
                       **({'authorization_id': chat_package['authorization_id']}
                          if chat_package and 'authorization_id' in chat_package else {})})
            images = _ExportImages()
            if p.get('export'):
                self.validate_export_basis(p, file_digest=images.digest)
                return {'adopted': True, 'complete': True, 'export': p['export'], **result} if scoped else p['export']
            if not scoped:
                workflow, _ = asset_review_workflow(p['path'])
                if workflow == 'awaiting_kickoff':
                    raise ValueError(ASSET_KICKOFF_REQUIRED)
                if workflow == 'website':
                    if not p['rows'] and all(image_decision(req) != '待定' for req in p['narrative']['asset_requirements']):
                        raise ValueError('本轮无资产图交付项，协调方可沿已授权流程继续')
                    raise ValueError('资产正式采用需要审核站真实通过回执，或获准的对话确认、代审依据')
                if payload.get('confirm') is not True:
                    raise ValueError('兼容项目请确认整张需求表后交接')
            requirements = p['narrative']['asset_requirements']
            missing_outputs = any(image_decision(r) == '待定' for r in requirements)
            pending_backup_ids = set()
            if missing_outputs and not scoped:
                raise ValueError('资产图需求仍有待定项，先补齐判断再交接')
            if not p['rows'] and not requirements and not p['narrative'].get('_asset_choices'):
                raise ValueError('项目没有资产需求，无需生成资产交接')
            collected = []
            technical_checks = {}
            required_finals = character_output_contract(Path(p['path']), [row['requirement']['asset_id'] for row in p['rows']])
            for row in p['rows']:
                if not row.get('job_id'):
                    if scoped:
                        missing_outputs = True
                        continue
                    raise ValueError('尚未完成：' + row['requirement']['asset_id'])
                job = self.lib.get(row['job_id'], 'jobs')
                ast = row['requirement']['asset_id']
                final_problems = final_output_problems(required_finals, [ast], {ast: [
                    {'output_id': step_output_id(step), 'output_kind': step['kind']}
                    for step in job['steps'] if step.get('required') is not False]})
                if final_problems:
                    if scoped:
                        missing_outputs = True
                        continue
                    raise ValueError(' / '.join(final_problems))
                if self.row_uses_wardrobe(row):
                    expected = upstream_outfits(row['requirement'])
                    finals = [s for s in job['steps'] if s['kind'] == '人物1x4' and s.get('required') is not False]
                    if len(finals) != len(expected):
                        if scoped:
                            missing_outputs = True
                            continue
                        raise ValueError('人物服装套数与上游不一致，请打开卡片重新对应：' + job['title'])
                if job.get('blockers'):
                    if scoped:
                        missing_outputs = True
                        continue
                    raise ValueError('仍有需求缺口：' + job['ast_id'])
                for i, step in enumerate(job['steps']):
                    if step.get('required') is False:
                        continue
                    if step['status'] != 'confirmed' or not step.get('project_confirmation'):
                        if scoped:
                            missing_outputs = True
                            continue
                        raise ValueError('尚未逐项确认：' + job['ast_id'] + ' / ' + step['name'])
                    observed_stamp = workbench_stamp(record_path)
                    if observed_stamp != stamp:
                        current = self._current_workbench(p)
                        stamp = observed_stamp
                    _, step = self._project_step(current, job, i, required_finals=required_finals)
                    asset = self.lib.get(step['selected'])
                    source = Path(asset['original'])
                    backed = asset.get('backup', {}).get('status') == 'saved'
                    self.dependencies(job, step, file_digest=images.digest)
                    if scoped and not self._has_current_confirmation(
                            p, job, step, asset, file_digest=images.digest):
                        raise ValueError('选图没有对应的当前来源、用途或字节确认依据：' + ast + ' / ' + step_output_id(step))
                    if asset.get('reference_only'):
                        raise ValueError('项目参考图不能作为正式交付：' + step['name'])
                    if asset.get('trashed_at') or not source.is_file():
                        raise ValueError('选定图片已删除或丢失：' + step['name'])
                    if not backed:
                        if scoped:
                            pending_backup_ids.add(asset['id'])
                            continue
                        raise ValueError('图片仍待知识库备份：' + asset['name'] + '。' +
                                         asset.get('backup', {}).get('message', '请先重试知识库备份'))
                    backup_path = Path(asset['backup']['path'])
                    checksum = images.digest(source)
                    verify_image_confirmation(source, step['project_confirmation'], file_digest=images.digest)
                    if not backup_path.is_file() or images.digest(backup_path) != checksum:
                        raise ValueError('知识库备份缺失或与选图不一致，请先修复备份')
                    ratio = None if step['kind'] == '人物1x4' else step['ratio']
                    key = (checksum, ratio)
                    if key not in technical_checks:
                        technical_checks[key] = self.skill.technical_check(str(source), ratio)
                        if images.digest(source) != checksum:
                            raise ValueError('技术检查期间选图字节已变化，请重新核对：' + str(source))
                    check = technical_checks[key]
                    if not check['passed']:
                        raise ValueError('图片技术检查未通过')
                    collected.append((row, job, step, asset, (check, checksum)))
            complete = not (missing_outputs or pending_backup_ids)
            if scoped:
                result.update(adopted=bool(collected), complete=complete)
                if missing_outputs:
                    result['waiting_for_remaining_outputs'] = True
                if pending_backup_ids:
                    result.update(waiting_for_backup=True, pending_backup_ids=sorted(pending_backup_ids))
                if not collected:
                    return result
            current_basis = [[job['ast_id'], step_output_id(step), step['project_confirmation']['id']]
                             for _, job, step, _, _ in collected]
            previous_export = p.get('partial_export')
            if (previous_export and not p.get('export_pending') and not complete
                    and previous_export.get('items') == current_basis):
                self.validate_export_snapshot(p, previous_export, complete=False, file_digest=images.digest)
                return {**result, 'export': {key: value for key, value in previous_export.items() if key != 'items'}}

            root = Path(p['path'])
            manifest = load_manifest(root)
            logical = 'visual.asset.manifest'
            previous = [a for a in manifest['artifacts'] if a.get('logical_id') == logical]
            active = [entry for entry in previous if entry.get('status') in {'current', 'stale'}]
            previous_asset_snapshot = (asset_export_snapshot(root, active[0], previous_asset_snapshot,
                                                             file_digest=images.digest)
                                       if len(active) == 1 else None)
            previous_outputs = (previous_asset_snapshot[1] or {}) if previous_asset_snapshot else {}
            if not p.get('export_pending'):
                serial = max([int(a['revision'][1:]) for a in previous if re.fullmatch(r'R\d+', a.get('revision', ''))] or [0]) + 1
                p['export_pending'] = {'id': uid(), 'revision': f'R{serial:02d}', 'at': now()}
                if scoped:
                    summary = {}
                    if website_package and all(self._same_website_asset(step['project_confirmation'],
                            website_package['assets'].get((job['ast_id'], step_output_id(step))), p['source'])
                            for _, job, step, _, _ in collected):
                        summary['website_review'] = {key: website_package[key] for key in ('approval', 'publication')}
                    if chat_package and chat_package.get('channel') == 'chat_confirmation' and all(all((step['project_confirmation'].get('chat_confirmation') or {}).get(key)
                            == chat_package[key] for key in ('authorization_id', 'authorization_ref', 'confirmation'))
                            for _, _, step, _, _ in collected):
                        summary['chat_confirmation'] = {key: chat_package[key]
                            for key in ('authorization_id', 'authorization_ref', 'confirmation')}
                    p['export_pending'].update(items=current_basis, complete=complete, summary=summary)
                self.save(p)
            transaction = p['export_pending']
            advances_after_retry = False
            if 'items' in transaction:
                advances_after_retry = transaction['items'] != current_basis or transaction['complete'] != complete
                by_confirmation = {tuple(key): record for key, record in zip(current_basis, collected)}
                if any(tuple(key) not in by_confirmation for key in transaction['items']):
                    raise ValueError('待恢复的确认输出已变化，请保留现场并核对')
                collected = [by_confirmation[tuple(key)] for key in transaction['items']]
                current_basis = transaction['items']
                complete = transaction['complete']
            revision = transaction['revision']
            asset_dir = '06_参考帧与临时素材/资产母版' if (root / '06_参考帧与临时素材').is_dir() and not (root / '04_资产').is_dir() else '04_资产/母版'
            folder = root / asset_dir / (revision + '_' + transaction['id'])
            folder.mkdir(parents=True, exist_ok=True)
            delegated_export = any(step['project_confirmation'].get('agent_review') for _, _, step, _, _ in collected)
            receipt_path = folder / ('资产采用记录.json' if delegated_export else '用户确认记录.json')
            path = folder / 'visual-asset-manifest.json'
            receipt = {'id': transaction['id'], 'at': transaction['at'],
                       'method': ('含授权代审的资产交接' if delegated_export
                                  else '逐项确认后整表确认交接' if complete else '已确认输出阶段交接'),
                       'source_narrative': p['source'], 'items': []}
            if delegated_export:
                receipt.update(kind='delegated-asset-export', revision=revision)
            if 'summary' in transaction:
                receipt.update(transaction['summary'])
            elif scoped and receipt_path.exists():
                previous_receipt = read(receipt_path)
                receipt.update({key: previous_receipt[key] for key in ('website_review', 'chat_confirmation')
                                if key in previous_receipt})
            elif website_package:
                receipt['website_review'] = {key: website_package[key] for key in ('approval', 'publication')}
            elif chat_package and chat_package.get('channel') == 'chat_confirmation':
                receipt['chat_confirmation'] = {key: chat_package[key]
                    for key in ('authorization_id', 'authorization_ref', 'confirmation')}
            if p['narrative'].get('_asset_table'):
                receipt['source_asset_table'] = p['narrative']['_asset_table']['source']
            paragraph_mode = p['narrative'].get('schema_version') == '2.0'
            out = {'schema_version': '2.0' if paragraph_mode else '1.1', 'project_id': p['name'], 'revision': revision,
                   'source_narrative': p['source'], 'source_asset_requirement_ids': list(dict.fromkeys(job['ast_id'] for _, job, _, _, _ in collected)),
                   'assets': [], 'scope_gaps': []}
            if paragraph_mode and p['narrative'].get('_product_usage'):
                out['product_usage'] = copy.deepcopy(p['narrative']['_product_usage'])
            if p['narrative'].get('_asset_choices'):
                out['asset_requirement_decisions'] = copy.deepcopy(p['narrative']['_asset_choices'])
                receipt['asset_requirement_decisions'] = copy.deepcopy(out['asset_requirement_decisions'])
            for row in p['rows']:
                req = row['requirement']
                row_outputs = [c for c in collected if c[1]['ast_id'] == req['asset_id']]
                if not row_outputs:
                    continue
                record = {'asset_id': req['asset_id'], 'category': req['category'], 'name': req['name'],
                          'status': 'confirmed',
                          ('paragraph_scope' if paragraph_mode else 'event_scope'): req['source_paragraph_ids' if paragraph_mode else 'source_event_ids'], 'shot_scope': [],
                          'interactions': req['interactions'], 'display_surfaces': [], 'orientations': [],
                          'required_states': req['required_states'], 'must_inherit': req['identity_constraints'],
                          'must_not_inherit': [], 'reference_paths': [],
                          'candidates': [], 'confirmed_asset': None, 'confirmed_outputs': []}
                if req['category'] == 'CHARACTER':
                    record['character_type'] = character_type(req)
                for n, (_, job, step, asset, (check, expected)) in enumerate(row_outputs, 1):
                    output_id = step_output_id(step)
                    original = previous_outputs.get((req['asset_id'], output_id))
                    confirmation = step['project_confirmation']
                    if (original and original['confirmation_ref'].partition('#')[2] == confirmation['id']
                            and original['sha256'] == confirmation.get('image_sha256')
                            and all(original.get(field) == step.get(field) for field in ('purpose', 'view', 'state'))
                            and original.get('output_kind') == step['kind']):
                        target = Path(original['path'])
                    else:
                        target = folder / req['asset_id'] / (req['asset_id'] + '_' + safe(step['key']) + '_' + safe(step['view']) + '_' + revision + Path(asset['original']).suffix.lower())
                    target.parent.mkdir(exist_ok=True)
                    source = Path(asset['original'])
                    created, matches = images.copy(source, target, expected=expected)
                    if not matches:
                        if not created:
                            raise ValueError('导出目标已有不同图片，保留现场：' + str(target))
                        raise ValueError('复制校验未通过')
                    verify_image_confirmation(target, confirmation, file_digest=images.digest)
                    candidate_id = req['asset_id'] + f'-C{n:02d}'
                    confirm_ref = str(receipt_path) + '#' + step['project_confirmation']['id']
                    refs = list(dict.fromkeys(req['reference_paths'] + [self.lib.get(i)['original'] for i in step.get('reference_ids', [])]))
                    if not refs:
                        # The original import location is historical provenance;
                        # the verified project copy is the durable current input.
                        refs = [str(self.copy_to_project(p, asset, images=images, expected=expected))]
                    refs = list(dict.fromkeys(resolve_reference(root, r, manifest['artifacts'], load_project_state(root))
                                              for r in refs))
                    record['reference_paths'] = list(dict.fromkeys(record['reference_paths'] + refs))
                    provenance = asset.get('provenance') or {}
                    authorization = step.get('authorization') or {}
                    external = provenance.get('external_generation')
                    actual_provider = provenance.get('provider') or authorization.get('provider') or job['provider']
                    actual_model = (provenance.get('model') or authorization.get('model') or
                                    ('Codex 内置 ImageGen' if actual_provider == 'imagegen' else job['model']))
                    technical_qc = {'path_exists': True, 'format': check['format'].lower(),
                                    'width': check['width'], 'height': check['height'],
                                    'naming_valid': True, 'reference_traceable': True}
                    if req['category'] != 'REAL_PRODUCT':
                        technical_qc['product_fact_check'] = 'not_applicable'
                    elif confirmation.get('agent_review'):
                        technical_qc['product_fact_check'] = 'passed'
                    record['candidates'].append({'candidate_id': candidate_id, 'path': str(target),
                        'status': 'candidate' if confirmation.get('agent_review') else 'awaiting_user_review',
                        'source_references': refs or [asset['source']],
                        'technical_qc': technical_qc,
                        'generation_record': dict(external) if external else ({'tool': actual_provider, 'model': actual_model,
                            'user_authorization_ref': str(receipt_path) + '#' + output_id} if step.get('authorization') and provenance.get('kind') != 'manual_result_import' else None)})
                    basis_field = 'agent_review_ref' if confirmation.get('agent_review') else 'user_confirmation_ref'
                    confirmed = {'candidate_id': candidate_id, 'path': str(target), basis_field: confirm_ref}
                    covered_states = (scene_state_coverage(p['narrative'], req).get(step['state'], [])
                                      if req['category'] == 'LOCATION' else [])
                    record['confirmed_outputs'].append({**confirmed, 'output_id': output_id, 'purpose': step['purpose'],
                                                        'view': step['view'], 'state': step['state'], 'output_kind': step['kind'],
                                                        **({'covered_states': covered_states} if len(covered_states) > 1 else {})})
                    prior_character_final = any(o['output_id'] in {step_output_id(s) for s in job['steps'] if s['kind'] == '人物1x4'} for o in record['confirmed_outputs'][:-1])
                    if record['confirmed_asset'] is None or (job.get('character_workflow') and step['kind'] == '人物1x4' and not prior_character_final):
                        record['confirmed_asset'] = confirmed
                    record['orientations'].append(step['view'])
                    record['display_surfaces'].append(step['purpose'])
                    record['must_not_inherit'] += step.get('must_not_inherit', [])
                    receipt['items'].append({'ast_id': job['ast_id'], 'output_id': output_id,
                                            'confirmation': step['project_confirmation'], 'library_asset_id': asset['id'],
                                            'prompt': step.get('prompt'), 'audit': step.get('audit'),
                                            'authorization': step.get('authorization'), 'provenance': provenance, 'backup': asset['backup']})
                out['assets'].append(record)
            for _, _, _, asset, (_, expected) in collected:
                if (images.digest(asset['original']) != expected
                        or images.digest(asset['backup']['path']) != expected):
                    raise ValueError('导出期间原图或知识库备份已变化，请重新核对：' + asset['name'])
            validate(out, 'visual-asset-manifest.schema.json')
            # Everything is copied and validated before updating the project's official state.
            self.fresh(p)
            artifact_id = 'visual-assets-' + transaction['id']
            manifest = load_manifest(root)
            registered = next((a for a in manifest['artifacts'] if a['artifact_id'] == artifact_id), None)
            if registered is None:
                # A failed export must never look like an unregistered legacy
                # confirmation to downstream readers of the master directory.
                registered = register_workspace_artifact(root, {'artifact_id': artifact_id,
                    'logical_id': logical, 'type': 'visual-asset-manifest', 'revision': revision,
                    'path_or_url': str(path),
                    'depends_on': ['narrative.package'] + (['narrative.assets'] if p['narrative'].get('_asset_table') else []),
                    'user_approved': not delegated_export, 'confirmation_ref': str(receipt_path)})
            if (registered.get('status') not in {'candidate', 'current'}
                    or registered.get('logical_id') != logical or registered.get('revision') != revision
                    or registered.get('user_approved') is delegated_export
                    or project_path(root, registered['path_or_url']) != path
                    or project_path(root, registered.get('confirmation_ref', '')) != receipt_path):
                raise ValueError('待恢复的资产导出登记不一致，请保留现场并核对')
            if registered.get('checksum') and (not path.is_file() or digest(path) != registered['checksum']):
                raise ValueError('待恢复的资产导出清单已变化或缺失，请保留原记录并核对')
            if path.exists():
                if read(path) != out:
                    raise ValueError('已有资产导出清单与当前确认不一致，保留原文件；请重新交接新版本')
            elif registered['status'] == 'current':
                raise ValueError('已确认资产导出清单缺失，请保留登记并恢复原文件')
            else:
                write(path, out)
            if receipt_path.exists():
                saved_receipt = read(receipt_path)
                # Backup timestamps/catalog metadata can change independently.
                # The saved confirmation is immutable; compare the exact approved
                # outputs and keep its original provenance and bytes on retry.
                def confirmation_basis(value):
                    if not isinstance(value, dict) or not isinstance(value.get('items'), list):
                        raise ValueError('已有资产确认记录结构无效，请保留原文件并核对')
                    try:
                        return [(i['ast_id'], i['output_id'], i['confirmation']) for i in value['items']]
                    except (KeyError, TypeError) as exc:
                        raise ValueError('已有资产确认记录制作项无效，请保留原文件并核对') from exc
                saved_basis = confirmation_basis(saved_receipt)
                if (saved_receipt.get('id') != receipt['id']
                        or saved_receipt.get('source_narrative') != receipt['source_narrative']
                        or ('website_review' in saved_receipt
                            and saved_receipt['website_review'] != receipt.get('website_review'))
                        or ('chat_confirmation' in saved_receipt
                            and saved_receipt['chat_confirmation'] != receipt.get('chat_confirmation'))
                        or saved_basis != confirmation_basis(receipt)):
                    raise ValueError('已有资产确认记录与当前制作项不一致，请保留原文件并核对')
            elif registered['status'] == 'current':
                raise ValueError('已确认资产记录缺失，请恢复原记录，不能重新编造确认')
            else:
                write(receipt_path, receipt)
            if registered['status'] == 'candidate':
                manifest = load_manifest(root)
                entry = next(a for a in manifest['artifacts'] if a['artifact_id'] == artifact_id)
                entry['checksum'] = digest(path)
                if delegated_export:
                    entry['asset_review_sha256'] = digest(receipt_path)
                save_manifest(root, manifest)
                promote_candidate(root, logical, artifact_id, previous_asset_snapshot=previous_asset_snapshot,
                                  file_digest=images.digest)
            # A current pending transaction already committed. Only complete its
            # missing state/cache/handoff; never rewrite its manifest or receipt.
            state = load_project_state(root)
            previous_ids = {a['artifact_id'] for a in previous}
            current = state.get('current_artifacts', [])
            state['current_artifacts'] = [a for a in current if a not in previous_ids | {artifact_id}]
            state['current_artifacts'].append(artifact_id)
            if not delegated_export and not any(artifact_id in c.get('artifact_ids', []) for c in state['user_confirmations']):
                methods = {step['project_confirmation']['method'] for _, _, step, _, _ in collected}
                method = '资产网页整表确认'
                if scoped:
                    method = next(iter(methods)) if len(methods) == 1 else '逐项资产确认交接'
                state['user_confirmations'].append({'at': transaction['at'], 'artifact_ids': [artifact_id],
                                                    'confirmation_ref': str(receipt_path), 'method': method})
            # Asset work can run alongside planning; never overwrite its stage or next_stage.
            write_project_state(root, state)
            handoff = folder / '资产交接.md'
            record_label = '资产采用记录' if delegated_export else '用户确认记录'
            handoff.write_text('# 项目资产交接\n\n- 正式清单：' + str(path) + '\n- ' + record_label + '：' + str(receipt_path) +
                '\n- 上游版本：' + p['source']['revision']
                + ('' if complete else '\n- 本版范围：已确认并完成备份的输出。其他输出待交接。')
                + '\n\n仅使用清单中已确认的主图或 confirmed_outputs 对应用途、状态、视角的图片。\n'
                '下一阶段按项目状态读取；本文件不自动执行下一阶段。\n', encoding='utf-8')
            product_ids = [a['asset_id'] for a in out['assets'] if a['category'] == 'REAL_PRODUCT']
            if product_ids:
                with handoff.open('a', encoding='utf-8') as stream:
                    if delegated_export:
                        stream.write('\n产品事实核对：' + '、'.join(product_ids) +
                                     '。逐图观察和来源见采用记录。仅明确记录的 product_fact_check 为通过。\n')
                    else:
                        stream.write('\n产品事实核对：' + '、'.join(product_ids) +
                                     ' 的图片已有准确用户选图确认；本导出入口未记录 Agent 产品事实核对结果。'
                                     '清单省略 product_fact_check 不表示通过，不得据此声称已核对产品事实。\n')
            if out.get('asset_requirement_decisions'):
                choices = out['asset_requirement_decisions']
                with handoff.open('a', encoding='utf-8') as stream:
                    stream.write('\n## 用户资产图需求调整\n\n当前表：' + choices['path'] + '\n')
                    for ast, decision in choices['excluded'].items():
                        stream.write('- ' + ast + ' ' + decision['name'] + '：用户明确不需要这项资产图，不得作为缺图补回。\n')
                    for req in choices['added']:
                        if req['asset_id'] not in choices['excluded']:
                            stream.write('- ' + req['asset_id'] + ' ' + req['name'] + '：出现位置 ' + '、'.join(req.get('source_paragraph_ids', req.get('source_event_ids', []))) + '\n')
            exported = {'folder': str(folder), 'manifest': str(path), 'handoff': str(handoff),
                        'artifact_id': artifact_id, 'revision': revision, 'at': now(), 'count': len(collected)}
            if complete:
                p['export'] = exported
                p.pop('partial_export', None)
            else:
                p['partial_export'] = {**exported, 'items': current_basis}
            p.pop('export_pending', None)
            self.save(p)
            if advances_after_retry:
                return self._export_locked(payload, website_package=website_package, chat_package=chat_package)
            return {**result, 'export': exported} if scoped else exported

    def continue_project(self, ident):
        p = self.get(ident)
        self.fresh(p)
        if not p.get('export'):
            raise ValueError('请先完成必需图片的审核和导出')
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

    def action(self, payload, *, background=True):
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
            ident = payload['project_id']
            mode = payload.get('mode', 'prepare')
            if mode == 'apply':
                return product_usage.apply(self, ident, payload.get('signature'), payload.get('result'))
            with self.lib.lock:
                p = self.get(ident)
                self.fresh(p)
                if mode == 'prepare':
                    return product_usage.prepare(self, p)
                if mode == 'codex-cli':
                    product_usage.schedule(self, p, use_cli=True, background=background)
                    return self.detail(p['id'])
                raise ValueError('未知的产品动作核对方式')
        from visual_asset_choices import change
        actions = {'character-output-mode': lambda: self.character_output_mode(payload), 'scene-reference': lambda: self.scene_reference(payload), 'asset-choice': lambda: change(self, payload), 'scene-output-mode': lambda: self.scene_output_mode(payload), 'prepare-reuse': lambda: self.prepare_reuse(payload), 'prepare-character': lambda: prepare_character(self, payload), 'production-route': lambda: self.production_route(payload), 'character-route': lambda: choose_character_route(self, payload), 'bind': lambda: self.bind(payload['path']), 'detail': lambda: self.detail(payload['project_id']),
                   'sync': lambda: self.sync(payload['project_id']), 'references': lambda: self.references(payload),
                   'plan': lambda: self.plan(payload['project_id'], payload.get('ast_id')), 'select': lambda: self.select(payload),
                   'confirm': lambda: self.confirm(payload), 'reopen': lambda: self.reopen(payload),
                   'clear-references': lambda: self.clear_references(payload), 'resolve': lambda: self.resolve(payload),
                   'batch': lambda: self.batch(payload), 'export': lambda: self.export(payload),
                   'continue': lambda: self.continue_project(payload['project_id'])}
        if payload.get('action') not in actions:
            raise ValueError('未知项目操作')
        return actions[payload['action']]()
