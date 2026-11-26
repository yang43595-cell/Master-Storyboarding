#!/usr/bin/env python3
"""Build and validate bounded stage context files without starting agents."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__:
    from .core import stage_context as context
    from .core.stage_handoff import _confirmed_handoff_inputs, handoff, status
    from .core.stage_review import review_pack
else:
    from core import stage_context as context
    from core.stage_handoff import _confirmed_handoff_inputs, handoff, status
    from core.stage_review import review_pack


def __getattr__(name):
    """Preserve Python callers of the original command module."""
    return getattr(context, name)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    state_cmd = commands.add_parser("status", help="只读查看项目状态与续跑阻塞；不执行事务恢复")
    state_cmd.add_argument("--project", required=True)
    authorization = commands.add_parser("authorize-workflow", help="登记已有准确业务流程授权；不确认产物或生成")
    authorization.add_argument("--project", required=True)
    authorization.add_argument("--authorization-id", required=True)
    authorization.add_argument("--authorization-ref", required=True, help="用户对本项目及列明阶段的准确流程授权依据")
    authorization.add_argument("--skill", action="append", required=True, help="授权自行验收的阶段，可重复；范围由核心入口核验")
    change = commands.add_parser("open-change", help="为已采用的一项产物登记内容返修，后续准备复用同一变更案")
    change.add_argument("--project", required=True)
    change.add_argument("--change-id", required=True)
    change.add_argument("--target-logical-id", required=True)
    change.add_argument("--reason", required=True, help="用户要求的具体修改")
    change.add_argument("--authorization-id", help="本次适用的既有自主修订授权")
    acceptance = commands.add_parser("accept-artifact", help="凭流程授权及准确自查采用已登记候选；不新增人工确认")
    acceptance.add_argument("--project", required=True)
    acceptance.add_argument("--artifact-id", required=True)
    acceptance.add_argument("--self-check-json", required=True, help="项目内自查 JSON 的相对路径或绝对路径")
    acceptance.add_argument("--change-id", help="自主修订的原活动变更案；与候选准确匹配后验收并提交")
    acceptance.add_argument("--preflight-json", help="采用回读后只读预检的单段 frozen JSON；失败时沿同一入口续作")
    adopt = commands.add_parser("adopt-package", help="登记并确认初次平台无关正式稿件；不上传或发布")
    adopt.add_argument("--project", required=True)
    adopt.add_argument("--artifact-id", required=True)
    adopt.add_argument("--logical-id", choices=("narrative.package", "storyboard.package", "video.prompt"), required=True)
    adopt.add_argument("--path", required=True, help="项目内的正式 JSON 相对路径")
    adopt.add_argument("--revision", required=True)
    adopt.add_argument("--approval-ref", required=True, help="准确用户整稿确认依据")
    adopt.add_argument("--scope-description", default="", help="本次整稿确认范围说明")
    adopt.add_argument("--depends-on", action="append", default=[], help="准确上游逻辑产物，可重复")
    chat = commands.add_parser("confirm-script-in-chat", help="以用户对准确版本的整稿对话确认正式采用编导稿")
    chat.add_argument("--project", required=True)
    chat.add_argument("--artifact-id", required=True)
    chat.add_argument("--path", required=True, help="项目内准确叙事包 JSON 路径")
    chat.add_argument("--revision", required=True)
    chat.add_argument("--sha256", required=True, help="交给用户审阅的 JSON 文件原始字节 SHA-256")
    chat.add_argument("--confirmation-json", required=True, help="确认依据 JSON 文件，或 - 从标准输入读取；含用户原话、时区时间、消息位置及整稿范围")
    chat.add_argument("--change-id", help="返修时对应已登记候选的活动变更案")
    testing = commands.add_parser("candidate-test", help="显式配置独立候选试跑；绝不登记用户产物确认")
    testing.add_argument("--project", required=True)
    testing.add_argument("--run-id", required=True)
    testing.add_argument("--authorization-ref", required=True)
    testing.add_argument("--input-artifact", action="append", default=[])
    testing.add_argument("--usage-contract-json", help="明确受控试跑的持久计量要求；缺真实 turn 可登记 null 并阻止准备")
    testing.add_argument("--skill", help="显式设置本轮候选阶段；须为现有 Skill，不设置生产 next_stage 或确认")
    source_index = commands.add_parser("candidate-source-index", help="只读核验 profile 固定的候选来源快照并列出可试用规则")
    source_index.add_argument("--skill", required=True)
    source_index.add_argument("--source-id", required=True)
    source_index.add_argument("--snapshot", required=True, help="显式提供已核验 Library 快照的本地物化路径")
    kickoff = commands.add_parser("kickoff", help="核对或登记现有项目需求中的开工决定")
    kickoff.add_argument("--project", required=True, type=Path)
    kickoff.add_argument("--answers-json", type=Path, help="已确认的开工决定 JSON 文件；- 从标准输入读取；省略则只读")
    start = commands.add_parser("prepare")
    start.add_argument("--project", required=True)
    start.add_argument("--skill", required=True)
    start.add_argument("--objective", required=True)
    start.add_argument("--request-json")
    start.add_argument("--batch-receipt", help="固定本批现有成图实看回执的项目内路径")
    start.add_argument("--vault", help="兼容旧命令；如提供，必须是本仓库 knowledge/runtime")
    start.add_argument("--actor-id", help="实际执行此任务的 agent 标识；缺失时阅读声明不能放行")
    start.add_argument("--task-id", help="稳定任务标识；同一任务重 prepare 可复用同版阅读声明")
    start.add_argument("--change-id")
    start.add_argument("--target-logical-id", action="append")
    start.add_argument("--test-proposed-method", action="append", default=[], help="仅隔离测试：显式加载一个 proposed 方法；其启动包会被生产前校验拒绝，可重复")
    start.add_argument("--test-profile-json", help="仅隔离测试：包含 proposed 方法绑定的临时 profile；必须同时给出 --test-proposed-method")
    gate = commands.add_parser("ensure-prepared")
    gate.add_argument("--project", required=True)
    gate.add_argument("--skill", required=True, help="当前生产 Skill；启动包 Skill 必须与之匹配")
    gate.add_argument('--show-task-basis', action='store_true', help='通过门禁后返回不含后台目录的任务阅读视图')
    gate.add_argument("--actor-id", help="实际执行此任务的 agent 标识")
    gate.add_argument("--task-id", help="稳定任务标识")
    gate.add_argument("--required-rule", action="append", default=[], help="本任务必需的动态规则 ID，可重复")
    gate.add_argument("--required-reference", action="append", default=[], help="本任务必需的本地规范，仓库相对路径，可重复")
    gate.add_argument("--mark-read", action="store_true", help="读完后在同一调用登记本轮必读方法，再执行产前门禁")
    gate.add_argument("--method", action="append", default=[], help="已完整阅读的必读专业方法 ID；随 --mark-read 使用，可重复")
    gate.add_argument("--candidate-scope", action="append", default=[], help="跨职责候选的本职采用范围：ID=范围与理由；随 --mark-read 使用")
    usage_check = commands.add_parser("usage-check", help="直接读取或画布批次的只读计量检查，不创建准备包")
    usage_check.add_argument("--project", required=True)
    for command in (start, gate):
        command.add_argument("--candidate-test", action="store_true", help="仅隔离候选试跑；不构成生产放行")
        command.add_argument("--candidate-source-id", help="profile 中固定的候选来源 ID，仅 candidate-test 使用")
        command.add_argument("--candidate-source-snapshot", help="显式提供来源快照物化文件，仅 candidate-test 使用")
        command.add_argument("--candidate-rule", action="append", default=[], help="指定进入隔离试用包的 candidate 状态规则，可重复")
    for command in (start, gate, usage_check):
        command.add_argument("--usage-log", action="append", help="本次 Codex 原始日志，可重复提供窗口分片")
        command.add_argument("--usage-turn-id", help="准确的本轮 turn_id；不以文件名代替边界")
        command.add_argument("--usage-boundaries-json", help="显式累计边界 JSON；不自动发现宿主历史")
        command.add_argument("--max-input", type=int, help="任一次已观测输入的停止阈值（含缓存）")
        command.add_argument("--max-total", type=int, help="本轮累计 token 停止阈值")
        command.add_argument("--max-requests", type=int, help="本轮已记录模型请求次数停止阈值")
    receipt = commands.add_parser("record-method-reading")
    receipt.add_argument("--project", required=True)
    receipt.add_argument("--skill", required=True)
    receipt.add_argument("--actor-id", help="实际执行此任务的 agent 标识")
    receipt.add_argument("--task-id", help="稳定任务标识")
    receipt.add_argument("--method", action="append", default=[], help="已完整阅读的必读专业方法 ID，可重复")
    receipt.add_argument("--candidate-rule", action="append", default=[], help="实际采用并按精确目录读取的候选规则 ID，可重复")
    receipt.add_argument("--candidate-scope", action="append", default=[], help="跨职责候选的本职采用范围：ID=范围及适用理由；范围外内容不成为产出要求")
    receipt.add_argument("--vault", help="兼容旧命令；如提供，必须是本仓库 knowledge/runtime")
    review = commands.add_parser("review-pack")
    review.add_argument("--project", required=True)
    review.add_argument("--skill", required=True)
    review.add_argument("--artifact", action="append", required=True)
    review.add_argument("--review-kind", choices=("production", "skill_contract"), default="production", help="生产候选或 Skill 合同审查；两者不混用")
    review.add_argument("--check", action="append", default=[])
    review.add_argument("--evidence", action="append", default=[], help="明确列入审核包的项目外只读文件证据")
    review.add_argument("--review-mode", choices=("initial", "revision"), default="initial")
    review.add_argument("--formal-archive", action="store_true", help="仅用户要求正式留档时启用版本目录、稳定问题 ID 及真实基线对比")
    review.add_argument("--change-summary", action="append", default=[], help="轻复审实际改动的位置、内容与直接影响；候选字节改变时必须提供，可重复")
    review.add_argument("--previous-review", help="上轮发现 JSON（issues 数组）；轻流程只要求四项发现，正式留档沿用旧结构")
    review.add_argument("--baseline", action="append", help="上轮被审文件快照；顺序与上轮 scope 和本轮 artifact 一致")
    review.add_argument("--producer-response", help="本轮生产者逐问题回复 JSON：{\"responses\":[{id,summary,evidence}]}；不另建 change-log")
    review.add_argument("--change-id")
    review.add_argument("--target-logical-id", action="append")
    review.add_argument("--usage-phase", choices=("production", "self_check", "initial_review", "revision_review"), help="本次用量记录所属阶段；只标注已观测计量，不声称控制宿主请求")
    for option, kwargs in (("--usage-log", {"action": "append"}), ("--usage-turn-id", {}), ("--max-input", {"type": int}), ("--max-total", {"type": int}), ("--max-requests", {"type": int})):
        review.add_argument(option, **kwargs)
    review.add_argument("--usage-boundaries-json", help="显式累计边界 JSON；与单轮参数二选一")
    handoff_cmd = commands.add_parser("handoff")
    handoff_cmd.add_argument("--candidate-test", action="store_true")
    handoff_cmd.add_argument("--project", required=True)
    handoff_cmd.add_argument("--to-skill", help="绑定已有目标的具体执行者、批次或兼容交接；默认由程序解析")
    handoff_cmd.add_argument("--from-skill", help="协调方显式登记已到达的来源阶段，须同时引用现有流程授权")
    handoff_cmd.add_argument("--authorization-id", help="显式登记正式流转时使用的现有流程授权 ID")
    handoff_cmd.add_argument("--objective", help="本轮准确目标，写入现有交接卡")
    handoff_cmd.add_argument("--stop-at", help="本轮完成条件与停止点，不能扩展已有授权")
    handoff_cmd.add_argument("--actor-id", help="普通编导或导演交接的实际接收执行者，与 --task-id 同用")
    handoff_cmd.add_argument("--task-id", help="接收方的稳定任务 ID，与 --actor-id 同用")
    handoff_cmd.add_argument("--request-json", help="资产批次沿用含 image_targets 的现有请求")
    handoff_cmd.add_argument("--batch-receipt", help="本批实际实看回执的准确项目内路径")
    handoff_cmd.add_argument("--receive-batch", action="store_true", help="只读接收本批的紧凑结果，不更新交接或项目状态")
    prompt_input = commands.add_parser("recreation-prompt-input", help="只读提取深度复刻写词的本段源文、对白、接缝和参考")
    prompt_input.add_argument("--project", type=Path, required=True)
    prompt_input.add_argument("--segment-id", action="append", required=True, help="本次实际负责的分段号，可重复指定不同段")
    for command in (start, gate, receipt, review, handoff_cmd):
        command.add_argument("--context-id", help="可选独立批次上下文 ID；仅隔离准备/阅读/交接文件，不隔离项目状态或批准")
    check = commands.add_parser("validate")
    check.add_argument("--kind", choices=sorted(context.LIMITS), required=True)
    check.add_argument("--path", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    handlers = {"status": status, "authorize-workflow": context.authorize_workflow,
                "accept-artifact": context.accept_artifact, "adopt-package": context.adopt_package,
                "confirm-script-in-chat": context.confirm_script_in_chat, "candidate-source-index": context.candidate_source_index,
                "prepare": context.prepare, "ensure-prepared": context.ensure_prepared,
                "record-method-reading": context.record_method_reading, "review-pack": review_pack, "handoff": handoff}
    if args.command in handlers:
        result = handlers[args.command](args)
    elif args.command == "open-change":
        project = Path(args.project).resolve()
        context.require_production(project)
        if not all(value.strip() for value in (args.change_id, args.reason, args.target_logical_id)):
            raise context.StageContextError("变更案须有编号、准确目标及本次修改内容")
        result = context.open_change(project, change_id=args.change_id, tier="T1", logical_ids=[args.target_logical_id],
                             reason=args.reason, authorization_id=args.authorization_id)
        result.update(change_id=args.change_id, target_logical_id=args.target_logical_id)
    elif args.command == "candidate-test":
        usage_contract = None
        if args.usage_contract_json:
            usage_contract = context._read_json(Path(args.usage_contract_json), default=None)
            context.validate_usage_contract(usage_contract)
        result = context.configure_test(Path(args.project), args.run_id, args.authorization_ref, args.input_artifact, current_stage=args.skill, usage_contract=usage_contract)
    elif args.command == "kickoff":
        answers = context._json_input(args.answers_json) if args.answers_json else None
        result = context.kickoff_project(args.project, answers)
    elif args.command == "usage-check":
        usage = context._check_run_budget(args)
        if usage is None:
            raise context.StageContextError("usage-check 必须提供本轮日志边界和预算")
        result = {"ok": True, "run_usage": usage}
    elif args.command == "recreation-prompt-input":
        try:
            result = context.recreation_prompt_context(args.project.resolve(strict=True), args.segment_id)
        except (KeyError, TypeError) as exc:
            raise context.StageContextError(f"局部写词输入结构不完整：{exc}") from exc
    else:
        result = context.validate_text(args.kind, Path(args.path).expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get("ok") is False else 0


if __name__ == "__main__":
    # The shell may select GBK while the host decodes UTF-8. Configure both
    # channels before producing any non-ASCII JSON or diagnostic output.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    try:
        raise SystemExit(main())
    except (OSError, context.StageContextError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
