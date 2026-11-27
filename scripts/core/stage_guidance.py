"""Automatic stage & product must-read cheat-sheet emitted by progressive stage CLIs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .character_layout import character_layout_references
from .product_reference_pack import resolve_product_pack
from .project_kickoff import load_kickoff

ROOT = Path(__file__).resolve().parents[2]

_STAGE_CORE_DOCS: dict[str, list[str]] = {
    "creative-director": [
        "PROJECT_RULES.md",
        "skills/creative-director/SKILL.md",
        "skills/creative-director/modules/story-method.md",
    ],
    "asset-image-assistant": [
        "PROJECT_RULES.md",
        "skills/asset-image-assistant/SKILL.md",
        "skills/_shared/redframe-operations.md",
    ],
    "storyboard-planner": [
        "PROJECT_RULES.md",
        "skills/storyboard-planner/SKILL.md",
        "skills/storyboard-planner/modules/shot-planning.md",
    ],
    "storyboard-director": [
        "PROJECT_RULES.md",
        "skills/storyboard-director/SKILL.md",
        "skills/storyboard-director/references/image-continuity-hard-rules.md",
    ],
    "voiceover-to-video-script": [
        "PROJECT_RULES.md",
        "skills/voiceover-to-video-script/SKILL.md",
        "skills/voiceover-to-video-script/modules/video-method.md",
    ],
}

_STAGE_REMINDERS: dict[str, list[str]] = {
    "creative-director": [
        "防断连硬红线：每回合只写 1–2 个段落 JSON（≤100 行）并立即调用 narrative_package.py put-paragraph 落盘",
        "严禁翻读底层核心 Python 源码或手拼整份叙事包大 JSON",
    ],
    "asset-image-assistant": [
        "原图实看硬红线：必须用 read_file 亲眼实看每一张候选图或新出图（核五官服饰、水印穿帮、产品包装），严禁盲信文件名",
        "使用 asset_stage_cli.py stage-and-show 与 confirm-round 挂图交审及自动拼 1×4 人物板",
    ],
    "storyboard-planner": [
        "防断连硬红线：每回合最多写 1 场（≤4 镜；>4 镜拆 put-scene + append-shots）；>6 场长片用 plan-acts 锁接口多幕并行写稿",
        "阅读边界：导演阶段只读文字规范与 PRODUCT_BRIEF.md，不读取产品 PNG/MP4 媒体原件",
    ],
    "storyboard-director": [
        "原图实看硬红线：必须用 read_file 原尺寸实看本轮母版与分镜补图，严禁不看图直接写检查结论",
        "使用 visual_stage_cli.py save-check 与 register-supplement 完成全镜覆盖与补图采用",
    ],
    "voiceover-to-video-script": [
        "防断连硬红线：每回合最多写 1–2 个生成段（≤80 行）；>8 段长片用 plan-acts 锁接口多幕并行写稿",
        "严格保留导演包 required_visuals 原句单独占行、100% 覆盖原词对白；红帧只保存回读草稿，未经明确授权绝不点击生成视频",
    ],
}


def build_stage_guidance(project: Path, skill: str) -> dict[str, Any]:
    project = Path(project).resolve()
    core_docs = list(_STAGE_CORE_DOCS.get(skill, ["PROJECT_RULES.md", f"skills/{skill}/SKILL.md"]))
    reminders = list(_STAGE_REMINDERS.get(skill, []))
    product_docs: list[str] = []
    method_docs: list[str] = []

    kickoff = load_kickoff(project) or {}
    product_cfg = kickoff.get("product") if isinstance(kickoff.get("product"), dict) else {}
    canvas_cfg = kickoff.get("canvas") if isinstance(kickoff.get("canvas"), dict) else {}
    product_id = str(product_cfg.get("product_id") or "")
    model_norm = str(canvas_cfg.get("model") or "").lower().replace(" ", "")

    # 1. Match skill's knowledge-profile.json confirmed methods
    kp_path = ROOT / "skills" / skill / "knowledge-profile.json"
    if kp_path.is_file():
        try:
            kp = json.loads(kp_path.read_text(encoding="utf-8"))
            for m in kp.get("professional_methods") or []:
                if m.get("binding_status") != "user_confirmed":
                    continue
                reqs = m.get("requires") or {}
                req_pid = reqs.get("request.product_id")
                if req_pid and req_pid != product_id:
                    continue
                req_mod = reqs.get("request.selected_video_model.id")
                if req_mod and req_mod.lower().replace(" ", "") not in model_norm and model_norm:
                    continue
                m_rel = f"knowledge/runtime/{m['path']}"
                if (ROOT / m_rel).is_file():
                    target_list = product_docs if req_pid else method_docs
                    if m_rel not in target_list:
                        target_list.append(m_rel)
        except (OSError, ValueError, KeyError):
            pass

    # 2. Match product reference pack brief & runtime wiki templates
    pack = resolve_product_pack(product_cfg) if product_cfg else None
    if pack:
        pname = str(pack.get("product_name") or "")
        pusage = str(pack.get("usage") or product_cfg.get("product_usage") or "")
        for ref in pack.get("references") or []:
            p = Path(str(ref.get("path") or ""))
            if ref.get("role") == "brief" and p.is_file():
                rel_str = p.resolve().relative_to(ROOT.resolve()).as_posix() if p.resolve().is_relative_to(ROOT.resolve()) else str(p)
                if rel_str not in product_docs:
                    product_docs.insert(0, rel_str)
        brand_prefix = pname[:3] if len(pname) >= 3 else pname
        entities_root = ROOT / "knowledge/runtime/wiki/entities/products"
        if brand_prefix and entities_root.is_dir():
            for folder in sorted(entities_root.iterdir()):
                if not folder.is_dir():
                    continue
                if brand_prefix in folder.name or folder.name in pname:
                    for md in sorted(folder.rglob("*.md")):
                        if skill == "storyboard-planner" and "生图模板" in md.name:
                            continue
                        rel_md = md.relative_to(ROOT).as_posix()
                        if rel_md not in product_docs:
                            product_docs.append(rel_md)
        action_count = sum(1 for r in (pack.get("references") or []) if r.get("role") in {"action", "technique"})
        reminders.append(
            f"已绑定产品参考包《{pname}》（模式：{pusage or 'default'}，动作参考 {action_count} 条）：动笔前先读 product_docs 中的产品简报与专属规范！"
        )

    out: dict[str, Any] = {
        "stage_skill": skill,
        "core_docs": core_docs,
        "method_docs": method_docs,
        "product_docs": product_docs,
        "key_reminders": reminders,
    }
    if skill == "asset-image-assistant":
        layouts = character_layout_references()
        if layouts:
            out["character_layout_templates"] = layouts
    return out
