"""Read-only structured dependency and final-file checks; no approval or export."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from .artifacts import project_path, load_manifest
from .media_types import LOCAL_REFERENCE_EXTENSIONS

# Conservative transfer budget for this workflow, not a universal platform limit.
DEFAULT_MAX_PACKAGE_BYTES = 19_000_000
PATH_KEYS = {'path', 'paths', 'path_or_url', 'file', 'files', 'localPath',
             'human_readable_path', 'image_path', 'video_path', 'audio_path',
             'source_image', 'source_video', 'source_audio', 'reference_images', 'reference_videos'}
REFERENCE_KEYS = {'reference', 'references', 'image', 'images'}
FILE_EXTENSION = re.compile('(?:' + '|'.join(re.escape(ext) for ext in sorted(LOCAL_REFERENCE_EXTENSIONS)) + ')$', re.I)

def validate_required_files(project: Path, files: list[str], input_roots: list[Path] = ()) -> list[str]:
    """Read-only readiness for explicitly selected inputs, not content/approval."""
    errors = []
    try:
        project = project.resolve()
        allowed_roots = [project, *(Path(root).resolve() for root in input_roots)]
    except (OSError, ValueError, RuntimeError) as exc:
        return [f'输入根目录无法解析：{exc}']
    for raw in files:
        try:
            if not raw.strip():
                raise ValueError('必需文件路径不能为空')
            path = project_path(project, raw)
            # External absolute inputs require an explicitly allowed root.
            # Relative inputs always stay inside this project, including symlinks.
            absolute = Path(raw).is_absolute() or bool(re.match(r'^[A-Za-z]:[/\\]', raw))
            if not absolute and not path.is_relative_to(project):
                raise ValueError('相对必需文件路径越出项目')
            if not any(path.is_relative_to(root) for root in allowed_roots):
                raise ValueError('必需文件越出项目及显式 input-root 范围')
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError('必需文件缺失、不是文件或为空')
            with path.open('rb') as stream:
                stream.read(1)
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f'{raw}: {exc}')
    return errors


def discover_dependencies(project: Path, files=(), input_roots=(), *, payload=None, verify_hashes=False):
    """Follow selected JSON path fields and exact manifest IDs, never scan prose.

    Paths are project-relative, as in artifact manifests; external absolute paths
    need explicit roots. Only selected reachable JSON is read (cycles deduped).
    Other document formats are checked as bytes, not parsed for more references.
    """
    project = Path(project).resolve()
    report = {'ok': False, 'checked_files': [], 'errors': [], 'uncheckable': [],
              'coverage': 'Selected JSON path fields, artifact_id and depends_on; no prose or remote fetch'}
    manifest = None
    testing = None
    seen_files, seen_ids = set(), set()
    hash_checked = set()

    def error(location, message):
        report['errors'].append(f'{location}: {message}')

    def entries():
        nonlocal manifest, testing
        if manifest is None:
            manifest = load_manifest(project)
            from .candidate_test import context
            testing = context(project)
            if testing:
                report.update(production_blocked=True, review_status='unreviewed')
        return manifest['artifacts']

    def artifact(ident, location, logical=False):
        if not isinstance(ident, str) or not ident.strip():
            error(location, 'missing artifact dependency ID')
            return
        key = (ident, logical)
        if key in seen_ids:
            return
        seen_ids.add(key)
        available = entries()
        matches = [a for a in available if
                   a.get('logical_id' if logical else 'artifact_id') == ident
                   and (not logical or (a.get('artifact_id') in testing['input_artifact_ids']
                                        if testing else a.get('status') == 'current'))]
        if len(matches) != 1:
            error(location, f'missing/ambiguous current or selected manifest dependency: {ident}')
            return
        walk(matches[0], location + '/' + ident, registered=True)

    def file(raw, location, *, resolve_manifest=True):
        if not isinstance(raw, str):
            error(location, 'file reference must be a string')
            return
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', raw):
            report['uncheckable'].append(f'{location}: remote reference cannot be checked locally: {raw}')
            return
        errors = validate_required_files(project, [raw], input_roots)
        if errors:
            report['errors'].extend(f'{location}: {e}' for e in errors)
            return
        path = project_path(project, raw)
        if path in seen_files:
            return
        seen_files.add(path)
        report['checked_files'].append(str(path))
        # A selected file may omit its manifest identity inside its own JSON.
        # Resolve only exact registered paths, never filename/latest guesses.
        registered = []
        for entry in entries() if resolve_manifest else []:
            raw_path = entry.get('path_or_url')
            if not isinstance(raw_path, str) or '://' in raw_path:
                continue
            try:
                if project_path(project, raw_path) == path:
                    registered.append(entry)
            except (OSError, ValueError, RuntimeError):
                continue  # Unselected malformed entries are outside this scope.
        if len(registered) > 1:
            error(location, 'ambiguous manifest entries for selected file')
        elif registered:
            artifact(registered[0].get('artifact_id'), location)
        if path.suffix.lower() == '.json':
            try:
                walk(json.loads(path.read_text(encoding='utf-8-sig')), str(path))
            except (ValueError, OSError, UnicodeError, RecursionError) as exc:
                error(str(path), f'cannot inspect JSON dependencies: {exc}')

    def walk(value, location, *, registered=False):
        if isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f'{location}/{i}')
        elif isinstance(value, dict):
            if verify_hashes:
                # Only an explicit single-file identity carries this digest.
                # input_sha256/frozen_sha256 and plural paths are not file hashes.
                paths = [value[key] for key in ('path', 'path_or_url', 'file')
                         if isinstance(value.get(key), str) and value[key]]
                hashes = [value[key] for key in ('sha256', 'checksum') if key in value]
                if hashes and paths:
                    if len(set(paths)) != 1:
                        error(location, 'ambiguous file hash binding: multiple paths')
                    elif any(not isinstance(h, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', h) for h in hashes):
                        error(location, 'invalid declared file hash')
                    elif len({h.lower() for h in hashes}) != 1:
                        error(location, 'conflicting sha256/checksum declarations')
                    elif '://' not in paths[0] and not validate_required_files(project, [paths[0]], input_roots):
                        target = project_path(project, paths[0])
                        actual = hashlib.sha256(target.read_bytes()).hexdigest()
                        if actual != hashes[0].lower():
                            error(location, 'declared file hash differs from actual bytes: ' + paths[0])
                        else:
                            hash_checked.add(str(target))
            for key, child in value.items():
                here = f'{location}/{key}'
                if key in {'artifact_id', 'source_artifact_id'}:
                    artifact(child, here)
                elif key in {'artifact_ids', 'source_artifact_ids'}:
                    if not isinstance(child, list):
                        error(here, 'artifact_ids must be a list')
                    else:
                        for ident in child:
                            artifact(ident, here)
                elif key == 'depends_on':
                    if not isinstance(child, list):
                        error(here, 'depends_on must be a list')
                    else:
                        for ident in child:
                            artifact(ident, here, logical=True)
                elif key in PATH_KEYS or key.endswith(('_path', '_paths', '_file', '_files')):
                    # Empty optional paths in existing candidate records mean no
                    # selection. Explicit --require-file still rejects empty.
                    if child in ('', None):
                        continue
                    for raw in child if isinstance(child, list) else [child]:
                        if isinstance(raw, dict):
                            walk(raw, here)
                        else:
                            file(raw, here, resolve_manifest=not registered)
                elif key in REFERENCE_KEYS and isinstance(child, (str, list)):
                    for raw in child if isinstance(child, list) else [child]:
                        if isinstance(raw, str) and FILE_EXTENSION.search(urlsplit(raw).path if '://' in raw else raw):
                            file(raw, here)
                        elif isinstance(raw, (dict, list)):
                            walk(raw, here)
                else:
                    walk(child, here)
    try:
        for raw in files:
            file(str(raw), 'input')
        if payload is not None:
            walk(payload, 'delivery')
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
        error('dependencies', f'cannot inspect dependencies: {exc}')
    if verify_hashes:
        report['hash_checks'] = {'checked_files': sorted(hash_checked),
            'unhashed_files': sorted(set(report['checked_files']) - hash_checked),
            'scope': 'Existing unambiguous path/path_or_url/file + sha256/checksum only; unhashed bytes do not establish historical exact versions'}
    report['ok'] = not report['errors'] and not report['uncheckable']
    return report


def check_package_files(files, max_bytes=DEFAULT_MAX_PACKAGE_BYTES):
    """Read actual finished bytes (including archive overhead), never estimates."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError('max-package-bytes must be a positive integer')
    result = []
    for raw in files:
        path = Path(raw)
        if not path.is_file():
            raise ValueError(f'Package missing or not a file: {path}')
        digest = hashlib.sha256()
        size = 0
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        if size == 0 or size >= max_bytes:
            raise ValueError(f'Package {path}: actual {size} bytes; requires 0 < bytes < {max_bytes}. '
                             'Rebuild a smaller self-contained package or explicitly choose a verified channel budget; '
                             'preserve relative links and source files, then rerun this check before sending.')
        result.append({'path': str(path), 'bytes': size, 'sha256': digest.hexdigest(), 'max_bytes_exclusive': max_bytes})
    return result
