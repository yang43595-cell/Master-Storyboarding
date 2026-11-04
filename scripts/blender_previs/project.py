"""Optional project registration for local Blender spatial references.

No renderer or stage transition is launched here. Director text stays authoritative;
numeric staging is authored against the captured shot rather than inferred from prose.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

if __package__ == 'scripts.blender_previs':
    from ..core import artifacts, revision_control
    from ..core.candidate_test import require_production
    from ..core.revision_control import guard_action
    from ..core.asset_refs import require_images, scope_assets
    from ..core.io import project_locked, read_json, sha256_file, utc_now, write_json, write_text
    from ..core.project_state import load_project_state, render_project_state_markdown
    from .output_contract import png_size
else:
    from core import artifacts, revision_control
    from core.candidate_test import require_production
    from core.revision_control import guard_action
    from core.asset_refs import require_images, scope_assets
    from core.io import project_locked, read_json, sha256_file, utc_now, write_json, write_text
    from core.project_state import load_project_state, render_project_state_markdown
    from blender_previs.output_contract import png_size


class ProjectPrevisError(ValueError):
    pass


PREVIS = Path('05_分镜') / '预演'


def identifier(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise ProjectPrevisError(f'编号只能含英文字母、数字、点、横线或下划线：{value}')
    return value


def project_file(root: Path, raw: str) -> Path:
    path = artifacts.project_path(root, raw)
    if not path.is_relative_to(root.resolve()):
        raise ProjectPrevisError(f'项目登记路径必须位于项目内：{raw}')
    return path


def empty_directory(path: Path) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ProjectPrevisError(f'输出目录必须为空：{path}')


def file_record(path: Path, root: Path, **extra) -> dict:
    if not path.is_file():
        raise ProjectPrevisError(f'文件不存在：{path}')
    return dict(path=path.relative_to(root).as_posix(), sha256=sha256_file(path), **extra)


def verify_file(root: Path, record: dict) -> Path:
    path = project_file(root, record['path'])
    if not path.is_file() or sha256_file(path) != record['sha256']:
        raise ProjectPrevisError(f'文件缺失或内容变化，需新修订：{path}')
    return path


def entry_by_id(root: Path, artifact_id: str) -> dict:
    matches = [a for a in artifacts.load_manifest(root)['artifacts']
               if a['artifact_id'] == artifact_id]
    if len(matches) != 1:
        raise ProjectPrevisError(f'产物编号不存在或不唯一：{artifact_id}')
    return matches[0]


def director_input(root: Path, shot_id: str) -> dict:
    state = load_project_state(root)
    entries = artifacts.load_manifest(root)['artifacts']
    matches = [a for a in entries if a.get('logical_id') == 'storyboard.package'
               and a['artifact_id'] in state['current_artifacts']
               and artifacts.artifact_is_confirmed(a, state, root)]
    if len(matches) != 1:
        raise ProjectPrevisError('必须有唯一当前、整稿确认的导演包')
    entry = matches[0]
    path = project_file(root, entry['path_or_url'])
    data = read_json(path)
    if data.get('revision') != entry['revision'] or data.get('status') != 'confirmed':
        raise ProjectPrevisError('导演包 revision/status 与登记不一致')
    if data.get('schema_version') != '4.0':
        raise ProjectPrevisError('新白模输入须为当前 v4 导演包；旧包只读')
    text_source = None
    sources = [(entry, path)]
    if data.get('human_readable_path'):
        text_path = project_file(root, data['human_readable_path'])
        plans = [a for a in entries if a.get('logical_id') == 'storyboard.plan'
                 and a.get('revision') == entry['revision']
                 and project_file(root, a['path_or_url']) == text_path
                 and a['artifact_id'] in state['current_artifacts']
                 and artifacts.artifact_is_confirmed(a, state, root)]
        if len(plans) != 1:
            raise ProjectPrevisError('同修订的确认导演正文未登记或已失效')
        sources.append((plans[0], text_path))
        text_source = dict(artifact_id=plans[0]['artifact_id'], **file_record(text_path, root))
    elif not entry.get('checksum'):
        raise ProjectPrevisError('确认导演 JSON 缺少登记指纹')
    for a, p in sources:
        if a.get('checksum') and sha256_file(p) != a['checksum']:
            raise ProjectPrevisError(f'确认导演产物内容变化：{p}')
    found = [(s, i, shot) for s in data['scenes'] for i, shot in enumerate(s['shots'])
             if shot['shot_id'] == shot_id]
    if len(found) != 1:
        raise ProjectPrevisError(f'镜号不存在或不唯一：{shot_id}')
    scene, i, shot = found[0]
    return {
        'source_storyboard': dict(artifact_id=entry['artifact_id'], revision=entry['revision'],
                                 **file_record(path, root)),
        'source_director_text': text_source,
        'scene': {k: v for k, v in scene.items() if k != 'shots'},
        'shot': shot,
        'adjacent_states': {
            'previous_end': scene['shots'][i - 1]['take']['end'] if i else None,
            'next_start': scene['shots'][i + 1]['take']['start'] if i + 1 < len(scene['shots']) else None,
        },
    }


def scene_input(root: Path, artifact_id: str) -> dict:
    entry = entry_by_id(root, artifact_id)
    if entry.get('type') != 'previs-scene' or entry.get('status') != 'current':
        raise ProjectPrevisError('空间基座必须为当前 previs-scene')
    path = project_file(root, entry['path_or_url'])
    if sha256_file(path) != entry['checksum']:
        raise ProjectPrevisError('空间基座登记内容变化')
    data = read_json(path)
    for f in data['files']:
        verify_file(root, f)
    refs = appearance_inputs(root, [r['ref_id'] for r in data['appearance_references']])
    if refs != data['appearance_references']:
        raise ProjectPrevisError('空间基座的确认参考已变化，须先核对布局版本')
    return dict(artifact_id=artifact_id, logical_id=entry['logical_id'],
                revision=entry['revision'], **file_record(path, root))


def check_binding(root: Path, binding: dict) -> None:
    actual = director_input(root, binding['shot']['shot_id'])
    if actual != {k: binding[k] for k in actual}:
        raise ProjectPrevisError('导演来源/正文/场景/镜头已变，重新准备并核对本镜')
    if scene_input(root, binding['scene_base']['artifact_id']) != binding['scene_base']:
        raise ProjectPrevisError('空间基座已变，重新核对本镜')


def backup_metadata(root: Path) -> None:
    """Preserve pre-write facts, never replace an earlier backup."""
    folder = root / '99_历史与废弃' / '白模接入前登记备份'
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('artifact_manifest.json', 'project_state.json', 'project_state.md'):
        source = root / '00_项目管理' / name
        target = folder / name
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)


@project_locked
def save_state_additions(root: Path, artifact_id: str, receipt: dict | None = None) -> None:
    path = root / '00_项目管理/project_state.json'
    # Keep unrelated fields verbatim, including legacy fields, and do not advance a stage.
    data = read_json(path)
    ids = data.setdefault('current_artifacts', [])
    if artifact_id not in ids:
        ids.append(artifact_id)
    if receipt is not None:
        data.setdefault('user_confirmations', []).append(receipt)
    write_json(path, data)
    write_text(root / '00_项目管理/project_state.md',
               render_project_state_markdown(load_project_state(root)))


def appearance_inputs(root: Path, requests: list[str]) -> list[dict]:
    if not requests or any('@' not in ref for ref in requests) or len(set(requests)) != len(requests):
        raise ProjectPrevisError('外观参考必须列唯一的 AST-ID@OUTPUT-ID')
    ok, issues = require_images(root, requests)
    entries, more = scope_assets(root, requests)
    if not ok or more:
        raise ProjectPrevisError('确认母图不可用：' + '; '.join(issues + more))
    result = []
    for ref, entry in zip(requests, entries):
        output = entry['outputs'][0]
        path = Path(output['path']).resolve()
        result.append(dict(ref_id=ref, confirmation_ref=output['confirmation_ref'],
                           **file_record(path, root)))
    return result


@project_locked
def register_scene(root: Path, scene_id: str, revision: str, blend: Path,
                   evidence: Path, references: list[str], recipe: Path | None = None) -> dict:
    identifier(scene_id); identifier(revision)
    refs = appearance_inputs(root, references)
    if blend.suffix.lower() != '.blend' or not blend.is_file() or not evidence.is_file():
        raise ProjectPrevisError('空间基座需要已有 .blend 和真实检查记录')
    if read_json(evidence).get('ok') is not True:
        raise ProjectPrevisError('空间基座的实测检查未通过')
    if recipe is not None and not recipe.is_file():
        raise ProjectPrevisError('空间基座配方不存在')
    folder = root / PREVIS / '空间' / f'{scene_id}_{revision}'
    empty_directory(folder)
    artifact_id = f'ART-PREVIS-SCENE-{scene_id}-{revision}'
    if any(a['artifact_id'] == artifact_id for a in artifacts.load_manifest(root)['artifacts']):
        raise ProjectPrevisError('该空间修订已登记')
    backup_metadata(root)
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for source, name, role in ((blend, 'previs.blend', 'scene'),
                               (evidence, 'source-checks.json', 'technical_evidence'),
                               (recipe, 'room.recipe.json', 'recipe')):
        if source is not None:
            target = folder / name
            shutil.copy2(source, target)
            files.append(file_record(target, root, role=role, imported_from=str(source.resolve())))
    path = folder / 'scene-reference.json'
    write_json(path, dict(scene_id=scene_id, revision=revision, files=files,
                         appearance_references=refs, created_at=utc_now(),
                         role='可复用空间基座；不证明逐镜验收或身份服装'))
    entry = artifacts.register_workspace_artifact(root, dict(
        artifact_id=artifact_id, logical_id=f'previs.scene.{scene_id}', type='previs-scene',
        path_or_url=path.relative_to(root).as_posix(), revision=revision, status='candidate',
        checksum=sha256_file(path), user_approved=False, depends_on=[]))
    manifest = artifacts.load_manifest(root)
    artifacts.mark_impacted_artifacts_stale(manifest, {entry['logical_id']}, '空间基座修订替代，逐镜参考须重新核对', revision)
    artifacts.save_manifest(root, manifest)
    artifacts.promote_candidate(root, entry['logical_id'], artifact_id)
    # A reusable technical base is explicitly selected by prepare-shot, never
    # injected into the global list of confirmed production inputs.
    return entry_by_id(root, artifact_id)


def prepare_shot(root: Path, shot_id: str, revision: str, scene_artifact: str) -> Path:
    identifier(shot_id); identifier(revision)
    data = director_input(root, shot_id)
    data['scene_base'] = scene_input(root, scene_artifact)
    folder = root / PREVIS / '输入' / f'{shot_id}_{revision}'
    empty_directory(folder)
    data.update(reference_revision=revision, numeric_staging='依据正文另写现有预演配方；不从文字自动猜机位、动作或尺度')
    path = folder / 'shot-input.json'
    write_json(path, data)
    return path


def render_runtime(spec: dict, evidence: dict, files: list[dict], shot_id: str) -> dict:
    """Keep local defaults; an explicit cloud run must carry native byte evidence."""
    location = spec.get('runtime_location', 'local')
    backend = str(spec['render_backend'])
    if location not in ('local', 'cloud'):
        raise ProjectPrevisError('runtime_location 只能为 local 或 cloud')
    if location == 'local':
        if not backend.startswith('local Blender'):
            raise ProjectPrevisError('默认只登记本机 Blender；已授权云执行须显式 runtime_location=cloud')
        return dict(runtime_location='local')
    version = str(evidence.get('blender_version', ''))
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?: LTS)?', version)
    if not match or int(match.group(1)) < 1:
        raise ProjectPrevisError('云白模须有原生 verify 的真实 Blender 版本证据')
    if 'Blender' not in backend or match.group(0).removesuffix(' LTS') not in backend or backend.startswith('local Blender'):
        raise ProjectPrevisError('云 render_backend 与实际 Blender 版本/执行位置不一致')
    by_role = {}
    for role in ('main_image', 'shot_scene', 'render_manifest'):
        matches = [Path(f['path']) for f in files if f['role'] == role]
        if len(matches) != 1 or not matches[0].is_file():
            raise ProjectPrevisError(f'云白模须有唯一真实 {role} 文件')
        by_role[role] = matches[0]
    scene_hash = sha256_file(by_role['shot_scene'])
    manifest_path = by_role['render_manifest']
    if (evidence.get('scene_sha256') != scene_hash
            or evidence.get('render_manifest_sha256') != sha256_file(manifest_path)):
        raise ProjectPrevisError('云白模场景/渲染清单与实际 verify 字节不一致')
    manifest = read_json(manifest_path)
    if evidence.get('shots_sha256') is not None and evidence['shots_sha256'] != manifest.get('shots_sha256'):
        raise ProjectPrevisError('原生验收与渲染清单镜头配方指纹不一致')
    shots = [shot for shot in manifest.get('shots', []) if shot.get('id') == shot_id]
    if len(shots) != 1:
        raise ProjectPrevisError('云白模渲染清单必须唯一绑定准确镜号：' + shot_id)
    if 'shot_ids' in evidence and shot_id not in evidence['shot_ids']:
        raise ProjectPrevisError('原生验收未覆盖准确镜号：' + shot_id)
    still_hashes = {f.get('sha256') for f in shots[0].get('stills', [])}
    if manifest.get('scene_sha256') != scene_hash or sha256_file(by_role['main_image']) not in still_hashes:
        raise ProjectPrevisError('云白模主图不属于已验收的同一场景渲染')
    return dict(runtime_location='cloud', blender_version=version)


@project_locked
def register_reference(root: Path, input_path: Path, spec_path: Path) -> dict:
    binding = read_json(input_path)
    check_binding(root, binding)
    spec = read_json(spec_path)
    shot_id = binding['shot']['shot_id']
    revision = identifier(binding['reference_revision'])
    refs = appearance_inputs(root, spec['appearance_refs'])
    allowed = set(binding['shot']['source_asset_requirement_ids'])
    if any(r['ref_id'].partition('@')[0] not in allowed for r in refs):
        raise ProjectPrevisError('外观参考不属于本镜已登记的 AST 范围')
    actors = spec['actors']
    if not isinstance(actors, list) or len({a['proxy_id'] for a in actors}) != len(actors):
        raise ProjectPrevisError('人物占位体映射须为列表且不可重复')
    colors = [a['display_color'] for a in actors]
    if len(set(colors)) != len(colors):
        raise ProjectPrevisError('人物占位颜色不能相同')
    for actor in actors:
        if actor['asset_id'] not in allowed or not actor.get('state') or not actor.get('scale_basis'):
            raise ProjectPrevisError('AST/关键状态/尺度依据必须对应当前导演镜头')
    for key in ('target_state', 'coverage', 'camera', 'limitations', 'render_backend'):
        if not spec.get(key):
            raise ProjectPrevisError(f'白模说明缺少 {key}')
    if spec['camera'].get('director_contract') != binding['shot']['camera']:
        raise ProjectPrevisError('摄影机说明必须保留原导演 camera 合同，不改拍法')
    evidence = read_json(Path(spec['technical_evidence']))
    if not (evidence.get('ok') is True or evidence.get('technical_checks_passed') is True):
        raise ProjectPrevisError('技术检查未通过，不登记白模参考')
    if evidence.get('source_package_sha256', binding['source_storyboard']['sha256']) != binding['source_storyboard']['sha256']:
        raise ProjectPrevisError('技术检查来源导演包不同')
    files = spec['files']
    runtime = render_runtime(spec, evidence, files, shot_id)
    if sum(f['role'] == 'main_image' for f in files) != 1:
        raise ProjectPrevisError('必须有且仅有一张 main_image')
    for f in files:
        identifier(f['role'])
        if not Path(f['path']).is_file():
            raise ProjectPrevisError(f"产物不存在：{f['path']}")
        if f['role'] == 'main_image' and Path(f['path']).suffix.lower() not in ('.png', '.jpg', '.jpeg'):
            raise ProjectPrevisError('主参考必须为图片')
        if f['role'] == 'main_image' and Path(f['path']).suffix.lower() == '.png':
            size = list(png_size(Path(f['path'])))
            if spec['camera'].get('resolution') != size:
                raise ProjectPrevisError('主图像素尺寸与摄影机说明不一致')
    folder = root / PREVIS / '镜头' / f'{shot_id}_{revision}'
    empty_directory(folder)
    artifact_id = f'ART-PREVIS-{shot_id}-{revision}'
    if any(a['artifact_id'] == artifact_id for a in artifacts.load_manifest(root)['artifacts']):
        raise ProjectPrevisError('该镜头修订已登记')
    backup_metadata(root)
    folder.mkdir(parents=True, exist_ok=True)
    registered = []
    for i, f in enumerate(files):
        source = Path(f['path']).resolve()
        suffix = source.suffix.lower()
        target = folder / f'{i:02d}_{identifier(f["role"])}{suffix}'
        shutil.copy2(source, target)
        registered.append(file_record(target, root, role=f['role'], imported_from=str(source)))
    target = folder / 'technical-evidence.json'
    shutil.copy2(spec['technical_evidence'], target)
    registered.append(file_record(target, root, role='technical_evidence'))
    path = folder / 'white-reference.json'
    data = dict(source=binding, revision=revision, shot_id=shot_id, files=registered,
                **runtime, appearance_references=refs, **{k: spec[k] for k in (
                    'actors', 'target_state', 'coverage', 'camera', 'limitations', 'render_backend')})
    write_json(path, data)
    return artifacts.register_workspace_artifact(root, dict(
        artifact_id=artifact_id, logical_id=f'previs.shot.{shot_id}', type='previs-reference',
        path_or_url=path.relative_to(root).as_posix(), revision=revision, checksum=sha256_file(path),
        depends_on=['storyboard.package', 'storyboard.plan', binding['scene_base']['logical_id']]))


def reference_data(root: Path, artifact_id: str) -> tuple[dict, dict]:
    entry = entry_by_id(root, artifact_id)
    if entry.get('type') != 'previs-reference':
        raise ProjectPrevisError('只能选择逐镜白模参考产物')
    path = project_file(root, entry['path_or_url'])
    if sha256_file(path) != entry['checksum']:
        raise ProjectPrevisError('白模参考登记内容变化')
    data = read_json(path)
    check_binding(root, data['source'])
    for f in data['files']:
        verify_file(root, f)
    if data.get('runtime_location') == 'cloud':
        evidence_file = next(f for f in data['files'] if f['role'] == 'technical_evidence')
        evidence = read_json(verify_file(root, evidence_file))
        runtime = render_runtime(data, evidence, [dict(f, path=str(project_file(root, f['path']))) for f in data['files']], data['source']['shot']['shot_id'])
        if runtime['blender_version'] != data.get('blender_version'):
            raise ProjectPrevisError('云白模版本登记与技术证据不一致')
    refs = appearance_inputs(root, [r['ref_id'] for r in data['appearance_references']])
    if refs != data['appearance_references']:
        raise ProjectPrevisError('确认外观参考已变化，重新核对')
    return entry, data


def confirm_reference(root: Path, artifact_id: str, approval_ref: str, user_quote: str) -> dict:
    """Adopt one exact receipt using the existing recoverable revision transaction.

    No current selection or downstream validity changes before durable intent.
    An interrupted intent rolls forward on retry; confirmations are never deleted.
    """
    if not approval_ref.strip() or not user_quote.strip():
        raise ProjectPrevisError('须填实际用户确认引用和原话；技术通过不等于用户确认')
    root = root.resolve()
    require_production(root)
    revision_control.recover_transactions(root)
    with revision_control.file_lock(root / revision_control.LOCK_RELATIVE):
        entry, data = reference_data(root, artifact_id)
        guard_action(root, logical_ids=[entry['logical_id']])
        receipt_path = project_file(root, entry['path_or_url']).parent / 'user-confirmation.json'
        receipt = dict(scope='artifact', scope_description='仅本逐镜白模参考，不含成图或其他镜头',
                       kind='previs-reference-confirmation', artifact_ids=[artifact_id], reference_shot_id=data['shot_id'],
                       revision=entry['revision'], approval_ref=approval_ref, user_quote=user_quote,
                       checksum=entry['checksum'], files=data['files'],
                       confirmation_ref=receipt_path.relative_to(root).as_posix())
        if receipt_path.exists():
            previous = read_json(receipt_path)
            if {k: v for k, v in previous.items() if k != 'at'} != receipt:
                raise ProjectPrevisError('已有确认记录不可重写：确认依据或准确版本冲突')
            receipt = previous
            if entry.get('status') == 'current':
                confirmed_reference_data(root, artifact_id)
                state = read_json(root / '00_项目管理/project_state.json')
                if state.get('user_confirmations', []).count(receipt) != 1:
                    raise ProjectPrevisError('确认状态不一致，须核查原事务，不重复写确认')
                return receipt
        else:
            receipt['at'] = utc_now()
        if entry.get('status') != 'candidate':
            raise ProjectPrevisError('只能采用当前候选；已确认版本不可重写')
        manifest = artifacts.load_manifest(root)
        artifacts.validate_candidate_submission(root, entry, manifest)
        active = [a for a in manifest['artifacts'] if a.get('logical_id') == entry['logical_id']
                  and a['artifact_id'] != artifact_id and a.get('status') in {'current', 'stale'}]
        if len(active) > 1:
            raise ProjectPrevisError('同一 logical_id 对应多个有效/待返工产物，不能整组覆盖')
        artifacts.mark_impacted_artifacts_stale(
            manifest, {entry['logical_id']}, '逐镜白模修订替代，下游参考须重新核对', entry['revision'])
        for old in active:
            old.update(status='superseded', superseded_at=utc_now())
        adopted = next(a for a in manifest['artifacts'] if a['artifact_id'] == artifact_id)
        adopted.update(status='current', user_approved=True, confirmation_ref=receipt['confirmation_ref'])
        manifest['updated_at'] = utc_now()
        state_path = root / '00_项目管理/project_state.json'
        state = read_json(state_path)
        # Preserve the effective selection, including legacy active_artifact_ids
        # and object-shaped IDs, before introducing the canonical field. Keep
        # all unrelated raw fields untouched; the transaction reconciles stale IDs.
        state['current_artifacts'] = list(load_project_state(root)['current_artifacts'])
        if artifact_id not in state['current_artifacts']:
            state['current_artifacts'].append(artifact_id)
        confirmations = state.setdefault('user_confirmations', [])
        if receipt not in confirmations:
            confirmations.append(receipt)
        backup_metadata(root)
        revision_control._commit_json_transaction_unlocked(root, {
            root / artifacts.MANIFEST_PATH: manifest,
            state_path: state,
            receipt_path: receipt,
        }, reason='confirm exact previs reference', preserve_state_extensions=True)
        return receipt


def confirmed_reference_data(root: Path, artifact_id: str) -> tuple[dict, dict]:
    """Read-only gate shared by handoff and downstream reference validation."""
    entry, data = reference_data(root, artifact_id)
    guard_action(root, logical_ids=[entry['logical_id']])
    state = load_project_state(root)
    if entry['artifact_id'] not in state['current_artifacts'] or not artifacts.artifact_is_confirmed(entry, state, root):
        raise ProjectPrevisError('白模未经用户确认或已失效，不交给下游')
    receipt = read_json(project_file(root, entry['confirmation_ref']))
    if receipt.get('checksum') != entry['checksum'] or receipt.get('files') != data['files']:
        raise ProjectPrevisError('用户确认未绑定当前白模文件')
    return entry, data


def handoff(root: Path, artifact_id: str, output: Path, media_roles: list[str] | None = None) -> Path:
    entry, data = confirmed_reference_data(root, artifact_id)
    output = project_file(root, str(output))
    empty_directory(output)
    media_suffixes = {'.png', '.jpg', '.jpeg', '.webp', '.mp4', '.mov', '.webm', '.mkv', '.avi'}
    media = [f for f in data['files'] if Path(f['path']).suffix.lower() in media_suffixes]
    # Unknown registered files may contribute to the source coverage (e.g. GIF).
    # Do not silently treat a filtered package as the complete source reference.
    unsupported = [f for f in data['files'] if Path(f['path']).suffix.lower()
                   not in media_suffixes | {'.blend', '.json'}]
    selected = media
    if media_roles is not None:
        if not media_roles or set(media_roles) - {f['role'] for f in media}:
            raise ProjectPrevisError('交接媒体 role 必须来自本参考已登记的图片或视频')
        selected = [f for f in media if f['role'] in media_roles]
    coverage = data['coverage'] if selected == media and not unsupported else (
        '仅所选媒体（' + '、'.join(dict.fromkeys(f['role'] for f in selected)) +
        '）的实际覆盖；完整源参考覆盖须按 ' + artifact_id + ' 回源核对，不由局部包代替。')
    if unsupported:
        coverage += ' 未打包的未支持文件 role：' + '、'.join(f['role'] for f in unsupported) + '，不得据其声明包内覆盖。'
    references = []
    for i, (f, role) in enumerate([(f, '空间构图：白模' if f['role'] == 'main_image' else '空间参考：白模/' + f['role']) for f in selected] + [
            (f, '外观：' + f['ref_id']) for f in data['appearance_references']]):
        source = verify_file(root, f)
        target = output / f'{i:02d}{source.suffix}'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        references.append(dict(role=role, source=f, **file_record(target, root)))
    bundle = dict(artifact_id=artifact_id, revision=data['revision'], source_shot_ids=[data['shot_id']],
                  source_storyboard=data['source']['source_storyboard'], source_director_text=data['source']['source_director_text'],
                  scene_base=data['source']['scene_base'], shot=data['source']['shot'],
                  scene=data['source']['scene'], adjacent_states=data['source']['adjacent_states'],
                  target_state=data['target_state'], coverage=coverage, camera=data['camera'],
                  actors=data['actors'], references=references, limitations=data['limitations'],
                  runtime_location=data.get('runtime_location', 'local'), render_backend=data['render_backend'],
                  blender_version=data.get('blender_version'),
                  user_confirmation_ref=entry['confirmation_ref'],
                  generation_authorized=False, image_status='not_generated',
                  responsibility='白模按本包媒体实际覆盖承担构图、站位、朝向、接触与相机/粗路径参考；静帧不证明连续运动或全程遮挡。身份、服装、场景外观分别由确认母图承担；不继承占位颜色材质，不复刻精细四肢动作。')
    path = output / 'reference-handoff.json'
    write_json(path, bundle)
    write_text(output / '参考职责.md', f'# {data["shot_id"]} 白模参考交接\n\n'
               f'来源导演包 {bundle["source_storyboard"]["artifact_id"]}；白模 {artifact_id}。\n\n'
               f'{bundle["responsibility"]}\n\n覆盖：{bundle["coverage"]}\n\n'
               '本包只交接已确认空间参考与既有确认母图。生图、视频另按用户当前授权执行。\n')
    return path
