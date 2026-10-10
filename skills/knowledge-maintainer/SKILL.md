---
name: knowledge-maintainer
description: 用于 Master-Storyboarding 知识与规则的只读审计、授权维护、收尾提炼或定稿归档；不生成生产产物。
---

# Knowledge Maintainer

由当前执行者按 [PROJECT_RULES.md 的 Skill 执行分工](../../PROJECT_RULES.md#skill-执行分工) 执行本阶段；默认由主 agent 直接执行和自检，已明确委派时由受派执行者完成。维护仅按已授权范围和下列维护输入执行，不套用生产阶段启动包或自动进入生产链。

## 职责

维护知识系统的单一真相和确定性审计边界。专业方法正文只保存在 Obsidian Vault；Skill 本地只保留稳定执行合同、工具合同、技术清单和可直接产出的模板。

开始前完整读取 [references/audit-contract.md](references/audit-contract.md) 与 [modules/maintenance-method.md](modules/maintenance-method.md)。

- **只读审计**：按本次问题检查规则与入口，不要求生产结束、人工复盘或新对话，不修改文件。
- **授权维护**：按用户批准范围修改本地合同或知识规则；先完成授权内准备，不重复审批。涉及 Vault 写入时仍遵守对应门禁。
- **任务收尾提炼**：仅在用户要求长期沉淀时执行，基于已完成修正的经验提炼规则；上下文过重时用干净对话和最小材料续接。此条件不约束当前产物返工或独立审计。专业知识写入既有主题页，复盘记录留在 `wiki/projects/<项目名>/`。
- **项目定稿归档（生产完成后触发）**：用户已确认成片并明确授权整体归档后，把桌面项目**整体移动**到知识库 `archive/定稿项目/<项目名>/`（脚本、分镜、执行文档、正式视频片段、参考素材、生成记录、历史文件全部，图片/视频一并）。在 `00_项目管理/` 写 `定稿归档说明.md`（定稿信息、状态说明、后续修改规则）与 `定稿归档文件清单.csv`（每个文件的相对路径、大小、SHA256）。归档完成后清理桌面源目录；在 `wiki/log.md` 记一条、`projects/index.md` 登记为定稿项目。归档**只读保留**，后续要改须从归档拷回桌面建新工作版本，并在其 `00_项目管理/project_state.md` 记录来源归档路径。

## 输入

- 待维护的知识规则、topic、Skill profile、本地合同或执行入口。
- 项目根目录与 Obsidian Vault 路径。
- 如涉及 Vault 写入，必须具备干净工作区和通过的写入门禁。

## 输出

- 去重后的唯一正文或本地合同。
- 更新后的 `knowledge-profile.json` 或 `knowledge-entrypoints.json`。
- `scripts/audit-knowledge-entrypoints.py` 的确定性审计结果与错误解释。

## 执行顺序

0. 先区分只读审计、授权维护、收尾提炼和定稿归档。只读审计只输出证据与建议；授权维护不等待生产结束；提炼需用户明确要求保存经验；归档需用户明确授权归档范围，不把确认成片自动解释为移动目录。归档继续遵守上文完整性检查和记录要求。

1. 判断内容属于 Vault 专业知识还是仓库稳定合同。
2. 按维护方法执行来源检查、规则去重与 topic 定位。
3. 只更新唯一真相，不复制知识正文。
4. 运行：`python scripts/audit-knowledge-entrypoints.py --vault <Vault路径>`。
5. 逐项解释非零退出结果；修复后重新审计。

## 硬禁区

- 不进入生产阶段链，不生成 `stage_startup.md`、审核包或交接卡。审计和维护不要求生产结束；收尾提炼与定稿归档按各自条件执行。
- 不决定当前阶段、下一阶段或用户批准事实。
- 不在 Skill 中保存 Vault 专业知识快照。
- 不绕过 Vault 脏状态、Git 门禁或 confirmed 冲突。
- 不复制或改写审计 CLI 的确定性逻辑。
