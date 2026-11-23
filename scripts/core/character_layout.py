"""Bundled layout-only image references for character asset generation."""
import hashlib
import re
from pathlib import Path


_DIRECTORY = Path(__file__).resolve().parents[2] / "assets" / "character-layout-templates"
CHARACTER_PROMPT_PATH = (_DIRECTORY.parents[1] / "skills" / "asset-image-assistant"
                         / "references" / "character-layout-prompts.md")
BOARD_IMPORT_ONLY = "人物1×4不再生图；请将已确认大头照与对应服装三视图本地拼版后导入成图"
_FILES = {
    "大头照": "大头照_通用灰模_3x4_R01.png",
    "服装": "服装三视图_通用灰模_4x3_R02.png",
}
_ALIASES = {"服装三视图": "服装"}
_DESIGN_TEMPLATES = {"大头照": "大头照（无对标）", "服装": "服装（无对标）"}
_ROLE = "灰模仅参考主体的位置与大小。人物身份、发型、头饰、服装款式、材质和颜色以原参考及本次要求为准"


def character_layout_reference(kind):
    if not isinstance(kind, str) or (name := _FILES.get(_ALIASES.get(kind, kind))) is None:
        return None
    path = _DIRECTORY / name
    return {"original": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "role": _ROLE, "layout_only": True}


def character_layout_references(kinds=None):
    return {kind: character_layout_reference(kind) for kind in _FILES if kinds is None or kind in kinds}


def character_prompt_templates(kinds=None):
    """Use the same approved template bodies in ordinary preparation and Web."""
    text = CHARACTER_PROMPT_PATH.read_text(encoding="utf-8")
    bodies = re.findall(r"^\x60\x60\x60text\n(.*?)\n\x60\x60\x60$", text, re.MULTILINE | re.DOTALL)
    keys = (*_FILES, *_DESIGN_TEMPLATES.values())
    if len(bodies) != len(keys) or not all(body.strip() for body in bodies):
        raise ValueError("人物资产图提示词模板缺少正文")
    if kinds is not None:
        kinds = set(kinds)
        kinds.update(variant for kind, variant in _DESIGN_TEMPLATES.items() if kind in kinds)
    return {kind: body for kind, body in zip(keys, bodies) if kinds is None or kind in kinds}


def character_template_kinds(output_kinds):
    return {kind for value in output_kinds if isinstance(value, str)
            if (kind := _ALIASES.get(value, value)) in _FILES}


def unfilled_character_prompt_fields(kind, prompt):
    if kind not in _DESIGN_TEMPLATES or '[' not in prompt:
        return []
    template = character_prompt_templates({kind})[_DESIGN_TEMPLATES[kind]]
    return [field for field in re.findall(r"\[[^\[\]\n]+\]", template) if field in prompt]


def render_character_prompt(kind, references):
    """Adapt slot numbers and permit planned designs without identity/wardrobe photos."""
    templates = character_prompt_templates()
    body = templates[kind]
    has_layout = bool(references and references[-1].get("layout_only"))
    original_refs = references[:-1] if has_layout else references
    source = next((index for index, ref in enumerate(original_refs, 1)
                   if kind != "服装" or ref.get("category") != "大头照"), None)
    if not source:
        body = templates[_DESIGN_TEMPLATES[kind]]
    if not has_layout:
        body = "\n\n".join(block for block in body.split("\n\n") if "灰模" not in block)
    if source:
        slots = {1: source, 2: len(references)}
        return re.sub(r"参考图 ([12])", lambda m: f"参考图 {slots[int(m[1])]}", body)
    if has_layout:
        body = body.replace("参考图 1", f"参考图 {len(references)}")
    if kind == "服装" and original_refs:
        body = "身份参考只用于角色身份，不把身份参考中的服装当作本次服装设计。\n\n" + body
    return body
