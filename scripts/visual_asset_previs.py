"""Read-only access to derived previews inside a connected production project."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path, PureWindowsPath
from urllib.parse import quote

if __package__:
    from .blender_previs.project import ProjectPrevisError, check_binding
else:
    from blender_previs.project import ProjectPrevisError, check_binding

PREVIEW_FOLDER = Path('05_分镜') / '预演' / '预览'


class StalePreview(ValueError):
    pass


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise ValueError('无效白模预览编号')
    return value


def project_root(library, project_id):
    project = library.projects.get(project_id)
    root = Path(project['path']).resolve()
    if not root.is_dir():
        raise ValueError('已连接项目目录不存在')
    return root


def contained(path, root):
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('白模预览路径越界')
    return resolved


def preview_root(root):
    return contained(root / PREVIEW_FOLDER, root)


def preview_directory(root, preview_id):
    folder = preview_root(root)
    return contained(folder / identifier(preview_id), folder)


def source_file(root, raw):
    if not isinstance(raw, str) or not raw or '\\' in raw:
        raise ValueError('预览来源必须使用项目相对路径')
    path = Path(raw)
    if path.is_absolute() or PureWindowsPath(raw).drive or any(p in {'.', '..'} for p in path.parts):
        raise ValueError('预览来源路径越界')
    return contained(root / path, root)


def json_bytes(payload):
    data = json.loads(payload.decode('utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('预览记录必须是 JSON 对象')
    return data


def read_json(path, root):
    return json_bytes(contained(path, root).read_bytes())


def number(value, positive=False):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and (not positive or value > 0)


def vector(value, size):
    return isinstance(value, list) and len(value) == size and all(number(v) for v in value)


def validate_manifest(data):
    if data.get('schema_version') != '1.0':
        raise ValueError('不支持的白模预览版本')
    identifier(data.get('shot_id')); identifier(data.get('revision'))
    if not number(data.get('fps'), positive=True) or not number(data.get('duration_seconds'), positive=True):
        raise ValueError('预览帧率或镜长无效')
    if not vector(data.get('resolution'), 2) or any(v <= 0 for v in data['resolution']):
        raise ValueError('预览画幅无效')
    bounds = data.get('bounds', {})
    if not isinstance(bounds, dict) or not vector(bounds.get('min'), 3) or not vector(bounds.get('max'), 3):
        raise ValueError('预览空间范围无效')
    frames = data.get('frames')
    if not isinstance(frames, list) or not frames:
        raise ValueError('预览缺少摄影机时间轴')
    previous = -1
    for frame in frames:
        if not isinstance(frame, dict) or not number(frame.get('time')) or not 0 <= frame['time'] <= data['duration_seconds'] + 1e-6:
            raise ValueError('预览时间点无效')
        if frame['time'] <= previous or not vector(frame.get('matrix_world'), 16) or not vector(frame.get('projection_matrix'), 16):
            raise ValueError('预览摄影机矩阵或顺序无效')
        previous = frame['time']
    if abs(frames[0]['time']) > 1e-6 or abs(frames[-1]['time'] - data['duration_seconds']) > 1e-6:
        raise ValueError('预览时间轴未覆盖完整镜长')
    actors = data.get('actors')
    if not isinstance(actors, list) or any(not isinstance(a, dict) or not all(isinstance(a.get(k), str) for k in ('id', 'label', 'color')) for a in actors):
        raise ValueError('预览人物说明无效')
    sources = data.get('sources')
    if not isinstance(sources, list) or not sources or sum(isinstance(s, dict) and s.get('role') == 'binding' for s in sources) != 1:
        raise ValueError('预览必须绑定本镜来源')
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get('role'), str):
            raise ValueError('预览来源说明无效')
        if not isinstance(source.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', source['sha256']):
            raise ValueError('预览来源指纹无效')
    glb = data.get('glb', {})
    if not isinstance(glb, dict) or glb.get('path') != 'preview.glb' or not isinstance(glb.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', glb['sha256']):
        raise ValueError('预览模型说明无效')


def verify_sources(root, data):
    binding = None
    for source in data['sources']:
        path = source_file(root, source.get('path'))
        # Parse the same binding bytes whose hash was checked, never reopen it.
        if source['role'] == 'binding':
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            binding = json_bytes(payload)
        else:
            digest = digest_file(path)
        if digest != source['sha256']:
            raise StalePreview('来源文件已变化或缺失，请重新导出预览')
    shot = binding.get('shot')
    if not isinstance(shot, dict):
        raise ValueError('预览镜号绑定无效')
    if shot.get('shot_id') != data['shot_id'] or binding.get('reference_revision') != data['revision']:
        raise StalePreview('预览与镜号绑定不一致，请重新导出预览')
    try:
        check_binding(root, binding)
    except (ProjectPrevisError, ValueError, KeyError, TypeError, OSError) as exc:
        raise StalePreview('导演或空间来源已变化，请重新准备本镜：' + str(exc)) from exc


def digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validated_model(root, folder, data):
    """Own the consumption boundary: validate and return this exact byte buffer.

    No cross-request cache. Read the model before checking current authority so
    changes/revocation during acquisition are checked before it can be returned.
    Replacing the path after acquisition never changes the validated response.
    """
    path = contained(folder / 'preview.glb', folder)
    payload = path.read_bytes()
    if contained(folder / 'preview.glb', folder) != path:
        raise StalePreview('预览模型在读取时变化，请重新导出预览')
    if hashlib.sha256(payload).hexdigest() != data['glb']['sha256']:
        raise StalePreview('预览模型已变化或缺失，请重新导出预览')
    verify_sources(root, data)
    return payload


def inspect_preview(root, preview_id, *, consume=True):
    """Explicit full inspection, or metadata-only pending detail for the UI."""
    data = None
    try:
        folder = preview_directory(root, preview_id)
        if (folder / 'export_error.json').exists():
            failure = read_json(folder / 'export_error.json', folder)
            return {'manifest': None, 'status': 'error', 'error': str(failure.get('error') or '预览导出失败'),
                    'shot_id': failure.get('shot_id'), 'revision': failure.get('revision')}
        payload = contained(folder / 'preview_manifest.json', folder).read_bytes()
        data = json_bytes(payload)
        validate_manifest(data)
        # Pending means unverified content, never unbounded paths.
        for source in data['sources']:
            source_file(root, source.get('path'))
        contained(folder / 'preview.glb', folder)
        if consume:
            validated_model(root, folder, data)
        return {'manifest': data, 'manifest_sha256': hashlib.sha256(payload).hexdigest(),
                'status': 'ready' if consume else 'pending', 'error': ''}
    except StalePreview as exc:
        return {'manifest': data, 'status': 'stale', 'error': str(exc)}
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {'manifest': data, 'status': 'error', 'error': str(exc)}


def list_previews(library, project_id):
    root = project_root(library, project_id)
    folder = preview_root(root)
    if not folder.exists():
        return {'previews': []}
    previews = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name):
        if not path.is_dir():
            continue
        # Existing manifests are the index; no new authoritative/cache file.
        # Only metadata is read. Sources, GLB and full frame validation belong to
        # selected detail/model consumption, so this must never claim ready.
        record = {'id': path.name, 'shot_id': None, 'revision': None,
                  'status': 'pending', 'error': '', 'duration_seconds': None}
        try:
            directory = preview_directory(root, path.name)
            failure = directory / 'export_error.json'
            if failure.exists():
                data = read_json(failure, directory)
                record.update(status='error', error=str(data.get('error') or '预览导出失败'),
                              shot_id=data.get('shot_id'), revision=data.get('revision'))
            else:
                data = read_json(directory / 'preview_manifest.json', directory)
                identifier(data.get('shot_id')); identifier(data.get('revision'))
                if not number(data.get('duration_seconds'), positive=True):
                    raise ValueError('预览镜长无效')
                record.update(shot_id=data['shot_id'], revision=data['revision'],
                              duration_seconds=data['duration_seconds'])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            record.update(status='error', error=str(exc))
        previews.append(record)
    return {'previews': previews}


def detail(library, project_id, preview_id):
    root = project_root(library, project_id)
    identifier(preview_id)
    result = inspect_preview(root, preview_id, consume=False)
    result.pop('shot_id', None); result.pop('revision', None)
    if result['status'] == 'pending':
        result['glb_url'] = (f'/previs-media/{quote(project_id, safe="")}/{quote(preview_id, safe="")}/preview.glb'
                             f'?manifest_sha256={result["manifest_sha256"]}')
    return result


def model_bytes(library, project_id, preview_id, manifest_sha256=None):
    root = project_root(library, project_id)
    result = inspect_preview(root, preview_id, consume=False)
    if result['status'] != 'pending':
        raise ValueError(result['error'])
    if manifest_sha256 is not None and manifest_sha256 != result['manifest_sha256']:
        raise StalePreview('预览记录在载入时变化，请刷新后重新选择镜头')
    return validated_model(root, preview_directory(root, preview_id), result['manifest'])
