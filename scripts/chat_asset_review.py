"""Prepare ordinary image reviews and record decisions on the exact images shown."""

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
    from .core import artifacts, asset_refs, character_boards as boards, production_handoff
    from .core.asset_batch_receipt import DIRECT_REQUESTS, summarize_asset_batch_receipt, validate_asset_review_targets
    from .core.io import project_lock, read_json, write_json, sha256_json, utc_now
    from .core.project_state import write_project_state
    from .core.schema_contracts import require_schema
else:
    import stage_context_cli as stage
    from core import artifacts, asset_refs, character_boards as boards, production_handoff
    from core.asset_batch_receipt import DIRECT_REQUESTS, summarize_asset_batch_receipt, validate_asset_review_targets
    from core.io import project_lock, read_json, write_json, sha256_json, utc_now
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


def effective_requirements(root, narrative, *, story=None):
    if story is None:
        story = read_json(inside(root, narrative["path_or_url"]))
    return {r["asset_id"]: r for r in asset_refs.effective(story, asset_refs.read_choices(root))}


def check_basis(basis, narrative, context):
    if (basis.get("skill") != "asset-image-assistant" or basis.get("context_id") != context
            or basis["input_snapshot"].get("candidate_test")):
        raise ValueError("图片批次任务依据与当前项目或执行身份不符")
    if not any(
        a["record"]["artifact_id"] == narrative["artifact_id"]
        and a["file"]["sha256"] == narrative["checksum"]
        for a in basis["input_snapshot"]["artifacts"]
    ):
        raise ValueError("批次与当前编导版本不符")


def batch_items(root, context, narrative, requirements):
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
    basis = read_json(stage._context_path(root, context, "task_basis.json"))
    check_basis(basis, narrative, context)
    return review_items(root, batch, requirements, basis["task_request"].get("image_output_kinds", {}))


def direct_items(root, receipt, narrative, requirements):
    receipt = inside(root, receipt)
    document = read_json(receipt)
    digest = document.get("request_sha256") if isinstance(document, dict) else None
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest) or document.get("context_id"):
        raise ValueError("主对话直做回执缺本阶段准确请求哈希，或属于另一执行批次")
    binding_path = inside(root, DIRECT_REQUESTS / (digest + ".json"))
    if binding_path.is_file():
        binding = read_json(binding_path)
        if (binding.get("request_sha256") != digest
                or binding.get("skill") != "asset-image-assistant"
                or (binding.get("narrative_id"), binding.get("narrative_sha256"))
                != (narrative["artifact_id"], narrative["checksum"])):
            raise ValueError("直做批次与冻结的项目、请求或编导版本不符")
        request, request_sha = stage._request_snapshot(inside(root, binding["request"]))
        targets = request.get("image_targets")
        kinds = request.get("image_output_kinds", {})
        if digest != request_sha or targets != binding["targets"] or kinds != binding["image_output_kinds"]:
            raise ValueError("直做回执与冻结的批次请求不符")
    else:
        basis = read_json(stage._context_path(root, None, "task_basis.json"))
        check_basis(basis, narrative, None)
        request_path = Path(basis["request_path"])
        if request_path.is_absolute():
            request_path = request_path.relative_to(basis["reading_manifest"]["project_path"])
        request, request_sha = stage._request_snapshot(inside(root, request_path))
        frozen = basis["task_request"]
        semantic = stage._semantic_request(stage.bind_stage_request(request, stage.load_kickoff(root)), basis["skill"])
        if digest != request_sha or semantic != frozen:
            raise ValueError("直做回执与本阶段冻结请求不符")
        targets = frozen["image_targets"]
        kinds = frozen.get("image_output_kinds", {})
    batch = summarize_asset_batch_receipt(root, receipt, targets, binding={"request_sha256": digest, "context_id": None},
                                          document=document)
    return review_items(root, batch, requirements, kinds, document)


def review_items(root, batch, requirements, kinds, original=None):
    if not any(row["status"] == "ready_for_review" for row in batch["items"]):
        return [], batch["items"]
    if original is None:
        original = read_json(inside(root, batch["receipt_path"]))
    items = []
    pending = []
    for row in batch["items"]:
        if row["status"] != "ready_for_review":
            pending.append(row)
            continue
        source = next((
            i
            for i in original["items"]
            if i["target"] == row["target"]
            and i.get("candidate_id") == row["candidate_id"]
            and str(i.get("sha256")).lower() == row["sha256"]
        ), None)
        if source is None:
            pending.append({"target": row["target"], "issue": "实看回执在接收期间已更新", "status": "pending"})
            continue
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
                or kinds.get(row["target"]),
            }
        )
    return items, pending


