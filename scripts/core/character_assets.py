"""Shared character anatomy and production routes; never infer species from names."""
import re

CHARACTER_TYPES = {'HUMAN', 'HUMANOID', 'ANIMAL', 'NON_HUMANOID', 'PET'}
CATEGORY_KINDS = {'CHARACTER': ['大头照', '服装', '人物1x4', '人物单图'], 'WARDROBE': ['服装'],
                  'LOCATION': ['环境'], 'PROP': ['物品'], 'REAL_PRODUCT': ['产品'],
                  'STYLE_REFERENCE': ['风格', '色卡'], 'OTHER': ['物品', '风格']}


def character_type(req):
    # Old packages retain their original human workflow until explicitly corrected.
    value = req.get('character_type', 'HUMAN')
    if value not in CHARACTER_TYPES:
        raise ValueError('角色类型必须是人类、人形角色、动物、非人形角色或宠物／幻想伙伴')
    return value


def uses_wardrobe(req):
    return req.get('category') == 'CHARACTER' and character_type(req) in {'HUMAN', 'HUMANOID'}


def production_kinds(req):
    if req.get('category') == 'CHARACTER' and not uses_wardrobe(req):
        return ['角色外观']
    return CATEGORY_KINDS[req['category']]


def step_output_id(step):
    return step.get('output_id', step['key'])


def _valid_output_id(value):
    return (isinstance(value, str) and bool(value.strip()) and value == value.strip()
            and not any(ord(c) < 32 or c in '/\\*?@' for c in value))


def board_outfit_ids(requirement):
    """Map the approved wardrobe states to their exact final board IDs."""
    if requirement.get('character_output_kind') != '人物1x4':
        return {}
    ast = requirement['asset_id']
    if not uses_wardrobe(requirement):
        raise ValueError('人物1x4需求只适用于人类或人形角色：' + ast)
    outfits = requirement.get('wardrobe_states')
    if not isinstance(outfits, list) or not 1 <= len(outfits) <= 8:
        raise ValueError('人物1x4须逐套登记 wardrobe_states：' + ast)
    mapping = {}
    for outfit in outfits:
        if (not isinstance(outfit, dict)
                or not all(isinstance(outfit.get(key), str) and outfit[key].strip()
                           for key in ('name', 'evidence'))
                or not _valid_output_id(outfit.get('output_id'))):
            raise ValueError('人物1x4每套服装须登记名称、依据和准确 output_id：' + ast)
        name, output_id = outfit['name'].strip(), outfit['output_id']
        if name in mapping or output_id in mapping.values():
            raise ValueError('人物1x4服装名称或 output_id 重复：' + ast)
        mapping[name] = output_id
    return mapping


