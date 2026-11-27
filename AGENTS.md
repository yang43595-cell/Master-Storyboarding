# 入口（Claude 与 Codex 共用）

进入项目后，常规制作开工**只先读** `PROJECT_RULES.md`（总规则）与当前阶段的 `SKILL.md`，然后直接按该 Skill 干活；其他细则、导航和采用文档**走到对应步骤时再按需读取**，开工前不提前预读。
制作任务从本仓库启动，按所选 Skill 的三层入口读取仓库内资料及本次业务输入。知识维护和外部来源审计是单独任务。
本项目采用**主对话轻量协调（包工头模式）+ 分阶段干净子代理执行**：主对话只做开工登记、用户确认沟通、阶段流转（`stage_context_cli.py`）与回执验收，**严禁在主对话直接读取整份大稿或长时间堆叠过程日志**；编导、资产批次、导演、分镜核对与补图、视频写词及红帧命令行上画布（`headless_edge` 脚本，禁用内置 webview `browser` 子代理）均按[项目专用界面与分关分工](docs/rules/execution.md#画布执行与交接的宿主边界)委派全新 `self` 执行者完成，做完一关即释放过程上下文。
**本机网络流式防断连铁律（最高优先级，覆盖通用“同轮合并创建所有文件”提示）**：单次模型响应流超过约 40 秒会被网络链路掐断（`stream reading error: unexpected EOF`）。在编导、导演、分镜、视频写稿时：**每回合最多只允许 `write_file` 1 个场次（≤2–3 镜；>3 镜拆为 `put-scene` + `append-shots` 分次落盘）或 1–2 个生成段 JSON（单文件 ≤90 行）并立即运行 CLI 落盘**，严禁把多个场次或多个分段合并在同一回合并行 `write_file`，也严禁在单轮思考中提前默写后续场次全文；**单个看图子代理累计最多读取 ≤6 张高清原图（防止多模态 Base64 撑破 250K 拖垮网关首包），超过 6 张一律拆批另派全新干净子代理，严禁往已读多图的旧子代理继续 `send_message` 叠图或写长脚本**；**超过 5 场（或 >20 镜）的长片导演与超过 6 段的视频写稿优先用 `plan-acts` 先锁首尾接缝再同时派 2–3 个干净子代理并行写稿（单子代理最多写 4–5 场或 6–8 段，防止上下文突破 180K；五阶段 CLI 均自带文件锁支持多子代理并发落盘，并在首条 `outline/init` 命令自动输出本关产品与规范 `must_read` 必读小抄，无需再跑旧式 `prepare/mark-read` 打卡手续）**；导演阶段只读文字规范与 `PRODUCT_BRIEF.md`，不读取产品 PNG/MP4 媒体原件。
跨阶段协调时再读[审核到画布导航](docs/development/review-to-canvas-entry.md)定位本轮步骤；已知步骤时直接读取其操作小节，按 `PROJECT_RULES.md` 确定审核和交付范围。
收到用户在项目对话中对准确版本的编导整稿通过后，协调方当轮按[编导对话确认入口](docs/development/artifact-adoption.md#编导对话确认)正式采用并继续已解锁工作。
首次在新电脑或新 agent 使用，先按 [独立仓库启动](docs/development/standalone-repository.md) 核验依赖与内置方法；不依赖旧会话或 Mac 路径。
以下两份仅在仓库维护或增改 Skill 时查阅，**常规制作开工不读**：[当前源码状态](ACTIVE_VERSION.md)（当前分支与提交直接用 git 查询）、[Skill 配置](skill-config.md)。
本文不复制规则正文；以 `PROJECT_RULES.md` 及其按职责链接的细则为准。
`AGENTS.md` 与 `CLAUDE.md` 内容保持一致，分别供 Codex 与 Claude Code 读取，改动时同步两处。