def store_shown(root, narrative, items, **scope):
    snapshot = dict(narrative=narrative["checksum"], narrative_id=narrative["artifact_id"], items=items, **scope)
    key = sha256_json(snapshot)
    write_json(inside(root, SHOWN / (key + ".json")), snapshot)
    return dict(shown=key, items=items, pending=[])


def show(root, context, narrative):
    items, pending = batch_items(root, context, narrative, effective_requirements(root, narrative))
    if not items:
        raise ValueError("本批暂无可审成图")
    return dict(store_shown(root, narrative, items, context=context), pending=pending)


def round_targets(root, targets_path=None):
    request_path = inside(root, stage.REQUEST_PATH)
    request = read_json(request_path) if request_path.is_file() else {}
    registered = request.get("asset_review_targets")
    targets = registered
    if targets_path:
        plan = read_json(inside(root, targets_path))
        targets = plan.get("asset_review_targets", plan.get("image_targets")) if isinstance(plan, dict) else None
    targets = validate_asset_review_targets(targets)
    if registered is not None and set(targets) != set(validate_asset_review_targets(registered)):
        raise ValueError("指定范围与项目已登记的整轮交审目标不一致")
    return targets


def approved_versions(images):
    return {f"{ast}@{o['output_id']}": {"sha256": o["sha256"], "purpose": o["purpose"]}
            for ast, asset in images.items() for o in asset.get("outputs", []) if o.get("sha256")}


def reviewed_purpose(item):
    return item["purpose"] + ("。已披露偏差：" + item["issue"] if item.get("issue") else "")


def rejected_targets(root, targets):
    decisions, issues = asset_refs._output_decisions(root)
    pairs = {tuple(t.split("@")) for t in targets}
    for issue in issues:
        if any(issue.get("asset_id") in (None, ast) and issue.get("output_id") in (None, "", output)
               for ast, output in pairs):
            raise ValueError(issue["message"])
    rejected = {f"{ast}@{output}" for (ast, output), decision in decisions.items() if decision["status"] == "rejected"}
    if excluded := rejected.intersection(targets):
        raise ValueError("清单含项目已排除的输出，请使用本轮替代槽：" + "、".join(sorted(excluded)))
    return rejected


def show_round(root, contexts, narrative, targets_path=None, receipts=()):
    targets = round_targets(root, targets_path)
    expected = set(targets)
    rejected = rejected_targets(root, expected)
    story = read_json(inside(root, narrative["path_or_url"]))
    assets, _, waiting = production_handoff._asset_readiness(root, story, file_digest=artifacts.cache_file_digests())
    inventory, _ = production_handoff.unlisted_asset_work(root, assets, waiting, {t.split("@")[0] for t in targets})
    if inventory:
        raise ValueError("整轮清单遗漏仍需人审的资产：" + "、".join(sorted(inventory)))
    for target in targets:
        ast, output = target.split("@")
        asset = assets.get(ast, {})
        if asset.get("state") != "needs_image" or output in asset.get("required_board_ids", []):
            raise ValueError("整轮交审目标须为当前需人审的准确源图：" + target)
    requirements = effective_requirements(root, narrative, story=story)
    contexts = sorted(set(contexts))
    receipts = sorted({inside(root, path).relative_to(root).as_posix() for path in receipts})
    batches = [batch_items(root, c, narrative, requirements) for c in contexts]
    batches.extend(direct_items(root, path, narrative, requirements) for path in receipts)
    ready, pending = {}, {}
    for items, blocked in batches:
        for row in items:
            target = row["target"]
            if target in ready and ready[target] != row:
                raise ValueError("同一目标有多份待审版本，请指定实际批次：" + target)
            ready[target] = row
        for row in blocked:
            pending[row["target"]] = row
    approved = approved_versions({ast: asset["image"] for ast, asset in assets.items()})
    reused = {}
    same_version = {t for t, row in ready.items()
                    if approved.get(t) == dict(sha256=row["sha256"], purpose=reviewed_purpose(row))}
    for target, previous in approved.items():
        if target in ready and target not in same_version:
            continue
        if target not in pending:
            reused[target] = previous
    extra = (ready.keys() | pending.keys()) - expected - reused.keys() - rejected
    if extra:
        raise ValueError("执行批次含本轮清单外且未经同版确认的目标：" + "、".join(sorted(extra)))
    reused = {t: reused[t] for t in targets if t in reused}
    missing = []
    for target in targets:
        if target not in reused and target not in ready:
            missing.append(pending.get(target) or dict(target=target, status="pending", issue="尚缺本轮目标的成图回执"))
        elif target in pending and target in same_version:
            missing.append(pending[target])
    if missing:
        return dict(status="pending", pending=missing)
    items = [ready[t] for t in targets if t not in reused]
    gaps = boards.round_board_source_gaps(root, assets, items)
    if gaps:
        return dict(status="pending", pending=gaps)
    if len(reused) == len(targets):
        return dict(status="already_confirmed", confirmed=targets, items=[], pending=[])
    scope = dict(targets=targets, reused=reused, targets_path=(
        inside(root, targets_path).relative_to(root).as_posix() if targets_path else None
    ))
    return store_shown(root, narrative, items, contexts=contexts, receipts=receipts, review_round=scope)


