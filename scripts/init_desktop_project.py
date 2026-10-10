#!/usr/bin/env python3
"""创建符合当前阶段式流程的桌面项目目录。

用法：
  python scripts/init_desktop_project.py --name "项目名"
  python scripts/init_desktop_project.py --path "C:\\Users\\LIUYANG\\Desktop\\项目名"
"""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from .core.project_state import new_project_state, write_project_state
    from .core.io import write_json
except ImportError:  # direct CLI execution
    from core.project_state import new_project_state, write_project_state
    from core.io import write_json


PROJECT_DIRECTORIES = (
    "00_项目管理",
    "01_原始资料",
    "02_编导",
    "03_导演",
    "04_资产",
    "05_分镜",
    "06_视频",
    "07_声音与配音",
    "99_历史与废弃",
)


def initialize(project: Path) -> Path:
    """Create an empty project only; never merge into an existing directory."""
    project = project.expanduser().resolve()
    if project.exists():
        raise ValueError(f"目标项目目录已存在，拒绝覆盖：{project}")

    project.mkdir(parents=True)
    for relative in PROJECT_DIRECTORIES:
        (project / relative).mkdir()

    write_json(project / "00_项目管理" / "project_request.json", {
        "schema_version": "1.0",
        "project_mode": "new",
        "project_name": project.name,
    })
    write_project_state(project, new_project_state())
    return project


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--path", help="新桌面项目目录的绝对路径")
    target.add_argument("--name", help="在当前用户桌面创建的项目名称")
    args = parser.parse_args()
    path = Path(args.path) if args.path else Path.home() / "Desktop" / args.name
    print(initialize(path))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
