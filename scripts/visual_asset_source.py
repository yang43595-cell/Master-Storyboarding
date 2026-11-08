"""Read the confirmed upstream table; structured requirements belong to the package."""
import copy
import hashlib
import json
import re
from pathlib import Path
if __package__:
    from .core.artifacts import artifact_is_confirmed, project_path, digest, load_manifest
    from .core.project_state import load_project_state
    from .core.schema_contracts import require_schema
    from .core.story_sources import validate_story_sources
    from .visual_asset_choices import apply_choices
    from .visual_asset_product_usage import load_usage
else:
    from core.artifacts import artifact_is_confirmed, project_path, digest, load_manifest
    from core.project_state import load_project_state
    from core.schema_contracts import require_schema
    from core.story_sources import validate_story_sources
    from visual_asset_choices import apply_choices
    from visual_asset_product_usage import load_usage


REPOSITORY = Path(__file__).resolve().parents[1]
TEXT_REFERENCE_SUFFIXES = {'.md', '.json', '.txt'}


def local_reference_document(root, path):
    resolved = Path(path).resolve()
    return resolved.is_relative_to(Path(root).resolve()) or resolved.is_relative_to(REPOSITORY)


def resolve_reference(root, raw, artifacts=(), state=None):
    """Resolve local provenance, using a unique confirmed registration after relocation."""
    filename, separator, anchor = str(raw).partition('#')
    path = project_path(root, filename)
    text_reference = bool(separator) or Path(filename).suffix.lower() in TEXT_REFERENCE_SUFFIXES
    scope_error = '文本参考超出项目和仓库范围，请在项目内登记所需原始资料：' + str(raw)

    def allowed(candidate):
        return (not text_reference and candidate.suffix.lower() not in TEXT_REFERENCE_SUFFIXES
                or local_reference_document(root, candidate))

    if path.is_file():
        if not allowed(path):
            raise ValueError(scope_error)
    else:
        matches, outside = [], False
        for artifact in artifacts:
            registered = artifact.get('path_or_url', '')
            if Path(str(registered)).name != Path(filename).name:
                continue
            candidate = project_path(root, registered)
            if not allowed(candidate):
                outside = True
            elif artifact_is_confirmed(artifact, state or {}, root):
                matches.append((artifact, candidate))
        if not matches and (outside or not allowed(path)):
            raise ValueError(scope_error)
        if len(matches) != 1:
            raise ValueError('来源文件不存在或登记不唯一：' + str(raw))
        entry, path = matches[0]
        if not path.is_file() or not entry.get('checksum') or digest(path) != entry['checksum']:
            raise ValueError('来源登记校验不一致：' + str(raw))
    if separator:
        if path.suffix.lower() != '.md' or not anchor:
            raise ValueError('无法核验来源锚点：' + str(raw))
        text = path.read_text(encoding='utf-8-sig')
        headings = re.findall(r'^#{1,6}\s+(.+?)\s*#*$', text, re.M)
        if anchor not in headings and not re.search(r'\^' + re.escape(anchor) + r'\s*$', text, re.M):
            raise ValueError('来源锚点不存在：' + str(raw))
    return str(path) + (separator + anchor if separator else '')


