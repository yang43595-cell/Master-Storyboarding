"""Project-bound browser previews; derivative files never adopt production state."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import project
from .output_contract import file_sha256, require_empty_output


def read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise project.ProjectPrevisError(f'预览输入不是对象：{path}')
    return value


def output_path(root: Path, raw: str) -> Path:
    path = project.project_file(root, raw)
    parent = (root / project.PREVIS / '预览').resolve()
    if path.parent != parent or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', path.name):
        raise project.ProjectPrevisError('预览输出必须是项目 05_分镜/预演/预览 下的单独目录，名称使用字母、数字、下划线或连字符')
    require_empty_output(path)
    return path


def stage_inputs(root: Path, raw: str) -> tuple[dict, dict, list[dict], Path]:
    folder = project.project_file(root, raw)
    files = {role: project.project_file(root, str(folder / name)) for role, name in (
        ('scene', 'previs.blend'), ('recipe', 'resolved.recipe.json'),
        ('binding', 'shot-input.json'), ('staging', 'stage_manifest.json'))}
    # Capture each JSON once: its parsed value and recorded digest must describe
    # the same bytes. The large staged scene is hashed only once per boundary.
    payloads = {role: files[role].read_bytes() for role in ('binding', 'recipe', 'staging')}
    values = {role: json.loads(payload.decode('utf-8')) for role, payload in payloads.items()}
    if any(not isinstance(value, dict) for value in values.values()):
        raise project.ProjectPrevisError('逐镜预览输入必须是 JSON 对象')
    hashes = {role: hashlib.sha256(payload).hexdigest() for role, payload in payloads.items()}
    hashes['scene'] = file_sha256(files['scene'])
    binding, recipe, staging = (values[role] for role in ('binding', 'recipe', 'staging'))
    project.check_binding(root, binding)
    if staging.get('ok') is not True or staging.get('room_geometry_unchanged') is not True:
        raise project.ProjectPrevisError('逐镜布置未成功或基座几何已变化')
    for role, key in (('scene', 'staged_scene_sha256'), ('recipe', 'recipe_sha256'),
                      ('binding', 'binding_sha256')):
        if staging.get(key) != hashes[role]:
            raise project.ProjectPrevisError('逐镜布置缺少当前文件指纹或文件已变化；请在新目录重新 stage-shot：' + role)
    base_entry = project.project_file(root, binding['scene_base']['path'])
    base = read(base_entry)
    base_scene = next(item for item in base['files'] if item['role'] == 'scene')
    if staging['source_scene_sha256'] != base_scene['sha256']:
        raise project.ProjectPrevisError('逐镜布置与当前选用的空间基座不一致')
    shots = recipe.get('camera', {}).get('shots', [])
    if len(shots) != 1 or shots[0]['id'] != binding['shot']['shot_id']:
        raise project.ProjectPrevisError('预览配方必须且只能覆盖已准备的导演镜号')
    if float(shots[0]['start']) != 0 or abs(float(shots[0]['end']) - float(recipe['project']['duration_seconds'])) > 1e-6:
        raise project.ProjectPrevisError('逐镜预览使用从 0 秒开始的本镜完整时间')
    records = [{'role': role, 'path': path.relative_to(root).as_posix(), 'sha256': hashes[role]}
               for role, path in files.items()]
    return binding, recipe, records, files['scene']
