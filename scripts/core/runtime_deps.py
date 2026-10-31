"""第三方运行依赖的唯一解析出口。

本仓库同时被两种解释器使用：项目 Python（3.13，跑测试与确定性工具）和 Codex 内置
Python（3.12，跑视觉资产库服务）。隔离依赖目录 `.master-storyboarding/visual-library-deps`
只能交给真正能导入它的解释器；否则会把为另一个 ABI 编译的扩展抢先放进 sys.path，
静默破坏同进程后续的普通导入（历史故障：3.12 装的 rpds 让 3.13 下 import jsonschema
失败，97 项测试与资产 schema 校验一起挂掉）。

判定规则只有这一处：

1. 隔离目录带可信 `stamp.json`（解释器标识、平台、依赖清单哈希都对得上）→ 仍核包清单并真实导入；
2. 没有可信 stamp → 用当前解释器起一次隔离子进程，逐个导入目录里的顶层包，要求
   解析结果确实落在该目录内；探测不通过就不插入；
3. 不使用隔离目录时，交给当前环境已有的 site-packages，不猜测、不报错。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

VENDOR_RELATIVE = Path(".master-storyboarding") / "visual-library-deps"
STAMP_NAME = "stamp.json"
STAMP_SCHEMA_VERSION = "runtime-deps-1.0"
REQUIREMENTS_RELATIVE = Path("config") / "requirements.txt"
REMEDY_COMMAND = "python scripts/install_runtime_deps.py"

_PROBE_TIMEOUT_SECONDS = 120
_SKIP_DIRECTORIES = {"__pycache__", "bin", "Scripts", "include", "lib", "share"}


class RuntimeDepsError(RuntimeError):
    """隔离依赖不可用且当前环境也缺少必需依赖。"""


@dataclass(frozen=True)
class RuntimeDeps:
    """一次依赖解析结果；path 为 None 表示不使用隔离目录。"""

    path: str | None
    source: str
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "source": self.source, "detail": self.detail}


def vendor_directory(root: Path) -> Path:
    return Path(root) / VENDOR_RELATIVE


def requirements_path(root: Path) -> Path:
    return Path(root) / REQUIREMENTS_RELATIVE


def requirements_digest(root: Path) -> str:
    path = requirements_path(root)
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def interpreter_identity(root: Path) -> dict[str, str]:
    return {
        "python_version": sys.version.split()[0],
        "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": sys.platform,
        "requirements_sha256": requirements_digest(root),
    }


def vendored_packages(vendor: Path) -> list[str]:
    """列出隔离目录里可导入的顶层包名，用于逐包探测。"""
    names: list[str] = []
    if not vendor.is_dir():
        return names
    for entry in sorted(vendor.iterdir()):
        name = entry.name
        if name.startswith(".") or name in _SKIP_DIRECTORIES or name.endswith(".dist-info"):
            continue
        if entry.is_dir():
            if (entry / "__init__.py").is_file() or (entry / "__init__.pyi").is_file():
                names.append(name)
        elif entry.suffix == ".py" and name != "setup.py":
            names.append(entry.stem)
    return names


def read_stamp(vendor: Path) -> dict[str, object] | None:
    path = vendor / STAMP_NAME
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("schema_version") != STAMP_SCHEMA_VERSION:
        return None
    return value


def stamp_problem(root: Path, stamp: dict[str, object] | None) -> str:
    """返回 stamp 不可用的原因；空字符串表示可信。"""
    if stamp is None:
        return "隔离依赖目录缺少 stamp.json，无法证明它是为哪个解释器安装的"
    for key, value in interpreter_identity(root).items():
        if str(stamp.get(key) or "") != value:
            return (f"隔离依赖的 {key}={stamp.get(key)!r} 与当前解释器 {value!r} 不匹配，"
                    f"不能用于本次运行")
    return ""


def _probe_source() -> str:
    # 必须真正 import：find_spec 只看得到包目录，看不出里面的扩展是否为当前 ABI 编译。
    return (
        "import importlib, json, sys\nfrom pathlib import Path\n"
        "root = sys.argv[1]\n"
        "names = json.loads(sys.argv[2])\n"
        "sys.path.insert(0, root)\n"
        "failed = {}\n"
        "for name in names:\n"
        "    try:\n"
        "        module = importlib.import_module(name)\n"
        "    except BaseException as exc:\n"
        "        failed[name] = f'{type(exc).__name__}: {exc}'\n"
        "        continue\n"
        "    paths = list(getattr(module, '__path__', []) or [])\n"
        "    origin = str(getattr(module, '__file__', '') or '')\n"
        "    if not (paths and all(Path(item).resolve().is_relative_to(Path(root).resolve()) for item in paths)) and not (origin and Path(origin).resolve().is_relative_to(Path(root).resolve())):\n"
        "        failed[name] = 'imported from outside the vendor directory'\n"
        "print(json.dumps(failed))\n"
    )


def probe_vendored(vendor: Path, packages: list[str], *, python: str | None = None) -> tuple[bool, str]:
    """用当前解释器隔离探测隔离目录；只有全部顶层包都解析到该目录才算通过。"""
    if not packages:
        return False, "隔离依赖目录里没有可导入的包"
    command = [python or sys.executable, "-I", "-c", _probe_source(), str(vendor), json.dumps(packages)]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"隔离依赖探测无法执行：{exc}"
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip().splitlines()
        return False, f"隔离依赖探测失败：{tail[-1] if tail else '退出码 ' + str(completed.returncode)}"
    try:
        missing = json.loads(completed.stdout.strip())
    except ValueError:
        return False, "隔离依赖探测返回了无法解析的结果"
    if not isinstance(missing, dict):
        return False, "隔离依赖探测没有返回逐包结果对象"
    if missing:
        return False, f"隔离依赖无法被当前解释器导入：{', '.join(missing)}"
    return True, f"{len(packages)} 个顶层包均解析到隔离目录"


def verify_vendored(root: Path) -> tuple[bool, str]:
    """A matching installation receipt does not prove surviving package bytes."""
    vendor = vendor_directory(root)
    packages = vendored_packages(vendor)
    stamp = read_stamp(vendor)
    if stamp is not None and not stamp_problem(root, stamp):
        recorded = stamp.get("packages")
        if (not isinstance(recorded, list) or not recorded
                or any(not isinstance(name, str) for name in recorded)
                or sorted(recorded) != packages):
            return False, "隔离依赖实际包清单与 stamp 不一致，不能沿用旧安装通过"
    return probe_vendored(vendor, packages)


@lru_cache(maxsize=16)
def resolve_runtime_deps(root_text: str) -> RuntimeDeps:
    """决定是否使用隔离依赖目录；只读判定，不修改 sys.path。"""
    root = Path(root_text)
    vendor = vendor_directory(root)
    if not vendor.is_dir():
        return RuntimeDeps(None, "host", f"没有隔离依赖目录，按当前环境导入；需要隔离时可运行 {REMEDY_COMMAND}")
    stamp = read_stamp(vendor)
    if stamp is not None:
        # 已登记的目录只属于登记时那一台解释器：不匹配就直接弃用，绝不试探性加载。
        reason = stamp_problem(root, stamp)
        if not reason:
            ok, detail = verify_vendored(root)
            if ok:
                return RuntimeDeps(str(vendor), "vendored", f"stamp 匹配且当前实际导入通过：{detail}")
            return RuntimeDeps(None, "host", f"旧 stamp 不能证明当前依赖可用：{detail}；改用当前环境")
        return RuntimeDeps(
            None, "host",
            f"{reason}；本次运行改用已有环境，如需属于当前解释器的隔离依赖请运行 {REMEDY_COMMAND}"
        )
    ok, detail = probe_vendored(vendor, vendored_packages(vendor))
    if ok:
        return RuntimeDeps(str(vendor), "vendored", f"未登记 stamp，但当前解释器探测通过：{detail}")
    return RuntimeDeps(None, "host", f"隔离依赖目录未登记且当前解释器无法导入（{detail}）；"
                                     f"本次运行改用已有环境，如需重建请运行 {REMEDY_COMMAND}")


def ensure_runtime_deps(root: Path) -> RuntimeDeps:
    """唯一允许把隔离依赖加入 sys.path 的入口。"""
    resolved = resolve_runtime_deps(str(Path(root)))
    if resolved.path and resolved.path not in sys.path:
        sys.path.insert(0, resolved.path)
    return resolved


def missing_required(root: Path, required: tuple[str, ...] = ("jsonschema", "referencing")) -> list[str]:
    """当前进程（含已按需加入的隔离依赖）仍无法导入的必需依赖。"""
    import importlib

    missing: list[str] = []
    for name in required:
        try:
            importlib.import_module(name)
        except (ImportError, ValueError, OSError, RuntimeError, SyntaxError):
            missing.append(name)
    return missing


def require_runtime_deps(root: Path, required: tuple[str, ...] = ("jsonschema", "referencing")) -> RuntimeDeps:
    """先解析，再要求必需依赖可用；缺失时给出可执行的补救命令。"""
    resolved = ensure_runtime_deps(root)
    missing = missing_required(root, required)
    if missing:
        raise RuntimeDepsError(
            f"缺少运行依赖 {', '.join(missing)}（{resolved.detail}）；"
            f"请用当前解释器运行 `{REMEDY_COMMAND}` 安装到隔离目录"
        )
    return resolved