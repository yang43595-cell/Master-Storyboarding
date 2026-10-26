"""The asset Skill's Web boundary: complete bounded inputs, never a reading attestation."""
import json
import hashlib
import re
import sys
from core.character_assets import production_kinds
from pathlib import Path

SKILL = Path(__file__).resolve().parent
ROOT = SKILL.parents[1]


def profile():
    return json.loads((SKILL / 'library-profile.json').read_text(encoding='utf-8'))


def checks(mode='asset-library', kind=None):
    return json.loads((SKILL / 'review-profile.json').read_text(encoding='utf-8'))[
        'object_prompt_checks' if kind == '物品' else 'production_artifact_checks' if mode == 'project' else 'library_checks']


def recipe(kind):
    try:
        return profile()['recipes'][kind]
    except KeyError as exc:
        raise ValueError('资产Skill未提供此输出类型') from exc


def language_issues(prompt):
    # Concrete forbidden examples in shared/prompt-language and PROMPT-LANG.
    terms = re.findall(r'(?i)(?<![a-z0-9])(?:[248]\s*[kｋ])(?![a-z0-9])|影视级|高级感?|高端|唯美|大片|电影感|大师摄影|最佳画质|超高清|高细节', prompt)
    return ['PROMPT-LANG：将含糊画质词改为可观察描述：' + '、'.join(dict.fromkeys(terms))] if terms else []


def local_references(kind, request=None):
    common = ['PROJECT_RULES.md', 'skills/asset-image-assistant/SKILL.md',
        'skills/asset-image-assistant/modules/library-mode.md',
        'skills/asset-image-assistant/modules/asset-method.md',
        'skills/asset-image-assistant/modules/web-input-contract.md',
        'skills/asset-image-assistant/knowledge-profile.json',
        'skills/asset-image-assistant/library-profile.json',
        'skills/asset-image-assistant/review-profile.json',
        'skills/asset-image-assistant/references/00-asset-production-workflow.md',
        'skills/asset-image-assistant/references/00a-asset-complexity-routing-guide.md',
        'skills/asset-image-assistant/references/05-asset-manifest-handoff-guide.md',
        'schemas/visual-asset-manifest.schema.json',
        'skills/storyboard-director/references/image-continuity-hard-rules.md',
        'skills/storyboard-planner/references/lighting-prompt-standard.md',
        'skills/voiceover-to-video-script/references/prompt-language-hard-rules.md']
    common += ['skills/_shared/' + name + '.md' for name in
               ['skill-layers', 'draft-lifecycle', 'product-reference', 'path-delivery', 'user-confirmation', 'knowledge-access', 'prompt-language']]
    if not request or not request.get('rows'):
        common.append('skills/asset-image-assistant/references/' + recipe(kind)['guide'])
    if request and request.get('mode') == 'project':
        common.remove('skills/asset-image-assistant/modules/library-mode.md')
        common.append('skills/asset-image-assistant/modules/project-web-mode.md')
        kinds = profile()['category_kinds']
        for row in request.get('rows', []):
            category = row['requirement']['category']
            if category not in kinds:
                raise ValueError('资产 Skill 未提供此 AST 类别：' + category)
            common.append('skills/asset-image-assistant/references/' + recipe(production_kinds(row['requirement'])[0])['guide'])
    if request and not request.get('rows'):
        # Planning and export contracts do not govern writing one image prompt.
        excluded = {'00-asset-production-workflow.md', '00a-asset-complexity-routing-guide.md',
                    '05-asset-manifest-handoff-guide.md', 'project-web-mode.md', 'library-mode.md',
                    'path-delivery.md', 'draft-lifecycle.md'}
        common = [ref for ref in common if Path(ref).name not in excluded]
    return list(dict.fromkeys(common))


def method_request(request):
    """Derive applicability from actual output kinds/AST rows, never free-text keywords."""
    kinds = [request['kind']] if not request.get('rows') else [
        production_kinds(row['requirement'])[0] for row in request['rows']]
    scopes = {recipe(kind)['method_scope']: True for kind in kinds}
    return {**request, 'asset_method_scopes': scopes,
            'knowledge_topics': ['image-prompt', 'prompt-pollution'],
            'language_review': '提示词污染 画质大词 8K 超高清'}


