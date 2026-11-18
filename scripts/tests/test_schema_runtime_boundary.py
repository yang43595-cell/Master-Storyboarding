"""Standalone schema consumers must resolve dependencies without a web-service import."""

import importlib.util
from pathlib import Path
import subprocess
import sys

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
    'packages': runtime_deps.vendored_packages(Path(sys.argv[2])),
}
errors = schema_contracts.schema_errors({}, 'prompt-intent.schema.json')
assert errors, 'Invalid input must reach the actual schema validator'
import jsonschema
assert Path(jsonschema.__file__).is_relative_to(Path(sys.argv[2]))
""", str(dependency_root))
