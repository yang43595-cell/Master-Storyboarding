"""Show one finished ordinary image batch and record a decision on exact shown lists."""

import argparse
import hashlib
from datetime import datetime
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from PIL import Image

if __package__:
    from . import stage_context_cli as stage
    from .core import artifacts, asset_refs
    from .core.derived_character_boards import register
    from .core.io import project_lock, read_json, write_json, sha256_json
    from .core.project_state import write_project_state
    from .core.schema_contracts import require_schema
else:
    import stage_context_cli as stage
    from core import artifacts, asset_refs
    from core.derived_character_boards import register
    from core.io import project_lock, read_json, write_json, sha256_json
    from core.project_state import write_project_state
    from core.schema_contracts import require_schema

LOGICAL = "visual.asset.manifest"
SHOWN = Path("00_项目管理/资产交审")


def inside(root, name):
    path = artifacts.project_path(root, name)
    if not path.is_relative_to(root) or path == root:
        raise ValueError("路径越出项目")
    return path


def current(entries, logical):
    rows = [e for e in entries if e["logical_id"] == logical and e["status"] == "current"]
    if len(rows) > 1:
        raise ValueError("存在多个当前版本")
    return next(iter(rows), None)


def effective_requirements(root, narrative):
    story = read_json(inside(root, narrative["path_or_url"]))
    return {r["asset_id"]: r for r in asset_refs.effective(story, asset_refs.read_choices(root))}


def show(root, context, narrative):
    args = stage.parser().parse_args(
        [
            "handoff",
            "--project",
            str(root),
            "--to-skill",
            "asset-image-assistant",
            "--receive-batch",
            "--context-id",
            context,
        ]
    )
    batch = stage.handoff(args)
    basis = read_json(inside(root, f"00_项目管理/执行上下文/批次/{context}/task_basis.json"))
    if not any(
        a["record"]["artifact_id"] == narrative["artifact_id"]
        and a["file"]["sha256"] == narrative["checksum"]
        for a in basis["input_snapshot"]["artifacts"]
    ):
        raise ValueError("批次与当前编导版本不符")
    requirements = effective_requirements(root, narrative)
    original = read_json(inside(root, batch["receipt_path"]))
    items = []
    pending = []
    for row in batch["items"]:
        if row["status"] != "ready_for_review":
            pending.append(row)
            continue
        source = next(
            i
            for i in original["items"]
            if i["target"] == row["target"]
            and i.get("candidate_id") == row["candidate_id"]
            and str(i.get("sha256")).lower() == row["sha256"]
        )
        if not isinstance(source.get("purpose"), str) or not source["purpose"].strip():
            pending.append({"target": row["target"], "issue": "回执缺本图用途", "status": "pending"})
            continue
        req = requirements.get(row["target"].split("@")[0])
        if not req or req["image_decision"] not in ("需要制作", "复用现有素材"):
            pending.append({"target": row["target"], "issue": "当前有效需求已变化", "status": "pending"})
            continue
        items.append(
            {
                **row,
                "requirement_sha256": sha256_json(req),
                "final_output_id": source.get("final_output_id"),
                "candidate_revision": source.get("candidate_revision") or source.get("source_revision"),
                "limitations": source.get("limitations"),
                "path": inside(root, row["path"]).relative_to(root).as_posix(),
                "purpose": source["purpose"],
                "view": source.get("view"),
                "state": source.get("state"),
                "output_kind": source.get("output_kind")
                or basis["task_request"].get("image_output_kinds", {}).get(row["target"]),
            }
        )
    if not items:
        raise ValueError("本批暂无可审成图")
    snapshot = dict(
        context=context, narrative=narrative["checksum"], narrative_id=narrative["artifact_id"], items=items
    )
    key = sha256_json(snapshot)
    write_json(inside(root, SHOWN / (key + ".json")), snapshot)
    return dict(shown=key, items=items, pending=pending)


