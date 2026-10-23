"""Copy the current visual library into the authorized Vault asset directory.

Preserves sources, stores a stable source-to-destination naming manifest, and
requires an active visual-asset-library Vault write transaction for --apply.
"""
import argparse
import csv
import importlib.util
import json
import re
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

CATEGORIES = {'大头照': 'HEAD', '服装三视图': 'WARD', '人物1x4': 'CHAR',
              '物品': 'PROP', '产品': 'PROD', '风格': 'STYLE', '其他': 'OTHER'}
OWNER = 'visual-asset-library'


def category_for(item):
    name = item['name'].lower().replace('×', 'x')
    if item['category'] == '道具':
        return '物品'
    if item['category'] == '产品' and any(term in name for term in
            ['手镜', '对讲机', '沙漏', '皇家护理刷', '托盘', '湿毛巾', '皇家实验ab双管',
             '研究档案', '取水桶', '卫星电话', '恒温样本盒', '直升机']):
        return '物品'
    if '1x4' in name and (item['category'] in {'人物', '服装', '人物1x4'} or name.startswith('ch-')):
        return '人物1x4'
    if ('1x3' in name or '三视图' in name) and any(w in name for w in ['服装', '衣服', '睡袍', '礼服', '服饰']):
        return '服装三视图'
    if any(w in name for w in ['大头照', '大脸', '脸部', '面部', '头部特写']):
        return '大头照'
    return item['category'] if item['category'] in CATEGORIES else '其他'


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value)
    value = re.sub(r'^(?:CH|PR|PD|SC|ENV|STY|REF|VEH)-[A-Z0-9]+[_-]*', '', value, flags=re.I)
    value = re.sub(r'(?:[_-]?(?:候选|candidate))', '', value, flags=re.I)
    value = re.sub(r'[_-]?(?:20\d{6}|R\d{2,}|v\d{1,3})$', '', value, flags=re.I)
    return re.sub(r'[_\s]+', '_', value).strip(' ._')[:32] or '未命名图片'


