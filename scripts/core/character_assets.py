"""Shared character anatomy and production routes; never infer species from names."""
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
