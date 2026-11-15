# 入口（Claude 与 Codex 共用）

进入项目后先读 `PROJECT_RULES.md`（总规则及按需细则入口）与所选 Skill 的 `SKILL.md`，然后按 Skill 做这一阶段的活。
制作任务从本仓库启动，按所选 Skill 的三层入口读取仓库内资料及本次业务输入。知识维护和外部来源审计是单独任务。
编导稿与资产图的网站审核、其余阶段自主检查并交付画布草稿，统一从 [审核到画布入口](docs/development/review-to-canvas-entry.md) 定位操作与接线现状；规则仍以 `PROJECT_RULES.md` 为准。
首次在新电脑或新 agent 使用，先按 [独立仓库启动](docs/development/standalone-repository.md) 核验依赖与内置方法；不依赖旧会话或 Mac 路径。
仓库源码状态见 [当前源码状态](ACTIVE_VERSION.md)；它不记录提交号，当前分支与提交以仓库根目录的 git 查询为准。
Skill 的必需文件、目录结构与生产名单见 [Skill 配置](skill-config.md)。
本文不复制规则正文；以 `PROJECT_RULES.md` 及其按职责链接的细则为准。
`AGENTS.md` 与 `CLAUDE.md` 内容保持一致，分别供 Codex 与 Claude Code 读取，改动时同步两处。
