#!/usr/bin/env python3
"""Lean CLI for project status, kickoff, workflow authorization, artifact adoption, revision control, and handoff."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__:
    from .core import stage_context as context
    from .core.stage_handoff import _confirmed_handoff_inputs, handoff, status
else:
    from core import stage_context as context
    from core.stage_handoff import _confirmed_handoff_inputs, handoff, status


def __getattr__(name):
    return getattr(context, name)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    state_cmd = commands.add_parser("status", help="只读查看项目状态与续跑阻塞")
    state_cmd.add_argument("--project", required=True)

    authorization = commands.add_parser("authorize-workflow", help="登记已有准确业务流程授权；不确认产物或生成")
    authorization.add_argument("--project", required=True)
    authorization.add_argument("--authorization-id", required=True)
    authorization.add_argument("--authorization-ref", required=True)
    authorization.add_argument("--skill", action="append", required=True)

    change = commands.add_parser("open-change", help="为已采用的一项产物登记内容返修")
    change.add_argument("--project", required=True)
    change.add_argument("--change-id", required=True)
    change.add_argument("--target-logical-id", required=True)
    change.add_argument("--reason", required=True)
    change.add_argument("--authorization-id")

    acceptance = commands.add_parser("accept-artifact", help="凭流程授权及准确自查采用已登记候选")
    acceptance.add_argument("--project", required=True)
    acceptance.add_argument("--artifact-id", required=True)
    acceptance.add_argument("--self-check-json", required=True)
    acceptance.add_argument("--change-id")
    acceptance.add_argument("--preflight-json")

    adopt = commands.add_parser("adopt-package", help="登记并确认初次平台无关正式稿件")
    adopt.add_argument("--project", required=True)
    adopt.add_argument("--artifact-id", required=True)
    adopt.add_argument("--logical-id", choices=("narrative.package", "storyboard.package", "video.prompt"), required=True)
    adopt.add_argument("--path", required=True)
    adopt.add_argument("--revision", required=True)
    adopt.add_argument("--approval-ref", required=True)
    adopt.add_argument("--scope-description", default="")
    adopt.add_argument("--depends-on", action="append", default=[])

    chat = commands.add_parser("confirm-script-in-chat", help="以用户对准确版本的整稿对话确认正式采用编导稿")
    chat.add_argument("--project", required=True)
    chat.add_argument("--artifact-id", required=True)
    chat.add_argument("--path", required=True)
    chat.add_argument("--revision", required=True)
    chat.add_argument("--sha256", required=True)
    chat.add_argument("--confirmation-json", required=True)
    chat.add_argument("--change-id")

    testing = commands.add_parser("candidate-test", help="显式配置独立候选试跑；绝不登记用户产物确认")
    testing.add_argument("--project", required=True)
    testing.add_argument("--run-id", required=True)
    testing.add_argument("--authorization-ref", required=True)
    testing.add_argument("--input-artifact", action="append", default=[])
    testing.add_argument("--usage-contract-json")
    testing.add_argument("--skill")

    kickoff = commands.add_parser("kickoff", help="核对或登记现有项目需求中的开工决定")
    kickoff.add_argument("--project", required=True, type=Path)
    kickoff.add_argument("--answers-json", type=Path)
    kickoff.add_argument("--authorization-id")
    kickoff.add_argument("--authorize-skill", action="append", default=[])
    kickoff.add_argument("--authorization-ref")

    handoff_cmd = commands.add_parser("handoff", help="生成或读取阶段交接卡")
    handoff_cmd.add_argument("--candidate-test", action="store_true")
    handoff_cmd.add_argument("--project", required=True)
    handoff_cmd.add_argument("--to-skill")
    handoff_cmd.add_argument("--from-skill")
    handoff_cmd.add_argument("--authorization-id")
    handoff_cmd.add_argument("--objective")
    handoff_cmd.add_argument("--stop-at")
    handoff_cmd.add_argument("--request-json")
    handoff_cmd.add_argument("--batch-receipt")
    handoff_cmd.add_argument("--receive-batch", action="store_true")
    handoff_cmd.add_argument("--context-id")

    prompt_input = commands.add_parser("recreation-prompt-input", help="只读提取深度复刻写词的本段源文、对白、接缝和参考")
    prompt_input.add_argument("--project", type=Path, required=True)
    prompt_input.add_argument("--segment-id", action="append", required=True)

    check = commands.add_parser("validate")
    check.add_argument("--kind", choices=sorted(context.LIMITS), required=True)
    check.add_argument("--path", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    handlers = {
        "status": status,
        "authorize-workflow": context.authorize_workflow,
        "accept-artifact": context.accept_artifact,
        "adopt-package": context.adopt_package,
        "confirm-script-in-chat": context.confirm_script_in_chat,
        "handoff": handoff,
    }
    if args.command in handlers:
        result = handlers[args.command](args)
    elif args.command == "open-change":
        project = Path(args.project).resolve()
        context.require_production(project)
        if not all(value.strip() for value in (args.change_id, args.reason, args.target_logical_id)):
            raise context.StageContextError("变更案须有编号、准确目标及本次修改内容")
        result = context.open_change(
            project, change_id=args.change_id, tier="T1",
            logical_ids=[args.target_logical_id], reason=args.reason,
            authorization_id=args.authorization_id,
        )
        result.update(change_id=args.change_id, target_logical_id=args.target_logical_id)
    elif args.command == "candidate-test":
        usage_contract = None
        if args.usage_contract_json:
            usage_contract = context._read_json(Path(args.usage_contract_json), default=None)
            context.validate_usage_contract(usage_contract)
        result = context.configure_test(
            Path(args.project), args.run_id, args.authorization_ref,
            args.input_artifact, current_stage=args.skill, usage_contract=usage_contract,
        )
    elif args.command == "kickoff":
        answers = context._json_input(args.answers_json) if args.answers_json else None
        result = context.kickoff_project(args.project, answers)
        if args.authorization_id or args.authorize_skill or args.authorization_ref:
            if not args.authorization_id or not args.authorize_skill:
                raise context.StageContextError("同一步登记流程授权须同时提供 --authorization-id 和 --authorize-skill")
            kickoff_data = result.get("kickoff") or {}
            auth_ref = args.authorization_ref or kickoff_data.get("decision_ref")
            if not auth_ref:
                raise context.StageContextError("登记流程授权须提供 --authorization-ref 或已登记的 kickoff.decision_ref")
            result["authorization"] = context.register_workflow_authorization(
                args.project.expanduser().resolve(),
                authorization_id=args.authorization_id,
                authorization_ref=auth_ref,
                skills=list(args.authorize_skill),
            )
    elif args.command == "recreation-prompt-input":
        try:
            result = context.recreation_prompt_context(args.project.resolve(strict=True), args.segment_id)
        except (KeyError, TypeError) as exc:
            raise context.StageContextError(f"局部写词输入结构不完整：{exc}") from exc
    else:
        result = context.validate_text(args.kind, Path(args.path).expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except context.StageContextError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc
