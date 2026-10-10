"""File operations for the visual library and its associated Vault copies."""
import csv
import hashlib
import io
import json
import os
import shutil
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.io import write_bytes, write_json


def inside(root, path):
    path = Path(path).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('图片路径超出资产库，已停止操作')
    return path


_write_locks = {}


def guard(vault, action):
    """Serialize asset-file changes without involving the Vault Git worktree."""
    key = (threading.get_ident(), Path(vault).resolve())
    if action == 'begin':
        if key in _write_locks:
            raise ValueError('当前操作已持有资产库写入锁')
        path = key[1] / '资产库' / '.write.lock'
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open('a+b')
        handle.seek(0)
        if not handle.read(1):
            handle.seek(0)
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + 5
        try:
            while True:
                try:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ValueError('资产库正在由另一项操作更新，请稍后重试') from exc
                    time.sleep(0.1)
            _write_locks[key] = handle
        except Exception:
            handle.close()
            raise
        return
    if action not in {'finish', 'abort'}:
        raise ValueError('未知资产库写入操作')
    handle = _write_locks.pop(key, None)
    if handle is None:
        raise ValueError('当前操作没有资产库写入锁')
    try:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def write_index(root, index):
    index['counts'] = dict(Counter(row['category'] for row in index['assets'] if not row.get('trashed_at')))
    index['trash_count'] = sum(bool(row.get('trashed_at')) for row in index['assets'])
    write_json(root / '资产索引.json', index)
    fields = ['asset_id', 'category', 'name', 'state', 'source_project', 'destination', 'original_source', 'trashed_at', 'expires_at']
    with io.StringIO(newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(index['assets'])
        write_bytes(root / '资产索引.csv', handle.getvalue().encode('utf-8-sig'))


def manage(library, ids, action, category=None, at=None, manual=False):
    if action not in {'move', 'trash', 'restore', 'purge'}:
        raise ValueError('未知资产操作')
    if action == 'move' and category not in library.categories:
        raise ValueError('请选择目标分类')
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise ValueError('请先勾选图片')
    moment = at or datetime.now(timezone.utc)
    with library.lock:
        journal = library.root / 'management-pending.json'
        if journal.exists():
            raise ValueError('上次文件操作未完成，请先检查 management-pending.json，已保留现场')
        items = [library.get(ident) for ident in dict.fromkeys(ids)]
        vault = library.vault
        index = None
        rows = {}
        if vault:
            root = (vault / '资产库').resolve()
            index = json.loads((root / '资产索引.json').read_text(encoding='utf-8'))
            rows = {r['library_asset_id']: r for r in index['assets']}
        operations = []
        for item in items:
            if action == 'move' and ((item.get('media_type') == 'video') != (category == '视频参考')):
                raise ValueError('视频参考与图片分类不能互换')
            deleted = bool(item.get('trashed_at'))
            if deleted != (action in {'restore', 'purge'}):
                raise ValueError('选择中包含状态已变化的图片，请刷新后重试')
            if action == 'purge' and not manual and datetime.fromisoformat(item['expires_at']) > moment:
                raise ValueError('图片尚未满 30 天，不能自动删除')
            if action == 'purge' and (item.get('status') == 'confirmed' or item.get('project_confirmations') or
                any(item['id'] in step.get('outputs', []) for job in library.items('jobs') if job.get('project_id')
                    for step in job.get('steps', []))):
                raise ValueError('已确认或被项目引用的资产不能永久清理：' + item['name'])
            current = inside((library.root / 'assets').resolve(), Path(item['thumbnail']).parent)
            if not current.is_dir():
                raise ValueError('缩略图目录缺失：' + item['name'])
            if not Path(item['original']).is_file() or not Path(item['thumbnail']).is_file():
                raise ValueError('图片文件缺失：' + item['name'])
            target_category = category if action == 'move' else item.get('restore_category', item['category'])
            if target_category == '道具':
                target_category = '物品'
            target = inside((library.root / 'assets').resolve(), library.root / 'assets' /
                            ('垃圾桶' if action in {'trash', 'purge'} else target_category) / item['id'])
            moves = []
            if action != 'purge' and target != current:
                if target.exists():
                    raise ValueError('目标位置已有同名资产，拒绝覆盖')
                moves.append((current, target))
            row = rows.get(item['id'])
            if not row:
                raise ValueError('知识库索引没有这张图片，先修复入库记录：' + item['name'])
            old_vault = new_vault = None
            if row:
                old_vault = inside(root, root / row['destination'])
                if not old_vault.is_file():
                    raise ValueError('知识库图片已在外部删除，请先同步索引：' + item['name'])
                new_vault = inside(root, root / ('垃圾桶' if action in {'trash', 'purge'} else target_category) /
                                   old_vault.parent.name / old_vault.name)
                if action != 'purge' and new_vault.parent != old_vault.parent:
                    if new_vault.parent.exists():
                        raise ValueError('知识库目标位置已存在，拒绝覆盖')
                    moves.append((old_vault.parent, new_vault.parent))
            if action == 'purge':
                expected = {Path(item['thumbnail']).resolve()}
                if Path(item['original']).resolve().is_relative_to((library.root / 'assets').resolve()):
                    expected.add(Path(item['original']).resolve())
                if set(current.iterdir()) != expected:
                    raise ValueError('垃圾桶资产目录出现额外文件，暂停自动清理：' + item['name'])
                if row and set(old_vault.parent.iterdir()) - {old_vault, old_vault.parent / '资产说明.md'}:
                    raise ValueError('知识库垃圾桶目录出现额外文件，暂停自动清理')
            operations.append((item, row, current, target, old_vault, new_vault, target_category, moves))
        locked = False
        try:
            if any(op[1] for op in operations):
                guard(vault, 'begin')
                locked = True
                # Re-read under the asset-only lock; do not overwrite concurrent index changes.
                fresh = json.loads((root / '资产索引.json').read_text(encoding='utf-8'))
                if fresh != index:
                    raise ValueError('知识库索引刚刚发生变化，请重试')
            journal.write_text(json.dumps({'action': action, 'items': items,
                'moves': [[str(a), str(b)] for op in operations for a, b in op[-1]],
                'vault_index': index}, ensure_ascii=False, indent=2), encoding='utf-8')
            with library.connect() as db:
                for item, row, current, target, old_vault, new_vault, target_category, moves in operations:
                    for old, new in moves:
                        new.parent.mkdir(parents=True, exist_ok=True)
                        old.rename(new)
                        if old.exists() or not new.exists():
                            raise ValueError(f'移动后目录校验失败：{old} -> {new}')
                    if action == 'purge':
                        for file in current.iterdir():
                            file.unlink()
                        current.rmdir()
                        if row:
                            for file in old_vault.parent.iterdir():
                                file.unlink()
                            old_vault.parent.rmdir()
                            index['assets'].remove(row)
                            index.setdefault('removed_assets', []).append({**row, 'purged_at': moment.isoformat()})
                        db.execute('DELETE FROM assets WHERE id=?', (item['id'],))
                        continue
                    item['original'] = str(new_vault)
                    item['thumbnail'] = str(target / Path(item['thumbnail']).name)
                    if action == 'trash':
                        item.update(trashed_at=moment.isoformat(), expires_at=(moment + timedelta(days=30)).isoformat(),
                                    restore_category=item['category'])
                    else:
                        item['category'] = target_category
                        for key in ['trashed_at', 'expires_at', 'restore_category']:
                            item.pop(key, None)
                    if row:
                        previous = row['destination']
                        if item.get('backup', {}).get('status') == 'saved':
                            item['backup']['path'] = str(new_vault)
                        row['destination'] = new_vault.relative_to(root).as_posix()
                        row['category'] = item['category']
                        row['record'] = {**item, 'original': str(new_vault)}
                        for key in ['trashed_at', 'expires_at', 'restore_category']:
                            if key in item:
                                row[key] = item[key]
                            else:
                                row.pop(key, None)
                        note = new_vault.parent / '资产说明.md'
                        if note.exists():
                            body = note.read_text(encoding='utf-8').replace('资产库/' + previous, '资产库/' + row['destination'])
                            lines = [('- 分类：' + row['category']) if line.startswith('- 分类：') else line for line in body.splitlines()]
                            note.write_text('\n'.join(lines) + '\n', encoding='utf-8')
                    db.execute('UPDATE assets SET data=? WHERE id=?', (json.dumps(item, ensure_ascii=False), item['id']))
            if locked:
                write_index(root, index)
                guard(vault, 'finish')
                locked = False
            journal.unlink()
        finally:
            if locked:
                guard(vault, 'abort')
        return {'count': len(items), 'action': action}


def cleanup(library, at=None):
    moment = at or datetime.now(timezone.utc)
    used = {ident for job in library.items('jobs') if job.get('project_id')
            for step in job.get('steps', []) for ident in step.get('outputs', [])}
    expired = [i['id'] for i in library.items() if i.get('expires_at') and
               datetime.fromisoformat(i['expires_at']) <= moment and
               i.get('status') != 'confirmed' and not i.get('project_confirmations') and i['id'] not in used]
    return manage(library, expired, 'purge', at=moment) if expired else {'count': 0}


ROLE_SCORE = {'母版': 500, '参考': 400, '候选': 300, '分镜': 200, '历史': 100, '待整理': 0}


def duplicate_groups(library):
    groups = {}
    for item in library.items():
        if item.get('trashed_at'):
            continue
        path = Path(item['original'])
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        groups.setdefault(digest, []).append(item)
    return [group for group in groups.values() if len(group) > 1]


def _keep_score(item):
    # Exact bytes mean the visual content is identical. Keep the richest
    # catalog record, while preferring a meaningful category over Other.
    return (
        10000 if item.get('status') == 'confirmed' else 0,
        ROLE_SCORE.get(item.get('role'), 0),
        50 if item.get('category') not in {'其他', '道具'} else 0,
        20 if item.get('project') == 'assets' else 0,
        -len(item.get('name', '')),
        item.get('created_at', ''),
    )


def dedupe(library, at=None):
    # Keep reference checks and moves in the same operation lock. Identical bytes
    # do not make project confirmations or references interchangeable IDs.
    with library.lock:
        groups = duplicate_groups(library)
        protected = {item['id'] for item in library.items()
                     if item.get('status') == 'confirmed' or item.get('project_confirmations')}
        jobs = {job['id']: job for job in library.items('jobs')}
        for summary in library.projects.summaries():
            project = library.projects.get(summary['id'])
            for row in project['rows']:
                protected.update(row.get('reference_ids', []))
                for step in jobs.get(row.get('job_id'), {}).get('steps', []):
                    protected.update(step.get('outputs', []))
                    protected.update(step.get('reference_ids', []))
                    if step.get('selected'):
                        protected.add(step['selected'])
        keep, trash = [], []
        for group in groups:
            canonical = max([item for item in group if item['id'] in protected] or group, key=_keep_score)
            duplicates = [item['id'] for item in group
                          if item['id'] != canonical['id'] and item['id'] not in protected]
            keep.append({'id': canonical['id'], 'name': canonical['name'], 'duplicates': duplicates})
            trash.extend(duplicates)
        report = {'schema_version': '1.0', 'created_at': (at or datetime.now(timezone.utc)).isoformat(),
                  'duplicate_groups': len(groups), 'kept': keep, 'trashed_ids': trash}
        (library.root / 'duplicate-cleanup-plan.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        result = manage(library, trash, 'trash', at=at) if trash else {'count': 0, 'action': 'trash'}
        return {**result, 'duplicate_groups': len(groups),
                'kept_count': sum(len(group) for group in groups) - len(trash),
                'plan': str(library.root / 'duplicate-cleanup-plan.json')}
