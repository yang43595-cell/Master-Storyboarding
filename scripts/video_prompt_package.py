#!/usr/bin/env python3
"""Progressive builder and validator for creative video.prompt (v2.0) packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from copy import deepcopy
if __package__:
    from .core import revision_control as rc
    from .core.agent_acceptance import acceptance_binding
    from .core.artifacts import (
        load_manifest,
        project_locked,
        register_workspace_artifact,
        save_manifest,
        validate_candidate_submission,
    )
    from .core.asset_refs import resolve_final_images
    from .core.generation_boundaries import _CREATOR_BOWL_BRUSH_OFFER
    from .core.io import project_lock
    from .core.production_handoff import _asset_readiness, load_kickoff
    from .core.product_reference_pack import resolve_product_pack
    from .core.prompt_body import submitted_body_issues, submitted_prompt_body
    from .core.quality_evidence import director_shot_audio, source_shot_ids, validate_required_visuals, validate_shot_durations
    from .core.stage_guidance import build_stage_guidance
else:
    from core import revision_control as rc
    from core.agent_acceptance import acceptance_binding
    from core.artifacts import (
        load_manifest,
        project_locked,
        register_workspace_artifact,
        save_manifest,
        validate_candidate_submission,
    )
    from core.asset_refs import resolve_final_images
    from core.generation_boundaries import _CREATOR_BOWL_BRUSH_OFFER
    from core.io import project_lock
    from core.production_handoff import _asset_readiness, load_kickoff
    from core.product_reference_pack import resolve_product_pack
    from core.prompt_body import submitted_body_issues, submitted_prompt_body
    from core.quality_evidence import director_shot_audio, source_shot_ids, validate_required_visuals, validate_shot_durations
    from core.stage_guidance import build_stage_guidance

REPO = Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _shot_seconds(shot: dict[str, Any], default: float = 1.0) -> float:
    raw = shot.get("duration_seconds")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    m = re.search(r"(\d+(?:\.\d+)?)\s*秒", str(shot.get("rhythm") or ""))
    if m:
        return max(0.5, float(m.group(1)))
    return default


def _current_entry(manifest: dict[str, Any], logical_id: str) -> dict[str, Any] | None:
    hits = [e for e in manifest.get("artifacts", []) if e.get("logical_id") == logical_id and e.get("status") == "current"]
    return hits[0] if len(hits) == 1 else None


def _context(project: Path) -> dict[str, Any]:
    manifest = load_manifest(project)
    kickoff = load_kickoff(project)
    dir_entry = _current_entry(manifest, "storyboard.package")
    nar_entry = _current_entry(manifest, "narrative.package")
    if not dir_entry or not nar_entry:
        raise SystemExit("缺少已正式采用的编导稿或导演包")
    storyboard = _load_json(project / dir_entry["path_or_url"])
    narrative = _load_json(project / nar_entry["path_or_url"])
    _, ready_assets, _ = _asset_readiness(project, narrative)
    supplements: dict[str, dict[str, Any]] = {}
    for entry in manifest.get("artifacts", []):
        if entry.get("type") == "visual-supplement-image" and entry.get("status") == "current":
            aid = str(entry.get("artifact_id") or entry.get("id"))
            lid = str(entry.get("logical_id") or entry.get("id") or aid)
            supplements[aid] = entry
            supplements[lid] = entry
    product_pack = resolve_product_pack(kickoff["product"]) if isinstance(kickoff.get("product"), dict) else None
    real_product_asset_id = next(
        (str(a["asset_id"]) for a in narrative.get("asset_requirements", []) if isinstance(a, dict) and a.get("category") == "REAL_PRODUCT"),
        "AST-PR-01",
    )
    shots_ordered: list[dict[str, Any]] = []
    shot_scene: dict[str, dict[str, Any]] = {}
    for scene in storyboard.get("scenes", []):
        for shot in scene.get("shots", []):
            shots_ordered.append(shot)
            shot_scene[shot["shot_id"]] = scene
    return {
        "project": project,
        "manifest": manifest,
        "kickoff": kickoff,
        "dir_entry": dir_entry,
        "storyboard": storyboard,
        "narrative": narrative,
        "ready_assets": ready_assets,
        "supplements": supplements,
        "product_pack": product_pack,
        "real_product_asset_id": real_product_asset_id,
        "shots_ordered": shots_ordered,
        "shot_by_id": {s["shot_id"]: s for s in shots_ordered},
        "shot_scene": shot_scene,
    }


def _resolve_materials(ctx: dict[str, Any], specs: list[dict[str, Any]], source_shots: list[str]) -> tuple[list[dict[str, Any]], set[str]]:
    project: Path = ctx["project"]
    ready_assets = ctx["ready_assets"]
    supplements = ctx["supplements"]
    product_pack = ctx["product_pack"]
    img_idx = 0
    vid_idx = 0
    materials: list[dict[str, Any]] = []
    ext_roots: set[str] = set()
    role_defaults = {
        "action": "action", "technique": "action",
        "packaging": "appearance", "material": "appearance", "state": "state_reference",
    }
    for item in specs:
        ref_key = str(item.get("ref") or "").strip()
        shot_ids = list(item.get("source_shot_ids") or source_shots)
        if ref_key.startswith("PROD:"):
            prod_id = ref_key.split(":", 1)[1]
            if not product_pack:
                raise SystemExit(f"项目未绑定产品资料包，无法解析 {ref_key}")
            hit = next((f for f in product_pack.get("references", []) if f.get("id") == prod_id), None)
            if not hit:
                raise SystemExit(f"产品资料包中未找到 {prod_id}")
            abs_p = Path(hit["path"]).resolve()
            kind = "video" if abs_p.suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"} else "image"
            if kind == "video":
                vid_idx += 1
                ref_tag = f"@视频{vid_idx}"
            else:
                img_idx += 1
                ref_tag = f"@图片{img_idx}"
            role = str(item.get("role") or role_defaults.get(str(hit.get("role")), "appearance"))
            ext_roots.add(str(Path(product_pack["manifest_path"]).resolve().parent))
            mat_path = str(abs_p)
            if hit.get("role") in {"action", "technique"} and hit.get("action_key"):
                staged_rel = Path("04_资产/产品参考") / f"{prod_id}_{abs_p.name}"
                staged_abs = project / staged_rel
                if not staged_abs.is_file() or _sha256(staged_abs) != str(hit["sha256"]):
                    staged_abs.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(abs_p, staged_abs)
                mat_path = staged_rel.as_posix()
            mat_entry: dict[str, Any] = {
                "reference": ref_tag, "kind": kind, "role": role, "roles": [role],
                "asset_id": str(item.get("asset_id") or ctx["real_product_asset_id"]),
                "entity_ids": list(item.get("entity_ids") or [str(item.get("asset_id") or ctx["real_product_asset_id"])]),
                "product_reference_id": prod_id, "path": mat_path,
                "sha256": str(hit["sha256"]),
                "source_shot_ids": shot_ids,
            }
            if hit.get("role") in {"action", "technique"} and hit.get("action_key"):
                mat_entry["action_references"] = [
                    {"shot_id": s, "action_key": str(hit["action_key"])} for s in shot_ids
                ]
            materials.append(mat_entry)
        elif ref_key in supplements:
            sup = supplements[ref_key]
            img_idx += 1
            rel_p = str(sup.get("path_or_url") or sup.get("path"))
            sup_shots = sup.get("quality_evidence", {}).get("source_shot_ids") or sup.get("visual_supplement", {}).get("source_shot_ids") or sup.get("source_shot_ids") or shot_ids
            allowed = [s for s in shot_ids if s in set(sup_shots)] or shot_ids
            allowed_set = set(allowed)
            matching_uses = [
                u for u in (sup.get("quality_evidence") or {}).get("uses", [])
                if isinstance(u, dict) and set(u.get("source_shot_ids") or []) & allowed_set
            ]
            explicit_roles = item.get("roles")
            if isinstance(explicit_roles, list) and explicit_roles:
                roles = [str(r) for r in explicit_roles if str(r).strip()]
                role = str(item.get("role") or roles[0])
            elif item.get("role"):
                role = str(item["role"])
                roles = [role]
            else:
                use_roles = [str(r) for u in matching_uses for r in (u.get("roles") or []) if str(r).strip()]
                roles = list(dict.fromkeys(use_roles)) or ["state_reference"]
                role = roles[0]
            entity_ids = list(item.get("entity_ids") or sorted({
                str(e) for u in matching_uses for e in (u.get("entity_ids") or []) if str(e).strip()
            }))
            if not entity_ids and sup.get("asset_id"):
                entity_ids = [str(sup["asset_id"])]
            state_ids = [str(u["state_id"]) for u in matching_uses if u.get("state_id")]
            state_id = item.get("state_id") or (state_ids[0] if len(set(state_ids)) == 1 else None)
            sup_entry: dict[str, Any] = {
                "reference": f"@图片{img_idx}", "kind": "image", "role": role, "roles": roles,
                "artifact_id": str(sup.get("artifact_id") or sup.get("id")),
                "revision": str(sup["revision"]),
                "path": rel_p, "sha256": str(sup.get("checksum") or _sha256(project / rel_p)),
                "source_shot_ids": allowed,
                "requires_face_certification": bool(item.get("requires_face_certification", True)),
            }
            if entity_ids:
                sup_entry["entity_ids"] = entity_ids
            if state_id:
                sup_entry["state_id"] = str(state_id)
            materials.append(sup_entry)
        else:
            role = str(item.get("role") or "character")
            ast_key = ref_key.split("@", 1)[0]
            candidates = list(ready_assets.get(ast_key) or [])
            preferred = next((c for c in candidates if "1x4" in c or "三视图" in c), candidates[0] if candidates else "")
            binding = ref_key if "@" in ref_key else preferred
            if not binding:
                raise SystemExit(f"资产 {ref_key} 未找到已确认正式图绑定")
            resolved = resolve_final_images(project, [binding])[binding]
            img_idx += 1
            ast_part, out_part = binding.split("@", 1)
            default_face = bool(
                resolved.get("requires_face_certification")
                if "requires_face_certification" in resolved
                else (ast_part.startswith("AST-CH-") and "无头" not in Path(str(resolved["path"])).name)
            )
            base_entry: dict[str, Any] = {
                "reference": f"@图片{img_idx}", "kind": "image", "role": role, "roles": [role],
                "asset_id": ast_part, "output_id": out_part,
                "entity_ids": list(item.get("entity_ids") or [ast_part]),
                "path": str(resolved["path"]), "sha256": str(resolved["sha256"]),
                "source_shot_ids": shot_ids,
                "requires_face_certification": bool(item.get("requires_face_certification", default_face)),
            }
            if item.get("state_id"):
                base_entry["state_id"] = str(item["state_id"])
            materials.append(base_entry)
    return materials, ext_roots


def _refresh_scope_and_continuity(ctx: dict[str, Any], pkg: dict[str, Any]) -> None:
    segs = pkg.get("generation_segments", [])
    shot_by_id = ctx["shot_by_id"]
    shot_scene = ctx["shot_scene"]
    all_shot_ids = [s["shot_id"] for s in ctx["shots_ordered"]]
    covered: list[str] = []
    edit_cursor = 0.0
    for idx, seg in enumerate(segs):
        s_ids = list(seg.get("source_shots") or [])
        covered.extend(s_ids)
        dur = float(seg.get("target_edit_duration_seconds") or seg.get("duration_seconds") or 0)
        seg["edit_in_seconds"] = round(edit_cursor, 2)
        edit_cursor += dur
        seg["edit_out_seconds"] = round(edit_cursor, 2)
        if idx > 0 and s_ids:
            prev = segs[idx - 1]
            prev_shots = list(prev.get("source_shots") or [])
            if prev_shots:
                prev_tail = prev_shots[-1]
                curr_head = s_ids[0]
                prev_sc = shot_scene[prev_tail]["scene_id"]
                curr_sc = shot_scene[curr_head]["scene_id"]
                cut_exit = str(shot_by_id[prev_tail].get("cut", {}).get("exit") or "").strip()
                if cut_exit:
                    prev_field, prev_exit = "cut.exit", cut_exit[:36]
                else:
                    prev_field, prev_exit = "action", str(shot_by_id[prev_tail].get("action") or "")[:36]
                curr_act = str(shot_by_id[curr_head].get("action") or "")[:36]
                if prev_sc != curr_sc and not isinstance(seg.get("cross_scene_continuity"), dict):
                    seg["cross_scene_continuity"] = {
                        "from_segment_id": prev["segment_id"],
                        "decision": "new_action_or_time_jump",
                        "basis": f"{prev_sc} 切换至 {curr_sc} 进入新场景事件",
                        "previous_source": {"shot_id": prev_tail, "field": prev_field, "quote": prev_exit},
                        "current_source": {"shot_id": curr_head, "field": "action", "quote": curr_act},
                    }
                elif prev_sc == curr_sc:
                    if not isinstance(seg.get("same_scene_split"), dict):
                        seg["same_scene_split"] = {
                            "from_segment_id": prev["segment_id"],
                            "reason": "event_complete",
                            "evidence": f"{prev_sc} 前半段动作完成后切入后半段事件",
                            "source_boundary": {"shot_id": prev_tail, "field": prev_field, "quote": prev_exit},
                        }
                    if not isinstance(seg.get("physical_continuity"), dict):
                        seg["physical_continuity"] = {
                            "from_segment_id": prev["segment_id"],
                            "permitted_change": f"沿用 {prev_sc} 同场空间承接后续动作",
                            "props": {
                                "status": "no_visual_needed",
                                "previous_state": prev_exit or "前段尾帧状态",
                                "current_state": curr_act or "本段起幅状态",
                                "basis": "同场既有锚点与产品参考已锁定外观，无需额外切点桥图",
                            },
                        }
    unwritten = [sid for sid in all_shot_ids if sid not in set(covered)]
    if not unwritten and covered == all_shot_ids:
        pkg["scope"] = {
            "type": "full_local_authored",
            "authored_segment_count": len(segs),
            "authored_shot_count": len(covered),
            "scope_seconds": round(edit_cursor, 2),
            "unwritten_shot_ids": [],
        }
    else:
        pkg["scope"] = {
            "type": "partial_in_progress",
            "authored_segment_count": len(segs),
            "authored_shot_count": len(covered),
            "scope_seconds": round(edit_cursor, 2),
            "unwritten_shot_ids": unwritten,
        }


def _sync_reconciliation(project: Path, manifest: dict[str, Any], pkg: dict[str, Any], reason: str | None = None) -> None:
    active = rc.show_change(project).get("active_change") or {}
    target_aid = next((t.get("artifact_id") for t in (active.get("targets") or []) if t.get("logical_id") == "video.prompt"), None)
    if target_aid:
        prior = next((e for e in manifest.get("artifacts", []) if e.get("artifact_id") == target_aid), None)
    else:
        priors = [e for e in manifest.get("artifacts", []) if e.get("logical_id") == "video.prompt" and e.get("status") in {"current", "stale"}]
        prior = priors[0] if len(priors) == 1 else None
    if not prior or not prior.get("path_or_url") or not prior.get("checksum"):
        pkg.pop("segment_reconciliation", None)
        return
    old_path = project / str(prior["path_or_url"])
    if not old_path.is_file():
        return
    old_rows = list((_load_json(old_path).get("generation_segments")) or [])
    new_rows = list(pkg.get("generation_segments") or [])
    if not old_rows or not new_rows:
        return
    why = str(reason or active.get("reason") or f"{pkg.get('revision', 'R02')} 按上游修订同步更新生成段")
    old_ids = [r["segment_id"] for r in old_rows]
    new_ids = [r["segment_id"] for r in new_rows]
    old_by_id = {r["segment_id"]: r for r in old_rows}
    new_by_id = {r["segment_id"]: r for r in new_rows}
    common = sorted(set(old_by_id) & set(new_by_id))
    changes: list[dict[str, Any]] = []
    for sid in sorted(set(old_ids) - set(new_ids)):
        changes.append({"action": "remove", "segment_id": sid, "reason": why})
    for sid in sorted(set(new_ids) - set(old_ids)):
        changes.append({"action": "add", "segment_id": sid, "reason": why})
    if [i for i in old_ids if i in common] != [i for i in new_ids if i in common]:
        changes.append({"action": "reorder", "before": old_ids, "after": new_ids, "reason": why})
    for sid in common:
        for field in ("source_shot_ids", "source_dialogue_ids", "source_paragraph_ids"):
            b = (source_shot_ids(old_by_id[sid]) or None) if field == "source_shot_ids" else old_by_id[sid].get(field)
            a = (source_shot_ids(new_by_id[sid]) or None) if field == "source_shot_ids" else new_by_id[sid].get(field)
            if b != a:
                changes.append({"action": "coverage", "segment_id": sid, "field": field, "before": b, "after": a, "reason": why})
        old_p, new_p = submitted_prompt_body(old_by_id[sid]), submitted_prompt_body(new_by_id[sid])
        if " ".join(old_p.split()) != " ".join(new_p.split()):
            changes.append({
                "action": "revise", "segment_id": sid,
                "before_sha256": hashlib.sha256(old_p.encode("utf-8")).hexdigest(),
                "after_sha256": hashlib.sha256(new_p.encode("utf-8")).hexdigest(),
                "reason": why,
            })
    if changes:
        pkg["segment_reconciliation"] = {
            "baseline": {"artifact_id": prior["artifact_id"], "sha256": prior["checksum"]},
            "changes": changes,
        }
    else:
        pkg.pop("segment_reconciliation", None)


def cmd_outline(args: argparse.Namespace) -> int:
    ctx = _context(Path(args.project).resolve())
    scenes_out = []
    for sc in ctx["storyboard"].get("scenes", []):
        shots = sc.get("shots", [])
        dur = round(sum(_shot_seconds(s, 0.0) for s in shots), 2)
        req = [rv.get("prompt_clause") for s in shots for rv in (s.get("required_visuals") or []) if isinstance(rv, dict)]
        scenes_out.append({
            "scene_id": sc["scene_id"],
            "location": sc.get("location", ""),
            "shot_ids": [s["shot_id"] for s in shots],
            "shot_count": len(shots),
            "total_seconds": dur,
            "required_visual_clauses": req,
        })
    print(json.dumps({
        "must_read": build_stage_guidance(ctx["project"], "voiceover-to-video-script"),
        "storyboard_revision": ctx["dir_entry"]["revision"],
        "total_shots": len(ctx["shots_ordered"]),
        "ready_assets": sorted(ctx["ready_assets"].keys()),
        "adopted_supplements": sorted({str(v.get("artifact_id") or v.get("id")): str(v.get("path_or_url") or v.get("path")) for v in ctx["supplements"].values()}.items()),
        "product_files": [{"id": f["id"], "role": f.get("role"), "action_key": f.get("action_key"), "path": f["path"]} for f in (ctx["product_pack"] or {}).get("references", [])],
        "scenes": scenes_out,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_plan_acts(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    ctx = _context(project)
    max_dur = float(args.target_duration)
    supp_by_shot: dict[str, list[str]] = {}
    seen_supps = set()
    for v in ctx["supplements"].values():
        aid = str(v.get("artifact_id") or v.get("id"))
        if aid in seen_supps:
            continue
        seen_supps.add(aid)
        for sid in (v.get("quality_evidence") or {}).get("source_shot_ids") or []:
            supp_by_shot.setdefault(sid, []).append(aid)

    planned_segs: list[dict[str, Any]] = []
    seg_idx = 0
    for sc in ctx["storyboard"].get("scenes", []):
        shots = sc.get("shots") or []
        bucket: list[dict[str, Any]] = []
        bucket_sec = 0.0
        for sh in shots:
            sh_sec = _shot_seconds(sh, 3.0)
            if bucket and (len(bucket) >= 4 or bucket_sec + sh_sec > max_dur):
                seg_idx += 1
                planned_segs.append(_build_planned_seg(seg_idx, sc["scene_id"], bucket, supp_by_shot))
                bucket = []
                bucket_sec = 0.0
            bucket.append(sh)
            bucket_sec += sh_sec
        if bucket:
            seg_idx += 1
            planned_segs.append(_build_planned_seg(seg_idx, sc["scene_id"], bucket, supp_by_shot))

    for i, pseg in enumerate(planned_segs):
        pseg["seam_in"] = planned_segs[i - 1]["seam_out"] if i > 0 else "全片开场起幅"

    step = max(1, int(args.max_segments_per_act))
    acts = []
    for a_idx, start in enumerate(range(0, len(planned_segs), step), 1):
        chunk = planned_segs[start:start + step]
        acts.append({
            "act_id": f"ACT-{a_idx:02d}",
            "segment_range": f"{chunk[0]['segment_id']}..{chunk[-1]['segment_id']}",
            "segments": chunk,
        })
    plan_doc = {
        "schema_version": "1.0",
        "storyboard_id": ctx["dir_entry"]["artifact_id"],
        "total_segments": len(planned_segs),
        "acts": acts,
    }
    out_rel = args.output or "06_视频/分幕接口卡.json"
    with project_lock(project):
        _save_json(project / out_rel, plan_doc)
    print(json.dumps({
        "saved_plan": out_rel,
        "total_segments": len(planned_segs),
        "acts": [{"act_id": a["act_id"], "segment_range": a["segment_range"], "count": len(a["segments"])} for a in acts],
    }, ensure_ascii=False, indent=2))
    return 0


def _build_planned_seg(idx: int, scene_id: str, shots: list[dict[str, Any]], supp_by_shot: dict[str, list[str]]) -> dict[str, Any]:
    s_ids = [s["shot_id"] for s in shots]
    dur = max(4, min(15, round(sum(_shot_seconds(s, 3.0) for s in shots))))
    asts = sorted({a for s in shots for a in (s.get("source_asset_requirement_ids") or [])})
    supps = sorted({sp for sid in s_ids for sp in supp_by_shot.get(sid, [])})
    prods = []
    req_clauses = []
    for s in shots:
        for rv in s.get("required_visuals") or []:
            if isinstance(rv, dict):
                if rv.get("prompt_clause"):
                    req_clauses.append(str(rv["prompt_clause"]))
                ar = rv.get("action_reference") or {}
                if ar.get("reference_id"):
                    prods.append(f"PROD:{ar['reference_id']}")
    tail = shots[-1]
    exit_txt = str((tail.get("cut") or {}).get("exit") or tail.get("action") or "")[:60]
    return {
        "segment_id": f"SEG-{idx:02d}",
        "scene_id": scene_id,
        "source_shots": s_ids,
        "suggested_duration_seconds": dur,
        "suggested_materials": asts + supps + sorted(set(prods)),
        "required_visual_clauses": req_clauses,
        "seam_out": f"{tail['shot_id']} 尾态：{exit_txt}",
    }


def cmd_segment_source(args: argparse.Namespace) -> int:
    ctx = _context(Path(args.project).resolve())
    shot_ids = [s.strip() for s in args.shots.split(",") if s.strip()]
    shots = [ctx["shot_by_id"][sid] for sid in shot_ids if sid in ctx["shot_by_id"]]
    missing = [sid for sid in shot_ids if sid not in ctx["shot_by_id"]]
    if missing:
        raise SystemExit(f"导演包中不存在镜头：{missing}")
    print(json.dumps({
        "shot_ids": shot_ids,
        "suggested_duration_seconds": max(4, min(30, round(sum(_shot_seconds(s, 0.0) for s in shots)))),
        "shots": shots,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    ctx = _context(project)
    dir_entry = ctx["dir_entry"]
    dir_rel = str(dir_entry.get("path_or_url") or dir_entry.get("path"))
    dir_path = project / dir_rel
    cap_rel = args.capability_evidence
    cap_path = project / cap_rel
    if not cap_path.is_file():
        raise SystemExit(f"模型能力取证文件不存在：{cap_rel}")
    cap_json = _load_json(cap_path)
    canvas = ctx["kickoff"].get("canvas") or {}
    out_path = project / args.output
    pkg = {
        "schema_version": "2.0",
        "kind": "video.prompt",
        "project_id": ctx["manifest"].get("project_id") or project.name,
        "revision": args.revision,
        "status": "candidate",
        "review_status": "unreviewed",
        "production_blocked": False,
        "generation_authorized": False,
        "open_questions": [],
        "source_storyboard": {
            "artifact_id": str(dir_entry.get("artifact_id") or dir_entry.get("id")),
            "logical_id": "storyboard.package",
            "revision": str(dir_entry["revision"]),
            "path": dir_rel,
            "sha256": str(dir_entry.get("checksum") or _sha256(dir_path)),
        },
        "selected_video_model": {
            "id": str(canvas.get("model") or "Seedance 2.5"),
            "platform": str(canvas.get("platform") or "redframe"),
            "entry_point": "redframe_canvas",
            "reference_mode": "reference",
            "confirmed_by_user": True,
            "min_duration_seconds": int(args.min_duration),
            "max_duration_seconds": int(args.max_duration),
            "capability_evidence": {
                "path": cap_rel,
                "sha256": _sha256(cap_path),
                "locator": str(args.capability_locator),
                "checked_at": str(cap_json.get("captured_at") or cap_json.get("checkedAt") or cap_json.get("checked_at") or "2026-11-26T23:38:56+08:00"),
            },
        },
        "input_roots": [],
        "generation_segments": [],
    }
    from_pkg = getattr(args, "from_package", None)
    if from_pkg:
        base = _load_json(project / from_pkg)
        shot_audio_map = director_shot_audio([ctx["storyboard"]])
        roots = set(base.get("input_roots") or [])
        cloned_segs = []
        for old_seg in deepcopy(base.get("generation_segments") or []):
            s_shots = [s for s in (old_seg.get("source_shots") or []) if s in ctx["shot_by_id"]]
            specs = []
            for m in old_seg.get("materials") or []:
                if m.get("product_reference_id"):
                    specs.append({"ref": f"PROD:{m['product_reference_id']}", "role": m["role"], "source_shot_ids": m.get("source_shot_ids")})
                elif m.get("artifact_id"):
                    aid = str(m["artifact_id"])
                    if aid not in ctx["supplements"]:
                        want = set(m.get("source_shot_ids") or s_shots)
                        hit = next((str(v.get("artifact_id") or v.get("id")) for v in ctx["supplements"].values()
                                    if want & set((v.get("quality_evidence") or {}).get("source_shot_ids") or [])), aid)
                        aid = hit
                    specs.append({"ref": aid, "role": m["role"], "source_shot_ids": m.get("source_shot_ids"),
                                  "requires_face_certification": m.get("requires_face_certification", True)})
                else:
                    specs.append({"ref": f"{m['asset_id']}@{m['output_id']}", "role": m["role"], "source_shot_ids": m.get("source_shot_ids"),
                                  "requires_face_certification": m.get("requires_face_certification", True)})
            mats, ext = _resolve_materials(ctx, specs, s_shots)
            roots |= ext
            old_seg["materials"] = mats
            old_seg["source_audio"] = [{"shot_id": sid, "dialogue": shot_audio_map.get(sid, [])} for sid in s_shots]
            cloned_segs.append(old_seg)
        pkg["input_roots"] = sorted(roots)
        pkg["generation_segments"] = cloned_segs
    with project_lock(project):
        _refresh_scope_and_continuity(ctx, pkg)
        _sync_reconciliation(project, ctx["manifest"], pkg)
        _save_json(out_path, pkg)
    print(json.dumps({
        "must_read": build_stage_guidance(project, "voiceover-to-video-script"),
        "initialized": str(out_path.relative_to(project)),
        "revision": args.revision,
        "cloned_from": from_pkg,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_put_segment(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    ctx = _context(project)
    pkg_path = project / args.package
    pkg = _load_json(pkg_path)
    seg_in = _load_json(Path(args.input).resolve())
    source_shots = [str(s) for s in seg_in.get("source_shots") or []]
    if not source_shots:
        raise SystemExit("source_shots 不能为空")
    for sid in source_shots:
        if sid not in ctx["shot_by_id"]:
            raise SystemExit(f"未知镜头 ID：{sid}")
    duration = int(seg_in["duration_seconds"])
    shot_durations = seg_in.get("shot_durations")
    if not shot_durations:
        raw_durs = [_shot_seconds(ctx["shot_by_id"][s], 1.0) for s in source_shots]
        scale = duration / sum(raw_durs)
        shot_durations = [round(d * scale, 2) for d in raw_durs]
        shot_durations[-1] = round(duration - sum(shot_durations[:-1]), 2)
    if len(shot_durations) != len(source_shots):
        raise SystemExit(f"shot_durations 长度 ({len(shot_durations)}) 必须与 source_shots ({len(source_shots)}) 一致")
    materials, ext_roots = _resolve_materials(ctx, list(seg_in.get("materials") or []), source_shots)
    prompt = str(seg_in.get("prompt") or "").strip()
    body_errs = submitted_body_issues(prompt)
    if __package__:
        from .check_video_prompt_focal_intent import FOCAL_RE, _focal_measurements, _has_optical_purpose
    else:
        from check_video_prompt_focal_intent import FOCAL_RE, _focal_measurements, _has_optical_purpose
    if FOCAL_RE.search(prompt) and not _has_optical_purpose(prompt):
        body_errs.append("出现定性焦段词但缺少可观察光学目的（请在同句写出保持/突出/压缩/看清/纵深/层次等目的，或加「光学目的：…」）")
    if _focal_measurements(prompt):
        body_errs.append("生成正文出现未标来源的精确毫米数（请改用广角/标准焦段/中长焦等定性焦段倾向）")
    prod = (ctx["kickoff"] or {}).get("product") or {}
    if isinstance(prod, dict) and prod.get("product_id") == "creator-polishing-mask-2" and _CREATOR_BOWL_BRUSH_OFFER.search(prompt):
        body_errs.append("赠品口播请在视频生成正文使用「还送高定条膜碗刷」")
    if body_errs:
        raise SystemExit("提示词正文未过门禁：" + "；".join(body_errs))
    canvas = ctx["kickoff"].get("canvas") or {}
    shot_audio_map = director_shot_audio([ctx["storyboard"]])
    source_audio = [
        {"shot_id": sid, "dialogue": shot_audio_map.get(sid, [])}
        for sid in source_shots
    ]
    built: dict[str, Any] = {
        "segment_id": str(seg_in["segment_id"]),
        "title": str(seg_in.get("title") or seg_in["segment_id"]),
        "source_shots": source_shots,
        "duration_seconds": duration,
        "shot_durations": shot_durations,
        "target_edit_duration_seconds": float(seg_in.get("target_edit_duration_seconds") or duration),
        "parameters": {
            "model": str(canvas.get("model") or "Seedance 2.5"),
            "aspect_ratio": str(canvas.get("aspect_ratio") or "9:16"),
            "resolution": str(canvas.get("resolution") or "480p"),
            "duration_seconds": duration,
            "reference_mode": "reference",
        },
        "source_audio": source_audio,
        "materials": materials,
        "prompt": prompt,
    }
    for opt_k in ("cross_scene_continuity", "same_scene_split", "physical_continuity"):
        if isinstance(seg_in.get(opt_k), dict):
            built[opt_k] = seg_in[opt_k]
    seg_errs: list[str] = []
    try:
        validate_shot_durations(built, built["parameters"])
    except ValueError as exc:
        seg_errs.append(str(exc))
    req_visuals = {
        sid: reqs
        for sid in source_shots
        if isinstance(reqs := ctx["shot_by_id"][sid].get("required_visuals"), list) and reqs
    }
    try:
        validate_required_visuals([built], req_visuals, shot_order=source_shots, project=project)
    except ValueError as exc:
        seg_errs.append(str(exc))
    if seg_errs:
        raise SystemExit("分段镜头时长/必现画面校验未通过：" + "；".join(seg_errs))
    shot_rank = {s["shot_id"]: i for i, s in enumerate(ctx["shots_ordered"])}
    with project_lock(project):
        pkg = _load_json(pkg_path)
        segs = list(pkg.get("generation_segments") or [])
        idx = next((i for i, s in enumerate(segs) if s.get("segment_id") == built["segment_id"]), None)
        if idx is None:
            segs.append(built)
        else:
            segs[idx] = built
        segs.sort(key=lambda s: (
            shot_rank.get((s.get("source_shots") or [""])[0], 10**6),
            s.get("segment_id", ""),
        ))
        pkg["generation_segments"] = segs
        roots = set(pkg.get("input_roots") or []) | ext_roots
        pkg["input_roots"] = sorted(roots)
        _refresh_scope_and_continuity(ctx, pkg)
        _sync_reconciliation(project, ctx["manifest"], pkg, getattr(args, "reconcile_reason", None))
        _save_json(pkg_path, pkg)
    print(json.dumps({
        "saved_segment": built["segment_id"],
        "shots": source_shots,
        "materials": [f"{m['reference']}->{Path(m['path']).name}" for m in materials],
        "scope": pkg["scope"],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    pkg_path = project / args.package
    pkg = _load_json(pkg_path)
    errs: list[str] = []
    try:
        validate_candidate_submission(
            project,
            {
                "artifact_id": f"VID-{pkg.get('revision', 'R01')}",
                "logical_id": "video.prompt",
                "type": "video.prompt",
                "revision": str(pkg.get("revision", "R01")),
                "path_or_url": args.package,
            },
        )
    except Exception as exc:
        errs.append(str(exc))
    print(json.dumps({
        "package": args.package,
        "scope": pkg.get("scope"),
        "segment_count": len(pkg.get("generation_segments") or []),
        "validation_ok": not errs,
        "validation_errors": errs,
    }, ensure_ascii=False, indent=2))
    return 0 if not errs else 2


@project_locked
def _upsert_video_candidate(project: Path, artifact: dict[str, Any]) -> dict[str, Any]:
    manifest = load_manifest(project)
    existing = next((e for e in manifest.get("artifacts", []) if e.get("artifact_id") == artifact["artifact_id"]), None)
    if existing is not None:
        if (
            existing.get("status") != "candidate"
            or existing.get("user_approved") is not False
            or "agent_acceptance" in existing
            or existing.get("logical_id") != artifact["logical_id"]
            or existing.get("path_or_url") != artifact["path_or_url"]
            or existing.get("revision") != artifact["revision"]
        ):
            raise ValueError(f"产物已存在且不是同路径未审候选，不可原地刷新：{artifact['artifact_id']}")
        existing["checksum"] = artifact["checksum"]
        existing["depends_on"] = artifact["depends_on"]
        save_manifest(project, manifest)
        return existing
    return register_workspace_artifact(project, artifact)


def cmd_register(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    pkg_path = project / args.package
    pkg = _load_json(pkg_path)
    if pkg.get("scope", {}).get("type") != "full_local_authored":
        raise SystemExit(f"尚未写完全片镜头，剩余：{pkg.get('scope', {}).get('unwritten_shot_ids')}")
    manifest = load_manifest(project)
    _sync_reconciliation(project, manifest, pkg, getattr(args, "reconcile_reason", None))
    _save_json(pkg_path, pkg)
    rel = pkg_path.relative_to(project).as_posix()
    checksum = _sha256(pkg_path)
    active = rc.show_change(project).get("active_change")
    chg_id = getattr(args, "change_id", None) or (
        active["change_id"] if isinstance(active, dict) and active.get("status") == "awaiting_candidate"
        and any(t.get("logical_id") == "video.prompt" for t in (active.get("targets") or [])) else None
    )
    if chg_id:
        rc.record_candidate(
            project, change_id=chg_id, artifact_id=args.artifact_id, logical_id="video.prompt",
            artifact_type="video.prompt", candidate_path=rel, revision=str(pkg["revision"]),
            depends_on=["storyboard.package"],
        )
        print(json.dumps({"registered": args.artifact_id, "change_id": chg_id, "status": "candidate", "sha256": checksum}, ensure_ascii=False, indent=2))
        return 0
    record = _upsert_video_candidate(project, {
        "artifact_id": args.artifact_id,
        "logical_id": "video.prompt",
        "type": "video.prompt",
        "path_or_url": rel,
        "revision": str(pkg["revision"]),
        "depends_on": ["storyboard.package"],
        "checksum": checksum,
        "user_approved": False,
    })
    print(json.dumps({"registered": args.artifact_id, "status": record.get("status"), "sha256": checksum}, ensure_ascii=False, indent=2))
    return 0


def cmd_self_check_template(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone
    if __package__:
        from .core import revision_control as rc
        from .core.agent_acceptance import accept_artifact
    else:
        from core import revision_control as rc
        from core.agent_acceptance import accept_artifact
    project = Path(args.project).resolve()
    manifest = load_manifest(project)
    entry = next((e for e in manifest.get("artifacts", []) if e.get("artifact_id") == args.artifact_id), None)
    if not entry:
        raise SystemExit(f"请先用 register 登记候选产物：{args.artifact_id}")
    profile_path = REPO / "skills/voiceover-to-video-script/review-profile.json"
    profile = _load_json(profile_path)
    out_path = project / args.output
    obs_txt = str(args.observation or "").strip()
    checks = []
    for c in profile.get("production_artifact_checks", []):
        cid = str(c["id"])
        na = cid in {"VIDEO-SUBMISSION", "VIDEO-MEDIA"}
        checks.append({
            "id": cid,
            "requirement": str(c.get("text") or ""),
            "result": "not_applicable" if na else "pass",
            "reason": "当前仅采用视频提示词并交付红帧未点击生成草稿，不发起视频生成任务" if na else "",
            "observation": (obs_txt if not na else "本次按默认草稿收口边界不提交视频生成、不验收成片媒体"),
            "locator": f"{entry['path_or_url']}#generation_segments",
        })
        if not na:
            checks[-1].pop("reason", None)
    report = {
        "schema_version": "1.0",
        "kind": "agent-self-check",
        "scope": "artifact",
        "verdict": "pass" if obs_txt else "",
        "authorization_id": args.authorization_id,
        "agent_id": args.agent_id,
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "review_profile_sha256": _sha256(profile_path),
        **acceptance_binding(project, entry),
        "checks": checks,
    }
    _save_json(out_path, report)
    rel_sc = out_path.relative_to(project).as_posix()
    active = rc.show_change(project).get("active_change") or {}
    chg_id = active.get("change_id") if (active.get("candidate_receipt") or {}).get("artifact_id") == args.artifact_id else None
    res: dict[str, Any] = {
        "self_check": rel_sc,
        "fill": "逐项核实并填写各 pass 项的真实 observation 与 locator 后，将 verdict 设为 pass，再执行 stage_context_cli.py accept-artifact",
    }
    if args.auto_accept:
        if not obs_txt:
            raise SystemExit("--auto-accept 须同时提供 --observation 真实复核结论")
        adopted = accept_artifact(argparse.Namespace(project=str(project), artifact_id=args.artifact_id, self_check_json=rel_sc, change_id=chg_id, preflight_json=None))
        res["accepted"] = adopted.get("ok", True)
        res["change_id"] = chg_id
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_out = sub.add_parser("outline", help="输出场次镜头概览、已确认资产、补图与产品参考清单")
    p_out.add_argument("--project", required=True)
    p_out.set_defaults(func=cmd_outline)

    p_plan = sub.add_parser("plan-acts", help="自动按场景与时长预算生成锁死首尾接缝的多幕并行分段接口卡")
    p_plan.add_argument("--project", required=True)
    p_plan.add_argument("--max-segments-per-act", type=int, default=8)
    p_plan.add_argument("--target-duration", type=float, default=10.0)
    p_plan.add_argument("--output", default="06_视频/分幕接口卡.json")
    p_plan.set_defaults(func=cmd_plan_acts)

    p_src = sub.add_parser("segment-source", help="按镜头列表读取导演包分镜原文与建议时长")
    p_src.add_argument("--project", required=True)
    p_src.add_argument("--shots", required=True, help="逗号分隔镜头 ID，如 S01-001,S01-002")
    p_src.set_defaults(func=cmd_segment_source)

    p_init = sub.add_parser("init", help="初始化或从旧版克隆 v2.0 视频提示词包骨架")
    p_init.add_argument("--project", required=True)
    p_init.add_argument("--revision", required=True)
    p_init.add_argument("--output", required=True, help="如 06_视频/R07_视频提示词.json")
    p_init.add_argument("--from-package", help="可选：从旧版视频提示词包克隆分段并自动重绑最新导演包与补图")
    p_init.add_argument("--capability-evidence", required=True, help="如 06_视频/红帧Seedance25原生模型能力取证.json")
    p_init.add_argument("--capability-locator", default="model.durationResolutionMap")
    p_init.add_argument("--min-duration", type=int, default=4)
    p_init.add_argument("--max-duration", type=int, default=30)
    p_init.set_defaults(func=cmd_init)

    p_put = sub.add_parser("put-segment", help="写入或更新单个生成段并立即校验门禁")
    p_put.add_argument("--project", required=True)
    p_put.add_argument("--package", required=True)
    p_put.add_argument("--input", required=True, help="单段 JSON 输入文件")
    p_put.add_argument("--reconcile-reason", help="可选：生成段返修对账理由")
    p_put.set_defaults(func=cmd_put_segment)

    p_st = sub.add_parser("status", help="检查完成度与全包候选门禁")
    p_st.add_argument("--project", required=True)
    p_st.add_argument("--package", required=True)
    p_st.set_defaults(func=cmd_status)

    p_reg = sub.add_parser("register", help="全包校验并登记为 candidate 产物（支持活动变更案）")
    p_reg.add_argument("--project", required=True)
    p_reg.add_argument("--package", required=True)
    p_reg.add_argument("--artifact-id", required=True)
    p_reg.add_argument("--change-id", help="可选：绑定活动变更案编号（省略时自动检测）")
    p_reg.add_argument("--reconcile-reason", help="可选：生成段返修对账理由")
    p_reg.set_defaults(func=cmd_register)

    p_sc = sub.add_parser("self-check-template", help="生成绑定好指纹的自查表骨架（支持 --observation 与 --auto-accept）")
    p_sc.add_argument("--project", required=True)
    p_sc.add_argument("--artifact-id", required=True)
    p_sc.add_argument("--authorization-id", default="FLOW-R01")
    p_sc.add_argument("--agent-id", default="voiceover-to-video-script")
    p_sc.add_argument("--output", required=True)
    p_sc.add_argument("--observation", help="可选：填入真实全包复核结论以直接生成 pass 自查报告")
    p_sc.add_argument("--auto-accept", action="store_true", help="配合 --observation 一键完成 accept-artifact 正式采用")
    p_sc.set_defaults(func=cmd_self_check_template)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
