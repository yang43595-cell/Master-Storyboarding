"""Check focal intent and known internal-note leaks in submitted prompt bodies.

This is a text check, not an assessment of staging or generated video quality.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


HEADING_RE = re.compile(r"^##[ \t]+[^\n]+", re.MULTILINE)
SEGMENT_RE = re.compile(
    r"^##[ \t]+(?:第[ \t]*(?P<legacy>\d+)[ \t]*段|"
    r"生成段[ \t]*(?P<current>\d+(?:[ \t]*[–—\-~至][ \t]*\d+)?)|"
    r"提示词[ \t]*(?P<prompt>\d+|[一二三四五六七八九十百零〇两]+))"
    r"(?=[ \t：:｜|（(]|$)"
)
BODY_HEADING_RE = re.compile(r"^###[ \t]+(?:提交正文|提示词|模型正文)(?:[ \t]*[（(][^\n]*)?[ \t]*$", re.MULTILINE)
SUBHEADING_RE = re.compile(r"^###[ \t]+(?P<title>[^\n]+)", re.MULTILINE)
MODEL_PARTS = {"素材职责", "公共条件", "连续视听正文"}
CODE_BLOCK_RE = re.compile(r"^```(?:text|txt)?[ \t]*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
FOCAL_RE = re.compile(r"广角|中长焦|长焦|标准(?:焦段|镜头|倾向)|焦段倾向[：:][ \t]*标准")
PURPOSE_LABEL_RE = re.compile(r"光学目的[：:][ \t]*\S[^\n]*")
PURPOSE_RE = re.compile(r"保留|保持|看清|看见|看得到|清楚|可辨|可读|突出|分离|压缩|拉出|集中|呈现|显示|纵深|层次|比例|自然距离|焦点|柔化")
MM_RE = re.compile(r"(?<!\d)\d{1,3}\s*(?:mm|毫米)(?![A-Za-z0-9])", re.IGNORECASE)
MM_SOURCE_RE = re.compile(r"(?:焦距来源|焦段依据|导演依据|证据来源)[：:][^\n]+")
META_RE = re.compile(r"与(?:上|前一)段不连续|(?:视频)?模型没有上下文|(?:接|承接|沿用)(?:上一|前一|上)段|见导演包|同上")
INTERNAL_ID_RE = re.compile(r"(?<![A-Za-z0-9_-])(?:(?:DLG|AST|STP|ART)-[A-Z0-9-]+|S\d{2}-\d{3})(?![A-Za-z0-9_-])")
PATH_RE = re.compile(r"[A-Za-z]:[\\/]|/(?:Users|home|tmp)/")
CHECKLIST_RE = re.compile(r"(?:^|\n)[^\n]{0,20}(?:完成条件|自查结论|验收清单)[：:]")


def _body_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        block = CODE_BLOCK_RE.match(text)
        return block.group(1).strip() if block else ""
    return re.split(r"^(?:制作备注|后期小字|自查结论)[：:]", text,
                    maxsplit=1, flags=re.MULTILINE)[0].strip()


def _segments(text: str) -> list[tuple[str, str, str]]:
    """Read known prose layouts, without making their headings mandatory."""
    headings = list(HEADING_RE.finditer(text))
    segments: list[tuple[str, str, str]] = []
    for index, heading in enumerate(headings):
        match = SEGMENT_RE.match(heading.group())
        if not match:
            continue
        label = match.group("legacy") or match.group("current") or match.group("prompt")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.end():end]
        subheadings = list(SUBHEADING_RE.finditer(section))
        body = ""
        for part_index, part in enumerate(subheadings):
            if part.group("title").strip() in MODEL_PARTS:
                # The three sibling sections all go to the model. Stop before
                # production notes; references alone cannot replace the action body.
                parts = []
                has_action = False
                for j in range(part_index, len(subheadings)):
                    current = subheadings[j]
                    title = current.group("title").strip()
                    if title not in MODEL_PARTS:
                        break
                    stop = subheadings[j + 1].start() if j + 1 < len(subheadings) else len(section)
                    content = _body_text(section[current.end():stop])
                    if title == "连续视听正文":
                        has_action = bool(content)
                    parts.append(content)
                body = "\n\n".join(parts).strip() if has_action else ""
                break
            if BODY_HEADING_RE.fullmatch(part.group()):
                stop = subheadings[part_index + 1].start() if part_index + 1 < len(subheadings) else len(section)
                body = _body_text(section[part.end():stop])
                # A legacy wrapper may directly contain the new sibling layout.
                if body or part_index + 1 == len(subheadings):
                    break
        segments.append((label, body, section))
    return segments


def _json_segments(text: str) -> list[tuple[str, str, str]]:
    """Extract existing business fields only; not a schema or submission check."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层不是对象")
    if "drafts" in data and ("generation_segments" in data or "creative_sections" in data):
        raise ValueError("JSON 同时存在 drafts 与生成段，不猜权威正文")
    body_key = "prompt_text"
    if "generation_segments" in data:
        rows = data["generation_segments"]
    elif "creative_sections" in data:
        sections = data["creative_sections"]
        if not isinstance(sections, list) or any(
                not isinstance(s, dict) or not isinstance(s.get("generation_segments"), list)
                for s in sections):
            raise ValueError("创作段 generation_segments 格式无法识别")
        rows = [row for section in sections for row in section["generation_segments"]]
    elif "drafts" in data:
        rows, body_key = data["drafts"], "prompt"
    else:
        return []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("生成段列表格式无法识别")
    if "generation_segments" in data and "creative_sections" in data:
        sections = data["creative_sections"]
        if not isinstance(sections, list) or any(
                not isinstance(s, dict) or not isinstance(s.get("generation_segments"), list)
                for s in sections):
            raise ValueError("创作段生成段引用列表格式无法识别")
        links = [ident for section in sections for ident in section["generation_segments"]]
        ids = [row.get("segment_id") for row in rows]
        if (any(not isinstance(ident, str) for ident in links + ids)
                or set(links) != set(ids)):
            raise ValueError("顶层生成段与创作段引用不一致，不猜权威正文")

    def source_strings(value):
        if isinstance(value, dict):
            return "\n".join(f"{key}：{source_strings(item)}" for key, item in value.items())
        if isinstance(value, list):
            return "\n".join(source_strings(item) for item in value)
        return value if isinstance(value, str) else ""

    return [(str(row.get("segment_id", row.get("node_id", index))),
             row[body_key].strip() if isinstance(row.get(body_key), str) else "",
             source_strings(row)) for index, row in enumerate(rows, 1)]


