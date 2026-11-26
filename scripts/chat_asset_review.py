"""Prepare ordinary image reviews and record decisions on the exact images shown."""

import argparse
import hashlib
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from tempfile import TemporaryDirectory
from uuid import uuid4

from PIL import Image

if __package__:
    from . import stage_context_cli as stage
    from .core import artifacts, asset_refs, character_boards as boards, production_handoff
    from .core.asset_batch_receipt import DIRECT_REQUESTS, summarize_asset_batch_receipt, validate_asset_review_targets, require_candidate_revision
    from .core.io import project_lock, read_json, write_json, sha256_json, utc_now
    from .core.project_kickoff import REQUEST_PATH
    from .core.project_state import write_project_state
    from .core.schema_contracts import require_schema
else:
    import stage_context_cli as stage
    from core import artifacts, asset_refs, character_boards as boards, production_handoff
    from core.asset_batch_receipt import DIRECT_REQUESTS, summarize_asset_batch_receipt, validate_asset_review_targets, require_candidate_revision
    from core.io import project_lock, read_json, write_json, sha256_json, utc_now
    from core.project_kickoff import REQUEST_PATH
    from core.project_state import write_project_state
    from core.schema_contracts import require_schema

LOGICAL = "visual.asset.manifest"
SHOWN = asset_refs.CHAT_REVIEW
REVIEW_FOLDER = SHOWN / "待审图"


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
                "candidate_revision": source.get("candidate_revision"),
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
    return {f"{ast}@{o['output_id']}": {"sha256": o["sha256"], "purpose": o["purpose"],
            "candidate_id": o.get("source_candidate_id"), "candidate_revision": o.get("source_candidate_revision"),
            "source_path": o.get("source_candidate"), "confirmation_ref": o["confirmation_ref"]}
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


def active_targets(targets, requirements):
    """Drop targets whose asset the user has since cancelled; keep order."""
    live = {ast for ast, req in requirements.items() if req.get("image_decision") in ("需要制作", "复用现有素材")}
    kept = [t for t in targets if t.partition("@")[0] in live]
    return kept, sorted(set(targets) - set(kept))


def show_round(root, contexts, narrative, targets_path=None, receipts=()):
    story = read_json(inside(root, narrative["path_or_url"]))
    requirements = effective_requirements(root, narrative, story=story)
    targets, cancelled = active_targets(round_targets(root, targets_path), requirements)
    expected = set(targets)
    rejected = rejected_targets(root, expected)
    assets, _, waiting = production_handoff._asset_readiness(root, story, file_digest=artifacts.cache_file_digests())
    planned_assets = production_handoff.review_source_assets(assets, targets)
    inventory, _ = production_handoff.unlisted_asset_work(root, assets, waiting, planned_assets)
    if inventory:
        raise ValueError("整轮清单遗漏仍需人审的资产：" + "、".join(sorted(inventory)))
    contexts = sorted(set(contexts))
    receipts = sorted({inside(root, path).relative_to(root).as_posix() for path in receipts})
    batches = [batch_items(root, c, narrative, requirements) for c in contexts]
    batches.extend(direct_items(root, path, narrative, requirements) for path in receipts)
    cancelled_assets = {t.partition("@")[0] for t in cancelled}
    ready, pending = {}, {}
    for items, blocked in batches:
        for row in items:
            target = row["target"]
            if target in ready and ready[target] != row:
                raise ValueError("同一目标有多份待审版本，请指定实际批次：" + target)
            ready[target] = row
        for row in blocked:
            # Images of assets the user has cancelled stay in their batch; they no longer block the round.
            if row["target"].partition("@")[0] in cancelled_assets:
                continue
            pending[row["target"]] = row
    approved = approved_versions({ast: asset["image"] for ast, asset in assets.items()})
    reused = {}
    same_version = {t for t, row in ready.items()
                    if t in approved and approved[t]["sha256"] == row["sha256"]
                    and approved[t]["purpose"] == reviewed_purpose(row)
                    and approved[t]["source_path"] == row["path"]
                    and isinstance(row.get("candidate_revision"), str) and row["candidate_revision"].strip()
                    and approved[t]["candidate_revision"] == row["candidate_revision"]
                    and approved[t]["candidate_id"] == row["candidate_id"]}
    for target, previous in approved.items():
        if target in ready and target not in same_version:
            continue
        if target not in pending:
            reused[target] = previous
    extra = (ready.keys() | pending.keys()) - expected - reused.keys() - rejected - set(cancelled)
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
    if cancelled:
        scope["cancelled"] = cancelled
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
    if item.get("sources"):
        confirmed = asset_refs.confirmed_images(root, requests=[ast])
        current_sources = {(r["output_id"], r["sha256"], r["confirmation_ref"]) for r in confirmed.get(ast, {}).get("outputs", [])}
        if any((src["output_id"], src["sha256"], src["confirmation_ref"]) not in current_sources for src in item["sources"]):
            raise ValueError("展示后拼版源图已变化，请重新拼版")
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
            candidate_revision=item.get("candidate_revision"),
            purpose=item["purpose"],
            **({"sources": item["sources"]} if item.get("sources") else {}),
            accepted_deviation=item.get("issue", ""),
            limitations=item.get("limitations"),
        ),
    )


