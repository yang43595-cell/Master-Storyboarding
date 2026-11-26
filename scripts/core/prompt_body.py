"""Read submitted-body and segmented-prompt fields without guessing authority."""

import math
import re

SOURCE_SEGMENT_FIELDS = ("generation_segments", "creative_sections", "independent_candidate_ranges")


_INTERNAL_BODY = (
    (re.compile(r"与(?:上|前一)段不连续|(?:视频)?模型没有上下文|(?:接|承接|沿用)(?:上一|前一|上)段|见导演包|同上"),
     "出现生成提示词元话语；改为可直接生成的具体画面"),
    (re.compile(r"(?<![A-Za-z0-9_-])(?:(?:DLG|AST|STP|ART)-[A-Z0-9-]+|S\d{2}-\d{3})(?![A-Za-z0-9_-])"),
     "混入内部对白、镜头或资产编号"),
    (re.compile(r"[A-Za-z]:[\\/]|/(?:Users|home|tmp)/"),
     "混入本地文件路径；改用实际上传引用标记"),
    (re.compile(r"(?:^|\n)[^\n]{0,20}(?:完成条件|自查结论|验收清单)[：:]"),
     "混入内部完成条件或自查清单；移到制作备注"),
)

QUOTED_TEXT_RE = re.compile(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|《[^》]*》|"[^"\n]*"')
_UNQUOTED_TEXT_TURN = re.compile(
    r'(?:(?:说|问|答|喊|念)(?:道)?|旁白|画外音|自言自语)[：:]'
    r'[ \t]*(?![ \t\0])[^。.!?！？；;\n]+'
)
_SENTENCE_END = '。.!?！？'
_QUALITY_HEADING = r'(?:整体风格|风格(?:要求)?|画质(?:要求)?|动作(?:要求)?|表演要求)[：:][ \t]*'
_QUOTED_QUALITY_VALUES = re.compile(
    rf'(?P<heading>{_QUALITY_HEADING})(?P<values>(?:[ \t、，,]*(?:{QUOTED_TEXT_RE.pattern}))+)'
)
_STANDALONE_QUALITY = re.compile(
    rf'(?:{_QUALITY_HEADING})?'
    r'(?:(?:全片|全段|整体|保持|确保|呈现)\s*)*'
    r'(?P<phrase>动作(?:自然(?:且?连贯)?|连贯|流畅)|画质\s*[：:]?\s*(?:真实|逼真)|'
    r'产品质地特写真实|真人实拍感|高级感|电影感|松弛感)'
)


def standalone_quality_issues(body: str) -> list[str]:
    """Catch known filler clauses only. Connected detail still needs a source review."""
    # A quoted entry under an explicit style/quality heading is an instruction.
    body = _QUOTED_QUALITY_VALUES.sub(
        lambda match: match['heading'] + '、'.join(
            quote[0][1:-1] for quote in QUOTED_TEXT_RE.finditer(match['values'])
        ), body,
    )
    # Preserve boundaries when masking remaining dialogue and literal names or signage.
    visible = QUOTED_TEXT_RE.sub(
        lambda match: '\0' + (match[0][-2] if match[0][-2] in _SENTENCE_END else ''), body
    )
    visible = _UNQUOTED_TEXT_TURN.sub('\0', visible)
    phrases = {}
    for clause in re.split(r'[。.!?！？；;，,、\n]', visible):
        match = _STANDALONE_QUALITY.fullmatch(clause.strip())
        if match:
            phrases[match['phrase']] = None
    return [
        f'正文含独立泛词「{phrase}」，删去无新增信息的词，或按来源补足可见动作、光线或材质'
        for phrase in phrases
    ]


