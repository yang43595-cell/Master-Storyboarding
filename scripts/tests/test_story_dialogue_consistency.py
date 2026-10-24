"""Reject the wrong-speaker, dropped-line and reordered-line failures from production."""
import copy
from pathlib import Path

import pytest

from test_story_paragraph_sources import narrative
from core.story_sources import validate_story_sources
from core.story_sources import validate_candidate


def two_lines(tmp_path):
    n = narrative(tmp_path)
    n['dialogues'].append({**n['dialogues'][0], 'dialogue_id': 'DLG-03', 'text': '再见。'})
    n['story_paragraphs'][0]['text'] += '\n\n主角（DLG-03）："再见。"'
    path = tmp_path / 'script.md'
    md = path.read_text(encoding='utf-8')
    md = md.replace('## 故事正文', '| DLG-03 | CHR-A | 再见。 | STP-01 | confirmed | user |\n\n## 故事正文')
    md = md.replace('## 资产需求', '主角（DLG-03）："再见。"\n\n## 资产需求')
    path.write_text(md, encoding='utf-8')
    return n


@pytest.mark.parametrize('mutation', ['speaker', 'order', 'delete', 'text', 'paragraph'])
def test_json_cannot_diverge_from_approved_markdown(tmp_path, mutation):
    n = two_lines(tmp_path)
    if mutation == 'speaker': n['dialogues'][0]['speaker_id'] = 'CHR-WRONG'
    if mutation == 'order': n['dialogues'].reverse()
    if mutation == 'delete': n['dialogues'].pop(0)
    if mutation == 'text': n['dialogues'][0]['text'] = '不'
    if mutation == 'paragraph': n['dialogues'][0]['source_paragraph_id'] = 'STP-OTHER'
    with pytest.raises(ValueError):
        validate_story_sources(n, project_root=tmp_path)


@pytest.mark.parametrize('mutation', ['speaker', 'order', 'delete', 'duplicate', 'unregistered', 'text'])
def test_body_cannot_diverge_even_when_json_paragraphs_match(tmp_path, mutation):
    n = two_lines(tmp_path)
    old = n['story_paragraphs'][0]['text']
    a, b = '主角（DLG-01）："不。"', '主角（DLG-03）："再见。"'
    new = old
    if mutation == 'speaker': new = old.replace('主角（DLG-01）', '别人（DLG-01）')
    if mutation == 'order': new = '人物拒绝。\n\n' + b + '\n\n' + a
    if mutation == 'delete': new = old.replace(a, '')
    if mutation == 'duplicate': new = old + '\n\n' + a
    if mutation == 'unregistered': new = old + '\n\n主角（DLG-09）："新增。"'
    if mutation == 'text': new = old.replace('"不。"', '"不。又加一句。"')
    n['story_paragraphs'][0]['text'] = new
    path = tmp_path / 'script.md'
    path.write_text(path.read_text(encoding='utf-8').replace(old, new), encoding='utf-8')
    with pytest.raises(ValueError):
        validate_story_sources(n, project_root=tmp_path)


def test_stable_gap_and_later_approval_do_not_change_content(tmp_path):
    n = two_lines(tmp_path)
    path = tmp_path / 'script.md'
    path.write_text(path.read_text(encoding='utf-8').replace('confirmed', 'draft'), encoding='utf-8')
    before = copy.deepcopy(n)
    validate_story_sources(n, project_root=tmp_path)
    assert n == before


def test_missing_table_fails_for_speech_but_silent_story_is_valid(tmp_path):
    n = narrative(tmp_path)
    path = tmp_path / 'script.md'
    path.write_text('## 故事正文\n### STP-01\n' + n['story_paragraphs'][0]['text'], encoding='utf-8')
    with pytest.raises(ValueError):
        validate_story_sources(n, project_root=tmp_path)
    n['dialogues'] = []
    n['story_paragraphs'][0]['text'] = '人物沉默。'
    path.write_text('## 故事正文\n### STP-01\n人物沉默。', encoding='utf-8')
    validate_story_sources(n, project_root=tmp_path)


def test_in_memory_validation_also_checks_body(tmp_path):
    n = narrative(tmp_path)
    n['dialogues'] = []
    with pytest.raises(ValueError):
        validate_story_sources(n)


