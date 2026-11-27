#!/usr/bin/env python3
"""Write a storyboard-package (5.0) one scene at a time, so no single reply has to carry the whole film.

用法（仓库根目录，用 .venv/bin/python）：
  init       从已采用编导稿建空骨架（顶层来源字段自动预填），--header 只需给 director_rules 等少量创作字段
  put-scene  追加或替换一个/几个 scene（按 scene_id），按故事段落顺序排好并做单场结构校验
  set        更新顶层创作字段（director_rules、timing_basis、open_questions、asset_gaps）
  status     列出已写场次、已覆盖/待写 STP 与对白、镜头总数，并给出下一步的局部或全片校验命令
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

if __package__:
    from .core import artifacts
    from .core.asset_refs import effective, read_choices
    from .core.io import project_lock, read_json, write_json
    from .core.schema_contracts import SCHEMA_ROOT
    from .core.stage_guidance import build_stage_guidance
else:
    from core import artifacts
    from core.asset_refs import effective, read_choices
    from core.io import project_lock, read_json, write_json
    from core.schema_contracts import SCHEMA_ROOT
    from core.stage_guidance import build_stage_guidance

SCHEMA = "storyboard-package.schema.json"
HEADER_FIELDS = ("director_rules", "timing_basis", "open_questions", "asset_gaps")
DEFAULT_TIMING = {"status": "not_evaluable", "source_type": None, "source_path": None, "source_field": None}


def _narrative(root):
    entries = artifacts.load_manifest(root)["artifacts"]
    state = artifacts.load_project_state(root)
    rows = [e for e in entries if e["logical_id"] == "narrative.package" and e["status"] == "current"]
    if len(rows) != 1 or not artifacts.artifact_is_adopted(rows[0], state, root):
        raise ValueError("缺当前已采用的编导稿，先完成编导采用")
    entry = rows[0]
    path = artifacts.project_path(root, entry["path_or_url"])
    if artifacts.digest(path) != entry["checksum"]:
        raise ValueError("编导稿文件与登记的校验值不符")
    return entry, read_json(path)


def _package_path(root, out):
    path = artifacts.project_path(root, out)
    if path == root or not path.is_relative_to(root) or path.suffix != ".json":
        raise ValueError("导演包须是项目内的 .json 路径")
    return path


def _scene_validator(definition="scene"):
    import jsonschema
    schema = json.loads((SCHEMA_ROOT / SCHEMA).read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator({"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]})


def _scene_errors(scene, definition="scene"):
    errors = sorted(_scene_validator(definition).iter_errors(scene), key=lambda e: tuple(map(str, e.absolute_path)))
    return [(".".join(map(str, e.absolute_path)) or "$") + ": " + e.message for e in errors]


def _read_input(path, root=None):
    if str(path) == "-":
        return json.load(sys.stdin)
    p = Path(path)
    if not p.is_absolute() and not p.is_file() and root is not None:
        candidate = artifacts.project_path(root, path)
        if candidate.is_file():
            p = candidate
    return read_json(p)


def _order(story):
    return {p["paragraph_id"]: i for i, p in enumerate(story["story_paragraphs"])}


def _sort_scenes(scenes, order):
    return sorted(scenes, key=lambda s: min((order.get(p, 10**6) for p in s.get("source_paragraph_ids", [])), default=10**6))


def init(root, out, header=None, revision=None, from_package=None):
    from copy import deepcopy
    entry, story = _narrative(root)
    path = _package_path(root, out)
    if path.exists():
        raise ValueError(f"导演包已存在，不覆盖：{out}（续写用 put-scene，改顶层用 set）")
    base = read_json(_package_path(root, from_package)) if from_package else {}
    assets = effective(story, read_choices(root))
    cloned_scenes = []
    for sc in deepcopy(base.get("scenes") or []):
        cloned_scenes.append(fill_derived(sc, story))
    package = {
        "schema_version": "5.0",
        "project_id": story["project_id"],
        "revision": revision or entry["revision"],
        "status": "candidate",
        "source_narrative": {"artifact_id": entry["artifact_id"], "logical_id": "narrative.package",
                             "revision": entry["revision"], "path": entry["path_or_url"]},
        "source_paragraph_ids": [p["paragraph_id"] for p in story["story_paragraphs"]],
        "source_dialogue_ids": [d["dialogue_id"] for d in story["dialogues"]],
        "source_asset_requirement_ids": [a["asset_id"] for a in assets],
        "director_rules": deepcopy(base.get("director_rules")),
        "scenes": _sort_scenes(cloned_scenes, _order(story)) if cloned_scenes else [],
        "asset_gaps": deepcopy(base.get("asset_gaps") or []),
        "open_questions": deepcopy(base.get("open_questions") or []),
        "timing_basis": deepcopy(base.get("timing_basis") or DEFAULT_TIMING),
    }
    _apply_header(package, header)
    if not isinstance(package["director_rules"], dict):
        raise ValueError("init 须在 --header 里给 director_rules（或通过 --from-package 继承）")
    write_json(path, package)
    return status(root, out)


def _apply_header(package, header):
    if header is None:
        return
    if not isinstance(header, dict) or set(header) - set(HEADER_FIELDS):
        raise ValueError("顶层只能改：" + "、".join(HEADER_FIELDS))
    package.update(header)


def set_header(root, out, header):
    path = _package_path(root, out)
    package = read_json(path)
    _apply_header(package, header)
    write_json(path, package)
    return status(root, out)


def fill_derived(scene, story):
    """Fill what the narrative already decides, so the director never retypes it.

    - shot.source_paragraph_ids defaults to the scene's paragraphs;
    - each dialogue placement may give only dialogue_id (+presentation/delivery, and text when a
      line is split across shots); speaker_id and full text come from the narrative;
    - shot.source_dialogue_ids is derived from the placements;
    - legacy v4.0 shot fields (take / cut.trigger+information_handoff) are losslessly upgraded to v5.0.
    """
    lines = {d["dialogue_id"]: d for d in story.get("dialogues", [])}
    for shot in scene.get("shots", []):
        shot.setdefault("source_paragraph_ids", list(scene.get("source_paragraph_ids", [])))
        if "action" not in shot and isinstance(shot.get("take"), dict):
            tk = shot.pop("take")
            parts = [str(tk.get(k) or "").strip() for k in ("start", "process", "end") if str(tk.get(k) or "").strip()]
            shot["action"] = " ".join(parts)
        elif "take" in shot and "action" in shot:
            shot.pop("take", None)
        cut = shot.get("cut")
        if isinstance(cut, dict) and "handoff" not in cut:
            h_parts = [str(cut.get(k) or "").strip() for k in ("trigger", "information_handoff", "entry") if str(cut.get(k) or "").strip()]
            shot["cut"] = {"exit": str(cut.get("exit") or ""), "handoff": "；".join(h_parts) or str(cut.get("exit") or "硬切")}
        placements = shot.get("audio", {}).get("dialogue", [])
        for placement in placements:
            line = lines.get(placement.get("dialogue_id"))
            if line is None:
                raise ValueError(f"{shot.get('shot_id', '?')} 引用了编导稿里没有的对白：{placement.get('dialogue_id')}")
            placement.setdefault("speaker_id", line["speaker_id"])
            placement.setdefault("text", line["text"])
            placement.setdefault("presentation", "SYNC")
        if "audio" in shot:
            shot.setdefault("source_dialogue_ids", list(dict.fromkeys(p["dialogue_id"] for p in placements)))
        ast_list = list(shot.get("source_asset_requirement_ids") or [])
        for rv in shot.get("required_visuals") or []:
            prod_ast = (rv.get("action_reference") or {}).get("product_ast_id") if isinstance(rv, dict) else None
            if isinstance(prod_ast, str) and prod_ast and prod_ast not in ast_list:
                ast_list.append(prod_ast)
        if ast_list:
            shot["source_asset_requirement_ids"] = ast_list
    return scene


def put_scenes(root, out, scenes):
    _, story = _narrative(root)
    path = _package_path(root, out)
    package = read_json(path)
    if isinstance(scenes, dict):
        scenes = [scenes]
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("--scene 须是一个 scene 对象或 scene 数组")
    problems = []
    for scene in scenes:
        fill_derived(scene, story)
        for error in _scene_errors(scene):
            problems.append(f"{scene.get('scene_id', '?')}.{error}")
    if problems:
        raise ValueError("场次结构不符合导演包格式：" + "；".join(problems[:20]))
    incoming = {s["scene_id"] for s in scenes}
    kept = [s for s in package["scenes"] if s["scene_id"] not in incoming]
    seen = {}
    for scene in kept + scenes:
        for shot in scene["shots"]:
            if shot["shot_id"] in seen:
                raise ValueError(f"镜号重复：{shot['shot_id']}（{seen[shot['shot_id']]} 与 {scene['scene_id']}）")
            seen[shot["shot_id"]] = scene["scene_id"]
    unknown = {p for s in scenes for p in s["source_paragraph_ids"]} - set(_order(story))
    if unknown:
        raise ValueError("引用了编导稿里没有的段落：" + "、".join(sorted(unknown)))
    package["scenes"] = _sort_scenes(kept + scenes, _order(story))
    write_json(path, package)
    return status(root, out)


def outline(root):
    """Return a compact whole-film outline (~1.5KB) so init never reads all 11 full scenes upfront."""
    entry, story = _narrative(root)
    assets = {a["asset_id"]: a["name"] for a in effective(story, read_choices(root))}
    by_p = {}
    for d in story.get("dialogues", []):
        by_p.setdefault(d["source_paragraph_id"], []).append(d)
    rows = []
    for p in story["story_paragraphs"]:
        pid = p["paragraph_id"]
        first_line = next((ln.strip() for ln in p["text"].splitlines() if ln.strip()), "")[:80]
        dlgs = by_p.get(pid, [])
        p_assets = [aid for aid in assets if any(
            aid == a["asset_id"] and pid in a.get("source_paragraph_ids", [])
            for a in story.get("asset_requirements", [])
        )]
        rows.append({
            "paragraph_id": pid,
            "summary": first_line,
            "dialogue_ids": [d["dialogue_id"] for d in dlgs],
            "dialogue_chars": sum(len(d["text"]) for d in dlgs),
            "asset_ids": p_assets,
        })
    return {
        "must_read": build_stage_guidance(root, "storyboard-planner"),
        "narrative_id": entry["artifact_id"],
        "revision": entry["revision"],
        "objective": story.get("objective"),
        "locked_facts": [f.get("statement", f) for f in story.get("locked_facts", [])],
        "paragraphs": rows,
    }


def scene_source(root, paragraph_ids):
    """Return verbatim text, dialogues, and valid ASTs for ONLY the requested paragraph(s)."""
    entry, _ = _narrative(root)
    if __package__:
        from .core.director_inputs import sources
    else:
        from core.director_inputs import sources
    return sources(root, entry, paragraph_ids=set(paragraph_ids))


def append_shots(root, out, scene_id, shots):
    """Replace existing shot_ids in place or append new shots on an existing scene."""
    _, story = _narrative(root)
    path = _package_path(root, out)
    package = read_json(path)
    target = next((s for s in package["scenes"] if s["scene_id"] == scene_id), None)
    if target is None:
        raise ValueError(f"场次 {scene_id} 尚未创建，先用 put-scene 写入含前几镜的场次")
    shots = shots if isinstance(shots, list) else [shots]
    if not shots:
        raise ValueError("--shots 不能为空")
    temp_scene = {**target, "shots": list(shots)}
    fill_derived(temp_scene, story)
    by_id = {s["shot_id"]: s for s in temp_scene["shots"]}
    existing_ids = {s["shot_id"] for s in target["shots"]}
    updated = [by_id.get(s["shot_id"], s) for s in target["shots"]]
    appended = [s for s in temp_scene["shots"] if s["shot_id"] not in existing_ids]
    merged = {**target, "shots": updated + appended}
    return put_scenes(root, out, [merged])


def _plan_path(root, out):
    path = _package_path(root, out)
    return path.with_name(path.stem + ".parallel-plan.json")


def save_plan(root, out, plan):
    """Persist the lead director's parallel group plan and validate full paragraph coverage."""
    _, story = _narrative(root)
    expected = [p["paragraph_id"] for p in story["story_paragraphs"]]
    groups = plan.get("groups") if isinstance(plan, dict) else None
    if not isinstance(groups, list) or not groups:
        raise ValueError("分组计划须含非空 groups 数组")
    seen_groups, seen_scenes, covered = set(), set(), []
    for g in groups:
        gid = g.get("group_id") if isinstance(g, dict) else None
        scenes = g.get("scenes") if isinstance(g, dict) else None
        if not isinstance(gid, str) or not gid.strip() or gid in seen_groups or not isinstance(scenes, list) or not scenes:
            raise ValueError(f"分组 {gid!r} 须有唯一 group_id 与非空 scenes 列表")
        seen_groups.add(gid)
        for s in scenes:
            sid = s.get("scene_id") if isinstance(s, dict) else None
            pars = s.get("paragraphs") if isinstance(s, dict) else None
            if (not isinstance(sid, str) or sid in seen_scenes or not isinstance(pars, list) or not pars
                    or not all(isinstance(s.get(k), str) and s[k].strip() for k in ("entry_handoff", "exit_handoff"))):
                raise ValueError(f"场次 {sid!r} 须有唯一 scene_id、paragraphs 及非空 entry_handoff/exit_handoff 交接棒")
            seen_scenes.add(sid)
            covered.extend(pars)
    if sorted(covered) != sorted(expected):
        missing = sorted(set(expected) - set(covered))
        extra = sorted(set(covered) - set(expected))
        dupes = sorted({p for p in covered if covered.count(p) > 1})
        raise ValueError(f"分组段落须不重不漏覆盖全片：缺 {missing or '无'}；多 {extra or '无'}；重复 {dupes or '无'}")
    dest = _plan_path(root, out)
    write_json(dest, plan)
    return {"plan": dest.relative_to(root).as_posix(), "groups": [g["group_id"] for g in groups], "scenes": len(seen_scenes)}


