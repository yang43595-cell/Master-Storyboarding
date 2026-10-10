"""Read the confirmed upstream table; structured requirements belong to the package."""
import copy
import re
from pathlib import Path
from core.artifacts import artifact_is_confirmed, project_path, digest


def resolve_reference(root, raw, artifacts=(), state=None):
    """Resolve local provenance, using a unique confirmed registration after relocation."""
    filename, separator, anchor = str(raw).partition('#')
    path = project_path(root, filename)
    if not path.is_file():
        matches = [a for a in artifacts
                   if Path(str(a.get('path_or_url', ''))).name == Path(filename).name
                   and artifact_is_confirmed(a, state or {}, root)]
        if len(matches) != 1:
            raise ValueError('来源文件不存在或登记不唯一：' + str(raw))
        entry = matches[0]
        path = project_path(root, entry['path_or_url'])
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
    tables = [a for a in artifacts if a.get('logical_id') == 'narrative.assets'
              and a.get('revision') == entry['revision']]
    if not tables:
        if any(d == 'narrative.assets' or 'ASSETS' in str(d).upper() for d in entry.get('depends_on', [])):
            raise ValueError('叙事包依赖的独立资产表尚未登记')
        return None  # Historical confirmed packages did not have a separate table.
    if len(tables) != 1 or not artifact_is_confirmed(tables[0], state, root):
        raise ValueError('同版本 narrative.assets 必须唯一、有效且有用户确认记录')
    table = tables[0]
    path = project_path(root, table['path_or_url'])
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