def _knowledge(request):
    # Reuse the existing selector, method resolver and read-only context helpers.
    # No stage CLI is executed; a Web request owns its files, not the project stage.
    scripts = str(ROOT / 'scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from core.knowledge_blocks import build_knowledge_index, load_policy
    from core.knowledge_selector import select_knowledge, render_knowledge_bundle
    from core.method_documents import resolve_method_documents
    from stage_context_cli import _vault_root, _state, _state_summary, _tombstone_fingerprints

    vault = _vault_root(None)
    if not vault.is_dir():
        raise ValueError('资产 Skill 知识库不存在：' + str(vault))
    knowledge_profile = json.loads((SKILL / 'knowledge-profile.json').read_text(encoding='utf-8'))
    if knowledge_profile.get('skill') != 'asset-image-assistant':
        raise ValueError('资产 Skill 知识配置身份不匹配')
    state = _state_summary(_state(Path(request['project_path']))) if request.get('mode') == 'project' else {}
    resolved = resolve_method_documents(vault, knowledge_profile,
        {'skill': 'asset-image-assistant', 'request': request, 'project_state': state})
    expected = set(profile()['required_methods'])
    # Body anatomy uses the local body-character guide, not the Vault human clothing method.
    # IMAGE-CONTINUITY-METHOD and the dynamic prompt rules still apply to every recipe.
    expected.update('ASSET-' + scope.upper() + '-METHOD' for scope in request['asset_method_scopes'] if scope != 'body')
    pending = [entry['id'] for entry in resolved.pending
               if entry['id'] in expected and entry['reason'] == 'awaiting_user_confirmation']
    if pending:
        raise ValueError('资产 Web 方法绑定尚未确认：' + ', '.join(pending))
    missing_methods = expected - {method.id for method in resolved.required}
    if missing_methods:
        raise ValueError('资产 Web 适用方法未完整绑定或条件不匹配：' + ', '.join(sorted(missing_methods)))
    for method in resolved.required:
        body = re.sub(r'\A---\s*\n.*?\n---\s*\n', '', method.content, count=1, flags=re.S)
        if not re.sub(r'(?m)^#+ .*$', '', body).strip():
            raise ValueError('资产 Web 专业方法正文为空：' + method.id)
    policy = load_policy(ROOT)
    selected = select_knowledge(build_knowledge_index(vault, policy), policy, 'asset-image-assistant',
        request, state, _tombstone_fingerprints(vault, policy), profile=knowledge_profile)
    if any(item.get('reason') == 'blocking_conflict' for item in selected.blocking_conflicts):
        raise ValueError('资产 Web 知识存在 confirmed 冲突：' + json.dumps(selected.blocking_conflicts, ensure_ascii=False))
    missing = set(profile()['required_rules']) - {block.id for block in selected.selected}
    if missing:
        raise ValueError('资产 Web 必需动态规则缺失：' + ', '.join(sorted(missing)))
    return resolved.required, render_knowledge_bundle('asset-image-assistant', selected), str(vault)


def _digest(value):
    """Stable, non-secret provenance marker for a task-package input."""
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def template_source(kind, raw=''):
    """Resolve a recipe source for both Web seed text and the final model input."""
    spec = recipe(kind)
    if 'template' not in spec:
        path = ROOT / 'skills/asset-image-assistant/references' / spec['guide']
    else:
        from stage_context_cli import _vault_root
        vault = _vault_root(None)
        path = Path(raw) if raw else vault / 'wiki/reference/提示词模板' / spec['template']
    if not path.is_file():
        raise ValueError(('本次指定的' if raw else '') + '模板或分类合同文件不存在：' + str(path))
    return path.resolve()


def _template_excerpt(raw, kind, view=''):
    spec = recipe(kind)
    path = template_source(kind, raw)
    text = path.read_text(encoding='utf-8-sig')
    if not text.strip():
        raise ValueError('模板或分类合同正文为空：' + str(path))
    if 'template' not in spec or path == (SKILL / 'references' / spec['guide']).resolve():
        return {'path': str(path), 'content': text, 'status': 'category-guide'}
    blocks = re.findall(r'```text\s*\n(.*?)```', text, re.S)
    step = 0
    if kind == '环境':
        step = next((i for i, name in enumerate(['45°', '正面', '反面', '俯视']) if name in view), 0)
    index = spec['blocks'][step]
    if index >= len(blocks) or not blocks[index].strip():
        raise ValueError('提示词模板缺少对应视图正文：' + spec['template'])
    return {'path': str(path.resolve()), 'sha256': _digest(text), 'block_index': index,
            'content': blocks[index].strip(), 'status': 'loaded'}


def _writing_rules(ref, content, single_item):
    """Remove named non-writing sections; all other source text passes through verbatim."""
    omitted = {
        'PROJECT_RULES.md': {'项目目录按生产流程排列', '双文件产物串行交付', 'JSON 交付与按需导出', '导演稿之后的生产顺序'},
        'skills/asset-image-assistant/SKILL.md': {'后续交接'},
        'skills/_shared/knowledge-access.md': {'局部维护边界', '读取与执行开销'},
    }.get(ref, set()) if single_item else set()
    if not omitted:
        return content
    parts = re.split(r'(?m)(?=^## )', content)
    return ''.join(part for part in parts if part.split('\n', 1)[0].removeprefix('## ').strip() not in omitted)


def build_task_package(request, refs=None, startup_text='', methods=()):
    """One input contract for planning, drafting and review; no experimental branch."""
    refs = refs or local_references(request['kind'], request)
    # Schemas/profiles are consumed by code; the model receives actual rules and
    # only the applicable review checks, not all recipes and both review modes.
    rules = {}
    for ref in refs:
        path = (ROOT / ref).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
            raise ValueError('资产 Web 本地合同缺失或越界：' + ref)
        content = path.read_text(encoding='utf-8')
        if not content.strip():
            raise ValueError('资产 Web 本地合同为空：' + ref)
        if ref.endswith('.md'):
            rules[ref] = _writing_rules(ref, content, not request.get('rows'))
    rules['startup_dynamic_knowledge'] = startup_text
    context = {k: v for k, v in request.items()
               if k not in {'prompt', 'task_package_cache', 'asset_token_pilot'}}
    if request.get('mode') == 'project':
        narrative = request['narrative']
        requirements = [request['requirement']] if request.get('requirement') else [r['requirement'] for r in request.get('rows', [])]
        paragraph_mode = 'story_paragraphs' in narrative
        source_key = 'source_paragraph_ids' if paragraph_mode else 'source_event_ids'
        content_key = 'story_paragraphs' if paragraph_mode else 'events'
        id_key = 'paragraph_id' if paragraph_mode else 'event_id'
        selected = {value for r in requirements for value in r.get(source_key, [])}
        context['narrative'] = {k: narrative[k] for k in ('project_id', 'revision', '_asset_table', 'locked_facts') if k in narrative}
        context['narrative'][content_key] = [item for item in narrative.get(content_key, []) if item.get(id_key) in selected]
        context['narrative']['asset_requirements'] = requirements
    package = {'package_version': 'asset-task-package-v3', 'mode': request.get('mode', 'asset-library'),
               'request': context, 'rules': rules, 'review_checks': checks(request.get('mode', 'asset-library'), request.get('kind') if not request.get('rows') else None),
               'professional_methods': [{'id': m.id, 'path': m.vault_path, 'version': m.version,
                   'sha256': m.checksum, 'content': m.content} for m in methods]}
    if not request.get('rows'):
        template = _template_excerpt(request.get('template_source', ''), request['kind'], request.get('view', ''))
        if request['kind'] == '环境' and template['status'] == 'loaded' and not request.get('reference_paths'):
            raise ValueError('场景派生模板需要参考母图；首张场景请使用分类指南')
        if template['status'] == 'category-guide':
            guide_ref = str(Path(template['path']).relative_to(ROOT)).replace('\\', '/')
            if rules.get(guide_ref) == template['content']:
                template['sha256'] = _digest(template.pop('content'))
                template['content_ref'] = 'rules.' + guide_ref
        package['production_item'] = {'kind': request['kind'], 'view': request.get('view', ''),
            'template': template}
    return package


def prepare(folder, request):
    """Web-specific restricted reading input; does not impersonate an agent's reading."""
    folder.mkdir(parents=True, exist_ok=True)
    mode = 'project' if request.get('mode') == 'project' else 'asset-library'
    request = method_request({**request, 'mode': mode})
    refs = local_references(request['kind'], request)
    methods, dynamic, vault = _knowledge(request)
    package = build_task_package(request, refs, dynamic, methods)
    evidence = {'version': profile()['version'], 'mode': mode, 'skill': 'asset-image-assistant',
        'reading_route': 'web-bounded-input', 'input_complete': True,
        'attestation': 'input_assembled_not_agent_reading', 'vault_root': vault,
        'references': refs, 'required_rules': profile()['required_rules'],
        'methods': [{k: v for k, v in m.items() if k != 'content'} for m in package['professional_methods']],
        'check_ids': [c['id'] for c in checks(mode, request.get('kind') if not request.get('rows') else None)]}
    package_text = json.dumps(package, ensure_ascii=False, indent=2)
    (folder / 'task-package.json').write_text(package_text, encoding='utf-8')
    evidence['task_package'] = {'version': package['package_version'], 'path': str(folder / 'task-package.json')}
    (folder / 'skill-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return package_text, evidence


def validate_review(data, mode='asset-library', kind=None):
    issues = [] if kind == '物品' else language_issues(data['prompt'])
    found = {line.split(':', 1)[0].split('：', 1)[0].strip() for line in data.get('checks', [])}
    missing = [c['id'] for c in checks(mode, kind) if c['id'] not in found]
    if missing:
        issues.append('Skill审查项缺失：' + '、'.join(missing))
    if issues:
        data['issues'] = list(dict.fromkeys(data.get('issues', []) + issues))
        data['passed'] = False
    return data


def approved(step):
    a = step.get('audit', {})
    return (a.get('status') == 'passed' and a.get('skill_execution', {}).get('version') == profile()['version']
            and (step.get('kind') == '物品' or not language_issues(step.get('prompt', ''))))


def technical_check(path, ratio):
    from PIL import Image
    with Image.open(path) as image:
        image.load()
        width, height = image.size
        fmt = image.format
    a, b = map(int, ratio.split(':'))
    passed = abs(width / height - a / b) / (a / b) <= 0.02
    return {'passed': passed, 'width': width, 'height': height, 'format': fmt,
            'expected_ratio': ratio, 'message': f'{width}×{height} · {fmt} · ' + ('比例符合要求，视觉质量待你确认' if passed else '比例不符合要求，请重做')}
