# 独立仓库启动与知识版本

本仓库包含项目规则、Skills、代码、schema、测试和经过选择的专业方法运行副本。新的 agent 拉取同一 commit 后，不需要旧对话、Mac 路径或安装 Obsidian，仍按 `AGENTS.md` 和本职 Skill 进入。

## 首次启动

建议使用 Python 3.12。克隆私有仓库所需的 GitHub 登录由使用者完成，不在仓库保存凭据。

```sh
git clone https://github.com/yang43595-cell/Master-Storyboarding.git
cd Master-Storyboarding
python -m venv .venv
# macOS/Linux:
. .venv/bin/activate
# Windows PowerShell 改用: .venv\Scripts\Activate.ps1
python -m pip install -r config/requirements-dev.txt
# Node.js 24，用于已有网页契约的离线校验：
npm ci --ignore-scripts --no-audit --no-fund
python -m scripts.core.knowledge_runtime
python scripts/stage_context_cli.py --help
```

仅运行核心 CLI 时可以只安装 `config/requirements.txt`；包含图片资产功能及测试时使用上述 dev 清单。依赖只从既定官方包源安装，不把 `.venv/` 或缓存提交到 Git。JavaScript 网页适配/测试使用 Node.js 24 与锁定的 Zod 3.25.76（`package-lock.json`）；Blender、ASR 和浏览器生成工具是按需能力，依各自 Skill 安装或连接，不要求普通文字阶段预装所有工具。

完整音视频技术回归还需要测试进程能找到真实 `ffmpeg` 与 `ffprobe`。先核 `ffmpeg -version`、`ffprobe -version` 及实际合成媒体解码；仅当前 PATH 找不到不能断言本机未安装，已有工具可通过本轮进程 PATH 使用，不把某台机器的路径写成仓库默认。可选 ASR 兼容测试在具备原测试依赖及 FFmpeg 的隔离环境中按 `config/requirements-asr.txt` 补齐，不需要下载模型。缺工具、可选依赖、Windows 平台或指定私人来源时，按真实失败/跳过范围报告，不把未运行算通过。

新业务项目放在源码仓库之外，不把素材、原片、生成结果和确认记录提交到源码仓库：

```sh
python scripts/init_desktop_project.py --path ../my-film
```

该命令只创建空项目，拒绝覆盖已有目录。之后读取 `PROJECT_RULES.md` 和对应 `skills/<skill>/SKILL.md`，提供真实需求与输入，按本阶段入口继续。正常阶段的 `prepare` 可以省略 `--vault`；视频提示词仍走现行直接读取入口，不恢复旧 prepare 流程。

## 隔离测试目录

规则/方法变更测试通过 `scripts/tests/source_fixture.py` 复制所需源码根目录及当前实际使用的源码文件类型，不复制整个工作树。它使用当前工作文件，保留未提交修改、新增源码、原始字节和缺失引用，不用 `git archive HEAD` 掩盖本地变化；缺失必需根目录、读取失败、源码符号链接和未知文件类型会直接报错。新测试需要白名单之外的源码或 fixture 数据时，应明确扩展输入范围，不能静默漏掉。副本可写，但不改变原文件权限。

`.venv/`、`node_modules/` 等根目录本地状态不在白名单内，所选源码目录内的同名依赖/缓存也不复制。嵌套目录内同样排除 `.gitignore` 所列凭据命名、`.aws/`、`.ssh/`、原始/生成媒体及项目运行状态；仅保留明确的 `.env.example` 样例。此筛选按用途、路径和类型工作，不是对任意源码正文的秘密扫描。子进程使用当前 Python 解释器，依赖仍在原安装位置，不从测试副本重新安装。只复制指定知识目录、网页契约或合成项目的现有测试不改成源码快照，以免漏掉它们实际要检查的数据。

普通 pytest 的 `tmp_path` 已按运行和测试隔离。需要显式指定位置时，每次新建独占目录，不复用共享固定 `--basetemp`（pytest 会清空该路径）。macOS/Linux 示例，在已激活上述 `.venv` 的终端运行：