def receive_table(root, artifacts, entry, state, narrative, package_path):
    if narrative.get('schema_version') == '2.0' and not narrative.get('human_readable_path'):
        return None  # Canonical JSON: optional old exports are not business inputs.
    tables = [a for a in artifacts if a.get('logical_id') == 'narrative.assets'
              and a.get('revision') == entry['revision']]
    if not tables:
        if any(d == 'narrative.assets' or 'ASSETS' in str(d).upper() for d in entry.get('depends_on', [])):
            raise ValueError('叙事包依赖的独立资产表尚未登记')
        return None  # Historical confirmed packages did not have a separate table.
    if len(tables) != 1:
        raise ValueError('同版本 narrative.assets 必须唯一、有效且有用户确认记录')
    table = tables[0]
    path = project_path(root, table['path_or_url'])
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('资产表必须登记在项目文件夹内：' + str(path))
    if not artifact_is_confirmed(table, state, root):
        raise ValueError('同版本 narrative.assets 必须唯一、有效且有用户确认记录')
    for artifact, file in [(entry, package_path), (table, path)]:
        if not artifact.get('checksum') or digest(file) != artifact['checksum']:
            raise ValueError('已登记来源文件内容变化，请回上游重新登记确认：' + str(file))
    # Markdown is the human deliverable. Its AST rows have already been reviewed
    # with the paired JSON upstream; do not implement a second Markdown compiler.
    text = path.read_text(encoding='utf-8-sig')
    background = re.search(r'^## 制作所需背景与范围\s*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    if not background or not background[1].strip():
        raise ValueError('资产表缺少制作所需背景与范围，请回上游补齐')
    return {'source': {'artifact_id': table['artifact_id'], 'logical_id': 'narrative.assets',
                       'revision': table['revision'], 'path': str(path), 'sha256': digest(path)},
            'background': background[1].strip()}


def validate_asset_requirement_granularity(narrative):
    """Reject ambiguous location records before the workbench can invent a split."""
    for req in narrative['asset_requirements']:
        if req['category'] != 'LOCATION':
            continue
        if req.get('spatial_scope') != 'SINGLE_CONTIGUOUS_SPACE':
            raise ValueError(
                f"场景需求 {req['asset_id']} 未声明单一连续空间；请在编导稿新版本中为独立地点分别登记 AST，网页不会自行拆分"
            )


def source_project(path):
    """Read the adopted narrative and the same effective image requirements for Web and review."""
    if not str(path or '').strip():
        raise ValueError('请填写项目文件夹，或选择已连接项目')
    root = Path(path).expanduser().resolve()
    if not (root / '00_项目管理/artifact_manifest.json').is_file():
        raise ValueError('所选目录没有项目产物登记表：' + str(root / '00_项目管理/artifact_manifest.json'))
    artifacts = load_manifest(root)['artifacts']
    entries = [a for a in artifacts if a.get('logical_id') == 'narrative.package' and a.get('status') == 'current']
    if len(entries) != 1 or entries[0].get('user_approved') is not True:
        raise ValueError('项目必须有唯一、当前有效且已由你确认的 narrative.package')
    entry = entries[0]
    path = project_path(root, entry['path_or_url'])
    if not path.is_relative_to(root):
        raise ValueError('叙事包必须登记在项目文件夹内：' + str(path))
    state = load_project_state(root)
    if not artifact_is_confirmed(entry, state, root):
        raise ValueError('项目状态缺少叙事包的用户确认记录，请先在项目中完成确认')
    raw = path.read_bytes()
    narrative = json.loads(raw.decode("utf-8-sig"))
    require_schema(narrative, 'narrative-package.schema.json', label='项目资产数据')
    if narrative.get('schema_version') == '2.0':
        validate_story_sources(narrative, project_root=root, narrative_path=path)
    validate_asset_requirement_granularity(narrative)
    if narrative['revision'] != entry['revision']:
        raise ValueError('叙事包正文状态或版本与项目登记不一致')
    if entry.get('checksum') and hashlib.sha256(raw).hexdigest() != entry['checksum']:
        raise ValueError('已确认叙事包内容已变化，与登记版本不一致；请回上游核对')
    ids = [r['asset_id'] for r in narrative['asset_requirements']]
    if len(ids) != len(set(ids)):
        raise ValueError('叙事包有重复 AST 编号')
    table = receive_table(root, artifacts, entry, state, narrative, path)
    if table:
        narrative['_asset_table'] = table
    narrative = apply_choices(root, narrative)
    usage = load_usage(root, narrative)
    if usage:
        narrative['_product_usage'] = usage
    effective_contract = {k: v for k, v in narrative.items() if not k.startswith('_')}
    require_schema(effective_contract, 'narrative-package.schema.json', label='项目资产数据')
    validate_asset_requirement_granularity(narrative)
    return root, {'artifact_id': entry['artifact_id'], 'logical_id': 'narrative.package',
                  'revision': entry['revision'], 'path': str(path.resolve())}, narrative


def prompt_context(narrative, ast_ids):
    ids = set(ast_ids)
    requirements = [copy.deepcopy(r) for r in narrative['asset_requirements'] if r['asset_id'] in ids]
    paragraph_mode = narrative.get('schema_version') == '2.0'
    ref_key = 'source_paragraph_ids' if paragraph_mode else 'source_event_ids'
    content_key = 'story_paragraphs' if paragraph_mode else 'events'
    id_key = 'paragraph_id' if paragraph_mode else 'event_id'
    selected = {value for r in requirements for value in r[ref_key]}
    context = {'project_id': narrative['project_id'], 'revision': narrative['revision'],
               'asset_requirements': requirements,
               content_key: [copy.deepcopy(item) for item in narrative.get(content_key, []) if item[id_key] in selected]}
    if narrative.get('_asset_table'):
        context['_asset_table'] = copy.deepcopy(narrative['_asset_table'])
        context['_asset_table'].pop('sections', None)
    else:
        context['locked_facts'] = copy.deepcopy(narrative.get('locked_facts', []))
    return context
