---
title: AI导演Agent专业影视分镜调研包
aliases: [AI_DIRECTOR_AGENT, 专业影视分镜调研包]
type: source
status: verified
authority: source-summary
created: 2026-08-21
updated: 2026-08-21
tags: [来源, AI调研, 影视分镜, Agent]
source_type: AI-researched package
original_location: "C:/Users/LIUYANG/Downloads/AI_DIRECTOR_AGENT/"
upstream: "用户提供的 ChatGPT 调研整理包"
sunk_to:
  - "Master-Storyboarding/skills/storyboard-planner/SKILL.md"
  - "Master-Storyboarding/references/storyboard-planner/专业影视分镜执行规范.md"
  - "Master-Storyboarding/references/storyboard-planner/专业影视分镜输出模板.md"
  - "Master-Storyboarding/references/storyboard-planner/专业影视分镜审查门槛.md"
  - "Master-Storyboarding/references/storyboard-planner/storyboard-schema.json"
---

# AI导演Agent专业影视分镜调研包

## 来源说明

本资料由用户交付，原始目录名为 `AI_DIRECTOR_AGENT`，内容由 ChatGPT 调研整理，目标是为 Agent 提供专业影视分镜的系统提示、导演知识、决策规则、JSON Schema、质量检查和示例。

它不是出版社、协会或单一官方机构发布的规范。摄入时按“可用方法来源”处理，不把其中的 AI 内部引用标记或未经独立核验的书目信息当作权威证据。

## 原始文件清单

| 原文件 | 主要内容 | 摄入结论 |
|---|---|---|
| `README.md` | 文件加载策略和工作模式 | 采用分层加载思想 |
| `AGENT_STARTER_INSTRUCTIONS.md` | Agent 启动说明 | 转译进 Skill 入口，不保留原文 |
| `00_SYSTEM_PROMPT.md` | 导演身份、固定决策顺序、职责边界 | 采用并重写为项目契约 |
| `01_DIRECTING_KNOWLEDGE.md` | 景别、机位、调度、运动、声音与剪辑知识 | 方法进入概念页，执行规则下沉仓库 |
| `02_DECISION_RULES.md` | 镜头选择与例外判断 | 采用导演层规则；剔除下游 AI 生成越界内容 |
| `03_STORYBOARD_SCHEMA.json` | Draft 2020-12 JSON Schema | 原结构有效；重构为“专业分镜＋资产需求表＋联合闸门” |
| `04_QC_RUBRIC.md` | 阻断项、百分制和最终检查 | 保留阻断与定性检查；废弃百分制裁决 |
| `05_EXAMPLES.md` | 对话、动作、情绪和产品示例 | 仅提炼为压力测试，不作为默认生成正文 |
| `99_RESEARCH_SOURCE.md` | 长篇调研、书目和官方链接 | 提炼跨项目方法；清除不可追溯内部引用 |

## 采用的核心结论

- 分镜是“叙事意图到可拍、可剪视听决策”的转译；
- Scene、Beat、Shot、Panel 必须分层；
- 决策顺序应从场景变化和观众信息开始，而非随机选择景别和焦距；
- Blocking、轴线、视线、屏幕方向和关键物体状态是连续性核心；
- 声音、切点和下一镜变化属于分镜正文；
- 设备未知时不应制造精确参数；
- 每镜需要通过目的测试和删除测试。

## 未直接采用或已修正

- **百分制 QC**：与 Master-Storyboarding 已确认的“用户审查为最终闸门”冲突，改为阻断项和定性检查。
- **AI 生成附加规则**：不属于专业文字分镜阶段，交由资产图、分镜图和视频执行阶段各自负责。
- **原始 JSON 字段要求**：对空镜强制人物 blocking、对所有镜头强制重参数会造成伪精确，已调整。
- **ChatGPT 内部引用标记**：形如 `turn…search…` 的引用无法在知识库追溯，全部移除。
- **示例正文**：不作为正式规范，避免 Agent 套写案例；只保留测试场景和判断目标。

## 可追溯在线来源

原调研包列出的以下官方入口予以保留，具体页面内容仍应在需要精确引用时重新核验：

- [Toon Boom Storyboard Pro Documentation](https://docs.toonboom.com/)
- [Adobe Camera Shots & Angles](https://www.adobe.com/creativecloud/video/production/cinematography/camera-shots-and-angles.html)
- [ARRI ALEXA 35](https://www.arri.com/en/cine-systems/cine-cameras/legacy-cine-cameras/alexa-35)
- [RED Shutter Angle Tutorial](https://www.red.com/red-101/shutter-angle-tutorial)
- [Academy Digital Collection](https://digitalcollections.oscars.org/)
- [Academy — A Saul Bass Celebration](https://www.oscars.org/videos-photos/saul-bass-celebration)
- [Lucasfilm — Temple of Doom Storyboards](https://www.lucasfilm.com/news/history-in-objects-doom-display/)

## 执行层转译记录

2026-08-21 将可逐字执行的规则一次性转译到 Master-Storyboarding 的 `storyboard-planner` Skill 与 `references/storyboard-planner/`。执行层物理权威在仓库；本页不复制完整执行模板。

知识方法主页面：[[../concepts/02-镜头与剪辑/专业影视分镜设计与执行方法|专业影视分镜设计与执行方法]]。

## 原始资料处置

原始下载目录只在转译和验证期间保留。确认 Skill、知识页、Schema、路由与测试均通过后，按用户授权移入 Windows 回收站，不另存 `raw/`。

