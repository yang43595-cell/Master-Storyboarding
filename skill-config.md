# Skill 配置

生产 Skill 位于 `skills/{skill-name}/`。`SKILL.md`、`knowledge-profile.json` 与 `modules/` 为必需项；`references/`、`templates/`、`tests/` 仅在存在真实执行合同、模板或测试材料时创建，不保留空目录。

`SKILL.md` 只保存触发、职责、输入输出、执行顺序和硬禁区；`modules/` 只保存稳定执行方法，不保存专业理论正文。专业理论与经验以 Obsidian Vault 为唯一正文，通过 `knowledge-profile.json` 选择。`skills/_shared/` 只保存用户确认、知识读取、产品参考与使用、路径交付、未确认稿生命周期和提示词语言等现有共享红线；具体边界以各文件适用范围为准，不以固定文件数判断过期。

当前生产 Skill：`creative-director`、`storyboard-planner`、`blender-previs`、`asset-image-assistant`、`voice-reference-manager`、`storyboard-director`（仅按需视觉补充）、`voiceover-to-video-script`。`stage-reviewer` 只做阶段末只读审核。

`knowledge-maintainer` 是非生产维护 Skill，只用于知识规则、topics、Skill profile 和本地执行合同的审计与更新；不得用于阶段生产。

任何 Skill 都不得自行调用下一 Skill 或决定阶段流转；执行者分工、子任务委派和独立审核统一按 PROJECT_RULES.md 的“Skill 执行分工”，不在此重复定义。不得另建 Agent 运行时或状态管理体系。