@pytest.mark.parametrize('quoted', ['“不。”', '「不。」', '不。'])
def test_existing_quote_styles(tmp_path, quoted):
    n = narrative(tmp_path)
    path = tmp_path / 'script.md'
    path.write_text(path.read_text(encoding='utf-8').replace('"不。"', quoted), encoding='utf-8')
    n['story_paragraphs'][0]['text'] = n['story_paragraphs'][0]['text'].replace('"不。"', quoted)
    validate_story_sources(n, project_root=tmp_path)


def test_escaped_pipe_and_legacy_speaker_prefix(tmp_path):
    n = narrative(tmp_path)
    n['dialogues'][0].update(speaker_id='MAIN', text='A|B。')
    n['asset_requirements'][0]['entity_id'] = 'CHR-MAIN'
    n['story_paragraphs'][0]['text'] = n['story_paragraphs'][0]['text'].replace('不。', 'A|B。')
    path = tmp_path / 'script.md'
    md = path.read_text(encoding='utf-8').replace('| CHR-A | 不。 |', r'| MAIN | A\|B。 |')
    path.write_text(md.replace('"不。"', '"A|B。"'), encoding='utf-8')
    validate_story_sources(n, project_root=tmp_path)


@pytest.mark.parametrize('mutation', ['speaker', 'order', 'delete'])
def test_asset_receiver_uses_same_dialogue_gate(tmp_path, mutation):
    from test_visual_asset_projects import fixture, pmod
    _, project, _ = fixture(tmp_path)
    root = Path(project['path'])
    n = two_lines(root)
    n['project_id'] = '验收项目'
    pmod.write(root / 'narrative.json', n)
    assert len(pmod.source_project(str(root))[2]['dialogues']) == 2
    if mutation == 'speaker': n['dialogues'][0]['speaker_id'] = 'CHR-WRONG'
    if mutation == 'order': n['dialogues'].reverse()
    if mutation == 'delete': n['dialogues'].pop()
    pmod.write(root / 'narrative.json', n)
    with pytest.raises(ValueError, match='最终对白表'):
        pmod.source_project(str(root))


@pytest.mark.parametrize('extra', ['主角："新增。"', 'CHR-A: 新增。'])
def test_unlabelled_character_speech_is_rejected(tmp_path, extra):
    n = narrative(tmp_path)
    n['story_paragraphs'][0]['text'] += '\n\n' + extra
    with pytest.raises(ValueError, match='缺少 DLG'):
        validate_story_sources(n)


def test_colons_in_actions_signage_and_multiline_speech_are_not_extra_dialogue(tmp_path):
    n = narrative(tmp_path)
    speech = '他说：\n主角：不要走。'
    n['dialogues'][0]['text'] = speech
    n['story_paragraphs'][0]['text'] = '时间：12:30。牌子写着：“请勿入内”。\n\n主角（DLG-01）："' + speech + '"\n\n提示：灯熄灭。'
    validate_story_sources(n)


def candidate_file(tmp_path):
    two_lines(tmp_path)
    p = tmp_path / 'script.md'
    p.write_text(p.read_text(encoding='utf-8') + '\n| AST-CHR-A | CHARACTER | 主角 |\n', encoding='utf-8')
    return p


def test_markdown_checks_before_any_json_exists(tmp_path):
    p = candidate_file(tmp_path)
    validate_candidate(p)
    assert not list(tmp_path.glob('*.json'))
    p.write_text(p.read_text(encoding='utf-8').replace('主角（DLG-01）', '别人（DLG-01）'), encoding='utf-8')
    with pytest.raises(ValueError, match='说话者'):
        validate_candidate(p)


@pytest.mark.parametrize('mutation', ['delete', 'speaker', 'text', 'order'])
def test_independent_baseline_detects_shared_changes(tmp_path, mutation):
    p = candidate_file(tmp_path)
    baseline = tmp_path / 'approved.md'
    original = p.read_text(encoding='utf-8')
    baseline.write_text(original, encoding='utf-8')
    changed = original
    if mutation == 'delete':
        changed = changed.replace('| DLG-03 | CHR-A | 再见。 | STP-01 | confirmed | user |', '').replace('主角（DLG-03）："再见。"', '')
    if mutation == 'speaker':
        changed = changed.replace('| DLG-01 | CHR-A |', '| DLG-01 | CHR-B |').replace('主角（DLG-01）', 'CHR-B（DLG-01）')
    if mutation == 'text': changed = changed.replace('不。', '好。')
    if mutation == 'order':
        changed = changed.replace('DLG-01', 'DLG-TEMP').replace('DLG-03', 'DLG-01').replace('DLG-TEMP', 'DLG-03')
    p.write_text(changed, encoding='utf-8')
    validate_candidate(p)
    with pytest.raises(ValueError, match='指定对白依据'):
        validate_candidate(p, baseline)
    # Explicitly adopted new wording may become the new baseline, without
    # removing the previous baseline or asking the checker to infer approval.
    updated = tmp_path / 'newly-approved.md'
    updated.write_text(changed, encoding='utf-8')
    validate_candidate(p, updated)


