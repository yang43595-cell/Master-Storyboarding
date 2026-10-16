from __future__ import annotations

import json

import pytest

from scripts.init_desktop_project import PROJECT_DIRECTORIES, initialize


def test_initialize_creates_current_stage_layout(tmp_path) -> None:
    project = initialize(tmp_path / "新项目")
    assert project.name == "新项目"
    assert all((project / directory).is_dir() for directory in PROJECT_DIRECTORIES)
    assert json.loads((project / "00_项目管理" / "project_request.json").read_text(encoding="utf-8")) == {
        "schema_version": "1.0", "project_mode": "new", "project_name": "新项目",
    }
    state = json.loads((project / "00_项目管理" / "project_state.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == "stage-1.0"
    assert state["current_stage"] == ""


def test_initialize_refuses_an_existing_directory(tmp_path) -> None:
    project = tmp_path / "已有项目"
    project.mkdir()
    with pytest.raises(ValueError, match="拒绝覆盖"):
        initialize(project)

def test_user_manual_lists_the_authoritative_project_directories() -> None:
    """手册目录表漂移过一次（旧布局被当成现行）；这里对齐唯一权威。"""
    from pathlib import Path

    manual = Path(__file__).resolve().parents[2] / "docs" / "development" / "README.md"
    text = manual.read_text(encoding="utf-8")
    missing = [directory for directory in PROJECT_DIRECTORIES if f"`{directory}`" not in text]
    assert missing == [], f"手册缺少现行目录：{missing}"
