"""Vault-owned visual originals, Web cache, and generation requests.

Run with the bundled Python (Pillow required): serve --open, import --source PATH.
No generation runs at startup. Dreamina submission requires a reviewed step in the UI.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid
import webbrowser
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
# 第三方依赖只经 core.runtime_deps 解析：隔离目录必须证明属于当前解释器才会被加入 sys.path。
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from core.runtime_deps import ensure_runtime_deps  # noqa: E402
from core.artifacts import digest, verify_image_confirmation

RUNTIME_DEPS = ensure_runtime_deps(ROOT)
_skill_spec = importlib.util.spec_from_file_location('asset_library_skill', ROOT / 'skills/asset-image-assistant/library_runtime.py')
asset_skill = importlib.util.module_from_spec(_skill_spec)
_skill_spec.loader.exec_module(asset_skill)
_management_spec = importlib.util.spec_from_file_location('visual_asset_management', Path(__file__).with_name('visual_asset_management.py'))
management = importlib.util.module_from_spec(_management_spec)
_management_spec.loader.exec_module(management)
_codex_spec = importlib.util.spec_from_file_location('visual_asset_codex', Path(__file__).with_name('visual_asset_codex.py'))
codex_bridge = importlib.util.module_from_spec(_codex_spec)
_codex_spec.loader.exec_module(codex_bridge)
sys.path.insert(0, str(Path(__file__).resolve().parent)) if str(Path(__file__).resolve().parent) not in sys.path else None
from visual_asset_projects import Projects
import visual_asset_backup
import visual_asset_upload
import visual_asset_previs
DEFAULT_LIBRARY = ROOT / '.master-storyboarding' / 'visual-library'
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
VIDEO_EXTS = {'.mp4', '.mov', '.webm'}
CATEGORIES = ['人物单图', '大头照', '服装三视图', '人物1x4', '宠物', '场景', '物品', '产品', '风格', '视频参考', '其他']
ROLES = ['母版', '候选', '参考', '分镜', '历史', '待整理']
MODELS = ['5.0', '5.0Pro', '4.7', '4.6', '4.5', '4.1', '4.0']
_UNSCOPED_PROJECT = object()
# Only the exact, repository-owned import map in visual_asset_ui/index.html may run inline.
PREVIS_IMPORTMAP_SHA256 = 'dkIVxJOkhk+dsLekdBE1wjlHzMelv+mUnkUYQRC39To='


def external_generation_provenance(source, record):
    """Import declared external generation facts, never a local run or approval."""
    fields = {'tool', 'model', 'user_authorization_ref', 'prompt', 'reference_paths', 'image_sha256'}
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError('外部生成记录须包含工具、模型、授权引用、提示词、参考路径和结果SHA256')
    for key in ('tool', 'model', 'user_authorization_ref', 'prompt'):
        if not isinstance(record[key], str) or not record[key].strip() or len(record[key]) > (20000 if key == 'prompt' else 2000):
            raise ValueError('外部生成记录字段无效：' + key)
    if not source.is_file() or source.suffix.lower() not in IMAGE_EXTS:
        raise ValueError('带外部生成记录时须指定单张图片，不能导入整目录或视频')
    if record['image_sha256'] != digest(source):
        raise ValueError('外部生成结果SHA256与实际图片不符，未导入')
    paths = record['reference_paths']
    if not isinstance(paths, list) or len(paths) > 16 or any(not isinstance(p, str) for p in paths):
        raise ValueError('外部生成参考路径无效')
    references = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
            raise ValueError('外部生成参考图片不存在：' + str(path))
        with Image.open(path) as image:
            image.verify()
        references.append({'path': str(path), 'sha256': digest(path)})
    return {'kind': 'external_generation_import', 'external_generation':
            {k: record[k] for k in ('tool', 'model', 'user_authorization_ref')},
            'prompt': record['prompt'], 'prompt_sha256': hashlib.sha256(record['prompt'].encode()).hexdigest(),
            'reference_images': references, 'image_sha256': record['image_sha256']}


def template_prompt(kind, subject, notes, index=0):
    recipe = asset_skill.recipe(kind)
    index = asset_skill.template_step(kind, index=index)
    source = asset_skill.template_source(kind)
    if 'template' not in recipe:
        return (f'从真实参考图制作「{subject}」的{recipe["steps"][index]}。' + recipe['description'] +
                '\n用户补充：' + notes, str(source))
    block = asset_skill._template_excerpt(str(source), kind, index=index)['content']
    if kind == '环境':
        if index == 0:
            block = block.replace('以图一【SC-XXX 场景母图】为唯一场景参考', '以用户所选照片为场景参考')
            block += '\n本次45°图是这组场景的第一张母图和文件夹封面。'
        elif index == 1:
            block = block.replace('生成【SC-XXX 场景名称】的场景母图，用作后续所有机位的唯一空间锚点。', '以已确认的45°母图为唯一空间锚点，生成同一场景的正面平视图。')
            block += '\n摄影机：正面自然平视高度；仅改变机位，不重新设计场景。'
        elif index == 2:
            block = block.replace('图一【SC-XXX 场景母图】', '图一【已确认的45°母图】与图二【已确认的正面图】')
        else:
            block = block.replace('图一【场景母图】、图二【反向平视回望图】和图三【45°斜俯视全景图】', '图一【45°母图】、图二【正面图】和图三【反面图】')
    fields = {'永久固定项': '参考图中的空间边界、建筑、门窗、通道、家具数量和位置、材质、颜色与光源方向',
              '状态编号与仅允许变化项': '保持参考图中的时间、光照与场景状态；只改变本步规定的视角'}
    block = re.sub(r'【([^】]+)】', lambda m: '「' + subject + '」' if 'SC-XXX' in m[1] else fields.get(m[1], '参照输入图片中的' + m[1]), block)
    prefix = f'从参考照片中提取「{subject}」。以所选照片的可见细节为准，不替换人物身份或重新设计服装与空间。\n'
    suffix = '\n未展示的结构仅按可见线索保守补全；不把推测当作真实背面证据。'
    return prefix + block.strip() + suffix + ('\n用户补充：' + notes if notes else ''), str(source)


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex[:16]


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uid() + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def classify(path):
    """Filename suggestions are organization only, never confirmation evidence."""
    text = str(path)
    role = '待整理'
    for label, terms in [('参考', ['参考', 'reference', 'raw']), ('母版', ['母版']),
                         ('候选', ['候选']), ('分镜', ['正式分镜', '分段交付', '关键帧']),
                         ('历史', ['99_历史', '废弃', 'QC', '过程版本', '工作文件', '模型底稿'])]:
        if any(t in text for t in terms):
            role = label
    # Parent folders can contain several asset types (e.g. 道具与产品母版).
    text = Path(path).stem
    category = '其他'
    for label, terms in [('风格', ['STY-', '风格', '色卡']), ('物品', ['PR-', '道具', 'VEH-']),
                         ('产品', ['PD-', '造物者', 'creator-', 'creator_', '真实白金礼盒',
                                 '透明波纹玻璃碗', '玻璃面膜碗', '硅胶面膜刷',
                                 '20260518-100605', '20260518-162859', '真实挤入参考', '真实发泡参考']),
                         ('大头照', ['大头照', '大脸', '脸部母版', '头部特写', '面部母版']),
                         ('服装三视图', ['服装三视图', '服装_三视图', '服装1x3', '服装1×3']),
                         ('人物单图', ['PERSON-', '人物单图']),
                         ('人物1x4', ['人物1x4', '人物1×4', '1x4', '1×4', '1X4']),
                         ('宠物', ['BODY-', 'PET-', '角色外观', '宠物'])]:
        if any(t in text for t in terms):
            category = label
    return category, role


def current_category(item):
    # Keep the original stored label; old labels no longer shown in navigation
    # fall back to a specific filename match or Other, without losing images.
    if item.get('category') == '道具':
        item['category'] = '物品'
    elif item.get('category') == '角色外观':
        item['category'] = '宠物'
    elif item.get('category') not in CATEGORIES:
        item['category'] = classify(item.get('source', item.get('name', '')))[0]
    return item


def asset_record(item):
    current_category(item)
    item.pop('file_error', None)
    item['reference_only'] = item.get('record_scope') in {'project_reference', 'upload_reference'}
    if not Path(item['original']).is_file():
        item['file_error'] = '原图缺失，请恢复原文件：' + item['original']
    return item


def web_view(value):
    """Leave durable execution evidence intact; omit copied inputs from Web responses."""
    if isinstance(value, list):
        return [web_view(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == 'request' and 'job_id' in value and 'index' in value:
            continue
        if key in {'prompt_history', 'result_history', 'generation_history', 'export_history'}:
            continue
        if key == 'authorization' and isinstance(item, dict):
            item = {k: v for k, v in item.items() if k != 'request'}
        result[key] = web_view(item)
    return result


class Library:
    def __init__(self, root, *, vault=None):
        self.root = Path(root).resolve()
        self.categories = CATEGORIES
        # Asset backup is a writable destination, not the read-only knowledge input.
        # Preserve explicit destinations, including ones that do not exist yet.
        fallback = self.root.parent / 'vault'
        self.vault = Path(vault or os.environ.get('MSB_VAULT_ROOT') or fallback).expanduser().resolve()
        from core.knowledge_runtime import bundled_root
        if self.vault.is_relative_to(bundled_root()) or self.root.is_relative_to(bundled_root()):
            raise ValueError('资产输出目录不能写入仓库内置知识包')
        self.cleanup_error = ''
        self.backup_wakeup = threading.Event()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.upload_lock = threading.Lock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, source_key TEXT UNIQUE, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS product_categories (id TEXT PRIMARY KEY, name TEXT NOT NULL)')
        self.reconcile_vault()
        self.projects = Projects(self, asset_skill, codex_bridge)
        self.project_template = template_prompt

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.root / 'library.sqlite3', timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def reconcile_vault(self):
        """Restore canonical file paths and saved catalog records after an interrupted Web run."""
        index_path = self.vault / '资产库' / '资产索引.json'
        if not index_path.is_file():
            return
        index = json.loads(index_path.read_text(encoding='utf-8'))
        root = index_path.parent.resolve()
        redundant = []
        with self.connect() as db:
            cached = {row[0]: json.loads(row[1]) for row in db.execute('SELECT id,data FROM assets')}
            for row in index.get('assets', []):
                ident = row['library_asset_id']
                if ident.startswith('raw:'):
                    continue  # DNG archive records are not Web-previewable assets.
                target = management.inside(root, root / row['destination'])
                record = row.get('record') or cached.get(ident)
                if record is None:
                    raise ValueError('知识库资产缺少完整记录：' + ident)
                previous = Path(cached.get(ident, {}).get('original', '')).resolve()
                if (target.is_file() and previous != target and previous.is_file() and
                        previous.is_relative_to((self.root / 'assets').resolve())):
                    if not visual_asset_backup.same_file(previous, target):
                        raise ValueError('知识库原图与网页旧副本不同，已暂停迁移：' + ident)
                    redundant.append(previous)
                record['original'] = str(target)
                record['backup'] = {'status': 'saved', 'path': str(target)} if target.is_file() else {
                    'status': 'pending', 'message': '原图缺失，请恢复原文件：' + str(target)}
                if ident in cached:
                    # Pending edits in the local cache have not reached the Vault yet.
                    if cached[ident].get('backup', {}).get('status') == 'pending':
                        record = cached[ident]
                        record['original'] = str(target)
                    db.execute('UPDATE assets SET data=? WHERE id=?', (json.dumps(record, ensure_ascii=False), ident))
                else:
                    db.execute('INSERT INTO assets VALUES (?,?,?)', (ident, 'vault|' + ident,
                               json.dumps(record, ensure_ascii=False)))
        for old in redundant:
            old.unlink()

    def items(self, table='assets'):
        assert table in {'assets', 'jobs'}
        with self.connect() as db:
            items = [json.loads(row[0]) for row in db.execute(f'SELECT data FROM {table} ORDER BY rowid DESC')]
            return [asset_record(item) for item in items] if table == 'assets' else [item for item in items if not item.get('record_deleted_at')]

    def get(self, ident, table='assets', *, require_file=False):
        assert table in {'assets', 'jobs'}
        with self.connect() as db:
            row = db.execute(f'SELECT data FROM {table} WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise ValueError('找不到这项记录')
        item = json.loads(row[0])
        if table == 'jobs' and item.get('record_deleted_at'):
            raise ValueError('这条生成记录已删除')
        if table == 'assets':
            asset_record(item)
            if require_file and item.get('file_error'):
                raise ValueError(item['file_error'])
            if require_file:
                verify_image_confirmation(item['original'], item.get('confirmation') or {})
                provenance = item.get('provenance') or {}
                if provenance.get('external_generation') and digest(Path(item['original'])) != provenance.get('image_sha256'):
                    raise ValueError('外部生成来源绑定的图片内容已变化，请保留原记录并重新导入候选')
        return item

    def operation_job(self, ident, project_id=_UNSCOPED_PROJECT):
        job = self.get(ident, 'jobs')
        if project_id is not _UNSCOPED_PROJECT and project_id != job.get('project_id'):
            raise ValueError('制作项不属于当前项目，请刷新后重试')
        return job

    def folder(self, payload):
        if payload.get('action') == 'reorder':
            order = payload.get('folder_ids')
            with self.lock:
                folders = {a['folder_id'] for a in self.items() if a.get('folder_id') and not a.get('trashed_at')}
                if not isinstance(order, list) or len(order) != len(folders) or set(order) != folders:
                    raise ValueError('文件夹列表已变化，请刷新后重试')
                with self.connect() as db:
                    for item in self.items():
                        if item.get('folder_id') in folders and not item.get('trashed_at'):
                            item['folder_order'] = order.index(item['folder_id'])
                            item['backup'] = {'status': 'pending'}
                            db.execute('UPDATE assets SET data=? WHERE id=?', (json.dumps(item, ensure_ascii=False), item['id']))
                self.backup_wakeup.set()
                return {'count': len(order)}
        ids = payload.get('ids')
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ValueError('请先选择图片')
        action = payload.get('action', 'put')
        if action not in {'put', 'remove', 'rename', 'cover'}:
            raise ValueError('未知文件夹操作')
        with self.lock:
            items = [self.get(i) for i in dict.fromkeys(ids)]
            if any(a.get('trashed_at') for a in items):
                raise ValueError('请先恢复垃圾桶图片')
            target = payload.get('folder_id')
            members = [a for a in self.items() if target and a.get('folder_id') == target and not a.get('trashed_at')]
            if target and not members:
                raise ValueError('文件夹不存在，请刷新')
            if action in {'rename', 'cover'} and (not target or any(a.get('folder_id') != target for a in items)):
                raise ValueError('请选择该文件夹中的图片')
            name = str(payload.get('name') or (members[0].get('folder_name') if members else '') or '新建文件夹').strip()[:100]
            folder_id = target or uid()
            changed = members if action in {'rename', 'cover'} else items
            with self.connect() as db:
                for item in changed:
                    if action == 'remove':
                        for key in ('folder_id', 'folder_name', 'folder_cover', 'folder_order'):
                            item.pop(key, None)
                        item['folder_manual'] = True
                    elif action == 'cover':
                        item['folder_cover'] = items[0]['id']
                    else:
                        item.update(folder_id=folder_id, folder_name=name)
                        if action == 'put':
                            item['folder_manual'] = True
                        if members:
                            item['folder_cover'] = members[0].get('folder_cover', members[0]['id'])
                            item['folder_order'] = members[0].get('folder_order', 0)
                        else:
                            item['folder_order'] = 1 + max((a.get('folder_order', -1) for a in self.items() if a.get('folder_id')), default=-1)
                    item['backup'] = {'status': 'pending'}
                    db.execute('UPDATE assets SET data=? WHERE id=?', (json.dumps(item, ensure_ascii=False), item['id']))
            self.backup_wakeup.set()
            return {'folder_id': folder_id, 'count': len(changed)}

    def auto_group_character(self, ident, identity, name):
        """Group 1x4 images only when a shared character identity is explicit."""
        if not identity or not name:
            return {'count': 0}
        folder_id = 'character-' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]
        with self.lock:
            item = self.get(ident)
            if item.get('category') != '人物1x4' or item.get('trashed_at'):
                return {'count': 0}
            if item.get('character_identity') not in (None, identity):
                return {'count': 0, 'reason': '已有其他人物身份，不覆盖'}
            peers = [a for a in self.items() if a['id'] != ident and a.get('category') == '人物1x4'
                     and not a.get('trashed_at') and a.get('character_identity') == identity]
            eligible = [a for a in [item, *peers] if not a.get('folder_manual')
                        and a.get('folder_id') in (None, folder_id)]
            existing = next((a for a in eligible if a.get('folder_id') == folder_id), None)
            folder_name = existing.get('folder_name') if existing else str(name).strip()[:90] + '｜人物1×4'
            cover = existing.get('folder_cover') if existing else min(eligible, key=lambda a: a.get('created_at', ''))['id'] if eligible else ident
            order = existing.get('folder_order') if existing else 1 + max(
                (a.get('folder_order') if isinstance(a.get('folder_order'), int) else -1
                 for a in self.items() if a.get('folder_id')), default=-1)
            changed = []
            item['character_identity'] = identity
            item['character_name'] = str(name).strip()[:100]
            changed.append(item)
            if len(eligible) >= 2:
                for asset in eligible:
                    asset.update(folder_id=folder_id, folder_name=folder_name,
                                 folder_cover=cover, folder_order=order)
                    if asset['id'] != ident:
                        changed.append(asset)
            with self.connect() as db:
                for asset in changed:
                    asset['backup'] = {'status': 'pending'}
                    db.execute('UPDATE assets SET data=? WHERE id=?',
                               (json.dumps(asset, ensure_ascii=False), asset['id']))
            self.backup_wakeup.set()
            return {'count': len(eligible) if len(eligible) >= 2 else 0,
                    'folder_id': folder_id if len(eligible) >= 2 else None}

    def product_categories(self):
        with self.connect() as db:
            return [{'id': row[0], 'name': row[1]} for row in db.execute('SELECT id,name FROM product_categories ORDER BY rowid')]

    def product_category(self, payload):
        action = payload.get('action')
        with self.lock, self.connect() as db:
            if action == 'create':
                name = str(payload.get('name', '')).strip()[:60]
                if not name:
                    raise ValueError('请填写分类名称')
                if any(c['name'] == name for c in self.product_categories()):
                    raise ValueError('这个产品分类已存在')
                ident = uid()
                db.execute('INSERT INTO product_categories VALUES (?,?)', (ident, name))
                return {'id': ident, 'name': name}
            ident = payload.get('id')
            if not any(c['id'] == ident for c in self.product_categories()):
                raise ValueError('产品分类不存在')
            if action == 'rename':
                name = str(payload.get('name', '')).strip()[:60]
                if not name or any(c['name'] == name and c['id'] != ident for c in self.product_categories()):
                    raise ValueError('分类名称为空或重复')
                db.execute('UPDATE product_categories SET name=? WHERE id=?', (name, ident))
                for asset in self.items():
                    if asset.get('product_category_id') == ident:
                        asset['backup'] = {'status': 'pending'}
                        db.execute('UPDATE assets SET data=? WHERE id=?', (json.dumps(asset, ensure_ascii=False), asset['id']))
                self.backup_wakeup.set()
                return {'id': ident, 'name': name}
            if action == 'delete':
                if any(a.get('product_category_id') == ident for a in self.items()):
                    raise ValueError('分类中还有资产，请先移出后再删除')
                db.execute('DELETE FROM product_categories WHERE id=?', (ident,))
                return {'id': ident}
            raise ValueError('未知产品分类操作')

    def assign_product_category(self, payload):
        ids, category_id = payload.get('ids'), payload.get('product_category_id') or ''
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ValueError('请先选择产品资产')
        with self.lock:
            if category_id and not any(c['id'] == category_id for c in self.product_categories()):
                raise ValueError('产品分类不存在')
            items = [self.get(i) for i in dict.fromkeys(ids)]
            if any(a.get('trashed_at') or a['category'] != '产品' for a in items):
                raise ValueError('所选内容包含非产品资产或垃圾桶资产')
            with self.connect() as db:
                for item in items:
                    item['product_category_id'] = category_id
                    item['backup'] = {'status': 'pending'}
                    db.execute('UPDATE assets SET data=? WHERE id=?', (json.dumps(item, ensure_ascii=False), item['id']))
            self.backup_wakeup.set()
            return {'count': len(items)}

    def delete_job(self, ident):
        with self.lock:
            job = self.get(ident, 'jobs')
            if job.get('project_id'):
                raise ValueError('项目制作记录由需求表管理；请在项目中重新选图或返工')
            if any(s['status'] in {'generating', 'submitting', 'submitted', 'querying'} or
                   s.get('audit', {}).get('status') in {'drafting', 'reviewing'} for s in job['steps']):
                raise ValueError('任务正在处理，请完成后再删除记录；删除不能取消生图')
            job['record_deleted_at'] = now()
            self.save(job, 'jobs')
            return {'message': '已从生成列表删除记录，资产库中的图片未删除'}

    def save(self, item, table='assets'):
        assert table in {'assets', 'jobs'}
        if table == 'assets':
            item = {k: v for k, v in item.items() if k not in {'file_error', 'reference_only'}}
        with self.connect() as db:
            if table == 'jobs':
                db.execute('INSERT INTO jobs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                           (item['id'], json.dumps(item, ensure_ascii=False)))
            else:
                db.execute('UPDATE assets SET data=? WHERE id=?', (json.dumps(item, ensure_ascii=False), item['id']))
        if table == 'assets' and item.get('record_scope') == 'project_reference':
            dump(Path(item['original']).parent / '参考记录.json', item)
        elif table == 'assets' and item.get('record_scope') == 'upload_reference':
            dump(Path(item['original']).parent / '参考记录.json', item)

    def store_project_reference(self, item, project):
        source = Path(item['original']).resolve()
        target = Path(project['path']).resolve() / '04_资产' / '项目参考' / item['id'] / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not visual_asset_backup.same_file(source, target):
            raise ValueError('项目参考已有不同文件，已保留两边原图')
        if not target.exists():
            shutil.copy2(source, target)
        if not visual_asset_backup.same_file(source, target):
            raise ValueError('项目参考副本校验失败，原图已保留')
        item.update(record_scope='project_reference', original=str(target), role='参考',
                    backup={'status': 'saved', 'storage': 'project', 'path': str(target)})
        item['provenance'].update(project_id=project['id'], confirmation_scope='reference_only')
        dump(target.parent / '参考记录.json', item)
        return item

    def relocate_project_reference(self, ident, project):
        """Move an explicitly reference-only legacy record without changing its ID or jobs."""
        with self.lock:
            item = self.get(ident, require_file=True)
            if (item['provenance'].get('confirmation_scope') != 'reference_only' or
                    item['provenance'].get('project_id') != project['id']):
                raise ValueError('此图片不是当前项目的专用参考')
            if any(ident in step.get('outputs', []) or step.get('selected') == ident
                   for job in self.items('jobs') for step in job['steps']):
                raise ValueError('此图片仍被用作制作结果，保留现状等待核对')
            previous = Path(item['original']).resolve()
            self.store_project_reference(item, project)
            root = (self.vault / '资产库').resolve()
            if previous.is_relative_to(root):
                management.guard(self.vault, 'begin')
                try:
                    index = json.loads((root / '资产索引.json').read_text(encoding='utf-8'))
                    index['assets'] = [r for r in index['assets'] if r['library_asset_id'] != ident]
                    management.write_index(root, index)
                    management.guard(self.vault, 'finish')
                except Exception:
                    management.guard(self.vault, 'abort')
                    raise
            self.save(item)
            if previous != Path(item['original']) and previous.is_relative_to(root):
                previous.unlink()
                note = previous.parent / '资产说明.md'
                if note.is_file():
                    note.write_text('此图仅作项目参考，已迁至：\n' + item['original'] + '\n', encoding='utf-8')
            return self.get(ident)

    def import_file(self, source, *, project='', role=None, category=None, provenance=None, reference_project=None,
                    upload_reference=False, metadata=None):
        metadata = metadata or {}
        if set(metadata) - {'name', 'source', 'name_history', 'name_source', 'upload_recognition', 'upload_crop'}:
            raise ValueError('无效导入记录字段')
        if not reference_project and (provenance or {}).get('confirmation_scope') == 'reference_only':
            reference_project = self.projects.get(provenance['project_id'])
        source = Path(source).resolve()
        video = source.suffix.lower() in VIDEO_EXTS
        if not source.is_file() or source.suffix.lower() not in IMAGE_EXTS | VIDEO_EXTS:
            raise ValueError('只支持存在的图片或 MP4、MOV、WEBM 视频')
        if video and category not in (None, '视频参考'):
            raise ValueError('视频只能归入视频参考')
        if not video and category == '视频参考':
            raise ValueError('视频参考分类需要视频文件')
        stat = source.stat()
        if video and stat.st_size > 2 * 1024 ** 3:
            raise ValueError('视频不能超过 2 GB')
        key = f'{os.path.normcase(str(source))}|{stat.st_size}|{stat.st_mtime_ns}'
        if reference_project:
            key = 'reference|' + reference_project['id'] + '|' + key
        elif upload_reference:
            key = 'upload-reference|' + key
        with self.lock, self.connect() as db:
            row = db.execute('SELECT data FROM assets WHERE source_key=?', (key,)).fetchone()
            if row:
                existing = json.loads(row[0])
                if (provenance or {}).get('external_generation') and existing.get('provenance') != provenance:
                    raise ValueError('此文件已导入但生成来源记录不同，保留原记录；请核对后另存候选')
                return existing, False
            if not video:
                with Image.open(source) as im:
                    im.verify()
            ident = uid()
            folder = self.root / 'assets' / ident
            folder.mkdir(parents=True)
            destination = folder / ('original' + source.suffix.lower())
            shutil.copy2(source, destination)
            duration = None
            if video:
                try:
                    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=width,height',
                                            '-select_streams', 'v:0', '-of', 'json', str(destination)],
                                           capture_output=True, text=True, timeout=30, check=True)
                    info = json.loads(probe.stdout)
                    stream = info['streams'][0]
                    width, height = int(stream['width']), int(stream['height'])
                    duration = float(info['format']['duration'])
                    if not 0 < duration <= 7200 or width < 1 or height < 1:
                        raise ValueError('视频时长或画面尺寸不符合要求')
                    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(destination), '-frames:v', '1',
                                    '-vf', 'scale=560:560:force_original_aspect_ratio=decrease',
                                    str(folder / 'thumbnail.webp')], capture_output=True, timeout=60, check=True)
                except (OSError, KeyError, IndexError, ValueError, subprocess.SubprocessError) as exc:
                    shutil.rmtree(folder)
                    raise ValueError('视频无法读取或生成封面：' + str(exc)) from exc
            else:
                with Image.open(destination) as im:
                    width, height = im.size
                    thumb = ImageOps.exif_transpose(im)
                    thumb.thumbnail((560, 560))
                    if thumb.mode not in {'RGB', 'RGBA'}:
                        thumb = thumb.convert('RGBA')
                    thumb.save(folder / 'thumbnail.webp', 'WEBP', quality=82)
            inferred_category, inferred_role = classify(source)
            item = dict(id=ident, name=source.stem, category='视频参考' if video else category or inferred_category,
                        role=role or inferred_role, project=project, tags=[], status='unreviewed',
                        source=str(source), original=str(destination), thumbnail=str(folder / 'thumbnail.webp'),
                        width=width, height=height, bytes=stat.st_size, created_at=now(),
                        confirmation=None, provenance=provenance or {}, group='', backup={'status': 'pending'},
                        media_type='video' if video else 'image')
            if video:
                item['duration'] = duration
            item = current_category(item)
            item.update(metadata)
            if not item['name']:
                item['name'] = f'{item["category"]}图片 · {ident[:6]}'
            if reference_project:
                self.store_project_reference(item, reference_project)
            elif upload_reference:
                item.update(record_scope='upload_reference', role='参考',
                            backup={'status': 'saved', 'storage': 'upload_reference', 'path': str(destination)})
                dump(folder / '参考记录.json', item)
            db.execute('INSERT INTO assets VALUES (?,?,?)', (ident, key, json.dumps(item, ensure_ascii=False)))
            db.commit()
            if reference_project:
                destination.unlink()
                return self.get(ident), True
            if upload_reference:
                return self.get(ident), True
            result = visual_asset_backup.run(self, management)
            if self.get(ident).get('backup', {}).get('status') != 'saved':
                db.execute('DELETE FROM assets WHERE id=?', (ident,))
                if folder.resolve().is_relative_to((self.root / 'assets').resolve()):
                    for temporary in folder.iterdir():
                        if temporary.is_file() and not temporary.is_symlink():
                            temporary.unlink()
                    folder.rmdir()
                raise ValueError('知识库入库失败：' + result['message'])
            return self.get(ident), True

    def upload_file(self, source, *, category=None, original_source=None):
        """Keep one usable asset; reject duplicates and unfixable ratios before import."""
        with self.upload_lock:
            return self._upload_file(source, category=category, original_source=original_source)

    def _upload_file(self, source, *, category=None, original_source=None):
        if category is not None and category not in CATEGORIES:
            raise ValueError('无效图片分类')
        source = Path(source).resolve()
        with Image.open(source) as picture:
            picture.verify()
        size = source.stat().st_size
        digest = visual_asset_upload.digest(source)
        for item in self.items():
            if item.get('trashed_at') or item.get('reference_only'):
                continue
            existing = Path(item['original'])
            if existing.is_file() and existing.stat().st_size == size and visual_asset_upload.digest(existing) == digest:
                if category and item['category'] != category:
                    return {'upload_processing': {'status': 'error', 'message':
                            f'已有图片属于「{item["category"]}」，项目位置是「{category}」；未入库'}}
                return {**item, 'upload_processing': {'status': 'duplicate', 'message': '与资产库已有图片完全相同，未重复入库'}}
        request_folder = (self.root / 'requests' / 'uploads' / uid()).resolve()
        try:
            recognition = visual_asset_upload.recognize(source, request_folder)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            return {'upload_processing': {'status': 'error', 'message': f'看图识别失败：{exc}；未入库'}}
        finally:
            reference = request_folder / 'reference'
            if reference.is_dir() and reference.is_relative_to((self.root / 'requests' / 'uploads').resolve()):
                for temporary in reference.iterdir():
                    if temporary.is_file() and not temporary.is_symlink():
                        temporary.unlink()
                reference.rmdir()
        if recognition['confidence'] == '低':
            return {'upload_processing': {'status': 'error', 'message': '类别识别把握较低，未入库；请人工核对'}}
        if category and recognition['category'] != category:
            return {'upload_processing': {'status': 'error', 'message':
                    f'项目位置是「{category}」，图片识别为「{recognition["category"]}」；未入库，请核对'}}
        chosen = category or recognition['category']
        destination = source.parent / (source.stem + '_自动调整比例.png')
        try:
            adjustment = visual_asset_upload.crop_to_category(source, destination, chosen, recognition)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            return {'upload_processing': {'status': 'error', 'message': f'比例处理失败：{exc}；未入库'}}
        if adjustment['status'] == 'skipped':
            return {'upload_processing': {'status': 'rejected', 'message':
                    f'比例不符合「{chosen}」要求，且无法安全调整：{adjustment["reason"]}；未入库'}}
        selected = destination if adjustment['status'] in {'cropped', 'padded', 'oriented'} else source
        try:
            visual_asset_upload.validate_dimensions(selected, chosen)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            return {'upload_processing': {'status': 'error', 'message': f'尺寸核对失败：{exc}；未入库'}}
        provenance = {'kind': 'upload_adjusted' if selected == destination else 'upload',
                      'original_source': str(original_source or source)}
        if adjustment['status'] == 'cropped':
            provenance['crop_box'] = adjustment['box']
        elif adjustment['status'] == 'padded':
            provenance['placement'] = adjustment['placement']
        generated_name = str(recognition.get('asset_name') or recognition.get('description') or '').strip()[:60]
        metadata = {'source': str(original_source or source), 'name': generated_name,
                    'name_history': [Path(original_source or source).stem],
                    'name_source': 'codex_cli' if recognition.get('asset_name') else 'description_fallback',
                    'upload_recognition': {'model': 'gpt-6-luna', 'result': recognition,
                        'suggested_category': recognition['category'],
                        'category_source': 'project_step' if category else 'image'},
                    'upload_crop': adjustment}
        asset, _ = self.import_file(selected, project='手动导入',
                                    role='候选' if selected == destination else '参考',
                                    category=chosen, provenance=provenance, metadata=metadata)
        if visual_asset_upload.digest(asset['original']) != visual_asset_upload.digest(selected):
            raise ValueError('入库副本校验失败，原文件尚未删除')
        return {**asset, 'upload_processing': {'status': 'added', 'message':
                f'已自动规范为 {asset["width"]}×{asset["height"]}（{adjustment["ratio"]}），已入库'
                if selected == destination else '原图尺寸符合要求，已入库'}}

    def local_upload_source(self, source):
        """Keep finished-asset uploads out of managed project and catalog folders."""
        source = Path(source)
        if source.is_symlink() or not source.is_file():
            raise ValueError('所选文件不是普通图片')
        source = source.resolve()
        protected_roots = [ROOT.resolve(), self.vault.resolve(),
                           self.root, *(Path(p['path']).resolve() for p in self.projects.summaries())]
        if any(source.is_relative_to(root) for root in protected_roots):
            raise ValueError('所选文件位于项目、知识库或资产库中，未上传也未删除')
        for asset in self.items():
            if any(source == Path(value).resolve() for value in
                   (asset.get('source'), asset.get('original'), asset.get('thumbnail'),
                    asset.get('backup', {}).get('path')) if value):
                raise ValueError('所选文件已被资产记录引用，未上传也未删除')
        if source.suffix.lower() not in IMAGE_EXTS or source.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('只支持不超过 64 MB 的 PNG、JPG、WEBP、BMP、TIFF 图片；文件未删除')
        return source

    def upload_local_file(self, path, *, category=None):
        """Process a temporary copy while always preserving the selected source."""
        source = self.local_upload_source(path)
        source_digest = visual_asset_upload.digest(source)
        staged_folder = (self.root / 'uploads' / uid()).resolve()
        staged_folder.mkdir(parents=True)
        staged = staged_folder / source.name
        try:
            shutil.copy2(source, staged)
            if visual_asset_upload.digest(staged) != source_digest:
                raise ValueError('上传暂存副本校验失败，电脑原文件未删除')
            result = self.upload_file(staged, category=category, original_source=source)
            result['upload_processing']['source_deleted'] = False
            result['upload_processing']['message'] += '；电脑原文件保留'
            return result
        finally:
            if staged_folder.is_relative_to((self.root / 'uploads').resolve()) and staged_folder.is_dir():
                for temporary in staged_folder.iterdir():
                    if temporary.is_file() and not temporary.is_symlink():
                        temporary.unlink()
                staged_folder.rmdir()

    def prepare_upload(self, payload):
        """Turn an input-only record into an existing Skill generation job."""
        source = self.get(payload['source_id'], require_file=True)
        if source.get('record_scope') != 'upload_reference':
            raise ValueError('请选择上传的制作参考文件')
        kind = payload.get('kind', '')
        recipes = asset_skill.profile()['recipes']
        if kind and (kind not in recipes or kind == '人物1x4'):
            raise ValueError('人物1×4需要已确认脸部和服装，请从照片转资产制作')
        subject = str(payload.get('subject', '')).strip()[:200]
        options = {'kind': kind, 'subject': subject}
        recognition = source.get('upload_recognition') if source.get('upload_options') == options else None
        source['upload_options'] = options
        if not recognition:
            source.pop('upload_recognition', None)
        self.save(source)
        refs = [source]
        if source.get('media_type') == 'video':
            refs = [self.get(ident, require_file=True) for ident in source.get('frame_ids', [])]
            if not refs:
                frames = visual_asset_upload.video_frames(source['original'],
                    Path(source['original']).parent / 'frames', source['duration'])
                for frame, timestamp in frames:
                    record, _ = self.import_file(frame, project='上传参考', upload_reference=True,
                        provenance={'kind': 'video_frame', 'source_id': source['id'], 'timestamp': timestamp})
                    refs.append(record)
                source['frame_ids'] = [record['id'] for record in refs]
                self.save(source)
        if not recognition:
            recognition = visual_asset_upload.recognize([r['original'] for r in refs],
                self.root / 'requests' / 'uploads' / uid(), kind=kind, subject=subject)
        source['upload_recognition'] = recognition
        self.save(source)
        if recognition['confidence'] == '低' or (recognition.get('has_multiple_subjects') and not subject):
            return {'id': source['id'], 'name': source['name'], 'upload_processing': {
                'status': 'needs_choice', 'message': '无法唯一确定提取主体，请指定要提取的人物、服装或物品；源文件保留',
                'source_deleted': False}}
        kind = kind or {'大头照': '大头照', '服装三视图': '服装', '宠物': '角色外观',
                        '场景': '环境', '物品': '物品', '产品': '产品', '风格': '风格'}.get(recognition['category'])
        if not kind:
            return {'id': source['id'], 'name': source['name'], 'upload_processing': {
                'status': 'needs_choice', 'message': '未能确定独立资产类型，请选择提取类型；源文件保留', 'source_deleted': False}}
        subject = str(subject or recognition.get('asset_name') or recognition['description']).strip()[:200]
        # Re-selecting the same unchanged input must not create another identical job.
        existing = next((j for j in self.items('jobs') if j.get('upload_source_id') == source['id']
                         and j['kind'] == kind and j['title'] == subject), None)
        if existing:
            return {'id': source['id'], 'job_id': existing['id'], 'name': source['name'],
                    'upload_processing': {'status': 'duplicate', 'message': '已有此文件的制作记录，已保留源文件',
                                          'source_deleted': False}}
        job = self.create_job({'source_id': refs[0]['id'], 'kind': kind, 'subject': subject,
            'notes': '从上传参考重新生成干净的独立资产，按该类别规范构图。保留主体身份或服装结构；'
                     '去除原画面杂物、叠加文字与水印、临时动作和剧情布光；'
                     '保留主体本身的图案、文字、标识与产品标签。不可见细节标明推测。',
            'must_inherit': {'大头照': '参考主体的面部身份、五官比例、发型和发色',
                             '服装': '目标服装的款式、剪裁、颜色、材质、图案及配饰关系'}.get(kind,
                             '参考中目标主体的固定外观特征，按当前资产类型的规范保留'),
            'must_not_inherit': '原背景、外加文字与水印、临时动作；大头照不继承衣服，服装图不继承脸部身份'})
        job.update(upload_source_id=source['id'], extra_reference_ids=[r['id'] for r in refs[1:]])
        self.save(job, 'jobs')
        message = '参考已保存，正在按资产规则准备重新生成；源文件保留'
        try:
            self.audit_prompt({'id': job['id'], 'index': 0, 'mode': 'write'})
        except ValueError as exc:
            message = '制作记录已建立，提示词准备需重试：' + str(exc) + '；源文件保留'
            job['steps'][0]['audit'] = {'status': 'error', 'message': str(exc)}
            self.save(job, 'jobs')
        return {'id': source['id'], 'job_id': job['id'], 'name': source['name'],
                'upload_processing': {'status': 'prepared', 'message': message, 'source_deleted': False}}

    def pick_files(self, payload):
        if not isinstance(payload.get('multiple', False), bool):
            raise ValueError('选文件参数无效')
        kind = payload.get('kind', '')
        if kind and (kind not in asset_skill.profile()['recipes'] or kind == '人物1x4'):
            raise ValueError('无效提取类型')
        paths = visual_asset_upload.pick_local_files(payload.get('multiple', False), media=True)
        results = []
        for path in paths:
            record = None
            try:
                source = Path(path).resolve()
                if source.suffix.lower() not in IMAGE_EXTS | VIDEO_EXTS or not source.is_file():
                    raise ValueError('请选择图片或 MP4、MOV、WEBM 视频')
                if source.suffix.lower() in IMAGE_EXTS and source.stat().st_size > 64 * 1024 ** 2:
                    raise ValueError('图片不能超过 64 MB')
                record, _ = self.import_file(source, project='上传参考', upload_reference=True,
                                            provenance={'kind': 'uploaded_source'})
                result = self.prepare_upload({**payload, 'source_id': record['id']})
                results.append(result)
            except (OSError, ValueError, subprocess.SubprocessError, Image.DecompressionBombError) as exc:
                results.append({'id': record['id'] if record else None, 'name': Path(path).name, 'upload_processing':
                                {'status': 'error', 'message': str(exc) + '；源文件保留', 'source_deleted': False}})
        return {'cancelled': not paths, 'results': results}

    def pick_upload(self, payload):
        category = payload.get('category')
        multiple = payload.get('multiple', False)
        if category is not None and category not in CATEGORIES:
            raise ValueError('无效图片分类')
        if not isinstance(multiple, bool):
            raise ValueError('选图参数无效')
        paths = visual_asset_upload.pick_local_files(multiple)
        results = []
        for path in paths:
            try:
                results.append(self.upload_local_file(path, category=category))
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                results.append({'name': Path(path).name, 'upload_processing':
                                {'status': 'error', 'message': str(exc), 'source_deleted': False}})
        return {'cancelled': not paths, 'results': results}

    def pick_video(self, payload):
        return self.pick_files(payload)

    def import_tree(self, source, *, external_generation=None):
        source = Path(source).expanduser().resolve()
        if not source.exists() or source == self.root or source.is_relative_to(self.root):
            raise ValueError('来源不存在，或来源是资产库自身')
        protected = [self.root, ROOT.resolve(), self.vault.resolve(),
                     *(Path(p['path']).resolve() for p in self.projects.summaries())]
        if any(source.is_relative_to(path) for path in protected):
            raise ValueError('任务或知识库内的图片不回流到网页；请从知识库选择现有资产')
        provenance = external_generation_provenance(source, external_generation) if external_generation is not None else None
        files = [source] if source.is_file() else sorted(source.rglob('*'))
        result = {'source': str(source), 'added': 0, 'existing': 0, 'errors': [], 'time': now()}
        for path in files:
            if path.suffix.lower() not in IMAGE_EXTS | VIDEO_EXTS:
                continue
            try:
                # The selected parent may contain protected projects/Vault files.
                # Check both the walked path and the actual symlink target before
                # import_file can read or copy bytes; retain hidden-source exclusions.
                resolved = path.resolve(strict=True)
                if any(p in {'.git', '.claude', '.obsidian'} for p in (*path.parts, *resolved.parts)):
                    continue
                if any(path.is_relative_to(root) or resolved.is_relative_to(root) for root in protected):
                    raise ValueError('任务、知识库或资产库内的图片不回流到网页：' + str(resolved))
                if not resolved.is_file():
                    continue
                parts = resolved.parts
                project = parts[parts.index('定稿项目') + 1] if '定稿项目' in parts else source.stem
                _, added = self.import_file(resolved, project=project, provenance=provenance)
                result['added' if added else 'existing'] += 1
            except (OSError, RuntimeError, ValueError, Image.DecompressionBombError) as exc:
                result['errors'].append({'path': str(path), 'error': str(exc)})
        dump(self.root / 'imports' / (uid() + '.json'), result)
        return result

    def update(self, payload):
        with self.lock:
            item = self.get(payload['id'])
            if item.get('trashed_at'):
                raise ValueError('请先从垃圾桶恢复图片')
            previous_category = item['category']
            previous_name = item['name']
            for key in ['name', 'category', 'role', 'group']:
                if key in payload:
                    item[key] = str(payload[key]).strip()[:300]
            if item['category'] not in CATEGORIES or item['role'] not in ROLES or not item['name']:
                raise ValueError('名称、分类或用途无效')
            if (item.get('media_type') == 'video') != (item['category'] == '视频参考'):
                raise ValueError('视频参考与图片分类不能互换')
            if 'product_category_id' in payload:
                ident = payload['product_category_id'] or ''
                if ident and not any(c['id'] == ident for c in self.product_categories()):
                    raise ValueError('产品分类不存在')
                if ident and item['category'] != '产品':
                    raise ValueError('请先把资产归入产品总类')
                item['product_category_id'] = ident
            if item['category'] != '产品':
                item.pop('product_category_id', None)
            if item['category'] != previous_category:
                management.manage(self, [item['id']], 'move', item['category'])
                moved = self.get(item['id'])
                item.update(original=moved['original'], thumbnail=moved['thumbnail'])
            if 'tags' in payload:
                item['tags'] = [str(t).strip()[:60] for t in payload['tags']][:30]
            if item['name'] != previous_name:
                item.setdefault('name_history', []).append(previous_name)
                item['name_source'] = 'user'
            item['backup'] = {'status': 'pending'}
            self.backup_wakeup.set()
            self.save(item)
            return item

    def group_scene(self, payload):
        name = str(payload.get('name', '')).strip()[:200]
        views = payload.get('views', [])
        labels = {'正面', '反面', '45°', '俯瞰'}
        if not name or not isinstance(views, list) or not 1 <= len(views) <= 4:
            raise ValueError('请填写场景名称，并选择 1 至 4 张图片')
        if any(not isinstance(v, dict) or v.get('view') not in labels or not isinstance(v.get('id'), str) for v in views):
            raise ValueError('请选择每张图片对应的视角')
        if len({v['id'] for v in views}) != len(views) or len({v['view'] for v in views}) != len(views):
            raise ValueError('同一场景中的图片和视角不能重复')
        with self.lock:
            items = [self.get(v['id']) for v in views]
            if any(i.get('trashed_at') or i['category'] != '场景' for i in items):
                raise ValueError('请先将图片移入场景分类，并恢复已删除的图片')
            scene_id = uid()
            with self.connect() as db:
                for item, view in zip(items, views):
                    item.update(scene_id=scene_id, group=name, scene_view=view['view'])
                    item['backup'] = {'status': 'pending'}
                    db.execute('UPDATE assets SET data=? WHERE id=?', (json.dumps(item, ensure_ascii=False), item['id']))
            self.backup_wakeup.set()
            return {'scene_id': scene_id, 'count': len(items)}

    def confirm(self, ident):
        with self.lock:
            item = self.get(ident, require_file=True)
            if item['reference_only']:
                raise ValueError('此图仅作项目参考，不能作为成品资产确认')
            if item.get('trashed_at'):
                raise ValueError('请先从垃圾桶恢复图片')
            if item.get('technical_check', {}).get('passed') is False:
                raise ValueError('图片技术检查未通过：' + item['technical_check']['message'])
            item['status'] = 'confirmed'
            item['confirmation'] = {'at': now(), 'method': '用户在资产库点击确认此图片',
                                    'image_sha256': digest(Path(item['original']))}
            if item.get('backup'):
                item['backup'] = {'status': 'pending'}
                self.backup_wakeup.set()
            self.save(item)
            for job in self.items('jobs'):
                if job.get('project_id'):
                    continue  # Library confirmation never confirms a project's AST output.
                changed = False
                for step in job['steps']:
                    if step['status'] != 'confirmed' and ident in step.get('outputs', []):
                        step['selected'] = ident
                        step['status'] = 'confirmed'
                        changed = True
                if changed:
                    self.save(job, 'jobs')
            return item

    def export(self, ids):
        if not ids:
            raise ValueError('先选择资产')
        folder = self.root / 'selections' / uid()
        folder.mkdir(parents=True)
        entries = []
        for ident in dict.fromkeys(ids):
            item = self.get(ident, require_file=True)
            if item['reference_only']:
                raise ValueError('项目参考图不能作为成品资产导出')
            if item.get('trashed_at'):
                raise ValueError('请先从垃圾桶恢复图片再导出')
            source = Path(item['original'])
            backed = Path(item.get('backup', {}).get('path', ''))
            if backed.suffix.lower() == source.suffix.lower() and backed.name.startswith(visual_asset_backup.PREFIX[item['category']] + '-'):
                filename = backed.name
            else:
                name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', item['name']).strip(' .')[:28] or '资产'
                view = '参考片段' if item.get('media_type') == 'video' else '单图'
                filename = f'{visual_asset_backup.PREFIX[item["category"]]}-{ident[:8]}_{name}_{view}_v001{source.suffix.lower()}'
            destination = folder / ident / filename
            destination.parent.mkdir()
            shutil.copy2(item['original'], destination)
            entries.append({**item, 'original': str(destination)})
        dump(folder / 'selection.json', {'schema_version': '1.0', 'created_at': now(), 'assets': entries,
              'product_categories': self.product_categories(),
              'purpose': '项目资产参考选用；不是当前项目的确认清单，不自动创建 AST 或推进阶段'})
        return {'folder': str(folder), 'manifest': str(folder / 'selection.json'), 'count': len(entries)}

    def create_job(self, p):
        source = self.get(p['source_id'], require_file=True)
        if source.get('media_type') == 'video':
            raise ValueError('视频参考不能直接用于照片转资产，请先选图片')
        if source.get('trashed_at'):
            raise ValueError('请先从垃圾桶恢复参考图片')
        if p['kind'] not in asset_skill.profile()['recipes'] or p.get('provider', 'imagegen') not in {'imagegen', 'dreamina'}:
            raise ValueError('请选择有效的输出类型和生成工具')
        subject = str(p.get('subject', '')).strip()
        if not subject:
            raise ValueError('请写明照片中要提取的衣服或环境')
        names = asset_skill.recipe(p['kind'])['steps']
        secondary = None
        if p['kind'] == '人物1x4':
            secondary = self.get(p.get('secondary_id', ''), require_file=True)
            if (source['category'] != '服装三视图' or secondary['category'] != '大头照' or
                    any(a['status'] != 'confirmed' or a.get('trashed_at') for a in [source, secondary])):
                raise ValueError('人物1×4须选择已确认服装三视图及已确认大头照')
        prompt, template = template_prompt(p['kind'], subject, str(p.get('notes', ''))[:3000])
        job = dict(id=uid(), title=subject[:200], kind=p['kind'], source_id=source['id'],
                   provider=p.get('provider', 'imagegen'), model=p.get('model', '5.0'), created_at=now(), workflow_version=3,
                   notes=str(p.get('notes', ''))[:3000], secondary_id=secondary['id'] if secondary else None,
                   must_inherit=str(p.get('must_inherit', ''))[:2000], must_not_inherit=str(p.get('must_not_inherit', ''))[:2000],
                   steps=[dict(name=n, status='waiting', outputs=[]) for n in names])
        job['steps'][0].update(prompt=prompt, template_source=template)
        if job['provider'] == 'dreamina' and job['model'] not in MODELS:
            raise ValueError('不支持此即梦模型')
        self.save(job, 'jobs')
        return job

    def step_request(self, job, index):
        if job.get('project_id'):
            return self.projects.request(job, index)
        if not isinstance(index, int) or not 0 <= index < len(job['steps']):
            raise ValueError('无效视图序号')
        step = job['steps'][index]
        refs = [self.get(job['source_id'], require_file=True)]
        extras = [self.get(ident, require_file=True) for ident in job.get('extra_reference_ids', [])]
        if job['kind'] == '人物1x4':
            face = self.get(job.get('secondary_id', ''), require_file=True)
            if refs[0]['category'] != '服装三视图' or face['category'] != '大头照':
                raise ValueError('人物1×4须回到独立服装三视图及独立大头照')
            refs = [refs[0], face, *extras]
            if any(a['category'] == '人物1x4' for a in refs):
                raise ValueError('人物1×4重生不得输入旧人物板；请使用原独立大头照和服装')
            if any(a['status'] != 'confirmed' or a.get('trashed_at') for a in refs):
                raise ValueError('人物1×4参考必须保持已确认且未删除')
        else:
            refs.extend(extras)
            if job.get('secondary_id'):
                refs.append(self.get(job['secondary_id'], require_file=True))
        if index:
            dependency = list(range(index)) if job.get('workflow_version', 0) >= 2 else (list(range(3)) if job['kind'] == '环境' and index == 3 else [0])
            refs = []
            for n in dependency:
                prior = job['steps'][n]
                if prior['status'] != 'confirmed':
                    raise ValueError('先确认前置视图，再生成这个角度')
                refs.append(self.get(prior['selected'], require_file=True))
        if any(a.get('trashed_at') for a in refs):
            raise ValueError('参考图片已在垃圾桶，请先恢复')
        prompt = f"从参考图提取「{job['title']}」，制作{job['kind']}资产的{step['name']}独立图片。"
        if job['kind'] == '服装':
            prompt += '衣服使用同一中性无头人台展示，纯浅灰背景、均匀柔光，保留款式、剪裁、颜色、材质、图案与配饰关系。各视图比例一致。'
        else:
            prompt += '以参考图固定的建筑、门窗、家具数量与位置、材质、光源方向为准，输出同一个空间的指定视角，画面清楚交代空间关系。'
        prompt += '照片未展示的部分按已有可见结构作保守补全；这是待确认的设计推测，不作为真实背面证据。'
        if job['notes']:
            prompt += '\n用户补充：' + job['notes']
        template = ''
        if job.get('workflow_version', 0) >= 2:
            prompt, template = template_prompt(job['kind'], job['title'], job['notes'], index)
        prompt = step.get('prompt', prompt)
        return {'job_id': job['id'], 'kind': job['kind'], 'index': index, 'view': step['name'], 'provider': job['provider'],
                'model': job['model'] if job['provider'] == 'dreamina' else 'Codex 内置 ImageGen（宿主管理模型）',
                'reference_paths': [r['original'] for r in refs], 'prompt': prompt, 'count': 1,
                'ratio': asset_skill.recipe(job['kind'])['ratio'], 'resolution': '2k', 'output_is_candidate': True,
                'must_inherit': job.get('must_inherit', ''), 'must_not_inherit': job.get('must_not_inherit', ''),
                'reference_records': [{k: a.get(k) for k in ('id', 'category', 'status', 'original')} for a in refs],
                'template_source': step.get('template_source', template)}

    def save_prompt(self, p):
        with self.lock:
            job = self.operation_job(p['id'], p.get('project_id', _UNSCOPED_PROJECT))
            index = int(p['index'])
            self.step_request(job, index)
            step = job['steps'][index]
            if step.get('audit', {}).get('status') in {'drafting', 'reviewing'}:
                raise ValueError('Codex正在处理此提示词，请完成后再修改')
            if step['status'] != 'waiting':
                raise ValueError('已提交或已有结果的提示词不能修改；请新建一组资产')
            prompt = str(p.get('prompt', '')).strip()
            if not prompt or len(prompt) > 20000:
                raise ValueError('提示词不能为空，且不能超过20000字')
            provider = p.get('provider', job['provider'])
            model = p.get('model', job['model'])
            if provider not in {'imagegen', 'dreamina'} or model not in MODELS:
                raise ValueError('无效生成工具或模型')
            if step.get('prompt') != prompt:
                step['audit'] = {'status': 'stale'}
                step.pop('prompt_approval', None)
            step['prompt'] = prompt
            if p.get('approve') is True:
                audit = step.get('audit', {})
                if not asset_skill.approved(step) or audit.get('prompt') != prompt:
                    raise ValueError('请先让Codex审查当前提示词并通过，再确认生成')
                step['prompt_approval'] = {'prompt': prompt, 'at': now()}
            job.update(provider=provider, model=model)
            self.save(job, 'jobs')
            return self.prepare(job['id'], index)

    def audit_prompt(self, p):
        with self.lock:
            job = self.operation_job(p['id'], p.get('project_id', _UNSCOPED_PROJECT))
            index = int(p['index'])
            request = self.step_request(job, index)
            if p.get('codex_model'):
                request['codex_model'] = str(p['codex_model'])
            step = job['steps'][index]
            if step['status'] != 'waiting':
                raise ValueError('已有提交或结果，不能重写提示词；请新建一组')
            if step.get('audit', {}).get('status') in {'drafting', 'reviewing'}:
                raise ValueError('此提示词正在处理，请勿重复提交')
            if sum(s.get('audit', {}).get('status') in {'drafting', 'reviewing'}
                   for j in self.items('jobs') for s in j['steps']) >= 3:
                raise ValueError('已有 3 项提示词处理中，请完成后再发起')
            exe = codex_bridge.executable()
            if not exe:
                raise ValueError('未找到本机Codex CLI，请先安装并登录')
            mode = p.get('mode', 'write')
            if mode not in {'write', 'review', 'repair'}:
                raise ValueError('无效的Codex处理方式')
            run_id = uid()
            previous_issues = step.get('audit', {}).get('issues', [])
            request['repair_issues'] = previous_issues if mode == 'repair' else []
            step['audit'] = {'status': 'drafting' if mode != 'review' else 'reviewing', 'run_id': run_id, 'started_at': now(), 'message': 'Luna正在准备并自查提示词'}
            step.pop('prompt_approval', None)
            self.save(job, 'jobs')
            threading.Thread(target=self._audit_worker, args=(exe, job['id'], index, run_id, mode, request), daemon=True).start()
            return {'message': 'Codex已开始看图处理，完成后提示词和审查结果会显示在页面', 'run_id': run_id}

    def _audit_worker(self, exe, ident, index, run_id, mode, request):
        folder = self.root / 'requests' / ident / str(index) / 'codex' / run_id
        try:
            rules, evidence = asset_skill.prepare(folder, request)
            with self.lock:
                prepared_job = self.get(ident, 'jobs')
                if prepared_job['steps'][index].get('audit', {}).get('run_id') != run_id:
                    return
                prepared_job['steps'][index]['audit']['skill_execution'] = evidence
                self.save(prepared_job, 'jobs')
            # Review is read-only even if the model returns an unsolicited rewrite.
            draft = {'prompt': request['prompt'], 'issues': request.get('repair_issues', [])}
            review = codex_bridge.run(exe, folder, 'review' if mode == 'review' else 'draft',
                                      request, draft, rules)
            original_issues = list(review.get('issues', []))
            review = asset_skill.validate_review(review, request.get('mode', 'asset-library'), request.get('kind'))
            missing_checks = any(s.startswith('Skill审查项缺失：') for s in review['issues'])
            # At most one extra Luna call. Missing evidence is checked again,
            # never fabricated; a review must still preserve the user's text.
            can_repair = (missing_checks and not original_issues) or (mode != 'review' and not review['passed'] and review['prompt'].strip())
            if can_repair:
                with self.lock:
                    current = self.get(ident, 'jobs')
                    if current['steps'][index].get('audit', {}).get('run_id') != run_id:
                        return
                    current['steps'][index]['audit'].update(message='Luna正在自动修复并重新自查（最多一次）', repair_attempts=1)
                    self.save(current, 'jobs')
                retry_request = {**request, 'prompt': review['prompt']}
                repair_phase = 'review' if missing_checks and not original_issues else 'draft'
                repaired = codex_bridge.run(exe, folder / 'repair-1', repair_phase, retry_request, review, rules)
                if repair_phase == 'review' and repaired['prompt'] != review['prompt']:
                    repaired.update(prompt=review['prompt'], passed=False,
                                    issues=repaired.get('issues', []) + ['补充检查时改写了原文，未采用改写或批准生成。'])
                review = asset_skill.validate_review(repaired, request.get('mode', 'asset-library'), request.get('kind'))
            if mode == 'review' and review['prompt'] != request['prompt']:
                review['suggested_prompt'] = review['prompt']
                review['prompt'] = request['prompt']
                review['passed'] = False
                review['issues'] = list(review.get('issues', [])) + ['审查返回了改写建议，原文已保留；请自行决定是否采用。']
            with self.lock:
                job = self.get(ident, 'jobs')
                step = job['steps'][index]
                if step.get('audit', {}).get('run_id') != run_id:
                    return
                if job.get('project_id'):
                    self.projects.context(job, index)
                if step.get('prompt', request['prompt']) != request['prompt']:
                    raise ValueError('正文已变化，本次审查结果不再适用；已保留当前正文')
                if mode != 'review' and review['prompt'].strip():
                    if step.get('prompt') and step['prompt'] != review['prompt']:
                        step.setdefault('prompt_history', []).append({'prompt': step['prompt'], 'at': now(), 'reason': '准备或修复提示词'})
                    step['prompt'] = review['prompt']
                started_at = step.get('audit', {}).get('started_at')
                step['audit'] = {**review, 'status': 'passed' if review['passed'] else 'needs_changes',
                                 'skill_execution': evidence,
                                 'run_id': run_id, 'started_at': started_at, 'finished_at': now(),
                                 'repair_attempts': int(bool(can_repair)),
                                 'message': '提示词已准备好' if review['passed'] else '自动处理后仍需解决：' + '；'.join(review['issues'])}
                self.save(job, 'jobs')
        except Exception as exc:
            with self.lock:
                job = self.get(ident, 'jobs')
                step = job['steps'][index]
                if step.get('audit', {}).get('run_id') == run_id:
                    step['audit'].update(status='error', message=str(exc)[:300], finished_at=now())
                    self.save(job, 'jobs')

    def prompt_is_approved(self, job, index):
        step = job['steps'][index]
        return (asset_skill.approved(step) and
                (not job.get('project_id') or step.get('audit', {}).get('skill_execution', {}).get('mode') == 'project') and
                step.get('audit', {}).get('prompt') == step.get('prompt') and
                step.get('prompt_approval', {}).get('prompt') == step.get('prompt'))

    def prepare(self, ident, index, *, project_id=_UNSCOPED_PROJECT):
        with self.lock:
            job = self.operation_job(ident, project_id)
            request = self.step_request(job, index)
            folder = self.root / 'requests' / ident / str(index)
            path = folder / 'request.json'
            dump(path, request)
            text = (f"请读取 {path}，使用其中指定的 {'ImageGen' if job['provider'] == 'imagegen' else '即梦 CLI'}，"
                    '先查看所有 reference_paths 图片，再按 prompt 制作这一张候选。保留独立原图，不覆盖已确认资产。'
                    '这是跨项目通用资产准备，不创建短剧 AST、不推进生产阶段。'
                    '生成完成后将输出的绝对路径导入资产库此视图，等待我确认。'
                    f' 入库使用 {Path(__file__).resolve()} 的 attach-result 子命令，'
                    f'全局 --library 参数为 {self.root}，--job {ident}，--view {index}，--file 为生成结果的绝对路径。'
                    '使用已包含 Pillow 的 Codex 内置 Python 运行；不自行升级候选确认状态。')
            (folder / '交给Codex.txt').write_text(text, encoding='utf-8')
            if job.get('project_id'):
                text = f'执行 asset-image-assistant 项目模式，读取 {path}，仅处理 AST {job["ast_id"]} 的此制作项，不推进其他阶段。'
                (folder / '交给Codex.txt').write_text(text, encoding='utf-8')
            approved = self.prompt_is_approved(job, index)
            audit = dict(job['steps'][index].get('audit', {'status': 'idle'}))
            if audit.get('status') == 'passed' and not asset_skill.approved(job['steps'][index]):
                audit.update(status='stale', message='这是旧流程的审查记录，请按资产Skill重新审查。')
            return {**request, 'path': str(path), 'handoff': text if approved else '',
                    'audit': audit, 'approved': approved}

    def attach(self, ident, index, paths, *, project_id=_UNSCOPED_PROJECT, manual=False):
        with self.lock:
            job = self.operation_job(ident, project_id)
            if not isinstance(index, int) or not 0 <= index < len(job['steps']):
                raise ValueError('无效视图序号')
            if job.get('project_id'):
                _, _, current = self.projects.context(job, index)
                self.projects.dependencies(job, current)
                if current.get('required') is False:
                    raise ValueError('此制作项当前不需要，请先核对制作路径')
            if manual and (job['steps'][index]['status'] in {'generating', 'submitting', 'submitted', 'querying'}
                           or job['steps'][index].get('audit', {}).get('status') in {'drafting', 'reviewing'}):
                raise ValueError('制作项正在处理，不能手动导入覆盖；请先核对原任务结果')
            if job['steps'][index]['status'] == 'confirmed':
                raise ValueError('已确认视图保留原版本；请新建一组资产进行修改')
            if not job.get('project_id'):
                self.step_request(job, index)
            step = job['steps'][index]
            kind = step['kind'] if job.get('project_id') else job['kind']
            ratio = step['ratio'] if job.get('project_id') else asset_skill.recipe(kind)['ratio']
            for path in paths:
                resolved = Path(path).resolve()
                if any(resolved.is_relative_to(Path(p['path']).resolve()) for p in self.projects.summaries()):
                    raise ValueError('任务目录中的图片不回流到网页资产库；请在任务内使用该图')
                provenance = {'job_id': ident, 'view': step['name'], 'inferred_hidden_surfaces': True,
                              'project_id': job.get('project_id'), 'ast_id': job.get('ast_id'), 'output_id': step.get('key'),
                              'source_narrative': job.get('source_narrative'), 'state': step.get('state'), 'prompt': step.get('prompt'),
                              'audit': step.get('audit'), 'authorization': step.get('authorization'),
                              'provider': job['provider'], 'model': job['model'] if job['provider'] == 'dreamina' else 'Codex 内置 ImageGen'}
                if manual:
                    provenance['kind'] = 'manual_result_import'
                    for key in ('provider', 'model', 'authorization'):
                        provenance.pop(key, None)  # A pasted file path cannot prove a native generation run.
                adjustment = None
                if job.get('upload_source_id'):
                    provenance.update(upload_source_id=job['upload_source_id'], generated_original=str(resolved))
                    category = asset_skill.recipe(kind)['category']
                    fitted = self.root / 'requests' / ident / str(index) / 'normalized' / (uid() + '.png')
                    adjustment = visual_asset_upload.crop_to_category(resolved, fitted, category,
                        {'focus_x': .5, 'focus_y': .5, 'critical_box': [0, 0, 1, 1]})
                    if adjustment['status'] == 'skipped':
                        raise ValueError('生成结果无法安全调整尺寸，未入库')
                    if adjustment['status'] != 'already_correct':
                        resolved = fitted
                    visual_asset_upload.validate_dimensions(resolved, category)
                    provenance['dimension_adjustment'] = adjustment
                item, _ = self.import_file(resolved, project=job.get('project_name', '照片转资产'), role='候选', category=asset_skill.recipe(kind)['category'],
                                          provenance=provenance)
                item['group'] = job['title']
                item['name'] = job['title'] + '_' + step['name'] + '_' + item['id'][:6]
                item['backup'] = {'status': 'pending'}
                item['technical_check'] = asset_skill.technical_check(item['original'], ratio)
                if adjustment:
                    item['upload_crop'] = adjustment
                self.save(item)
                if kind == '人物1x4':
                    identity = (f'project:{job["project_id"]}:{job["ast_id"]}' if job.get('project_id')
                                else f'face:{job["secondary_id"]}' if job.get('secondary_id') else '')
                    if identity:
                        self.auto_group_character(item['id'], identity,
                                                  job['title'] if job.get('project_id') else self.get(job['secondary_id'])['name'])
                if item['id'] not in job['steps'][index]['outputs']:
                    job['steps'][index]['outputs'].append(item['id'])
            if not job['steps'][index]['outputs']:
                raise ValueError('没有可导入的图片')
            job['steps'][index]['status'] = 'awaiting_review'
            self.save(job, 'jobs')
            self.backup_wakeup.set()
            return job

    def submit(self, p):
        with self.lock:
            job = self.operation_job(p['id'], p.get('project_id', _UNSCOPED_PROJECT))
            if job.get('project_id'):
                from core.candidate_test import require_production
                require_production(Path(self.projects.get(job['project_id'])['path']))
            index = int(p['index'])
            step = job['steps'][index]
            if job['provider'] != 'dreamina' or p.get('authorize') is not True:
                raise ValueError('需要在界面确认此次即梦上传、模型和额度消耗')
            if step['status'] != 'waiting':
                raise ValueError('本视图已提交或已有产物；不会重复扣费。返工请新建一组。')
            if not self.prompt_is_approved(job, index):
                raise ValueError('先完成Codex审查并确认当前提示词，再生成图片')
            exe = shutil.which('dreamina')
            if not exe:
                raise ValueError('未找到即梦 CLI')
            request = self.prepare(job['id'], index)
            run_id = uid()
            # Each paid attempt owns its logs and downloads; a redo never reads old results.
            for key in ('submit_id', 'query_failures', 'query_blocked', 'query_retry_after'):
                step.pop(key, None)
            step.update(status='submitting', generation_run_id=run_id,
                        authorization={'at': now(), 'request': request})
            self.save(job, 'jobs')
            threading.Thread(target=self._submit_worker, args=(exe, job['id'], index, request, run_id), daemon=True).start()
            return {'message': '已开始提交一张图片，请在生成区查看状态'}

    def _dreamina_folder(self, ident, index, run_id=None, submit_id=None):
        folder = self.root / 'requests' / ident / str(index)
        if run_id:
            return folder / 'dreamina' / run_id
        if submit_id:
            if not re.fullmatch(r'[A-Za-z0-9_-]+', submit_id):
                raise ValueError('旧即梦任务编号无效，请核对原提交记录')
            # Query the original task again; shared legacy downloads cannot prove task ownership.
            return folder / 'dreamina' / 'legacy' / submit_id
        return folder

    def _submit_worker(self, exe, ident, index, request, run_id=None):
        folder = self._dreamina_folder(ident, index, run_id)
        command = [exe, 'image2image' if request['reference_paths'] else 'text2image', '--prompt', request['prompt'], '--resolution_type', '2k',
                   '--ratio', request['ratio'], '--model_version', request['model'], '--generate_num', '1', '--poll', '0']
        for path in request['reference_paths']:
            command += ['--images', path]
        try:
            dump(folder / 'request.json', request)
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                    timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            (folder / 'submit.log').write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
            match = re.search(r'"submit_id"\s*:\s*"([A-Za-z0-9_-]+)"', result.stdout)
            with self.lock:
                job = self.get(ident, 'jobs')
                if job['steps'][index].get('generation_run_id') != run_id:
                    return
                job['steps'][index].update(status='submitted' if match else 'unknown',
                                          submit_id=match.group(1) if match else None,
                                          message='已提交，可点击查询结果' if match else '提交结果未确认；请检查提交日志，勿重复提交')
                self.save(job, 'jobs')
        except Exception as exc:
            with self.lock:
                job = self.get(ident, 'jobs')
                if job['steps'][index].get('generation_run_id') != run_id:
                    return
                job['steps'][index].update(status='unknown', message='提交结果未知：' + str(exc)[:200])
                self.save(job, 'jobs')

    def reject_result(self, p):
        with self.lock:
            job = self.operation_job(p['id'], p.get('project_id', _UNSCOPED_PROJECT))
            index = int(p['index'])
            self.step_request(job, index)
            step = job['steps'][index]
            redo = p.get('redo') is True
            if step['status'] not in {'awaiting_review', 'discarded'}:
                raise ValueError('只能处理尚未确认的生成结果；进行中或已确认的图片不能丢弃重做')
            if step['status'] == 'discarded' and not redo:
                raise ValueError('此结果已经移入垃圾桶')
            later = job['steps'][index + 1:]
            if job.get('project_id'):
                descendants = {step['key']}
                later = []
                for other in job['steps'][index + 1:]:
                    if any(key in descendants for key in other['dependencies']):
                        descendants.add(other['key'])
                        later.append(other)
            if any(s['status'] != 'waiting' or s.get('outputs') for s in later):
                raise ValueError('已有后续视图，请新建资产组，保留原有依赖')
            outputs = list(step['outputs'])
            items = [self.get(ident) for ident in outputs]
            if job.get('project_id') and any(a.get('provenance', {}).get('job_id') != job['id'] for a in items):
                raise ValueError('此项含复用资产，请点击重新选图；不要将其他资产当作本项生成结果删除')
            if any(item['status'] == 'confirmed' for item in items):
                raise ValueError('结果中有已确认资产，不能在此丢弃')
            active = [item['id'] for item in items if not item.get('trashed_at')]
            if active:
                management.manage(self, active, 'trash')
            if outputs:
                step.setdefault('result_history', []).append({'at': now(), 'outputs': outputs,
                    'prompt': step.get('prompt'), 'audit': step.get('audit'),
                    'generation_run_id': step.get('generation_run_id'), 'submit_id': step.get('submit_id')})
            step.update(outputs=[], status='waiting' if redo else 'discarded',
                        audit={'status': 'stale'},
                        message='旧图已移入垃圾桶；修改提示词并重新审查后再生成。' if redo else '不满意的结果已移入垃圾桶，30天内可恢复。')
            for key in ('prompt_approval', 'generation_run_id', 'submit_id', 'selected', 'authorization'):
                step.pop(key, None)
            self.save(job, 'jobs')
            return {'message': '已准备重做，尚未提交生图' if redo else '已移入垃圾桶，30天内可恢复'}

    def recover_imagegen(self, p):
        with self.lock:
            job = self.operation_job(p['id'], p.get('project_id', _UNSCOPED_PROJECT))
            index = int(p['index'])
            step = job['steps'][index]
            if job.get('project_id'):
                self.projects.context(job, index)
            if step['status'] != 'unknown' or job['provider'] != 'imagegen':
                raise ValueError('仅核对结果未知的ImageGen任务')
            run_id = step.get('generation_run_id')
            authorization = step.get('authorization', {})
            if not run_id or not authorization.get('at') or not authorization.get('request'):
                raise ValueError('缺少原生成记录，不能自动回填；没有重复生图。')
            folder = self.root / 'requests' / job['id'] / str(index) / 'imagegen' / run_id
            path = codex_bridge.generated_image_result(folder, authorization['request'],
                      datetime.fromisoformat(authorization['at']).timestamp())
            self.attach(job['id'], index, [path])
            job = self.get(job['id'], 'jobs')
            job['steps'][index]['message'] = '已核对原任务并回填已有图片，没有重复生图；请确认图片。'
            self.save(job, 'jobs')
            return {'message': job['steps'][index]['message']}

    def reset_imagegen(self, p):
        with self.lock:
            job = self.operation_job(p['id'], p.get('project_id', _UNSCOPED_PROJECT))
            index = int(p['index'])
            self.step_request(job, index)
            step = job['steps'][index]
            if step['status'] != 'unknown' or step['outputs']:
                raise ValueError('只有无图片结果的异常任务可以重新准备')
            step.setdefault('generation_history', []).append({k: step.get(k) for k in
                ('generation_run_id', 'authorization', 'message')})
            step.update(status='waiting', message='已重新准备；请核对提示词并再次同意生成。')
            step.pop('prompt_approval', None)
            step.pop('generation_run_id', None)
            self.save(job, 'jobs')
            return {'message': '已重新准备，尚未提交生图'}

    def submit_imagegen(self, p):
        with self.lock:
            job = self.operation_job(p['id'], p.get('project_id', _UNSCOPED_PROJECT))
            if job.get('project_id'):
                from core.candidate_test import require_production
                require_production(Path(self.projects.get(job['project_id'])['path']))
            index = int(p['index'])
            self.step_request(job, index)
            step = job['steps'][index]
            if job['provider'] != 'imagegen' or p.get('authorize') is not True:
                raise ValueError('请在网页确认使用ImageGen生成本次1张图片')
            if step['status'] != 'waiting':
                raise ValueError('本视图已经提交或有结果，不会重复生成；请核对已有任务')
            if not self.prompt_is_approved(job, index):
                raise ValueError('先完成Codex审查并确认当前提示词，再生成图片')
            exe = codex_bridge.executable()
            if not exe:
                raise ValueError('未找到本机Codex CLI')
            request = self.prepare(job['id'], index)
            run_id = uid()
            step.update(status='generating', generation_run_id=run_id,
                        message='ImageGen正在生成，完成后自动显示图片，无需复制或导入。',
                        authorization={'at': now(), 'request': request})
            self.save(job, 'jobs')
            threading.Thread(target=self._imagegen_worker, args=(exe, job['id'], index, run_id, request), daemon=True).start()
            return {'message': 'ImageGen已开始生成1张图片，结果将自动回填当前资产组'}

    def _imagegen_worker(self, exe, ident, index, run_id, request):
        folder = self.root / 'requests' / ident / str(index) / 'imagegen' / run_id
        try:
            path = codex_bridge.generate_image(exe, folder, request)
            with self.lock:
                job = self.get(ident, 'jobs')
                if job['steps'][index].get('generation_run_id') != run_id:
                    return
                self.attach(ident, index, [path])
                job = self.get(ident, 'jobs')
                job['steps'][index]['message'] = 'ImageGen生成完成，图片已导入；点击图片核对并确认。'
                self.save(job, 'jobs')
        except Exception as exc:
            with self.lock:
                job = self.get(ident, 'jobs')
                step = job['steps'][index]
                if step.get('generation_run_id') == run_id:
                    step.update(status='unknown', message=str(exc)[:350])
                    self.save(job, 'jobs')

    def query(self, ident, index, *, automatic=False, project_id=_UNSCOPED_PROJECT):
        with self.lock:
            job = self.operation_job(ident, project_id)
            step = job['steps'][index]
            if step['status'] != 'submitted' or not step.get('submit_id'):
                raise ValueError('没有可查询的已提交任务')
            if automatic and step.get('query_blocked'):
                raise ValueError(step.get('message', '自动查询已暂停，请核实原任务'))
            self._dreamina_folder(ident, index, step.get('generation_run_id'), step['submit_id'])
            if not automatic:
                step.pop('query_blocked', None)
                step.pop('query_failures', None)
            step['status'] = 'querying'
            self.save(job, 'jobs')
        threading.Thread(target=self._query_worker, args=(ident, index), daemon=True).start()
        return {'message': '正在查询并下载已完成图片'}

    def _query_worker(self, ident, index):
        job = self.get(ident, 'jobs')
        run_id, submit_id = job['steps'][index].get('generation_run_id'), job['steps'][index]['submit_id']
        folder = self._dreamina_folder(ident, index, run_id, submit_id) / 'results'
        folder.mkdir(parents=True, exist_ok=True)
        blocked, failed = False, False
        try:
            result = subprocess.run([shutil.which('dreamina'), 'query_result', '--submit_id', submit_id,
                                     '--download_dir', str(folder)], capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            (folder.parent / 'query.log').write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
            paths = [str(p) for p in folder.rglob('*') if p.suffix.lower() in IMAGE_EXTS and p.is_file()]
            if paths:
                with self.lock:
                    current = self.get(ident, 'jobs')['steps'][index]
                    if (current.get('generation_run_id'), current.get('submit_id')) != (run_id, submit_id):
                        return
                    self.attach(ident, index, paths)
                return
            if result.returncode != 0:
                blocked, failed = True, True
                message = f'查询命令失败（退出码 {result.returncode}），自动查询已暂停；请核实 query.log 后查询原任务，未重新提交'
            else:
                message = '暂未取得图片，稍后查询原任务；未重新提交'
        except (subprocess.TimeoutExpired, ConnectionError) as exc:
            failed = True
            message = '查询暂时未完成：' + str(exc)[:180]
        except Exception as exc:
            blocked, failed = True, True
            message = '自动查询已暂停，请核实原任务：' + str(exc)[:180]
        with self.lock:
            job = self.get(ident, 'jobs')
            step = job['steps'][index]
            if (step.get('generation_run_id'), step.get('submit_id')) != (run_id, submit_id):
                return
            failures = step.get('query_failures', 0) + 1 if failed else 0
            blocked = blocked or failures >= 3
            if blocked and not message.startswith(('自动查询已暂停', '查询命令失败')):
                message += '；连续查询失败，自动查询已暂停，可手动查询原任务'
            step.update(status='submitted', message=message, query_failures=failures,
                        query_blocked=blocked, query_retry_after=min(8 * 2 ** min(failures, 3), 60))
            self.save(job, 'jobs')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def safe_host(self):
        expected = f'127.0.0.1:{self.server.server_port}'
        return self.headers.get('Host') == expected and self.headers.get('Origin', 'http://' + expected) == 'http://' + expected

    def reply(self, data, status=200, content_type='application/json; charset=utf-8'):
        if status >= 400:
            print(json.dumps({'time': now(), 'method': self.command, 'path': urlparse(self.path).path,
                              'status': status, 'error': data.get('error') if isinstance(data, dict) else ''}, ensure_ascii=False), file=sys.stderr, flush=True)
        body = data if isinstance(data, bytes) else json.dumps(web_view(data), ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; img-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
                         f"script-src 'self' 'sha256-{PREVIS_IMPORTMAP_SHA256}'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def reply_video(self, path):
        size = path.stat().st_size
        start, end = 0, size - 1
        requested = self.headers.get('Range', '')
        if requested:
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', requested)
            if not match:
                return self.reply({'error': '视频范围无效'}, 416)
            start = int(match[1])
            end = min(int(match[2]), size - 1) if match[2] else size - 1
            if start > end or start >= size:
                return self.reply({'error': '视频范围越界'}, 416)
        self.send_response(206 if requested else 200)
        self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        if requested:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        with path.open('rb') as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def do_GET(self):
        if not self.safe_host():
            return self.reply({'error': '仅允许本机同源访问'}, 403)
        url = urlparse(self.path)
        try:
            if url.path == '/api/state':
                assets = self.server.library.items()
                return self.reply({'assets': [a for a in assets if not a['reference_only']],
                                   'references': [a for a in assets if a['reference_only']],
                                   'jobs': self.server.library.items('jobs'),
                                   'product_categories': self.server.library.product_categories(),
                                   'projects': self.server.library.projects.summaries(),
                                   'skill_profile': asset_skill.profile(),
                                   'management_ready': True,
                                   'cleanup_error': self.server.library.cleanup_error,
                                   'token': self.server.token, 'root': str(self.server.library.root),
                                   'vault': str(self.server.library.vault / '资产库'),
                                   'dreamina_available': bool(shutil.which('dreamina'))})
            if url.path == '/api/previs':
                query = parse_qs(url.query)
                project_id = query.get('project_id', [''])[0]
                preview_id = query.get('preview_id', [''])[0]
                result = (visual_asset_previs.detail(self.server.library, project_id, preview_id)
                          if preview_id else visual_asset_previs.list_previews(self.server.library, project_id))
                return self.reply(result)
            if url.path.startswith('/previs-media/'):
                parts = url.path.split('/')
                if len(parts) != 5 or parts[-1] != 'preview.glb':
                    raise ValueError('无效白模预览路径')
                query = parse_qs(url.query, keep_blank_values=True)
                data = visual_asset_previs.model_bytes(
                    self.server.library, unquote(parts[2]), unquote(parts[3]),
                    query.get('manifest_sha256', [None])[0])
                return self.reply(data, content_type='model/gltf-binary')
            if url.path.startswith('/media/'):
                _, _, ident, variant = url.path.split('/')
                if variant not in {'thumbnail', 'original'}:
                    raise ValueError('无效图片类型')
                path = Path(self.server.library.get(ident)[variant]).resolve()
                allowed = ((self.server.library.root / 'assets').resolve(),
                           (self.server.library.vault / '资产库').resolve())
                asset = self.server.library.get(ident)
                if asset.get('record_scope') == 'project_reference':
                    project = self.server.library.projects.get(asset['provenance']['project_id'])
                    allowed += (Path(project['path']).resolve() / '04_资产' / '项目参考',)
                if not any(path.is_relative_to(root) for root in allowed):
                    raise ValueError('图片路径越界')
                if variant == 'original' and path.suffix.lower() in VIDEO_EXTS:
                    return self.reply_video(path)
                return self.reply(path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            static = {'/': 'index.html', '/app.js': 'app.js', '/cyber.css': 'cyber.css', '/robot.js': 'robot.js', '/scenes.js': 'scenes.js', '/characters.js': 'characters.js', '/folders.js': 'folders.js', '/library-extensions.js': 'library-extensions.js', '/projects.js': 'projects.js', '/projects.css': 'projects.css', '/previs.js': 'previs.js', '/previs.css': 'previs.css'}
            for name in ('three.module.js', 'three.core.js', 'addons/loaders/GLTFLoader.js',
                         'addons/controls/OrbitControls.js', 'addons/utils/BufferGeometryUtils.js', 'LICENSE'):
                static['/vendor/three/' + name] = 'vendor/three/' + name
            if url.path in static:
                path = ROOT / 'scripts' / 'visual_asset_ui' / static[url.path]
                content_type = ('text/html' if url.path == '/' else 'text/css' if url.path.endswith('.css')
                                else 'text/plain' if url.path.endswith('/LICENSE') else 'text/javascript')
                return self.reply(path.read_bytes(), content_type=content_type + '; charset=utf-8')
            return self.reply({'error': '未找到'}, 404)
        except (ValueError, KeyError, OSError) as exc:
            return self.reply({'error': str(exc)}, 400)

    def do_POST(self):
        if not self.safe_host():
            return self.reply({'error': '请从本机资产库页面操作'}, 403)
        if self.headers.get('X-Library-Token') != self.server.token:
            return self.reply({'error': '页面连接已过期，请刷新后重试', 'code': 'session_expired'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 64 * 1024 * 1024:
                raise ValueError('请求为空或超过 64 MB')
            url = urlparse(self.path)
            raw = self.rfile.read(size)
            library = self.server.library
            if url.path == '/api/upload':
                raise ValueError('请刷新网页并使用“上传文件”入口')
            p = json.loads(raw)
            actions = {
                '/api/upload-local': lambda: library.pick_upload(p),
                '/api/upload-files': lambda: library.pick_files(p),
                '/api/upload-prepare': lambda: library.prepare_upload(p),
                '/api/video-local': lambda: library.pick_video(p),
                '/api/project': lambda: library.projects.action(p),
                '/api/product-references': lambda: __import__('visual_asset_product_usage').import_references(library, p),
                '/api/folder': lambda: library.folder(p),
                '/api/product-category': lambda: library.product_category(p),
                '/api/product-assign': lambda: library.assign_product_category(p),
                '/api/backup-retry': lambda: visual_asset_backup.retry(library, management),
                '/api/import': lambda: library.import_tree(p['source'], external_generation=p.get('external_generation')),
                '/api/update': lambda: library.update(p),
                '/api/scene': lambda: library.group_scene(p),
                '/api/move': lambda: management.manage(library, p['ids'], 'move', p.get('category')),
                '/api/trash': lambda: management.manage(library, p['ids'], 'trash'),
                '/api/restore': lambda: management.manage(library, p['ids'], 'restore'),
                '/api/purge': lambda: management.manage(library, p['ids'], 'purge', manual=True),
                '/api/dedupe': lambda: management.dedupe(library),
                '/api/confirm': lambda: library.confirm(p['id']),
                '/api/export': lambda: library.export(p['ids']),
                '/api/job': lambda: library.create_job(p),
                '/api/job-delete': lambda: library.delete_job(p['id']),
                '/api/prepare': lambda: library.prepare(p['id'], int(p['index']), project_id=p.get('project_id', _UNSCOPED_PROJECT)),
                '/api/prompt': lambda: library.save_prompt(p),
                '/api/codex': lambda: library.audit_prompt(p),
                '/api/attach': lambda: library.attach(p['id'], int(p['index']), p['paths'], project_id=p.get('project_id', _UNSCOPED_PROJECT), manual=True),
                '/api/submit': lambda: library.submit(p),
                '/api/imagegen': lambda: library.submit_imagegen(p),
                '/api/imagegen-reset': lambda: library.reset_imagegen(p),
                '/api/imagegen-recover': lambda: library.recover_imagegen(p),
                '/api/reject-result': lambda: library.reject_result(p),
                '/api/query': lambda: library.query(p['id'], int(p['index']), project_id=p.get('project_id', _UNSCOPED_PROJECT)),
            }
            if url.path not in actions:
                return self.reply({'error': '未找到操作'}, 404)
            return self.reply(actions[url.path]())
        except (ValueError, KeyError, TypeError, IndexError, OSError, sqlite3.Error) as exc:
            return self.reply({'error': str(exc)}, 400)


def serve(library, port, open_browser=False):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.library, server.token = library, secrets.token_urlsafe(32)
    def collect_trash():
        while True:
            try:
                management.cleanup(library)
                library.cleanup_error = ''
            except Exception as exc:
                library.cleanup_error = str(exc)
            threading.Event().wait(3600)
    threading.Thread(target=collect_trash, daemon=True).start()
    library.projects.recover()
    # Backfill successful generated images from earlier versions, preserving deletion records.
    for item in library.items():
        if not item.get('trashed_at') and not item.get('backup'):
            item['backup'] = {'status': 'pending'}
            library.save(item)
    def backup_images():
        while True:
            try:
                visual_asset_backup.run(library, management)
            except Exception as exc:
                print('资产备份等待处理：' + str(exc), file=sys.stderr, flush=True)
            library.backup_wakeup.wait(30)
            library.backup_wakeup.clear()
    threading.Thread(target=backup_images, daemon=True).start()
    # A crashed submission is uncertain, never auto-resubmit an operation that may have consumed credits.
    for job in library.items('jobs'):
        changed = False
        for step in job['steps']:
            if step.get('audit', {}).get('status') in {'drafting', 'reviewing'}:
                step['audit'].update(status='error', message='服务重启中断了Codex处理，请重新发起；未自动重试。')
                changed = True
            if step['status'] in {'submitting', 'querying', 'generating'}:
                step['status'] = 'submitted' if step.get('submit_id') else 'unknown'
                changed = True
        if changed:
            library.save(job, 'jobs')
    url = f'http://127.0.0.1:{server.server_port}'
    print(url, flush=True)
    if open_browser:
        webbrowser.open(url)
    server.serve_forever()


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=str(DEFAULT_LIBRARY))
    subs = parser.add_subparsers(dest='command', required=True)
    run = subs.add_parser('serve')
    run.add_argument('--port', type=int, default=8766)
    run.add_argument('--open', action='store_true')
    imp = subs.add_parser('import')
    imp.add_argument('--source', required=True)
    attach_parser = subs.add_parser('attach-result')
    attach_parser.add_argument('--job', required=True)
    attach_parser.add_argument('--view', type=int, required=True)
    attach_parser.add_argument('--file', action='append', required=True)
    subs.add_parser('list')
    args = parser.parse_args()
    lib = Library(args.library)
    if args.command == 'serve':
        serve(lib, args.port, args.open)
    elif args.command == 'attach-result':
        print(json.dumps(lib.attach(args.job, args.view, args.file), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(lib.import_tree(args.source) if args.command == 'import' else lib.items(), ensure_ascii=False, indent=2))