def test_baseline_cannot_be_self_or_empty(tmp_path):
    p = candidate_file(tmp_path)
    with pytest.raises(ValueError, match='同一候选'):
        validate_candidate(p, p)
    b = tmp_path / 'empty.md'; b.write_text('', encoding='utf-8')
    with pytest.raises(ValueError, match='缺少有效'):
        validate_candidate(p, b)


@pytest.mark.parametrize('partial', [
    {'scope': 'dialogue'}, {'scope': 'segment'}, {'scope': 'scene'},
    {'kind': 'dialogue-confirmation'}, {'dialogue_ids': ['DLG-01']},
    {'scene_ids': ['S01']}, {'shot_ids': ['S01-001']},
    {'source_shot_ids': ['S01-001']}, {'paragraph_ids': ['STP-01']},
    {'source_paragraph_ids': ['STP-01']}, {'segment_ids': ['restaurant']},
    {'scope': 'artifact', 'shot_ids': ['S01-001']},
    {'scope': '确认第一场'}, {'scope': '确认全片'},
])
def test_partial_approval_cannot_be_used_as_whole_artifact(partial):
    from core.artifacts import artifact_is_confirmed
    entry = {'artifact_id': 'ART-X', 'status': 'current', 'user_approved': True}
    legacy = {'artifact_ids': ['ART-X']}
    assert artifact_is_confirmed(entry, {'user_confirmations': [legacy]})
    assert not artifact_is_confirmed(entry, {'user_confirmations': [{**legacy, **partial}]})
    assert artifact_is_confirmed(entry, {'user_confirmations': [{**legacy, **partial}, legacy]})


def test_partial_receipt_is_blocked_at_handoff(tmp_path):
    import json
    from core.artifacts import digest
    from stage_context_cli import _confirmed_handoff_inputs
    manage = tmp_path / '00_项目管理'; manage.mkdir()
    story = tmp_path / 'story.md'; story.write_text('candidate', encoding='utf-8')
    entry = {'artifact_id': 'ART-X', 'logical_id': 'narrative.script', 'status': 'current',
             'user_approved': True, 'path_or_url': 'story.md', 'checksum': digest(story)}
    state = {'current_artifacts': ['ART-X'], 'next_stage': 'storyboard-planner', 'user_confirmations': [
        {'artifact_ids': ['ART-X'], 'scope': 'dialogue', 'approval_ref': 'user:turn'}]}
    (manage / 'artifact_manifest.json').write_text(json.dumps({'artifacts': [entry]}), encoding='utf-8')
    p = manage / 'project_state.json'; p.write_text(json.dumps(state), encoding='utf-8')
    with pytest.raises(ValueError, match='缺少用户确认'):
        _confirmed_handoff_inputs(tmp_path, 'storyboard-planner')
    state['user_confirmations'].append({'artifact_ids': ['ART-X'], 'scope': 'artifact'})
    p.write_text(json.dumps(state), encoding='utf-8')
    _confirmed_handoff_inputs(tmp_path, 'storyboard-planner')


def test_verified_legacy_scope_normalization_allows_handoff(tmp_path):
    import json
    from core.artifacts import digest
    from stage_context_cli import _confirmed_handoff_inputs
    manage = tmp_path / '00_项目管理'; manage.mkdir()
    source = tmp_path / 'story.md'; source.write_text('approved story', encoding='utf-8')
    entry = {'artifact_id': 'ART-X', 'logical_id': 'creative.script', 'status': 'current',
             'user_approved': True, 'path_or_url': 'story.md', 'checksum': digest(source)}
    receipt = {'kind': 'revision-candidate', 'artifact_id': 'ART-X',
               'sha256': entry['checksum'], 'approval_ref': 'user:confirmed-version',
               'scope': '已核对原确认依据的整稿说明'}
    state = {'current_artifacts': ['ART-X'], 'next_stage': 'storyboard-planner', 'user_confirmations': [receipt]}
    (manage / 'artifact_manifest.json').write_text(json.dumps({'artifacts': [entry]}), encoding='utf-8')
    state_path = manage / 'project_state.json'
    def save():
        state_path.write_text(json.dumps(state), encoding='utf-8')
    save()
    with pytest.raises(ValueError, match='缺少用户确认'):
        _confirmed_handoff_inputs(tmp_path, 'storyboard-planner')
    # Explicit evidence-based migration, never infer approval from prose.
    receipt['scope_description'] = receipt['scope']
    receipt['scope'] = 'artifact'
    save()
    _, inputs = _confirmed_handoff_inputs(tmp_path, 'storyboard-planner')
    assert inputs == [entry]
    source.write_text('changed after approval', encoding='utf-8')
    with pytest.raises(ValueError, match='内容与确认版本不一致'):
        _confirmed_handoff_inputs(tmp_path, 'storyboard-planner')


