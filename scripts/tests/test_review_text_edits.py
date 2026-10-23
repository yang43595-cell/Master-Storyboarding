import json
import random

from scripts.core.review_delta import text_change_view, section_impact


def restore(before, view):
    text = before
    for edit in reversed(view['text_edits']):
        a, b = edit['before_start'], edit['before_end']
        assert text[a:b] == edit['removed']
        text = text[:a] + edit['added'] + text[b:]
    return text


def test_long_prompt_single_word_does_not_repeat_unchanged_body():
    before = '## SEG-01\n' + '必要镜头内容。' * 200 + '固定中景' + '后续动作。' * 200 + '\n'
    after = before.replace('固定中景', '固定近景')
    view = text_change_view(before, after, 'old', 'new')
    assert view['diff_format'] == 'unicode-edits-v1'
    assert restore(before, view) == after
    assert len(json.dumps(view, ensure_ascii=False)) < len(before)
    assert section_impact(before.splitlines(), after.splitlines())['changed_sections'][0]['section'] == 'SEG-01'


def test_unicode_offsets_and_final_newline_are_not_lost():
    for before, after in [('😀甲\n', '😀乙'), ('甲', '甲\n'), ('甲\n乙\n', '甲\n乙'), ('', '😀\n')]:
        view = text_change_view(before, after, 'old', 'new')
        assert view['diff_format'] == 'unicode-edits-v1'
        assert restore(before, view) == after


def test_multiple_edits_reconstruct_exactly_without_changing_scope():
    rng = random.Random(193)
    before = '## SEG-01\n' + ''.join(rng.choices('甲乙丙丁😀 。\n', k=3000)) + '\n'
    for _ in range(30):
        a, b = sorted(rng.sample(range(len(before)-1), 2))
        after = before[:a] + '新增对白😀' + before[b:]
        # Force a final-newline difference so even small samples use the lossless view.
        after = after.rstrip('\n')
        view = text_change_view(before, after, 'old', 'new')
        assert restore(before, view) == after


def test_small_readable_diff_and_identical_files_keep_existing_shape():
    assert text_change_view('old\n', 'new\n', 'a', 'b')['diff_format'] == 'unified'
    assert text_change_view('same\n', 'same\n', 'a', 'b')['diff'] == ''


def test_large_replacement_uses_bounded_coarse_edit_and_keeps_every_character():
    before, after = '甲' * 20000 + '\n', '乙' * 20000
    view = text_change_view(before, after, 'old', 'new')
    assert restore(before, view) == after
    assert len(view['text_edits']) == 1
