# 历史源码备份：2026-10-08 恢复候选

此分支于 2026-10-10 补齐并保全，**不是当前正式生产版本，也不是完整项目或原始 Git 历史的恢复**。

- 实际来源：Library `libfile_9a507399ab9481918eb5bc1366f0c000`，版本 1，公主项目全链溯源_最小修复候选与证据_20261008.zip（2026-10-08 18:46 UTC）
- 来源 ZIP SHA-256：`5eb6e2801e54a0c7c02da9500c3a46ed52237403558beb1359bf942448d21b10`
- 原候选共 325 文件；本分支保存其中 266 份选定源码及 2 份备份说明/校验文件，共 268 文件。README 和 .gitignore 为备份范围增加了说明与排除项
- 2026-10-10 补齐原缺失的 Three.js `three.core.js` 与 `three.module.js`，使用来源包原字节
- 源码清单 `SOURCE_SHA256.json` 记录 266 份来源原字节；其中 README 和 .gitignore 的 hash 指修改前版本，不能直接用于验证这两个备份改写文件
- 不包含：制作媒体、产品参考资产、完整知识库、最新制作状态、正式提交 `6120d39965f530da489e1756c2e6dc9cd6392c57` 及其未提交改动、原始 Git 提交链
- 这是可检出的历史候选备份；来源及排除范围详见 `RECOVERY_PROVENANCE.md`。它未获生产采用，不应直接覆盖 main 或被当作最新全量版本
- 恢复校验针对文件字节与 Git 可检出性，不等同依赖安装、端到端运行或生产效果验收

---

# Master-Storyboarding

一个**个人用的轻量短视频工具**：用 AI 把一个故事做成 分镜 / 资产 / 视频脚本 / 成片，共 7 步。你在当前任务中与主 agent 协作，每步由主 agent 执行并自检，你审核确认后再按指令接下一步；独立子任务的委派边界见项目规则。

## 怎么用它做一部短剧

见 **[docs/development/README.md](docs/development/README.md)** —— 建项目、7 步流程、每步怎么跑、审核什么、红线速览。这是你的操作手册。

## 环境

```powershell
python -m pip install -r config/requirements-dev.txt
python -m pytest -c config/pytest.ini
```

（测试须用 Python313 解释器；项目运行只需要 `config/requirements.txt`。）

迁移旧 Windows 项目时，可仅给当前进程设置 `MSB_PROJECT_PATH_MAP`：内容是“原项目、仓库或 Vault 绝对根目录 → 当前本地对应绝对根目录”的 JSON 对象。只登记已核对的来源根及其旧别名；公共产物、素材图、产品参考和确认记录读取会据此解析原路径，不改确认正文、校验码或素材字节。不按项目同名猜测映射，未登记或缺失的文件仍阻塞。知识正文、模板与资产服务的图库/备份统一用 `MSB_VAULT_ROOT` 指定实际 Vault。未设置时保留原默认位置；隔离测试可给 `Library(..., vault=...)` 指定自己的可写副本。


## 规则与蓝图

- **[docs/PROJECT_BLUEPRINT.md](docs/PROJECT_BLUEPRINT.md)** —— 北星文档：项目「是什么 / 往哪走 / 什么不碰」，改动前先读。
- **[docs/decisions.md](docs/decisions.md)** —— 技术决策记录：为什么这么定 + 否掉了什么，改动前和蓝图一起读，避免走回头路。
- **[PROJECT_RULES.md](PROJECT_RULES.md)** —— 唯一规则正文（5 条红线 + 工作边界）；详细红线在每个 Skill 的 `skills/_shared/`。

## 目录

- `skills/`：7 个生产 Skill + `stage-reviewer`（阶段审核）+ `knowledge-maintainer`（知识维护）。
- `scripts/`：确定性打包 / 校验 / 交接工具（入口 `stage_context_cli.py`、`init_desktop_project.py`）。
- `schemas/`：Skill 之间传数据用的格式合同。
- `references/`：知识接线与 `workflow/` 知识策略。
- `assets/`：真实产品参考与生产资产。
- `config/`：环境配置（requirements、pytest.ini）。
- `docs/`：使用说明。