def test_silent_markdown_and_relocated_paragraph_are_supported(tmp_path):
    p = tmp_path / 'silent.md'
    p.write_text('## 故事正文\n### STP-01\n人物沉默。', encoding='utf-8')
    validate_candidate(p)
    p = candidate_file(tmp_path)
    b = tmp_path / 'approved.md'; b.write_bytes(p.read_bytes())
    p.write_text(p.read_text(encoding='utf-8').replace('STP-01', 'STP-09'), encoding='utf-8')
    validate_candidate(p, b)


@pytest.mark.parametrize('speech, cell', [
    ('他说：\n主角：不要走。', '他说：<br>主角：不要走。'),
    ('A|B\nC', r'A\|B<br/>C'),
    ('字面<br>与\\n', r'字面&lt;br&gt;与\n'),
    ('字面&lt;br&gt;\n仍保留', '字面&#38;lt;br&gt;<br />仍保留'),
])
def test_multiline_table_roundtrip_all_receivers(tmp_path, speech, cell):
    from core.story_sources import reading_supplement, read_dialogue_table
    n = narrative(tmp_path)
    p = tmp_path / 'script.md'
    old = n['story_paragraphs'][0]['text']
    new = '人物拒绝。\n\n主角（DLG-01）："' + speech + '"'
    text = p.read_text(encoding='utf-8').replace('| 原文 |', '| 原文（换行转义） |').replace('| 不。 |', '| ' + cell + ' |').replace(old, new)
    text += '\n| AST-CHR-A | CHARACTER | 主角 |\n'
    p.write_text(text, encoding='utf-8')
    n['story_paragraphs'][0]['text'] = new
    n['dialogues'][0]['text'] = speech
    baseline = tmp_path / 'dialogue-baseline.md'
    baseline.write_text(text, encoding='utf-8')
    validate_candidate(p, baseline)
    validate_story_sources(n, project_root=tmp_path)
    assert read_dialogue_table(p)[0][2] == speech
    assert 'text' not in reading_supplement(n, tmp_path)['narrative']['dialogues'][0]
    assert not list(tmp_path.glob('*.json'))  # Validation never creates confirmations.
    n['dialogues'][0]['text'] += '篡改'
    with pytest.raises(ValueError, match='最终对白表'):
        validate_story_sources(n, project_root=tmp_path)
    p.write_text(text.replace('DLG-01 | CHR-A', 'DLG-01 | CHR-WRONG'), encoding='utf-8')
    with pytest.raises(ValueError):
        validate_candidate(p, baseline)


def test_legacy_literal_markup_is_not_reinterpreted_without_encoding_header(tmp_path):
    from core.story_sources import read_dialogue_table
    p = tmp_path / 'legacy.md'
    p.write_text('## 最终对白\n| 对白 ID | 说话者 | 原文 | 来源段落 | 状态 | 来源证据 |\n'
                 '| DLG-01 | CHR-A | <br> &lt;br&gt; &#38; | STP-01 | locked | fixture |\n', encoding='utf-8')
    assert read_dialogue_table(p)[0][2] == '<br> &lt;br&gt; &#38;'


def test_legacy_speech_cannot_activate_header_encoding(tmp_path):
    from core.story_sources import read_dialogue_table
    p = tmp_path / 'legacy-marker.md'
    p.write_text('## 最终对白\n| 对白 ID | 说话者 | 原文 | 来源段落 | 状态 | 来源证据 |\n'
                 '| DLG-01 | CHR-A | 原文（换行转义） | STP-01 | locked | fixture |\n'
                 '| DLG-02 | CHR-A | 字面<br>和&lt;br&gt; | STP-01 | locked | fixture |\n', encoding='utf-8')
    assert [row[2] for row in read_dialogue_table(p)] == ['原文（换行转义）', '字面<br>和&lt;br&gt;']
