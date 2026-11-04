"""Small, writable source replicas for CLI rule/hash mutation tests.

Copy the working files, not HEAD: edits, new source files and missing references
must remain visible to the tests. This also works from a source archive or from
an earlier replica, without Git or a copied Python/Node environment.
"""
from pathlib import Path
import fnmatch
import shutil
import stat


# These are the inputs used by stage_context_cli and its subprocesses. Business
# projects, assets, evidence output and machine state are not fixture inputs.
SOURCE_ENTRIES = (
    "PROJECT_RULES.md", "config", "docs", "knowledge", "references",
    "schemas", "scripts", "skills",
)
_EXCLUDED_NAMES = (
    ".git", ".venv", "venv", "node_modules", "__pycache__", "*.py[cod]",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    ".blender-previs-cache", ".master-storyboarding", ".tmp_*",
    "regression_evidence", ".repair-evidence", "inputs", "history", "evidence_sources",
    "product-references", "*transcript*", ".aws", ".ssh", ".claude", ".codex",
    ".git-credentials", ".env", ".env.*", "*.env", "credentials*.json",
    "*.pem", "*.key", "*.p12", "*.pfx",
    "*.mp4", "*.mov", "*.mkv", "*.avi", "*.webm", "*.wav", "*.mp3",
    "*.m4a", "*.flac", "*.ogg", "*.blend", "*.glb", "*.gltf", "*.png",
    "*.jpg", "*.jpeg", "*.webp", "*.gif", "*.bmp", "*.tif", "*.tiff",
)
# Every currently used file type beneath SOURCE_ENTRIES is represented here.
# Unknown types fail rather than silently dropping a new runtime dependency.
_SOURCE_SUFFIXES = {
    ".py", ".md", ".json", ".js", ".cjs", ".mjs", ".ts", ".ps1",
    ".cmd", ".sh", ".txt", ".in", ".yaml", ".yml", ".ini", ".css", ".html",
}
_SOURCE_FILENAMES = {"LICENSE", ".env.example"}


def _ignore_local_state(directory, names):
    ignored = {name for name in names
               if any(fnmatch.fnmatchcase(name.lower(), pattern) for pattern in _EXCLUDED_NAMES)}
    ignored.discard(".env.example")
    # Never follow a source link into a real project or share writes with it.
    # Dependency links are excluded above, just like dependency directories.
    for name in set(names) - ignored:
        path = Path(directory) / name
        if path.is_symlink():
            raise ValueError(f"source fixture must not contain symlinks: {path}")
        if not path.is_dir() and name not in _SOURCE_FILENAMES and path.suffix.lower() not in _SOURCE_SUFFIXES:
            raise ValueError(f"unsupported source fixture file; declare its purpose before copying: {path}")
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
    # copy2/copytree preserve source modes, including a read-only checkout.
    # Make only the newly created replica writable for mutation tests.
    for path in destination.rglob("*"):
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
    return destination