def discard(root, message, narrative, entries):
    """Revoke an exact confirmed output or delete an unconfirmed candidate."""
    target, image, checksum = (message.get(key) for key in ("target", "path", "sha256"))
    (target,) = validate_asset_review_targets([target])
    if not all(isinstance(message.get(key), str) and message[key].strip()
               for key in ("quote", "review_ref", "at", "path", "sha256")):
        raise ValueError("删除资产须绑定文件、字节与明确的用户指令")
    if datetime.fromisoformat(message["at"]).utcoffset() is None:
        raise ValueError("删除时间须含时区")
    ast, output = target.split("@")
    if ast not in effective_requirements(root, narrative):
        raise ValueError("删除目标须为本项目登记的资产")
    path = inside(root, image)
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("删除对象须为项目内的图片文件")

    decisions, issues = asset_refs._output_decisions(root)
    if issues:
        raise ValueError(issues[0]["message"])
    previous = decisions.get((ast, output), {})
    key = sha256_json(message)
    retry = previous.get("decision_id") == key and previous.get("status") == "rejected"
    if path.exists() and artifacts.digest(path) != checksum:
        raise ValueError("待删除图片字节已变化，请核对实际文件")
    if not path.is_file() and not retry:
        raise ValueError("待删除图片已缺失或不是普通文件")

    active = current(entries, LOGICAL)
    manifest = artifacts.load_manifest(root)
    if active and (artifacts.digest(inside(root, active["path_or_url"])) != active["checksum"]
                   or not artifacts.artifact_is_adopted(active, artifacts.load_project_state(root), root)):
        raise ValueError("当前资产清单失效，请先核对原确认")
    selected = (asset_refs.registered_output_snapshot(root, active, include_discarded=True) or {}) if active else {}
    package = read_json(inside(root, active["path_or_url"])) if active else {}
    bound = [(asset["asset_id"], item["output_id"]) for asset in package.get("assets", [])
             for item in asset.get("confirmed_outputs", []) if artifacts.project_path(root, item["path"]) == path]
    slot = (ast, output)
    if set(bound) - {slot}:
        raise ValueError("同一文件仍被其他已确认输出使用，请先核对共用范围")
    if not bound and not retry:
        # An unconfirmed candidate can share a slot with a valid older output.
        # Delete only the selected file and leave that older approval intact.
        path.unlink()
        return {"target": target, "file_deleted": True, "confirmation_revoked": False}

    original = selected.get(slot)
    if not retry and (not original or original["sha256"] != checksum):
        raise ValueError("待撤销图片和当前确认依据不一致")
    replacement = message.get("replacement")
    substitute = None
    if replacement:
        (replacement,) = validate_asset_review_targets([replacement])
        replacement_key = tuple(replacement.split("@"))
        substitute = selected.get(replacement_key)
        if (replacement_key == slot or replacement_key[0] != ast or not substitute
                or decisions.get(replacement_key, {}).get("status") == "rejected"
                or (original and substitute.get("output_kind") != original.get("output_kind"))):
            raise ValueError("替代图须为同一资产下可用且同类型的已确认输出")
    if not retry:
        changed = {candidate: item for candidate, item in selected.items() if candidate != slot}
        edges, impacted = artifacts.asset_output_impact(root, manifest, selected, changed)
        decision = {"decision_id": key, "asset_id": ast, "output_id": output, "status": "rejected",
                    "scope": "project", "reason": "用户明确删除该图并撤销项目使用",
                    "user_instruction": message["quote"], "decided_at": message["at"],
                    "evidence_refs": [message["review_ref"]], "discarded_path": str(path),
                    "discarded_sha256": checksum, "replacement": replacement,
                    "affected_segments": impacted}
        decision_path = root / asset_refs.OUTPUT_DECISIONS
        ledger = (read_json(decision_path) if decision_path.is_file() else
                  {"schema_version": "asset-output-applicability-1.0", "decisions": []})
        # Record downstream impact while the original image is still available.
        prior_segments = {entry["artifact_id"]: set(reason["asset_output_segment_ids"])
                          for entry in manifest["artifacts"] for reason in entry.get("stale_reasons", [])
                          if reason.get("asset_output_segment_ids")}
        pending = {key: sorted(set(values) | prior_segments.get(key, set())) for key, values in impacted.items()}
        artifacts.mark_impacted_artifacts_stale(manifest, {LOGICAL}, "已确认资产撤回，相关镜头需换绑或待补",
                                                narrative["revision"], unchanged_edges=edges,
                                                asset_output_segments=pending)
        # Keep the current manifest authoritative for the unrelated outputs.
        index = next(index for index, entry in enumerate(manifest["artifacts"])
                     if entry["artifact_id"] == active["artifact_id"])
        manifest["artifacts"][index] = active
        artifacts.save_manifest(root, manifest)
        ledger["decisions"].append(decision)
        write_json(decision_path, ledger)
    else:
        decision = previous

    request_path = root / REQUEST_PATH
    if request_path.is_file():
        request = read_json(request_path)
        targets = request.get("asset_review_targets", [])
        if target in targets:
            remaining = [item for item in targets if item != target]
            if remaining:
                request["asset_review_targets"] = remaining
            else:
                request.pop("asset_review_targets")
            write_json(request_path, request)
    path.unlink(missing_ok=True)
    return {"target": target, "file_deleted": True, "confirmation_revoked": True,
            "affected_segments": decision["affected_segments"], "replacement": replacement,
            "segment_action": "rebind" if replacement else "awaiting_asset",
            **({"replacement_image": {field: substitute[field] for field in ("path", "sha256")}}
               if substitute else {})}