def plan_acts(root, out, max_scenes_per_act=5):
    """Auto-generate a seam-locked parallel multi-act plan from story_paragraphs."""
    _, story = _narrative(root)
    assets_by_id = {a["asset_id"]: a for a in story.get("asset_requirements") or []}
    pars = story["story_paragraphs"]
    scene_specs = []
    for idx, p in enumerate(pars, 1):
        pid = p["paragraph_id"]
        sid = f"S{idx:02d}"
        first_ln = next((ln.strip() for ln in p["text"].splitlines() if ln.strip()), "")[:60]
        last_ln = next((ln.strip() for ln in reversed(p["text"].splitlines()) if ln.strip()), "")[:60]
        p_asts = [
            f"{a['asset_id']}({a.get('name', '')})"
            for a in assets_by_id.values()
            if pid in (a.get("source_paragraph_ids") or [])
        ]
        prev_tail = (
            next((ln.strip() for ln in reversed(pars[idx - 2]["text"].splitlines()) if ln.strip()), "")[:45]
            if idx > 1 else "全片开场起幅"
        )
        next_head = (
            next((ln.strip() for ln in pars[idx]["text"].splitlines() if ln.strip()), "")[:45]
            if idx < len(pars) else "全片收束定格"
        )
        scene_specs.append({
            "scene_id": sid,
            "paragraphs": [pid],
            "local_shot_numbering_rule": f"并行写稿时本场镜号统一用局部独立编号 {sid}-001, {sid}-002...（汇总 finalize-acts 或 register 时自动重排为全片连续镜号）",
            "entry_handoff": f"承接上一场尾态（{prev_tail}）切入：{first_ln}",
            "exit_handoff": f"收束本场末态（{last_ln}），为下一场（{next_head}）留清动势或图形匹配切点",
            "active_assets": p_asts,
        })
    groups = []
    step = max(1, int(max_scenes_per_act))
    for g_idx, start in enumerate(range(0, len(scene_specs), step), 1):
        chunk = scene_specs[start:start + step]
        groups.append({
            "group_id": f"ACT-{g_idx:02d}",
            "scene_range": f"{chunk[0]['scene_id']}..{chunk[-1]['scene_id']}",
            "shared_space_note": "各幕并行子代理严禁改动他幕场次；每场用局部镜号 SXX-001 起编，严格对齐首场 entry_handoff 与末场 exit_handoff",
            "scenes": chunk,
        })
    plan = {"schema_version": "1.0", "groups": groups}
    return save_plan(root, out, plan)


