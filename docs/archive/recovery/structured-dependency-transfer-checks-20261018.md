# Structured dependency and transfer checks（归档摘要）

本文件是归档摘要版；原文恢复方法见上级目录 README.md。

## 这是什么

2026-10-18 的历史复盘（英文原文）。基线 f6a48c1（audit/20261017）：给直读交付链实现结构化依赖发现与成品包预算检查。改动只有源码、合成测试与文档，无业务资产、凭据、生成、发布或 main 合并。

## 当时改了什么

- product_refs CLI 新增 `--discover-dependencies`：沿所选 JSON 的路径/媒体字段、准确产物 ID 与 manifest 登记依赖遍历，不再要求逐个手工重复列出；同字节别名保留，路径-only 选择有歧义时失败而不猜修订。
- canvas_readback.validate_submission 自动调用同一发现（含既有 preflight、刷新回读与修订控制调用方），报告实际检查的文件与有界覆盖范围。
- 两条 CLI 新增 `--package`：按成品文件字节（含归档开销）度量并输出 SHA-256，默认每包严格小于 19,000,000 字节（工作流传输预算，非平台规格），可用已核实的渠道预算覆盖；空/缺失/超限包拒绝并给重建建议，不删、不拆、不改链接。
- 缺失、空、非文件、越界或不可读依赖，畸形 JSON，未解析 ID 与本地不可核媒体 URL 一律 fail-closed。

## 结论现在活在哪

实现已落 scripts.core.product_refs 与 canvas_readback；现行口径在 docs/development/delivery-snapshot-checks.md「依赖发现与文件包预算」。本篇只是当时的实现记录。

## 当时验证数字

最终全量 Python 回归 1144 passed、2 skipped、97 subtests passed；独立聚焦复核 128 passed；后续聚焦补查 108 passed。
