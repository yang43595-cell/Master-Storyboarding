"""Read the two existing submitted-body fields without guessing authority."""


def submitted_prompt_body(row: dict) -> str:
    """Preserve source text; invalid/missing bodies are empty, conflicts reject.

    Only explicit body fields count. A valid alias cannot hide an invalid one,
    and two populated aliases must match exactly (including whitespace).
    """
    values = [row[key] for key in ("prompt_text", "prompt") if key in row]
    if not values or any(not isinstance(value, str) or not value.strip() for value in values):
        return ""
    if len(values) == 2 and values[0] != values[1]:
        raise ValueError("prompt_text 与 prompt 正文不一致，不猜权威正文")
    return values[0]


def generated_overlay_issues(body: str) -> list[str]:
    """Flag known overlay directives, not every mention of text or real labels.

    This bounded text check cannot certify model compliance or visual quality.
    Spoken quotes and real packaging/scene labels are not overlay instructions.
    Negation applies only to a nearby text/action phrase, not any earlier 不.
    This is a finite Chinese directive heuristic, not a general semantic parser.
    """
    import re
    # Do not mistake a character talking about subtitles for an overlay request.
    visible = re.sub(r'(?:说|问|答|喊|念|旁白)[：:]\s*[“「"]([^”」"]*)[”」"]', '', body)
    target = r'(?:静默文字|无声文字|时间字|小字|字幕|片尾文字|片头文字|画面文字|屏幕文字|叠加文字|说明字卡|标题卡|字卡|时间码|倒计时|时间描述)'
    action = r'(?:出现|显示|叠加|浮现|打出|生成|呈现|写上|加上|添加|闪出|标出|加入|淡入|标)'
    # Keep negation bound to the text/action it modifies. Never let an unrelated
    # “人物不动” or “不要改变机位” cancel a subsequent overlay instruction.
    modifiers = r'(?:\s|再|重新|自动|额外|任何|新的|新|其他|全部|一律|均|都|画外|在画面(?:上|中)?|在屏幕(?:上|中)?)*'
    negative = r'(?:不(?:需要|需|必|应当|应该|应)?|禁止|不得|不要|无需|不可)'

    def negated_action(prefix):
        return bool(re.search(negative + modifiers + r'$', prefix))

    issues = []
    visible = re.sub(r'[“「"]([^”」"]*)[”」"]', '“内容”', visible)
    for clause in re.split(r'[。！？；;，,\n]|但是|但|然而|而是|却|并且|并(?!行)|同时|然后', visible):
        for match in re.finditer(target, clause):
            before, after = clause[:match.start()], clause[match.end():]
            intrinsic = re.search(r'(?:包装|商标|道具|实物|招牌|盒子|盒)(?:上|的|原有|固有){0,3}$', before)
            forbidden = (intrinsic or
                         re.search(negative + modifiers + r'(?:' + action + r'|渲染)?' + modifiers + r'(?:(?:' + target + r'|片头|片尾|文字|其他)(?:、|或|及|和)*)*$', before)
                         or re.match(r'\s*(?:均|都|一律)?' + negative + modifiers + r'(?:' + action + r'|渲染)', after)
                         or re.search(r'(?:^|[：:])\s*无(?!声)' + modifiers + r'$', before))
            if not forbidden and (re.search(action, before[-16:]) or re.search(action, after[:16])):
                issues.append('生成正文含叠加文字指令；原文、顺序与时机应移至正文外的后期安排')
        # Also catch quoted display text that omits the word 字幕/字卡.
        for display in re.finditer(r'(?:画面|屏幕|片尾|片头).{0,8}?(?P<action>'+action+r')\s*[：:]?\s*[“「"]', clause):
            if not negated_action(clause[:display.start('action')]):
                issues.append('生成正文含画面显示文字指令；只生成底画面，文字交后期')
    timed = re.search(r'(?:\d+(?:\.\d+)?|[零一二三四五六七八九十百两半]+)\s*(?:秒|s\b)|(?<!\d)\d{2}:\d{2}(?::\d{2})?(?!\d)', visible, re.I)
    # A timing-only declaration may put its no-text instruction after a comma.
    # Do not cross sentence/contrast boundaries or accept unrelated negatives.
    timing = r'(?:秒数|时间标记|切镜时间|时间指令|时间码|时间)'
    guard = re.search(
        timing + r'[^。！？\n但却；;]{0,70}?' + negative + modifiers +
        r'(?:显示|渲染|出现)(?:为)?(?:文字)?(?:[。！？；;，,\n]|$)', visible)
    guard = guard or re.search(
        timing + r'(?:仅|只)(?:用于|用来|控制)[^。！？\n但却；;]{0,35}' +
        r'(?:动作|切镜|剪辑)[^。！？\n但却；;]{0,20}[，,]\s*' + negative +
        modifiers + r'(?:生成|显示|渲染|出现)' + modifiers +
        r'(?:文字|字幕|时间字|时间码)(?:[。！？；;，,\n]|$)', visible)
    if timed and not guard:
        issues.append('正文含秒数或时间码，须明确仅控制动作/切镜，不在画面显示')
    return list(dict.fromkeys(issues))