def same_message(left, right):
    if left.get("message_id") and right.get("message_id"):
        return left["message_id"] == right["message_id"]
    return bool(left.get("review_ref")) and left["review_ref"] == right.get("review_ref")


def previous_direct_decision(root, message, entries, *, approval_id=None, feedback=None):
    for path in inside(root, SHOWN / "用户退回").glob("*.json"):
        saved = read_json(inside(root, path))
        if same_message(saved["feedback"], message):
            # A pre-revision return can replay its exact original, content-addressed display.
            if feedback and saved["shown"] == feedback["shown"] and saved["feedback"] == message and (
                    len(saved["items"]) == len(feedback["items"]) and all(
                        all(current.get(k) == v for k, v in old.items())
                        for old, current in zip(saved["items"], feedback["items"]))):
                return path
            raise ValueError("同一条用户消息已用于退回，不能改作其他决定")
    for entry in entries:
        ident = entry["artifact_id"]
        if not ident.startswith("visual-assets-chat-") or ident == approval_id:
            continue
        if entry["status"] == "candidate":
            raise ValueError("另一次资产确认待恢复，请按原交审编号和消息重试")
        other = read_json(inside(root, entry["confirmation_ref"]))
        if any(same_message(row["confirmation"], message) for row in other["items"]):
            raise ValueError("同一条用户消息已用于其他交审范围")


