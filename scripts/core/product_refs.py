"""Validate explicit product use, never infer it from ordinary prop names."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .asset_refs import current_narrative
from .artifacts import project_path
from .delivery_files import validate_required_files, discover_dependencies, check_package_files, DEFAULT_MAX_PACKAGE_BYTES
from ..visual_asset_choices import effective, read_choices


ASSET_ID = re.compile(r"(?<![A-Za-z0-9_-])AST-[A-Z0-9]+(?:-[A-Z0-9]+)*(?![A-Za-z0-9-])")
PRODUCT_MARKER = re.compile(r"PRODUCT_VISUAL_INPUT:\s*REAL_PRODUCT\b")
PRODUCT_REFERENCE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\.{1,2}[\\/]|assets[\\/])"
    r'[^`"|\r\n]*product-references[\\/][^`"|\r\n]+?\.(?:png|jpg|jpeg|webp)',
    re.IGNORECASE,
)


def validate_documents(project: Path, directory: Path) -> list[str]:
    """Check JSON and legacy Markdown explicit product markers or typed asset IDs.

    Free prose alone cannot establish whether a prop is an advertising product.
    Asset IDs use the confirmed narrative and current user adjustments, never the
    convenience snapshot or a filename-sorted candidate.
    """
    errors: list[str] = []
    categories: dict[str, str] | None = None
    source_error: str | None = None
    documents = sorted(p for p in directory.rglob('*') if p.is_file() and p.suffix.lower() in {'.md', '.json'})
    for path in documents:
        text = path.read_text(encoding='utf-8-sig')
        structured_product = False
        if path.suffix.lower() == '.json':
            try:
                value = json.loads(text)
            except ValueError as exc:
                errors.append(f'{path.name}: 无法核对无效 JSON：{exc}')
                continue
            def strings(item):
                if isinstance(item, dict):
                    for key, child in item.items():
                        yield key
                        yield from strings(child)
                elif isinstance(item, list):
                    for child in item:
                        yield from strings(child)
                elif isinstance(item, str):
                    yield item
            values = list(strings(value))
            structured_product = 'REAL_PRODUCT' in values
            text = '\n'.join(values)

        ids = set(ASSET_ID.findall(text))
        if ids and categories is None and source_error is None:
            try:
                narrative = current_narrative(project)
                # Excluding an image does not erase the story object's category.
                categories = {a['asset_id']: a['category'] for a in narrative['asset_requirements']}
                categories.update({a['asset_id']: a['category']
                                   for a in effective(narrative, read_choices(project))})
            except (OSError, ValueError, KeyError) as exc:
                source_error = str(exc)
        if ids and source_error:
            errors.append(f'{path.name}: 无法核对资产类别：{source_error}')
        if ids and categories is not None:
            unknown = sorted(ids - categories.keys())
            if unknown:
                errors.append(f'{path.name}: 未登记的资产编号：{", ".join(unknown)}')
        product_used = structured_product or PRODUCT_MARKER.search(text) or any(
            (categories or {}).get(asset_id) == 'REAL_PRODUCT' for asset_id in ids
        )
        if not product_used:
            continue
        refs = PRODUCT_REFERENCE.findall(text)
        if not refs:
            errors.append(f'{path.name}: REAL_PRODUCT 出镜但未引用 product-references 真实图片')
        for raw in refs:
            ref = project_path(project, raw)
            if not ref.is_file():
                errors.append(f'{path.name}: 产品参考图片不存在：{raw}')
    return errors


def _json_string_fields(value, pointer=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _json_string_fields(child, pointer + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _json_string_fields(child, pointer + (str(index),))
    elif isinstance(value, str):
        yield pointer, value


def validate_image_documents(project: Path, directory: Path) -> list[str]:
    """Generic image path existence for JSON fields/body and legacy MD/TXT."""
    prefixes = r'(?:05_分镜|04_资产|02_正式分镜图|06_参考帧与临时素材|assets[\\/]product-references)'
    pattern = re.compile(r'(?:[A-Za-z]:[\\/]|\.{1,2}[\\/]|' + prefixes
                         + r'[\\/])[^`"|()\s]+?\.(?:png|jpg|jpeg|webp)', re.I)
    extension = re.compile(r'\.(?:png|jpg|jpeg|webp)$', re.I)
    path_keys = {'path', 'paths', 'path_or_url', 'file', 'files', 'image', 'images', 'reference', 'references'}
    errors = []
    for path in sorted(directory.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in {'.json', '.md', '.txt'}:
            continue
        text = path.read_text(encoding='utf-8-sig')
        fields = [((), text)]
        if path.suffix.lower() == '.json':
            try:
                fields = list(_json_string_fields(json.loads(text)))
            except ValueError as exc:
                errors.append(f'{path.name}: 无法检查图片引用的无效 JSON：{exc}')
                continue
        refs = set()
        for pointer, value in fields:
            # JSON scalar path values may contain spaces and escaped backslashes.
            declared_path = any(key in path_keys or key.endswith(('_path', '_paths', '_file', '_files'))
                                for key in pointer)
            if declared_path and extension.search(value) and '\n' not in value and '://' not in value:
                refs.add(value)
            else:
                refs.update(pattern.findall(value))
        for raw in sorted(refs):
            try:
                if not project_path(project, raw).is_file():
                    errors.append(f'{path.name}: Referenced image does not exist: {raw}')
            except (OSError, ValueError) as exc:
                errors.append(f'{path.name}: 图片路径无法解析：{raw}: {exc}')
    return errors



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--directory', type=Path, help='可选：检查已写文档中的产品引用')
    parser.add_argument('--require-file', action='append', default=[], help='产出前必须可读且非空的准确文件；可重复，外置绝对路径须同时声明 --input-root')
    parser.add_argument('--input-root', type=Path, action='append', default=[], help='本轮显式选用的外部输入根目录；检查所选文件及已启用发现的可达依赖，不扫描根目录')
    parser.add_argument('--include-images', action='store_true', help='同时检查 JSON/MD/TXT 的通用图片路径')
    parser.add_argument('--discover-dependencies', action='store_true', help='递归检查所选 JSON 结构化文件引用及 manifest 依赖；不推断自然语言')
    parser.add_argument('--package', type=Path, action='append', default=[], help='发送前检查实际成品文件字节，可重复')
    parser.add_argument('--max-package-bytes', type=int, default=DEFAULT_MAX_PACKAGE_BYTES, help='每包严格小于该字节数，默认19000000')
    args = parser.parse_args()
    if args.directory is None and not args.require_file and not args.package:
        parser.error('至少提供 --directory、--require-file 或 --package')
    if args.include_images and args.directory is None:
        parser.error('--include-images 需要 --directory')
    try:
        errors = validate_required_files(args.project, args.require_file, args.input_root)
        if args.discover_dependencies:
            selected = list(args.require_file)
            if args.directory is not None:
                selected += [str(p.resolve()) for p in sorted(args.directory.rglob('*.json'))]
            if not selected:
                raise ValueError('--discover-dependencies requires selected files or JSON directory')
            report = discover_dependencies(args.project, selected, args.input_root)
            print(json.dumps(report, ensure_ascii=False))
            errors.extend(report['errors'] + report['uncheckable'])
        if args.package:
            print(json.dumps({'packages': check_package_files(args.package, args.max_package_bytes)}, ensure_ascii=False))
        if args.directory is not None:
            errors.extend(validate_documents(args.project.resolve(), args.directory.resolve()))
        if args.include_images:
            errors.extend(validate_image_documents(args.project.resolve(), args.directory.resolve()))
    except (OSError, ValueError, RuntimeError) as exc:
        errors = [str(exc)]
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
