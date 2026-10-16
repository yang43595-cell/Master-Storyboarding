"""Validate explicit product use, never infer it from ordinary prop names."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .asset_refs import current_narrative
from .artifacts import project_path
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    try:
        errors = validate_documents(args.project.resolve(), args.directory.resolve())
    except (OSError, ValueError) as exc:
        errors = [str(exc)]
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
