# Recovery and delivery chain: bounded synthetic audit（归档摘要）

本文件是归档摘要版；原文恢复方法见上级目录 README.md。

## 这是什么

2026-10-18 的历史复盘（英文原文）。基线 bae5d5b（audit/20261017），在隔离 checkout 中用合成输入对恢复与交付链做有界审计；不涉及生产变更、生成、发布或 main 合并。

## 当时改了什么

- 视频直读工作流原先对人工读取的方法/产品/动作文件不做集体前置检查：给既有 scripts.core.product_refs 增加可重复的 `--require-file`（含 `--input-root`），检查文件可读、非空、 regular file 与解析后包含关系，目录/空/缺失/越界/符号链接环均报错且不写任何文件；视频 Skill 改为输出前调用。属显式输入检查点，不是自动发现或内容真实性证明。
- 确认交付行为边界：合成 ZIP 与移动后的项目能保住相对链接与哈希，但候选执行仍拒绝被复制的绝对运行身份，包括在新位置重新配置——这是有意的授权保护，不静默修复；交付文档的绝对链接不因包移动而可移植。
- 更新交付说明：传输前按接收渠道预算核对所选载荷字节与成品包大小，解压后核相对路径与哈希。

## 结论现在活在哪

`--require-file` 与 fail-closed 消费已落 scripts.core.product_refs 及 docs/development/delivery-snapshot-checks.md。已晋升至 docs/development/delivery-snapshot-checks.md（交付包移动后绝对链接失效、运行身份不随迁一条，与 structured-dependency-end-to-end 共同提炼）。

## 当时验证数字

最终代码回归 1110 passed、2 skipped、97 subtests passed；聚焦检查 32 passed、1 skipped。