def append_candidate(record, path, sources, width, height, fmt):
    ast = record["asset_id"]
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
            source_references=sources,
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
    return candidate


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
    candidate = append_candidate(record, path, [item["path"]], width, height, fmt)
    if item.get("output_kind") in {boards.HEAD, *boards.CLOTHES}:
        boards.invalidate_changed_source(root, record, output, item["sha256"], out["scope_gaps"], receipt.as_posix(), {})
    selected = dict(
        candidate_id=candidate, path=path, user_confirmation_ref=receipt.as_posix() + "#" + output
    )
    record["confirmed_outputs"] = [r for r in record.get("confirmed_outputs", []) if r["output_id"] != output]
    record["confirmed_outputs"].append(
        dict(
            selected,
            output_id=output,
            purpose=reviewed_purpose(item),
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
    if datetime.fromisoformat(message["at"]).utcoffset() is None or message.get("scope") not in ("all_shown", "selected"):
        raise ValueError("确认须绑定已展示清单与含时区时间")
    rows = {}
    round_scope = None
    for key in keys:
        if not re.fullmatch("[0-9a-f]{64}", key):
            raise ValueError("交审编号无效")
        packet = read_json(inside(root, SHOWN / (key + ".json")))
        if sha256_json(packet) != key or (packet["narrative"], packet["narrative_id"]) != (
            narrative["checksum"],
            narrative["artifact_id"],
        ):
            raise ValueError("交审内容或编导版本已变化")
        if "review_round" in packet:
            if len(keys) != 1:
                raise ValueError("整轮交审只使用本轮唯一展示编号")
            round_scope = packet["review_round"]
        for item in packet["items"]:
            if item["target"] in rows and rows[item["target"]] != item:
                raise ValueError("同一输出展示了多个版本，请指定实际通过的清单")
            rows[item["target"]] = item
    if message["scope"] == "selected":
        selected = validate_asset_review_targets(message.get("targets"))
        if round_scope is None or not set(selected) <= rows.keys():
            raise ValueError("部分通过须明确列出本轮已展示且获通过的准确目标")
        rows = {target: item for target, item in rows.items() if target in selected}
        message = {**message, "targets": sorted(selected)}
    elif "targets" in message:
        raise ValueError("指定部分通过项时须用 selected 范围")
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
        if round_scope:
            if set(round_targets(root, round_scope["targets_path"])) != set(round_scope["targets"]):
                raise ValueError("整轮交审目标已变化，请按当前清单重新展示")
            rejected_targets(root, rows)
            reused = round_scope["reused"]
            if reused and approved_versions(asset_refs.confirmed_images(root, requests=reused)) != reused:
                raise ValueError("交审后沿用的已确认图已变化，请重新展示")
        for e in entries:
            if e["artifact_id"].startswith("visual-boards-") and e["status"] == "candidate":
                raise ValueError("已有拼版待恢复，请先按原实看编号续作")
            if e["artifact_id"] == ident or not e["artifact_id"].startswith("visual-assets-chat-"):
                continue
            if e["status"] == "candidate":
                raise ValueError("另一次资产确认待恢复，请按原交审编号和消息重试")
            other = read_json(inside(root, e["confirmation_ref"]))
            if any(r["confirmation"]["review_ref"] == message["review_ref"] for r in other["items"]):
                raise ValueError("同一条用户消息已用于其他交审范围")
        story = read_json(inside(root, narrative["path_or_url"]))
        requirements = effective_requirements(root, narrative, story=story)
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
        user_evidence = {k: v for k, v in message.items() if k != "targets"}
        with TemporaryDirectory(dir=root) as tmp:
            staging = Path(tmp)
            for target, item in rows.items():
                confirmed.append(
                    collect_output(root, rel, staging, target, item, requirements, out, user_evidence, receipt)
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
            publish(root, rel, ident, out, staging, receipt, dict(shown=keys, items=confirmed), old)
    if not old or old["status"] != "superseded":
        select_export(root, entries, ident, receipt, message["at"], "对话确认")
    return dict(artifact_id=ident, count=len(rows), manifest=str(dest))


def publish(root, rel, ident, out, staging, receipt, evidence, old):
    folder = inside(root, rel)
    dest = folder / "visual-asset-manifest.json"
    require_schema(out, "visual-asset-manifest.schema.json", label="对话确认资产")
    write_json(staging / dest.name, out)
    write_json(staging / receipt.name, evidence)
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


def select_export(root, entries, ident, receipt, at, method):
    # Promotion can reconcile the on-disk selection. Use its new state version.
    state = artifacts.load_project_state(root)
    asset_ids = {e["artifact_id"] for e in entries if e["logical_id"] == LOGICAL}
    selected = [i for i in state["current_artifacts"] if i in asset_ids or i == ident]
    confirmed = any(ident in row.get("artifact_ids", []) for row in state["user_confirmations"])
    if selected == [ident] and confirmed:
        return
    state["current_artifacts"] = [i for i in state["current_artifacts"] if i not in asset_ids and i != ident] + [ident]
    if not confirmed:
        state["user_confirmations"].append(
            dict(
                at=at,
                artifact_ids=[ident],
                confirmation_ref=receipt.as_posix(),
                method=method,
            )
        )
    write_project_state(root, state)


def compose_boards(root, specs, reviewed, narrative, entries):
    selection = current(entries, LOGICAL)
    if not selection:
        raise ValueError("须先确认头照和服装源图")
    source = inside(root, selection["path_or_url"])
    if artifacts.digest(source) != selection["checksum"]:
        raise ValueError("当前资产清单字节已变化")
    out = read_json(source)
    if out["source_narrative"]["artifact_id"] != narrative["artifact_id"]:
        raise ValueError("人物板上游编导版本已变化")
    expected = None
    if reviewed:
        if not re.fullmatch(r"[0-9a-f]{64}", reviewed):
            raise ValueError("实看编号无效")
        packet = read_json(inside(root, boards.PACKETS / (reviewed + ".json")))
        if sha256_json(packet) != reviewed or packet["narrative"] != narrative["checksum"]:
            raise ValueError("实看清单或编导版本已变化")
        requested = [[i["ast_id"] + "@" + i["output_id"], *(o["output_id"] for o in i["sources"])]
                     for i in packet["items"]]
        if specs and sorted(specs) != sorted(requested):
            raise ValueError("请按原实看范围登记")
        expected = {i["ast_id"] + "@" + i["output_id"]: i for i in packet["items"]}
        specs = requested
        # A crash after promoting but before updating project_state is recoverable via this receipt.
        if (selection["artifact_id"].startswith("visual-boards-")
                and read_json(inside(root, selection["confirmation_ref"])).get("review_key") == reviewed):
            select_export(root, entries, selection["artifact_id"], Path(selection["confirmation_ref"]),
                          selection["created_at"], "沿用源图确认并实看拼版")

    ready, reused, pending = boards.plans(root, out, specs, expected)
    if reviewed and pending:
        raise ValueError("实看范围内的来源已失效：" + "、".join(pending))
    rendered = [boards.render(root, plan) for plan in ready]
    result = dict(count=len(rendered), reused=reused, pending=pending,
                  items=[{k: row[k] for k in ("ast_id", "output_id", "path", "image_sha256", "pixel_size")} for row in rendered])
    if not rendered:
        return dict(result, artifact_id=selection["artifact_id"])
    if not reviewed:
        packet = dict(narrative=narrative["checksum"], items=rendered)
        key = sha256_json(packet)
        write_json(inside(root, boards.PACKETS / (key + ".json")), packet)
        return dict(result, review_key=key)
    if any(row != expected[row["ast_id"] + "@" + row["output_id"]] for row in rendered):
        raise ValueError("人物板或来源已变化，请重新实看")

    ident = "visual-boards-" + sha256_json([reviewed, selection["artifact_id"]])
    old = next((e for e in entries if e["artifact_id"] == ident), None)
    if old and old["status"] != "candidate":
        raise ValueError("历史拼版已被替代，请核对当前输出")
    for entry in entries:
        if (entry["logical_id"] == LOGICAL and entry["status"] == "candidate" and entry != old
                and entry["artifact_id"].startswith(("visual-boards-", "visual-assets-chat-"))):
            raise ValueError("另一次资产登记待恢复，请先按原编号续作")
    rel = Path(asset_refs.MASTER_DIR) / ident
    receipt = rel / "人物1×4拼版回执.json"
    revisions = [int(e["revision"][1:]) for e in entries
                 if e["logical_id"] == LOGICAL and re.fullmatch(r"R[0-9]+", e["revision"])]
    out["revision"] = old["revision"] if old else f"R{max(revisions, default=0) + 1:02}"
    records = {a["asset_id"]: a for a in out["assets"]}
    confirmations = []
    for row in rendered:
        ast, final = row["ast_id"], row["output_id"]
        record = records[ast]
        candidate = append_candidate(record, row["path"], [s["path"] for s in row["sources"]],
                                     *row["pixel_size"], "png")
        output = dict(output_id=final, output_kind=boards.BOARD, state=row["state"], purpose=record["name"] + "人物造型",
                      candidate_id=candidate, path=row["path"], user_confirmation_ref=receipt.as_posix() + "#" + final,
                      view="、".join(boards.ORDER))
        original = record["confirmed_outputs"]
        record["confirmed_outputs"] = [output if o["output_id"] == final else o for o in original]
        if not any(o["output_id"] == final for o in original):
            record["confirmed_outputs"].append(output)
        if record["confirmed_asset"]["candidate_id"] not in {o["candidate_id"] for o in record["confirmed_outputs"]}:
            record["confirmed_asset"] = {k: output[k] for k in ("candidate_id", "path", "user_confirmation_ref")}
        out["scope_gaps"] = [g for g in out["scope_gaps"] if g["gap_id"] != f"GAP-{ast}-{final}"]
        confirmations.append(dict(ast_id=ast, output_id=final,
                                  confirmation=dict(id=final, path=row["path"], image_sha256=row["image_sha256"],
                                                    sources=row["sources"], pixel_size=row["pixel_size"], view_order=boards.ORDER)))
    with TemporaryDirectory(dir=root) as tmp:
        publish(root, rel, ident, out, Path(tmp), receipt,
                dict(method="两份已确认源图等比拼版，执行者实看", review_key=reviewed, items=confirmations), old)
    select_export(root, entries, ident, receipt, utc_now(), "沿用源图确认并实看拼版")
    return dict(result, artifact_id=ident, manifest=str(root / rel / "visual-asset-manifest.json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show").add_argument("--context-id", required=True)
    together = commands.add_parser("show-round", help="按整轮需求一次展示已收齐的成图")
    together.add_argument("--context-id", action="append", default=[], help="委派批次，可重复")
    together.add_argument("--receipt", action="append", default=[], help="项目内直做批次实看回执，可重复")
    together.add_argument("--targets-json", help="项目内本轮总请求，默认读取项目需求的 asset_review_targets")
    decide = commands.add_parser("confirm")
    decide.add_argument("--shown", action="append", required=True)
    decide.add_argument("--confirmation-json", required=True, type=Path)
    compose = commands.add_parser("compose-boards", help="从同一角色的已确认源图拼版；实看后按编号登记")
    compose.add_argument("--board", action="append", nargs=3, default=[], metavar=("AST@FINAL", "HEAD", "WARDROBE"),
                         help="仅在指定范围或来源不唯一时提供，可重复")
    compose.add_argument("--reviewed", help="执行者已实看并通过的准确拼版编号")
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
        elif args.command == "show-round":
            result = show_round(root, args.context_id, narrative, args.targets_json, args.receipt)
        elif args.command == "confirm":
            result = confirm(root, args.shown, read_json(args.confirmation_json), narrative, entries)
        else:
            result = compose_boards(root, args.board, args.reviewed, narrative, entries)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
