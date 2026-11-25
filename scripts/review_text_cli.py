#!/usr/bin/env python3
"""Export authoritative narrative JSON and send its exact reading copy for review.

Publication receipts bind exact project files to website versions. Only an
effective, submitted website decision may authorize local adoption.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from urllib.parse import quote
from io import BytesIO
from pathlib import Path

try:
    from .core.agent_review_api import AgentReviewClient, AgentReviewError, DEFAULT_BASE_URL, MAX_IMAGE_BYTES, MAX_TEXT_BYTES, effective_approval
    from .core.artifacts import append_workspace_candidate, load_manifest, pending_narrative_candidate, save_manifest
    from .core.asset_refs import character_output_contract
    from .core.candidate_test import require_production
    from .core.image_technical import character_board_image_problem
    from .core.io import project_lock, utc_now, write_bytes, write_json
    from .core.review_publication import (project_file as _path, read_decision, read_publication,
                                          read_script_approval, require_script_approval)
    from .core.story_sources import export_markdown, narrative_review_projections, validate_story_sources
    from .visual_asset_choices import image_work_items
    from .visual_asset_source import source_project
except ImportError:  # direct execution
    from core.agent_review_api import AgentReviewClient, AgentReviewError, DEFAULT_BASE_URL, MAX_IMAGE_BYTES, MAX_TEXT_BYTES, effective_approval
    from core.artifacts import append_workspace_candidate, load_manifest, pending_narrative_candidate, save_manifest
    from core.asset_refs import character_output_contract
    from core.candidate_test import require_production
    from core.image_technical import character_board_image_problem
    from core.io import project_lock, utc_now, write_bytes, write_json
    from core.review_publication import (project_file as _path, read_decision, read_publication,
                                         read_script_approval, require_script_approval)
    from core.story_sources import export_markdown, narrative_review_projections, validate_story_sources
    from visual_asset_choices import image_work_items
    from visual_asset_source import source_project


_ADOPTION_FAILURES = {
    "CANDIDATE_ID_CONFLICT": "核对清单中该产物 ID 的修订、路径与依赖，保持原有登记。",
    "CANDIDATE_HASH_MISMATCH": "核对已登记候选和送审原件的字节；稿件内容变更需走现有返修确认。",
    "SCRIPT_CHANGE_REQUIRED": "当前已有正式稿，沿原修订控制定位本次变更案和已送审候选。",
    "LOCAL_ADOPTION_IO_ERROR": "核对本地项目文件和回执的可读性及写入状态。",
    "LOCAL_ADOPTION_BLOCKED": "核对本地采用状态和对应项目门禁。",
}


class _ScriptAdoptionBlocked(ValueError):
    def __init__(self, cause, *, args, receipt_path, receipt):
        super().__init__(str(cause))
        code = getattr(cause, "code", None)
        if code not in _ADOPTION_FAILURES:
            code = "LOCAL_ADOPTION_IO_ERROR" if isinstance(cause, OSError) else "LOCAL_ADOPTION_BLOCKED"
        self.report = {"ok": False, "step": "local_adoption", "code": code,
                       "website_decision": "verified_approved", "receipt": str(receipt_path),
                       "publication": receipt["publication"], "website_version": receipt["request"]["parameters"]["version"],
                       "artifact_id": args.adopt_script_id, "revision": receipt["source_json"]["revision"],
                       "message": _ADOPTION_FAILURES[code],
                       "resume": "先只读核对本地当前状态。阻塞条件修复后，沿原发布版本执行 decision，并使用新决定回执路径。"}
        if args.change_id:
            self.report["change_id"] = args.change_id


def _binding(project: Path, path: Path, raw: bytes) -> dict:
    return {"path": path.relative_to(project).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw)}


def _source(project: Path, value: str | Path, *, chat=False) -> tuple[dict, dict]:
    path = _path(project, value)
    raw = path.read_bytes()
    package = json.loads(raw.decode("utf-8-sig"))
    review_check = validate_story_sources(package, project_root=project, review_ready=True, prepare_chat_review=chat)
    return package, {**_binding(project, path, raw), "project_id": package["project_id"],
                     "revision": package["revision"], "status": package["status"],
                     "awaiting_creative_approval_ids": review_check["awaiting_creative_approval_ids"],
                     "awaiting_subtitle_approval_ids": review_check["awaiting_subtitle_approval_ids"]}


def _projection(project: Path, package: dict, value: str | Path, *, subtitle_review_ids=(),
                refresh_from=None) -> tuple[str, dict]:
    path = _path(project, value)
    raw = path.read_bytes()
    projections = tuple(narrative_review_projections(package, subtitle_review_ids=subtitle_review_ids))
    if raw not in projections:
        if refresh_from is None:
            raise ValueError("Reading Markdown differs from this exact narrative JSON; check the active candidate.")
        if hashlib.sha256(raw).hexdigest() != refresh_from:
            raise ValueError("现有阅读稿已手改，先将用户修改合入当前 JSON 并核对阅读稿")
        raw = projections[0]
        write_bytes(path, raw)
    return raw.decode("utf-8"), _binding(project, path, raw)


def _revision_changes(before, after):
    """Compact index into the exact old and new JSON used for review."""
    collections = {'story_paragraphs': 'paragraph_id', 'dialogues': 'dialogue_id',
                   'asset_requirements': 'asset_id'}
    changed = {k for k in before.keys() | after.keys()
               if k not in before or k not in after or before[k] != after[k]}
    result = {}
    for field, key in collections.items():
        if field not in changed:
            continue
        before_rows, after_rows = before.get(field), after.get(field)
        if not all(isinstance(rows, list) and all(isinstance(row, dict) and isinstance(row.get(key), str)
                   for row in rows) for rows in (before_rows, after_rows)):
            continue
        old = {row[key]: row for row in before_rows}
        new = {row[key]: row for row in after_rows}
        if len(old) != len(before_rows) or len(new) != len(after_rows):
            continue  # Unindexed legacy collections stay visible in other_fields.
        result[field] = {
            'added': [i for i in new if i not in old],
            'removed': [i for i in old if i not in new],
            'updated': [i for i in new if i in old and old[i] != new[i]],
            'reordered': [i for i in old if i in new] != [i for i in new if i in old],
        }
    result['other_fields'] = sorted(changed - result.keys())
    return result


def _archive_reading(project, raw):
    checksum = hashlib.sha256(raw).hexdigest()
    path = _path(project, f"99_历史与废弃/编导手改/{checksum}.md")
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("阅读稿的归档位置冲突")
    else:
        write_bytes(path, raw)
    return _binding(project, path, raw)


def _keep_edited_reading(project, prior, package, original):
    if not original:
        return
    raw = _path(project, original["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() == original["sha256"]:
        return
    prior["user_edited_reading"] = _archive_reading(project, raw)
    # Old packages can still point to edited Markdown. Restore the delivered
    # bytes only when the original projection reproduces its registered hash.
    chat = "review_markdown" in prior
    ids = ()
    if chat:
        checked = validate_story_sources(package, project_root=project, review_ready=True, prepare_chat_review=True)
        ids = checked["awaiting_subtitle_approval_ids"]
    projections = narrative_review_projections(package, subtitle_review_ids=ids)
    for previous in projections:
        if hashlib.sha256(previous).hexdigest() == original["sha256"]:
            prior["review_markdown" if chat else "reading_copy"] = _archive_reading(project, previous)
            break


def _check_preflight(project, client, path, *, case_id, stage, project_id=None):
    path = _path(project, path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    expected_request = {"caseId": case_id, "stage": stage}
    if (saved.get("kind") != "agent-review-api-receipt" or saved.get("command") != "status"
            or saved.get("project_path") != str(project) or saved.get("base_url") != client.base_url
            or (saved.get("request") or {}).get("parameters") != expected_request):
        raise ValueError("送审预读回执与本地项目、站点、案例或阶段不符")
    previous = saved.get("response") or {}
    expected_case = previous.get("case") or {}
    if (expected_case.get("id") != case_id or not expected_case.get("projectId")
            or (project_id is not None and expected_case["projectId"] != project_id)
            or previous.get("stage") != stage or not isinstance(previous.get("expectations"), dict)):
        raise ValueError("送审预读回执没有有效的项目与阶段状态")
    current = client.status(case_id=case_id, stage=stage)
    if current["case"]["projectId"] != expected_case["projectId"]:
        raise ValueError("网站项目已变化，请核对准确案例")
    version_fields = ("expectedHeadVersion", "expectedHeadGeneration", "expectedReviewRevision")
    if (any(key not in previous["expectations"]
            or current["expectations"][key] != previous["expectations"][key] for key in version_fields)
            or current["sourceScriptVersion"] != previous.get("sourceScriptVersion")):
        raise ValueError("网站当前版本或审核状态已变化，请先核对差异再决定是否送审")
    return {"path": path.relative_to(project).as_posix(), "response": current}


def _publication_candidate(project, args, source):
    from chat_asset_review import read_shown, workbench_records
    if args.shown:
        packet = read_shown(project, args.shown, source)
        rows = [row for row in packet["items"] if row["target"] == args.ast_id + "@" + args.output_id
                and row["candidate_id"] == args.artifact_id and row["purpose"] == args.purpose]
        if len(rows) != 1:
            raise ValueError("展示清单没有唯一匹配的送审候选")
        row = rows[0]
        image = _path(project, row["path"])
        revision, checksum = row.get("candidate_revision"), row["sha256"]
    else:
        assets = list(workbench_records(project, "assets", args.artifact_id))
        if len(assets) != 1 or assets[0].get("trashed_at") or assets[0].get("reference_only"):
            raise ValueError("找不到可送审的本地候选")
        asset = assets[0]
        if asset["id"] != args.artifact_id:
            raise ValueError("素材库候选 ID 不一致")
        from core.character_assets import step_output_id
        matches = [step for job in workbench_records(project, "active_jobs") if job["ast_id"] == args.ast_id
                   and not job.get("project_stale") and not job.get("record_deleted_at")
                   for step in job["steps"] if step_output_id(step) == args.output_id
                   and args.artifact_id in step["outputs"] and step["purpose"] == args.purpose]
        if len(matches) != 1:
            raise ValueError("本地制作项与送审候选不符")
        original = Path(asset["original"])
        image = _path(project, Path("04_资产/知识库参考") / asset["id"] / original.name)
        revision, checksum = asset.get("candidate_revision"), asset.get("image_sha256")
        if args.image and Path(args.image).resolve() == original.resolve():
            supplied = image
        else:
            supplied = _path(project, args.image) if args.image else image
        if supplied != image:
            raise ValueError("送审图片路径不属于该候选")
        from visual_asset_backup import same_file
        if not original.is_file() or not image.is_file() or not same_file(original, image):
            raise ValueError("制作项的项目副本与候选原图不符")
    if not isinstance(revision, str) or not revision.strip() or not checksum:
        raise ValueError("候选缺少修订或图片依据")
    if args.shown and args.image and _path(project, args.image) != image:
        raise ValueError("送审图片路径不属于该候选")
    return image, revision, checksum


def submission_path(project, receipt):
    params = receipt["request"]["parameters"]
    # Each operation has one frozen request and successful POST result.
    parts = [receipt["base_url"], params["caseId"], params["operationId"]]
    return _path(project, Path("00_项目管理/资产交审/网站送审") /
                 quote(parts[0], safe="") / ("case-" + quote(parts[1], safe="")) /
                 ("op-" + quote(parts[2], safe="") + ".json"))


def submission_binding(receipt):
    return {key: receipt[key] for key in ("base_url", "request", "image", "candidate_revision",
                                          "candidate_source", "script_version", "script_publication", "reference_publications")}


def verify_submission(project, receipt):
    path = submission_path(project, receipt)
    saved = json.loads(path.read_text()) if path.is_file() else {}
    candidate = receipt["request"]["parameters"].get("artifactId")
    if (not candidate or receipt.get("candidate_source") != "workbench" or not receipt.get("candidate_revision") or not saved.get("remote")
            or saved["binding"] != submission_binding(receipt) or saved["remote"] != receipt["response"]
            or ("artifactId" in saved["remote"] and saved["remote"]["artifactId"] != candidate)):
        raise ValueError("网站回执缺少准确的本地送审关联或候选 ID 不一致")
    return saved["binding"]["request"]["parameters"]["artifactId"]


def _asset_readback(client, receipt):
    published = receipt["response"]
    try:
        current = client.status(case_id=published["caseId"], stage="assets")
    except (AgentReviewError, OSError) as exc:
        return {"status": "unknown", "error": {
            "code": exc.code if isinstance(exc, AgentReviewError) else "LOCAL_IO_ERROR",
            "http_status": getattr(exc, "http_status", None),
        }}
    head = current.get("head") or {}
    decision = current.get("decision")
    validity = decision.get("validity") if isinstance(decision, dict) else None
    source_stale = validity.get("sourceStale") if isinstance(validity, dict) else None
    if current["case"]["projectId"] != published["projectId"]:
        status = "project_mismatch"
    elif head.get("version") != published["version"] or head.get("generation") != published["headGeneration"]:
        # Status describes the newer head. Its source says nothing about this POST.
        status = "historical" if head.get("generation", 0) > published["headGeneration"] else "unknown"
    elif current["sourceScriptVersion"] != receipt["script_version"] or source_stale is True:
        status = "source_mismatch"
    else:
        status = "current" if source_stale is False else "unknown"
    return {"status": status, "response": current}


def _adopt_script(project, publication, receipt_path, args, website_approval):
    try:
        from .stage_context_cli import StageContextError, adopt_package
        from .core.artifacts import artifact_is_adopted, digest, load_manifest
        from .core.project_state import load_project_state
        from .core.revision_control import commit_change, show_change
    except ImportError:
        from stage_context_cli import StageContextError, adopt_package
        from core.artifacts import artifact_is_adopted, digest, load_manifest
        from core.project_state import load_project_state
        from core.revision_control import commit_change, show_change

    source = publication["source_json"]
    artifacts = load_manifest(project)["artifacts"]
    entry = next((row for row in artifacts if row["artifact_id"] == args.adopt_script_id), None)
    if entry and entry.get("status") == "current":
        require_script_approval(project, entry, str(receipt_path), website_approval)
        if (digest(_path(project, source["path"])) != source["sha256"]
                or not artifact_is_adopted(entry, load_project_state(project), project)):
            raise ValueError("已采用的编导稿与本次网站通过不一致")
        if args.change_id and not any(
                change.get("change_id") == args.change_id and change.get("status") == "completed"
                and (change.get("candidate_receipt") or {}).get("artifact_id") == args.adopt_script_id
                for change in show_change(project)["history"]):
            raise ValueError("已采用的编导稿不属于指定变更案")
        return {"artifact_id": args.adopt_script_id, "already_current": True}
    if args.change_id:
        return commit_change(project, change_id=args.change_id, website_approval=website_approval)
    if any(row.get("logical_id") == "narrative.package" and row.get("status") in {"current", "stale"}
           for row in artifacts):
        raise StageContextError("编导稿已有正式版本，请登记返修候选并传入 --change-id", code="SCRIPT_CHANGE_REQUIRED")
    return adopt_package(argparse.Namespace(
        project=str(project), logical_id="narrative.package", artifact_id=args.adopt_script_id,
        path=source["path"], revision=source["revision"], approval_ref=str(receipt_path),
        depends_on=[], scope_description="审核站编导稿整版通过"), website_approval=website_approval)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Validate JSON and create a reading copy locally.")
    prepare.add_argument("--project", required=True, type=Path)
    prepare.add_argument("--narrative", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    prepare.add_argument("--artifact-id", help="交稿并登记准确候选，可用同一命令续作")
    prepare.add_argument("--replaces", help="此新编导候选替代的准确待审候选 ID")
    prepare.add_argument("--chat-review", action="store_true", help="交对话整稿审核")
    create = commands.add_parser("create-case", help="Create one explicitly authorized review case.")
    create.add_argument("--name", required=True)
    create.add_argument("--operation-id", required=True)
    publish = commands.add_parser("publish", help="Submit an exact JSON reading copy as script text.")
    publish.add_argument("--narrative", required=True, type=Path)
    publish.add_argument("--markdown", required=True, type=Path)
    publish.add_argument("--case-id", required=True)
    publish.add_argument("--operation-id", required=True)
    publish.add_argument("--purpose")
    publish.add_argument("--preflight", required=True, type=Path, help="Project status receipt checked before publication.")
    status = commands.add_parser("status", help="Read a stage head; this is not an approval receipt.")
    status.add_argument("--case-id", required=True)
    status.add_argument("--stage", choices=("script", "assets"), default="script")
    asset = commands.add_parser("publish-asset", help="Submit a project image against the approved script.")
    asset.add_argument("--script-publication", required=True, type=Path)
    asset.add_argument("--image", type=Path, help="可选，核对权威候选路径")
    asset.add_argument("--shown", help="普通对话资产使用的项目展示编号")
    asset.add_argument("--candidate-id", "--artifact-id", dest="artifact_id", required=True,
                       help="送审图片的准确本地候选 ID，随网站请求及本地回执保存为 artifactId")
    for name in ("ast-id", "output-id", "purpose", "operation-id"):
        asset.add_argument("--" + name, required=True)
    asset.add_argument("--reference-publication", action="append", default=[], type=Path)
    asset.add_argument("--preflight", type=Path, help="Asset status receipt checked before publication.")
    decision = commands.add_parser("decision", help="Read a submitted decision for the exact published version.")
    decision.add_argument("--publication", required=True, type=Path)
    decision.add_argument("--stage", choices=("script", "assets"), required=True)
    decision.add_argument("--adopt-script-id", help="Adopt the exact script after an effective website approval.")
    decision.add_argument("--change-id", help="Use the active revision change for --adopt-script-id.")
    for command in (create, publish, status, asset, decision):
        command.add_argument("--project", required=True, type=Path)
        command.add_argument("--token-file", required=True, type=Path)
        command.add_argument("--receipt", required=True, type=Path)
        command.add_argument("--base-url", default=DEFAULT_BASE_URL)
        command.add_argument("--timeout", default=20, type=float)
    return parser


def run(args: argparse.Namespace) -> dict:
    project = args.project.resolve(strict=True)
    if not project.is_dir():
        raise ValueError("The project must be an existing directory.")
    with project_lock(project):
        if args.command == "decision":
            if args.adopt_script_id and args.stage != "script":
                raise ValueError("Only a script decision can adopt a narrative package.")
            if args.change_id and not args.adopt_script_id:
                raise ValueError("--change-id requires --adopt-script-id.")
        if args.command == "prepare":
            chat = getattr(args, "chat_review", False)
            ident = getattr(args, "artifact_id", None)
            replaces = getattr(args, "replaces", None)
            if replaces is not None and (not ident or not replaces.strip() or replaces == ident):
                raise ValueError("替代候选须有独立的新旧产物 ID")
            package, source = _source(project, args.narrative, chat=chat)
            output = _path(project, args.output)
            ids = source["awaiting_subtitle_approval_ids"]
            refresh_from = None
            if ident:
                require_production(project)
                manifest = load_manifest(project)
                entry = next((r for r in manifest["artifacts"] if r["artifact_id"] == ident), None)
                fields = dict(artifact_id=ident, logical_id="narrative.package", type="narrative.package",
                              revision=source["revision"], path_or_url=source["path"], checksum=source["sha256"])
                if entry is None and not replaces and any(row.get("revision_requested") and row["status"] == "candidate"
                                                          for row in manifest["artifacts"]):
                    raise ValueError("新编导稿须用 --replaces 指定本次返修的旧候选")
                if entry is None and not replaces and any(
                        row["logical_id"] == "narrative.package" and row["status"] == "candidate"
                        for row in manifest["artifacts"]):
                    raise ValueError("已有本轮编导候选，请沿用原产物 ID、修订和文件路径")
                if replaces and entry is None:
                    prior = pending_narrative_candidate(manifest, replaces)
                    if not prior.get("revision_requested"):
                        raise ValueError("旧候选尚未登记打回，请沿用本轮原文件和修订")
                    previous_source = _path(project, prior["path_or_url"])
                    previous_raw = previous_source.read_bytes()
                    if prior.get("checksum") not in (None, hashlib.sha256(previous_raw).hexdigest()):
                        raise ValueError("旧候选源稿字节已改变，请核对旧稿并从新路径交稿")
                    previous_package = json.loads(previous_raw.decode("utf-8-sig"))
                    if (previous_package.get("project_id") != source["project_id"]
                            or previous_package.get("revision") != prior["revision"]):
                        raise ValueError("替代候选与旧稿的项目身份或原修订不一致")
                    old_reading = prior.get("review_markdown") or prior.get("reading_copy")
                    if (prior["revision"] == source["revision"] or
                            previous_source == _path(project, source["path"]) or
                            (old_reading and _path(project, old_reading["path"]) == output)):
                        raise ValueError("替代候选须另存新修订、JSON 和阅读稿")
                elif replaces and entry.get("replaces") != replaces:
                    raise ValueError("候选 ID 已用于其他替代关系")
                if entry:
                    if (entry.get("revision_requested") or entry["status"] != "candidate" or entry.get("user_approved")
                            or any(entry.get(k) != v for k, v in fields.items() if k != "checksum")):
                        raise ValueError("候选 ID 已用于其他版本")
                    reading = entry.get("review_markdown") or entry.get("reading_copy")
                    if reading and _path(project, reading["path"]) != output:
                        raise ValueError("同一候选须沿用原阅读稿路径")
                    if entry.get("checksum") not in (None, source["sha256"]):
                        publication_started = entry.get("website_publication_started", not chat)
                        if publication_started or not reading:
                            raise ValueError("候选源稿与登记字节不一致，请核对审核渠道和原回执")
                        refresh_from = reading["sha256"]
            if ident and output.exists():
                _, reading = _projection(project, package, output, subtitle_review_ids=ids, refresh_from=refresh_from)
            else:
                export_markdown(package, output, subtitle_review_ids=ids)
                reading = _binding(project, output, output.read_bytes())
            if ident:
                fields["review_markdown" if chat and ids else "reading_copy"] = reading
                if replaces:
                    fields["replaces"] = replaces
                if entry is None:
                    fields["website_publication_started"] = False
                    if replaces:
                        fields["revision_changes"] = _revision_changes(previous_package, package)
                        _keep_edited_reading(project, prior, previous_package, old_reading)
                        prior.update(status="superseded", superseded_by=ident, superseded_at=utc_now())
                    append_workspace_candidate(project, manifest, fields)
                    save_manifest(project, manifest)
                elif any(entry.get(k) != v for k, v in fields.items()):
                    if fields.get("review_markdown") and "reading_copy" in entry:
                        entry.pop("reading_copy")
                    elif fields.get("reading_copy") and "review_markdown" in entry:
                        entry.pop("review_markdown")
                    entry.update(fields)
                    save_manifest(project, manifest)
            response = {"source_json": source, "markdown": reading, **({"artifact_id": ident} if ident else {})}
            if replaces and (changes := (entry or fields).get("revision_changes")) is not None:
                response.update(replaces=replaces, revision_changes=changes)
            return response
        if args.command in {"publish", "publish-asset"}:
            require_production(project)

        receipt_path = _path(project, args.receipt)
        if receipt_path.exists():
            raise FileExistsError("Receipt already exists; choose a new receipt path before retrying.")
        client = AgentReviewClient(base_url=args.base_url, token_file=args.token_file, timeout=args.timeout)
        website_approval = None
        receipt = {"kind": "agent-review-api-receipt", "created_at": utc_now(),
                   "base_url": client.base_url, "project_path": str(project),
                   "command": args.command, "review_approval_inferred": False,
                   "formal_adoption_performed": False}
        if args.command == "create-case":
            parameters = {"name": args.name, "operationId": args.operation_id}
            call = lambda: client.create_case(name=args.name, operation_id=args.operation_id)
            route = "create-case"
        elif args.command == "publish":
            package, source = _source(project, args.narrative)
            text, markdown = _projection(project, package, args.markdown,
                                         subtitle_review_ids=source["awaiting_subtitle_approval_ids"])
            if markdown["bytes"] > MAX_TEXT_BYTES:
                raise AgentReviewError("送审阅读稿超过 100 KiB，请先与审核站确认交付方式。", code="INVALID_INPUT")
            parameters = {"caseId": args.case_id, "stage": "script",
                          "name": Path(args.markdown).name, "operationId": args.operation_id}
            if args.purpose is not None:
                parameters["purpose"] = args.purpose
            receipt.update(source_json=source, markdown=markdown)
            receipt["preflight"] = _check_preflight(
                project, client, args.preflight, case_id=args.case_id, stage="script")
            call = lambda: client.publish(case_id=args.case_id, stage="script", name=parameters["name"],
                                          text=text, operation_id=args.operation_id, purpose=args.purpose)
            route = "publish"
        elif args.command == "publish-asset":
            script = read_publication(project, args.script_publication, client, "script")
            if not effective_approval(read_decision(client, script)):
                raise ValueError("Asset publication requires the current script's effective website approval.")
            _, source, narrative = source_project(project)
            published_source = script["source_json"]
            source_path = _path(project, source["path"])
            if (source_path != _path(project, published_source["path"])
                    or source["revision"] != published_source["revision"]
                    or narrative["project_id"] != published_source["project_id"]
                    or hashlib.sha256(source_path.read_bytes()).hexdigest() != published_source["sha256"]):
                raise ValueError("Asset publication requires the exact locally adopted script and website version.")
            active_asts = {row["asset_id"] for row in image_work_items(narrative)}
            if args.ast_id not in active_asts:
                raise ValueError("Asset publication requires an active image AST in the current project requirements.")
            case_id = script["response"]["caseId"]
            references = [read_publication(project, path, client, "assets")
                          for path in args.reference_publication]
            if any(ref["response"]["caseId"] != case_id or ref["response"]["projectId"] != script["response"]["projectId"]
                   or ref.get("script_version") != script["response"]["version"] for ref in references):
                raise ValueError("Referenced assets must belong to the same case and script version.")
            if any(ref["request"]["parameters"]["astId"] not in active_asts for ref in references):
                raise ValueError("Referenced assets must be active image ASTs in the current project requirements.")
            image, revision, checksum = _publication_candidate(project, args, {**source, "checksum": published_source["sha256"]})
            with image.open("rb") as stream:
                raw = stream.read(MAX_IMAGE_BYTES + 1)
            if len(raw) > MAX_IMAGE_BYTES:
                raise AgentReviewError("Asset image must contain at most 8 MiB.")
            finals = character_output_contract(project, [args.ast_id]).get(args.ast_id, {})
            if finals.get(args.output_id) == "人物1x4" and 0 < len(raw) <= MAX_IMAGE_BYTES:
                if problem := character_board_image_problem(BytesIO(raw)):
                    raise ValueError(f"{args.ast_id}@{args.output_id}：{problem}")
            parameters = {"caseId": case_id, "stage": "assets", "name": image.name,
                          "operationId": args.operation_id, "purpose": args.purpose,
                          "artifactId": args.artifact_id, "astId": args.ast_id, "outputId": args.output_id,
                          "references": [ref["response"]["assetId"] for ref in references]}
            receipt.update(image=_binding(project, image, raw), candidate_revision=revision, candidate_source=args.shown or "workbench", script_version=script["response"]["version"],
                           script_publication=_path(project, args.script_publication).relative_to(project).as_posix(),
                           reference_publications=[_path(project, path).relative_to(project).as_posix()
                                                   for path in args.reference_publication])
            if receipt["image"]["sha256"] != checksum:
                raise ValueError("送审图片与候选记录的字节不符")
            if args.preflight:
                receipt["preflight"] = _check_preflight(
                    project, client, args.preflight, case_id=case_id, stage="assets",
                    project_id=script["response"]["projectId"])
            route = "publish-assets"
            call = lambda: client.publish_asset(case_id=case_id, name=image.name, image=raw,
                                                purpose=args.purpose, operation_id=args.operation_id,
                                                artifact_id=args.artifact_id, ast_id=args.ast_id,
                                                output_id=args.output_id, references=parameters["references"])
            receipt["readback"] = {"status": "unknown"}
        elif args.command == "decision":
            publication = read_publication(project, args.publication, client, args.stage)
            parameters = {key: publication["response"][key] for key in ("caseId", "stage", "version")}
            receipt["publication"] = _path(project, args.publication).relative_to(project).as_posix()
            if args.stage == "script":
                receipt["source_json"] = publication["source_json"]
            def call():
                nonlocal website_approval
                if args.adopt_script_id:
                    decision, website_approval = read_script_approval(
                        project, publication, client, artifact_id=args.adopt_script_id, receipt_path=receipt_path)
                    return decision
                return read_decision(client, publication)
            route = "decision"
        else:
            parameters = {"caseId": args.case_id, "stage": args.stage}
            call = lambda: client.status(case_id=args.case_id, stage=args.stage)
            route = "status"
        receipt["request"] = {"method": "GET" if route in {"status", "decision"} else "POST",
                              "path": "/api/agent/review/" + route, "parameters": parameters}
        if route == "publish":
            receipt["request"].update(text_sha256=markdown["sha256"], text_bytes=markdown["bytes"])
            manifest = load_manifest(project)
            candidate = next((row for row in manifest["artifacts"] if (
                row["logical_id"], row["status"], row.get("path_or_url"), row.get("revision")
            ) == ("narrative.package", "candidate", source["path"], source["revision"])), None)
            if candidate:
                if candidate.get("checksum") not in (None, source["sha256"]):
                    raise ValueError("待发布的编导稿与候选登记字节不一致，请先核对原文件")
                if not candidate.get("website_publication_started"):
                    candidate["website_publication_started"] = True
                    save_manifest(project, manifest)
        if route == "publish-assets":
            operation = submission_path(project, receipt)
            if receipt_path == operation or operation.parent.parent.parent in receipt_path.parents:
                raise ValueError("回执位置不能覆盖送审绑定")
            binding = submission_binding(receipt)
            saved = json.loads(operation.read_text()) if operation.is_file() else {"binding": binding}
            if saved["binding"] != binding:
                raise ValueError("同一网站操作已绑定其他候选或修订")
            write_json(operation, saved)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        # Reserve before networking: a successful remote write must have a local
        # destination. No automatic POST retry is hidden behind this command.
        with receipt_path.open("x", encoding="utf-8", newline="\n") as output:
            try:
                receipt["response"] = call()
            except (ValueError, OSError, KeyError, TypeError) as exc:
                if args.command != "decision" and not isinstance(exc, (AgentReviewError, OSError)):
                    raise
                code = (exc.code if isinstance(exc, AgentReviewError) else
                        "LOCAL_IO_ERROR" if isinstance(exc, OSError) else "INVALID_LOCAL_BINDING")
                receipt.update(response=None, error={"code": code,
                                                     "http_status": getattr(exc, "http_status", None)})
                json.dump(receipt, output, ensure_ascii=False, indent=2)
                output.write("\n")
                raise
            json.dump(receipt, output, ensure_ascii=False, indent=2)
            output.write("\n")
            if route == "publish-assets":
                remote = receipt["response"]
                if (("artifactId" in remote and remote["artifactId"] != args.artifact_id)
                        or saved.get("remote", remote) != remote):
                    raise ValueError("网站返回了不同送审候选或操作结果")
                write_json(operation, {"binding": binding, "remote": remote})
        if "preflight" in receipt and (
                receipt["response"].get("projectId") != receipt["preflight"]["response"]["case"]["projectId"]):
            raise ValueError("发布回执的网站项目与预读不符，已保留回执供核查")
        result = {"receipt": str(receipt_path), "response": receipt["response"]}
        if route == "publish-assets":
            # The durable POST receipt remains usable even if this GET is interrupted.
            readback = _asset_readback(client, receipt)
            write_json(receipt_path, {**receipt, "readback": readback})
            result["readback"] = readback
        if website_approval is not None:
            try:
                result["adoption"] = _adopt_script(project, publication, receipt_path, args, website_approval)
            except (OSError, ValueError, TypeError, KeyError) as cause:
                blocked = _ScriptAdoptionBlocked(cause, args=args, receipt_path=receipt_path, receipt=receipt)
                try:
                    write_json(receipt_path, {**receipt, "local_adoption": blocked.report})
                except OSError:
                    blocked.report["local_adoption_note_saved"] = False
                raise blocked from cause
        return result


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except _ScriptAdoptionBlocked as exc:
        parser.exit(2, json.dumps(exc.report, ensure_ascii=False) + "\n")
    except AgentReviewError as exc:
        parser.exit(2, "Agent Review failed (" + exc.code + "); check the receipt and remote status before retrying.\n")
    except (OSError, ValueError, TypeError, KeyError):
        # Never echo arbitrary source text, response bodies, or credentials.
        parser.exit(2, "Review command failed local validation or file access; no approval was recorded.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
