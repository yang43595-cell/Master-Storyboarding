from pathlib import Path

import pytest

from scripts.check_video_prompt_focal_intent import inspect_document


def test_accepts_qualitative_focal_intent_without_millimetres(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("""## 第 1 段：测试

### 提示词

焦段倾向：标准与中长焦。光学目的：保持两人关系自然，并把表情从背景中分离。
""", encoding="utf-8")

    assert inspect_document(prompt) == (1, [])


def test_rejects_missing_focal_intent_and_unattributed_millimetres(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("""## 第 1 段：测试

### 提示词

视频模型没有上下文。固定 50mm 拍摄人物。
""", encoding="utf-8")

    count, errors = inspect_document(prompt)
    assert count == 1
    assert any("定性焦段倾向" in error for error in errors)
    assert any("光学目的" in error for error in errors)
    assert any("未标注来源的精确毫米数" in error for error in errors)
    assert any("生成提示词元话语" in error for error in errors)


def test_current_merged_segment_reads_only_submitted_body(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("""## 生成段 17–18（合并）｜入场

素材：@图片1；C:/Users/example/portrait.png；AST-CHR-FATHER。

### 模型正文（待审试写，不可直接上传）

```text
标准焦段保留人物和门的自然比例。硬切另一地点的会议室，中长焦突出表情。
```

制作备注：完成条件：按DLG-001检查。视频模型没有上下文。

## 生成段 19｜反击

### 提交正文

```text
中长焦把两张脸从后景分离，人物关系清楚。
```
""", encoding="utf-8-sig")
    assert inspect_document(prompt) == (2, [])


@pytest.mark.parametrize("body, expected", [
    ("固定中景，标准焦段。", "光学目的"),
    ("标准焦段保留空间关系。完成条件：八镜各一次。", "内部完成条件"),
    ("中长焦突出脸。\nDLG-012只由二叔说。", "内部对白"),
    ("中长焦突出脸，镜号S05-030。", "内部对白"),
    (r"中长焦突出脸，参考C:\Users\example\face.png。", "本地文件路径"),
    ("中长焦突出脸。与上段不连续，视频模型没有上下文。", "元话语"),
    ("中长焦突出脸。接上一段的动作。", "元话语"),
    ("固定85mm，突出脸。", "未标注来源"),
    ("85mm中长焦突出脸。", "未标注来源"),
])
def test_known_body_failures_are_reported(tmp_path: Path, body: str, expected: str) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text(f"## 生成段 7｜测试\n\n### 提交正文\n\n```text\n{body}\n```\n", encoding="utf-8")
    count, errors = inspect_document(prompt)
    assert count == 1
    assert any(expected in error for error in errors)


@pytest.mark.parametrize("section", [
    "## 生成段 7｜缺正文\n制作备注：中长焦突出表情。",
    "## 生成段 7｜空正文\n### 提交正文\n```text\n\n```\n",
    "## 生成段 7｜不完整\n### 提交正文\n```text\n中长焦突出表情。\n",
])
def test_missing_or_broken_body_does_not_pass(tmp_path: Path, section: str) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text(section, encoding="utf-8")
    count, errors = inspect_document(prompt)
    assert count == 1
    assert any("提交正文" in error for error in errors)


def test_metadata_does_not_supply_missing_focal_intent(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("""## 生成段 1｜测试
摄影表达：中长焦突出表情。
### 提交正文
```text
她看向门口。
```
""", encoding="utf-8")
    count, errors = inspect_document(prompt)
    assert count == 1
    assert any("定性焦段倾向" in error for error in errors)


def test_focal_source_can_remain_outside_model_body(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("""## 生成段 1｜测试
### 提交正文
```text
85mm中长焦突出面部，与环境后景分离。
```
制作备注：焦距来源：已确认导演镜头原值85mm。
""", encoding="utf-8")
    assert inspect_document(prompt) == (1, [])


def test_no_segments_is_an_explicit_failure(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# 制作说明\n中长焦突出表情。", encoding="utf-8")
    count, errors = inspect_document(prompt)
    assert count == 0
    assert errors


def test_actual_submission_txt_can_be_checked(tmp_path: Path) -> None:
    prompt = tmp_path / "submitted.txt"
    prompt.write_text("中长焦突出她的脸，让面容从背景中分离。", encoding="utf-8-sig")
    assert inspect_document(prompt) == (1, [])


@pytest.mark.parametrize('body', [
    '二维圆形向右平移后停住。',
    '固定中景，人物自然交流。',
    '标准焦段保持自然比例，展示厚5mm的零件。',
    '标准焦段保持自然比例，长120毫米的螺丝置于桌上。',
])
def test_irrelevant_optics_and_object_dimensions_are_not_errors(tmp_path, body):
    p = tmp_path / 'actual.txt'
    p.write_text(body, encoding='utf-8')
    assert inspect_document(p) == (1, [])


def test_explicit_upstream_optical_requirement_still_blocks_omission(tmp_path):
    from scripts.check_video_prompt_focal_intent import main
    p = tmp_path / 'actual.txt'
    p.write_text('她站在门边。', encoding='utf-8')
    assert inspect_document(p) == (1, [])
    assert inspect_document(p, require_focal_intent=True)[1]
    assert main([str(p), '--require-focal-intent']) == 1
    p.write_text('中长焦压缩远处背景并把脸从人群中分离。', encoding='utf-8')
    assert main([str(p), '--require-focal-intent']) == 0


def test_unrecognized_format_is_unchecked_not_content_failure(tmp_path, capsys):
    from scripts.check_video_prompt_focal_intent import main
    p = tmp_path / 'free.md'
    p.write_text('# 片段一\n她走进房间。', encoding='utf-8')
    assert main([str(p)]) == 2
    assert 'UNCHECKED' in capsys.readouterr().out
    p.write_text('## 生成段 1\n### 提交正文\n', encoding='utf-8')
    assert main([str(p)]) == 1


@pytest.mark.parametrize('body', ['直径49mm的镜头盖放在桌上。', '49mm的镜头盖放在桌上。', '镜头口径49mm。'])
def test_lens_accessory_dimensions_are_not_focal_lengths(tmp_path, body):
    p = tmp_path / 'lens-cap.txt'
    p.write_text(body, encoding='utf-8')
    assert inspect_document(p) == (1, [])


# Representative R18/candidate layout: these are sibling model-visible sections,
# not a new required production schema or a dependency on external deliverables.
MODERN_PROMPT = """## 提示词一 金额质问
### 素材职责
@丈夫人物板用于身份与服装，不继承白底。
### 公共条件
厨房灶台在左，入口在右；持续煎炒声，没有音乐。
### 连续视听正文
丈夫肩上近景，中长焦倾向，双眼清楚，旧门框仍可辨。
他抬眼问：“1000多？！”随后切到妻子。
### 制作备注
视频模型没有上下文。C:/Users/example/face.png；DLG-001。
## 提示词二 反问后继续劳动
### 公共条件
妻子仍站在灶前。
### 连续视听正文
她低头翻炒，锅铲声停后切下一场。
"""


def test_three_sibling_model_sections_and_notes_remain_separate(tmp_path):
    from scripts.check_video_prompt_focal_intent import _segments
    p = tmp_path / 'r18.md'
    p.write_text(MODERN_PROMPT, encoding='utf-8')
    segments = _segments(MODERN_PROMPT)
    assert len(segments) == 2
    assert all(part in segments[0][1] for part in ('@丈夫人物板', '持续煎炒声', '随后切到妻子'))
    assert 'DLG-001' not in segments[0][1]
    assert inspect_document(p) == (2, [])
    p.write_text(MODERN_PROMPT.replace('### 素材职责', '### 提交正文\n### 素材职责'), encoding='utf-8')
    assert inspect_document(p) == (2, [])


@pytest.mark.parametrize('target', ['@丈夫人物板', '持续煎炒声', '随后切到妻子'])
def test_leaks_anywhere_in_three_sections_are_checked(tmp_path, target):
    p = tmp_path / 'r18.md'
    p.write_text(MODERN_PROMPT.replace(target, target + '。DLG-012'), encoding='utf-8')
    count, errors = inspect_document(p)
    assert count == 2
    assert any('内部对白' in e for e in errors)


@pytest.mark.parametrize('action', ['', '### 连续视听正文\n', '### 连续视听正文\n```text\n未闭合正文'])
def test_new_layout_missing_action_body_still_fails(tmp_path, action):
    p = tmp_path / 'empty.md'
    p.write_text('## 提示词一 缺正文\n### 公共条件\n标准焦段保持自然比例。\n' + action, encoding='utf-8')
    count, errors = inspect_document(p)
    assert count == 1
    assert any('提交正文' in e for e in errors)


@pytest.mark.parametrize('layout', ['top', 'nested', 'linked', 'legacy'])
def test_json_existing_business_body_layouts(tmp_path, layout):
    import json
    row = {'segment_id': 'OPEN-001', 'prompt_text': '中长焦突出人物，与背景分离。',
           'notes': '视频模型没有上下文。DLG-001。C:/Users/example/ref.png'}
    if layout == 'nested':
        data = {'creative_sections': [{'generation_segments': [row]}]}
    elif layout == 'legacy':
        data = {'drafts': [{'node_id': 'OPEN-001', 'prompt': row['prompt_text'], 'notes': row['notes']}]}
    else:
        data = {'generation_segments': [row]}
        if layout == 'linked':
            data['creative_sections'] = [{'generation_segments': ['OPEN-001']}]
    p = tmp_path / 'prompts.json'
    p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    assert inspect_document(p) == (1, [])


def test_json_body_not_notes_supplies_optics_and_leak_checks(tmp_path):
    import json
    p = tmp_path / 'prompts.json'
    row = {'segment_id': 'OPEN-001', 'prompt_text': '她转身。DLG-001。',
           '摄影表达': '中长焦突出表情。'}
    p.write_text(json.dumps({'generation_segments': [row]}, ensure_ascii=False), encoding='utf-8')
    count, errors = inspect_document(p)
    assert count == 1
    assert any('定性焦段倾向' in e for e in errors)
    assert any('光学目的' in e for e in errors)
    assert any('内部对白' in e for e in errors)
    row['prompt_text'] = ''
    p.write_text(json.dumps({'generation_segments': [row]}), encoding='utf-8')
    assert any('提交正文' in e for e in inspect_document(p)[1])


@pytest.mark.parametrize('data', [
    '{', '{}', '{"generation_segments": []}',
    '{"drafts": [], "generation_segments": []}',
    '{"generation_segments": [{"segment_id": "A", "prompt_text": "ok"}], "creative_sections": [{"generation_segments": ["B"]}]}',
    '{"creative_sections": [{"generation_segments": ["OPEN-001"]}]}',
])
def test_unknown_invalid_or_ambiguous_json_is_unchecked(tmp_path, data, capsys):
    from scripts.check_video_prompt_focal_intent import main
    p = tmp_path / 'unknown.json'
    p.write_text(data, encoding='utf-8')
    assert main([str(p)]) == 2
    assert 'UNCHECKED' in capsys.readouterr().out


@pytest.mark.parametrize('layout', ['top', 'nested', 'linked'])
@pytest.mark.parametrize('fields', [
    {'prompt': '中长焦突出人物，与背景分离。'},
    {'prompt_text': '中长焦突出人物，与背景分离。'},
    {'prompt': '中长焦突出人物，与背景分离。', 'prompt_text': '中长焦突出人物，与背景分离。'},
])
def test_json_prompt_aliases_read_actual_body(tmp_path, layout, fields):
    import json
    from scripts.check_video_prompt_focal_intent import _json_segments, main
    row = {'segment_id': 'P01', **fields, 'notes': 'DLG-001。视频模型没有上下文。'}
    data = {'generation_segments': [row]}
    if layout == 'nested':
        data = {'creative_sections': [{'generation_segments': [row]}]}
    elif layout == 'linked':
        data['creative_sections'] = [{'generation_segments': ['P01']}]
    text = json.dumps(data, ensure_ascii=False)
    assert _json_segments(text)[0][1] == next(iter(fields.values()))
    p = tmp_path / 'aliases.json'; p.write_text(text, encoding='utf-8')
    assert inspect_document(p) == (1, [])
    assert main([str(p)]) == 0


@pytest.mark.parametrize('fields', [
    {}, {'prompt': ''}, {'prompt_text': '  '}, {'prompt': None}, {'prompt_text': 42},
    {'prompt': {'body': '中长焦突出人物。'}},
    {'prompt': '中长焦突出人物。', 'prompt_text': None},
    {'prompt_text': '中长焦突出人物。', 'prompt': ''},
])
def test_json_invalid_alias_never_falls_back_to_metadata(tmp_path, fields):
    import json
    from scripts.check_video_prompt_focal_intent import main
    p = tmp_path / 'invalid.json'
    p.write_text(json.dumps({'generation_segments': [dict(segment_id='P01', **fields,
        metadata={'prompt': '中长焦突出人物。'}, notes='中长焦突出人物。')]}), encoding='utf-8')
    count, errors = inspect_document(p)
    assert count == 1 and errors == ['第 P01 段缺少非空、完整的提交正文']
    assert main([str(p)]) == 1


@pytest.mark.parametrize('other', ['另一份正文。', '正文。 '])
def test_conflicting_json_aliases_explicitly_unchecked(tmp_path, other, capsys):
    import json
    from scripts.check_video_prompt_focal_intent import _json_segments, main
    text = json.dumps({'generation_segments': [{'segment_id': 'P01', 'prompt': '正文。', 'prompt_text': other}]})
    with pytest.raises(ValueError, match='不猜权威正文'):
        _json_segments(text)
    p = tmp_path / 'conflict.json'; p.write_text(text, encoding='utf-8')
    count, errors = inspect_document(p)
    assert count == 0 and '正文不一致' in errors[0]
    assert main([str(p)]) == 2
    assert 'UNCHECKED' in capsys.readouterr().out
