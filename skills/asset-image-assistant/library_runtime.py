"""The asset Skill's Web boundary: complete bounded inputs, never a reading attestation."""
import json
import hashlib
import re
import sys
from core.character_assets import production_kinds
from core.character_layout import BOARD_IMPORT_ONLY, CHARACTER_PROMPT_PATH, render_character_prompt
from core.image_technical import technical_check
from core.knowledge_runtime import resolve_knowledge_root, verify_selected_file
from pathlib import Path

SKILL = Path(__file__).resolve().parent
ROOT = SKILL.parents[1]


def profile():
    return json.loads((SKILL / 'library-profile.json').read_text(encoding='utf-8'))


def recipe(kind, library=None):
    try:
        return (library if library is not None else profile())['recipes'][kind]
    except KeyError as exc:
        raise ValueError('资产Skill未提供此输出类型') from exc


def _output_kinds(kind, rows):
    kinds = []
    for row in rows or ():
        requirement = row['requirement']
        try:
            kinds.append(production_kinds(requirement)[0])
        except KeyError as exc:
            raise ValueError('资产 Skill 未提供此 AST 类别：' + str(requirement.get('category'))) from exc
    return kinds or [kind]


def local_references(kind, request=None, *, library=None):
    rows = request.get('rows', []) if request else []
    common = ['docs/rules/execution.md', 'docs/rules/artifacts.md',
        'docs/rules/visual-production.md', 'PROJECT_RULES.md', 'skills/asset-image-assistant/SKILL.md',
        'skills/asset-image-assistant/modules/library-mode.md',
        'skills/asset-image-assistant/modules/asset-method.md',
        'skills/asset-image-assistant/modules/web-input-contract.md',
        'skills/asset-image-assistant/knowledge-profile.json',
        'skills/asset-image-assistant/library-profile.json',
        'skills/asset-image-assistant/references/00-asset-production-workflow.md',
        'skills/asset-image-assistant/references/00a-asset-complexity-routing-guide.md',
        'skills/asset-image-assistant/references/05-asset-manifest-handoff-guide.md',
        'schemas/visual-asset-manifest.schema.json',
        'skills/storyboard-director/references/image-continuity-hard-rules.md',
        'skills/storyboard-planner/references/lighting-prompt-standard.md',
        'skills/voiceover-to-video-script/references/prompt-language-hard-rules.md']
    common += ['skills/_shared/' + name + '.md' for name in
               ['skill-layers', 'draft-lifecycle', 'product-reference', 'path-delivery', 'user-confirmation', 'knowledge-access', 'prompt-language']]
    # Continuity rules specific to a kind come from its existing category guide.
    common.extend('skills/asset-image-assistant/references/' + recipe(output_kind, library)['guide']
                  for output_kind in _output_kinds(kind, rows))
    if request and request.get('mode') == 'project':
        common.remove('skills/asset-image-assistant/modules/library-mode.md')
        if any(record.get('layout_only') for record in request.get('reference_records', [])):
            common.append(CHARACTER_PROMPT_PATH.relative_to(ROOT).as_posix())
        common.append('skills/asset-image-assistant/modules/project-web-mode.md')
    if request and not rows:
        # Planning and export contracts do not govern writing one image prompt.
        excluded = {'00-asset-production-workflow.md', '00a-asset-complexity-routing-guide.md',
                    '05-asset-manifest-handoff-guide.md', 'project-web-mode.md', 'library-mode.md',
                    'path-delivery.md', 'draft-lifecycle.md'}
        common = [ref for ref in common if Path(ref).name not in excluded]
    return list(dict.fromkeys(common))


def method_request(request, *, library=None):
    """Derive applicability from actual output kinds/AST rows, never free-text keywords."""
    kinds = _output_kinds(request['kind'], request.get('rows'))
    scopes = {recipe(kind, library)['method_scope']: True for kind in kinds}
    return {**request, 'asset_method_scopes': scopes,
            'knowledge_topics': ['image-prompt', 'prompt-pollution'],
            'language_review': '提示词污染 画质大词 8K 超高清'}


def _knowledge(request, library):
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
    scopes = request['asset_method_scopes']
    if not scopes.get('scene'):
        # Keep the ordinary prepare profile intact. Web has an explicit output-kind scope.
        entries = knowledge_profile.get('professional_methods')
        if isinstance(entries, list):
            knowledge_profile['professional_methods'] = [
                entry for entry in entries
                if not isinstance(entry, dict) or entry.get('id') != 'IMAGE-CONTINUITY-METHOD']
    state = _state_summary(_state(Path(request['project_path']))) if request.get('mode') == 'project' else {}
    resolved = resolve_method_documents(vault, knowledge_profile,
        {'skill': 'asset-image-assistant', 'request': request, 'project_state': state})
    # Body anatomy uses its local guide. The fixed-background method belongs to scenes.
    expected = {'ASSET-' + scope.upper() + '-METHOD' for scope, enabled in scopes.items()
                if enabled and scope != 'body'}
    if scopes.get('scene'):
        expected.add('IMAGE-CONTINUITY-METHOD')
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
    missing = set(library['required_rules']) - {block.id for block in selected.selected}
    if missing:
        raise ValueError('资产 Web 必需动态规则缺失：' + ', '.join(sorted(missing)))
    return resolved.required, render_knowledge_bundle('asset-image-assistant', selected), str(vault)


