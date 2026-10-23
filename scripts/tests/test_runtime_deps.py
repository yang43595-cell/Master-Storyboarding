"""隔离依赖解析边界（core.runtime_deps）与安装登记的自动验收。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from scripts import install_runtime_deps as installer
from scripts.core import runtime_deps


@pytest.fixture(autouse=True)
def _clear_resolution_cache():
    runtime_deps.resolve_runtime_deps.cache_clear()
    yield
    runtime_deps.resolve_runtime_deps.cache_clear()
    vendor = runtime_deps.vendor_directory(Path.cwd())
    if str(vendor) in sys.path:
        sys.path.remove(str(vendor))


def _write_package(vendor: Path, name: str, body: str) -> Path:
    package = vendor / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(body, encoding="utf-8")
    return package


def _foreign_stamp(root: Path, **overrides) -> dict:
    stamp = dict(runtime_deps.interpreter_identity(root))
    stamp.update(schema_version=runtime_deps.STAMP_SCHEMA_VERSION, packages=["demo_package"])
    stamp.update(overrides)
    return stamp


def test_foreign_stamp_is_never_inserted_into_sys_path(tmp_path: Path):
    vendor = runtime_deps.vendor_directory(tmp_path)
    _write_package(vendor, "demo_package", "VALUE = 1\n")
    (vendor / runtime_deps.STAMP_NAME).write_text(
        json.dumps(_foreign_stamp(tmp_path, python_major_minor="0.0")), encoding="utf-8"
    )

    resolved = runtime_deps.ensure_runtime_deps(tmp_path)

    assert resolved.path is None
    assert resolved.source == "host"
    assert "不匹配" in resolved.detail
    assert str(vendor) not in sys.path


def test_stale_vendor_copy_cannot_shadow_the_host_package(tmp_path: Path):
    """复现历史故障：3.12 装的隔离目录在 3.13 下抢走 import jsonschema。"""
    vendor = runtime_deps.vendor_directory(tmp_path)
    _write_package(vendor, "jsonschema", "from rpds import missing_extension\n")

    resolved = runtime_deps.ensure_runtime_deps(tmp_path)

    assert resolved.path is None
    assert "jsonschema" in resolved.detail
    assert str(vendor) not in sys.path
    spec = importlib.util.find_spec("jsonschema")
    assert spec is not None, "宿主环境应提供可用的 jsonschema"
    assert "visual-library-deps" not in str(spec.origin or "")


def test_unimportable_vendor_directory_is_refused(tmp_path: Path):
    vendor = runtime_deps.vendor_directory(tmp_path)
    _write_package(vendor, "broken_package", "raise ImportError('stale build')\n")

    resolved = runtime_deps.ensure_runtime_deps(tmp_path)

    assert resolved.path is None
    assert "broken_package" in resolved.detail


def test_matching_stamp_is_trusted_without_probe(tmp_path: Path):
    vendor = runtime_deps.vendor_directory(tmp_path)
    _write_package(vendor, "demo_package", "VALUE = 1\n")
    installer.write_stamp(tmp_path)

    resolved = runtime_deps.ensure_runtime_deps(tmp_path)

    assert resolved.source == "vendored"
    assert resolved.path == str(vendor)
    assert str(vendor) in sys.path
    assert importlib.import_module("demo_package").VALUE == 1
    sys.path.remove(str(vendor))
    sys.modules.pop("demo_package", None)


def test_stamp_problem_reports_interpreter_and_requirements_drift(tmp_path: Path):
    vendor = runtime_deps.vendor_directory(tmp_path)
    _write_package(vendor, "demo_package", "VALUE = 1\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "requirements.txt").write_text("jsonschema>=4.26,<4.27\n", encoding="utf-8")
    stamp = installer.write_stamp(tmp_path)
    assert runtime_deps.stamp_problem(tmp_path, stamp) == ""

    assert "python_major_minor" in runtime_deps.stamp_problem(
        tmp_path, dict(stamp, python_major_minor="0.0")
    )
    (tmp_path / "config" / "requirements.txt").write_text("jsonschema>=4.0\n", encoding="utf-8")
    assert "requirements_sha256" in runtime_deps.stamp_problem(tmp_path, stamp)


def test_installer_plan_never_rebinds_a_foreign_directory(tmp_path: Path):
    vendor = runtime_deps.vendor_directory(tmp_path)
    _write_package(vendor, "demo_package", "VALUE = 1\n")
    plan = installer.plan_action(tmp_path)
    assert plan["action"] == installer.ACTION_ADOPT  # 未登记但本解释器可用

    (vendor / runtime_deps.STAMP_NAME).write_text(
        json.dumps(_foreign_stamp(tmp_path, python_major_minor="0.0")), encoding="utf-8"
    )
    plan = installer.plan_action(tmp_path)
    assert plan["action"] == installer.ACTION_REFUSE
    assert "--force" in plan["reason"]

    (vendor / runtime_deps.STAMP_NAME).unlink()
    (vendor / "demo_package" / "__init__.py").write_text("raise ImportError('x')\n", encoding="utf-8")
    plan = installer.plan_action(tmp_path)
    assert plan["action"] == installer.ACTION_REFUSE
    assert "--force" in plan["reason"]


def test_installer_plan_installs_when_directory_is_absent(tmp_path: Path):
    assert installer.plan_action(tmp_path)["action"] == installer.ACTION_INSTALL


def test_only_runtime_deps_may_add_the_vendor_directory_to_sys_path():
    """唯一边界：其他脚本不得自己把隔离目录塞进 sys.path。"""
    scripts_root = Path(runtime_deps.__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in scripts_root.rglob("*.py"):
        if "tests" in path.parts or path.name == "runtime_deps.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "sys.path.insert" not in line:
                continue
            if "visual-library-deps" in line or "vendor_directory(" in line:
                offenders.append(f"{path.name}:{number}")
    assert offenders == []
