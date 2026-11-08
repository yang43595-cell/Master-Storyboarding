"""Read-only structured dependency and final-file checks; no approval or export."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from .artifacts import project_path, load_manifest
from .io import sha256_file
from .media_types import LOCAL_REFERENCE_EXTENSIONS
from .quality_evidence import IMAGE_ASPECTS

# Conservative transfer budget for this workflow, not a universal platform limit.
DEFAULT_MAX_PACKAGE_BYTES = 19_000_000
PATH_KEYS = {'path', 'paths', 'path_or_url', 'file', 'files', 'localPath',
             'human_readable_path', 'image_path', 'video_path', 'audio_path',
             'source_image', 'source_video', 'source_audio', 'reference_images', 'reference_videos'}
REFERENCE_KEYS = {'reference', 'references', 'image', 'images'}
FILE_EXTENSION = re.compile('(?:' + '|'.join(re.escape(ext) for ext in sorted(LOCAL_REFERENCE_EXTENSIONS)) + ')$', re.I)

def _local_reference_path(project: Path, raw: str) -> Path:
    """Resolve a PDF page locator to its file, preserving literal existing names."""
    literal = project_path(project, raw)
    if literal.is_file():
        return literal
    page = re.fullmatch(r'(.+\.pdf)#page=([1-9][0-9]*)', raw, re.I)
    return project_path(project, page[1]) if page else literal

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
            path = _local_reference_path(project, raw)
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


def discover_dependencies(project: Path, files=(), input_roots=(), *, payload=None, verify_hashes=False,
                          json_projections=None):
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
    expanded_files = set()
    manifest_matches, binding_hashes = {}, {}
    hash_checked = set()
    # Internal consumers may select an exact node from a historical snapshot.
    # The original bytes remain checked; never expose projections as CLI input.
    projections = json_projections or {}

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

    def entries_for_path(path):
        if path not in manifest_matches:
            matches = []
            for entry in entries():
                raw_path = entry.get('path_or_url')
                if not isinstance(raw_path, str) or '://' in raw_path:
                    continue
                try:
                    if project_path(project, raw_path) == path:
                        matches.append(entry)
                except (OSError, ValueError, RuntimeError):
                    continue  # Unselected malformed entries are outside this scope.
            manifest_matches[path] = matches
        return manifest_matches[path]

    def artifact(ident, location, logical=False, source=None):
        if not isinstance(ident, str) or not ident.strip():
            error(location, 'missing artifact dependency ID')
            return
        binding = (source['artifact_id'], source['revision'], source['path']) if source is not None else None
        key = (ident, logical, binding)
        if key in seen_ids:
            return
        seen_ids.add(key)
        available = entries()
        matches = [a for a in available if
                   a.get('logical_id' if logical else 'artifact_id') == ident
                   and (not logical or (a.get('artifact_id') in testing['input_artifact_ids']
                                        if testing else a.get('status') == 'current'))]
        if source is not None and testing:
            matches = [a for a in matches if a.get('artifact_id') == source['artifact_id']
                       and a.get('revision') == source['revision']
                       and project_path(project, a['path_or_url']) == project_path(project, source['path'])]
        if len(matches) != 1:
            error(location, f'missing/ambiguous current or selected manifest dependency: {ident}')
            return
        walk(matches[0], location + '/' + ident, registered=True)

    def file(raw, location, *, resolve_manifest=True, follow_json=True,
             bound_artifact_id=None, bound_sha256=None):
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
        path = _local_reference_path(project, raw)
        if path not in seen_files:
            seen_files.add(path)
            report['checked_files'].append(str(path))
        # Cache path candidates, but check each reference's own identity.
        registered = entries_for_path(path) if resolve_manifest else []
        if (len(registered) > 1 and isinstance(bound_artifact_id, str)
                and isinstance(bound_sha256, str)
                and re.fullmatch(r'[0-9a-fA-F]{64}', bound_sha256)):
            # Reusing unchanged bytes may give several historical registrations
            # the same path. An explicit ID + matching registered/actual digest
            # selects one identity; a bare path remains ambiguous.
            exact = [entry for entry in registered
                     if entry.get('artifact_id') == bound_artifact_id
                     and isinstance(entry.get('checksum'), str)
                     and entry.get('checksum', '').lower() == bound_sha256.lower()]
            if len(exact) == 1:
                if path not in binding_hashes:
                    binding_hashes[path] = sha256_file(path)
                if binding_hashes[path] == bound_sha256.lower():
                    registered = exact
        if len(registered) > 1:
            error(location, 'ambiguous manifest entries for selected file')
        elif registered:
            artifact(registered[0].get('artifact_id'), location)
        if path.suffix.lower() == '.json':
            if path in expanded_files:
                return
            try:
                value = json.loads(path.read_text(encoding='utf-8-sig'))
                if not follow_json:
                    # A QA source is a byte-bound observation source. A plan
                    # inside it may still name failed/future outputs; these are
                    # not the current delivery's selected media. Validate the
                    # original JSON, but do not promote its contents to inputs.
                    return
                expanded_files.add(path)
                if str(path) in projections:
                    expected, selected = projections[str(path)]
                    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                        raise ValueError('selected snapshot bytes changed')
                    value = selected
                walk(value, str(path))
            except (ValueError, OSError, UnicodeError, RecursionError) as exc:
                error(str(path), f'cannot inspect JSON dependencies: {exc}')

    def walk(value, location, *, registered=False):
        if isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f'{location}/{i}')
        elif isinstance(value, dict):
            change_pointer = (re.search(r'/diff/[0-9]+$', location) is not None
                              and {'path', 'before', 'after'}.issubset(value)
                              and isinstance(value['path'], str)
                              and re.fullmatch(r'(?:/(?:[^~/]|~[01])*)+', value['path']) is not None)
            if verify_hashes:
                # Only an explicit single-file identity carries this digest.
                # input_sha256/frozen_sha256 and plural paths are not file hashes.
                paths = [value[key] for key in ('path', 'path_or_url', 'file')
                         if not (change_pointer and key == 'path')
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
                        target = _local_reference_path(project, paths[0])
                        actual = hashlib.sha256(target.read_bytes()).hexdigest()
                        if actual != hashes[0].lower():
                            error(location, 'declared file hash differs from actual bytes: ' + paths[0])
                        else:
                            hash_checked.add(str(target))
            for key, child in value.items():
                here = f'{location}/{key}'
                if key == 'path' and change_pointer:
                    # A structured JSON change locator is not a filesystem path.
                    # before/after still recurse, including their real file refs.
                    continue
                elif key in {'artifact_id', 'source_artifact_id'}:
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
                        package_sources = {}
                        if registered and testing:
                            raw = value.get('path_or_url')
                            if isinstance(raw, str) and not validate_required_files(project, [raw], input_roots):
                                path = _local_reference_path(project, raw)
                                if path.suffix.lower() == '.json':
                                    package_sources = json.loads(path.read_text(encoding='utf-8-sig'))
                                    if not isinstance(package_sources, dict):
                                        package_sources = {}
                        for ident in child:
                            source_key = {'narrative.package': 'source_narrative',
                                          'storyboard.package': 'source_storyboard'}.get(ident) if isinstance(ident, str) else None
                            source = package_sources.get(source_key)
                            if (not isinstance(source, dict) or
                                    not all(isinstance(source.get(k), str) and source[k].strip()
                                            for k in ('artifact_id', 'revision', 'path'))):
                                source = None
                            artifact(ident, here, logical=True, source=source)
                elif key == 'project_path':
                    # Context roots are directories; exact scope is checked by
                    # the consuming authority, not treated as a required file.
                    continue
                elif key == 'previous_quality_evidence_records':
                    # Append-only superseded observations are retained for
                    # audit, not re-used as current image admission evidence.
                    # The canonical quality_evidence and all selected files
                    # still take the ordinary current checks below.
                    continue
                elif (key == 'source_prompt' and isinstance(child, dict)
                      and set(child) == {'path', 'sha256', 'segment_id', 'material_order'}
                      and isinstance(child['path'], str) and isinstance(child['sha256'], str)
                      and Path(child['path']).suffix.lower() == '.json'
                      and re.fullmatch(r'[0-9a-fA-F]{64}', child['sha256'])
                      and isinstance(child['segment_id'], str)
                      and type(child['material_order']) is int and child['material_order'] > 0
                      and location.endswith('/quality_evidence')):
                    # This prior observation names a historical prompt and slot.
                    # Its old active inputs are not this draft's active inputs.
                    file(child['path'], here, resolve_manifest=False, follow_json=False)
                    if not validate_required_files(project, [child['path']], input_roots):
                        prior = _local_reference_path(project, child['path'])
                        if hashlib.sha256(prior.read_bytes()).hexdigest() != child['sha256'].lower():
                            error(here, 'historical source prompt hash differs from actual bytes')
                        elif verify_hashes:
                            hash_checked.add(str(prior))
                elif key == 'professional_method_versions' and 'professional_methods_root' in value:
                    root = value['professional_methods_root']
                    if not isinstance(root, str) or not Path(root).is_absolute() or not isinstance(child, list):
                        error(here, 'professional method versions require an absolute recorded root and a list')
                        continue
                    root = Path(root).resolve()
                    for i, method in enumerate(child):
                        where = f'{here}/{i}'
                        if not isinstance(method, dict) or not isinstance(method.get('path'), str) or not method['path'].strip():
                            error(where, 'professional method version requires a file path')
                            continue
                        path = (root / method['path']).resolve()
                        if not path.is_relative_to(root):
                            error(where, 'professional method path escapes its recorded root')
                            continue
                        walk({**method, 'path': str(path)}, where)
                elif (key == 'source_files'
                      and isinstance(value.get('image_sha256'), str)
                      and re.fullmatch(r'[0-9a-fA-F]{64}', value['image_sha256'])
                      and isinstance(value.get('checks'), dict)
                      and set(value['checks']) == set(IMAGE_ASPECTS)
                      and value.get('status') in {'pass', 'fail', 'unknown'}
                      and isinstance(value.get('view'), dict)
                      and value['view'].get('resolution') == 'original'
                      and isinstance(value.get('uses'), list) and value['uses']
                      and isinstance(value.get('observer'), str) and value['observer'].strip()
                      and isinstance(value.get('checked_at'), str) and value['checked_at'].strip()):
                    # This is the existing image-QA contract, not a generic
                    # escape hatch for arbitrary source_files collections.
                    if not isinstance(child, list) or not child:
                        error(here, 'image QA source_files must be a nonempty list')
                        continue
                    for i, source in enumerate(child):
                        where = f'{here}/{i}'
                        if (not isinstance(source, dict) or set(source) != {'path', 'sha256'}
                                or not isinstance(source['path'], str)
                                or not isinstance(source['sha256'], str)
                                or not re.fullmatch(r'[0-9a-fA-F]{64}', source['sha256'])):
                            error(where, 'image QA source must bind an exact path and SHA256')
                            continue
                        file(source['path'], where, resolve_manifest=False, follow_json=False)
                        if verify_hashes and not validate_required_files(project, [source['path']], input_roots):
                            source_path = _local_reference_path(project, source['path'])
                            if hashlib.sha256(source_path.read_bytes()).hexdigest() != source['sha256'].lower():
                                error(where, 'image QA source hash differs from actual bytes')
                            else:
                                hash_checked.add(str(source_path))
                elif key in PATH_KEYS or key.endswith(('_path', '_paths', '_file', '_files')):
                    # Empty optional paths in existing candidate records mean no
                    # selection. Explicit --require-file still rejects empty.
                    if child in ('', None):
                        continue
                    for raw in child if isinstance(child, list) else [child]:
                        if isinstance(raw, dict):
                            walk(raw, here)
                        else:
                            file(raw, here, resolve_manifest=not registered,
                                 bound_artifact_id=value.get('artifact_id'),
                                 bound_sha256=value.get('sha256') or value.get('checksum'))
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
