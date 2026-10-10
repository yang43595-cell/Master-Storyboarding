# 第三批：资产方法分流与 Web 读取边界

状态：独立分支实施并验证完成，尚未合并或切换正在使用的网页。执行仓库基线 `8126ac3`，Vault 基线 `58ee145`；两边分支均为 `codex/msb-third-batch-assets`。

后续验证与续补：资产网页真实看图写词记录见 `.third-batch-real-test-20260912/真实测试报告.md`；分镜图其余方法分流见 [third-batch-storyboard.md](third-batch-storyboard.md)。下文保留首轮实施当时的范围和测试结果。

## 问题与处理

原 Web 流程在模型读取前依次调用 prepare、ensure-prepared；资产没有专业方法绑定时能够通过。加入必读方法后，ensure-prepared 要求真实执行者的阅读声明，Web 当时尚未调用模型，因而被阻断。同时，Web 资料组装器只读取本地 Markdown 和启动页，没有把独立方法全文送入模型。

这两处已经在隔离输入中复现。溯源分别是 `fd4b6d0` 引入的方法阅读机制和 `497af2b` 中保留的 Web 调用顺序；问题来自两种执行流程未衔接。没有通过跳过检查、伪造阅读声明或保留第二套旧方法解决。

现在 Web 两种模式共用 `skills/asset-image-assistant/library_runtime.py` 的受限读取入口：按当前制作项解析方法、动态知识、本地合同、模板与对应检查项，完整组装后再调用模型。模型仍在一次调用中写词、自查与修正。输入完整性只记录为 `input_assembled_not_agent_reading`；用户确认、工具/模型/额度授权和图片终审继续使用原流程。

普通对话资产生产与 storyboard-director 仍走 prepare、实际阅读登记、ensure-prepared，没有修改该全局门槛。Web 输入不再写真实项目的固定执行上下文，只在现有请求目录保存 task-package 与 skill-evidence。项目事实与确认依旧由原项目接收代码核验。

Web 起始模板和正式模型输入现在都通过资产 runtime 解析同一来源；明确指定的模板缺失时不会静默换成默认模板。分类按实际制作项与 AST 计算，不接受请求中伪造的分类开关。

## 三层分流

| 对象 | 第一层保留 | 第二层方法 |
|---|---|---|
| 人物 | 身份卡字段、生成/确认顺序、1×4 格式与模板 | 人物分级、身份不变项、活人感、拟人结构与专业自查 |
| 场景 | 方向/资产/状态表、生成顺序、画幅、提示词模板和登记 | 母图选择、固定结构、机位职责、文字表达与专业自查 |
| 道具/产品 | 真实参考门槛、身份/状态表、1×3 格式、工具与产品业务依据 | 类型判断、必要表现集合、结构与文字表现、专业自查 |
| 风格/色卡 | 输出字段、色卡格式、模板、交付与确认 | 风格锁、候选差异、验证表现、色卡使用与专业自查 |
| 图片连续性 | 触发条件、引用与输出规格、确认状态 | 两个图片 Skill 共用的背景固定层与单变量派生判断 |

5 份方法的唯一正文位于 Vault `projects/Master-Storyboarding/应用知识/图片资产/`。4 类方法分别由 `request.asset_method_scopes.style/character/scene/object=true` 选择，共用 `IMAGE-CONTINUITY-METHOD` 按 Skill 绑定。Web 从实际类别计算条件；普通对话执行者据本次 AST 填写条件，并核对所需方法确已加载。

共迁移 46 个段落/片段；四类指南的 12 个模板代码块全部保留在本地。逐行对照原版与迁移后的本地+方法正文，没有遗失原有非标题内容（仅规范化色卡步骤编号）。没有新增经验层材料，没有改白模、声音、网页界面或选图确认逻辑。

## 验证及限制

- 相关 Python 检查：134 项测试、27 个子测试通过。覆盖原资产库/项目/人物/来源流程、实际方法加载、8 种输出×2 种 Web 模式、混合 AST、无效方法、缺绑定/模板/本地合同/动态规则、confirmed 冲突、分类覆盖、请求目录隔离与普通对话阅读门槛。
- 模型传输检查：真实资料组装结果完整进入原 Codex 调用参数；用模拟 CLI 接收，确认仍只有一次调用。没有实际调用模型写词，也不把模拟返回当作品质量验证。
- 网页交互逻辑：6 项 Node 测试通过。HTML/JS/CSS、项目选图与确认代码没有改动；没有启动或切换现有 Web 服务。
- 2 个 Skill 通过 quick_validate；Windows 下使用 `py -3.13 -X utf8`，未修改系统编码或官方校验脚本。
- 知识入口审计：从 `8126ac3` 导出原资料核验，与当前实现均为 13 项错误，新增错误为 0。原有 5 项旧消费者名、5 项页面正文哈希待复核、3 项视频 Skill 直接引用问题保留，未借本次迁移处理。
- 未进行付费生图、真实提示词质量试验、真实项目生产或线上网页视觉验收。宿主对话 token 未另行计量；没有新建计量系统。

## 复核与使用这份分支

验证时仅给测试进程设置 `MSB_VAULT_ROOT=C:/Users/LIUYANG/Documents/Obsidian-Vault-worktrees/msb-third-batch-assets`。没有持久修改环境变量。

```powershell
$env:MSB_VAULT_ROOT = 'C:/Users/LIUYANG/Documents/Obsidian-Vault-worktrees/msb-third-batch-assets'
py -3.13 -m pytest -c config/pytest.ini scripts/tests/test_visual_asset_library.py scripts/tests/test_visual_asset_projects.py scripts/tests/test_visual_asset_characters.py scripts/tests/test_visual_asset_source.py scripts/tests/test_visual_asset_reading.py scripts/tests/test_method_documents.py scripts/tests/test_stage_context_cli.py scripts/tests/test_knowledge_selector.py -q
node --test scripts/tests/test_visual_asset_ui.cjs scripts/tests/test_visual_asset_scenes.cjs
```

正式集成须先让配套 Vault 方法可用，再更新执行仓库；不能只发布半边。当前运行网页仍使用原主仓库，本次未合并、未重启。主仓库既有 Seedance 清理工作没有处理。

本次完整运行输出、前置审查、隔离复现和迁移映射位于本 worktree 的 `.third-batch-validation/`，属于独立测试产物，不是生产项目或第二份方法真源。主要证据：`final-python-tests.txt`、`final-web-tests.txt`、`audit-before-verified.json`、`audit-after-verified.json`、`content-conservation.json`、`migration-map.json`。