def finalize_acts(root, out, renumber_global=False):
    """Verify cross-scene/cross-act seam continuity and optionally normalize shot_ids sequentially."""
    import re
    _, story = _narrative(root)
    path = _package_path(root, out)
    package = read_json(path)
    scenes = _sort_scenes(package.get("scenes") or [], _order(story))
    expected_pars = [p["paragraph_id"] for p in story["story_paragraphs"]]
    covered_pars = [p for s in scenes for p in s.get("source_paragraph_ids", [])]
    if sorted(set(covered_pars)) != sorted(expected_pars):
        missing = sorted(set(expected_pars) - set(covered_pars))
        raise ValueError(f"尚有未完成段落，无法合并终检：{missing}")
    seam_warnings = []
    all_ids = [sh.get("shot_id", "") for sc in scenes for sh in (sc.get("shots") or [])]
    shot_pat = re.compile(r"^S[0-9]{2,}[A-Z]?-[0-9]{3,}$")
    needs_renumber = bool(
        renumber_global
        or len(all_ids) != len(set(all_ids))
        or any(not shot_pat.match(str(sid)) for sid in all_ids)
    )
    remap: dict[str, str] = {}
    serial = 0
    for idx, sc in enumerate(scenes):
        shots = sc.get("shots") or []
        if not shots:
            raise ValueError(f"场次 {sc.get('scene_id')} 无镜头")
        if idx > 0:
            prev_last = scenes[idx - 1]["shots"][-1]
            curr_first = shots[0]
            if not str((prev_last.get("cut") or {}).get("handoff") or "").strip():
                seam_warnings.append(f"{scenes[idx - 1]['scene_id']}->{sc['scene_id']}: 前场末镜缺 cut.handoff")
            if not str((curr_first.get("camera") or {}).get("framing") or "").strip():
                seam_warnings.append(f"{scenes[idx - 1]['scene_id']}->{sc['scene_id']}: 后场首镜缺 camera.framing")
        if needs_renumber:
            for shot in shots:
                serial += 1
                old_id = shot["shot_id"]
                new_id = f"{sc['scene_id']}-{serial:03d}"
                if old_id != new_id:
                    remap[old_id] = new_id
                    shot["shot_id"] = new_id
        for st_ev in sc.get("scene_states") or []:
            if remap and isinstance(st_ev, dict) and isinstance(st_ev.get("source_shot_ids"), list):
                st_ev["source_shot_ids"] = [remap.get(sid, sid) for sid in st_ev["source_shot_ids"]]
        for ep_st in sc.get("endpoint_strategies") or []:
            if remap and isinstance(ep_st, dict):
                if isinstance(ep_st.get("source_shot_ids"), list):
                    ep_st["source_shot_ids"] = [remap.get(sid, sid) for sid in ep_st["source_shot_ids"]]
                for bk in ("initial_boundary", "final_boundary"):
                    bobj = ep_st.get(bk)
                    if isinstance(bobj, dict) and bobj.get("shot_id") in remap:
                        bobj["shot_id"] = remap[bobj["shot_id"]]
    for gap in package.get("asset_gaps") or []:
        if remap and isinstance(gap, dict) and isinstance(gap.get("affected_shot_ids"), list):
            gap["affected_shot_ids"] = [remap.get(sid, sid) for sid in gap["affected_shot_ids"]]
    package["scenes"] = scenes
    write_json(path, package)
    st = status(root, out)
    st["renumbered_count"] = len(remap)
    st["seam_warnings"] = seam_warnings
    return st


