"""Bounded offline checks; never submit a video or modify an existing canvas."""
import copy
import json
import pytest

from scripts.core.prompt_body import generated_overlay_issues, submitted_body_issues
from scripts.canvas_readback import compare, digest, validate_submission
from scripts.tests.test_delivery_preflight import delivery, bind_synthetic_quality

@pytest.mark.parametrize('body', [
    '片尾文字依次静默出现：“谢谢观看”。',
    '字幕显示“你好”。',
    '画面出现“十年后”。',
    '叠加说明字卡：“请稍候”。',
    '画面显示时间码。',
    '屏幕淡入“第二天”。',
    '静默文字“稍早｜另一座商场”在开场约两秒内显示。',
    '无声时间字“敷膜15–20分钟后”与动作并行显示。',
    '无声小字标“顺时针混合40秒后”。',
    '静默文字“敷用15–20分钟后”完整显示约两秒。',
])
def test_overlay_is_flagged(body):
    assert generated_overlay_issues(body)

@pytest.mark.parametrize('body', [
    '生成画面不添加字幕、片头或片尾文字、说明字卡、时间描述、时间码或其他叠加文字。',
    '包装上的文字及商标按真实参考保留，不改品牌。',
    '角色说：“字幕显示得很好”。',
    '0–3秒：人物走入；第3秒切近景。',
    '镜头停留约半秒后切近景。',
    '镜头停留2秒；尺寸为30毫米。',
    '两个人静静坐在餐桌边，没有新增画面元素。',
    '9:16 竖屏，16:9 横屏参考只用于身份。',
    '包装上的小字不重新生成，按真实参考保留。',
])
def test_negative_constraint_real_labels_spoken_words_and_timing_are_not_overlays(body):
    assert not generated_overlay_issues(body)

@pytest.mark.parametrize('host', ['xiaoyunque.com', 'www.xiaoyunque.com', 'xyq.jianying.com'])
@pytest.mark.parametrize('resolution', ['720P', '1080p', '', None])
def test_xiaoyunque_submission_rejects_wrong_resolution_without_writes(tmp_path, resolution, host):
    frozen, _ = delivery(tmp_path)
    frozen['target_url'] = f'https://{host}/project?projectId=fixture'
    frozen['drafts'][0]['parameters']['resolution'] = resolution
    before = copy.deepcopy(frozen)
    with pytest.raises(ValueError, match='480p'):
        validate_submission(frozen, tmp_path)
    assert frozen == before


@pytest.mark.parametrize('host', ['xiaoyunque.com', 'www.xiaoyunque.com', 'xyq.jianying.com'])
def test_480p_passes_and_historical_720p_comparison_is_preserved(tmp_path, host):
    frozen, got = delivery(tmp_path)
    frozen['target_url'] = f'https://{host}/project?projectId=fixture'
    for row in frozen['drafts']:
        row['parameters']['resolution'] = '480P'
    from scripts.core import artifacts
    manifest_path = tmp_path / artifacts.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text())
    for item in manifest['artifacts']:
        for binding in item.get('upload_bindings', []):
            binding['target_url'] = frozen['target_url']
    manifest_path.write_text(json.dumps(manifest))
    bind_synthetic_quality(tmp_path, frozen)
    assert validate_submission(frozen, tmp_path)['ok']
    from scripts.tests.test_canvas_readback import sample
    frozen, _ = sample()
    frozen['target_url'] = f'https://{host}/project?projectId=fixture'
    for row in frozen['drafts']:
        row['parameters']['resolution'] = '720P'
    got = copy.deepcopy(frozen)
    got.update(phase='after_refresh', frozen_sha256=digest(frozen))
    for row in got['drafts']:
        row['native_mentions'] = [{**m, 'entity_type':'fixture-chip', 'native_noneditable_entity':True}
                                  for m in row['ordered_media']]
    assert compare(frozen, got)['ok']


def test_submission_rejects_overlay_in_body(tmp_path):
    frozen, _ = delivery(tmp_path)
    frozen['drafts'][0]['prompt'] = '片尾文字依次静默出现：“谢谢观看”。'
    with pytest.raises(ValueError, match='叠加文字'):
        validate_submission(frozen, tmp_path)


@pytest.mark.parametrize('host', ['other.jianying.com', 'xyq.jianying.com.example.invalid'])
def test_480p_rule_is_not_applied_to_other_hosts(tmp_path, host):
    frozen, _ = delivery(tmp_path)
    frozen['target_url'] = f'https://{host}/project?projectId=fixture'
    from scripts.core import artifacts
    manifest_path = tmp_path / artifacts.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text())
    for item in manifest['artifacts']:
        for binding in item.get('upload_bindings', []):
            binding['target_url'] = frozen['target_url']
    manifest_path.write_text(json.dumps(manifest))
    for row in frozen['drafts']:
        row['parameters']['resolution'] = '720P'
    bind_synthetic_quality(tmp_path, frozen)
    assert validate_submission(frozen, tmp_path)['ok']


def test_add_overlay_verb_regression():
    from scripts.core.prompt_body import generated_overlay_issues
    assert generated_overlay_issues('添加字幕：你好。')
    assert not generated_overlay_issues('不要添加字幕。')
    assert not generated_overlay_issues('女主说：“添加字幕”。')


