"""Source snapshots stay small without hiding source drift or copy failures."""
from pathlib import Path
import stat

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
        "references/synthetic-fixture.json": b'{"synthetic": true}\r\n',
        "scripts/.env.example": b"EXAMPLE=placeholder\n",
        "config/requirements.in": b"jsonschema>=4.26,<4.27\n",
    }
    for relative, data in required.items():
        write(source, relative, data)
    for directory in (".git", ".venv", "venv", "node_modules", "inputs", "assets",
                      "regression_evidence", ".repair-evidence", ".arbitrary-local-venv"):
        write(source, f"{directory}/do-not-copy")
    for directory in (".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
                      ".blender-previs-cache", ".master-storyboarding", ".tmp_session"):
        write(source, f"scripts/{directory}/do-not-copy")
    for name in (".env", ".env.local", "cached.pyc", ".DS_Store"):
        write(source, "skills/" + name)
    # Sparse dependency data reproduces amplification without consuming RAM/disk.
    with (source / ".venv/large-dependency").open("wb") as handle:
        handle.truncate(512 * 1024 * 1024)
    replica = source_fixture.copy_source_fixture(source, tmp_path / "replica")
    copied = {p.relative_to(replica).as_posix(): p.read_bytes()
              for p in replica.rglob("*") if p.is_file()}
    assert copied == required


def test_nested_credentials_media_and_project_state_are_never_copied(source, tmp_path):
    excluded = (
        "config/credentials-synthetic.json", "config/local.env", "config/synthetic.key",
        "config/private.pem", "config/certificate.p12", "config/certificate.pfx",
        "config/.git-credentials", "scripts/.aws/credentials", "scripts/.ssh/id_rsa",
        "scripts/.claude/settings.local.json", "skills/inputs/story.md",
        "skills/history/old.json", "references/evidence_sources/source.txt",
        "references/product-references/notes.md", "references/private-transcript.txt",
        "knowledge/runtime/.master-storyboarding/local.json",
        "config/CREDENTIALS-UPPER.JSON", "config/LOCAL.ENV",
    )
    for relative in excluded:
        write(source, relative)
    media = ("mp4", "mov", "mkv", "avi", "wav", "mp3", "blend", "glb", "gltf",
             "png", "jpg", "jpeg", "webp", "gif", "MP4")
    for suffix in media:
        write(source, "references/private-media." + suffix)
    with (source / "references/private-media.mp4").open("wb") as handle:
        handle.truncate(8 * 1024 * 1024)
    write(source, "config/schema.json", b'{"type":"object"}')
    write(source, "scripts/.env.example", b"SYNTHETIC_EXAMPLE=placeholder\n")
    replica = source_fixture.copy_source_fixture(source, tmp_path / "safe")
    copied = {path.relative_to(replica).as_posix() for path in replica.rglob("*") if path.is_file()}
    assert copied == {"PROJECT_RULES.md", "config/schema.json", "scripts/.env.example"}


def test_unknown_source_type_fails_instead_of_disappearing(source, tmp_path):
    write(source, "references/new-required-fixture.bin", bytes(range(256)))
    with pytest.raises(ValueError, match="unsupported source fixture file"):
        source_fixture.copy_source_fixture(source, tmp_path / "unsupported")


def test_read_only_source_remains_read_only_and_replica_is_writable(source, tmp_path):
    original = write(source, "skills/read-only.md", b"original")
    original.chmod(0o444)
    (source / "skills").chmod(0o555)
    try:
        replica = source_fixture.copy_source_fixture(source, tmp_path / "writable")
        copied = replica / "skills/read-only.md"
        assert copied.stat().st_mode & stat.S_IWUSR
        assert (replica / "skills").stat().st_mode & stat.S_IWUSR
        copied.write_bytes(b"isolated edit")
        assert original.read_bytes() == b"original"
        assert not original.stat().st_mode & stat.S_IWUSR
        assert not (source / "skills").stat().st_mode & stat.S_IWUSR
    finally:
        (source / "skills").chmod(0o755)
        original.chmod(0o644)


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
