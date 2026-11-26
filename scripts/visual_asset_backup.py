"""Commit staged originals and catalog changes to the Vault asset folder."""
from hashlib import sha256
import errno
import os
import tempfile
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .core.asset_batch_receipt import require_candidate_revision
    from .core.artifacts import cache_file_digests
else:
    from core.asset_batch_receipt import require_candidate_revision
    from core.artifacts import cache_file_digests


PREFIX = {'人物单图': 'PERSON', '大头照': 'HEAD', '服装三视图': 'WARD', '人物1x4': 'CHAR', '角色外观': 'BODY', '宠物': 'PET', '场景': 'SCENE',
          '物品': 'PROP', '产品': 'PROD', '风格': 'STYLE', '视频参考': 'VREF', '其他': 'OTHER'}


def same_file(first, second):
    if first.samefile(second):
        return True
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open('rb') as left, second.open('rb') as right:
        for block in iter(lambda: left.read(1024 * 1024), b''):
            if block != right.read(1024 * 1024):
                return False
        return not right.read(1)


def catalog_revision(row, asset):
    revision = row.get('library_version')
    require_candidate_revision(asset.get('candidate_revision'), revision)
    return revision


def run(library, management, *, asset_ids=None):
    with library.lock:
        assets = library.items() if asset_ids is None else (library.get(ident) for ident in dict.fromkeys(asset_ids))
        items = [a for a in assets if not a.get('reference_only') and a.get('backup', {}).get('status') == 'pending']
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
                asset['candidate_revision'] = catalog_revision(row, asset)
                if target.exists():
                    if not same_file(target, source):
                        raise ValueError('知识库已有不同图片，拒绝覆盖：' + str(target))
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
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
        copy_key = (source.resolve(), checksum)
        origin = self._copies.get(copy_key, source)
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
            self._copies[copy_key] = target
        else:
            self._remove_own_copy(target, identity)
        return True, matches