def _digest(value):
    """Stable, non-secret provenance marker for a task-package input."""
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def template_source(kind, raw='', *, spec=None):
    """Resolve a recipe source for both Web seed text and the final model input."""
    spec = spec or recipe(kind)
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


def template_step(kind, view='', index=None, *, spec=None):
    """Resolve display labels/legacy arguments once; unknown scene views fail closed."""
    spec = spec or recipe(kind)
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


def _template_excerpt(raw, kind, view='', index=None, *, spec=None):
    spec = spec or recipe(kind)
    path = template_source(kind, raw, spec=spec)
    text = path.read_text(encoding='utf-8-sig')
    if not text.strip():
        raise ValueError('模板或分类合同正文为空：' + str(path))
    if 'template' not in spec or path == (SKILL / 'references' / spec['guide']).resolve():
        return {'path': str(path), 'content': text, 'status': 'category-guide'}
    blocks = re.findall(r'```text\s*\n(.*?)```', text, re.S)
    step = template_step(kind, view, index, spec=spec)
    index = spec['blocks'][step]
    if index >= len(blocks) or not blocks[index].strip():
        raise ValueError('提示词模板缺少对应视图正文：' + spec['template'])
    return {'path': str(path.resolve()), 'sha256': _digest(text), 'block_index': index,
            'content': blocks[index].strip(), 'status': 'loaded'}


def executable_template(kind, subject='', notes='', *, raw='', view='', index=None,
                        allow_unreferenced_design=False, reference_records=(), has_references=True, spec=None):
    """Render the same bounded template for the seed and the model's task package."""
    if kind == '人物1x4':
        raise ValueError(BOARD_IMPORT_ONLY)
    if reference_records and reference_records[-1].get('layout_only'):
        if raw and Path(raw).resolve() != CHARACTER_PROMPT_PATH:
            raise ValueError('人物灰模的提示词须使用项目已确认模板')
        body = render_character_prompt(kind, reference_records)
        return {'path': str(CHARACTER_PROMPT_PATH), 'sha256': _digest(CHARACTER_PROMPT_PATH.read_text(encoding='utf-8')),
                'content': body, 'content_sha256': _digest(body), 'status': 'loaded'}
    if kind in {'大头照', '服装'}:
        if raw and Path(raw).resolve() != CHARACTER_PROMPT_PATH:
            template_source(kind, raw, spec=spec)
        refs = reference_records or ([{}] if has_references else [])
        body = render_character_prompt(kind, refs)
        roles = [(index, ref['role']) for index, ref in enumerate(refs, 1) if ref.get('role')]
        if roles:
            body += '\n\n参考职责：' + '；'.join(f'图{index}：{role}' for index, role in roles) + '。'
        if subject:
            body += '\n\n制作对象：' + subject
        if notes:
            body += '\n用户补充：' + notes
        return {'path': str(CHARACTER_PROMPT_PATH), 'sha256': _digest(CHARACTER_PROMPT_PATH.read_text(encoding='utf-8')),
                'content': body, 'content_sha256': _digest(body), 'status': 'loaded'}
    spec = spec or recipe(kind)
    template = _template_excerpt(raw, kind, view, index, spec=spec)
    if template['status'] != 'loaded':
        return template
    block = template['content']
    fields = {'永久固定项': '参考图中的空间边界、建筑、门窗、通道、家具数量和位置、材质、颜色与光源方向',
              '状态编号与仅允许变化项': '保持参考图中的时间、光照与场景状态；只改变本步规定的视角'}
    if kind == '环境':
        step = template_step(kind, view, index, spec=spec)
        if step == 0:
            block = block.replace('以图一【SC-XXX 场景母图】为唯一场景参考', '以用户所选照片为场景参考')
            block = block.replace(
                '目的：补全平视图中不易识别的前后距离、功能分区、主要通道与固定资产相对位置。',
                '目的：建立可供后续正反打和运镜复用的环境母图。保留参考中已确认的结构、尺度、固定陈设和光向，'
                '沿同一布局补全原图裁切、遮挡或没拍到的相邻区域。未核实部分作为设计候选。')
            block = block.replace(
                '摄影机：自然的高位 45°斜俯视，保留真实空间透视与屋顶结构，不移动家具或重排物品。',
                '摄影机：放在空间一角，朝对角线作 45°斜俯广角全景。开放场地从边缘斜看核心区。'
                '保留自然透视、屋顶结构和已确认陈设位置。')
            block = block.replace(
                '本次只改变：摄影机高度、位置与构图；清楚展示入口区、核心行动区、辅助区、后景区及核心资产的远近关系。',
                '画面覆盖：同框交代主要入口、通道、核心行动区、后景边界与固定陈设的相对位置，'
                '给站位和移动留出空间。')
            block += '\n本次45°图是这组场景的第一张母图和文件夹封面。'
            fields['永久固定项'] = '已确认的墙体、门窗、通道、固定陈设位置与数量、材质、颜色和光源方向'
            fields['状态编号与仅允许变化项'] = '延续本轮已确认的时段、光照方向与场景状态'
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
    """Select the applicable sections without rewriting their source text."""
    if ref == 'skills/_shared/knowledge-access.md':
        required = {'## 制作的仓库知识根', '## 读取与执行开销', '## 压缩后的主动恢复核对'}
        sections = {}
        for section in re.split(r'(?m)(?=^## )', content):
            heading, _, body = section.partition('\n')
            if heading in required and body.strip():
                sections[heading] = section
        missing = required - sections.keys()
        if missing:
            raise ValueError('资产 Web 知识读取合同缺少正文：' + '、'.join(sorted(missing)))
        return ''.join(sections.values())
    omitted = {
        'docs/rules/artifacts.md': {'JSON 交付与按需导出', '确认范围'},
        'docs/rules/visual-production.md': {'导演稿之后的生产顺序'},
        'skills/asset-image-assistant/SKILL.md': {'后续交接'},
    }.get(ref, set()) if single_item else set()
    if ref == 'docs/rules/execution.md':
        omitted = {'等待与工具回传'}
    elif ref == 'skills/storyboard-director/references/image-continuity-hard-rules.md':
        omitted = {'按任务读取'}
    if not omitted:
        return content
    parts = re.split(r'(?m)(?=^## )', content)
    return ''.join(part for part in parts if part.split('\n', 1)[0].removeprefix('## ').strip() not in omitted)


