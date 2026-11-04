# Skill 配置

生产 Skill 位于 `skills/{skill-name}/`。`SKILL.md`、`knowledge-profile.json` 与 `modules/` 为必需项；`references/`、`templates/`、`tests/` 仅在存在真实执行合同、模板或测试材料时创建，不保留空目录。

`SKILL.md` 只保存触发、职责、输入输出、执行顺序和硬禁区；`modules/` 只保存稳定执行方法，不保存专业理论正文。专业方法通过 `knowledge-profile.json` 选择；默认从 `knowledge/runtime/` 读取本仓库 commit 固定的已选运行副本，它们按职责属于 Skill 第二层。来源参考保留第三层身份。Obsidian Vault 是可选上游，不是新电脑的启动前提；同步与版本边界见 [独立仓库说明](docs/development/standalone-repository.md)。`skills/_shared/` 只保存用户确认、知识读取、产品参考与使用、路径交付、未确认稿生命周期和提示词语言等现有共享红线；具体边界以各文件适用范围为准，不以固定文件数判断过期。

当前生产 Skill：`creative-director`、`storyboard-planner`、`blender-previs`、`asset-image-assistant`、`voice-reference-manager`、`storyboard-director`（仅按需视觉补充）、`voiceover-to-video-script`。`stage-reviewer` 只做阶段末只读审核。

`knowledge-maintainer`（展示名“项目知识治理”）是非生产 Skill，按需执行四项治理任务：接收整理、分层检查、晋升发布、失效回退；负责项目知识入口、候选来源与治理记录、topics、Skill profile 和仓库执行合同的审计与授权更新。维护者不得自评自批。它不搬运或同步 Mac Vault，不负责整片归档或清理源目录，也不得用于阶段生产。历史云端快照只是固定来源；仓库内置运行副本按 Git 审查更新，不自动写回上游。没有真实 Vault 时不能声称完成全 Vault 审计或写回。晋升的代码结构检查不证明用户意图、实验真实性或效果质量。

任何 Skill 都不得自行调用下一 Skill 或决定阶段流转；执行者分工、子任务委派和独立审核统一按 PROJECT_RULES.md 的“Skill 执行分工”，不在此重复定义。不得另建 Agent 运行时或状态管理体系。