def group_brief(root, out, group_id):
    """Return bounded rules, handoff batons, and verbatim sources for one parallel writer group."""
    dest = _plan_path(root, out)
    if not dest.is_file():
        raise ValueError("尚未保存分组计划，先由总导演运行 save-plan")
    plan = read_json(dest)
    group = next((g for g in plan["groups"] if g["group_id"] == group_id), None)
    if group is None:
        raise ValueError(f"未找到分组 {group_id}")
    pkg = read_json(_package_path(root, out))
    pars = [p for s in group["scenes"] for p in s["paragraphs"]]
    return {
        "group_id": group_id,
        "director_rules": pkg.get("director_rules"),
        "shared_space_note": group.get("shared_space_note", ""),
        "assigned_scenes": group["scenes"],
        "sources": scene_source(root, pars),
    }


def status(root, out):
    _, story = _narrative(root)
    path = _package_path(root, out)
    package = read_json(path)
    order = _order(story)
    covered = {p for s in package["scenes"] for p in s["source_paragraph_ids"]}
    placed = {d for s in package["scenes"] for shot in s["shots"] for d in shot.get("source_dialogue_ids", [])}
    all_dialogues = [d["dialogue_id"] for d in story["dialogues"]]
    written = sorted(covered, key=order.get)
    remaining = [p for p in order if p not in covered]
    rel = path.relative_to(root).as_posix()
    narrative_rel = package["source_narrative"]["path"]
    base = (f".venv/bin/python -m scripts.core.story_sources --project '{root}' "
            f"--narrative '{root / narrative_rel}' --storyboard '{root / rel}'")
    return {
        "package": rel,
        "scenes": [s["scene_id"] for s in package["scenes"]],
        "shots": sum(len(s["shots"]) for s in package["scenes"]),
        "written_paragraphs": written,
        "remaining_paragraphs": remaining,
        "dialogues_missing": [d for d in all_dialogues if d not in placed],
        "next_check": (base + "".join(f" --candidate-paragraph-id {p}" for p in written)) if remaining and written
        else (base if not remaining else None),
        "complete": not remaining,
    }


