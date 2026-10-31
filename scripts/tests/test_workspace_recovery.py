"""Synthetic source recovery boundaries; no business files or platform requests."""
import subprocess
from pathlib import Path
import pytest
from scripts.verify_workspace import source_tree


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args]).decode().strip()


def source(root):
    root.mkdir()
    git(root, 'init', '-q')
    (root / 'code.py').write_text('VALUE = 1\n')
    git(root, 'add', 'code.py')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid',
        'commit', '-qm', 'synthetic source')
    return git(root, 'rev-parse', 'HEAD^{tree}')


def test_exact_tree_recovery_to_new_directory_and_repeat(tmp_path):
    root = tmp_path / 'original'
    tree = source(root)
    bundle = tmp_path / 'source.bundle'
    git(root, 'bundle', 'create', str(bundle), '--all')
    restored = tmp_path / 'restored'
    subprocess.run(['git', 'clone', '-q', str(bundle), str(restored)], check=True)
    for _ in range(2):
        report = source_tree(restored, tree)
        assert report['ok'] and report['files_checked'] == 1
    before = (restored / 'code.py').read_bytes()
    retry = subprocess.run(['git', 'clone', '-q', str(bundle), str(restored)], capture_output=True)
    assert retry.returncode != 0 and (restored / 'code.py').read_bytes() == before


@pytest.mark.parametrize('fault', ['missing', 'changed', 'symlink'])
def test_wrong_or_missing_source_fails_closed(tmp_path, fault):
    root = tmp_path / 'root'
    tree = source(root)
    path = root / 'code.py'
    if fault == 'changed':
        path.write_text('VALUE = 2\n')
    else:
        path.unlink()
        if fault == 'symlink':
            outside = tmp_path / 'outside.py'
            outside.write_text('VALUE = 1\n')
            path.symlink_to(outside)
    assert not source_tree(root, tree)['ok']


def test_commit_or_branch_not_accepted_as_exact_tree(tmp_path):
    root = tmp_path / 'root'
    source(root)
    for value in ('HEAD', git(root, 'rev-parse', 'HEAD')):
        with pytest.raises(ValueError):
            source_tree(root, value)


def test_extra_unregistered_entry_cannot_verify_old_tree(tmp_path, monkeypatch):
    from scripts import verify_workspace as verifier
    root = tmp_path / 'root'
    tree = source(root)
    entry = root / 'verify_extra.py'
    entry.write_text('# not included in the expected tree\n')
    monkeypatch.setattr(verifier, '__file__', str(entry))
    with pytest.raises(ValueError, match='entry is not in'):
        verifier.verify(root, tree)


@pytest.mark.parametrize('mode', ['correct', 'wrong', 'invalid', 'conflicting', 'hashless', 'multiple'])
def test_recovery_checks_only_declared_single_file_hashes(tmp_path, mode):
    import hashlib, json
    from scripts.core.delivery_files import discover_dependencies
    target = tmp_path / 'reference with spaces.png'
    target.write_bytes(b'synthetic reference bytes, not actual media')
    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    ref = {'path': target.name, 'sha256': sha}
    if mode == 'wrong': ref['sha256'] = '0' * 64
    elif mode == 'invalid': ref['sha256'] = False
    elif mode == 'conflicting': ref['checksum'] = '1' * 64
    elif mode == 'hashless': ref = {'path': target.name, 'input_sha256': 'not-a-file-hash'}
    elif mode == 'multiple': ref['file'] = 'different.png'
    manifest = tmp_path / 'selected.json'
    manifest.write_text(json.dumps(ref))
    report = discover_dependencies(tmp_path, ['selected.json'], verify_hashes=True)
    assert report['ok'] == (mode in {'correct', 'hashless'})
    assert str(manifest) in report['hash_checks']['unhashed_files']
    if mode == 'correct': assert str(target) in report['hash_checks']['checked_files']
    if mode == 'hashless': assert str(target) in report['hash_checks']['unhashed_files']
    if mode == 'wrong':
        assert discover_dependencies(tmp_path, ['selected.json'])['ok']  # Original existence-only route remains unchanged.


def test_hash_recovery_respects_explicit_path_mapping_and_input_root(tmp_path, monkeypatch):
    import hashlib, json
    from scripts.core.delivery_files import discover_dependencies
    project = tmp_path / 'project'; project.mkdir()
    external = tmp_path / 'external'; external.mkdir()
    target = external / 'input.json'; target.write_text('{"original":true}')
    monkeypatch.setenv('MSB_PROJECT_PATH_MAP', json.dumps({'C:/old/input': str(external)}))
    payload = {'path': 'C:/old/input/input.json', 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    good = discover_dependencies(project, input_roots=[external], payload=payload, verify_hashes=True)
    assert good['ok'] and good['hash_checks']['checked_files'] == [str(target)]
    assert not discover_dependencies(project, payload=payload, verify_hashes=True)['ok']
