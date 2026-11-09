# 逐 Skill 执行 SOP 与产物审查核对（归档摘要）

本文件是归档摘要版；原文恢复方法见上级目录 README.md。原文与 project-chain-audit 互链，该文件同在本目录。

## 这是什么

2026-11-04 的历史复盘：审查 9 个 Skill 的入口、执行方法、产物审查清单与程序接线并落地 SOP；其后按用户要求完成四项程序修复，并做了一轮本机技术补验。未运行真实生产或媒体生成。

## 当时改了什么

- 定下共用完成判定：先定入口/交付/范围/停止点；实际读取要有原文返回，程序检查要有本轮对象与结果；有效旧证据可复用但不能说成本轮重新执行；必需项缺证或失败不得报全部通过。
- 逐 Skill 补齐缺失检查、收窄多余要求，固定各自执行顺序与交付停止点。
- 四项程序修复：资产导出不再按类别自动写 `product_fact_check: passed`；声音候选/current 必须有授权引用与非空 permitted_uses 并核对本次用途；对白证据缺失/错字节在正式采用、返修与交接入口一律拒绝；换声按现代视频稿的登记来源核对依赖并使相关产物失效，不再依赖旧 production_plan。

## 结论现在活在哪

完成判定落在 skills/_shared/skill-layers.md「执行与完成核对」，逐 Skill 口径在各 Skill 本体，四项修复已落代码。已晋升至 docs/development/dependency-checks.md「本机验收环境」（本机验收环境口径，与 project-chain-audit 共同提炼）。

## 当时验证数字

首轮相关测试 401 passed、16 subtests；修复阶段全量曾为 2373 passed、3 failed（均为测试进程 PATH 找不到 ffmpeg）；补验确认工具实际存在后最终全量 2393 passed、0 failed、3 skipped、177 subtests；Node 77 passed。
