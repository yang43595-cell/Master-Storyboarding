"""Lossless presentation helpers; never edits or approves the business package."""
import re
from collections import Counter

TECHNICAL_MARKER = '【逐镜执行附录】'
LABELS = {
    'space_rules':'空间共用设定', 'defaults':'场景共用声光', 'axis':'轴线',
    'screen_direction':'画面方向', 'subject_positions':'人物位置',
    'key_objects':'关键物件', 'directional_anchors':'方向锚点',
    'light_source':'光源', 'sound_base':'声音基础', 'light_color_intent':'光色',
    'ambience':'环境声', 'music':'音乐', 'camera':'机位', 'framing':'景别与构图',
    'movement':'运镜', 'focus':'焦点', 'take':'镜内行动', 'start':'起态',
    'process':'过程', 'end':'末态', 'audio':'声音与画面文字', 'dialogue':'对白承载',
    'text':'原文', 'speaker_id':'说话者', 'dialogue_id':'对白编号',
    'presentation':'呈现方式', 'delivery':'语气', 'sfx':'音效', 'cut':'切出',
    'purpose':'观看目的', 'rhythm':'节奏', 'overrides':'本镜覆盖', 'shot_id':'镜号',
}


def readable_fields(value, indent=''):
    """Render nested source fields without losing false, empty arrays or unknown keys."""
    if isinstance(value, dict):
        lines = []
        for key, child in value.items():
            label = LABELS.get(key, key)
            if isinstance(child, (dict, list)) and child:
                lines.append(indent + label + '：')
                lines.extend(readable_fields(child, indent + '  '))
            else:
                lines.append(indent + label + '：' + scalar(child))
        return lines
    if isinstance(value, list):
        lines = []
        for child in value:
            if isinstance(child, (dict, list)):
                lines.append(indent + '- ')
                lines.extend(readable_fields(child, indent + '  '))
            else:
                lines.append(indent + '- ' + scalar(child))
        return lines
    return [indent + scalar(value)]


def scalar(value):
    if value == []:
        return '无（源字段为空数组，不继承默认项）'
    if value == {}:
        return '空对象'
    if value is None:
        return '未指定（null）'
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def director_scene_reading(scene):
    flow = scene.get('audiovisual_flow', '')
    shots = scene.get('shots', [])
    # Conservative visibility guard, not a creative/completeness certification.
    # Never hide execution cards when the source flow omits a shot or spoken fragment.
    norm = lambda text: re.sub(r'\s+', '', text)
    body = norm(flow)
    dialogue = Counter(norm(d.get('text', '')) for shot in shots
                       for d in shot.get('audio', {}).get('dialogue', []) if d.get('text'))
    missing = [shot['shot_id'] for shot in shots if shot['shot_id'] not in flow]
    complete = bool(flow) and not missing and all(body.count(text) >= count for text, count in dialogue.items())
    lines = []
    for key in ('space_rules', 'defaults'):
        if key in scene:
            lines += [LABELS[key] + '：', *readable_fields(scene[key]), '']
    lines += ['连续视听正文：', flow or '本旧版没有连续视听正文字段。']
    if not complete:
        lines += ['', '阅读提示：当前正文未完整承载逐镜编号或对白，须同时查看下列逐镜执行附录；不将摘要视为完整导演稿。']
    technical = '\n\n'.join('\n'.join(readable_fields(shot)) for shot in shots)
    # Legacy scene-level beats and all other fields remain in the full source appendix.
    return {'body': '\n'.join(lines).strip(), 'technical': technical,
            'technical_open': not complete}
