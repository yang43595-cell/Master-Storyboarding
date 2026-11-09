# 结构化依赖检查的端到端验证与路径修正（归档摘要）

本文件是归档摘要版；原文恢复方法见上级目录 README.md。

## 这是什么

2026-10-18 的历史复盘：对提交 `19598d9` 的 `scripts.core.product_refs --discover-dependencies` 做端到端独立验证。只保存去敏技术事实，不涉及生产采用、发布或媒体生成。

## 当时改了什么

初始证据只跑了既有 prepare / ensure-prepared 的方法依赖检查，不能证明新依赖发现已实际运行。独立检查实际运行新入口后，发现一个参考路径使用 `../` 越出项目边界，被正确拒绝。修正办法：把参考资料按原始字节复制到隔离项目内部、改用项目相对路径，保留原失败证据并重新冻结修订产物；未改对白、故事或导演设计。

## 结论现在活在哪

依赖发现行为已落 scripts.core.product_refs 与 canvas_readback，现行口径见 docs/development/delivery-snapshot-checks.md。已晋升至 docs/development/delivery-snapshot-checks.md（交付包移动后绝对链接失效、运行身份不随迁一条，与 recovery-delivery-audit 共同提炼）。

## 当时验证数字

正常项目退出码 0、检查 40 个可达文件且 errors 与 uncheckable 为空；负例（移除一张被引用参考图）退出码 1、检查 39 个文件并准确报告缺失；两份离线审核包 18,859,139 与 2,987,069 字节，均通过同一提交的 `--package` 检查；异路径执行因候选运行身份绑定被正确拒绝。
