#!/usr/bin/env python3
"""Progressive builder, dialogue-evidence binder, and validator for narrative.package (v2.0)."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from copy import deepcopy
from core import revision_control as rc
from core.io import project_lock
from core.product_reference_pack import resolve_product_pack
from core.project_kickoff import load_kickoff
from core.schema_contracts import require_schema
from core.stage_guidance import build_stage_guidance

REPO = Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_init(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    base = _load_json(project / args.from_package) if getattr(args, "from_package", None) else {}
    inp = _load_json(Path(args.header).resolve()) if args.header else {}
    rev = str(args.revision or inp.get("revision") or "R01")
    out_rel = args.output or f"02_编导/叙事包/{rev}_叙事包.json"
    raw_cs = inp.get("change_summary") or getattr(args, "change_summary", None)
    cs_list = list(raw_cs) if isinstance(raw_cs, list) else ([str(raw_cs)] if raw_cs else [f"{rev} 编导叙事包"])
    raw_lf = deepcopy(inp.get("locked_facts") if "locked_facts" in inp else (base.get("locked_facts") or []))
    norm_lf = [
        {"fact_id": f"FACT-{i:02d}", "statement": str(f), "source_ref": "project_request.json#kickoff"}
        if isinstance(f, str) else f
        for i, f in enumerate(raw_lf, 1)
    ]
    pkg: dict[str, Any] = {
        "schema_version": "2.0",
        "project_id": str(inp.get("project_id") or base.get("project_id") or f"MSB-{project.name.upper()[:20]}"),
        "revision": rev,
        "status": "candidate",
        "objective": str(inp.get("objective") or args.objective or base.get("objective") or "编写完整连贯的短剧叙事包"),
        "locked_facts": norm_lf,
        "story_paragraphs": deepcopy(base.get("story_paragraphs") or []),
        "dialogues": deepcopy(base.get("dialogues") or []),
        "asset_requirements": deepcopy(base.get("asset_requirements") or []),
        "change_summary": cs_list,
        "open_questions": deepcopy(inp.get("open_questions") if "open_questions" in inp else (base.get("open_questions") or [])),
    }
    with project_lock(project):
        _save_json(project / out_rel, pkg)
    print(json.dumps({
        "must_read": build_stage_guidance(project, "creative-director"),
        "initialized": out_rel,
        "revision": rev,
        "cloned_from": getattr(args, "from_package", None),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_put_paragraph(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    pkg_path = project / args.package
    inp = _load_json(Path(args.input).resolve())
    if isinstance(inp, dict) and isinstance(inp.get("story_paragraphs"), list):
        items = inp["story_paragraphs"]
    else:
        items = inp if isinstance(inp, list) else [inp]
    with project_lock(project):
        pkg = _load_json(pkg_path)
        pars = list(pkg.get("story_paragraphs") or [])
        dlgs = list(pkg.get("dialogues") or [])
        for item in items:
            pid = str(item["paragraph_id"])
            p_obj = {"paragraph_id": pid, "text": str(item["text"])}
            idx = next((i for i, p in enumerate(pars) if p.get("paragraph_id") == pid), None)
            if idx is None:
                pars.append(p_obj)
            else:
                pars[idx] = p_obj
            for d in item.get("dialogues") or []:
                did = str(d["dialogue_id"])
                if f"（{did}）" not in p_obj["text"] and f"({did})" not in p_obj["text"]:
                    raise SystemExit(f"段落 {pid} 正文中缺少对白锚点标记（{did}）")
                d_obj = {
                    "dialogue_id": did,
                    "speaker_id": str(d.get("speaker_id") or d["speaker"]),
                    "text": str(d["text"]),
                    "source_paragraph_id": pid,
                    "status": str(d.get("status") or "confirmed"),
                    "source_ref": str(d.get("source_ref") or f"{pkg['revision']} 原创台词"),
                }
                d_idx = next((i for i, x in enumerate(dlgs) if x.get("dialogue_id") == did), None)
                if d_idx is None:
                    dlgs.append(d_obj)
                else:
                    dlgs[d_idx] = d_obj
        pkg["story_paragraphs"] = sorted(pars, key=lambda x: x["paragraph_id"])
        pkg["dialogues"] = sorted(dlgs, key=lambda x: x["dialogue_id"])
        _save_json(pkg_path, pkg)
    print(json.dumps({"paragraphs": [p["paragraph_id"] for p in pkg["story_paragraphs"]], "dialogues_count": len(pkg["dialogues"])}, ensure_ascii=False))
    return 0


def cmd_put_assets(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    pkg_path = project / args.package
    pkg = _load_json(pkg_path)
    inp = _load_json(Path(args.input).resolve())
    items = inp if isinstance(inp, list) else list(inp.get("asset_requirements") or [inp])
    kickoff = load_kickoff(project) or {}
    pack = resolve_product_pack(kickoff["product"]) if isinstance(kickoff.get("product"), dict) else None
    pack_rel_paths = []
    if pack:
        for r in pack.get("references", []):
            p = Path(r["path"]).resolve()
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                try:
                    pack_rel_paths.append(p.relative_to(REPO).as_posix())
                except ValueError:
                    pack_rel_paths.append(str(p))
    all_pids = [p["paragraph_id"] for p in pkg.get("story_paragraphs") or []] or ["STP-01"]
    built = list(pkg.get("asset_requirements") or [])
    for a in items:
        aid = str(a["asset_id"])
        raw_cat = str(a["category"]).upper()
        cat = "LOCATION" if raw_cat in {"ENVIRONMENT", "SCENE"} else raw_cat
        entry: dict[str, Any] = {
            "asset_id": aid,
            "category": cat,
            "name": str(a["name"]),
            "narrative_purpose": str(a.get("narrative_purpose") or f"{a['name']} 在剧情中的核心视觉锚点"),
            "source_paragraph_ids": list(a.get("source_paragraph_ids") or all_pids[:1]),
            "identity_constraints": list(a.get("identity_constraints") or [f"{a['name']} 全片外观、材质与比例保持一致"]),
            "required_states": list(a.get("required_states") or ["常规标准状态"]),
            "interactions": list(a.get("interactions") or []),
            "reference_paths": list(a.get("reference_paths") or (pack_rel_paths[:4] if cat == "REAL_PRODUCT" else [])),
            "unknowns": list(a.get("unknowns") or []),
            "image_decision": str(a.get("image_decision") or "复用现有素材"),
            "image_reason": str(a.get("image_reason") or "使用已核对的母版或实物参考锁定本轮外观"),
        }
        if a.get("entity_id"):
            entry["entity_id"] = str(a["entity_id"])
        elif cat == "CHARACTER":
            entry["entity_id"] = f"CHR-{aid.replace('AST-CH-', '').replace('AST-', '')}"
        if cat == "CHARACTER":
            entry["character_type"] = str(a.get("character_type") or "HUMAN")
            if entry["character_type"] == "HUMAN":
                kind = str(a.get("character_output_kind") or ("人物1x4" if a.get("board_output_id") else "人物单图"))
                entry["character_output_kind"] = kind
                if a.get("wardrobe_states"):
                    entry["wardrobe_states"] = [
                        {
                            "name": str(w.get("name") or f"{a['name']}造型"),
                            "evidence": str(w.get("evidence") or f"{aid} 母版造型"),
                            **({"output_id": str(w["output_id"])} if w.get("output_id") else {}),
                        }
                        if isinstance(w, dict) else {"name": str(w), "evidence": f"{aid} 母版造型"}
                        for w in a["wardrobe_states"]
                    ]
                else:
                    ws: dict[str, Any] = {
                        "name": str(a.get("wardrobe_name") or f"{a['name']}默认造型"),
                        "evidence": str(a.get("wardrobe_evidence") or f"{aid} 母版造型"),
                    }
                    if kind == "人物1x4":
                        ws["output_id"] = str(a.get("board_output_id") or f"{aid.replace('AST-', '')}_1x4")
                    entry["wardrobe_states"] = [ws]
        elif cat == "LOCATION":
            entry["spatial_scope"] = str(a.get("spatial_scope") or "SINGLE_CONTIGUOUS_SPACE")
        idx = next((i for i, x in enumerate(built) if x.get("asset_id") == aid), None)
        if idx is None:
            built.append(entry)
        else:
            built[idx] = entry
    pkg["asset_requirements"] = built
    _save_json(pkg_path, pkg)
    print(json.dumps({"asset_count": len(built), "asset_ids": [x["asset_id"] for x in built]}, ensure_ascii=False))
    return 0


def cmd_submit_chat_review(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    pkg_rel = args.package
    pkg_path = project / pkg_rel
    pkg = _load_json(pkg_path)
    rev = str(pkg.get("revision") or "R01")
    ev_cmd = [
        sys.executable, str(REPO / "scripts/visual_stage_cli.py"),
        "init-dialogue-evidence", "--project", str(project), "--narrative", pkg_rel,
    ]
    subprocess.run(ev_cmd, cwd=REPO, env={**dict(__import__("os").environ), "PYTHONPATH": str(REPO / "scripts")}, check=True)
    pkg = _load_json(pkg_path)
    require_schema(pkg, "narrative-package.schema.json", label="narrative.package")
    md_rel = args.reading_output or f"02_编导/阅读稿/{rev}_编导阅读稿.md"
    art_id = args.artifact_id or f"NAR-{rev}"
    active = rc.show_change(project).get("active_change")
    chg_id = getattr(args, "change_id", None) or (
        active["change_id"] if isinstance(active, dict) and any(t.get("logical_id") == "narrative.package" for t in (active.get("targets") or [])) else None
    )
    prep_cmd = [
        sys.executable, str(REPO / "scripts/review_text_cli.py"),
        "prepare", "--project", str(project),
        "--narrative", pkg_rel, "--output", md_rel, "--chat-review",
    ]
    if not chg_id:
        prep_cmd.extend(["--artifact-id", art_id])
    res = subprocess.run(prep_cmd, cwd=REPO, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"review_text_cli prepare 失败：{res.stderr.strip() or res.stdout.strip()}")
    if chg_id:
        active = rc.show_change(project).get("active_change") or {}
        if active.get("status") == "awaiting_impact_confirmation":
            rc.confirm_impact(project, change_id=chg_id, approval_ref=getattr(args, "impact_ref", None) or f"chat://revision-impact/{chg_id}")
            active = rc.show_change(project).get("active_change") or {}
        if active.get("status") == "awaiting_candidate":
            rc.record_candidate(project, change_id=chg_id, artifact_id=art_id, logical_id="narrative.package",
                                artifact_type="narrative.package", candidate_path=pkg_rel, revision=rev, depends_on=[])
    sha = _sha256(pkg_path)
    chg_flag = f" --change-id '{chg_id}'" if chg_id else ""
    print(json.dumps({
        "submitted": art_id,
        "change_id": chg_id,
        "narrative_path": pkg_rel,
        "reading_md": md_rel,
        "sha256": sha,
        "confirm_command": (
            f".venv/bin/python scripts/stage_context_cli.py confirm-script-in-chat "
            f"--project '{project}' --artifact-id '{art_id}' --path '{pkg_rel}' "
            f"--revision '{rev}' --sha256 '{sha}'{chg_flag} --confirmation-json -"
        ),
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("init", help="初始化或从旧版克隆 v2.0 叙事包骨架")
    p1.add_argument("--project", required=True)
    p1.add_argument("--revision", default="R01")
    p1.add_argument("--from-package", help="可选：从旧版叙事包克隆未变段落、对白与资产需求")
    p1.add_argument("--change-summary", action="append", help="修订摘要条目，可重复")
    p1.add_argument("--objective")
    p1.add_argument("--header")
    p1.add_argument("--output")
    p1.set_defaults(func=cmd_init)

    p2 = sub.add_parser("put-paragraph", help="追加或更新单场/多场故事段落与对白（自动检查对白标记）")
    p2.add_argument("--project", required=True)
    p2.add_argument("--package", required=True)
    p2.add_argument("--input", required=True)
    p2.set_defaults(func=cmd_put_paragraph)

    p3 = sub.add_parser("put-assets", help="批量写入资产需求（自动补齐人类角色/空间/真实商品默认必填项）")
    p3.add_argument("--project", required=True)
    p3.add_argument("--package", required=True)
    p3.add_argument("--input", required=True)
    p3.set_defaults(func=cmd_put_assets)

    p4 = sub.add_parser("submit-chat-review", help="一键生成对白证据、校验合同、导出阅读稿并登记对话待审候选（支持返修变更案）")
    p4.add_argument("--project", required=True)
    p4.add_argument("--package", required=True)
    p4.add_argument("--artifact-id")
    p4.add_argument("--reading-output")
    p4.add_argument("--change-id", help="可选：绑定活动变更案编号（省略时自动检测）")
    p4.add_argument("--impact-ref", help="可选：影响确认凭据引用")
    p4.set_defaults(func=cmd_submit_chat_review)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
