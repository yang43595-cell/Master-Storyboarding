"""Read-only recovery verification against an explicitly selected Git tree.

Restore with ordinary git clone/bundle first. This does not install dependencies,
rewrite project paths, select candidates, approve artifacts or contact a platform.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def source_tree(root: Path, expected_tree: str) -> dict:
    root = root.resolve()
    if not re.fullmatch(r'[0-9a-f]{40}', expected_tree):
        raise ValueError('expected-tree must be the exact previously verified Git tree SHA')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)
    if Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve() != root:
        raise ValueError('source root is not the selected repository root')
    if git('rev-parse', f'{expected_tree}^{{tree}}').decode().strip() != expected_tree:
        raise ValueError('expected-tree must name a tree, not a commit or branch')
    entries = {}
    modes = {}
    for raw in git('ls-tree', '-rz', expected_tree).decode().split('\0'):
        if not raw:
            continue
        metadata, name = raw.split('\t', 1)
        mode, kind, sha = metadata.split()
        if kind != 'blob' or mode not in {'100644', '100755'}:
            raise ValueError(f'unsupported source entry: {name} ({mode}/{kind})')
        entries[name] = sha
        modes[name] = mode
    if not entries:
        raise ValueError('source tree has no files; not a recovery pass')
    errors = []
    for name, expected in entries.items():
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            errors.append(f'missing or redirected source: {name}')
            continue
        # The byte proof is portable; executable mode is additionally checked on POSIX.
        tree_mode = modes[name]
        if os.name != 'nt' and bool(path.stat().st_mode & 0o111) != (tree_mode == '100755'):
            errors.append(f'source executable mode differs: {name}')
        data = path.read_bytes()
        actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if actual != expected:
            errors.append(f'source bytes differ: {name}')
    return {'ok': not errors, 'root': str(root), 'expected_tree': expected_tree,
            'files_checked': len(entries), 'errors': errors, '_blobs': entries}


def verify(root: Path, expected_tree: str, *, project: Path | None = None,
           files=(), input_roots=()) -> dict:
    result = {'ok': False, 'production_ready': False, 'scope': 'recovery checks only',
              'business_inputs': {'status': 'not_requested'},
              'platform_binding': 'not_checked', 'user_approval': 'not_checked'}
    source = source_tree(root, expected_tree)
    expected = source.pop('_blobs')
    result['source'] = source
    if not source['ok']:
        return result  # Do not import source already known to differ.
    entry = Path(__file__).resolve()
    if not entry.is_relative_to(root.resolve()):
        raise ValueError('verification entry is outside the selected source root')
    data = entry.read_bytes()
    entry_blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    if expected.get(entry.relative_to(root.resolve()).as_posix()) != entry_blob:
        raise ValueError('verification entry is not in the exact selected source tree')
    result['entry'] = {'path': str(entry), 'blob': entry_blob}
    from scripts.core import runtime_deps
    runtime_deps.resolve_runtime_deps.cache_clear()  # Recovery is a fresh check, not a cached startup result.
    runtime = runtime_deps.require_runtime_deps(root)
    versions = {}
    dependency_origins = {}
    for name in ('jsonschema', 'referencing'):
        module = importlib.import_module(name)
        origin = Path(module.__file__).resolve()
        if not origin.is_file():
            raise ValueError(f'loaded dependency origin is missing: {name}')
        if runtime.path and not origin.is_relative_to(Path(runtime.path).resolve()):
            raise ValueError(f'loaded dependency is outside selected vendor: {name}')
        versions[name] = importlib.metadata.version(name)
        dependency_origins[name] = str(origin)
    result['runtime'] = {'interpreter': sys.executable, 'python': sys.version.split()[0],
                         'dependencies': runtime.as_dict(), 'versions': versions,
                         'dependency_origins': dependency_origins,
                         'requirements_sha256': runtime_deps.requirements_digest(root),
                         'scope': 'actual imports and reported versions; run declared installation and full tests for compatibility'}
    # Import the actual consumption path before recording loaded source modules.
    delivery = importlib.import_module('scripts.core.delivery_files')
    if project is not None:
        project = project.resolve()
        if not project.is_dir() or not files:
            raise ValueError('business-input recovery requires an existing project and explicit --require-file selections')
        result['business_inputs'] = delivery.discover_dependencies(project, list(files), list(input_roots), verify_hashes=True)
        result['business_inputs']['status'] = 'checked'
    elif files or input_roots:
        raise ValueError('--require-file/--input-root require --project')
    loaded = []
    for name, module in list(sys.modules.items()):
        if not name.startswith(('scripts.', 'core.')) or not getattr(module, '__file__', None):
            continue
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError(f'loaded source module outside selected root: {name}')
        relative = path.relative_to(root.resolve()).as_posix()
        data = path.read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if expected.get(relative) != blob:
            raise ValueError(f'loaded source module not in exact selected tree: {name}')
        loaded.append({'module': name, 'path': str(path), 'blob': blob})
    result['runtime']['loaded_modules'] = sorted(loaded, key=lambda x: x['module'])
    result['ok'] = result['business_inputs'].get('ok', project is None)
    result['limitations'] = ('Only explicitly selected structured input dependencies are checked; no prose, '
        'visual inspection, confirmation, authorization or native platform readback is inferred.')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-tree', required=True)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--require-file', action='append', default=[])
    parser.add_argument('--input-root', type=Path, action='append', default=[])
    args = parser.parse_args(argv)
    try:
        result = verify(ROOT, args.expected_tree, project=args.project,
                        files=args.require_file, input_roots=args.input_root)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError, ImportError) as exc:
        result = {'ok': False, 'production_ready': False, 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
