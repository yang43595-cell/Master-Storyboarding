"""The asset Skill's Web boundary: complete bounded inputs, never a reading attestation."""
import json
import hashlib
import re
import sys
from core.character_assets import production_kinds
from core.image_technical import technical_check
from core.knowledge_runtime import resolve_knowledge_root, verify_selected_file
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
    # Only unambiguous quality expressions are deterministic blockers.
    # Bare adjectives can be source names (高级中学) or visible quantities
    # (大片草地); the existing PROMPT-LANG semantic review owns those cases.
    terms = re.findall(r'(?i)(?<![a-z0-9])(?:[248]\s*[kｋ])(?![a-z0-9])|影视级|高级感|(?:高级|高端|唯美|大片)\s*(?:的\s*)?(?:画质|质感|风格|画面|摄影|影像|效果)|大片感|电影感|大师摄影|最佳画质|超高清|高细节|(?<![\w])(?:高级|高端|唯美|大片)(?![\w])', prompt)
    return ['PROMPT-LANG：将含糊画质词改为可观察描述：' + '、'.join(dict.fromkeys(terms))] if terms else []


def local_references(kind, request=None):
    common = ['docs/rules/execution.md', 'docs/rules/artifacts.md',
        'docs/rules/visual-production.md', 'PROJECT_RULES.md', 'skills/asset-image-assistant/SKILL.md',
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
    from stage_context_cli import _state, _state_summary, _tombstone_fingerprints

    vault = resolve_knowledge_root(repo_root=ROOT)
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
    guide = SKILL / 'references' / spec['guide']
    if 'template' not in spec or (raw and Path(raw).resolve() == guide.resolve()):
        if not guide.is_file() or not guide.resolve().is_relative_to(SKILL.resolve()):
            raise ValueError('资产 Skill 分类合同不存在或越界：' + str(guide))
        return guide.resolve()
    vault = resolve_knowledge_root(repo_root=ROOT)
    template = verify_selected_file(vault, 'wiki/reference/提示词模板/' + spec['template'])
    if raw and Path(raw).resolve() != template:
        raise ValueError('本次指定的模板与资产 Skill 内置模板不符：' + str(raw))
    return template


def template_step(kind, view='', index=None):
    """Resolve display labels/legacy arguments once; unknown scene views fail closed."""
    spec = recipe(kind)
    if index is not None:
        if type(index) is not int or not 0 <= index < len(spec['steps']):
            raise ValueError('无效视图序号')
        # Index is the stable identity carried by library jobs. Labels are display only.
        return index
    if kind != '环境':
        return 0
    keys = spec['step_keys']
    if view in spec['steps']:
        return spec['steps'].index(view)
    key = spec.get('legacy_views', {}).get(view, view)
    if key in keys:
        return keys.index(key)
    raise ValueError('未知环境视角：' + str(view))


def _template_excerpt(raw, kind, view='', index=None):
    spec = recipe(kind)
    path = template_source(kind, raw)
    text = path.read_text(encoding='utf-8-sig')
    if not text.strip():
        raise ValueError('模板或分类合同正文为空：' + str(path))
    if 'template' not in spec or path == (SKILL / 'references' / spec['guide']).resolve():
        return {'path': str(path), 'content': text, 'status': 'category-guide'}
    blocks = re.findall(r'```text\s*\n(.*?)```', text, re.S)
    step = template_step(kind, view, index)
    index = spec['blocks'][step]
    if index >= len(blocks) or not blocks[index].strip():
        raise ValueError('提示词模板缺少对应视图正文：' + spec['template'])
    return {'path': str(path.resolve()), 'sha256': _digest(text), 'block_index': index,
            'content': blocks[index].strip(), 'status': 'loaded'}


def executable_template(kind, subject='', notes='', *, raw='', view='', index=None,
                        allow_unreferenced_design=False, reference_records=(), has_references=True):
    """Render the same bounded template for the seed and the model's task package."""
    template = _template_excerpt(raw, kind, view, index)
    if template['status'] != 'loaded':
        return template
    block = template['content']
    # Only these known legacy template sentences are rewritten, before inserting
    # user names/notes. Never scrub a source name or a numeric product specification.
    replacements = {
        '超写实专业人物摄影，真实自然，4K，高细节': '人物摄影，皮肤纹理自然，发丝边缘和五官轮廓清楚',
        '超写实服装电商棚拍，4K，高细节': '服装电商棚拍，织物纹理、缝线和配件边缘清楚',
    }
    for old, new in replacements.items():
        block = block.replace(old, new)
    if kind == '环境':
        step = template_step(kind, view, index)
        if step == 0:
            block = block.replace('以图一【SC-XXX 场景母图】为唯一场景参考', '以用户所选照片为场景参考')
            block += '\n本次45°图是这组场景的第一张母图和文件夹封面。'
        elif step == 1:
            block = block.replace('生成【SC-XXX 场景名称】的场景母图，用作后续所有机位的唯一空间锚点。',
                                  '以图一（已确认的45°母图）为唯一空间锚点，生成同一场景的正面平视图。')
            block += '\n摄影机：正面自然平视高度；仅改变机位，不重新设计场景。'
        elif step == 2:
            block = block.replace('以图一【SC-XXX 场景母图】为唯一场景参考',
                                  '以图一（已确认的45°母图）和图二（已确认的正面图）为场景参考')
        else:
            block = block.replace('图一【场景母图】、图二【反向平视回望图】和图三【45°斜俯视全景图】',
                                  '图一（45°母图）、图二（正面图）和图三（反面图）')
    fields = {'永久固定项': '参考图中的空间边界、建筑、门窗、通道、家具数量和位置、材质、颜色与光源方向',
              '状态编号与仅允许变化项': '保持参考图中的时间、光照与场景状态；只改变本步规定的视角'}
    basis = '本次需求中的' if allow_unreferenced_design else '输入图片中的'
    block = re.sub(r'【([^】]+)】', lambda m: '「' + subject + '」' if 'SC-XXX' in m[1]
                   else fields.get(m[1], '参照' + basis + m[1]), block)
    if allow_unreferenced_design:
        prefix = f'依据当前需求设计「{subject}」尚未锁定的外观候选，保留已确认身份与故事约束。'
        if has_references:
            prefix += '已有参考只按各自职责继承，不把身份参考中的服装当作本次服装设计。'
    else:
        prefix = f'从参考照片中提取「{subject}」。以所选照片的可见细节及指定参考职责为准，不替换已锁定身份或实物外观。'
    roles = [(i + 1, str(record['role'])) for i, record in enumerate(reference_records) if record.get('role')]
    if roles:
        prefix += '\n参考职责：' + '；'.join(f'图{i}：{role}' for i, role in roles) + '。'
    suffix = ('\n未锁定且不可见的外观仅作待用户审核的设计选择，不冒充参考事实。'
              if allow_unreferenced_design else '\n未展示的结构仅按可见线索保守补全；不把推测当作真实背面证据。')
    template['content'] = prefix + '\n' + block.strip() + suffix + ('\n用户补充：' + notes if notes else '')
    template['content_sha256'] = _digest(template['content'])
    return template


def _writing_rules(ref, content, single_item):
    """Remove named non-writing sections; all other source text passes through verbatim."""
    omitted = {
        'docs/rules/artifacts.md': {'JSON 交付与按需导出', '确认范围'},
        'docs/rules/visual-production.md': {'导演稿之后的生产顺序'},
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
                   'sha256': m.checksum, 'content': m.content,
                   **({'template_path': m.template_path, 'template_sha256': m.template_checksum,
                       'template_content': m.template_content} if m.template_path else {})} for m in methods]}
    if not request.get('rows'):
        template = executable_template(request['kind'], request.get('subject', request.get('requirement', {}).get('name', '')),
            request.get('template_notes', ''), raw=request.get('template_source', ''), view=request.get('view', ''),
            index=request.get('index') if request.get('mode', 'asset-library') == 'asset-library' else None,
            allow_unreferenced_design=request.get('allow_unreferenced_design', False),
            reference_records=request.get('reference_records', []), has_references=bool(request.get('reference_paths')))
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
        'methods': [{k: v for k, v in m.items() if k not in {'content', 'template_content'}} for m in package['professional_methods']],
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
