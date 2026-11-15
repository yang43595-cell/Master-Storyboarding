"""Bundled layout-only image references for character asset generation."""
import hashlib
import re
from pathlib import Path


_DIRECTORY = Path(__file__).resolve().parents[2] / "assets" / "character-layout-templates"
CHARACTER_PROMPT_PATH = (_DIRECTORY.parents[1] / "skills" / "asset-image-assistant"
                         / "references" / "character-layout-prompts.md")
_FILES = {
    "大头照": "大头照_通用灰模_3x4_R01.png",
    "服装": "服装三视图_通用灰模_16x9_R01.png",
    "人物1x4": "人物1x4_通用灰模_留发饰空间_R02.png",
}
_ALIASES = {"服装三视图": "服装", "人物1×4": "人物1x4"}
_ROLE = "灰模仅参考主体的位置与大小。人物身份、发型、头饰、服装款式、材质和颜色以原参考及本次要求为准"


def character_layout_reference(kind):
    if not isinstance(kind, str) or (name := _FILES.get(_ALIASES.get(kind, kind))) is None:
        return None
    path = _DIRECTORY / name
    return {"original": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "role": _ROLE, "layout_only": True}


def character_layout_references():
    return {kind: character_layout_reference(kind) for kind in _FILES}


def character_prompt_templates():
    """Use the same approved template bodies in ordinary preparation and Web."""
    text = CHARACTER_PROMPT_PATH.read_text(encoding="utf-8")
    bodies = re.findall(r"^\x60\x60\x60text\n(.*?)\n\x60\x60\x60$", text, re.MULTILINE | re.DOTALL)
    if len(bodies) != len(_FILES) or not all(body.strip() for body in bodies):
        raise ValueError("人物资产图提示词模板缺少正文")
    return dict(zip(_FILES, bodies))


def render_character_prompt(kind, references):
    """Adapt slot numbers and permit planned designs without identity/wardrobe photos."""
    body = character_prompt_templates()[kind]
    gray_slot = len(references)
    if kind == "人物1x4":
        return body
    source = next((index for index, ref in enumerate(references[:-1], 1)
                   if kind != "服装" or ref.get("category") != "大头照"), None)
    if source:
        slots = {1: source, 2: gray_slot}
        return re.sub(r"参考图 ([12])", lambda m: f"参考图 {slots[int(m[1])]}", body)
    _, layout, framing = body.split("\n\n", 2)
    layout = layout.split("。", 1)[0].replace("参考图 2", f"参考图 {gray_slot}") + "。"
    if kind == "大头照":
        direction = "依据当前需求设计人物的正面大头照。以本次要求确定身份、肤色、发型及头饰。"
    else:
        direction = ("按本次设定制作同一套服装的正面、左侧和背面三视图。以本次要求确定服装、配饰和鞋履。"
                     "如附有身份参考，只参考适用的体型信息，不把身份参考中的服装当作本次服装设计。")
    return "\n\n".join((direction, layout, framing))