def _has_optical_purpose(body: str) -> bool:
    if PURPOSE_LABEL_RE.search(body):
        return True
    return any(FOCAL_RE.search(sentence) and PURPOSE_RE.search(sentence)
               for sentence in re.split(r"[。！？；;\n]+", body))


def _focal_measurements(body: str) -> list[re.Match]:
    """Only locally identified lens values, never a prop's size/thickness."""
    found = []
    for match in MM_RE.finditer(body):
        before, after = body[max(0, match.start()-18):match.start()], body[match.end():match.end()+18]
        if re.search(r"(?:直径|半径|厚度?|长度?|宽度?|高度?|尺寸|口径|边长)[：:为是约\s]*$", before):
            continue
        if (re.search(r"(?:焦距|焦段|镜头|定焦|变焦)[：:为是\s]*$", before)
                or re.match(r"\s*(?:的)?(?:焦距|焦段|镜头(?!盖|罩|袋|布|口)|定焦|变焦|中长焦|长焦|广角|拍摄)", after)
                or (re.search(r"固定\s*$", before)
                    and re.match(r"\s*[，,]?\s*(?:拍摄|突出|压缩|保留)", after))):
            found.append(match)
    return found


def inspect_document(path: Path, *, require_focal_intent: bool = False) -> tuple[int, list[str]]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    try:
        if path.suffix.lower() == ".txt":
            segments = [(path.stem, text.strip(), text)]
        elif path.suffix.lower() == ".json":
            segments = _json_segments(text)
        else:
            segments = _segments(text)
    except ValueError as error:
        return 0, [f"无法检查：{error}；不按0段通过"]
    errors: list[str] = []
    if not segments:
        return 0, ["无法检查：当前格式未识别提交正文；可检查实际提交 TXT。此结果不是内容不合格，也不按0段通过"]
    for number, body, section in segments:
        if not body:
            errors.append(f"第 {number} 段缺少非空、完整的提交正文")
            continue
        focal_values = _focal_measurements(body)
        declared = any(FOCAL_RE.search(line) or _focal_measurements(line)
                       for line in re.findall(r"(?:摄影表达|导演摄影|导演要求|焦段依据)[：:][^\n]+", section))
        applicable = require_focal_intent or declared or bool(FOCAL_RE.search(body) or focal_values)
        if applicable and not FOCAL_RE.search(body):
            errors.append(f"第 {number} 段缺少定性焦段倾向（本段已有适用摄影要求）")
        if applicable and not _has_optical_purpose(body):
            errors.append(f"第 {number} 段缺少可观察光学目的")
        if focal_values and not MM_SOURCE_RE.search(section):
            errors.append(f"第 {number} 段出现未标注来源的精确毫米数；依据可写在正文外的制作备注中")
        if META_RE.search(body):
            errors.append(f"第 {number} 段出现生成提示词元话语；改为可直接生成的具体画面")
        if INTERNAL_ID_RE.search(body):
            errors.append(f"第 {number} 段混入内部对白、镜头或资产编号")
        if PATH_RE.search(body):
            errors.append(f"第 {number} 段混入本地文件路径；改用实际上传引用标记")
        if CHECKLIST_RE.search(body):
            errors.append(f"第 {number} 段混入内部完成条件或自查清单；移到制作备注")
    return len(segments), errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查提交正文的定性焦段表达与内部说明泄漏")
    parser.add_argument("prompt_file", type=Path)
    parser.add_argument("--require-focal-intent", action="store_true",
                        help="仅当已核对上游确有关键光学要求时启用；脚本不替代适用性判断")
    args = parser.parse_args(argv)
    count, errors = inspect_document(args.prompt_file, require_focal_intent=args.require_focal_intent)
    if not count:
        print(f"UNCHECKED {args.prompt_file}: " + "; ".join(errors))
        return 2
    if errors:
        print(f"FAIL {args.prompt_file}: {count} 个生成片段，{len(errors)} 个文字检查缺口")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(f"PASS {args.prompt_file}: {count} 个正文通过适用的焦段表达与已知内部说明检查；未指定上游的光学需求仍须人工核对，不代表空间、动作或成片效果通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