```sh
test_tmp=$(mktemp -d "${TMPDIR:-/tmp}/msb-tests.XXXXXX")
(
  trap 'rm -rf -- "$test_tmp"' EXIT
  export TMPDIR="$test_tmp" PYTHONDONTWRITEBYTECODE=1
  python -m pytest -q -c config/pytest.ini --basetemp="$test_tmp/pytest" -o cache_dir="$test_tmp/cache"
)
node --test scripts/tests/*.cjs
python -m scripts.core.knowledge_runtime
```

只清理本次创建的临时目录，不清空共享 `/tmp`，不碰真实项目、素材或其他任务目录。某项失败仍须按日志处理；不得把磁盘耗尽的连锁失败当成独立产品缺陷，或跳过失败测试来换取通过。

## 第二层方法与第三层来源

- `knowledge/runtime/` 保留上游相对路径，profile 不需要换成机器绝对路径。已绑定专业方法按职责属于 Skill 第二层；目录名不决定层次。
- `knowledge/manifest.json` 固定所选文件的 SHA256、来源版本和角色。运行版本以当前 Git commit 为准，不声称自动跟随上游最新版。
- 来源页仍是第三层参考，保留 `needs_review` 等原状态；打包不等于认可全部内容。资产 Web 必需的提示词规则单独保存为当前原页第84–106行的精确摘录，manifest 同时记录完整原页与摘录 SHA，不能把它声称为整页原文。历史项目原片、完整 Vault、私人业务状态和登录资料不在包内。两份资产模板保留原字节用于已有 recipe；其中旧 Markdown 登记方式、旧顺序或画质词不覆盖当前 JSON 合同、视图顺序与提示词语言规则。
- 动态知识只从 manifest 的 `dynamic_rule_ids` 精确集合读取。目前保留资产 Web 已明确要求的 `MSB-KB-PROMPT-002`；其余旧动态块即使存在于来源页也不启用，不绕过原有候选/晋升门禁。
- `.gitattributes` 保留运行副本原字节，避免 Windows 换行转换导致 hash 漂移。校验失败应恢复对应版本或审查变更，不直接重写 hash 来掩盖差异。

## 外部知识根是显式选项

选择顺序是本次 `--vault` 参数、环境变量 `MSB_VAULT_ROOT`、仓库内置 `knowledge/runtime/`。错误的显式路径立即报错，不悄悄使用内置副本或猜测用户 Documents 目录。资产 Web 通过同一默认解析入口读取；视频直接读取也遵守同一顺序并先核内置包。

内置包与显式外部 Vault 的范围不同。外部 Vault 仍使用它原有的动态选择与墓碑机制，不会写回本仓库；跨 agent 交接应同时给准确仓库 commit、业务项目路径及本轮明确外部根（如有），不能只说“用最新”。

## 同步与回退

1. 在新分支中对照当前 profiles、实际本地引用和 manifest，只读检查上游对应精确路径。不要扫描或复制整个 Vault。
2. 核实来源、状态、适用范围、差异与必要依赖。新增方法、改变动态规则准入或方法状态仍需原项目治理要求；仅更新快照不能伪装成晋升。
3. 对经授权的准确文件保留原字节，更新对应 SHA256 和来源修订。删除或改名必须先检查全部消费者；缺失来源不能用空文件或近似模板代替。
4. 运行内置包校验、相关测试和干净目录验收，再以 PR 交审。不要在运行时联网自动更新，不自动合并，不把本项目副本覆盖回 Mac。
5. 回退使用此前已审 commit，并重新准备受影响任务；历史阅读或批准记录不自动继承给不同字节。

## 不随仓库提供的条件能力

`knowledge/manifest.json` 的 `conditional_gaps` 列出准确缺口，包括产品真人上脸专用模板、真实动作参考、产品 brief、可选尺度经验等。需要这些内容的分支必须提供真实文件或停止受影响操作；普通编导、方法读取和其他独立步骤可以继续。

浏览器登录、平台账户、付费生成、Blender/ASR 软件、真实媒体和用户批准都不是源码可打包的权限。方法加载、测试或离线网页编译通过，不表示已生成图片/视频、已验证效果、已发布审核站点或已得到用户确认。