def collect_output(root, rel, staging, target, item, requirements, out, message, receipt):
    ast, output = target.split("@")
    if ast not in requirements:
        raise ValueError("当前资产需求已变更 " + target)
    req = requirements[ast]
    if sha256_json(req) != item["requirement_sha256"] or req["image_decision"] not in (
        "需要制作",
        "复用现有素材",
    ):
        raise ValueError("当前资产需求不支持确认 " + target)
    source = inside(root, item["path"])
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError("待审图片字节已变化 " + target)
    name = sha256_json(target)[:10] + "_" + source.name
    (staging / name).write_bytes(raw)
    with Image.open(staging / name) as image:
        width, height = image.size
        fmt = image.format.lower()
        image.verify()
    path = (rel / name).as_posix()
    record = next((r for r in out["assets"] if r["asset_id"] == ast), None)
    if record is None:
        record = dict(
            asset_id=ast,
            name=req["name"],
            category=req["category"],
            status="confirmed",
            paragraph_scope=req["source_paragraph_ids"],
            shot_scope=[],
            interactions=req["interactions"],
            required_states=req["required_states"],
            must_inherit=req["identity_constraints"],
            must_not_inherit=[],
            display_surfaces=[],
            orientations=[],
            reference_paths=list(req.get("reference_paths", [])),
            candidates=[],
            confirmed_outputs=[],
        )
        if req.get("character_type"):
            record["character_type"] = req["character_type"]
        out["assets"].append(record)
        out["source_asset_requirement_ids"].append(ast)
    record["status"] = "confirmed"
    used = {r["candidate_id"] for r in record["candidates"]}
    serial = 1
    while f"{ast}-C{serial:02}" in used:
        serial += 1
    candidate = f"{ast}-C{serial:02}"
    record["candidates"].append(
        dict(
            candidate_id=candidate,
            path=path,
            status="awaiting_user_review",
            source_references=[item["path"]],
            technical_qc=dict(
                path_exists=True,
                format=fmt,
                width=width,
                height=height,
                naming_valid=True,
                reference_traceable=True,
            ),
        )
    )
    selected = dict(
        candidate_id=candidate, path=path, user_confirmation_ref=receipt.as_posix() + "#" + output
    )
    record["confirmed_outputs"] = [r for r in record.get("confirmed_outputs", []) if r["output_id"] != output]
    record["confirmed_outputs"].append(
        dict(
            selected,
            output_id=output,
            purpose=item["purpose"] + ("。已披露偏差：" + item["issue"] if item.get("issue") else ""),
            view=item.get("view") or "见已确认成图",
            state=item.get("state") or output,
            output_kind=item["output_kind"] or output,
        )
    )
    record["confirmed_asset"] = {k: v for k, v in record["confirmed_outputs"][0].items() if k in selected}
    return dict(
        ast_id=ast,
        output_id=output,
        confirmation=dict(
            message,
            id=output,
            path=path,
            image_sha256=item["sha256"],
            source_candidate=item["path"],
            candidate_id=item["candidate_id"],
            purpose=item["purpose"],
            visual_status_before_user_review=item["visual_status"],
            accepted_deviation=item.get("issue", ""),
            limitations=item.get("limitations"),
        ),
    )


