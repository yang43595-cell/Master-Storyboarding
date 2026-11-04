"""Source snapshots stay small without hiding source drift or copy failures."""
from pathlib import Path

import pytest

from scripts.tests import source_fixture


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    for name in source_fixture.SOURCE_ENTRIES:
        if name.endswith(".md"):
            (root / name).write_bytes(b"working rules\r\n")
        else:
            (root / name).mkdir()
    return root


def write(root, relative, data=b"synthetic local state"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_only_required_sources_are_copied_without_dependencies(source, tmp_path):
    required = {
        "PROJECT_RULES.md": b"working rules\r\n",
        "skills/new-uncommitted-method.md": "新增方法\r\n".encode(),
        "references/synthetic-fixture.bin": bytes(range(256)),
        "scripts/.env.example": b"EXAMPLE=placeholder\n",
    }
    for relative, data in required.items():
        write(source, relative, data)
    for directory in (".git", ".venv", "venv", "node_modules", "inputs", "assets",
                      "regression_evidence", ".repair-evidence", ".arbitrary-local-venv"):
        write(source, f"{directory}/do-not-copy")
    for directory in (".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
                      ".blender-previs-cache", ".master-storyboarding", ".tmp_session"):
        write(source, f"scripts/{directory}/do-not-copy")
    for name in (".env", ".env.local", "cached.pyc"):
        write(source, "skills/" + name)
    # Sparse dependency data reproduces amplification without consuming RAM/disk.
    with (source / ".venv/large-dependency").open("wb") as handle:
        handle.truncate(512 * 1024 * 1024)
    replica = source_fixture.copy_source_fixture(source, tmp_path / "replica")
    copied = {p.relative_to(replica).as_posix(): p.read_bytes()
              for p in replica.rglob("*") if p.is_file()}
    assert copied == required


def test_recopy_keeps_working_edits_new_files_and_missing_files(source, tmp_path):
    write(source, "skills/method.md", b"before")
    first = source_fixture.copy_source_fixture(source, tmp_path / "nested/first")
    (first / "skills/method.md").unlink()
    write(first, "scripts/new-module.py", b"VALUE = 'uncommitted'\n")
    (first / "PROJECT_RULES.md").write_bytes(b"changed working bytes\n")
    second = source_fixture.copy_source_fixture(first, tmp_path / "second")
    assert not (second / "skills/method.md").exists()
    assert (second / "scripts/new-module.py").read_bytes() == b"VALUE = 'uncommitted'\n"
    assert (second / "PROJECT_RULES.md").read_bytes() == b"changed working bytes\n"
    (second / "PROJECT_RULES.md").write_bytes(b"isolated mutation")
    assert (first / "PROJECT_RULES.md").read_bytes() == b"changed working bytes\n"
    assert (source / "PROJECT_RULES.md").read_bytes() == b"working rules\r\n"


def test_missing_required_root_and_existing_destination_fail(source, tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError):
        source_fixture.copy_source_fixture(source, destination)
    (source / "PROJECT_RULES.md").unlink()
    with pytest.raises(FileNotFoundError):
        source_fixture.copy_source_fixture(source, tmp_path / "missing")


def test_destination_cannot_recurse_into_source(source):
    with pytest.raises(ValueError, match="outside"):
        source_fixture.copy_source_fixture(source, source / "scripts/recursive")


def test_copy_errors_are_not_hidden(source, tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("synthetic unreadable source")
    monkeypatch.setattr(source_fixture.shutil, "copytree", denied)
    with pytest.raises(PermissionError, match="unreadable"):
        source_fixture.copy_source_fixture(source, tmp_path / "denied")


def test_source_links_fail_but_dependency_links_are_not_followed(source, tmp_path):
    outside = write(tmp_path, "outside/private.txt")
    try:
        (source / "scripts/node_modules").symlink_to(outside.parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable on this platform")
    replica = source_fixture.copy_source_fixture(source, tmp_path / "replica")
    assert not (replica / "scripts/node_modules").exists()
    (source / "skills/linked-method.md").symlink_to(outside)
    with pytest.raises(ValueError, match="symlinks"):
        source_fixture.copy_source_fixture(source, tmp_path / "unsafe")


def test_real_source_replica_preserves_bundle_and_collected_contracts(tmp_path, monkeypatch):
    from scripts import stage_context_cli as cli
    from scripts.core.knowledge_runtime import verify_bundle
    original = Path(__file__).resolve().parents[2]
    documents = cli._local_standard_documents(original / "skills/storyboard-planner")
    expected = {path.relative_to(original): path.read_bytes() for path in documents}
    replica = source_fixture.copy_source_fixture(original, tmp_path / "repo")
    monkeypatch.setattr(cli, "ROOT", replica)
    actual = cli._local_standard_documents(replica / "skills/storyboard-planner")
    assert {path.relative_to(replica): path.read_bytes() for path in actual} == expected
    assert verify_bundle(replica / "knowledge/runtime") == verify_bundle(original / "knowledge/runtime")
    method = replica / "skills/storyboard-planner/references/visual-conception.md"
    method.unlink()
    with pytest.raises(cli.StageContextError, match="缺失"):
        cli._local_standard_documents(replica / "skills/storyboard-planner")
