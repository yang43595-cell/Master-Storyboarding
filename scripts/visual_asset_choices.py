"""Explicit project image requirements, separate from immutable story approvals."""
import copy
import hashlib
if __package__:
    from .core.character_assets import CHARACTER_TYPES, uses_wardrobe
else:
    from core.character_assets import CHARACTER_TYPES, uses_wardrobe
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

RELATIVE = '04_资产/资产需求调整.json'


def _load_asset_refs():
    """兼容 scripts 作为导入根或仓库根两种调用方式。"""
    if __package__:
        from .core import asset_refs
    else:
        from core import asset_refs
    return asset_refs


def read_choices(root):
    path = Path(root) / RELATIVE
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'revision': 0, 'added': [], 'excluded': {}, 'replaced': {}, 'history': []}


def image_decision(requirement):
    # Historical confirmed packages retain their existing production behavior.
    return requirement.get('image_decision', '需要制作')


def image_work_items(narrative):
    return [r for r in narrative['asset_requirements']
            if image_decision(r) in {'需要制作', '复用现有素材'}]


def requirement_digest(requirement):
    """Bind production choices to the unchanged source requirement, not its label."""
    raw = json.dumps({k: v for k, v in requirement.items() if k not in {'image_decision', 'image_reason'}}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def narrative_digest(narrative):
    business = {k: v for k, v in narrative.items() if not k.startswith('_')}
    raw = json.dumps(business, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _apply_production_spec(requirement, specification):
    result = copy.deepcopy(requirement)
    names = specification.get('wardrobe_name_map', {})
    if 'wardrobe_name_map' in specification:
        if (not isinstance(names, dict) or not names
                or specification.get('unchanged_appearance') is not True):
            raise ValueError('造型标签对应须明确外观未变化；实际换装不能继承原图确认')
        states = result.get('wardrobe_states')
        if (result.get('category') != 'CHARACTER' or not isinstance(states, list)
                or not states or not isinstance(names, dict)
                or not set(names) <= {s['name'] for s in states}
                or any(not isinstance(v, str) or not v.strip() for v in names.values())):
            raise ValueError('造型标签对应须引用已有明确服装槽位，不能新增换装或修改人物身份')
        for state in states:
            state['name'] = names.get(state['name'], state['name'])
        if len({s['name'] for s in states}) != len(states):
            raise ValueError('造型标签对应不能合并不同服装槽位')
    coverage = specification.get('scene_state_coverage')
    if coverage is not None:
        states = result.get('required_states', [])
        if (result.get('category') != 'LOCATION' or not isinstance(coverage, dict) or not coverage
                or specification.get('unchanged_static_space') is not True):
            raise ValueError('场景复用须明确同一静态空间未变化，并逐项对应原状态')
        flattened = []
        for primary, covered in coverage.items():
            if (primary not in states or not isinstance(covered, list) or not covered
                    or primary not in covered or any(v not in states for v in covered)):
                raise ValueError('场景覆盖必须引用原 required_states，且包含主状态')
            flattened.extend(covered)
        if len(flattened) != len(set(flattened)) or set(flattened) != set(states):
            raise ValueError('场景覆盖须完整且唯一，不能遗漏、重叠或合并未知状态')
    return result


def production_spec(narrative, requirement):
    specification = (narrative.get('_asset_choices', {}).get('production_specs', {})
                     .get(requirement['asset_id']))
    return specification or {}


def scene_state_coverage(narrative, requirement):
    specification = production_spec(narrative, requirement)
    coverage = specification.get('scene_state_coverage')
    return copy.deepcopy(coverage) if coverage is not None else {
        state: [state] for state in requirement.get('required_states', [])}


def retained_specification(old_requirement, new_requirement, narrative, previous_narrative=None):
    """Only an explicitly recorded label mapping may retain an existing image job."""
    specification = production_spec(narrative, new_requirement)
    baseline = copy.deepcopy(old_requirement)
    if previous_narrative is not None:
        prior = production_spec(previous_narrative, old_requirement).get('wardrobe_name_map', {})
        reverse = {new: old for old, new in prior.items()}
        for state in baseline.get('wardrobe_states', []):
            state['name'] = reverse.get(state['name'], state['name'])
    if (not specification.get('wardrobe_name_map')
            or specification.get('source_requirement_sha256') != requirement_digest(baseline)):
        return False
    return _apply_production_spec(baseline, specification) == new_requirement


def effective(narrative, choices):
    requirements = copy.deepcopy(narrative['asset_requirements'])
    ids = {r['asset_id'] for r in requirements}
    for req in choices['added']:
        if req['asset_id'] in ids:
            raise ValueError('新增资产编号与上游冲突，请核对资产需求调整表')
        ids.add(req['asset_id'])
        requirements.append(copy.deepcopy(req))
    unknown = (set(choices['excluded']) | set(choices['replaced'])) - ids
    if unknown:
        raise ValueError('资产调整引用的上游需求已消失，请核对：' + ', '.join(sorted(unknown)))
    paragraphs = {p.get('paragraph_id', p.get('event_id')) for p in narrative.get('story_paragraphs', narrative.get('events', []))}
    for req in choices['added']:
        refs = req.get('source_paragraph_ids', req.get('source_event_ids', []))
        if not refs or not set(refs) <= paragraphs:
            raise ValueError('新增资产出现位置已失效，请重新核对')
    judgement = choices.get('image_decisions', {})
    if judgement:
        if judgement.get('source_revision') != narrative['revision']:
            raise ValueError('资产图判断的上游版本已变化，请核对后重新采用')
        decisions = judgement.get('items', {})
        if set(decisions) - ids:
            raise ValueError('资产图判断引用了不存在的资产编号')
        for req in requirements:
            decision = decisions.get(req['asset_id'])
            if decision is None:
                continue
            if (decision.get('image_decision') not in {'无需独立图', '复用现有素材', '需要制作', '待定'}
                    or not str(decision.get('image_reason', '')).strip()):
                raise ValueError('资产图判断缺少有效结论或依据：' + req['asset_id'])
            req.update({k: decision[k] for k in ('image_decision', 'image_reason')})
    specifications = choices.get('production_specs', {})
    if not isinstance(specifications, dict) or set(specifications) - ids:
        raise ValueError('制作规格引用了不存在的资产编号')
    for ast, specification in specifications.items():
        req = next(r for r in requirements if r['asset_id'] == ast)
        if (not isinstance(specification, dict)
                or specification.get('source_revision') != narrative['revision']
                or specification.get('source_requirement_sha256') != requirement_digest(req)
                or specification.get('source_narrative_sha256') != narrative_digest(narrative)
                or not all(isinstance(specification.get(k), str) and specification[k].strip()
                           for k in ('decision_ref', 'quote'))):
            raise ValueError('制作规格的上游或决定依据已失效，请核对：' + ast)
        req.update(_apply_production_spec(req, specification))
    disabled = set(choices['excluded']) | set(choices['replaced'])
    types = choices.get('character_types', {})
    for ast, item in types.items():
        req = next((r for r in requirements if r['asset_id'] == ast), None)
        if req is None or req['category'] != 'CHARACTER' or item.get('source_revision') != narrative['revision']:
            raise ValueError('角色类型登记已失效，请核对：' + ast)
        if item.get('character_type') not in CHARACTER_TYPES or not item.get('evidence', '').strip():
            raise ValueError('角色类型缺少有效分类或依据：' + ast)
        req['character_type'] = item['character_type']
    return [r for r in requirements if r['asset_id'] not in disabled]


def apply_choices(root, narrative):
    choices = read_choices(root)
    if not choices['revision']:
        return narrative
    result = copy.deepcopy(narrative)
    result['asset_requirements'] = effective(narrative, choices)
    result['_asset_choices'] = {**choices, 'path': str(Path(root) / RELATIVE)}
    return result


def confirmed_split_children(root, narrative, choices):
    """Resolve legacy batch split approval against its saved adjustment revision."""
    approvals = [h for h in choices.get('history', [])
                 if h.get('operation') == 'split' and h.get('user_confirmed') is True
                 and h.get('source_revision') == narrative['revision']]
    if not approvals:
        return set()
    originals = {a['asset_id']: a for a in narrative['asset_requirements']}
    current = {a['asset_id']: a for a in choices['added']}
    confirmed = set()
    # Batch records have no ast_id. The snapshot where that event was last
    # records its exact children; a later edit must not inherit blanket approval.
    for path in (Path(root) / RELATIVE).parent.joinpath('需求调整历史').glob('R*.json'):
        snapshot = json.loads(path.read_text(encoding='utf-8-sig'))
        history = snapshot.get('history', [])
        if not history or history[-1] not in approvals:
            continue
        saved = {a['asset_id']: a for a in snapshot.get('added', [])}
        for parent, split in snapshot.get('replaced', {}).items():
            if parent not in originals or choices.get('replaced', {}).get(parent) != split:
                continue
            for child in split.get('children', []):
                requirement = saved.get(child)
                if (requirement is not None and current.get(child) == requirement
                        and child not in originals):
                    confirmed.add(child)
    return confirmed


def save_choices(root, narrative, choices):
    """JSON is authoritative; Markdown is the user-readable current asset table."""
    root = Path(root)
    active = effective(narrative, choices)
    path = root / RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    archive = path.parent / '需求调整历史'
    archive.mkdir(exist_ok=True)
    serialized = json.dumps(choices, ensure_ascii=False, indent=2) + '\n'
    snapshot = archive / f"R{choices['revision']:03d}.json"
    if snapshot.exists() and snapshot.read_text(encoding='utf-8') != serialized:
        raise ValueError('资产调整修订冲突，刷新后重试')
    snapshot.write_text(serialized, encoding='utf-8')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(serialized, encoding='utf-8')
    temporary.replace(path)
    def cell(s):
        return str(s).replace('|', '／').replace('\n', ' ')
    asset_refs = _load_asset_refs()
    images = asset_refs.confirmed_images(root)

    def image_state(ast):
        return '已确认母版' if images.get(ast) else '未出图'

    # 人物身高基准（cm）：预演白模比例、遮挡与景别判断的唯一来源。
    heights = {}
    height_path = path.parent / '人物身高基准.json'
    if height_path.is_file():
        heights = json.loads(height_path.read_text(encoding='utf-8-sig')).get('heights', {})

    def height_cell(req):
        if req.get('category') != 'CHARACTER':
            return '—'
        value = heights.get(req['asset_id'])
        return str(value) if value else '待定'

    rows = ['# 当前资产图需求表', '', '用户删除表示本项目不需要该资产图，不代表删除故事情节。新增位置由用户核对；不作为原片客观事实证明。', '',
            '| 资产编号 | 名称 | 角色类型 | 出现位置 | 处理结论 | 判断依据 | 图片状态 | 身高基准(cm) |', '|---|---|---|---|---|---|---|---|']
    for req in active:
        refs = req.get('source_paragraph_ids', req.get('source_event_ids', []))
        rows.append('| ' + ' | '.join(map(cell, [req['asset_id'], req['name'], ({'HUMAN':'人类','HUMANOID':'人形角色','ANIMAL':'动物','NON_HUMANOID':'非人形角色','PET':'宠物／幻想伙伴'}.get(req.get('character_type', 'HUMAN')) if req['category']=='CHARACTER' else '—'), '；'.join(refs), req.get('image_decision', '旧表未记录判断'), req.get('image_reason', '沿用历史确认；实质修订时补齐'), image_state(req['asset_id']), height_cell(req)])) + ' |')
    for ast, item in choices['excluded'].items():
        rows.append('| ' + ' | '.join(map(cell, [ast, item['name'], '—', '见原需求', '无需独立图', '用户明确不需要；可恢复', image_state(ast), '—'])) + ' |')
    for ast, item in choices['replaced'].items():
        rows.append('| ' + ' | '.join(map(cell, [ast, item['name'], '—', '见原需求', '已拆分', '已拆分为：' + '、'.join(item['children']), image_state(ast), '—'])) + ' |')
    for ast, specification in choices.get('production_specs', {}).items():
        labels = [old + ' → ' + new for old, new in specification.get('wardrobe_name_map', {}).items()]
        coverage = [primary + ' 覆盖 ' + '、'.join(states)
                    for primary, states in specification.get('scene_state_coverage', {}).items()]
        rows.extend(['', '制作规格 ' + ast + '：' + '；'.join(labels + coverage),
                     '用户原话：' + specification['quote'] + '；依据：' + specification['decision_ref']])
    (path.parent / '当前资产需求表.md').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    if __package__:
        from .visual_asset_product_usage import load_usage, append_table
    else:
        from visual_asset_product_usage import load_usage, append_table
    append_table(root, load_usage(root, narrative))
    # 当前资产清单：下游写稿与素材清单的唯一入口（状态 + 在场区间 + 图片状态）
    asset_refs.write_asset_list(root, narrative, choices)


def change(service, payload):
    if payload.get('confirm') is not True:
        raise ValueError('请明确确认本次资产需求调整')
    with service.lib.lock:
        p = service.get(payload['project_id'])
        service.fresh(p)
        # Do not interrupt running generation or prompt calls, including other rows.
        if p.get('planning', {}).get('status') == 'running' or p.get('batch', {}).get('status') == 'running':
            raise ValueError('项目正在处理，完成后再调整资产需求')
        for row in p['rows']:
            job = service.lib.get(row['job_id'], 'jobs') if row.get('job_id') else None
            if job and any(s.get('status') in {'generating', 'submitting', 'submitted', 'querying'} or (s.get('audit') or {}).get('status') in {'drafting', 'reviewing'} for s in job['steps']):
                raise ValueError('图片或提示词正在处理，完成后再调整需求')
        root = Path(p['path'])
        # Always evaluate against the original story package, never an already merged copy.
        narrative = json.loads(Path(p['source']['path']).read_text(encoding='utf-8-sig'))
        choices = read_choices(root)
        action = payload['operation']
        now = datetime.now(timezone.utc).isoformat()
        if action == 'exclude':
            row = service.row(p, payload['ast_id'])
            choices['excluded'][payload['ast_id']] = {'name': row['requirement']['name'], 'at': now,
                'message': '用户明确：本项目不需要这项资产图', 'reason': str(payload.get('reason', '')).strip()}
        elif action == 'restore':
            if payload['ast_id'] not in choices['excluded']:
                raise ValueError('该资产不在已删除需求中')
            del choices['excluded'][payload['ast_id']]
        elif action == 'character-type':
            req = service.row(p, payload['ast_id'])['requirement']
            value, evidence = payload.get('character_type'), str(payload.get('evidence', '')).strip()
            if req['category'] != 'CHARACTER' or value not in CHARACTER_TYPES or not evidence:
                raise ValueError('请选择角色类型并提供已确认依据')
            choices.setdefault('character_types', {})[req['asset_id']] = {
                'character_type': value, 'evidence': evidence, 'source_revision': narrative['revision']}
        elif action == 'production-spec':
            req = next((r for r in narrative['asset_requirements'] if r['asset_id'] == payload['ast_id']), None)
            if req is None:
                raise ValueError('制作规格只能对应原叙事中的准确 AST')
            allowed = {'wardrobe_name_map', 'unchanged_appearance',
                       'scene_state_coverage', 'unchanged_static_space'}
            supplied = payload.get('specification')
            if (not isinstance(supplied, dict) or not supplied or set(supplied) - allowed
                    or not (set(supplied) & {'wardrobe_name_map', 'scene_state_coverage'})
                    or not all(isinstance(payload.get(k), str) and payload[k].strip()
                               for k in ('decision_ref', 'quote'))):
                raise ValueError('制作规格只接受造型标签对应或静态场景覆盖，并须记录真实用户原话及依据')
            specification = {**copy.deepcopy(supplied), 'source_revision': narrative['revision'],
                             'source_requirement_sha256': requirement_digest(req),
                             'source_narrative_sha256': narrative_digest(narrative),
                             'decision_ref': payload['decision_ref'], 'quote': payload['quote']}
            _apply_production_spec(req, specification)
            if choices.get('production_specs', {}).get(req['asset_id']) == specification:
                return service.detail(p['id'])
            choices.setdefault('production_specs', {})[req['asset_id']] = specification
        elif action == 'add':
            name = str(payload.get('name', '')).strip()
            category = payload.get('category')
            if not name or len(name) > 100 or category not in {'PROP', 'REAL_PRODUCT', 'LOCATION', 'CHARACTER', 'WARDROBE', 'STYLE_REFERENCE', 'OTHER'}:
                raise ValueError('请填写资产名称并选择类别')
            if any(r['name'] == name for r in narrative['asset_requirements'] + choices['added']):
                raise ValueError('已有同名资产，请先查看或恢复原需求')
            paragraph_mode = narrative.get('schema_version') == '2.0'
            key = 'source_paragraph_ids' if paragraph_mode else 'source_event_ids'
            content = narrative.get('story_paragraphs', narrative.get('events', []))
            by_id = {x.get('paragraph_id', x.get('event_id')): x for x in content}
            refs = payload.get('source_ids', [])
            if not isinstance(refs, list) or not refs or not all(isinstance(x, str) and x in by_id for x in refs):
                raise ValueError('请核对至少一个有效故事出现位置')
            description = str(payload.get('appearance', '')).strip()
            if not description or payload.get('location_confirmed') is not True:
                raise ValueError('请说明资产如何出现，并核对该位置；不能只填物品名称')
            ast = 'AST-USER-' + uuid4().hex[:10].upper()
            req = {'asset_id': ast, 'category': category, 'name': name, 'narrative_purpose': description,
                key: list(dict.fromkeys(refs)), 'identity_constraints': [], 'required_states': [description],
                'interactions': [], 'reference_paths': [], 'unknowns': ['外观由用户选图或真实参考确定']}
            if category == 'LOCATION':
                req['spatial_scope'] = 'SINGLE_CONTIGUOUS_SPACE'
            if category == 'CHARACTER':
                req['character_type'] = payload.get('character_type', 'HUMAN')
                if uses_wardrobe(req):
                    names = [x.strip() for x in str(payload.get('wardrobe_states', '')).split('；') if x.strip()]
                    if not names:
                        raise ValueError('请明确人物服装状态，款式可选图时确定')
                    req['wardrobe_states'] = [{'name': x, 'evidence': description} for x in names]
            choices['added'].append(req)
        else:
            raise ValueError('未知资产需求操作')
        choices['revision'] += 1
        choices['history'].append({'operation': action, 'at': now, 'user_confirmed': True,
            'ast_id': payload.get('ast_id', locals().get('ast')), 'appearance': payload.get('appearance', ''),
            'source_revision': p['source']['revision'],
            **({k: payload[k] for k in ('decision_ref', 'quote')} if action == 'production-spec' else {})})
        # Validate additions with the same narrative contract before any persistence.
        if __package__:
            from .core.schema_contracts import require_schema
        else:
            from core.schema_contracts import require_schema
        check = copy.deepcopy(narrative)
        check['asset_requirements'] = effective(narrative, choices)
        require_schema(check, 'narrative-package.schema.json', label='资产需求调整')
        save_choices(root, narrative, choices)
        # 资产被取消、恢复或新增后，自动列出仍引用它／该补引用的下游位置（只报告，不改稿）。
        changed_id = payload.get('ast_id') or locals().get('ast')
        if changed_id:
            try:
                _load_asset_refs().scan_and_write(root, narrative, [changed_id], choices)
            except Exception:
                pass  # 影响清单只作提示，失败不阻断用户的资产操作
        service.sync(p['id'])
        if action == 'restore':
            current = service.get(p['id'])
            row = service.row(current, payload['ast_id'])
            previous = next((r for r in reversed(current.get('history', [])) if r['requirement'] == row['requirement'] and r.get('job_id')), None)
            if previous:
                job = service.lib.get(previous['job_id'], 'jobs')
                if job and not job.get('project_stale'):
                    row.update(reference_ids=previous.get('reference_ids', []), job_id=previous['job_id'])
                    service.save(current)
        return service.detail(p['id'])
