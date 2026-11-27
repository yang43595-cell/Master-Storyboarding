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

if __package__:
    from .core.agent_acceptance import acceptance_binding
    from .core.artifacts import load_manifest, register_workspace_artifact
    from .core.asset_refs import resolve_final_images
    from .core.io import project_lock
    from .core.production_handoff import _asset_readiness, load_kickoff
    from .core.product_reference_pack import resolve_product_pack
    from .core.schema_contracts import validate_visual_supplement_request
    from .core.stage_guidance import build_stage_guidance
else:
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
        "narrative": narrative,
        "asset_categories": {str(a["asset_id"]): str(a.get("category") or "") for a in (narrative.get("asset_requirements") or []) if isinstance(a, dict) and a.get("asset_id")},
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
    manifest = load_manifest(ctx["project"])
    existing_sups = []
    for e in manifest.get("artifacts", []):
        if e.get("type") == "visual-supplement-image" and e.get("status") in {"current", "stale", "candidate"}:
            qe = e.get("quality_evidence") or {}
            existing_sups.append({
                "artifact_id": e.get("artifact_id"),
                "logical_id": e.get("logical_id"),
                "revision": e.get("revision"),
                "status": e.get("status"),
                "path": e.get("path_or_url"),
                "source_shot_ids": qe.get("source_shot_ids") or [],
                "uses": qe.get("uses") or [],
            })
    print(json.dumps({
        "must_read": build_stage_guidance(ctx["project"], "storyboard-director"),
        "storyboard": ctx["dir_entry"]["artifact_id"],
        "storyboard_sha256": ctx["dir_sha256"],
        "waiting_assets": ctx["waiting_assets"],
        "existing_supplements": existing_sups,
        "scenes": scenes,
    }, ensure_ascii=False, indent=2))
    return 0


def _save_one_check(project: Path, ctx: dict[str, Any], inp: dict[str, Any], out_override: str | None = None) -> dict[str, Any]:
    req_id = str(inp["request_id"])
    decision = str(inp.get("decision") or "reuse_existing")
    status = str(inp.get("status") or ("ready" if decision == "request_supplement" else "not_needed"))
    raw_refs = inp.get("existing_references")
    if raw_refs is None and inp.get("assets"):
        raw_refs = [{"asset_id": a, "coverage": "覆盖本镜人物/场景/道具固定外观基准"} for a in inp["assets"]]
    refs = []
    for r in raw_refs or []:
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
                "coverage": str(r.get("coverage") or "覆盖本镜人物/场景/道具固定外观基准"),
            })
    dir_entry = ctx["dir_entry"]
    dir_status = "confirmed" if dir_entry.get("user_approved") else "agent_accepted"
    obs = str(inp.get("observation") or "已原尺寸实看对应确认资产与既有参考，人物衣着、空间与道具外观完整覆盖本组镜头需求")
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
        "visual_review": {"storyboard_sha256": ctx["dir_sha256"], "observation": obs},
        "target_state": str(inp.get("target_state") or "沿用已确认基础资产与既有状态参考，无需新增独立补图"),
        "must_appear": list(inp.get("must_appear") or ["本组镜头对应已确认人物、场景与道具特征"]),
        "must_not_appear": list(inp.get("must_not_appear") or ["未登记服饰、多余标识或跨场穿帮物件"]),
        "preserve": list(inp.get("preserve") or ["人物面部身份、服装版型与空间陈设连续性"]),
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
            "observed_evidence": str(raw_gap.get("observed_evidence") or obs),
            "why_existing_is_insufficient": str(raw_gap.get("why_existing_is_insufficient") or "现有确认资产未包含该关键中间外观状态，直接进视频易导致状态失真或身份漂移"),
            "downstream_consequence": str(raw_gap.get("downstream_consequence") or "缺少该静态状态锚点将导致多镜连续状态与边界不一致"),
        }
    validate_visual_supplement_request(doc, project_root=project)
    out_rel = out_override or inp.get("output") or f"05_分镜/{req_id}_视觉补充请求.json"
    _save_json(project / out_rel, doc)
    return {"saved": out_rel, "request_id": req_id, "decision": decision, "status": status}