def build_plan(library, vault):
    destination = vault / '资产库'
    with sqlite3.connect(library / 'library.sqlite3') as db:
        items = [json.loads(row[0]) for row in db.execute('SELECT data FROM assets')]
    for file in sorted((vault / 'raw' / 'assets').glob('*.DNG')):
        items.append({'id': 'raw:' + file.name, 'name': file.stem, 'source': str(file),
                      'original': str(file), 'category': '其他', 'role': '参考',
                      'project': '产品实拍原始文件', 'status': 'unreviewed', 'confirmation': None,
                      'bytes': file.stat().st_size, 'width': None, 'height': None})
    records = []
    previous_path = destination / '资产索引.json'
    previous_index = json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else {}
    previous = previous_index.get('assets', [])
    removed = previous_index.get('removed_assets', [])
    removed_ids = {row['library_asset_id'] for row in removed}
    previous_by_id = {row['library_asset_id']: row for row in previous}
    serial = max([int(row['asset_id'].rsplit('-', 1)[1]) for row in previous + removed] or [0])
    for item in sorted(items, key=lambda row: row['source'].casefold()):
        if item['id'] in removed_ids:
            continue
        if item['id'] in previous_by_id:
            records.append(previous_by_id[item['id']])
            continue
        source = Path(item['original']).resolve()
        if not source.is_file() or source.stat().st_size != item['bytes']:
            raise ValueError(f'来源文件不存在或大小变化：{source}')
        category = category_for(item)
        serial += 1
        asset_id = f'{CATEGORIES[category]}-{serial:06d}'
        label = safe_name(item['name'])
        view = {'大头照': '大头照', '服装三视图': '三视图', '人物1x4': '1x4'}.get(category, '单图')
        state = '已确认' if item.get('confirmation') and item['status'] == 'confirmed' else {'候选': '候选', '历史': '历史'}.get(item['role'], '待核对')
        folder = destination / category / f'{asset_id}_{label}'
        path = folder / f'{asset_id}_{label}_{view}_v001{source.suffix.lower()}'
        if len(str(path)) >= 245 or not path.resolve().is_relative_to(destination.resolve()):
            raise ValueError(f'目标路径过长或越界：{path}')
        records.append({'asset_id': asset_id, 'library_asset_id': item['id'], 'name': item['name'],
                        'category': category, 'original_category': item['category'], 'role': item['role'],
                        'state': state, 'view': view, 'library_version': 'v001', 'source_project': item['project'],
                        'original_source': item['source'], 'copy_source': str(source),
                        'destination': path.relative_to(destination).as_posix(), 'bytes': item['bytes'],
                        'width': item.get('width'), 'height': item.get('height'),
                        'confirmation': item.get('confirmation')})
    # Retain earlier entries even when a local catalog entry is later removed.
    records.extend(row for row in previous if row['library_asset_id'] not in {i['id'] for i in items})
    paths = [r['destination'].casefold() for r in records]
    if len(paths) != len(set(paths)):
        raise ValueError('目标文件名存在冲突')
    return {'schema_version': '1.0', 'created_at': datetime.now(timezone.utc).isoformat(),
            'root': str(destination), 'assets': records, 'removed_assets': removed,
            'counts': dict(Counter(r['category'] for r in records if not r.get('trashed_at')))}


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def flatten_folders(vault):
    root = (vault / '资产库').resolve()
    spec = importlib.util.spec_from_file_location('vault_guard', vault / 'tools' / 'vault_git_guard.py')
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    guard.require_active_write(vault, OWNER, root)
    plan = json.loads((root / '资产索引.json').read_text(encoding='utf-8'))
    moves = []
    for row in plan['assets']:
        old = (root / row['destination']).resolve()
        if len(Path(row['destination']).parts) == 3:
            continue
        if len(Path(row['destination']).parts) != 4 or old.parent.name not in {'候选', '待核对', '已确认', '历史'}:
            raise ValueError(f'不符合预期的目录结构：{old}')
        new = old.parent.parent / old.name
        if not old.is_relative_to(root) or not new.resolve().is_relative_to(root):
            raise ValueError('移动路径超出资产库')
        source = Path(row['copy_source'])
        current = old if old.exists() else new
        if not current.is_file() or current.read_bytes() != source.read_bytes():
            raise ValueError(f'图片缺失或内容有变化：{current}')
        if old.exists() and new.exists():
            raise ValueError(f'新位置已存在文件，拒绝覆盖：{new}')
        moves.append((old, new))
        row['destination'] = new.relative_to(root).as_posix()
    for old, new in moves:
        if old.exists():
            old.rename(new)
        if old.parent.exists() and not any(old.parent.iterdir()):
            old.parent.rmdir()  # Empty directory only; never recursive.
    result = apply_plan(plan, vault)
    result['flattened'] = len(moves)
    return result


