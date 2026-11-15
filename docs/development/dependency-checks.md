# Python 依赖与最小自动检查

本轮只准备源码维护检查，不操作真实项目、素材、网站或生成任务。发布 workflow、运行 Actions、设为合并必需检查和合并分支是不同操作；不能把本地通过说成线上检查已启用。

## 安装与锁文件

- `config/requirements.in`：核心 CLI 的兼容范围。
- `config/requirements-dev.in`：核心依赖、pytest 和 Pillow 的兼容范围。
- `config/requirements-asr.in`：独立可选 ASR 环境的兼容范围。
- 同名 `.txt`：从官方 PyPI 解析的完整版本及 SHA256 锁，正常安装只读 `.txt`。dev 锁约束到核心锁，避免同一依赖出现两套版本。

使用 Python 3.12 的全新虚拟环境，按[首次启动](standalone-repository.md#首次启动)安装。哈希检查校验安装包字节，不证明软件没有漏洞或所有功能兼容。安装后运行 `python -m pip check`。已有隔离安装器 `scripts/install_runtime_deps.py` 仍读取 `requirements.txt`，原有 requirements 指纹会覆盖完整核心锁；旧 stamp 因锁变化失效时按既有检查流程处理，不自动重建或改绑其他解释器。宿主已有 site-packages 的历史回退策略不变，不宣称锁文件会强制改写宿主环境。

锁解析保留不同平台的条件依赖（例如 Windows 的 colorama），验证基线是 CPython 3.12。解析成功不等于该平台有 wheel，更不等于已在该平台执行测试。ASR 仅锁依赖，不下载模型、不执行推理，不加入普通 CI。

## 有意更新依赖

先修改对应 `.in`，使用本轮生成工具 `uv 0.13.4`；工具本身不属于业务运行依赖。只从官方源取得工具，安装或更新按当前授权执行。不要手写包哈希，也不要用宿主的全部 `pip freeze` 代替项目依赖。

在仓库根目录运行以下命令。现有锁会作为版本偏好；需要有意升级时增加 `--upgrade-package <准确包名>`，并检查间接依赖变化。ASR 锁独立更新。

```sh
uv pip compile config/requirements.in --universal --python-version 3.12 --generate-hashes --default-index https://pypi.org/simple --no-config --no-build -o config/requirements.txt
uv pip compile config/requirements-dev.in -c config/requirements.txt --universal --python-version 3.12 --generate-hashes --default-index https://pypi.org/simple --no-config --no-build -o config/requirements-dev.txt
uv pip compile config/requirements-asr.in --universal --python-version 3.12 --generate-hashes --default-index https://pypi.org/simple --no-config --no-build -o config/requirements-asr.txt
```

提交 `.in` 与 `.txt` 的准确差异，在隔离环境按哈希安装，再运行原有回归。已安装 dev 锁的环境可用 `python -m pip install --dry-run --no-index -r config/requirements-dev.in` 检查其是否满足维护输入；这不下载或升级任何包，不能取代重新解析与干净安装。

## 自动检查范围

`.github/workflows/checks.yml` 设计为目标分支为 `main` 的 PR 检查，以及显式手动触发。仅一个 Ubuntu 24.04 job、Python 3.12、Node 24；同一 PR 新提交取消旧运行，15 分钟超时。它不设定账单限额，是否收费取决于账户额度与计费设置，发布或触发前须核实授权。

步骤为：完整获取源码历史并补齐当前 runner 的本地 `main` 引用（供既有 `merge-base` 状态检查）、创建临时虚拟环境、按哈希安装 dev 锁、`pip check`、离线核对 `.in`、`npm ci --ignore-scripts --no-audit --no-fund`、从 Ubuntu 官方 apt 源安装 FFmpeg 并核对 ffmpeg/ffprobe、校验内置知识包与 CLI 入口、执行现有 Python 全回归和 Node 全回归。只补 runner 内的 Git 引用，不切换待测 revision、不推送远端。测试仅使用合成/临时资料和本地离线契约，不调用生成、业务网站或真实项目。下载依赖需要联网；测试名称中的 offline 不代表强制网络沙箱。

官方 Actions 固定到完整 commit SHA；仓库 token 仅 `contents: read`，checkout 不保留 Git 凭据。不使用 `pull_request_target`、secret、自托管 runner、部署、artifact 上传、依赖缓存或定时任务。它不安装可选 ASR/Blender，不配置分支保护，不自动合并。

pytest 显式收集 `scripts/tests` 与根目录 `tests`，不依赖从当前目录递归发现。`pytest -ra` 会列出当前明确的可选 ASR、Windows 与私有来源缺口；跳过不算通过。FFmpeg/ffprobe 缺失会直接使前置检查失败，而不是默默缩小预期覆盖。要让失败真正阻止 GitHub 合并，须在线上成功运行并获授权后另行设置 required check，不能仅凭 workflow 文件宣称已经强制保护 `main`。

## 本机验收环境

- 测试一律使用本轮独占的临时目录，并把 TMPDIR、basetemp、cache 规范化为真实 `/private/var` 路径；macOS 的 `/var` 是别名，混用会造成路径字符串断言误报，这类失败先按环境问题处理，不改断言迁就。
- 测试进程在 PATH 里找不到某个工具，只证明该进程环境缺失，不证明本机未安装。先沿项目已登记的工具入口核实工具真实存在与版本，再只给验收子进程补 PATH，并记录补的是哪条路径；不据此修改系统配置或项目依赖。

## 本轮本地验证（2026-11-04）

基线为 `e6d96a5d29a70fa705a1bd1ce10fff5a764965f5`，在独立维护分支验证，不改 `main`。复用已有隔离 Python 3.12.14 环境，核对全部适用 dev 锁版本逐包一致；Node 24.22.1 与已有 Zod 3.25.76。未安装全局包，未触发 GitHub Actions。

- 完整 Python 回归：2289 passed、177 subtests passed、4 skipped。跳过为可选 ASR 缺少 numpy、Windows 专项、两组未提供的私有来源回归。
- 完整 Node 回归：77 passed，0 failed。内置知识包 52 文件校验、CLI help、`pip check`、`.in` 离线兼容检查、Git 空白检查通过。
- 三份锁用文档命令重复生成后字节完全一致；Linux dev 的全部 12 个实际 wheel 已用 `pip download --require-hashes --only-binary=:all:` 从 PyPI 下载并校验，没有执行安装。
- core/dev 锁（dev 包含 core）用 `uv pip install --dry-run --reinstall --require-hashes --only-binary=:all: --python-version 3.12 --python-platform <目标> -r config/requirements-dev.txt` 对 Linux x64、Windows x64、macOS ARM64 解析通过；Windows 分支包含 colorama。此项未安装软件、不执行平台测试。
- ASR 锁同样对 Linux x64、Windows x64、macOS 14 ARM64 解析通过。uv 默认的 macOS 13 ARM64 目标明确失败：原有 `av==19.0.1` 的 ARM64 wheel 要求 macOS 14。使用单次进程变量 `MACOSX_DEPLOYMENT_TARGET=14.0` 后完整锁解析通过，没有改机器设置。保留原版本与 macOS 13 的失败事实，不放宽哈希、不自动源码构建或换版本；这不影响核心/dev，仍不能当成在 Mac 实机跑过 ASR。
- workflow YAML 和全部 shell 片段静态解析通过；未在 GitHub runner 执行，未验证在线安装、账户额度、计费或 required-check 设置。ASR 模型推理、其他操作系统实机、真实媒体和外部平台均未运行。

依据：[uv 通用解析](https://docs.astral.sh/uv/concepts/resolution/#universal-resolution)、[pip 安全安装](https://pip.pypa.io/en/stable/topics/secure-installs/)、[GitHub workflow 语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)。