def register(root, out, artifact_id, change_id=None):
    """Run the full strict check, then register the exact candidate (never adopts it)."""
    if __package__:
        from .core import revision_control as rc
    else:
        from core import revision_control as rc
    entry, story = _narrative(root)
    path = _package_path(root, out)
    with project_lock(root):
        package = read_json(path)
        if package["source_narrative"]["artifact_id"] != entry["artifact_id"]:
            raise ValueError("导演包绑定的编导稿不是当前采用版")
        expected_pars = {p["paragraph_id"] for p in story["story_paragraphs"]}
        covered_pars = {p for s in package.get("scenes", []) for p in s.get("source_paragraph_ids", [])}
        if covered_pars == expected_pars:
            finalize_acts(root, out)
            package = read_json(path)
    checked = subprocess.run(
        [sys.executable, "-m", "scripts.core.story_sources", "--project", str(root),
         "--narrative", str(artifacts.project_path(root, entry["path_or_url"])), "--storyboard", str(path)],
        cwd=REPO, capture_output=True, text=True)
    if checked.returncode:
        raise ValueError("全片严格校验未通过：" + (checked.stderr.strip().splitlines() or ["未知错误"])[-1])
    active = rc.show_change(root).get("active_change")
    chg_id = change_id or (
        active["change_id"] if isinstance(active, dict) and active.get("status") == "awaiting_candidate"
        and any(t.get("logical_id") == "storyboard.package" for t in (active.get("targets") or [])) else None
    )
    rel_p = path.relative_to(root).as_posix()
    if chg_id:
        rc.record_candidate(root, change_id=chg_id, artifact_id=artifact_id, logical_id="storyboard.package",
                            artifact_type="storyboard.package", candidate_path=rel_p,
                            revision=package["revision"], depends_on=["narrative.package"])
        return {"registered": artifact_id, "change_id": chg_id, "status": "candidate", "sha256": artifacts.digest(path),
                "strict_check": checked.stdout.strip().splitlines()[-1:]}
    manifest = artifacts.load_manifest(root)
    existing = next((e for e in manifest["artifacts"] if e.get("artifact_id") == artifact_id), None)
    if existing and existing.get("status") == "candidate" and not existing.get("user_approved") and not existing.get("agent_acceptance"):
        manifest["artifacts"] = [e for e in manifest["artifacts"] if e.get("artifact_id") != artifact_id]
        artifacts.save_manifest(root, manifest)
    record = artifacts.register_workspace_artifact(root, {
        "artifact_id": artifact_id, "logical_id": "storyboard.package", "type": "storyboard.package",
        "path_or_url": rel_p, "revision": package["revision"],
        "depends_on": ["narrative.package"], "checksum": artifacts.digest(path), "user_approved": False,
    })
    return {"registered": artifact_id, "status": record.get("status"), "sha256": artifacts.digest(path),
            "strict_check": checked.stdout.strip().splitlines()[-1:]}


