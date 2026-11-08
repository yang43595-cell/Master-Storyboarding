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
python -m pip install --index-url https://pypi.org/simple --require-hashes --only-binary=:all: -r config/requirements-dev.txt
python -m pip check
# Node.js 24，用于已有网页契约的离线校验：
npm ci --ignore-scripts --no-audit --no-fund
python -m scripts.core.knowledge_runtime
python scripts/stage_context_cli.py --help
```

仅运行核心 CLI 时可以把安装命令的文件换成 `config/requirements.txt`；包含图片资产功能及测试时使用上述 dev 清单。三份 `config/requirements*.txt` 均固定直接和间接依赖版本及发行包 SHA256；对应 `.in` 文件只表达维护时的兼容范围，不作为日常安装入口。依赖只从既定官方包源安装，不把 `.venv/` 或缓存提交到 Git。JavaScript 网页适配/测试使用 Node.js 24 与锁定的 Zod 3.25.76（`package-lock.json`）；Blender、ASR 和浏览器生成工具是按需能力，依各自 Skill 安装或连接，不要求普通文字阶段预装所有工具。

锁文件以 CPython 3.12 为验证基线，保留 Windows 等平台条件依赖，不能把一次 Linux 通过当成其他系统已实测。`--only-binary=:all:` 在缺少适用 wheel 时明确失败，不临时编译未知构建依赖；不要为安装成功而去掉哈希检查。Python、Node、FFmpeg 和系统库本身不包含在 Python 锁文件中。更新依赖与自动检查说明见 [依赖与自动检查](dependency-checks.md)。

完整音视频技术回归还需要测试进程能找到真实 `ffmpeg` 与 `ffprobe`。先核 `ffmpeg -version`、`ffprobe -version` 及实际合成媒体解码；仅当前 PATH 找不到不能断言本机未安装，已有工具可通过本轮进程 PATH 使用，不把某台机器的路径写成仓库默认。可选 ASR 兼容测试在具备原测试依赖及 FFmpeg 的隔离环境中按 `config/requirements-asr.txt` 补齐，不需要下载模型。缺工具、可选依赖、Windows 平台或指定私人来源时，按真实失败/跳过范围报告，不把未运行算通过。

新业务项目放在源码仓库之外，不把素材、原片、生成结果和确认记录提交到源码仓库：

```sh
python scripts/init_desktop_project.py --path ../my-film
```

该命令只创建空项目，拒绝覆盖已有目录。之后读取 `PROJECT_RULES.md` 和对应 `skills/<skill>/SKILL.md`，提供真实需求与输入，按本阶段入口继续。正常阶段的 `prepare` 直接选取仓库内置知识。视频提示词仍走本 Skill 的直接读取入口。

## 隔离测试目录

规则/方法变更测试通过 `scripts/tests/source_fixture.py` 复制所需源码根目录及当前实际使用的源码文件类型，不复制整个工作树。它使用当前工作文件，保留未提交修改、新增源码、原始字节和缺失引用，不用 `git archive HEAD` 掩盖本地变化；缺失必需根目录、读取失败、源码符号链接和未知文件类型会直接报错。新测试需要白名单之外的源码或 fixture 数据时，应明确扩展输入范围，不能静默漏掉。副本可写，但不改变原文件权限。

`.venv/`、`node_modules/` 等根目录本地状态不在白名单内，所选源码目录内的同名依赖/缓存也不复制。嵌套目录内同样排除 `.gitignore` 所列凭据命名、`.aws/`、`.ssh/`、原始/生成媒体及项目运行状态；仅保留明确的 `.env.example` 样例。此筛选按用途、路径和类型工作，不是对任意源码正文的秘密扫描。子进程使用当前 Python 解释器，依赖仍在原安装位置，不从测试副本重新安装。只复制指定知识目录、网页契约或合成项目的现有测试不改成源码快照，以免漏掉它们实际要检查的数据。

普通 pytest 的 `tmp_path` 已按运行和测试隔离。需要显式指定位置时，每次新建独占目录，不复用共享固定 `--basetemp`（pytest 会清空该路径）。macOS/Linux 示例，在已激活上述 `.venv` 的终端运行：

```sh
test_tmp=$(mktemp -d "${TMPDIR:-/tmp}/msb-tests.XXXXXX")
(
  trap 'rm -rf -- "$test_tmp"' EXIT
  export TMPDIR="$test_tmp" PYTHONDONTWRITEBYTECODE=1
  python -m pytest -q -ra -c config/pytest.ini scripts/tests tests --basetemp="$test_tmp/pytest" -o cache_dir="$test_tmp/cache"
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

## 制作链的知识来源

| 入口 | 实际读取 |
| --- | --- |
| 编导、导演、普通资产、分镜及已选用的声音或白模 | 从本 Skill 入口和 profile 选仓库本地合同、模板和 `knowledge/runtime/` 中的适用方法与参考。 |
| 资产 Web | 从同一内置包组装方法和模板，按本次项目或素材登记读取实际媒体。 |
| 视频提示词 | 直接读取本 Skill 指定的仓库方法、模板及业务输入。 |
| 审核与画布交付 | 读取当轮已经冻结的要求、准确稿件、素材和实际平台回读。 |

生产的知识根固定在当前仓库。原有环境变量 `MSB_VAULT_ROOT` 不能切换知识来源，普通生产命令传入外部 `--vault` 会被拒绝。历史准备包若绑定了外部方法，重新按本仓库生成本阶段准备包。跨 agent 交接给准确仓库 commit 和业务项目路径。外部知识审计、来源维护与隔离试验由独立入口执行。

## 同步与回退

1. 日常制作从仓库读当前版本。专门授权维护来源时，在新分支中对照当前 profiles、实际本地引用和 manifest，只读检查本次指定的准确来源。
2. 核实来源、状态、适用范围、差异与必要依赖。新增方法、改变动态规则准入或方法状态仍需原项目治理要求；仅更新快照不能伪装成晋升。
3. 对经授权的准确文件保留原字节，更新对应 SHA256 和来源修订。删除或改名必须先检查全部消费者；缺失来源不能用空文件或近似模板代替。
4. 运行内置包校验、相关测试和干净目录验收，再以 PR 交审。不要在运行时联网自动更新，不自动合并，不把本项目副本覆盖回 Mac。
5. 回退使用此前已审 commit，并重新准备受影响任务；历史阅读或批准记录不自动继承给不同字节。

## 不随仓库提供的条件能力

`knowledge/manifest.json` 的 `conditional_gaps` 列出准确缺口，例如未入包的真实动作参考和产品 brief。温泉酸上脸模板及条件尺度案例均随仓库提供，真实图片和原片按项目输入登记。缺少业务素材时须提供真实文件或停止受影响操作。若缺必需的知识方法或模板，先由维护流程把准确文件纳入仓库，再运行该分支。可选案例缺失时略过该案例。

浏览器登录、平台账户、付费生成、Blender/ASR 软件、真实媒体和用户批准都不是源码可打包的权限。方法加载、测试或离线网页编译通过，不表示已生成图片/视频、已验证效果、已发布审核站点或已得到用户确认。
