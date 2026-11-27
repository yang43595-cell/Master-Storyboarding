"""Pure project asset-choice rules, decoupled from downstream asset_refs."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .character_assets import CHARACTER_TYPES

RELATIVE = '04_资产/资产需求调整.json'


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
    raw = json.dumps(
        {k: v for k, v in requirement.items() if k not in {'image_decision', 'image_reason'}},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    )
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
                or not states
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