def self_check_template(root, artifact_id, authorization_id, agent_id, output, observation=None, auto_accept=False):
    """Write the self-check skeleton (or completed report when observation is passed) with exact bindings."""
    from datetime import datetime, timezone
    if __package__:
        from .core.agent_acceptance import acceptance_binding, accept_artifact
        from .core.io import sha256_file
        from .core import revision_control as rc
    else:
        from core.agent_acceptance import acceptance_binding, accept_artifact
        from core.io import sha256_file
        from core import revision_control as rc
    entries = artifacts.load_manifest(root)["artifacts"]
    entry = next((e for e in entries if e["artifact_id"] == artifact_id), None)
    if entry is None:
        raise ValueError("先用 register 登记候选：" + artifact_id)
    profile_path = REPO / "skills/storyboard-planner/review-profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    target = _package_path(root, output)
    obs_txt = str(observation).strip() if observation else ""
    if target.exists() and not obs_txt and not auto_accept:
        raise ValueError(f"自查文件已存在，不覆盖：{output}")
    loc = f"{entry['path_or_url']}#scenes"
    report = {
        "schema_version": "1.0", "kind": "agent-self-check", "scope": "artifact",
        "verdict": "pass" if obs_txt else "",
        "authorization_id": authorization_id, "agent_id": agent_id,
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "review_profile_sha256": sha256_file(profile_path),
        **acceptance_binding(root, entry),
        "checks": [
            {
                "id": c["id"], "requirement": c.get("text", ""),
                "result": "pass" if obs_txt else "",
                "observation": obs_txt,
                "locator": loc if obs_txt else "",
            }
            for c in profile["production_artifact_checks"]
        ],
    }
    active = rc.show_change(root).get("active_change") or {}
    chg_id = active.get("change_id") if (active.get("candidate_receipt") or {}).get("artifact_id") == artifact_id else None
    chg_flag = f" --change-id '{chg_id}'" if chg_id else ""
    write_json(target, report)
    rel_sc = target.relative_to(root).as_posix()
    res = {"self_check": rel_sc, "checks": len(report["checks"]),
           "fill": "逐项填 result（pass / not_applicable+reason）、observation、locator；全部真实通过后把 verdict 改为 pass",
           "core_checks_must_pass": ["PLAN-PACKAGE", "PLAN-CONTINUITY"],
           "then": f".venv/bin/python scripts/stage_context_cli.py accept-artifact --project '{root}' "
                   f"--artifact-id '{artifact_id}'{chg_flag} --self-check-json '{rel_sc}'"}
    if auto_accept:
        if not obs_txt:
            raise ValueError("--auto-accept 须同时提供 --observation 真实复核结论")
        adopted = accept_artifact(argparse.Namespace(
            project=str(root), artifact_id=artifact_id, self_check_json=rel_sc,
            change_id=chg_id, preflight_json=None,
        ))
        res["accepted"] = adopted.get("ok", True)
        res["change_id"] = chg_id
    return res


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", type=Path, help=argparse.SUPPRESS)
    common.add_argument("--out", "--package", dest="out_sub", help=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", dest="project_root", type=Path)
    parser.add_argument("--out", "--package", dest="out_root", help="项目内导演包路径，如 03_导演/R07_storyboard-package.json")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("outline", parents=[common], help="只读全片紧凑大纲（供统筹预算，不灌入全片原文）")
    src = commands.add_parser("scene-source", parents=[common], help="只读当前要写段落的原文、对白和有效资产")
    src.add_argument("--paragraph", required=True, action="append", help="如 STP-01，可重复")
    pacts = commands.add_parser("plan-acts", parents=[common], help="自动生成锁死首尾交接棒与局部镜号规则的多幕并行计划")
    pacts.add_argument("--max-scenes-per-act", type=int, default=5)
    facts = commands.add_parser("finalize-acts", parents=[common], help="核验跨幕接缝并在需要时顺排连续镜号")
    facts.add_argument("--renumber-global", action="store_true", help="强制将各场镜号重排为全片递增序号")
    splan = commands.add_parser("save-plan", parents=[common], help="总导演保存分组并行计划（核对段落不重不漏与进出交接棒）")
    splan.add_argument("--plan", required=True, help="分组计划 JSON 文件；- 读标准输入")
    gbrief = commands.add_parser("group-brief", parents=[common], help="分场导演只读领取本组规则、交接棒与本组原文")
    gbrief.add_argument("--group-id", required=True)
    start = commands.add_parser("init", parents=[common])
    start.add_argument("--header", help="含 director_rules 的 JSON 文件，- 读标准输入（--from-package 时可省略）")
    start.add_argument("--from-package", help="可选：从旧版导演包克隆场次与规则并重绑当前编导稿（自动无损升级 v4->v5）")
    start.add_argument("--revision", help="默认沿用编导稿修订号")
    put = commands.add_parser("put-scene", parents=[common])
    put.add_argument("--scene", required=True, action="append", help="一个 scene（或数组）的 JSON 文件，可重复；- 读标准输入")
    more = commands.add_parser("append-shots", aliases=["patch-shots"], parents=[common], help="原地替换已有镜号或在场末追加新镜头")
    more.add_argument("--scene-id", required=True)
    more.add_argument("--shots", required=True, help="镜头数组 JSON 文件；- 读标准输入")
    change = commands.add_parser("set", parents=[common])
    change.add_argument("--header", required=True)
    commands.add_parser("status", parents=[common])
    reg = commands.add_parser("register", parents=[common], help="全片严格校验通过后登记候选（支持活动变更案）")
    reg.add_argument("--artifact-id", required=True)
    reg.add_argument("--change-id", help="可选：绑定活动变更案编号（省略时自动检测）")
    tpl = commands.add_parser("self-check-template", parents=[common], help="生成已绑定版本的自查报告（支持 --observation 与 --auto-accept）")
    tpl.add_argument("--artifact-id", required=True)
    tpl.add_argument("--authorization-id", required=True)
    tpl.add_argument("--agent-id", default="storyboard-planner")
    tpl.add_argument("--output", required=True, help="项目内路径，如 00_项目管理/导演R07自查.json")
    tpl.add_argument("--observation", help="可选：填入真实全片复核结论以直接生成 pass 自查报告")
    tpl.add_argument("--auto-accept", action="store_true", help="配合 --observation 一键完成 accept-artifact 正式采用")
    args = parser.parse_args(argv)
    proj_arg = getattr(args, "project", None) or getattr(args, "project_root", None)
    if proj_arg is None:
        parser.error("the following arguments are required: --project")
    args.out = getattr(args, "out_sub", None) or getattr(args, "out_root", None) or "03_导演/storyboard-package.json"
    root = proj_arg.expanduser().resolve(strict=True)
    if args.command == "register":
        result = register(root, args.out, args.artifact_id, args.change_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "self-check-template":
        result = self_check_template(
            root, args.artifact_id, args.authorization_id, args.agent_id, args.output,
            observation=args.observation, auto_accept=args.auto_accept,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "outline":
        print(json.dumps(outline(root), ensure_ascii=False, indent=2))
        return 0
    if args.command == "scene-source":
        print(json.dumps(scene_source(root, args.paragraph), ensure_ascii=False, indent=2))
        return 0
    if args.command == "group-brief":
        print(json.dumps(group_brief(root, args.out, args.group_id), ensure_ascii=False, indent=2))
        return 0
    with project_lock(root):
        if args.command == "init":
            hdr = _read_input(args.header, root) if args.header else None
            result = init(root, args.out, hdr, args.revision, args.from_package)
        elif args.command == "plan-acts":
            result = plan_acts(root, args.out, args.max_scenes_per_act)
        elif args.command == "finalize-acts":
            result = finalize_acts(root, args.out, renumber_global=args.renumber_global)
        elif args.command == "save-plan":
            result = save_plan(root, args.out, _read_input(args.plan, root))
        elif args.command == "put-scene":
            scenes = []
            for item in args.scene:
                value = _read_input(item, root)
                scenes.extend(value if isinstance(value, list) else [value])
            result = put_scenes(root, args.out, scenes)
        elif args.command in {"append-shots", "patch-shots"}:
            result = append_shots(root, args.out, args.scene_id, _read_input(args.shots, root))
        elif args.command == "set":
            result = set_header(root, args.out, _read_input(args.header, root))
        else:
            result = status(root, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
