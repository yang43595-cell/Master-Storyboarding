"""Bounded checks of declared scene facts, never a video/semantic evaluator.

Optional review annotations live in the current business JSON, outside model
prose. They are byte-bound to the inspected bodies; no files or approvals are
written. Missing annotations are unverified, not an implied quality pass.
"""
from __future__ import annotations

import hashlib
import math


def prompt_digest(body):
    # Match the existing text checker's outer-whitespace normalization only.
    return hashlib.sha256(body.strip().encode('utf-8')).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _time(value):
    if type(value) not in (int, float) or value < 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def inspect_scene_realism(segments, records):
    """Check explicit facts against each other, not their truth or completeness.

    segments are the existing parser's (id, submitted_body, source_text) tuples.
    records are optional scene_realism annotations, in the same segment order.
    All times are planned seconds local to a segment, never measured playback.
    """
    findings = []

    def add(segment, code, message, status='needs_revision'):
        findings.append(dict(segment_id=segment, code=code, status=status, message=message))

    ids = [s[0] for s in segments]
    if (not segments or len(ids) != len(set(ids)) or not isinstance(records, list)
            or len(records) != len(segments) or any(not isinstance(r, dict) for r in records)
            or [r.get('segment_id') for r in records] != ids):
        add('*', 'coverage', '场景真实感未验证：缺少逐段且同序的记录，或段号不唯一。', 'unverified')
        return _result(findings)
    previous = None
    for (segment, body, _), record in zip(segments, records):
        if record.get('prompt_sha256') != prompt_digest(body):
            add(segment, 'stale_body', '正文已变化或未绑定，本段观察声明须重新核对。', 'unverified')
            previous = None
            continue
        # A malformed group cannot erase other diagnostics or crash the CLI.
        lists = {}
        for key, id_key in [('events', 'event_id'), ('reactions', 'person_id'), ('objects', 'object_id')]:
            rows = record.get(key)
            if (not isinstance(rows, list) or any(not isinstance(r, dict) or not _text(r.get(id_key)) for r in rows)
                    or len({r[id_key] for r in rows}) != len(rows)):
                add(segment, key, f'{key} 缺失、格式错误或身份重复，不能按空项通过。', 'unverified')
                lists[key] = []
            else:
                lists[key] = rows
            if isinstance(rows, list) and not rows and not _text(record.get(key + '_not_applicable')):
                add(segment, key, f'{key} 为空须说明本段不适用的实际原因。', 'unverified')
        events = {r['event_id']: r for r in lists['events']}
        for event in events.values():
            if not _time(event.get('at')) or not _text(event.get('description')):
                add(segment, 'event', '事件须有描述及有限、非负的计划时刻。', 'unverified')
        groups = {}
        for reaction in lists['reactions']:
            who = reaction['person_id']
            if any(key not in reaction for key in ('perceived_at', 'response', 'response_at')):
                add(segment, 'reaction_declaration', f'{who} 须明确填写感知时刻、反应及反应时刻；没有时填 null，不能省略。', 'unverified')
                continue
            event_id = reaction.get('event_id')
            event = events.get(event_id) if _text(event_id) else None
            if event is None:
                add(segment, 'reaction_event', f'{who} 的反应缺准确触发事件。', 'unverified')
                continue
            if not all(_text(reaction.get(k)) for k in ('prior_activity', 'perception_basis')):
                add(segment, 'perception_basis', f'{who} 缺原活动或看见/听见条件。', 'unverified')
            mode = reaction.get('perception')
            if not isinstance(mode, str) or mode not in {'seen', 'heard', 'not_perceived'}:
                add(segment, 'perception', f'{who} 的感知条件未知。', 'unverified')
                continue
            response, when = reaction.get('response'), reaction.get('response_at')
            if response is not None and not _text(response):
                add(segment, 'response', f'{who} 的反应须为具体动作或 null。', 'unverified')
                continue
            if mode == 'not_perceived':
                if response is not None or when is not None or reaction.get('perceived_at') is not None:
                    add(segment, 'unperceived_response', f'{who} 未感知事件却对它作出反应。')
                continue
            perceived = reaction.get('perceived_at')
            if not _time(perceived) or not _time(event.get('at')):
                add(segment, 'perception_time', f'{who} 的事件/感知时刻未核。', 'unverified')
                continue
            if perceived < event['at']:
                add(segment, 'early_perception', f'{who} 在触发事件之前已察觉。')
            if mode == 'heard' and not _text(event.get('audible_source')):
                add(segment, 'inaudible_event', f'{who} 靠听见反应，但触发事件未交代可听声源。')
            if response is None:
                if when is not None:
                    add(segment, 'response_time', f'{who} 无反应却填写反应时刻。')
                if not _text(reaction.get('no_visible_response_reason')):
                    add(segment, 'missing_response', f'{who} 已感知事件却无可见反应，也没有克制/僵住等具体依据。')
            elif not _time(when):
                add(segment, 'response_time', f'{who} 的反应时刻未核。', 'unverified')
            else:
                if when < perceived:
                    add(segment, 'early_response', f'{who} 先反应后感知。')
                groups.setdefault((reaction['event_id'], when, response.strip()), []).append(reaction)
        for group in groups.values():
            if len(group) > 1 and not all(_text(r.get('coordination_reason')) for r in group):
                add(segment, 'copied_reactions', '多人同一时刻、同一动作，须核对是否复制反应；合理齐声行为写具体共同动因。')
        objects = {}
        for item in lists['objects']:
            ident = item['object_id']
            before, after = item.get('before'), item.get('after')
            valid = all(isinstance(state, dict) and type(state.get('count')) is int and state['count'] >= 0
                        and _text(state.get('place')) and 'holder' in state
                        and (state['holder'] is None or _text(state['holder']))
                        for state in (before, after))
            if not valid:
                add(segment, 'object_state', f'{ident} 缺准确数量、位置或持有人。', 'unverified')
                continue
            # Only the declared supported attributes are compared.
            before = {k: before.get(k) for k in ('count', 'place', 'holder')}
            after = {k: after.get(k) for k in ('count', 'place', 'holder')}
            if any(state['count'] == 0 and state['holder'] is not None for state in (before, after)):
                add(segment, 'empty_holder', f'{ident} 数量为零却有持有人。')
            objects[ident] = (before, after)
            change = item.get('change')
            if before != after or 'change' in item:
                if (not isinstance(change, dict) or not _text(change.get('event_id'))
                        or change['event_id'] not in events):
                    add(segment, 'object_cause', f'{ident} 状态改变但没有对应来源事件。')
                    continue
                delta = change.get('count_delta')
                if type(delta) is not int or before['count'] + delta != after['count']:
                    add(segment, 'object_count', f'{ident} 数量变化与来源事件记录不一致。')
                if (before['place'], before['holder']) != (after['place'], after['holder']):
                    if not _text(change.get('path')):
                        add(segment, 'object_path', f'{ident} 改变位置/持有人却无取得或移动路径。')
                    if before['holder'] and after['holder'] and before['holder'] != after['holder'] and not _text(change.get('contact')):
                        add(segment, 'handover_contact', f'{ident} 交接缺接稳、承托和释放的可见过程。')
        sound = record.get('environment_sound')
        if (not isinstance(sound, dict) or not isinstance(sound.get('mode'), str)
                or sound['mode'] not in {'present', 'intentional_silence'}):
            add(segment, 'environment_sound', '环境声未核；缺声音安排不能默认无声。', 'unverified')
        elif sound['mode'] == 'intentional_silence':
            if not _text(sound.get('reason')):
                add(segment, 'silence_reason', '有意静音须有本场拍法依据。', 'unverified')
        elif (not isinstance(sound.get('sources'), list) or not sound['sources']
                or any(not isinstance(s, dict) or not all(_text(s.get(k)) for k in ('source', 'behavior')) for s in sound['sources'])):
            add(segment, 'sound_source', '环境声须说明声源及事件发生前后的保持或变化。', 'unverified')
        boundary = record.get('boundary')
        if (not isinstance(boundary, dict) or not isinstance(boundary.get('kind'), str)
                or boundary['kind'] not in {'start', 'continuous', 'time_jump', 'new_scene'}):
            add(segment, 'boundary', '跨镜接口未核。', 'unverified')
            previous = None
            continue
        states = []
        for key in ('entry_state', 'exit_state'):
            state = record.get(key)
            valid = isinstance(state, dict) and bool(state) and all(_text(k) and _text(v) for k, v in state.items())
            if not valid:
                add(segment, key, '需以同一空间/人物自身坐标写明确入态和尾态；未知不能省略。', 'unverified')
            states.append(state if valid else None)
        if boundary['kind'] == 'start':
            if previous is not None or boundary.get('from_segment_id') is not None:
                add(segment, 'reset_boundary', '不能在已有前段之后用 start 擅自重置连续状态。')
        else:
            if previous is None or boundary.get('from_segment_id') != previous['id']:
                add(segment, 'missing_predecessor', '缺准确相邻前段，不猜跨镜来源。', 'unverified')
            elif boundary['kind'] == 'continuous':
                if previous['exit'] is not None and states[0] is not None and previous['exit'] != states[0]:
                    add(segment, 'seam_state', '前段尾态与后段入态不一致或继承项漏列；核持物、方向、衣饰及动作相位。')
                if previous['objects'] != {k: v[0] for k, v in objects.items()}:
                    add(segment, 'seam_objects', '相邻段物件数量、位置或持有人不一致；出画不等于消失。')
            if boundary['kind'] in {'time_jump', 'new_scene'} and not _text(boundary.get('basis')):
                add(segment, 'boundary_basis', '时间省略/换场须有来源依据，不借标签解释瞬移。', 'unverified')
        previous = dict(id=segment, exit=states[1], objects={k: v[1] for k, v in objects.items()})
    return _result(findings)


def _result(findings):
    return {'verification': 'declared_plan_only', 'visual_quality_verified': False,
            'audio_quality_verified': False,
            'status': ('needs_revision' if any(f['status'] == 'needs_revision' for f in findings)
                       else 'unverified' if findings else 'consistent'),
            'findings': findings}
