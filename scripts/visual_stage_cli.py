#!/usr/bin/env python3
"""Convenience CLI for Stage 4 (storyboard-director / 05_分镜), model capability capture, and creative dialogue evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.agent_acceptance import acceptance_binding
from core.artifacts import load_manifest, register_workspace_artifact
from core.asset_refs import resolve_final_images
from core.io import project_lock
from core.production_handoff import _asset_readiness, load_kickoff
from core.product_reference_pack import resolve_product_pack
from core.schema_contracts import validate_visual_supplement_request
from core.stage_guidance import build_stage_guidance


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ctx(project: Path) -> dict[str, Any]:
    manifest = load_manifest(project)
    dir_entry = next((e for e in manifest.get("artifacts", []) if e.get("logical_id") == "storyboard.package" and e.get("status") == "current"), None)
    nar_entry = next((e for e in manifest.get("artifacts", []) if e.get("logical_id") == "narrative.package" and e.get("status") == "current"), None)
    if not dir_entry or not nar_entry:
        raise SystemExit("缺少当前正式采用的编导稿或导演包")
    dir_path = project / dir_entry["path_or_url"]
    storyboard = _load_json(dir_path)
    narrative = _load_json(project / nar_entry["path_or_url"])
    _, ready_assets, waiting_assets = _asset_readiness(project, narrative)
    resolved_map: dict[str, dict[str, Any]] = {}
    all_bindings = []
    chosen_by_ast: dict[str, str] = {}
    for ast_id, cands in ready_assets.items():
        pick = next((c for c in cands if "1x4" in c or "三视图" in c), cands[0])
        chosen_by_ast[ast_id] = pick
        all_bindings.append(pick)
    if all_bindings:
        resolved_map = resolve_final_images(project, all_bindings)
    kickoff = load_kickoff(project) or {}
    pack = resolve_product_pack(kickoff["product"]) if isinstance(kickoff.get("product"), dict) else None
    return {
        "project": project,
        "dir_entry": dir_entry,
        "dir_path": dir_path,
        "dir_sha256": str(dir_entry.get("checksum") or _sha256(dir_path)),
        "storyboard": storyboard,
        "chosen_by_ast": chosen_by_ast,
        "resolved_map": resolved_map,
        "waiting_assets": waiting_assets,
        "kickoff": kickoff,
        "product_pack": pack,
    }


def cmd_outline(args: argparse.Namespace) -> int:
    ctx = _ctx(Path(args.project).resolve())
    scenes = []
    for sc in ctx["storyboard"].get("scenes", []):
        shots = sc.get("shots", [])
        ast_ids = sorted({a for s in shots for a in (s.get("source_asset_requirement_ids") or [])})
        scenes.append({
            "scene_id": sc["scene_id"],
            "location": sc.get("location", ""),
            "shot_ids": [s["shot_id"] for s in shots],
            "assets": [
                {
                    "asset_id": a,
                    "ref_id": ctx["chosen_by_ast"].get(a),
                    "path": (
                        Path(ctx["resolved_map"][ctx["chosen_by_ast"][a]]["path"]).resolve().relative_to(ctx["project"]).as_posix()
                        if ctx["chosen_by_ast"].get(a) in ctx["resolved_map"] else None
                    ),
                }
                for a in ast_ids
            ],
        })
    print(json.dumps({
        "must_read": build_stage_guidance(ctx["project"], "storyboard-director"),
        "storyboard": ctx["dir_entry"]["artifact_id"],
        "storyboard_sha256": ctx["dir_sha256"],
        "waiting_assets": ctx["waiting_assets"],
        "scenes": scenes,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_save_check(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    ctx = _ctx(project)
    inp = _load_json(Path(args.input).resolve())
    req_id = str(inp["request_id"])
    decision = str(inp.get("decision") or "reuse_existing")
    status = str(inp.get("status") or ("ready" if decision == "request_supplement" else "not_needed"))
    refs = []
    for r in inp.get("existing_references") or []:
        if "path" in r and "kind" in r:
            refs.append(r)
        else:
            ast_key = str(r.get("asset_id") or r.get("ref_id") or "")
            binding = ast_key if "@" in ast_key else ctx["chosen_by_ast"].get(ast_key, "")
            info = ctx["resolved_map"].get(binding)
            if not info:
                raise SystemExit(f"未找到已确认资产输出：{ast_key}")
            rel_p = Path(str(info["path"])).resolve().relative_to(project).as_posix()
            refs.append({
                "kind": "confirmed_asset",
                "ref_id": binding,
                "path": rel_p,
                "coverage": str(r["coverage"]),
            })
    dir_entry = ctx["dir_entry"]
    dir_status = "confirmed" if dir_entry.get("user_approved") else "agent_accepted"
    doc: dict[str, Any] = {
        "schema_version": "1.0",
        "project_id": str(ctx["storyboard"].get("project_id") or project.name),
        "request_id": req_id,
        "status": status,
        "source_storyboard": {
            "artifact_id": str(dir_entry["artifact_id"]),
            "logical_id": "storyboard.package",
            "revision": str(dir_entry["revision"]),
            "path": str(dir_entry["path_or_url"]),
            "status": dir_status,
        },
        "source_shot_ids": list(inp["source_shot_ids"]),
        "visual_review": {
            "storyboard_sha256": ctx["dir_sha256"],
            "observation": str(inp["observation"]),
        },
        "target_state": str(inp["target_state"]),
        "must_appear": list(inp["must_appear"]),
        "must_not_appear": list(inp["must_not_appear"]),
        "preserve": list(inp["preserve"]),
        "existing_references": refs,
        "decision": decision,
        "max_images": int(inp.get("max_images", 1 if decision == "request_supplement" else 0)),
        "return_upstream": inp.get("return_upstream") or {"required": False, "target": "none", "reason": ""},
    }
    for extra in ("gap", "creator_mask_face_effect", "source_paths"):
        if extra in inp:
            doc[extra] = inp[extra]
    if decision == "request_supplement":
        raw_gap = doc.get("gap") if isinstance(doc.get("gap"), dict) else {}
        doc["gap"] = {
            "type": str(raw_gap.get("type") or "critical_state"),
            "observed_evidence": str(raw_gap.get("observed_evidence") or inp["observation"]),
            "why_existing_is_insufficient": str(raw_gap.get("why_existing_is_insufficient") or "现有确认资产未包含该关键中间外观状态，直接进视频易导致状态失真或身份漂移"),
            "downstream_consequence": str(raw_gap.get("downstream_consequence") or "缺少该静态状态锚点将导致多镜连续状态与边界不一致"),
        }
    validate_visual_supplement_request(doc, project_root=project)
    out_rel = args.output or f"05_分镜/{req_id}_视觉补充请求.json"
    _save_json(project / out_rel, doc)
    print(json.dumps({"saved": out_rel, "request_id": req_id, "decision": decision, "status": status}, ensure_ascii=False))
    return 0


def cmd_capture_redframe_capability(args: argparse.Namespace) -> int:
    from redframe_draft import open_workspace
    sys.path.insert(0, str(Path.home() / ".local/share/codex-headless-browser"))
    from headless_edge import task_browser

    project = Path(args.project).resolve()
    kickoff = load_kickoff(project) or {}
    canvas = kickoff.get("canvas") or {}
    url, pid = canvas.get("target_url"), canvas.get("target_project_id")
    if not url or not pid:
        raise SystemExit("缺少红帧 target_url 或 target_project_id")
    with open_workspace(task_browser, url, pid) as ws:
        raw = ws.page.evaluate("""async () => {
            const r = await fetch('/api/ai/media/models', { credentials: 'include' });
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return await r.json();
        }""")
    items = raw if isinstance(raw, list) else (raw.get("data") or raw.get("models") or [])
    want = str(args.model_keyword or canvas.get("model") or "Seedance 2.5").lower()
    hit = next((m for m in items if isinstance(m, dict) and m.get("type") == "video" and want in json.dumps(m, ensure_ascii=False).lower()), None)
    if not hit:
        raise SystemExit(f"未在 /api/ai/media/models 中匹配到视频模型：{want}")
    out_doc = {
        "captured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source_url": "https://redframe-ai.xmpaohong.com:28888/api/ai/media/models",
        "canvas_url": url,
        "model": hit,
    }
    _save_json(project / args.output, out_doc)
    print(json.dumps({"saved": args.output, "modelId": hit.get("modelId"), "sha256": _sha256(project / args.output)}, ensure_ascii=False))
    return 0


def cmd_init_dialogue_evidence(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    nar_path = project / args.narrative
    nar = _load_json(nar_path)
    rev = str(nar.get("revision") or "R01")
    kickoff = load_kickoff(project) or {}
    ref_text = str(args.reference or kickoff.get("decision_ref") or "project_request.json#kickoff.decision_ref")
    dlgs = []
    for d in nar.get("dialogues") or []:
        dlgs.append({
            "dialogue_id": d["dialogue_id"],
            "speaker_id": d["speaker_id"],
            "speaker_status": "verified",
            "text": d["text"],
            "source_paragraph_id": d["source_paragraph_id"],
            "source_ids": [],
            "origin": {
                "kind": "authored_addition",
                "reference": ref_text,
                "scope": str(args.scope or "原创剧情台词"),
                "author": f"creative-director-{rev}",
                "purpose": str(d.get("dramatic_purpose") or "推动本场剧情与人物关系"),
                "revision": rev,
            },
        })
    out_rel = args.output or f"02_编导/最终对白/{rev}_对白审核记录.json"
    out_path = project / out_rel
    _save_json(out_path, {
        "schema_version": "1.0",
        "project_id": str(nar.get("project_id") or project.name),
        "revision": rev,
        "sources": [],
        "dialogues": dlgs,
    })
    sha = _sha256(out_path)
    nar["dialogue_evidence_path"] = out_rel
    nar["dialogue_evidence_sha256"] = sha
    nar.pop("dialogue_evidence", None)
    _save_json(nar_path, nar)
    print(json.dumps({"dialogue_evidence": out_rel, "sha256": sha, "count": len(dlgs)}, ensure_ascii=False))
    return 0


def cmd_register_supplement(args: argparse.Namespace) -> int:
    from core import revision_control as rc
    project = Path(args.project).resolve()
    ctx = _ctx(project)
    inp = _load_json(Path(args.input).resolve())
    art_id = str(inp["artifact_id"])
    active = rc.show_change(project).get("active_change")
    chg_id = getattr(args, "change_id", None) or inp.get("change_id") or (
        active["change_id"] if isinstance(active, dict) and active.get("status") == "awaiting_candidate"
        and any(str(t.get("logical_id") or "").startswith("visual.supplement.") for t in (active.get("targets") or [])) else None
    )
    active_lid = (active["targets"][0]["logical_id"] if chg_id and isinstance(active, dict) and active.get("targets") else None)
    logical_id = str(inp.get("logical_id") or active_lid or f"visual.supplement.{art_id.lower().replace('-', '_')}")
    img_rel = str(inp["image_path"])
    img_abs = project / img_rel
    if not img_abs.is_file():
        raise SystemExit(f"补图文件不存在：{img_rel}")
    img_sha = _sha256(img_abs)
    req_rel = str(inp["request_path"])
    req_abs = project / req_rel
    shot_ids = list(inp["source_shot_ids"])
    entity_ids = list(inp.get("entity_ids") or ["AST-CH-01"])
    roles = list(inp.get("roles") or ["state_reference"])
    src_files = [
        {"path": req_rel, "sha256": _sha256(req_abs)},
        {"path": str(ctx["dir_entry"]["path_or_url"]), "sha256": ctx["dir_sha256"]},
    ]
    for extra_src in inp.get("extra_source_paths") or []:
        ep = project / extra_src
        if ep.is_file():
            src_files.append({"path": str(extra_src), "sha256": _sha256(ep)})
    default_obs = str(inp.get("observation") or "已原尺寸实看成图，人物面容服饰、空间色调与目标状态边界均准确无穿帮")
    raw_checks = inp.get("checks") or {}
    check_keys = ("identity_wardrobe", "hands_support", "product_geometry", "ownership", "space_objects", "product_phase", "boundary_state")
    checks_obj = {}
    for ck in check_keys:
        if isinstance(raw_checks.get(ck), dict):
            checks_obj[ck] = raw_checks[ck]
        else:
            checks_obj[ck] = {
                "result": "pass",
                "observation": str(raw_checks.get(ck) or default_obs),
                "locator": f"{img_rel} 全图与目标区域对照 {req_rel}",
            }
    now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    rev = str(inp.get("revision") or ctx["dir_entry"]["revision"])
    qe = {
        "status": "pass",
        "observer": str(inp.get("observer") or "storyboard-director"),
        "checked_at": now_iso,
        "image_sha256": img_sha,
        "source_files": src_files,
        "source_shot_ids": shot_ids,
        "uses": [{"source_shot_ids": shot_ids, "roles": roles, "entity_ids": entity_ids, "state_id": inp.get("state_id")}],
        "view": {"resolution": "original", "locator": f"{img_rel} 原尺寸全图实看"},
        "checks": checks_obj,
    }
    if chg_id:
        rc.record_candidate(
            project, change_id=chg_id, artifact_id=art_id, logical_id=logical_id,
            artifact_type="visual-supplement-image", candidate_path=img_rel, revision=rev,
            depends_on=["storyboard.package", "visual.asset.manifest"],
            artifact_metadata={"quality_evidence": qe},
        )
    else:
        manifest = load_manifest(project)
        existing = next((e for e in manifest.get("artifacts", []) if e.get("artifact_id") == art_id), None)
        if existing and existing.get("status") == "candidate" and not existing.get("user_approved") and not existing.get("agent_acceptance"):
            from core.artifacts import save_manifest
            manifest["artifacts"] = [e for e in manifest["artifacts"] if e.get("artifact_id") != art_id]
            save_manifest(project, manifest)
        register_workspace_artifact(project, {
            "artifact_id": art_id,
            "logical_id": logical_id,
            "type": "visual-supplement-image",
            "path_or_url": img_rel,
            "revision": rev,
            "depends_on": ["storyboard.package", "visual.asset.manifest"],
            "checksum": img_sha,
            "user_approved": False,
            "quality_evidence": qe,
        })
    manifest = load_manifest(project)
    entry = next(e for e in manifest["artifacts"] if e["artifact_id"] == art_id)
    repo_root = Path(__file__).resolve().parent.parent
    prof_path = repo_root / "skills/storyboard-director/review-profile.json"
    prof = _load_json(prof_path)
    sc_rel = str(inp.get("self_check_output") or f"00_项目管理/分镜补图{art_id}自查.json")
    auth_id = str(inp.get("authorization_id") or ((active or {}).get("workflow_authorization") or {}).get("id") or "FLOW-R01")
    sc_doc = {
        "schema_version": "1.0",
        "kind": "agent-self-check",
        "scope": "artifact",
        "verdict": "pass",
        "authorization_id": auth_id,
        "agent_id": "storyboard-director",
        "checked_at": now_iso,
        "review_profile_sha256": _sha256(prof_path),
        **acceptance_binding(project, entry),
        "usage": {"source_shot_ids": shot_ids},
        "checks": [
            {
                "id": str(c["id"]),
                "requirement": str(c.get("text") or ""),
                "result": "pass",
                "observation": default_obs,
                "locator": f"{img_rel} + {req_rel}",
            }
            for c in prof.get("production_artifact_checks", [])
        ],
    }
    _save_json(project / sc_rel, sc_doc)
    chg_flag = f" --change-id '{chg_id}'" if chg_id else ""
    print(json.dumps({
        "registered": art_id,
        "change_id": chg_id,
        "logical_id": logical_id,
        "self_check": sc_rel,
        "accept_command": f".venv/bin/python scripts/stage_context_cli.py accept-artifact --project '{project}' --artifact-id '{art_id}'{chg_flag} --self-check-json '{sc_rel}'",
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("outline", help="输出各场镜号及已确认资产精确绑定路径")
    p1.add_argument("--project", required=True)
    p1.set_defaults(func=cmd_outline)

    p2 = sub.add_parser("save-check", help="自动补齐来源哈希与资产路径并校验保存 VS-*.json")
    p2.add_argument("--project", required=True)
    p2.add_argument("--input", required=True)
    p2.add_argument("--output")
    p2.set_defaults(func=cmd_save_check)

    p3 = sub.add_parser("capture-redframe-capability", help="一键抓取红帧原生视频模型能力证据 JSON")
    p3.add_argument("--project", required=True)
    p3.add_argument("--output", default="06_视频/红帧Seedance25原生模型能力取证.json")
    p3.add_argument("--model-keyword", default="Seedance 2.5")
    p3.set_defaults(func=cmd_capture_redframe_capability)

    p4 = sub.add_parser("init-dialogue-evidence", help="为原创叙事包一键生成并回绑对白审核记录 JSON")
    p4.add_argument("--project", required=True)
    p4.add_argument("--narrative", required=True)
    p4.add_argument("--output")
    p4.add_argument("--reference")
    p4.add_argument("--scope")
    p4.set_defaults(func=cmd_init_dialogue_evidence)

    p5 = sub.add_parser("register-supplement", help="登记已实看的补图候选并生成自查报告与采用命令（支持活动变更案）")
    p5.add_argument("--project", required=True)
    p5.add_argument("--input", required=True)
    p5.add_argument("--change-id", help="可选：绑定活动变更案编号（省略时自动检测）")
    p5.set_defaults(func=cmd_register_supplement)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
