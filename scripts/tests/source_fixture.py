"""Small, writable source replicas for CLI rule/hash mutation tests.

Copy the working files, not HEAD: edits, new source files and missing references
must remain visible to the tests. This also works from a source archive or from
an earlier replica, without Git or a copied Python/Node environment.
"""
from pathlib import Path
import shutil


# These are the inputs used by stage_context_cli and its subprocesses. Business
# projects, assets, evidence output and machine state are not fixture inputs.
SOURCE_ENTRIES = (
    "PROJECT_RULES.md", "config", "docs", "knowledge", "references",
    "schemas", "scripts", "skills",
)
_LOCAL_STATE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "node_modules", "__pycache__", "*.py[co]",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    ".blender-previs-cache", ".master-storyboarding", ".tmp_*",
    "regression_evidence", ".repair-evidence", ".env", ".env.*",
)


def _ignore_local_state(directory, names):
    ignored = set(_LOCAL_STATE(directory, names))
    ignored.discard(".env.example")
    # Never follow a source link into a real project or share writes with it.
    # Dependency links are excluded above, just like dependency directories.
    for name in set(names) - ignored:
        if (Path(directory) / name).is_symlink():
            raise ValueError(f"source fixture must not contain symlinks: {Path(directory) / name}")
    return ignored


def copy_source_fixture(source: Path, destination: Path) -> Path:
    """Copy required source roots to a new, caller-owned temporary directory.

    Missing roots and I/O errors are failures, never silently repaired/skipped.
    No hardlinks or symlinks are created, so mutations cannot touch the source.
    """
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.resolve().is_relative_to(source):
        raise ValueError("source fixture destination must be outside its source")
    for entry in SOURCE_ENTRIES:
        path = source / entry
        if path.is_symlink():
            raise ValueError(f"source fixture must not contain symlinks: {path}")
        if not path.exists():
            raise FileNotFoundError(path)
    destination.mkdir(parents=True)  # Fail on reuse rather than overwrite a fixture.
    for entry in SOURCE_ENTRIES:
        path, target = source / entry, destination / entry
        if path.is_dir():
            shutil.copytree(path, target, ignore=_ignore_local_state)
        else:
            shutil.copy2(path, target)
    return destination
