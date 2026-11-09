# 云端核对与独立仓库验收（归档摘要）

本文件是归档摘要版；原文恢复方法见上级目录 README.md。

## 这是什么

2026-11-04 的历史验收记录：把云电脑当前项目文件与 GitHub 核对上传，并以"独立、可直接使用的仓库"为目标做验收。基线为 main `6fdad894`（tree `229af692`）。列车真实感经验的草稿 PR #24 不在本次范围，未重复收录。

## 当时改了什么

- 远端 399 份源码按 Git blob 核验恢复；云端两套规则摘录 53 个文件逐一比较，除换行与文件末尾空白外与基线一致。
- 补齐独立仓库所需内容：12 份 profile 绑定的专业方法、36 份来源参考与 1 份采用索引、两份资产 recipe 模板按上游原字节补齐、动态规则 `MSB-KB-PROMPT-002` 单独保留，合计 52 份选定内容及独立 manifest；默认从当前仓库读取，不再猜 Mac 的 Obsidian 目录。
- 补 Node.js 24 与 Zod 3.25.76 依赖清单和锁文件；Codex/Claude 共用入口一致。

## 结论现在活在哪

独立仓库的现行启动与核验入口是 docs/development/standalone-repository.md；本篇只是当时的验收证据，不是现行说明。

## 当时验证数字

52 份选定内容哈希通过；Python 2184 passed、168 subtests passed、4 skipped；Node 77 passed、0 failed。
