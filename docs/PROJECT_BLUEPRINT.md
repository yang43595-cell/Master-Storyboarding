# 项目蓝图（北星文档）

> 这个项目的「为什么 + 往哪走」。**做任何改动、加任何东西之前，先读这一份**；它和 `PROJECT_RULES.md` 的「方向」段一致，是更高一层的展开。

## 一、这是什么

- 它是**你个人做 AI 短剧的工具**：把一个故事 → 分镜 → 资产 → 视频脚本 → 成片。
- 它**不是一个框架**：不追求功能全、不做通用平台、**不为「维护工具本身」而存在**。
- 一句话：**顺手、轻、能出片。** 只有你一个用户，你的手感就是标准。

## 二、核心（拍片之路，永远优先保留）

每一步由当前主 agent 直接执行一个 Skill 并自检；按需独立审核，执行分工以 `PROJECT_RULES.md` 为准。

| 步 | Skill | 产出 |
|---|---|---|
| 0 | `init_desktop_project` | 建 9 目录 + project_state.json |
| 1 | `creative-director` 编导 | 叙事 JSON（Markdown 按需导出） |
| 2 | `storyboard-planner` 分镜 | 导演 JSON（Markdown 按需导出） |
| 3 | `blender-previs` 预演（可选·已有 runner，按当前配方验收） | Blender 预演 |
| 4 | `asset-image-assistant` 资产 | 人物/场景/道具/产品母版图 |
| 5 | `voice-reference-manager` 声音（可选·已有导入/绑定 CLI，按当前输入验收） | 声音参考库 |
| 6 | `storyboard-director` 画面（按需） | 正式分镜图 |
| 7 | `voiceover-to-video-script` 成片 | 已确认导演包、原文对白与真实素材 → 完整分段 Markdown 提示词 → Seedance |

- **每步之间**：除 `voiceover-to-video-script` 外使用 `stage_context_cli` 的 `prepare` / `review-pack` / `handoff` / `validate`；视频提示词按其 Skill 入口直接读取和自查。
- **生产与审核**：主 agent 直接产出并自检；只有用户明确要求时才启动空白独立审核，返修回当前执行者，复审回原审核者。你确认且明确继续后进入下一阶段。

### 运行流程（一图看懂）

```mermaid
flowchart TD
    YOU[你 · 终审] --> GO[主 agent]
    GO --> WORK[直接做这一步并自检]
    WORK --> DRAFT[出一份草稿]
    DRAFT --> DECIDE{用户明确要求独立审核？}
    DECIDE -->|需要| CHECK[空白只读审查员核对]
    CHECK -->|发现问题| WORK
    CHECK -->|完成| YOU
    DECIDE -->|不需要| YOU
    YOU -->|确认 → 进入下一步| NEXT[下一步]
    YOU -->|要改 → 退回重做| WORK
```

> 一大步怎么切成上面这几步、每一部的产出是什么，见上方**步骤表**；每个 Skill 的具体职责与产出见 `skills/<skill>/SKILL.md`。

## 三、边界（什么不碰 / 不主动加）

「**只加帮出片的东西**」。以下属工程性 / 治理性重量，**不主动加，除非直接挡着你出片**：

- 大型测试套件（按改动风险保留最小充分的核心测试，不固定数量）。
- 冗余格式合同 / 多版本 schema。
- 治理 / 审核规范**制度**（安全红线保留，制度性流程不加）。
- 多 agent 运行时 / 黑箱调度引擎 / 架构重构层。
- 为「别的项目 / 通用场景」准备的东西（一次性、写死某个项目的除外）。

## 四、决策标准（拿不定时按顺序问自己）

1. 它**让你更快把这部片子做出来**吗？ 是 → 考虑加；否 → 默认不加。
2. 它是在**维护工具本身**（防回归 / 可维护性 / 通用性）吗？ 是 → 这是工程性重量，警惕。
3. 有**更轻、更少**的做法吗？ 有 → 选轻的。宁缺一个功能，不让项目回重。
4. 这会让项目**更难被你一个人用**吗？ 会 → 不做。
5. 与「方向段 / 安全红线」冲突吗？ 冲突 → 不做。

## 五、明确删掉的（别加回来）

- 冗余 schema：`production-plan-v3.1`、`artifact-manifest`、`project-state`、`knowledge-rule`。当前 `review-profile` 是按需审核的最小清单，不在删除范围。
- `production_plan` 整条路径已退役（现行视频阶段不生成）：`core/production_plan.py`、`scripts/validate-production-plan.py` 与两个 schema 仍在，**只用于读旧项目**，别再往新项目接线。
- 死脚本 + `scene_topology` 引擎 + `core/archive`、2 个一次性脚本。
- 历史上删除了 29 个冗余测试、保留 8 个核心；该数量是历史记录，不是后续上限。
- `sync-log`、死模板、`visual-benchmarks`、13 个空 `references` 脚手架。
- 治理规范细节 + 生产/空白对话分割的繁文缛节（安全红线和原生空白子 agent 隔离保留，见 `PROJECT_RULES.md` 的执行分工）。

## 六、安全红线（保护你的东西）

五条红线见 **[PROJECT_RULES.md](../PROJECT_RULES.md)**，此处不重复，避免两处同步漂移。

## 七、怎么用这份蓝图

- 想改 / 加东西：第一件事读它，按「三、边界」「四、决策标准」判断。
- **技术决策记录见 [docs/decisions.md](decisions.md)**：记的是"为什么这么定 + 否掉了什么"，改动前和蓝图一起读，避免走回头路 / 重新发明。
- 它和 PROJECT_RULES「方向」段一致；本蓝图是更详细的一张图。
- **你（唯一用户）有最终决定权。** 蓝图帮你和 AI 保持同一条线，不替你拍板。
