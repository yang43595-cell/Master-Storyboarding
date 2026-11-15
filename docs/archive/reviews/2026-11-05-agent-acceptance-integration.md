# 本地自主验收与消费接线（归档摘要）

本文件是归档摘要版；原文恢复方法见上级目录 README.md。

## 这是什么

2026-11-05 的历史复盘，分支 `codex/agent-stage-acceptance`：把本地流程授权下的整稿自查、自主采用与下游消费接线落地。业务规则以 [PROJECT_RULES.md](../../../PROJECT_RULES.md) 为准，操作见 [审核到画布入口](../../development/review-to-canvas-entry.md)。

## 当时改了什么

- 新增 scripts/core/agent_acceptance.py：登记已有流程授权，自查绑定本项目、获准阶段、准确产物/修订/字节、直接上游与检查 profile，图片另绑定实际观察与产品参考 hash；报告须覆盖全部检查 ID，未决或阻塞项不能自报通过。
- 自主采用、授权返修与提交继续保留 `user_approved:false`，不新增逐稿人工确认；`artifact_is_confirmed` 只表示人工确认，`artifact_is_adopted` 读两种合法依据。
- 阶段交接、补图请求、图片引用与画布快照消费读取同一有效依据；三个生产 Skill 已指向真实的 authorize-workflow / accept-artifact 操作说明。

## 结论现在活在哪

现行口径在 PROJECT_RULES.md「网站审核到画布的统一入口」一节与 docs/development/review-to-canvas-entry.md 的接线现状；实现为 scripts/core/agent_acceptance.py。本篇只是当时的接线记录；当时审核站只验了认证与空参数处理，未做真实发布。

## 当时验证数字

完整本地 Python 回归 2527 passed、177 subtests passed、3 skipped；test_agent_acceptance.py 56 项全部通过；知识包验证 56 个文件。
