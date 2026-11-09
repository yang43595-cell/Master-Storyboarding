"""文档引用完整性守门：链接目标、锚点、链接文字与共享登记的一致性。

2026-11-09 审查发现 decisions 断链、链接文字与目标错位、索引漏登记等问题
全部烂在「没有任何自动检查覆盖普通文档引用」的盲区里。本文件把五类检查
收进测试套件，fail 时给出 文件:行号 + 目标，便于定位。
"""
from __future__ import annotations

import importlib.util
import re
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)
TEXT_FILE_RE = re.compile(r"^(.+?\.md)\s*的")


def _doc_files() -> list[Path]:
    files = sorted(ROOT.glob("*.md"))
    for base in ("docs", "skills", "references"):
        root = ROOT / base
        if root.exists():
            files += sorted(root.rglob("*.md"))
    return files


def _slug(title: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)
    text = text.replace("`", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path not in cache:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            cache[path] = set()
        else:
            cache[path] = {_slug(m.group(1)) for m in HEADING_RE.finditer(text)}
    return cache[path]


def _iter_links(path: Path):
    """逐行产出 (行号, 链接文字, 目标)，跳过代码围栏内的示例链接。"""
    in_fence = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in LINK_RE.finditer(line):
            yield lineno, m.group(1), m.group(2)


def _external_or_placeholder(target: str) -> bool:
    return (
        "://" in target
        or target.startswith(("mailto:", "//"))
        or "<" in target
        or ">" in target
        or "{" in target
    )


def test_doc_links_and_anchors_resolve() -> None:
    failures: list[str] = []
    cache: dict[Path, set[str]] = {}
    for path in _doc_files():
        rel = path.relative_to(ROOT)
        for lineno, _text, target in _iter_links(path):
            if _external_or_placeholder(target):
                continue
            raw_path, _, raw_anchor = target.partition("#")
            anchor = urllib.parse.unquote(raw_anchor)
            dest = path if raw_path == "" else path.parent / urllib.parse.unquote(raw_path)
            if not dest.exists():
                failures.append(f"{rel}:{lineno} 链接目标不存在：{target}")
                continue
            if anchor and dest.is_file() and dest.suffix == ".md":
                anchors = _anchors(dest, cache)
                base = re.sub(r"-\d+$", "", anchor)
                if anchor not in anchors and base not in anchors:
                    failures.append(f"{rel}:{lineno} 锚点不存在：{target}")
    assert failures == [], "文档链接/锚点失效：\n" + "\n".join(failures)


def test_link_text_names_match_target_files() -> None:
    failures: list[str] = []
    for path in _doc_files():
        rel = path.relative_to(ROOT)
        for lineno, text, target in _iter_links(path):
            if _external_or_placeholder(target):
                continue
            m = TEXT_FILE_RE.match(text.strip())
            if not m:
                continue
            raw_path = urllib.parse.unquote(target.partition("#")[0])
            if raw_path and Path(raw_path).name != m.group(1):
                failures.append(
                    f"{rel}:{lineno} 链接文字写「{m.group(1)}」却指向 {raw_path}"
                )
    assert failures == [], "链接文字与目标文件不一致：\n" + "\n".join(failures)


def test_shared_contracts_registered_and_present() -> None:
    # library_runtime 按名硬编码的 _shared 合同：清单里有、磁盘必须有且非空，
    # 把 build_task_package 的运行时校验提前到测试时。
    spec = importlib.util.spec_from_file_location(
        "library_runtime", ROOT / "skills/asset-image-assistant/library_runtime.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    failures: list[str] = []
    for ref in module.local_references("物品"):
        dest = ROOT / ref
        if not dest.is_file() or dest.stat().st_size == 0:
            failures.append(f"local_references 登记的合同缺失或为空：{ref}")
    shared_root = ROOT / "skills" / "_shared"
    for md in sorted(shared_root.glob("*.md")):
        if md.stat().st_size == 0:
            failures.append(f"_shared 合同为空文件：{md.name}")
    assert failures == [], "共享合同登记不一致：\n" + "\n".join(failures)


def test_references_index_covers_subdirectories() -> None:
    index = (ROOT / "references" / "index.md").read_text(encoding="utf-8")
    missing = [
        d.name
        for d in sorted((ROOT / "references").iterdir())
        if d.is_dir() and d.name not in index
    ]
    assert missing == [], f"references/ 子目录未登记进 index.md：{missing}"
