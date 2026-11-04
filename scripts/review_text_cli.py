#!/usr/bin/env python3
"""Export authoritative narrative JSON and send its exact reading copy for review.

Only script text is supported here. Publication and status receipts never grant
approval or change a local manuscript, manifest, or project state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

try:
    from .core.agent_review_api import AgentReviewClient, AgentReviewError, DEFAULT_BASE_URL
    from .core.io import project_lock, utc_now
    from .core.story_sources import export_markdown, validate_story_sources
except ImportError:  # direct execution
    from core.agent_review_api import AgentReviewClient, AgentReviewError, DEFAULT_BASE_URL
    from core.io import project_lock, utc_now
    from core.story_sources import export_markdown, validate_story_sources


def _path(project: Path, value: str | Path) -> Path:
    candidate = Path(value)
    candidate = (candidate if candidate.is_absolute() else project / candidate).resolve()
    if not candidate.is_relative_to(project):
        raise ValueError("Review input and output paths must stay inside the project.")
    return candidate


def _binding(project: Path, path: Path, raw: bytes) -> dict:
    return {"path": path.relative_to(project).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw)}


def _source(project: Path, value: str | Path) -> tuple[dict, dict]:
    path = _path(project, value)
    raw = path.read_bytes()
    package = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(package, dict) or package.get("status") not in {"candidate", "confirmed"}:
        raise ValueError("Only a candidate or confirmed narrative revision can be submitted.")
    if package.get("open_questions"):
        raise ValueError("Close the narrative's open questions before preparing or submitting it.")
    validate_story_sources(package, project_root=project, require_dialogue_evidence=True)
    return package, {**_binding(project, path, raw), "project_id": package["project_id"],
                     "revision": package["revision"], "status": package["status"]}


def _projection(project: Path, package: dict, value: str | Path) -> tuple[str, dict]:
    path = _path(project, value)
    raw = path.read_bytes()
    with tempfile.TemporaryDirectory(prefix=".review-export-", dir=project) as folder:
        expected = Path(folder) / "reading.md"
        export_markdown(package, expected)
        if raw != expected.read_bytes():
            raise ValueError("Reading Markdown differs from this exact narrative JSON; export a new copy.")
    return raw.decode("utf-8"), _binding(project, path, raw)


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
    for command in (create, publish, status):
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
        if args.command == "prepare":
            package, source = _source(project, args.narrative)
            output = _path(project, args.output)
            export_markdown(package, output)
            return {"source_json": source, "markdown": _binding(project, output, output.read_bytes())}

        receipt_path = _path(project, args.receipt)
        if receipt_path.exists():
            raise FileExistsError("Receipt already exists; choose a new receipt path before retrying.")
        client = AgentReviewClient(base_url=args.base_url, token_file=args.token_file, timeout=args.timeout)
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
        else:
            parameters = {"caseId": args.case_id, "stage": "script"}
            call = lambda: client.status(case_id=args.case_id, stage="script")
            route = "status"
        receipt["request"] = {"method": "GET" if route == "status" else "POST",
                              "path": "/api/agent/review/" + route, "parameters": parameters}
        if route == "publish":
            receipt["request"].update(text_sha256=markdown["sha256"], text_bytes=markdown["bytes"])
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        # Reserve before networking: a successful remote write must have a local
        # destination. No automatic POST retry is hidden behind this command.
        with receipt_path.open("x", encoding="utf-8", newline="\n") as output:
            try:
                receipt["response"] = call()
            except (AgentReviewError, OSError) as exc:
                receipt.update(response=None, error={"code": exc.code if isinstance(exc, AgentReviewError) else "LOCAL_IO_ERROR",
                                                     "http_status": getattr(exc, "http_status", None)})
                json.dump(receipt, output, ensure_ascii=False, indent=2)
                output.write("\n")
                raise
            json.dump(receipt, output, ensure_ascii=False, indent=2)
            output.write("\n")
        return {"receipt": str(receipt_path), "response": receipt["response"]}


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
