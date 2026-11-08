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
import tempfile
from io import BytesIO
from pathlib import Path

try:
    from .core.agent_review_api import AgentReviewClient, AgentReviewError, DEFAULT_BASE_URL, MAX_IMAGE_BYTES, effective_approval
    from .core.asset_refs import character_output_contract
    from .core.candidate_test import require_production
    from .core.image_technical import character_board_image_problem
    from .core.io import project_lock, utc_now, write_json
    from .core.review_publication import (project_file as _path, read_decision, read_publication,
                                          read_script_approval, require_script_approval)
    from .core.story_sources import export_markdown, validate_story_sources
    from .visual_asset_choices import image_work_items
    from .visual_asset_source import source_project
except ImportError:  # direct execution
    from core.agent_review_api import AgentReviewClient, AgentReviewError, DEFAULT_BASE_URL, MAX_IMAGE_BYTES, effective_approval
    from core.asset_refs import character_output_contract
    from core.candidate_test import require_production
    from core.image_technical import character_board_image_problem
    from core.io import project_lock, utc_now, write_json
    from core.review_publication import (project_file as _path, read_decision, read_publication,
                                         read_script_approval, require_script_approval)
    from core.story_sources import export_markdown, validate_story_sources
    from visual_asset_choices import image_work_items
    from visual_asset_source import source_project


def _binding(project: Path, path: Path, raw: bytes) -> dict:
    return {"path": path.relative_to(project).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw)}


def _source(project: Path, value: str | Path) -> tuple[dict, dict]:
    path = _path(project, value)
    raw = path.read_bytes()
    package = json.loads(raw.decode("utf-8-sig"))
    review_check = validate_story_sources(package, project_root=project, review_ready=True)
    return package, {**_binding(project, path, raw), "project_id": package["project_id"],
                     "revision": package["revision"], "status": package["status"],
                     "awaiting_creative_approval_ids": review_check["awaiting_creative_approval_ids"]}


def _projection(project: Path, package: dict, value: str | Path) -> tuple[str, dict]:
    path = _path(project, value)
    raw = path.read_bytes()
    with tempfile.TemporaryDirectory(prefix=".review-export-", dir=project) as folder:
        expected = Path(folder) / "reading.md"
        export_markdown(package, expected)
        if raw != expected.read_bytes():
            raise ValueError("Reading Markdown differs from this exact narrative JSON; export a new copy.")
    return raw.decode("utf-8"), _binding(project, path, raw)


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Validate JSON and create a reading copy locally.")
    prepare.add_argument("--project", required=True, type=Path)
    prepare.add_argument("--narrative", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    create = commands.add_parser("create-case", help="Create one explicitly authorized review case.")
    create.add_argument("--name", required=True)
    create.add_argument("--operation-id", required=True)
    publish = commands.add_parser("publish", help="Submit an exact JSON reading copy as script text.")
    publish.add_argument("--narrative", required=True, type=Path)
    publish.add_argument("--markdown", required=True, type=Path)
    publish.add_argument("--case-id", required=True)
    publish.add_argument("--operation-id", required=True)
    publish.add_argument("--purpose")
    status = commands.add_parser("status", help="Read the script head; this is not an approval receipt.")
    status.add_argument("--case-id", required=True)
    asset = commands.add_parser("publish-asset", help="Submit a project image against the approved script.")
    asset.add_argument("--script-publication", required=True, type=Path)
    asset.add_argument("--image", required=True, type=Path)
    for name in ("artifact-id", "ast-id", "output-id", "purpose", "operation-id"):
        asset.add_argument("--" + name, required=True)
    asset.add_argument("--reference-publication", action="append", default=[], type=Path)
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
            package, source = _source(project, args.narrative)
            output = _path(project, args.output)
            export_markdown(package, output)
            return {"source_json": source, "markdown": _binding(project, output, output.read_bytes())}
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
            text, markdown = _projection(project, package, args.markdown)
            parameters = {"caseId": args.case_id, "stage": "script",
                          "name": Path(args.markdown).name, "operationId": args.operation_id}
            if args.purpose is not None:
                parameters["purpose"] = args.purpose
            receipt.update(source_json=source, markdown=markdown)
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
            image = _path(project, args.image)
            with image.open("rb") as stream:
                raw = stream.read(MAX_IMAGE_BYTES + 1)
            finals = character_output_contract(project, [args.ast_id]).get(args.ast_id, {})
            if finals.get(args.output_id) == "人物1x4" and 0 < len(raw) <= MAX_IMAGE_BYTES:
                if problem := character_board_image_problem(BytesIO(raw)):
                    raise ValueError(f"{args.ast_id}@{args.output_id}：{problem}")
            parameters = {"caseId": case_id, "stage": "assets", "name": image.name,
                          "operationId": args.operation_id, "purpose": args.purpose,
                          "artifactId": args.artifact_id, "astId": args.ast_id, "outputId": args.output_id,
                          "references": [ref["response"]["assetId"] for ref in references]}
            receipt.update(image=_binding(project, image, raw), script_version=script["response"]["version"],
                           script_publication=_path(project, args.script_publication).relative_to(project).as_posix(),
                           reference_publications=[_path(project, path).relative_to(project).as_posix()
                                                   for path in args.reference_publication])
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
            parameters = {"caseId": args.case_id, "stage": "script"}
            call = lambda: client.status(case_id=args.case_id, stage="script")
            route = "status"
        receipt["request"] = {"method": "GET" if route in {"status", "decision"} else "POST",
                              "path": "/api/agent/review/" + route, "parameters": parameters}
        if route == "publish":
            receipt["request"].update(text_sha256=markdown["sha256"], text_bytes=markdown["bytes"])
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
        result = {"receipt": str(receipt_path), "response": receipt["response"]}
        if route == "publish-assets":
            # The durable POST receipt remains usable even if this GET is interrupted.
            readback = _asset_readback(client, receipt)
            write_json(receipt_path, {**receipt, "readback": readback})
            result["readback"] = readback
        if website_approval is not None:
            try:
                from .stage_context_cli import adopt_package
                from .core.artifacts import artifact_is_adopted, digest, load_manifest
                from .core.project_state import load_project_state
                from .core.revision_control import commit_change, show_change
            except ImportError:
                from stage_context_cli import adopt_package
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
                result["adoption"] = {"artifact_id": args.adopt_script_id, "already_current": True}
            elif args.change_id:
                result["adoption"] = commit_change(project, change_id=args.change_id, website_approval=website_approval)
            else:
                if any(row.get("logical_id") == "narrative.package" and row.get("status") in {"current", "stale"}
                       for row in artifacts):
                    raise ValueError("编导稿已有正式版本，请登记返修候选并传入 --change-id")
                result["adoption"] = adopt_package(argparse.Namespace(
                    project=str(project), logical_id="narrative.package", artifact_id=args.adopt_script_id,
                    path=source["path"], revision=source["revision"], approval_ref=str(receipt_path),
                    depends_on=[], scope_description="审核站编导稿整版通过"), website_approval=website_approval)
        return result


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except AgentReviewError as exc:
        parser.exit(2, "Agent Review failed (" + exc.code + "); check the receipt and remote status before retrying.\n")
    except (OSError, ValueError, TypeError, KeyError):
        # Never echo arbitrary source text, response bodies, or credentials.
        parser.exit(2, "Review command failed local validation or file access; no approval was recorded.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