def apply_plan(plan, vault):
    spec = importlib.util.spec_from_file_location('vault_guard', vault / 'tools' / 'vault_git_guard.py')
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    root = Path(plan['root']).resolve()
    guard.require_active_write(vault, OWNER, root)
    if root != (vault / '资产库').resolve():
        raise ValueError('目标必须为当前知识库的资产库根目录')
    # Complete all collision checks before the first copy. Existing bytes are
    # compared directly (no hashes); an existing different file is never replaced.
    for row in plan['assets']:
        target, source = root / row['destination'], Path(row['copy_source'])
        if not target.resolve().is_relative_to(root):
            raise ValueError('路径越界')
        if not source.is_file() or source.stat().st_size != row['bytes']:
            raise ValueError(f'源文件变化：{source}')
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise ValueError(f'目标已有不同内容，拒绝覆盖：{target}')
    for category in CATEGORIES:
        folder = root / category
        folder.mkdir(parents=True, exist_ok=True)
        (folder / '分类说明.md').write_text(f'# {category}\n\n每项资产使用唯一编号单独建档。原始名称与来源见根目录资产索引。分类根据已有标注和文件名匹配，尚未逐图人工复核。\n', encoding='utf-8')
    copied = 0
    for row in plan['assets']:
        target = root / row['destination']
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(row['copy_source'], target)
            copied += 1
        if target.read_bytes() != Path(row['copy_source']).read_bytes():
            raise ValueError(f'复制校验失败：{target}')
        note = target.parent / '资产说明.md'
        note.write_text(f"# {row['asset_id']}｜{row['name']}\n\n"
                        f"- 分类：{row['category']}\n- 原始用途记录：{row['role']}\n- 原始状态记录：{row['state']}\n"
                        f"- 库内版本：{row['library_version']}（首次入库，不代表原项目版本）\n"
                        f"- 来源项目：{row['source_project']}\n- 原始文件名：{Path(row['original_source']).name}\n"
                        f"- 原始路径：`{row['original_source']}`\n- 图片：[[资产库/{target.relative_to(root).as_posix()}]]\n\n"
                        '原名称和历史版本保留于来源记录。入库与目录分类不构成当前短剧的用户确认。\n', encoding='utf-8')
    write_json(root / '资产索引.json', plan)
    with (root / '资产索引.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        fields = ['asset_id', 'category', 'name', 'state', 'source_project', 'destination', 'original_source']
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(plan['assets'])
    (root / '命名与入库规范.md').write_text('''# 资产命名与入库规范

## 目录与命名

路径：`类别/类别前缀-六位编号_名称/类别前缀-六位编号_名称_视图_v001.扩展名`。

分类前缀：大头照 HEAD；服装三视图 WARD；人物1x4 CHAR；道具 PROP；产品 PROD；风格 STYLE；其他 OTHER。
例如：`WARD-000001_公主粉色睡袍_三视图_v001.png`（示例，不指代当前具体编号）。
编号全库唯一、后续不因名称或分类调整重复分配。同名图以编号区分，不覆盖、不凭同名合并。
名称使用已有语义名称；无法识别的图片保留原始名称，等待补充，不凭空命名。
文件名中的名称最多 32 字符；完整原名、旧版本、旧路径永久保留在资产索引和资产说明。
视图明确时写大头照、三视图、1x4；无法可靠判断的写单图，后续可登记正面、侧面、背面、45度俯视、90度俯瞰。
扩展名统一小写，图片内容、分辨率和原有格式保持不变。v001 表示本库首次保存，不抹除原项目 R05 等版本事实。

## 状态与来源

图片直接放在各自资产文件夹中，不再建立候选、待核对、历史、已确认等状态子目录。
状态仅作为原始来源记录保留，不控制资产库的文件夹布局，也不因整理目录自动确认项目产物。
分类仅依赖已有分类及明确文件名证据，本次没有逐张视觉终审；环境和不能可靠归入现有类别的图片归其他。
一个历史图片文件暂建一个资产条目；不自动把相似图、同名图或不同项目版本合并为同一套图。

## 索引与后续入库

`资产索引.json` 是本次复制及命名映射的主清单；CSV 是便于查看的导出版本。
重复执行按原资产库 ID 复用现有编号及路径；已有不同内容禁止覆盖。以后修改图片新增版本。
源项目和执行库副本均保留。本次只是复制，不迁移、不修订原项目引用、不推进生产阶段。
以后新增正式归档图片仍通过 visual-asset-library 写入门禁进入本目录；界面运行、缩略图缓存和执行代码不写到这里。
知识库既有规则忽略图片二进制的 Git 跟踪。图片已在磁盘保存，但 Git 提交只包含说明与索引，不能作为图片备份证明。
''', encoding='utf-8')
    return {'copied': copied, 'total': len(plan['assets']), 'counts': plan['counts'], 'byte_comparison': 'passed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--flatten', action='store_true')
    args = parser.parse_args()
    if args.flatten:
        print(json.dumps(flatten_folders(args.vault.resolve()), ensure_ascii=False))
        raise SystemExit(0)
    plan = build_plan(args.library.resolve(), args.vault.resolve())
    args.plan.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.plan, plan)
    result = apply_plan(plan, args.vault.resolve()) if args.apply else {'total': len(plan['assets']), 'counts': plan['counts']}
    print(json.dumps(result, ensure_ascii=False))
