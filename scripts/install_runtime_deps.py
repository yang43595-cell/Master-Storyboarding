"""隔离运行依赖的唯一安装与登记入口。

用法：

```
python scripts/install_runtime_deps.py            # 用当前解释器安装/登记隔离依赖
python scripts/install_runtime_deps.py --check    # 只报告，不写盘
python scripts/install_runtime_deps.py --force    # 清空后按当前解释器重建
```

不变式：一个 `.master-storyboarding/visual-library-deps` 只属于一个解释器。目录里已有
属于其他解释器的 stamp 时，本脚本拒绝安装（避免 3.12 与 3.13 的扩展混在同一目录），
除非显式 `--force` 重建。判定动作集中在 `plan_action()`，可被测试直接调用。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from core import runtime_deps  # noqa: E402

ACTION_OK = "ok"
ACTION_ADOPT = "adopt"
ACTION_INSTALL = "install"
ACTION_REFUSE = "refuse"


def stamp_body(root: Path) -> dict[str, object]:
    body: dict[str, object] = {"schema_version": runtime_deps.STAMP_SCHEMA_VERSION}
    body.update(runtime_deps.interpreter_identity(root))
    body["packages"] = runtime_deps.vendored_packages(runtime_deps.vendor_directory(root))
    return body


def write_stamp(root: Path) -> dict[str, object]:
    vendor = runtime_deps.vendor_directory(root)
    body = stamp_body(root)
    (vendor / runtime_deps.STAMP_NAME).write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return body


def verify(root: Path) -> tuple[bool, str]:
    vendor = runtime_deps.vendor_directory(root)
    if not vendor.is_dir():
        return False, "隔离依赖目录不存在"
    return runtime_deps.verify_vendored(root)


def plan_action(root: Path) -> dict[str, object]:
    """决定该对隔离依赖做什么；只读判定，不安装也不写盘。"""
    vendor = runtime_deps.vendor_directory(root)
    stamp = runtime_deps.read_stamp(vendor)
    problem = runtime_deps.stamp_problem(root, stamp)
    plan: dict[str, object] = {
        "vendor": str(vendor),
        "vendor_exists": vendor.is_dir(),
        "stamp": stamp,
        "stamp_problem": problem,
    }
    if stamp is not None and not problem:
        ok, detail = verify(root)
        plan["probe"] = {"通过": ok, "说明": detail}
        if ok:
            plan.update(action=ACTION_OK, reason="stamp 匹配且实际依赖导入通过，无需改动")
        else:
            plan.update(action=ACTION_REFUSE, reason=f"旧 stamp 对应的依赖已不可用（{detail}）；核对后用 --force 重建，不覆盖当前目录")
        return plan
    if not vendor.is_dir():
        plan.update(action=ACTION_INSTALL, reason="隔离目录不存在，按当前解释器安装")
        return plan
    ok, detail = verify(root)
    plan["probe"] = {"通过": ok, "说明": detail}
    if ok and stamp is None:
        plan.update(action=ACTION_ADOPT, reason="隔离目录可用但未登记，按当前解释器登记 stamp")
        return plan
    if stamp is not None:
        plan.update(
            action=ACTION_REFUSE,
            reason=(f"{problem}；如确实要把该目录改绑到当前解释器，加 --force 重建"
                    "（会先清空目录，另一个解释器将失去它的隔离依赖）"),
        )
        return plan
    plan.update(
        action=ACTION_REFUSE,
        reason=(f"隔离目录无法被当前解释器导入（{detail}）；为免破坏可能属于其他解释器的安装，"
                "脚本不会直接覆盖；确认可重建时加 --force"),
    )
    return plan


def _pip_install(root: Path) -> None:
    vendor = runtime_deps.vendor_directory(root)
    requirements = runtime_deps.requirements_path(root)
    if not requirements.is_file():
        raise SystemExit(f"找不到依赖清单：{requirements}")
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--target", str(vendor),
         "--upgrade", "-r", str(requirements)],
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit("隔离依赖安装失败；上面的 pip 输出是唯一依据，未写入 stamp")


def report(root: Path) -> dict[str, object]:
    plan = plan_action(root)
    return {
        "解释器": runtime_deps.interpreter_identity(root),
        "计划": plan,
        "本次运行会使用": runtime_deps.resolve_runtime_deps(str(root)).as_dict(),
        "当前仍缺的必需依赖": runtime_deps.missing_required(root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="登记或重建视觉资产库的隔离依赖")
    parser.add_argument("--check", action="store_true", help="只报告状态，不安装也不写 stamp")
    parser.add_argument("--force", action="store_true", help="清空隔离目录后按当前解释器重建")
    args = parser.parse_args(argv)

    root = ROOT
    runtime_deps.resolve_runtime_deps.cache_clear()
    plan = plan_action(root)

    if args.check:
        print(json.dumps(report(root), ensure_ascii=False, indent=2))
        return 0

    if args.force:
        vendor = runtime_deps.vendor_directory(root)
        if vendor.exists():
            shutil.rmtree(vendor)
        plan = {"action": ACTION_INSTALL, "reason": "--force：按当前解释器重建"}

    action = plan["action"]
    if action == ACTION_OK:
        print(f"隔离依赖已登记且当前解释器可用：{runtime_deps.vendor_directory(root)}")
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if action == ACTION_ADOPT:
        print(json.dumps(write_stamp(root), ensure_ascii=False, indent=2))
        return 0
    if action == ACTION_REFUSE:
        raise SystemExit(plan["reason"])

    _pip_install(root)
    ok, detail = verify(root)
    if not ok:
        raise SystemExit(f"安装后仍无法导入（{detail}），未写入 stamp")
    print(json.dumps(write_stamp(root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())