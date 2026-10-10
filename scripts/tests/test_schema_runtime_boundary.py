"""Standalone schema consumers must resolve dependencies without a web-service import."""

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import blender_previs_cli as previs


ROOT = Path(__file__).resolve().parents[2]


def _isolated(script: str, *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "-B", "-I", "-S", "-c", script, str(ROOT), *arguments],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_standalone_schema_loads_a_matching_dependency_directory():
    # Reuse installed packages as a read-only bundle. The child has no site-packages;
    # only runtime_deps can make this directory available to the schema consumer.
    dependency_root = Path(importlib.util.find_spec("jsonschema").origin).parents[1]
    _isolated("""
import importlib.util, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.core import runtime_deps, schema_contracts
assert importlib.util.find_spec('jsonschema') is None
runtime_deps.vendor_directory = lambda root: Path(sys.argv[2])
runtime_deps.read_stamp = lambda vendor: {
    'schema_version': runtime_deps.STAMP_SCHEMA_VERSION,
    **runtime_deps.interpreter_identity(Path(sys.argv[1])),
}
errors = schema_contracts.schema_errors({}, 'prompt-intent.schema.json')
assert errors, 'Invalid input must reach the actual schema validator'
import jsonschema
assert Path(jsonschema.__file__).is_relative_to(Path(sys.argv[2]))
""", str(dependency_root))


def test_blender_refuses_validation_when_dependencies_are_unavailable(tmp_path):
    _isolated("""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.core import runtime_deps
from scripts.blender_previs_cli import PrevisError, validate_recipe
runtime_deps.vendor_directory = lambda root: Path(sys.argv[2]) / 'missing-dependencies'
try:
    validate_recipe({'schema_version': '1.0'})
except PrevisError as exc:
    assert 'install_runtime_deps.py' in str(exc)
else:
    raise AssertionError('Missing dependencies silently skipped schema validation')
""", str(tmp_path))


def test_blender_preserves_schema_and_timing_validation():
    recipe = {
        "schema_version": "1.0",
        "project": {"name": "test", "fps": 24, "duration_seconds": 2, "resolution": [64, 64]},
        "environment": {"preset": "studio"},
        "actors": [],
        "camera": {"keyframes": [{"time": 0, "position": [0, -3, 1], "look_at": [0, 0, 1]}]},
        "outputs": {"blend": True, "preview_video": False},
    }
    previs.validate_recipe(recipe)
    with pytest.raises(previs.PrevisError, match="required property"):
        previs.validate_recipe({"schema_version": "1.0"})
    recipe["camera"]["keyframes"][0]["time"] = 3
    with pytest.raises(previs.PrevisError, match="超出项目时长"):
        previs.validate_recipe(recipe)
