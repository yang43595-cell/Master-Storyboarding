"""Freeze the current review package and its exact local evidence."""
from __future__ import annotations

import argparse
import json
import hashlib
from pathlib import Path
from typing import Any

from . import stage_context as context


@context._context_scoped
def review_pack(args: argparse.Namespace) -> dict[str, Any]:
    context._require_preparation_skill(args.skill)
    project = Path(args.project).expanduser().resolve()
    context.require_production(project)
    review_kind = getattr(args, "review_kind", "production")
    if review_kind not in {"production", "skill_contract"}:
        raise context.StageContextError(f"未知审核类型：{review_kind}")
    if args.skill == "asset-image-assistant" and review_kind == "production":
        raise context.StageContextError("请将资产候选交给用户验收")
    artifacts = [context._review_target(project, Path(item), review_kind) for item in args.artifact]
    external_evidence = [Path(item).expanduser().resolve() for item in (getattr(args, "evidence", None) or [])]
    external_evidence = list(dict.fromkeys(external_evidence))
    manifest_path = project / "00_项目管理" / "artifact_manifest.json"
    state_path = project / "00_项目管理" / "project_state.json"
    missing = [str(item) for item in artifacts if not item.is_file()]
    if missing:
        raise context.StageContextError(f"被审产物不存在：{missing}")
    missing_evidence = [str(item) for item in external_evidence if not item.is_file()]
    if missing_evidence:
        raise context.StageContextError(f"外部审核证据不存在或不是文件：{missing_evidence}")
    basis_path = context._task_basis_path(project)
    basis_bytes = context._review_basis_bytes(basis_path)
    _, task_basis = context._load_task_basis(project, args.skill, raw=basis_bytes)
    manifest = task_basis.get("reading_manifest") or {}
    if manifest.get("test_only_proposed_method_ids"):
        raise context.StageContextError("隔离测试方法不能用于正式审核；请重新 prepare。")
    # prepare can write a packet before anyone records a reading receipt.
    packet_optional = not (manifest.get("methods") or manifest.get("candidate_catalog"))
    evidence_path = context._context_dir(project, "method_reading_evidence.json")
    packet_path = context._context_dir(project, "method_reading_packet.md")
    evidence_bytes = context._review_basis_bytes(evidence_path, optional=True)
    packet_bytes = context._review_basis_bytes(packet_path, optional=packet_optional)
    try:
        evidence = json.loads(evidence_bytes.decode("utf-8")) if evidence_bytes is not None else None
    except ValueError as exc:
        raise context.StageContextError(f"阅读证据无法解析：{evidence_path}；请重新准备并打包。") from exc
    reading = context._evidence_current(project, args.skill, basis=task_basis, reading_files=(evidence, packet_bytes))
    adopted = reading[1].get("adopted_candidates", []) if reading else []
    input_validation = context._validate_current_inputs(project, task_basis, adopted_candidates=adopted)
    delivery_contract = task_basis.get("delivery_contract")
    if not isinstance(delivery_contract, dict):
        raise context.StageContextError(f"任务依据缺少交付意图：{basis_path}；先重新 prepare。")
    usage = context._check_run_budget(args)
    profile_path, profile = context._load_review_profile(args.skill)
    profile_standard = task_basis["standards"].get("review_profile", {})
    if profile_standard.get("sha256") != hashlib.sha256(profile_path.read_bytes()).hexdigest():
        raise context.StageContextError("审核 profile 已在 prepare 后变化；先显式更新任务依据，不能用 live profile 重选标准。")
    acceptance = task_basis.get("review_acceptance", {}).get(review_kind)
    if not isinstance(acceptance, dict) or not isinstance(acceptance.get("required_checks"), list) or not isinstance(acceptance.get("manual_checks"), list):
        raise context.StageContextError("任务依据缺少冻结审核清单；请重新 prepare。")
    required_checks = acceptance["required_checks"]
    manual_texts = [item.strip() for item in (getattr(args, "check", None) or []) if item.strip()]
    manual_checks = acceptance["manual_checks"]
    if manual_texts and manual_texts != [item["text"] for item in manual_checks]:
        raise context.StageContextError("--check 不能在审核时另选标准；请在 prepare 的 request.review_checks 中冻结后重跑。")
    checks = [*required_checks, *manual_checks]
    revision = context._revision_summary(
        project,
        getattr(args, "change_id", None),
        getattr(args, "target_logical_id", None),
    )
    review_mode = getattr(args, "review_mode", "initial")
    if review_mode not in {"initial", "revision"}:
        raise context.StageContextError(f"未知审核模式：{review_mode}")
    previous = getattr(args, "previous_review", None)
    response = getattr(args, "producer_response", None)
    baselines = getattr(args, "baseline", None) or []
    # Supplying the complete historical archival inputs remains a supported
    # invocation. Partial archival arguments must not silently downgrade.
    formal = bool(getattr(args, "formal_archive", False) or response or baselines)
    delta = None
    if review_mode == "revision":
        if not previous:
            raise context.StageContextError("revision 必须提供 --previous-review；保留上轮未决发现，不能把遗漏当已解决。")
        try:
            if formal:
                if not response or not baselines:
                    raise ValueError("正式留档复审必须提供 --previous-review、--producer-response 和 --baseline。")
                delta, manual_checks = context.revision_delta(
                    Path(previous).expanduser().resolve(), [Path(p).expanduser().resolve() for p in baselines],
                    artifacts, context._producer_response_value(response), args.skill, required_checks, manual_checks, external_evidence,
                )
            else:
                delta = context.lightweight_revision(
                    Path(previous).expanduser().resolve(),
                    context._read_json(context._context_dir(project, "review_scope.json"), default={}), artifacts,
                    args.skill, review_kind, required_checks, manual_checks,
                    getattr(args, "change_summary", None) or [], external_evidence,
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise context.StageContextError(f"复审输入无效：{exc}") from exc
        checks = [*required_checks, *manual_checks]
    adopted_knowledge = [{**item, **({"adoption_scope": reading[1]["candidate_scopes"][item["id"]]}
                          if item["id"] in reading[1].get("candidate_scopes", {}) else {})}
                         for item in adopted]
    scope = {"skill": args.skill, "review_kind": review_kind, "task_basis_path": str(basis_path),
             "task_basis_sha256": hashlib.sha256(basis_bytes).hexdigest(),
             "task_basis_identity": context._review_identity(task_basis, adopted_knowledge),
             "artifacts": [context.fingerprint(p) for p in artifacts],
             "direct_evidence": [context.fingerprint(p) for p in external_evidence]}
    if formal:
        scope.update(input_issue_ids=[i["id"] for i in delta["issue_updates"]] if delta else [],
                     issue_id_high_watermark=delta["issue_id_high_watermark"] if delta else 0)
    elif delta:
        scope["input_findings"] = delta["unresolved_findings"]
    if delta is not None:
        identity_key = "task_basis_identity" if "task_basis_identity" in delta else "task_basis_sha256"
        if delta["review_kind"] != review_kind or delta[identity_key] != scope[identity_key]:
            raise context.StageContextError("任务依据或审核类型已改变；这不是普通复审，先显式变更任务依据并重新首审。")
    if delta is not None and formal and any(change['after_sha256'] != item['sha256']
                                           for change, item in zip(delta['actual_diff'], scope['artifacts'])):
        raise context.StageContextError('候选在差异计算与审核范围登记之间发生变化；重新生成审核包，不混用版本。')
    if delta is not None and not formal:
        if delta["current_artifacts"] != scope["artifacts"]:
            raise context.StageContextError("候选在轻复审打包中变化；重新生成，不混用版本。")
    if delta is not None and delta["current_direct_evidence"] != scope["direct_evidence"]:
        raise context.StageContextError("直接证据在增量计算与审核范围登记之间变化；重新生成，不混用版本。")
    round_dir = context._context_dir(project)
    if formal:
        round_key = hashlib.sha256(json.dumps([scope, delta], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        round_dir = context._context_dir(project, "review_rounds", round_key)
    scope_path, delta_path = round_dir / "review_scope.json", round_dir / "review_delta.json"
    review_boundary = (
        "本包仅用于用户明确要求的独立审核；生成文件本身不构成派发授权。默认自查在当前对话完成，不新建审核agent。已获独立审核授权时只读检查，不继承生产对话，不修改产物。"
        if review_mode == "initial"
        else "修订复审：必须回原审核子 agent，用 `followup_task` 交回最新审核包；下游打回上游也回该上游原审核者，换修订号不换审核者；原 agent 无法恢复时报告限制并暂停，不自动另建空白 agent；只读；仅复核修订项、原阻塞项及其直接影响；不修改产物。"
    )
    lines = [
        "# 阶段审核包", "",
        f"- Skill：`{args.skill}`",
        f"- 审核模式：`{review_mode}`",
        f"- 审核边界：{review_boundary}", "",
        "## 审核配置", "",
        f"- Profile：`{profile_path}`",
        f"- Profile 版本：`{profile['schema_version']}`",
        f"- 审核层：`{review_kind}`",
        "- 自动必检项不可由命令行删除；`--check` 只能复述 prepare 已冻结的手动项。", "",
        "## 本轮任务依据", "",
        f"- 唯一依据：`{basis_path}`（实质任务身份：`{scope['task_basis_identity']}`；文件 SHA-256 仅供追溯）",
        f"- 交付对象：{json.dumps(task_basis['deliverables'], ensure_ascii=False)}",
        f"- 非目标：{json.dumps(task_basis['non_goals'], ensure_ascii=False)}",
        f"- 输入版本：{json.dumps(task_basis['input_versions'], ensure_ascii=False)}",
        "- 适用标准、方法全文和版本见该依据及下列明确证据；不得在审核中另加标准。", "",
        "## 当前候选产物", "",
    ]
    lines.extend(f"- `{item}`" for item in artifacts)
    lines.extend(["", "## 本轮版本与冻结标准", "", f"- `{scope_path}`（当前候选、来源与验收身份；{'原样作为 review.json 的 scope' if formal else '仅保留当前范围，不要求复制进发现记录'}）"])
    if delta is not None and formal:
        lines.extend(["", "## 本轮增量", "", f"- `{delta_path}`（仅上轮未决项、逐 ID 修改说明、实算 diff）",
                      f"- 原审核 agent：`{delta['reviewer_id']}`",
                      "- 不读全部上轮 review、历史候选或对话；baseline 仅用于核对删除内容及旧行号，不是全文读取清单。",
                      "- 先读实际 diff、changed_sections、deleted_sections、file_level_changes；旧行号对应 baseline，新行号对应 current。空 changed_sections 不代表没有影响。",
                      "- diff_format=unicode-edits-v1时读取text_edits：removed/added是完整改动，context仅为附近定位，不是完整审查范围。位置按统一换行后的Unicode字符计数，不手工拼稿；程序已验证可还原。沿用同版已有上下文，必要时按before_line/after_line补读受影响段及直接关系；不得因切片短就缩小内容审查。",
                      "- continuity_neighbors 是同文件连续性定位；file_level_impact_review_required 要求判断文件级决定影响了哪些段。cross_file_context_review_required 提醒核对边界的直接关联候选或源证据，不按 artifact 列表顺序猜时间顺序，不全量重读。标记为 false 也不证明没有语义关联。",
                      "- 上轮仍有效的检查覆盖继续有效；下面冻结清单不是要求全量重审。",
                      "- `--evidence` 只能列本轮直接受影响的材料；登记、确认、知识依据有变化时仅显式提供有关片段及源位置。"])
    elif delta is not None:
        lines.extend(["", "## 本轮轻复审", "", f"- `{delta_path}`（上轮未决发现、声明改动与直接影响；不是程序实算 diff）",
                      "- 必须回原审核者，逐条回应输入未决发现及修改位置；解决项保留 check_id、target 并标记 status=resolved，遗漏不能当作已解决。",
                      "- 不要求 baseline 全文、生产者逐 ID 回复、编号水位或审核谱系；必要时只补读准确源位置。"])
    elif review_kind == "production":
        lines.extend(["", "## 产物登记证据", ""])
        lines.append(f"- {'缺少：' if not manifest_path.is_file() else ''}`{manifest_path}`")
        lines.append("- 只核对 task_basis 已列当前输入与相关确认；不读取全部候选或历史登记。")
        lines.extend(["", "## 项目状态与用户确认事实", ""])
        lines.append(f"- {'缺少：' if not state_path.is_file() else ''}`{state_path}`")
        lines.extend(["", "## 本轮按需知识依据", ""])
        if reading:
            lines.extend([f"- 完整方法/已采用知识快照：`{reading[2]}`", f"- 读取声明与版本证据：`{context._context_dir(project, 'method_reading_evidence.json')}`"])
        else:
            if isinstance(evidence, dict) and evidence.get("skill") == args.skill and evidence.get("adopted_candidates"):
                lines.append("- 已登记候选证据无效：`method_reading_evidence.json`（不得作为本轮依据）")
            else:
                lines.append("- 无有效读取快照；若任务依据列有必读方法，先完成读取登记再打包审核。")
    else:
        lines.extend(["", "## 合同审核边界", "", "- 仅审核当前 Skill 合同候选的触发、职责、输入输出、引用和硬禁区；不把真实项目产物结论混入本轮。"])
    if delta is not None:
        lines.extend(["", "## 直接证据身份变化", "",
                      "- 增量中的 direct_evidence_changes 明确新增、撤回、同路径换版及前后 SHA-256；它只证明字节身份，不裁定语义影响。",
                      "- 原审核者对相关未决及既有通过项判断保留、重开或无法判断，列准确依据与受影响对象；不得用旧证据关闭问题，也不因证据换版自动全量首审。"])
    lines.extend(["", "## 外部只读审核证据", ""])
    if external_evidence:
        lines.extend(f"- `{item}`" for item in external_evidence)
    else:
        lines.append("- 无（未声明外部审核证据）")
    if revision and review_mode == "initial":
        lines.extend(["", "## 前序修订约束", "", "```json", json.dumps(revision, ensure_ascii=False, indent=2), "```"])
    lines.extend(["", "## 检查清单", ""])
    lines.extend(f"- [ ] `{item['id']}`：{item['text']}" for item in checks)
    lines.extend(["", "## 输出要求", ""])
    if formal:
        lines.extend([f"- 本轮为正式留档：review.json / review.md 写入 `{round_dir}`；结构见审核方法的正式留档节。",
                      "- 问题使用稳定 B-012 等 ID，逐项输出 resolved / unresolved / new-blocker；建议为 suggestion。"])
    else:
        lines.extend(["- 默认轻审核：只回结论和每条 check_id、target（对象）、evidence（证据）、impact（影响）。",
                      "- 若需 CLI 复审，将这些发现记在同一 issues 数组；不要求 review.json / review.md 双文件、基线快照或问题编号。"])
    lines.extend(["- 只传本轮结论、未决发现和必要证据，不传完整聊天。",
                  "- 结论只允许“通过”“有条件通过”“不通过”。没有新证据不新增审美标准；图片视觉质量由用户终审。", ""])
    content = "\n".join(lines)
    output = context._context_dir(project, "review_packet.md")
    report = context.validate_text("review", output, content)
    for path, initial in ((basis_path, basis_bytes), (evidence_path, evidence_bytes), (packet_path, packet_bytes)):
        if context._review_basis_bytes(path, optional=initial is None) != initial:
            raise context.StageContextError(f"审核依据在打包期间变化：{path}；请重新准备或重新打包。")
    context.write_text(scope_path, json.dumps(scope, ensure_ascii=False, indent=2) + "\n")
    if delta is not None:
        context.write_text(delta_path, json.dumps(delta, ensure_ascii=False, indent=2) + "\n")
    if formal:
        context.write_text(round_dir / "review_packet.md", content)
    context.write_text(output, content)
    report["profile"] = str(profile_path)
    report["required_check_count"] = len(required_checks)
    report["manual_check_count"] = len(manual_checks)
    report["manifest_evidence_path"] = str(manifest_path)
    report["manifest_evidence_exists"] = manifest_path.is_file()
    report["project_state_evidence_path"] = str(state_path)
    report["project_state_evidence_exists"] = state_path.is_file()
    report["review_mode"] = review_mode
    report["review_kind"] = review_kind
    report["formal_archive"] = formal
    report.update(input_validation)
    report["task_basis_path"] = str(basis_path)
    report["review_scope_path"] = str(scope_path)
    report["review_delta_path"] = str(delta_path) if delta is not None else None
    report["external_evidence_paths"] = [str(item) for item in external_evidence]
    if usage is not None:
        report["review_usage"] = {"phase": getattr(args, "usage_phase", None) or ("initial_review" if review_mode == "initial" else "revision_review"), **usage}
    return report