def submitted_body_issues(body: str) -> list[str]:
    """Check one actual generated-prompt body at adoption and delivery."""
    if not body.strip():
        return ["缺少非空、完整的提交正文"]
    issues = [issue for pattern, issue in _INTERNAL_BODY if pattern.search(body)]
    return issues + generated_overlay_issues(body) + standalone_quality_issues(body)


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
    # Do not mistake a character talking about subtitles for an overlay request.
    visible = re.sub(r'(?:说|问|答|喊|念|旁白)[：:]\s*[“「"]([^”」"]*)[”」"]', '', body)
    target = r'(?:静默文字|无声文字|时间字|小字|字幕|片尾文字|片头文字|画面文字|屏幕文字|叠加文字|说明字卡|标题卡|字卡|时间码|倒计时|时间描述)'
    action = r'(?:出现|显示|叠加|浮现|打出|生成|呈现|写上|加上|添加|闪出|标出|加入|淡入|标)'
    # Keep negation bound to the text/action it modifies. Never let an unrelated
    # “人物不动” or “不要改变机位” cancel a subsequent overlay instruction.
    modifiers = r'(?:\s|再|重新|自动|额外|任何|新的|新|其他|全部|一律|均|都|画外|在画面(?:上|中)?|在屏幕(?:上|中)?)*'
    negative = r'(?:不(?:需要|需|必|应当|应该|应)?|禁止|不得|不要|无需|不可)'
    negated_prefix = negative + modifiers + r'(?:(?:' + action + r'|渲染)' + modifiers + r')*'
    device_screen = r'(?:手机|电脑|平板|电视|监视器)(?:的)?(?:屏幕|显示器)(?:内|上|中)?'

    def negated_action(prefix):
        return bool(re.search(negated_prefix + r'$', prefix))

    issues = []
    visible = re.sub(r'[“「"]([^”」"]*)[”」"]', '“内容”', visible)
    for clause in re.split(r'[。！？；;，,\n]|但是|但|然而|而是|却|并且|并(?!行)|同时|然后', visible):
        for match in re.finditer(target, clause):
            before, after = clause[:match.start()], clause[match.end():]
            intrinsic = re.search(r'(?:包装|商标|道具|实物|招牌|盒子|盒)(?:上|的|原有|固有){0,3}$', before)
            forbidden = (intrinsic or
                         re.search(negated_prefix + r'(?:(?:' + target + r'|片头|片尾|文字|其他)(?:、|或|及|和)*)*$', before)
                         or re.match(r'\s*(?:均|都|一律)?' + negative + modifiers + r'(?:' + action + r'|渲染)', after)
                         or re.search(r'(?:^|[：:])\s*无(?!声)' + modifiers + r'$', before))
            if not forbidden and (re.search(action, before[-16:]) or re.search(action, after[:16])):
                issues.append('生成正文含叠加文字指令；原文、顺序与时机应移至正文外的后期安排')
        # Device-anchored UI is intrinsic text, not a blanket screen exemption.
        # Explicit 字幕/字卡 above and overlay verbs still remain checked.
        for display in re.finditer(r'(?:画面|屏幕|片尾|片头).{0,8}?(?P<action>'+action+r')\s*(?:[：:]?\s*[“「"]|文字)', clause):
            prefix = clause[:display.start('action')]
            device_ui = (display.group('action') in {'显示', '呈现', '出现'}
                         and re.search(device_screen + r'$', prefix))
            if not device_ui and not negated_action(prefix):
                issues.append('生成正文含画面显示文字指令；只生成底画面，文字交后期')
    return list(dict.fromkeys(issues))


def _valid_edit_window(start, end):
    return (type(start) in (int, float) and type(end) in (int, float)
            and math.isfinite(start) and math.isfinite(end) and 0 <= start < end)


