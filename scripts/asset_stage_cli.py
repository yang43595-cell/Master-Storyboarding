#!/usr/bin/env python3
"""Fast-track CLI for Stage 2 (asset-image-assistant): batch candidate staging, show-round, 1x4 board composition, and asset exclusion."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import stage_context as stage
from core.artifacts import load_manifest
from core.project_kickoff import REQUEST_PATH
from core.stage_guidance import build_stage_guidance

REPO = Path(__file__).resolve().parent.parent

VIEW_BY_KIND = {
    "人物单图": "front_full",
    "大头照": "front_headshot",
    "服装三视图": "1x3_turnaround",
    "场景母图": "wide_establishing",
    "道具母图": "three_view",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_stage_and_show(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    manifest = load_manifest(project)
    nar_entry = next((e for e in manifest.get("artifacts", []) if e.get("logical_id") == "narrative.package" and e.get("status") == "current"), None)
    if not nar_entry:
        raise SystemExit("缺少当前正式采用的 narrative.package")
    raw = _load_json(Path(args.input).resolve())
    candidates = raw if isinstance(raw, list) else list(raw.get("candidates") or [])
    if not candidates:
        raise SystemExit("candidates 不能为空")
    cand_dir = project / "04_资产/候选/复用盘点"
    cand_dir.mkdir(parents=True, exist_ok=True)
    all_targets: list[str] = []
    staged_rows: list[dict[str, Any]] = []
    for idx, item in enumerate(candidates, 1):
        target = str(item["target"])
        all_targets.append(target)
        src_p = Path(str(item["src"]))
        if not src_p.is_absolute():
            src_p = (project / src_p).resolve()
        dst_name = item.get("dst_name") or src_p.name
        dst_p = cand_dir / dst_name
        if src_p.resolve() != dst_p.resolve():
            shutil.copy2(src_p, dst_p)
        kind = str(item.get("kind") or "道具母图")
        rev = str(item.get("revision") or nar_entry["revision"])
        row: dict[str, Any] = {
            "target": target,
            "candidate_id": str(item.get("candidate_id") or f"CAND-{idx:03d}-{rev}"),
            "candidate_revision": rev,
            "path": dst_p.relative_to(project).as_posix(),
            "sha256": _sha256(dst_p),
            "output_kind": kind,
            "purpose": str(item.get("purpose") or f"{target} 正式候选图"),
            "view": str(item.get("view") or VIEW_BY_KIND.get(kind, "front_full")),
            "state": str(item.get("state") or "default"),
        }
        if item.get("final_output_id"):
            row["final_output_id"] = str(item["final_output_id"])
        staged_rows.append(row)

    req_file = project / REQUEST_PATH
    proj_req = _load_json(req_file) if req_file.is_file() else {}
    proj_req["asset_review_targets"] = all_targets
    _save_json(req_file, proj_req)

    receipt_rels: list[str] = []
    for b_idx, start in enumerate(range(0, len(staged_rows), 8), 1):
        chunk = staged_rows[start:start + 8]
        b_targets = [r["target"] for r in chunk]
        b_kinds = {r["target"]: r["output_kind"] for r in chunk}
        src_req_rel = f"00_项目管理/执行上下文/直做图片请求_源/batch_{b_idx:02d}_request.json"
        src_req_path = project / src_req_rel
        _save_json(src_req_path, {
            "schema_version": "1.0",
            "project_name": project.name,
            "stage": "asset-image-assistant",
            "objective": f"资产候选批次 {b_idx}",
            "image_targets": b_targets,
            "image_output_kinds": b_kinds,
            "asset_method_scopes": {"style": False, "character": True, "body": False, "scene": True, "object": True},
        })
        _, req_sha = stage._request_snapshot(src_req_path)
        rcp_rel = f"00_项目管理/执行上下文/直做图片回执/batch_{b_idx:02d}_receipt.json"
        _save_json(project / "00_项目管理/执行上下文/直做图片请求" / f"{req_sha}.json", {
            "skill": "asset-image-assistant",
            "request": src_req_rel,
            "request_sha256": req_sha,
            "narrative_id": nar_entry["artifact_id"],
            "narrative_sha256": nar_entry["checksum"],
            "targets": b_targets,
            "image_output_kinds": b_kinds,
            "receipt": rcp_rel,
        })
        _save_json(project / rcp_rel, {
            "schema_version": "1.0",
            "request_sha256": req_sha,
            "batch_id": f"asset-batch-{b_idx:02d}",
            "executor": "asset-image-assistant",
            "items": chunk,
        })
        receipt_rels.append(rcp_rel)

    cmd = [sys.executable, str(REPO / "scripts/chat_asset_review.py"), "--project", str(project), "--no-open", "show-round"]
    for r in receipt_rels:
        cmd.extend(["--receipt", r])
    res = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"show-round 失败：{res.stderr.strip() or res.stdout.strip()}")
    parsed = json.loads(res.stdout)
    parsed["must_read"] = build_stage_guidance(project, "asset-image-assistant")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    return 0


def cmd_outline(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    manifest = load_manifest(project)
    nar_entry = next((e for e in manifest.get("artifacts", []) if e.get("logical_id") == "narrative.package" and e.get("status") == "current"), None)
    assets = []
    if nar_entry:
        nar = _load_json(project / nar_entry["path_or_url"])
        assets = [
            {"asset_id": a["asset_id"], "category": a.get("category"), "name": a.get("name"), "image_decision": a.get("image_decision")}
            for a in nar.get("asset_requirements") or []
        ]
    print(json.dumps({
        "must_read": build_stage_guidance(project, "asset-image-assistant"),
        "narrative_id": nar_entry["artifact_id"] if nar_entry else None,
        "asset_requirements": assets,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_confirm_round(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    msg = {
        "scope": "all_shown",
        "quote": args.quote,
        "at": now_iso,
        "review_ref": args.review_ref or f"chat-confirm-{args.shown[:12]}",
    }
    cmd = [
        sys.executable, str(REPO / "scripts/chat_asset_review.py"),
        "--project", str(project), "--no-open",
        "confirm", "--shown", args.shown, "--confirmation-json", "-",
    ]
    res = subprocess.run(cmd, cwd=REPO, input=json.dumps(msg, ensure_ascii=False), capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"confirm 失败：{res.stderr.strip() or res.stdout.strip()}")
    out = {"round_confirmed": json.loads(res.stdout)}
    if args.compose_boards:
        comp = subprocess.run(
            [sys.executable, str(REPO / "scripts/chat_asset_review.py"), "--project", str(project), "--no-open", "compose-boards"],
            cwd=REPO, capture_output=True, text=True,
        )
        if comp.returncode != 0:
            raise SystemExit(f"compose-boards 失败：{comp.stderr.strip() or comp.stdout.strip()}")
        comp_json = json.loads(comp.stdout)
        out["composed_boards"] = comp_json
        if args.auto_confirm_boards and comp_json.get("shown"):
            b_shown = str(comp_json["shown"])
            b_msg = {
                "scope": "all_shown",
                "quote": args.boards_quote or "人物1x4拼版全部通过",
                "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "review_ref": f"chat-confirm-boards-{b_shown[:12]}",
            }
            b_res = subprocess.run(
                [sys.executable, str(REPO / "scripts/chat_asset_review.py"), "--project", str(project), "--no-open",
                 "confirm", "--shown", b_shown, "--confirmation-json", "-"],
                cwd=REPO, input=json.dumps(b_msg, ensure_ascii=False), capture_output=True, text=True,
            )
            if b_res.returncode != 0:
                raise SystemExit(f"拼版 confirm 失败：{b_res.stderr.strip() or b_res.stdout.strip()}")
            out["boards_confirmed"] = json.loads(b_res.stdout)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_rebind_unchanged(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    manifest = load_manifest(project)
    nar_entry = next((e for e in manifest.get("artifacts", []) if e.get("logical_id") == "narrative.package" and e.get("status") == "current"), None)
    if not nar_entry:
        raise SystemExit("缺少当前正式采用的 narrative.package")
    bind_dir = project / "00_项目管理/执行上下文/直做图片请求"
    receipt_rels: list[str] = []
    for bp in sorted(bind_dir.glob("*.json")):
        b_doc = _load_json(bp)
        b_doc["narrative_id"] = nar_entry["artifact_id"]
        b_doc["narrative_sha256"] = nar_entry["checksum"]
        _save_json(bp, b_doc)
        if b_doc.get("receipt"):
            receipt_rels.append(str(b_doc["receipt"]))
    if not receipt_rels:
        raise SystemExit("未找到可重绑的直做图片请求与回执记录")
    cmd = [sys.executable, str(REPO / "scripts/chat_asset_review.py"), "--project", str(project), "--no-open", "show-round"]
    for r in sorted(set(receipt_rels)):
        cmd.extend(["--receipt", r])
    res = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"rebind show-round 失败：{res.stderr.strip() or res.stdout.strip()}")
    shown_info = json.loads(res.stdout)
    args.shown = str(shown_info["shown"])
    return cmd_confirm_round(args)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p0 = sub.add_parser("outline", help="输出本关产品/规范必读小抄与待制作资产清单")
    p0.add_argument("--project", required=True)
    p0.set_defaults(func=cmd_outline)

    p1 = sub.add_parser("stage-and-show", help="一键复制候选图、切片直做批次回执并运行 show-round")
    p1.add_argument("--project", required=True)
    p1.add_argument("--input", required=True)
    p1.set_defaults(func=cmd_stage_and_show)

    p2 = sub.add_parser("confirm-round", help="确认整轮展示图，并可选一键完成 1x4 人物拼版与确认")
    p2.add_argument("--project", required=True)
    p2.add_argument("--shown", required=True)
    p2.add_argument("--quote", default="全部通过")
    p2.add_argument("--review-ref")
    p2.add_argument("--compose-boards", action="store_true")
    p2.add_argument("--auto-confirm-boards", action="store_true")
    p2.add_argument("--boards-quote")
    p2.set_defaults(func=cmd_confirm_round)

    p3 = sub.add_parser("rebind-unchanged", help="编导升版但资产需求未变时，将既有已确认候选重绑至新版叙事包并确认")
    p3.add_argument("--project", required=True)
    p3.add_argument("--quote", default="资产不变沿用原确认")
    p3.add_argument("--review-ref")
    p3.add_argument("--compose-boards", action="store_true", default=True)
    p3.add_argument("--auto-confirm-boards", action="store_true", default=True)
    p3.add_argument("--boards-quote")
    p3.set_defaults(func=cmd_rebind_unchanged)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
