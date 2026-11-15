# JSON → 既有审核网页：隔离合同修正

> 状态：历史记录（2026-10-22）。隔离试验阶段的合同记录，现行网页接线以 [web-content-adapter.md](web-content-adapter.md) 为准。

实现基线：`1a57efeb729a95a6df2aac69a04fe5f723d1bb6d`，仅隔离实现，未合并 main，未切换生产入口。保留 PR #1。未搬入旧 v2 补丁或创作候选历史。

正常顺序为候选 JSON → 自查校验 → 现有审核网页审查 → 用户确认准确 JSON 版本及范围。Markdown 检查稿只有用户明确要求时才从同一版本导出，不是前置、业务权威或反向写入来源。历史 human_readable_path 的只读兼容、确认范围与正式交接授权保持。

网页依据是既有 `docs/development/web-content-adapter.md`、`scripts/core/web_content.py` 和 `references/web-contract-v78/`。v78 是固定验证快照，不能证明当前线上部署；本次未接入或发布网页，也未虚构地址。实际部署来源、当前入口、网页版本及发布回读流程待核实。

有限只读溯源：仓库 README 指向 `/workspace/msb-recovery/repo`；ACTIVE_VERSION.md 指向该云工作区的 `active-storyboarding/current`、`run-current.sh` 和 `active-storyboarding/ACTIVE.json`；Mac 备份索引仍列 `C:/Users/LIUYANG/Documents/Master-Storyboarding/`。这些位置未在本 Mac 核实为正式运行源码。Obsidian 备份不是本仓库工作树，未触碰其用户改动。

本次仅维护合同文字，不改变业务 schema、校验器、发布接口或确认代码。定向检查及其实际环境限制见同目录的本轮验证报告。旧全回归在收集时缺 `20260518-100605.png` 的阻塞保留，未补造 fixture，未恢复创作实验。

历史压力场景 `skills/storyboard-planner/tests/pressure-tests.md` 的 11/12 等仍保留原字节，不改写历史测试材料；其旧 Markdown 先行顺序按根规则仅供历史双文件路径参考，不作为新生产交付顺序。未修改既有测试记录。
