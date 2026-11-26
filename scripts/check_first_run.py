"""Inventory the tools available to a fresh checkout using the standard library."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPTIONAL = {
    "images": "本地图片",
    "media": "音视频处理",
    "redframe": "红帧",
    "asr": "本地转写",
    "web": "网页契约校验",
}


def run_probe(*command: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode:
        output = (result.stderr or result.stdout).strip().splitlines()
        return False, output[-1] if output else f"退出码 {result.returncode}"
    output = result.stdout.strip().splitlines()
    return True, output[0] if output else "可用"


def command_version(name: str, option: str) -> tuple[bool, str]:
    executable = shutil.which(name)
    if executable is None:
        return False, "当前 PATH 找不到"
    ok, detail = run_probe(str(Path(executable).absolute()), option)
    return ok, f"{detail} ({executable})"


def edge_executable() -> Path | None:
    if sys.platform == "darwin":
        candidates = [
            Path("/Applications/Microsoft Edge.app"),
            Path.home() / "Applications/Microsoft Edge.app",
        ]
        return next(
            (app for app in candidates if (app / "Contents/MacOS/Microsoft Edge").is_file()),
            None,
        )
    if sys.platform == "win32":
        candidates = [
            Path(os.environ[key]) / "Microsoft/Edge/Application/msedge.exe"
            for key in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")
            if os.environ.get(key)
        ]
        return next((path for path in candidates if path.is_file()), None)
    executable = shutil.which("microsoft-edge") or shutil.which("microsoft-edge-stable")
    if executable:
        return Path(executable)
    candidate = Path("/opt/microsoft/msedge/msedge")
    return candidate if candidate.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser(description="新电脑首次运行工具检查（只读）")
    parser.add_argument("--require", choices=OPTIONAL, action="append", default=[],
                        help="将对应能力的缺项计入退出码，可重复指定")
    parser.add_argument("--redframe-helper", type=Path,
                        default=Path.home() / ".local/share/codex-headless-browser/headless_edge.py",
                        help="独立后台浏览器入口 headless_edge.py 的路径")
    parser.add_argument("--asr-python", default=sys.executable, help="独立 ASR 环境的 Python")
    parser.add_argument("--asr-model", type=Path, help="已下载的 CTranslate2 模型目录")
    args = parser.parse_args()

    failed = []
    required = {"core", *args.require}

    def report(group: str, name: str, ok: bool, detail: str, hint: str = "") -> None:
        status = "通过" if ok else "缺项"
        requirement = "必需" if group in required else "按需"
        print(f"[{status} · {requirement}] {name}  {detail}")
        if not ok and hint:
            print(f"  补齐方式：{hint}")
        if not ok and group in required:
            failed.append(name)

    print(f"检查环境：{sys.executable}")
    report("core", "Python", sys.version_info[:2] >= (3, 12), sys.version.split()[0],
           "使用 Python 3.12。当前锁文件以 3.12 为验证基线。")
    if sys.version_info[:2] >= (3, 12):
        report("core", "项目 Python 依赖", *run_probe(
            sys.executable, "-B", "-c", "import jsonschema, referencing"),
            "在项目虚拟环境按 config/requirements.txt 安装后，用该解释器重跑。")
        report("core", "项目 CLI", *run_probe(
            sys.executable, "-B", "scripts/stage_context_cli.py", "--help"))
        report("core", "内置知识包", *run_probe(
            sys.executable, "-B", "-m", "scripts.core.knowledge_runtime"),
            "检查检出内容和文件换行，按启动说明恢复同一版本的完整知识包。")

    print("\n按制作场景检查")
    report("images", "Pillow", *run_probe(sys.executable, "-B", "-c", "from PIL import Image"),
           "在项目虚拟环境按 config/requirements-dev.txt 安装。")
    for name in ("ffmpeg", "ffprobe"):
        # The local ASR CLI needs FFmpeg, while general media workflows also use ffprobe.
        group = "asr" if name == "ffmpeg" and "asr" in required else "media"
        report(group, name, *command_version(name, "-version"),
               "安装系统 FFmpeg 工具，或把已安装工具所在目录加入本次进程的 PATH。")

    report("redframe", "Playwright", *run_probe(
        sys.executable, "-B", "-c", "from playwright.sync_api import sync_playwright"),
        "在用于浏览器操作的 Python 环境安装 Playwright，再用该环境重跑。")
    edge = edge_executable()
    report("redframe", "Microsoft Edge", edge is not None,
           str(edge) if edge else "常用位置未找到 Edge", "安装 Edge 或核对实际安装位置。")
    helper = args.redframe_helper.expanduser()
    report("redframe", "独立后台浏览器入口", helper.is_file(), str(helper),
           "配置目标系统适用的 headless_edge.py，可用 --redframe-helper 指定位置。")
    if sys.platform == "win32":
        print("[待实测] 红帧 Windows 适配需支持本机登录状态，现有 Mac 入口依赖 Unix 权限。")
    print("[待实测] 红帧独立会话启动、内网访问、登录及目标画布权限。")

    report("asr", "ASR Python 依赖", *run_probe(
        args.asr_python, "-B", "-c", "import numpy; from faster_whisper import WhisperModel"),
        "在独立 ASR 环境按 config/requirements-asr.txt 安装，用 --asr-python 指定解释器。")
    if args.asr_model:
        model = args.asr_model.expanduser()
        missing = [name for name in ("model.bin", "config.json") if not (model / name).is_file()]
        detail = f"{model} 缺少 {', '.join(missing)}" if missing else str(model)
        report("asr", "CTranslate2 模型文件", not missing, detail,
               "按 docs/development/local-asr.md 准备本地模型目录。")
    else:
        report("asr", "CTranslate2 模型文件", False, "未指定模型目录",
               "用 --asr-model 指定已下载的本地 CTranslate2 模型目录。")
    print("[待实测] 使用短音频验证转写推理。")

    node_ok, node_detail = command_version("node", "--version")
    report("web", "Node.js 24", node_ok and node_detail.startswith("v24."), node_detail,
           "为红帧 JS 离线回归测试安装 Node.js 24。")
    print("[待实测] 用到送审时确认工作台凭据和项目权限，用到深度复刻时在红帧画布确认「深度动作捕捉」入口。")

    if failed:
        print(f"\n必需项有缺口：{', '.join(failed)}")
        return 1
    print("\n本次选择的本机工具检查通过。待实测项需要按实际场景检查。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
