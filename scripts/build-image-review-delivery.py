#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成不携带图片数据的用户审核清单。

该工具只核对目录、文件名与交付映射，不读取图片像素，不输出 Base64 或图片预览。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from .core.visual_context_budget import VisualContextBudgetError, assert_payload_free
except ImportError:  # 直接执行时 scripts/ 在 sys.path 上，回退到核心导入
    from core.visual_context_budget import VisualContextBudgetError, assert_payload_free


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
IMAGE_LABEL_RE = re.compile(r"^@图片([1-9]\d*)$")


def _check_image(folder: Path, raw_name: str) -> str:
    name = Path(raw_name).name
    if name != raw_name or not name:
        raise ValueError(f"图片名只接受文件名，不接受路径：{raw_name}")
    if Path(name).suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f"不是支持的图片文件名：{name}")
    candidate = folder / name
    if not candidate.is_file():
        raise ValueError(f"待审核图片不存在：{candidate}")
    return name


def parse_review_entry(raw_entry: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in raw_entry.split("|", 2)]
    if len(parts) != 3 or not all(parts):
        raise ValueError("--entry 格式必须为：@图片N|准确文件名|唯一职责")
    return parts[0], parts[1], parts[2]


def build_delivery(
    folder: Path,
    image_names: list[str] | None = None,
    *,
    review_entries: list[tuple[str, str, str]] | None = None,
    prompt_document: Path | None = None,
) -> str:
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"审核文件夹不存在：{folder}")
    image_names = image_names or []
    review_entries = review_entries or []
    if image_names and review_entries:
        raise ValueError("--image 与 --entry 不得混用")
    if not image_names and not review_entries:
        raise ValueError("至少提供一个待审核图片名")

    checked_entries: list[tuple[str | None, str, str | None]] = []
    if review_entries:
        for expected_number, (label, raw_name, responsibility) in enumerate(review_entries, start=1):
            match = IMAGE_LABEL_RE.fullmatch(label)
            if not match or int(match.group(1)) != expected_number:
                raise ValueError(
                    f"图片编号必须按上传顺序从 @图片1 连续排列；第 {expected_number} 项收到：{label}"
                )
            if not responsibility.strip():
                raise ValueError(f"{label} 缺少唯一职责")
            checked_entries.append((label, _check_image(folder, raw_name), responsibility.strip()))
    else:
        checked_entries.extend((None, _check_image(folder, name), None) for name in image_names)

    prompt_link: str | None = None
    if review_entries and prompt_document is None:
        raise ValueError("严格 --entry 模式必须提供 --prompt-document")
    if prompt_document is not None:
        resolved_prompt = prompt_document.expanduser().resolve()
        if not resolved_prompt.is_file():
            raise ValueError(f"提示词文档不存在：{resolved_prompt}")
        prompt_link = resolved_prompt.as_posix()

    folder_link = folder.as_posix()
    lines: list[str] = []
    if review_entries:
        lines.extend(
            [
                f"提示词文档：[打开提示词文档](<{prompt_link}>)",
                "",
                f"审核文件夹：[打开图片文件夹](<{folder_link}>)",
                "",
                "上传顺序与图片职责：",
                "",
            ]
        )
        lines.extend(
            f"- {label} — `{name}` — 职责：{responsibility}"
            for label, name, responsibility in checked_entries
        )
    else:
        lines.extend(
            [
                f"审核文件夹：[打开图片文件夹](<{folder_link}>)",
                "",
                "待审核图片名：",
                "",
            ]
        )
        lines.extend(f"- `{name}`" for _, name, _ in checked_entries)
    output = "\n".join(lines)
    assert_payload_free(output)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成提示词与图片审核通道清单")
    parser.add_argument("--folder", required=True, type=Path, help="图片所在文件夹绝对路径")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--image", action="append", help="兼容旧调用：待审核图片名，可重复")
    input_group.add_argument(
        "--entry",
        action="append",
        help="严格交付项，可重复；格式：@图片N|准确文件名|唯一职责",
    )
    parser.add_argument("--prompt-document", type=Path, help="提示词文档绝对路径")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    try:
        entries = [parse_review_entry(item) for item in (args.entry or [])]
        print(
            build_delivery(
                args.folder,
                args.image,
                review_entries=entries,
                prompt_document=args.prompt_document,
            )
        )
    except (ValueError, VisualContextBudgetError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