def build_task_package(request, refs=None, startup_text='', methods=(), *, library=None):
    """Assemble the applicable production inputs for planning and writing."""
    refs = refs or local_references(request['kind'], request)
    rules = {}
    for ref in refs:
        path = (ROOT / ref).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
            raise ValueError('资产 Web 本地合同缺失或越界：' + ref)
        if ref.endswith('.md'):
            content = path.read_text(encoding='utf-8')
            if not content.strip():
                raise ValueError('资产 Web 本地合同为空：' + ref)
            rules[ref] = _writing_rules(ref, content, not request.get('rows'))
        elif not path.stat().st_size:
            raise ValueError('资产 Web 本地合同为空：' + ref)
    rules['startup_dynamic_knowledge'] = startup_text
    context = {k: v for k, v in request.items()
               if k not in {'prompt', 'task_package_cache', 'asset_token_pilot', 'model', 'provider', 'codex_model', 'repair_issues'}}
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
               'request': context, 'rules': rules,
               'professional_methods': [{'id': m.id, 'path': m.vault_path, 'version': m.version,
                   'sha256': m.checksum, 'content': m.content,
                   **({'template_path': m.template_path, 'template_sha256': m.template_checksum,
                       'template_content': m.template_content} if m.template_path else {})} for m in methods]}
    if not request.get('rows'):
        actual_paths = set(request.get('reference_paths', [])) - {
            record['original'] for record in request.get('reference_records', []) if record.get('layout_only')}
        template = executable_template(request['kind'], request.get('subject', request.get('requirement', {}).get('name', '')),
            request.get('template_notes', ''), raw=request.get('template_source', ''), view=request.get('view', ''),
            index=request.get('index') if request.get('mode', 'asset-library') == 'asset-library' else None,
            allow_unreferenced_design=request.get('allow_unreferenced_design', False),
            reference_records=request.get('reference_records', []), has_references=bool(actual_paths), spec=recipe(request['kind'], library))
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


def current_package(request, *, library=None):
    library = library or profile()
    mode = 'project' if request.get('mode') == 'project' else 'asset-library'
    request = method_request({**request, 'mode': mode}, library=library)
    refs = local_references(request['kind'], request, library=library)
    methods, dynamic, vault = _knowledge(request, library)
    return build_task_package(request, refs, dynamic, methods, library=library), refs, vault


def prepare(folder, request):
    """Web-specific restricted reading input; does not impersonate an agent's reading."""
    folder.mkdir(parents=True, exist_ok=True)
    library_profile = profile()
    package, refs, vault = current_package(request, library=library_profile)
    evidence = {'version': library_profile['version'], 'mode': package['mode'], 'skill': 'asset-image-assistant',
        'reading_route': 'web-bounded-input', 'input_complete': True,
        'attestation': 'input_assembled_not_agent_reading', 'vault_root': vault,
        'references': refs, 'required_rules': library_profile['required_rules'],
        'methods': [{k: v for k, v in m.items() if k not in {'content', 'template_content'}} for m in package['professional_methods']]}
    package_text = json.dumps(package, ensure_ascii=False, indent=2)
    (folder / 'task-package.json').write_text(package_text, encoding='utf-8')
    evidence['task_package'] = {'version': package['package_version'], 'path': str(folder / 'task-package.json')}
    (folder / 'skill-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return package_text, evidence
