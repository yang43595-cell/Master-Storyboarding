"""Commit staged originals and catalog changes to the Vault asset folder."""
import json
import hashlib
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

PREFIX = {'人物单图': 'PERSON', '大头照': 'HEAD', '服装三视图': 'WARD', '人物1x4': 'CHAR', '角色外观': 'BODY', '宠物': 'PET', '场景': 'SCENE',
          '物品': 'PROP', '产品': 'PROD', '风格': 'STYLE', '视频参考': 'VREF', '其他': 'OTHER'}


def same_file(first, second):
    if first.stat().st_size != second.stat().st_size:
        return False
    def digest(path):
        value = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                value.update(block)
        return value.digest()
    return digest(first) == digest(second)


def run(library, management):
    with library.lock:
        items = [a for a in library.items() if not a.get('reference_only') and a.get('backup', {}).get('status') == 'pending']
        if not items:
            return {'status': 'saved', 'count': 0, 'message': '没有待备份图片'}
        missing = [a for a in items if not Path(a['original']).is_file()]
        for asset in missing:
            asset['backup'] = {'status': 'pending', 'message': '原图缺失，请恢复原文件：' + asset['original']}
            library.save(asset)
        items = [a for a in items if Path(a['original']).is_file()]
        if not items:
            return {'status': 'blocked', 'count': 0, 'message': '待备份原图缺失，请恢复原文件',
                    'pending': [{'id': a['id'], 'name': a['name']} for a in missing]}
        if not library.vault:
            return {'status': 'blocked', 'count': 0, 'message': '未配置知识库，图片仍保存在本地，尚未备份'}
        vault = library.vault
        root = (vault / '资产库').resolve()
        locked = False
        try:
            management.guard(vault, 'begin')
            locked = True
            root.mkdir(exist_ok=True)
            index_path = root / '资产索引.json'
            index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'schema_version': '1.0', 'assets': [], 'removed_assets': []}
            index['product_categories'] = library.product_categories()
            rows = {r['library_asset_id']: r for r in index['assets']}
            removed = {r['library_asset_id'] for r in index.get('removed_assets', [])}
            numbers = [int(m[1]) for r in index['assets'] + index.get('removed_assets', [])
                       if (m := re.search(r'-(\d+)$', r['asset_id']))]
            serial = max(numbers or [0])
            saved = []
            for asset in items:
                if asset['id'] in removed:
                    raise ValueError('该图片已有永久删除记录，不自动重新入库')
                source = Path(asset['original'])
                row = rows.get(asset['id'])
                if not row:
                    serial += 1
                    asset_id = f'{PREFIX[asset["category"]]}-{serial:06d}'
                    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', asset['name']).strip(' .')[:28] or '资产'
                    view = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', asset.get('provenance', {}).get('view',
                                   '参考片段' if asset.get('media_type') == 'video' else '单图'))[:16]
                    folder = root / ('垃圾桶' if asset.get('trashed_at') else asset['category']) / (asset_id + '_' + name)
                    target = folder / f'{asset_id}_{name}_{view}_v001{source.suffix.lower()}'
                    row = {'asset_id': asset_id, 'library_asset_id': asset['id'], 'destination': target.relative_to(root).as_posix(),
                           'original_source': asset['source'], 'library_version': 'v001', 'view': view}
                    index['assets'].append(row)
                target = management.inside(root, root / row['destination'])
                if not source.is_file():
                    raise ValueError('图片原文件丢失：' + asset['name'])
                if target.exists() and not same_file(target, source):
                    raise ValueError('知识库已有不同图片，拒绝覆盖：' + str(target))
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copy2(source, target)
                if not same_file(target, source):
                    raise ValueError('知识库复制校验失败')
                row.update(name=asset['name'], category=asset['category'], role=asset['role'],
                           original_source=asset['source'],
                           state='已确认' if asset['status'] == 'confirmed' else '待核对',
                           source_project=asset['project'], copy_source=str(source), bytes=source.stat().st_size,
                           width=asset['width'], height=asset['height'], confirmation=asset.get('confirmation'),
                           folder_id=asset.get('folder_id'), folder_name=asset.get('folder_name'), folder_cover=asset.get('folder_cover'),
                           folder_order=asset.get('folder_order'), product_category_id=asset.get('product_category_id'),
                           product_category_name=next((c['name'] for c in index['product_categories'] if c['id'] == asset.get('product_category_id')), None),
                           media_type=asset.get('media_type', 'image'), duration=asset.get('duration'),
                           provenance=asset.get('provenance'), project_confirmations=asset.get('project_confirmations', []))
                # The Vault index is the durable catalog; SQLite is only the Web working cache.
                row['record'] = {**asset, 'original': str(target),
                                 'backup': {'status': 'saved', 'path': str(target)}}
                for key in ('trashed_at', 'expires_at', 'restore_category'):
                    if key in asset:
                        row[key] = asset[key]
                note = target.parent / '资产说明.md'
                note.write_text('# ' + row['asset_id'] + '｜' + asset['name'] + '\n\n- 分类：' + asset['category'] +
                    '\n- 状态：' + row['state'] + '\n- 文件：[[资产库/' + row['destination'] + ']]\n\n## 来源与生成记录\n\n```json\n' +
                    json.dumps({k: row.get(k) for k in ('source_project', 'original_source', 'provenance', 'confirmation', 'project_confirmations')}, ensure_ascii=False, indent=2) + '\n```\n', encoding='utf-8')
                saved.append((asset, target))
            management.write_index(root, index)
            management.guard(vault, 'finish')
            locked = False
            for asset, target in saved:
                previous = Path(asset['original']).resolve()
                asset['backup'] = {'status': 'saved', 'path': str(target), 'at': datetime.now(timezone.utc).isoformat()}
                asset['original'] = str(target)
                library.save(asset)
                if previous != target and previous.is_relative_to((library.root / 'assets').resolve()) and previous.is_file():
                    if not same_file(previous, target):
                        raise ValueError('知识库原图与网页旧副本不一致，旧副本已保留')
                    previous.unlink()
            return {'status': 'blocked' if missing else 'saved', 'count': len(saved),
                    'message': f'已备份 {len(saved)} 张图片' + (f'；{len(missing)} 项原图缺失' if missing else ''),
                    'pending': [{'id': a['id'], 'name': a['name']} for a in missing]}
        except Exception as exc:
            for asset in items:
                current = library.get(asset['id'])
                current['backup'] = {'status': 'pending', 'message': str(exc)[:700]}
                library.save(current)
            return {'status': 'blocked', 'count': 0, 'message': str(exc),
                    'pending': [{'id': a['id'], 'name': a['name']} for a in items]}
        finally:
            if locked:
                management.guard(vault, 'abort')


def retry(library, management):
    result = run(library, management)
    if result['status'] == 'blocked':
        raise ValueError(result['message'])
    return result