def scoped_output_requirements(narrative, choices, asset_ids):
    """Read explicit final-output requirements only for the requested assets.

    Adopted narrative decisions take precedence over historical adjustments.
    A broken requirement for another asset must not stop this exact scope.
    """
    scope = {value.partition('@')[0] for value in asset_ids}
    if not scope:
        return {}
    records = choices.get('character_output_requirements', {})
    if not isinstance(records, dict):
        raise ValueError('character_output_requirements 必须按 AST-ID 登记')
    disabled = set(choices.get('excluded', {})) | set(choices.get('replaced', {}))
    assets = [*narrative.get('asset_requirements', []), *choices.get('added', [])]
    result = {}
    for ast in sorted(scope - disabled):
        matches = [req for req in assets if req.get('asset_id') == ast]
        if ast not in records and not any(req.get('character_output_kind') == '人物1x4' for req in matches):
            continue
        if not re.fullmatch(r'AST-[A-Z0-9-]+', ast):
            raise ValueError('人物最终输出需求的 AST-ID 无效：' + ast)
        if len(matches) != 1:
            raise ValueError('人物最终输出需求没有唯一上游资产：' + ast)
        req = matches[0]
        if req.get('character_output_kind') == '人物单图':
            continue
        decision = req.get('image_decision', '需要制作')
        judgement = choices.get('image_decisions', {})
        override = judgement.get('items', {}).get(ast)
        if override is not None:
            if (judgement.get('source_revision') != narrative.get('revision')
                    or not isinstance(override, dict)
                    or not isinstance(override.get('image_reason'), str)
                    or not override['image_reason'].strip()):
                raise ValueError('资产图判断已失效或缺少依据：' + ast)
            decision = override.get('image_decision')
        if decision in {'无需独立图', '待定'}:
            continue
        if decision not in {'需要制作', '复用现有素材'}:
            raise ValueError('人物最终输出需求与当前出图决定冲突：' + ast)
        if req.get('category') != 'CHARACTER':
            raise ValueError('人物最终输出需求只适用于人物资产：' + ast)
        role = dict(req)
        correction = choices.get('character_types', {}).get(ast)
        if correction is not None:
            if (not isinstance(correction, dict)
                    or correction.get('source_revision') != narrative.get('revision')
                    or not isinstance(correction.get('evidence'), str)
                    or not correction['evidence'].strip()):
                raise ValueError('角色类型登记已失效或缺少依据：' + ast)
            role['character_type'] = correction.get('character_type')
        if not uses_wardrobe(role):
            raise ValueError('人物1x4需求只适用于人类或人形角色：' + ast)
        if req.get('character_output_kind') == '人物1x4':
            result[ast] = {output_id: '人物1x4' for output_id in board_outfit_ids(role).values()}
            continue
        item = records[ast]
        if not isinstance(item, dict) or item.get('source_revision') != narrative.get('revision'):
            raise ValueError('人物最终输出需求的来源版本已变化，请核对：' + ast)
        if not isinstance(item.get('evidence'), str) or not item['evidence'].strip():
            raise ValueError('人物最终输出需求缺少明确要求依据：' + ast)
        outputs = item.get('outputs')
        if not isinstance(outputs, dict) or not outputs:
            raise ValueError('人物最终输出需求缺少 outputs：' + ast)
        for output_id, kind in outputs.items():
            if not _valid_output_id(output_id) or kind != '人物1x4':
                raise ValueError('人物最终输出须为准确输出 ID 与 人物1x4：' + ast)
        result[ast] = dict(outputs)
    return result


def validate_output_plan(requirements, targets, kinds):
    """Check exact final targets, leaving intermediate production available."""
    problems = []
    kinds = kinds if isinstance(kinds, dict) else {}
    for target in dict.fromkeys(targets):
        ast, separator, output_id = target.partition('@')
        expected = requirements.get(ast, {}).get(output_id) if separator else None
        if expected is not None and kinds.get(target) != expected:
            problems.append(f'{target}：已要求最终 {expected}，本批 image_output_kinds 缺失或不符')
        elif ast in requirements and kinds.get(target) == '人物单图':
            problems.append(f'{target}：已要求最终人物1×4，不能另建人物单图替代')
    return problems


def final_output_problems(requirements, requests, available):
    """Validate selected final metadata, not pixels, confirmation or process files.

    Exact requests check only that output; an AST-only request checks its explicit
    final set. Existing final boards can be reused without recreating their head
    or clothing sources. Unregistered assets keep their original validation path.
    """
    problems = []
    for request in dict.fromkeys(requests):
        ast, separator, output_id = request.partition('@')
        required = requirements.get(ast)
        if required is None:
            continue
        if separator and output_id not in required:
            problems.append(f'{request}：不是已登记的最终人物输出，不能用中间图替代')
            continue
        selected = {output_id: required[output_id]} if separator else required
        for final_id, expected in selected.items():
            outputs = [value for value in available.get(ast, [])
                       if value.get('output_id') == final_id]
            if len(outputs) != 1:
                problems.append(f'{ast}@{final_id}：缺少唯一最终 {expected} 输出')
            elif outputs[0].get('output_kind') != expected:
                problems.append(f'{ast}@{final_id}：最终输出 output_kind 缺失或不符，应为 {expected}')
    return problems