def _check_segment_scope(scope, segments):
    """Cross-check the existing authored scope without treating it as segment prose."""
    if not isinstance(scope, dict):
        return
    from .quality_evidence import source_shot_ids
    full = scope.get('type') == 'full_local_authored'
    scoped_ranges = scope.get('independent_ranges')
    ids = [row['segment_id'] for row in segments]
    if 'authored_segment_count' in scope and (type(scope['authored_segment_count']) is not int
            or scope['authored_segment_count'] != len(ids)):
        raise ValueError('源稿审阅声明的生成段总数与实际生成段不一致')
    if 'authored_shot_count' in scope:
        actual = len({shot for row in segments for shot in source_shot_ids(row)})
        if type(scope['authored_shot_count']) is not int or scope['authored_shot_count'] != actual:
            raise ValueError('源稿审阅声明的镜号总数与实际生成段不一致')
    if full and scope.get('unwritten_shot_ids'):
        raise ValueError('源稿审阅声明全片，但仍有未写入的镜号')
    if 'scope_seconds' in scope:
        windows = [(row.get('edit_in_seconds'), row.get('edit_out_seconds')) for row in segments]
        declared = scope['scope_seconds']
        if (not _valid_edit_window(0, declared) or any(not _valid_edit_window(*window) for window in windows)
                or not math.isclose(declared, sum(end - start for start, end in windows), abs_tol=1e-6)):
            raise ValueError('源稿审阅声明的剪辑总时长与实际生成段不一致')
    if scoped_ranges is None:
        return
    if (not isinstance(scoped_ranges, list) or any(not isinstance(span, dict)
            or not isinstance(span.get('segment_ids'), list) or not span['segment_ids']
            for span in scoped_ranges)
            or [ident for span in scoped_ranges for ident in span['segment_ids']] != ids):
        raise ValueError('源稿审阅声明的范围与生成段顺序或覆盖不一致')
    by_id = {row['segment_id']: row for row in segments}
    for span in scoped_ranges:
        selected = [by_id[ident] for ident in span['segment_ids']]
        if 'shot_ids' in span and span['shot_ids'] != list(dict.fromkeys(
                shot for row in selected for shot in source_shot_ids(row))):
            raise ValueError('源稿审阅声明的范围镜号与生成段不一致')
        if 'edit_range_seconds' in span and span['edit_range_seconds'] != [
                selected[0].get('edit_in_seconds'), selected[-1].get('edit_out_seconds')]:
            raise ValueError('源稿审阅声明的范围时码与生成段不一致')


def source_review_segments(business):
    """Read all source segments in edit order; do not infer execution or approval."""
    if not isinstance(business, dict):
        raise ValueError('源稿审阅 JSON 顶层不是对象')
    if 'drafts' in business:
        raise ValueError('源稿审阅须输入原业务生成段，不以 drafts 传输快照替代')
    sections = business.get('creative_sections')
    if 'generation_segments' in business:
        segments = business['generation_segments']
    elif isinstance(sections, list) and sections and all(
            isinstance(s, dict) and isinstance(s.get('generation_segments'), list)
            and s['generation_segments'] for s in sections):
        segments = [row for section in sections for row in section['generation_segments']]
    else:
        raise ValueError('源稿审阅缺少完整 generation_segments')
    if not isinstance(segments, list):
        raise ValueError('源稿审阅生成段或 segment_id 不完整')
    ranges = business.get('independent_candidate_ranges', [])
    if (not isinstance(ranges, list) or any(not isinstance(span, dict)
            or not isinstance(span.get('generation_segments'), list) or not span['generation_segments']
            for span in ranges)):
        raise ValueError('源稿审阅独立候选范围缺真实 generation_segments')
    segments = [*segments, *(row for span in ranges for row in span['generation_segments'])]
    if not segments or any(
            not isinstance(row, dict) or not isinstance(row.get('segment_id'), str)
            or not row['segment_id'].strip() for row in segments):
        raise ValueError('源稿审阅生成段或 segment_id 不完整')
    ids = [row['segment_id'] for row in segments]
    if len(set(ids)) != len(ids):
        raise ValueError('源稿审阅 segment_id 重复')
    scope = business.get('scope')
    if ranges:
        if any(not _valid_edit_window(row.get('edit_in_seconds'), row.get('edit_out_seconds'))
               for row in segments):
            raise ValueError('源稿审阅独立候选范围须提供所有生成段的绝对剪辑起止时码')
        segments.sort(key=lambda row: row['edit_in_seconds'])
        full = isinstance(scope, dict) and scope.get('type') == 'full_local_authored'
        for left, right in zip(segments, segments[1:]):
            gap = right['edit_in_seconds'] - left['edit_out_seconds']
            if gap < -1e-6 or (full and gap > 1e-6):
                raise ValueError('源稿审阅独立候选范围存在时码重叠或全片空档')
    if 'generation_segments' in business and sections is not None:
        if (not isinstance(sections, list) or any(not isinstance(s, dict)
                or not isinstance(s.get('generation_segments'), list) for s in sections)):
            raise ValueError('源稿审阅创作段引用格式不完整')
        links = [ident for section in sections for ident in section['generation_segments']]
        if any(not isinstance(ident, str) for ident in links) or len(links) != len(ids) or set(links) != set(ids):
            raise ValueError('源稿审阅顶层生成段与创作段引用不一致')
    _check_segment_scope(scope, segments)
    return segments