@pytest.mark.parametrize('body', [
    '人物不动并添加字幕：你好。',
    '不要改变机位但添加字幕：你好。',
    '人物不动添加字幕：你好。',
    '不需要改变机位但添加字幕：你好。',
    '不要改变机位，字幕显示你好。',
    '不要添加字幕但显示片尾文字。',
    '字幕不显示但片尾文字浮现。',
    '不改变机位，屏幕显示“你好”。',
    '人物不动并且画面出现“你好”。',
    '不生成字幕而是添加时间字。',
    '不要改变机位但屏幕显示“你好”。',
    '0-3秒女主说话；时间仅用于动作控制，不生成任何文字。但添加字幕：你好。',
])
def test_unrelated_or_contrasted_negative_does_not_cancel_overlay(body):
    assert generated_overlay_issues(body)


@pytest.mark.parametrize('body', [
    '不要额外添加字幕。',
    '字幕不需要添加。',
    '不需要添加字幕。',
    '画面不需要显示“你好”。',
    '人物不动并且不要添加字幕。',
    '字幕不自动生成。',
    '不要添加字幕、片头或片尾文字、说明字卡。',
    '画面不要显示“你好”。',
    '屏幕不额外显示“你好”。',
    '女主说：“不要改变机位但添加字幕”。',
    '包装上的小字按参考显示。',
    '0-3秒女主说话；时间仅用于动作控制，不改变机位。',
    '0-3秒女主说话；人物不动，不显示表情。',
    '0-3秒女主说话；时间仅用于动作控制，不生成任何文字。',
    '0-3秒女主说话；时间只用于切镜，不在画面显示。',
    '0-3秒女主说话；时间标记仅用于动作控制，不渲染文字。',
])
def test_scoped_text_negation_and_timing_control_are_allowed(body):
    assert not generated_overlay_issues(body)


@pytest.mark.parametrize('body', [
    '不叠加画外字幕。',
    '画面不添加画外字幕、片尾文字。',
    '显示器内问题与答案按所配参考呈现，不叠加画外字幕。',
    '手机屏幕显示：“通话中”。',
    '电脑显示器内显示：“审核中”。',
    '手机屏幕上显示文字：“通话中”。',
    '画面不添加叠加字幕。',
    '画面不添加叠加文字。',
])
def test_offscreen_subtitle_negation_is_local_and_allowed(body):
    assert not generated_overlay_issues(body)


@pytest.mark.parametrize('body', [
    '不叠加画外字幕，同时添加字幕：你好。',
    '不叠加画外字幕，但显示片尾字卡。',
    '不叠加画外字幕。画面添加说明字卡。',
    '人物看画外，同时添加字幕。',
    '不看画外，添加字幕。',
    '画面浮现文字。',
    '手机屏幕上叠加字幕：“通话中”。',
    '手机屏幕上浮现：“通话中”。',
    '手机屏幕显示：“通话中”并添加字幕：“稍后”。',
    '手机屏幕显示：“通话中”，同时画面浮现文字：“稍后”。',
    '手机屏幕显示：“通话中”。屏幕显示：“稍后”。',
    '手机屏幕显示：“通话中”。画面显示时间码。',
])
def test_offscreen_modifier_does_not_hide_affirmative_overlay(body):
    assert generated_overlay_issues(body)


@pytest.mark.parametrize('body,phrases', [
    ('动作自然，画质真实。', ('动作自然', '画质真实')),
    ('人物低头看杯子。镜头稳定，动作自然连贯，真人实拍感。', ('动作自然连贯', '真人实拍感')),
    ('结束在发泡完成、刷子提起。动作连贯，产品质地特写真实，真人实拍感。',
     ('动作连贯', '产品质地特写真实', '真人实拍感')),
    ('整体风格：真人实拍感。\n画质要求：画质真实。', ('真人实拍感', '画质真实')),
    ('整体风格：「动作自然」「画质真实」。', ('动作自然', '画质真实')),
    ('女一问：“画质真实？” 动作自然。', ('动作自然',)),
])
def test_submitted_body_flags_independent_filler_clauses(body, phrases):
    issues = submitted_body_issues(body)
    assert len(issues) == len(phrases)
    assert all('独立泛词' in issue and f'「{phrase}」' in issue
               for issue, phrase in zip(issues, phrases))


@pytest.mark.parametrize('body', [
    '女一轻声说：“动作自然，画质真实。”',
    '女一轻声说：“整体风格：「动作自然」「画质真实」。”',
    '女一说：动作自然，画质真实。她把名牌放在桌上。',
    '女一倒茶，墙上挂着《画质真实》。',
    '门旁的牌子上印着「动作自然」。',
    '镜头保持固定，窗外自然光从右侧照到人物的指尖。',
    '女一的动作自然地从抬手衔接到把杯子移到窗台上，手指始终贴住杯柄。',
    '动作自然连贯地延续到把道具放回桌面。',
    '参考真人实拍的窗光和肤色，保留阳光照到面颊时的毛孔纹理。',
    '产品质地特写：刷毛提起后，奶白泡沫在刷尖形成短峰。',
])
def test_submitted_body_keeps_observable_directions_and_literal_text(body):
    assert not submitted_body_issues(body)


def test_formal_redframe_preflight_rejects_standalone_filler(tmp_path):
    from scripts.tests.test_workflow_repair_cli import redframe_fixture, bind_redframe_production_sources

    frozen, observed = redframe_fixture(tmp_path)
    frozen['drafts'][0]['prompt'] += ' 动作自然，画质真实。'
    bind_redframe_production_sources(tmp_path, frozen, observed)
    with pytest.raises(ValueError, match='one: 正文含独立泛词「动作自然」'):
        validate_submission(frozen, tmp_path, formal_delivery=True, preflight=True)