def confirm(root, keys, message, narrative, entries):
    if not all(isinstance(message.get(k), str) and message[k].strip() for k in ("at", "quote", "review_ref")):
        raise ValueError("缺用户确认原话、时间或消息引用")
    if datetime.fromisoformat(message["at"]).utcoffset() is None or message.get("scope") != "all_shown":
        raise ValueError("确认须绑定已展示清单与含时区时间")
    rows = {}
    for key in keys:
        if not re.fullmatch("[0-9a-f]{64}", key):
            raise ValueError("交审编号无效")
        packet = read_json(inside(root, SHOWN / (key + ".json")))
        if sha256_json(packet) != key or (packet["narrative"], packet["narrative_id"]) != (
            narrative["checksum"],
            narrative["artifact_id"],
        ):
            raise ValueError("交审内容或编导版本已变化")
        for item in packet["items"]:
            if item["target"] in rows and rows[item["target"]] != item:
                raise ValueError("同一输出展示了多个版本，请指定实际通过的清单")
            rows[item["target"]] = item
    key = sha256_json([sorted(set(keys)), message])
    ident = "visual-assets-chat-" + key
    rel = Path(asset_refs.MASTER_DIR) / ident
    folder = inside(root, rel)
    dest = folder / "visual-asset-manifest.json"
    receipt = rel / "用户确认记录.json"
    old = next((e for e in entries if e["artifact_id"] == ident), None)
    selection = current(entries, LOGICAL)
    if old and old["status"] not in ("candidate", "current", "superseded"):
        raise ValueError("此确认已失效，请重新交审")
    if old and old["status"] in ("current", "superseded"):
        saved = read_json(inside(root, receipt))
        confirmed = {r["ast_id"] + "@" + r["output_id"]: r["confirmation"] for r in saved["items"]}
        if confirmed.keys() != rows.keys() or any(
            any(confirmed[t].get(k) != message[k] for k in ("at", "quote", "review_ref", "scope"))
            or confirmed[t]["image_sha256"] != item["sha256"]
            or confirmed[t]["accepted_deviation"] != item.get("issue", "")
            or confirmed[t]["source_candidate"] != item["path"]
            for t, item in rows.items()
        ):
            raise ValueError("已登记的逐图确认与交审内容不符")
        if sorted(saved["shown"]) != sorted(keys) or any(
            artifacts.digest(inside(root, row["confirmation"]["path"])) != row["confirmation"]["image_sha256"]
            for row in saved["items"]
        ):
            raise ValueError("已确认图片或确认记录发生变化")
        if artifacts.digest(dest) != old["checksum"]:
            raise ValueError("已确认清单字节变化")
    else:
        for e in entries:
            if e["artifact_id"] == ident or not e["artifact_id"].startswith("visual-assets-chat-"):
                continue
            if e["status"] == "candidate":
                raise ValueError("另一次资产确认待恢复，请按原交审编号和消息重试")
            other = read_json(inside(root, e["confirmation_ref"]))
            if any(r["confirmation"]["review_ref"] == message["review_ref"] for r in other["items"]):
                raise ValueError("同一条用户消息已用于其他交审范围")
        story = read_json(inside(root, narrative["path_or_url"]))
        requirements = effective_requirements(root, narrative)
        revisions = [
            int(e["revision"][1:])
            for e in entries
            if e["logical_id"] == LOGICAL and re.fullmatch(r"R[0-9]+", e.get("revision", ""))
        ]
        revision = old["revision"] if old else f"R{max(revisions, default=0) + 1:02}"
        out = dict(
            schema_version="2.0",
            project_id=story["project_id"],
            revision=revision,
            source_narrative=dict(
                artifact_id=narrative["artifact_id"],
                logical_id="narrative.package",
                path=narrative["path_or_url"],
                revision=narrative["revision"],
            ),
            source_asset_requirement_ids=[],
            assets=[],
            scope_gaps=[],
        )
        if selection:
            previous = inside(root, selection["path_or_url"])
            if artifacts.digest(previous) != selection["checksum"]:
                raise ValueError("当前资产清单字节已变化")
            prior = read_json(previous)
            if prior["source_narrative"]["artifact_id"] != narrative["artifact_id"]:
                raise ValueError("当前资产表属于另一编导版本，须核对沿用范围")
            out.update(prior, revision=out["revision"])
        confirmed = []
        with TemporaryDirectory(dir=root) as tmp:
            staging = Path(tmp)
            for target, item in rows.items():
                confirmed.append(
                    collect_output(root, rel, staging, target, item, requirements, out, message, receipt)
                )
            for target, item in rows.items():
                ast, output = target.split("@")
                final = item.get("final_output_id")
                gap_id = f"GAP-{ast}-{output}"
                out["scope_gaps"] = [g for g in out["scope_gaps"] if g["gap_id"] != gap_id]
                if not final or final == output:
                    continue
                gap_id = f"GAP-{ast}-{final}"
                if any(
                    o["output_id"] == final
                    for a in out["assets"]
                    if a["asset_id"] == ast
                    for o in a["confirmed_outputs"]
                ) or any(g["gap_id"] == gap_id for g in out["scope_gaps"]):
                    continue
                out["scope_gaps"].append(
                    dict(
                        gap_id=gap_id,
                        requested_item=final,
                        reason="本次确认覆盖拼版源图，合成输出待交付",
                        source_ref=receipt.as_posix(),
                        production_stopped=True,
                    )
                )
            require_schema(out, "visual-asset-manifest.schema.json", label="对话确认资产")
            write_json(staging / dest.name, out)
            write_json(staging / receipt.name, dict(shown=keys, items=confirmed))
            checksum = artifacts.digest(staging / dest.name)
            if old and old["checksum"] != checksum:
                raise ValueError("待恢复的资产清单与原登记不一致")
            if old is None:
                artifacts.register_workspace_artifact(
                    root,
                    dict(
                        artifact_id=ident,
                        logical_id=LOGICAL,
                        type="visual-asset-manifest",
                        revision=out["revision"],
                        path_or_url=(rel / dest.name).as_posix(),
                        depends_on=["narrative.package"],
                        user_approved=True,
                        checksum=checksum,
                        confirmation_ref=receipt.as_posix(),
                    ),
                )
            if folder.exists():
                if any(artifacts.digest(folder / f.name) != artifacts.digest(f) for f in staging.iterdir()):
                    raise ValueError("已有导出与本次确认冲突")
            else:
                folder.parent.mkdir(parents=True, exist_ok=True)
                staging.rename(folder)
            artifacts.promote_candidate(root, LOGICAL, ident)
    if not old or old["status"] != "superseded":
        # Promotion can reconcile the on-disk selection. Use its new state version.
        state = artifacts.load_project_state(root)
        asset_ids = {e["artifact_id"] for e in entries if e["logical_id"] == LOGICAL}
        state["current_artifacts"] = [i for i in state["current_artifacts"] if i not in asset_ids] + [ident]
        if not any(ident in r.get("artifact_ids", []) for r in state["user_confirmations"]):
            state["user_confirmations"].append(
                dict(
                    at=message["at"],
                    artifact_ids=[ident],
                    confirmation_ref=receipt.as_posix(),
                    method="对话确认",
                )
            )
        write_project_state(root, state)
    return dict(artifact_id=ident, count=len(rows), manifest=str(dest))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show").add_argument("--context-id", required=True)
    decide = commands.add_parser("confirm")
    decide.add_argument("--shown", action="append", required=True)
    decide.add_argument("--confirmation-json", required=True, type=Path)
    commands.add_parser("register-derived-boards").add_argument("--board-receipt", required=True, type=Path)
    args = parser.parse_args()
    root = args.project.resolve(strict=True)
    with project_lock(root):
        stage.require_production(root)
        entries = artifacts.load_manifest(root)["artifacts"]
        state = artifacts.load_project_state(root)
        narrative = current(entries, "narrative.package")
        if (
            not narrative
            or not artifacts.artifact_is_adopted(narrative, state, root)
            or artifacts.digest(inside(root, narrative["path_or_url"])) != narrative["checksum"]
        ):
            raise ValueError("缺当前已采用的准确编导稿")
        if args.command == "show":
            result = show(root, args.context_id, narrative)
        elif args.command == "confirm":
            result = confirm(root, args.shown, read_json(args.confirmation_json), narrative, entries)
        else:
            plan = read_json(inside(root, args.board_receipt))
            result = register(root, plan, narrative, entries, current(entries, LOGICAL))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