def workbench_records(root, table, ident=None):
    """Read the existing project's library without opening a writable Library session."""
    pointer = inside(root, SHOWN / "工作台位置.json")
    if pointer.is_file():
        project_file = Path(read_json(pointer)["project"])
        record = read_json(project_file)
        if Path(record["path"]).resolve() != root:
            raise ValueError("项目工作台位置不符")
        library, projects = project_file.parent.parent, [record]
    else:
        library = Path(__file__).resolve().parents[1] / ".master-storyboarding/visual-library"
        projects = [p for path in (library / "projects").glob("*.json")
                    if Path((p := read_json(path))["path"]).resolve() == root]
        if len(projects) > 1:
            raise ValueError("无法唯一定位项目工作台")
    if not projects:
        state = artifacts.load_project_state(root)
        if any("asset-image-assistant" in auth.get("skills", []) for auth in state.get("workflow_authorizations", [])):
            raise ValueError("项目有 Web 审图授权，请通过原制作库的项目绑定入口定位历史记录")
        return
    project = projects[0]
    with closing(sqlite3.connect((library / "library.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if table == "assets":
            rows = db.execute("SELECT data FROM assets WHERE id=?", (ident,))
        else:
            jobs = {row["job_id"] for row in project["rows"] if row.get("job_id")}
            query = "SELECT data FROM jobs WHERE id IN (SELECT value FROM json_each(?))"
            params = [json.dumps(sorted(jobs))]
            if table == "jobs":
                jobs.update(row["job_id"] for row in project.get("history", []) if row.get("job_id"))
                jobs.update(job for row in project["rows"] for job in row.get("job_history", []))
                query += " OR json_extract(data,'$.project_id')=?"
                params = [json.dumps(sorted(jobs)), project["id"]]
            rows = db.execute(query, params)
        for (data,) in rows:
            yield json.loads(data)


def cross_message(previous, message):
    if previous.get("message_id") and message.get("message_id"):
        return previous["message_id"] == message["message_id"]
    # Distinct timestamp AND words distinguish independent historical decisions.
    # Changing the review reference alone can never transfer an earlier message.
    if (previous.get("at") and message.get("at") and previous.get("quote") and message.get("quote")
            and datetime.fromisoformat(previous["at"]) != datetime.fromisoformat(message["at"])
            and previous["quote"] != message["quote"] and not same_message(previous, message)):
        return False
    raise ValueError("跨入口历史消息缺少稳定消息 ID，无法核对其他交审范围，请沿原入口处理")


def web_history(root, message, *, cross_channel=False):
    matches = cross_message if cross_channel else same_message
    for job in workbench_records(root, "jobs"):
        for step in job["steps"]:
            for version in (step, *step.get("history", [])):
                feedback = version.get("feedback")
                if isinstance(feedback, dict) and matches(feedback, message):
                    yield "returned", feedback
                review = (version.get("project_confirmation") or {}).get("chat_confirmation") or {}
                if review and matches(review["confirmation"], message):
                    yield "approved", review
    for entry in artifacts.load_manifest(root)["artifacts"]:
        if entry.get("logical_id") != LOGICAL or not entry.get("confirmation_ref"):
            continue
        path = inside(root, entry["confirmation_ref"])
        if path.is_file():
            for row in read_json(path).get("items", []):
                review = row["confirmation"].get("chat_confirmation")
                if review and matches(review["confirmation"], message):
                    yield "approved", review


def candidate_identity(root, row):
    target = row.get("target") or (row.get("ast_id", "") + "@" + row.get("output_id", ""))
    source = row.get("source_path") or row.get("source_candidate") or row.get("path")
    return (target, inside(root, source) if source else None, row.get("candidate_id") or row.get("asset_id"), row.get("candidate_revision"),
            row.get("image_sha256") or row.get("sha256"), row.get("purpose"))


def prior_channel_decision(root, message, outputs, outcome, *, from_web):
    """An exact cross-channel replay is read-only. A partial or ambiguous match cannot adopt anything."""
    wanted = {candidate_identity(root, row) for row in outputs}
    def check(previous, kind, rows):
        actual = {candidate_identity(root, row) for row in rows}
        if (not message.get("message_id") or previous.get("message_id") != message["message_id"]
                or kind != outcome or actual != wanted or any(not all(key) for key in actual)
                or any(previous.get(k) != message.get(k) for k in ("quote", "at"))):
            raise ValueError("同一条用户消息已用于其他交审范围或决定")
        return True

    found = False
    if from_web:
        for path in inside(root, SHOWN / "用户退回").glob("*.json"):
            saved = read_json(inside(root, path))
            if cross_message(saved["feedback"], message):
                found = check(saved["feedback"], "returned", saved["items"])
        for entry in artifacts.load_manifest(root)["artifacts"]:
            if not entry["artifact_id"].startswith("visual-assets-chat-"):
                continue
            path = inside(root, entry["confirmation_ref"])
            if not path.is_file():
                if entry["status"] == "candidate":
                    raise ValueError("另一次资产确认待恢复")
                raise ValueError("历史资产消息依据缺失")
            rows = [{**r["confirmation"], "ast_id": r["ast_id"], "output_id": r["output_id"]}
                    for r in read_json(path)["items"] if cross_message(r["confirmation"], message)]
            if rows:
                found = check(rows[0], "approved", rows)
    else:
        for _, saved in web_messages(root):
            previous = saved.get("decision") or saved
            if saved.get("message_id") == message.get("message_id") and saved.get("message_id") and not same_message(previous, message):
                raise ValueError("这是展示消息，不能改作其他决定")
            if saved.get("original_approval") and cross_message(saved["original_approval"], message):
                raise ValueError("原展示候选已退回")
            if (cross_message(previous, message) if saved.get("outcome") else same_message(saved, message)):
                if not saved.get("outcome"):
                    raise ValueError("同一条用户消息已绑定 Web 候选")
                found = check(previous, saved["outcome"], saved["outputs"])
        history = list(web_history(root, message, cross_channel=True))
        if history:
            historical = []
            for kind, review in history:
                if kind != outcome or kind == "returned" and not review.get("outputs"):
                    raise ValueError("同一条用户消息已用于其他历史决定")
                historical.extend(review.get("approved_outputs") or review.get("outputs") or [review])
            record = history[0][1]
            found = check(record.get("confirmation", record), outcome, historical)
    return found


def web_messages(root):
    for path in inside(root, SHOWN / "Web对话消息").glob("*.json"):
        yield path, read_json(inside(root, path))


def web_display(root, message_id):
    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("缺少展示消息 ID 和候选绑定")
    records = [(p, r) for p, r in web_messages(root) if r.get("message_id") == message_id]
    if len(records) != 1 or not records[0][1].get("display_source"):
        raise ValueError("缺少唯一的展示消息和候选绑定")
    return records[0]


def read_shown(root, key, narrative):
    if not re.fullmatch("[0-9a-f]{64}", key):
        raise ValueError("交审编号无效")
    packet = read_json(inside(root, SHOWN / (key + ".json")))
    if sha256_json(packet) != key or (packet["narrative"], packet["narrative_id"]) != (
        narrative["checksum"],
        narrative["artifact_id"],
    ):
        raise ValueError("交审内容或编导版本已变化")
    return packet


def shown_decision(root, keys, message, narrative):
    if not all(isinstance(message.get(k), str) and message[k].strip() for k in ("at", "quote", "review_ref")):
        raise ValueError("缺用户决定原话、时间或消息引用")
    if datetime.fromisoformat(message["at"]).utcoffset() is None or message.get("scope") not in ("all_shown", "selected"):
        raise ValueError("用户决定须绑定已展示清单与含时区时间")
    if "message_id" in message and not (isinstance(message["message_id"], str) and message["message_id"].strip()):
        raise ValueError("用户消息 ID 不能为空")
    if "shown" in message:
        shown = message["shown"]
        if (not isinstance(shown, list) or not all(isinstance(key, str) for key in shown)
                or sorted(shown) != sorted(set(keys))):
            raise ValueError("消息记录的候选展示编号与本次决定不符")
        message = {**message, "shown": sorted(shown)}
    rows = {}
    round_scope = None
    for key in keys:
        packet = read_shown(root, key, narrative)
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
        if not set(selected) <= rows.keys():
            raise ValueError("请列出本次决定涉及的已展示目标")
        rows = {target: item for target, item in rows.items() if target in selected}
        message = {**message, "targets": sorted(selected)}
    elif "targets" in message:
        raise ValueError("指定部分通过项时须用 selected 范围")
    return rows, round_scope, message


def revoked_confirmation_evidence(root, rows):
    by_target = {asset_refs.return_target(row): row for row in rows}
    requests = {f"{ast}@{slot}" for ast, output in by_target for slot in (output, "legacy-main")}
    images = asset_refs.confirmed_images(root, requests=requests)
    revoked = {}
    for ast, image in images.items():
        for output in image["outputs"]:
            for target in asset_refs.confirmation_targets(ast, output):
                row = by_target.get(target)
                if row and asset_refs.matches_returned_candidate(root, row, output):
                    revoked.setdefault("@".join(target), []).append(output["confirmation_ref"])
    return {"revoked_outputs": revoked,
            "revoked_confirmations": [ref for refs in revoked.values() for ref in refs]}


def return_for_revision(root, keys, message, narrative):
    rows, _, message = shown_decision(root, keys, message, narrative)
    if prior_channel_decision(root, message, rows.values(), "returned", from_web=False):
        return dict(status="returned", feedback=message["quote"], already_recorded=True)
    items = [{key: row.get(key) for key in ("target", "candidate_id", "candidate_revision", "path", "sha256", "purpose")}
             for row in rows.values()]
    feedback = dict(shown=keys, items=items, feedback=message)
    previous = previous_direct_decision(root, message, artifacts.load_manifest(root)["artifacts"], feedback=feedback)
    location = previous.relative_to(root) if previous else SHOWN / "用户退回" / (sha256_json(feedback) + ".json")
    path = inside(root, location)
    if previous:
        items = read_json(previous)["items"]
    if not path.is_file():
        if "shown" not in message:
            raise ValueError("新决定必须在消息中记录所见候选的 shown 编号列表")
        write_json(path, {**feedback, **revoked_confirmation_evidence(root, items)})
    return dict(status="returned", feedback=message["quote"], items=items, record=location.as_posix())


def confirm(root, keys, message, narrative, entries):
    rows, round_scope, message = shown_decision(root, keys, message, narrative)
    if prior_channel_decision(root, message, rows.values(), "approved", from_web=False):
        return dict(status="approved", already_recorded=True)
    # Shown snapshots also protect unconfirmed and withdrawn revisions against rollback.
    for path in inside(root, SHOWN).glob("[0-9a-f]" * 64 + ".json"):
        packet = read_json(inside(root, path))
        if (packet["narrative_id"], packet["narrative"]) != (narrative["artifact_id"], narrative["checksum"]):
            continue
        for prior in packet["items"]:
            item = rows.get(prior["target"])
            if item and item["candidate_id"] == prior["candidate_id"]:
                require_candidate_revision(prior.get("candidate_revision"), item.get("candidate_revision"))
    rejected_targets(root, rows)
    if any(set(keys).intersection(prior["shown"]) and rows.keys() & {item["target"] for item in prior["items"]}
           for path in inside(root, SHOWN / "用户退回").glob("*.json") for prior in [read_json(inside(root, path))]):
        raise ValueError("原展示候选已被退回，请重新展示本轮候选")
    returns = asset_refs._returned_candidates(root)
    if any(asset_refs.candidate_was_returned(root, item, returns) for item in rows.values()):
        raise ValueError("该版本候选已被退回，请重新展示实际返修后的候选")
    key = sha256_json([sorted(set(keys)), message])
    ident = "visual-assets-chat-" + key
    rel = Path(asset_refs.MASTER_DIR) / ident
    folder = inside(root, rel)
    dest = folder / "visual-asset-manifest.json"
    receipt = rel / "用户确认记录.json"
    old = next((e for e in entries if e["artifact_id"] == ident), None)
    previous_direct_decision(root, message, entries, approval_id=ident)
    if not old and "shown" not in message:
        raise ValueError("新决定必须在消息中记录所见候选的 shown 编号列表")
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
            or ("candidate_revision" in confirmed[t] and confirmed[t]["candidate_revision"] != item.get("candidate_revision"))
            or ("candidate_id" in confirmed[t] and confirmed[t]["candidate_id"] != item["candidate_id"])
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
            story = read_json(inside(root, narrative["path_or_url"]))
            live, _ = active_targets(round_targets(root, round_scope["targets_path"]),
                                     effective_requirements(root, narrative, story=story))
            if set(live) != set(round_scope["targets"]):
                raise ValueError("整轮交审目标已变化，请按当前清单重新展示")
            reused = round_scope["reused"]
            if reused and approved_versions(asset_refs.confirmed_images(root, requests=reused)) != reused:
                raise ValueError("交审后沿用的已确认图已变化，请重新展示")
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
            asset_refs.without_rejected_outputs(root, out)
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
                gap_id = boards.scope_gap_id(ast, output)
                out["scope_gaps"] = [g for g in out["scope_gaps"] if g["gap_id"] != gap_id]
                if not final or final == output:
                    continue
                gap_id = boards.scope_gap_id(ast, final)
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


def compose_boards(root, specs, narrative, entries):
    selection = current(entries, LOGICAL)
    if not selection:
        raise ValueError("须先确认头照和服装源图")
    source = inside(root, selection["path_or_url"])
    if artifacts.digest(source) != selection["checksum"]:
        raise ValueError("当前资产清单字节已变化")
    out = read_json(source)
    asset_refs.without_rejected_outputs(root, out)
    if out["source_narrative"]["artifact_id"] != narrative["artifact_id"]:
        raise ValueError("人物板上游编导版本已变化")
    ready, reused, pending = boards.plans(root, out, specs)
    requirements = effective_requirements(root, narrative)
    items = []
    for plan in ready:
        board = boards.render(root, plan)
        ast, final = board['ast_id'], board['output_id']
        items.append(dict(target=ast + '@' + final, candidate_id=board['image_sha256'],
                          sha256=board['image_sha256'], path=board['path'], sources=board['sources'],
                          state=board['state'], view='、'.join(boards.ORDER), output_kind=boards.BOARD,
                          purpose=requirements[ast]['name'] + '人物造型',
                          requirement_sha256=sha256_json(requirements[ast])))
    result = store_shown(root, narrative, items) if items else {'items': []}
    return dict(result, count=len(items), reused=reused, pending=pending)


def read_decision(path, keys):
    """Read a decision file (or stdin with '-') and fill in the shown list from --shown if omitted."""
    message = json.load(sys.stdin) if str(path) == "-" else read_json(path)
    if isinstance(message, dict) and "shown" not in message:
        message = {**message, "shown": sorted(set(keys))}
    return message


def review_folder(root, result):
    """Gather the shown images into one folder (links in display order) and a readable latest note."""
    items, key = result.get("items") or [], result.get("shown")
    if not key or not items:
        return None
    folder = inside(root, REVIEW_FOLDER / key[:12])
    if not folder.is_dir():
        folder.mkdir(parents=True, exist_ok=True)
        for number, item in enumerate(items, 1):
            source = inside(root, item["path"])
            name = re.sub(r'[\\/:*?"<>|\s]+', "_", item["target"])
            link = folder / f"{number:02}_{name}{source.suffix}"
            try:
                link.symlink_to(source)
            except OSError:
                shutil.copy2(source, link)
    lines = ["# 最新交审清单", "", f"- 展示编号：`{key}`", f"- 看图文件夹：`{folder.relative_to(root).as_posix()}`",
             f"- 共 {len(items)} 张", "", "| 序号 | 目标 | 用途 | 原图 |", "| --- | --- | --- | --- |"]
    lines += [f"| {n:02} | {i['target']} | {i.get('purpose', '')} | {i['path']} |" for n, i in enumerate(items, 1)]
    inside(root, SHOWN / "最新交审.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--no-open", action="store_true", help="交审清单就绪时不自动打开看图文件夹")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show").add_argument("--context-id", required=True)
    together = commands.add_parser("show-round", help="按整轮需求一次展示已收齐的成图")
    together.add_argument("--context-id", action="append", default=[], help="委派批次，可重复")
    together.add_argument("--receipt", action="append", default=[], help="项目内直做批次实看回执，可重复")
    together.add_argument("--targets-json", help="项目内本轮总请求，默认读取项目需求的 asset_review_targets")
    decide = commands.add_parser("confirm")
    decide.add_argument("--shown", action="append", required=True)
    decide.add_argument("--confirmation-json", required=True, type=Path,
                            help="记录原消息的 JSON 文件，或 - 从标准输入读取；省略 shown 时按 --shown 补齐")
    returned = commands.add_parser("return", help="保留所示候选并记录用户的返修意见")
    returned.add_argument("--shown", action="append", required=True)
    returned.add_argument("--feedback-json", required=True, type=Path,
                          help="记录原退回消息的 JSON 文件，或 - 从标准输入读取；省略 shown 时按 --shown 补齐")
    commands.add_parser("discard").add_argument("--decision-json", required=True, type=Path)
    compose = commands.add_parser("compose-boards", help="从同一角色的已确认源图拼版，展示给用户确认")
    compose.add_argument("--board", action="append", nargs=3, default=[], metavar=("AST@FINAL", "HEAD", "WARDROBE"),
                         help="仅在指定范围或来源不唯一时提供，可重复")
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
            result = confirm(root, args.shown, read_decision(args.confirmation_json, args.shown), narrative, entries)
        elif args.command == "return":
            result = return_for_revision(root, args.shown, read_decision(args.feedback_json, args.shown), narrative)
        elif args.command == "discard":
            result = discard(root, read_json(args.decision_json), narrative, entries)
        elif args.command == "compose-boards":
            result = compose_boards(root, args.board, narrative, entries)
        folder = review_folder(root, result) if args.command in ("show", "show-round", "compose-boards") else None
        if folder:
            result = {**result, "review_folder": str(folder)}
    if folder and not args.no_open and sys.platform == "darwin" and "PYTEST_CURRENT_TEST" not in os.environ:
        subprocess.run(["open", str(folder)], check=False)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