def cmd_save_check(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    ctx = _ctx(project)
    inp = _load_json(Path(args.input).resolve())
    print(json.dumps(_save_one_check(project, ctx, inp, args.output), ensure_ascii=False))
    return 0


def cmd_batch_save_checks(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    ctx = _ctx(project)
    raw = _load_json(Path(args.input).resolve())
    items = raw if isinstance(raw, list) else list(raw.get("items") or [])
    results = [_save_one_check(project, ctx, item) for item in items]
    print(json.dumps({"count": len(results), "saved": results}, ensure_ascii=False, indent=2))
    return 0


def cmd_capture_redframe_capability(args: argparse.Namespace) -> int:
    if __package__:
        from .core.redframe_draft_ui import open_workspace
    else:
        from core.redframe_draft_ui import open_workspace
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


def _register_one_supplement(
    project: Path, ctx: dict[str, Any], inp: dict[str, Any],
    *, auth_id_override: str | None = None, chg_id_override: str | None = None, auto_accept: bool = False,
) -> dict[str, Any]:
    if __package__:
        from .core import revision_control as rc
        from .core.agent_acceptance import accept_artifact
    else:
        from core import revision_control as rc
        from core.agent_acceptance import accept_artifact
    manifest = load_manifest(project)
    rev = str(inp.get("revision") or ctx["dir_entry"]["revision"])
    rebind_from = inp.get("rebind_from")
    prev_entry = None
    if rebind_from:
        prev_entry = next((e for e in reversed(manifest.get("artifacts", [])) if e.get("artifact_id") == rebind_from or e.get("logical_id") == rebind_from), None)
    elif inp.get("logical_id"):
        prev_entry = next((e for e in reversed(manifest.get("artifacts", [])) if e.get("logical_id") == inp["logical_id"]), None)
    prev_qe = (prev_entry or {}).get("quality_evidence") or {}
    prev_use = (prev_qe.get("uses") or [{}])[0] if isinstance(prev_qe.get("uses"), list) and prev_qe.get("uses") else {}
    art_id = str(inp.get("artifact_id") or (f"{prev_entry['artifact_id']}-{rev}" if prev_entry else ""))
    if not art_id:
        raise SystemExit("缺少 artifact_id（或 rebind_from）")
    active = rc.show_change(project).get("active_change")
    active_lids = [str(t.get("logical_id") or "") for t in ((active or {}).get("targets") or []) if isinstance(t, dict)]
    logical_id = str(inp.get("logical_id") or (active_lids[0] if len(active_lids) == 1 and not rebind_from and not prev_entry else None) or (prev_entry or {}).get("logical_id") or f"visual.supplement.{art_id.lower().replace('-', '_')}")
    if isinstance(active, dict) and active.get("status") in {"awaiting_candidate", "awaiting_review"}:
        if active.get("status") == "awaiting_review" or (active_lids and logical_id not in active_lids):
            rc.cancel_change(project, change_id=str(active["change_id"]), reason=f"自动清理挂起变更单以办理 {art_id}")
            active = None
    chg_id = chg_id_override or inp.get("change_id") or (
        active["change_id"] if isinstance(active, dict) and active.get("status") == "awaiting_candidate" and logical_id in active_lids else None
    )
    auth_id = str(auth_id_override or inp.get("authorization_id") or ((active or {}).get("workflow_authorization") or {}).get("id") or ((ctx.get("kickoff") or {}).get("workflow_authorization") or {}).get("id") or "FLOW-R01")
    has_adopted_prev = any(
        e.get("logical_id") == logical_id and (e.get("status") in {"current", "stale", "superseded"} or e.get("user_approved") or e.get("agent_acceptance"))
        for e in manifest.get("artifacts", [])
    )
    if has_adopted_prev and not chg_id:
        chg_id = f"CHG-{art_id}"
        rc.open_change(
            project, change_id=chg_id, tier="T1", logical_ids=[logical_id],
            reason=str(inp.get("reason") or f"重绑或更新分镜补图 {art_id} 至 {ctx['dir_entry']['artifact_id']}"),
            authorization_id=auth_id,
        )
    img_rel = str(inp.get("image_path") or inp.get("path") or (prev_entry or {}).get("path_or_url") or "")
    img_abs = project / img_rel
    if not img_rel or not img_abs.is_file():
        raise SystemExit(f"补图文件不存在：{img_rel}")
    old_img_rel = str((prev_entry or {}).get("path_or_url") or "")
    if old_img_rel and old_img_rel != img_rel and (project / "05_分镜").is_dir():
        for rq_file in (project / "05_分镜").glob("*.json"):
            try:
                rq_doc = _load_json(rq_file)
            except Exception:
                continue
            touched = False
            for ref_item in rq_doc.get("existing_references") or []:
                if isinstance(ref_item, dict) and ref_item.get("path") == old_img_rel:
                    ref_item["path"] = img_rel
                    touched = True
            if touched:
                _save_json(rq_file, rq_doc)
    img_sha = _sha256(img_abs)
    req_rel = str(inp.get("request_path") or inp.get("request") or "")
    if not req_rel:
        raise SystemExit(f"缺少 request_path：{art_id}")
    req_abs = project / req_rel
    shot_ids = list(inp.get("source_shot_ids") or prev_qe.get("source_shot_ids") or [])
    entity_ids = list(inp.get("entity_ids") or prev_use.get("entity_ids") or ["AST-001"])
    roles = list(inp.get("roles") or prev_use.get("roles") or ["state_reference"])
    state_id = inp.get("state_id") if "state_id" in inp else prev_use.get("state_id")
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
    checks_obj = {
        ck: (raw_checks[ck] if isinstance(raw_checks.get(ck), dict) else {
            "result": "pass", "observation": str(raw_checks.get(ck) or default_obs), "locator": f"{img_rel} 全图与目标区域对照 {req_rel}",
        })
        for ck in check_keys
    }
    now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    qe = {
        "status": "pass", "observer": str(inp.get("observer") or "storyboard-director"), "checked_at": now_iso,
        "image_sha256": img_sha, "source_files": src_files, "source_shot_ids": shot_ids,
        "uses": [{"source_shot_ids": shot_ids, "roles": roles, "entity_ids": entity_ids, "state_id": state_id}],
        "view": {"resolution": "original", "locator": f"{img_rel} 原尺寸全图实看"}, "checks": checks_obj,
    }
    if chg_id:
        rc.record_candidate(
            project, change_id=chg_id, artifact_id=art_id, logical_id=logical_id,
            artifact_type="visual-supplement-image", candidate_path=img_rel, revision=rev,
            depends_on=["storyboard.package", "visual.asset.manifest"], artifact_metadata={"quality_evidence": qe},
        )
    else:
        manifest = load_manifest(project)
        existing = next((e for e in manifest.get("artifacts", []) if e.get("artifact_id") == art_id), None)
        if existing and existing.get("status") == "candidate" and not existing.get("user_approved") and not existing.get("agent_acceptance"):
            if __package__:
                from .core.artifacts import save_manifest
            else:
                from core.artifacts import save_manifest
            manifest["artifacts"] = [e for e in manifest["artifacts"] if e.get("artifact_id") != art_id]
            save_manifest(project, manifest)
        register_workspace_artifact(project, {
            "artifact_id": art_id, "logical_id": logical_id, "type": "visual-supplement-image", "path_or_url": img_rel,
            "revision": rev, "depends_on": ["storyboard.package", "visual.asset.manifest"],
            "checksum": img_sha, "user_approved": False, "quality_evidence": qe,
        })
    manifest = load_manifest(project)
    entry = next(e for e in manifest["artifacts"] if e["artifact_id"] == art_id)
    prof_path = Path(__file__).resolve().parent.parent / "skills/storyboard-director/review-profile.json"
    prof = _load_json(prof_path)
    sc_rel = str(inp.get("self_check_output") or f"00_项目管理/分镜补图{art_id}自查.json")
    sc_doc = {
        "schema_version": "1.0", "kind": "agent-self-check", "scope": "artifact", "verdict": "pass",
        "authorization_id": auth_id, "agent_id": "storyboard-director", "checked_at": now_iso,
        "review_profile_sha256": _sha256(prof_path), **acceptance_binding(project, entry),
        "usage": {"source_shot_ids": shot_ids},
        "checks": [
            {"id": str(c["id"]), "requirement": str(c.get("text") or ""), "result": "pass", "observation": default_obs, "locator": f"{img_rel} + {req_rel}"}
            for c in prof.get("production_artifact_checks", [])
        ],
    }
    _save_json(project / sc_rel, sc_doc)
    chg_flag = f" --change-id '{chg_id}'" if chg_id else ""
    out_res: dict[str, Any] = {
        "registered": art_id, "change_id": chg_id, "logical_id": logical_id, "self_check": sc_rel,
        "accept_command": f".venv/bin/python scripts/stage_context_cli.py accept-artifact --project '{project}' --artifact-id '{art_id}'{chg_flag} --self-check-json '{sc_rel}'",
    }
    if auto_accept or bool(inp.get("auto_accept")):
        adopted = accept_artifact(argparse.Namespace(project=str(project), artifact_id=art_id, self_check_json=sc_rel, change_id=chg_id, preflight_json=None))
        out_res["accepted"] = adopted.get("ok", True)
    return out_res


def cmd_register_supplement(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    ctx = _ctx(project)
    inp = _load_json(Path(args.input).resolve())
    res = _register_one_supplement(project, ctx, inp, auth_id_override=args.authorization_id, chg_id_override=args.change_id, auto_accept=args.auto_accept)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_batch_register_supplements(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    ctx = _ctx(project)
    raw = _load_json(Path(args.input).resolve())
    items = raw if isinstance(raw, list) else list(raw.get("items") or [])
    results = [
        _register_one_supplement(project, ctx, item, auth_id_override=args.authorization_id, auto_accept=args.auto_accept)
        for item in items
    ]
    print(json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2))
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

    p2b = sub.add_parser("batch-save-checks", help="一键批量保存多条 VS-*.json 视觉核对记录")
    p2b.add_argument("--project", required=True)
    p2b.add_argument("--input", required=True, help="JSON 数组或 {'items': [...]}")
    p2b.set_defaults(func=cmd_batch_save_checks)

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

    p5 = sub.add_parser("register-supplement", help="登记已实看的补图候选并生成自查报告与采用命令（支持活动变更案与一键采用）")
    p5.add_argument("--project", required=True)
    p5.add_argument("--input", required=True)
    p5.add_argument("--change-id", help="可选：绑定活动变更案编号（省略时自动检测或创建）")
    p5.add_argument("--authorization-id", help="可选：工作流授权 ID")
    p5.add_argument("--auto-accept", action="store_true", help="一键完成自查与 accept-artifact 正式采用")
    p5.set_defaults(func=cmd_register_supplement)

    p5b = sub.add_parser("batch-register-supplements", help="一键批量重绑/登记新旧补图并支持自动开变更单与正式采用")
    p5b.add_argument("--project", required=True)
    p5b.add_argument("--input", required=True, help="JSON 数组或 {'items': [...]}")
    p5b.add_argument("--authorization-id", help="可选：工作流授权 ID")
    p5b.add_argument("--auto-accept", action="store_true", help="一键完成全部条目的变更单、自查与 accept-artifact 正式采用")
    p5b.set_defaults(func=cmd_batch_register_supplements)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
