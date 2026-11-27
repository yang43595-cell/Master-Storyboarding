"""Regression checks for direct story references and legacy read compatibility."""
import copy
import json
from pathlib import Path
import sys
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.schema_contracts import schema_errors
from core.story_sources import read_story_paragraphs, validate_shot_plan, validate_story_sources
from visual_asset_source import prompt_context
from test_storyboard_planner_contract import sample_package


def test_repository_root_package_import_in_clean_interpreter():
    """Existing tests add scripts to sys.path; -m entrypoints must work without it."""
    result = subprocess.run(
        [sys.executable, '-B', '-c',
         'from scripts.core.story_sources import validate_shot_plan; '
         'from scripts.visual_asset_choices import _load_asset_refs; '
         'assert _load_asset_refs().__name__ == "scripts.core.asset_refs"; '
         'from scripts.core.asset_refs import build_asset_list; '
         'from pathlib import Path; import tempfile; '
         'tmp = tempfile.TemporaryDirectory(); '
         'assert build_asset_list(Path(tmp.name), {"revision": "R01", "asset_requirements": []})["items"] == []; '
         'tmp.cleanup()'],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def narrative(tmp_path):
    text = '人物拒绝。\n\n主角（DLG-01）："不。"'
    (tmp_path / 'script.md').write_text(
        '## 最终对白\n\n| 对白 ID | 说话者 | 原文 | 来源段落 | 状态 | 来源证据 |\n'
        '|---|---|---|---|---|---|\n| DLG-01 | CHR-A | 不。 | STP-01 | confirmed | user |\n\n'
        '## 故事正文\n\n### STP-01｜拒绝\n\n'+text+'\n\n## 资产需求\n', encoding='utf-8')
    return {'schema_version':'2.0','project_id':'MSB-TEST','revision':'R01','status':'confirmed',
            'human_readable_path':'script.md','objective':'保留原文','locked_facts':[],
            'story_paragraphs':[{'paragraph_id':'STP-01','text':text}],
            'dialogues':[{'dialogue_id':'DLG-01','speaker_id':'CHR-A','text':'不。','source_paragraph_id':'STP-01','status':'confirmed','source_ref':'user'}],
            'asset_requirements':[{'asset_id':'AST-CHAR-A','entity_id':'CHR-A','category':'CHARACTER','name':'主角',
             'narrative_purpose':'拒绝','source_paragraph_ids':['STP-01'],'identity_constraints':['人物'],'required_states':['拒绝'],
             'interactions':[],'reference_paths':[],'unknowns':[]}], 'change_summary':[],'open_questions':[]}


def test_shot_plan_uses_confirmed_registration_not_latest_filename(tmp_path):
    from core.artifacts import digest
    from core.asset_refs import current_narrative
    from core.io import write_json
    from core.project_state import new_project_state, write_project_state
    current = narrative(tmp_path)
    # The authoritative path is deliberately outside the old R*_叙事包 pattern.
    package = tmp_path / "registered.json"
    write_json(package, current)
    candidate = copy.deepcopy(current)
    candidate.update(revision="R99", status="candidate")
    candidate["asset_requirements"][0]["asset_id"] = "AST-CANDIDATE-ONLY"
    write_json(tmp_path / "02_编导/R99_叙事包.json", candidate)
    entry = {"artifact_id": "ART-CURRENT", "logical_id": "narrative.package",
             "status": "current", "user_approved": True, "path_or_url": "registered.json",
             "checksum": digest(package)}
    write_json(tmp_path / "00_项目管理/artifact_manifest.json", {"artifacts": [entry]})
    state = new_project_state()
    state.update(current_artifacts=["ART-CURRENT"], user_confirmations=[{"artifact_ids": ["ART-CURRENT"]}])
    write_project_state(tmp_path, state)
    shot = tmp_path / "shot.md"
    shot.write_text("| 来源 | AST-CHAR-A |\n", encoding="utf-8")
    validate_shot_plan(shot, tmp_path)
    shot.write_text("| 来源 | AST-CANDIDATE-ONLY |\n", encoding="utf-8")
    with pytest.raises(ValueError, match="AST-CANDIDATE-ONLY"):
        validate_shot_plan(shot, tmp_path)
    state["current_artifacts"] = []
    write_project_state(tmp_path, state)
    with pytest.raises(ValueError, match="唯一且已确认"):
        current_narrative(tmp_path)
    state["current_artifacts"] = ["ART-CURRENT"]
    write_project_state(tmp_path, state)
    write_json(package, candidate)
    with pytest.raises(ValueError, match="登记版本不一致"):
        current_narrative(tmp_path)


def test_current_pipeline_uses_exact_paragraphs(tmp_path):
    n=narrative(tmp_path); board=sample_package()
    validate_story_sources(n, board, project_root=tmp_path)
    context=prompt_context(n,['AST-CHAR-A'])
    assert context['story_paragraphs']==n['story_paragraphs']
    assert 'events' not in context and 'source_event_ids' not in str(context)
    assert read_story_paragraphs(tmp_path/'script.md')==n['story_paragraphs']


def test_reading_supplement_preserves_all_nonduplicate_information(tmp_path):
    from core.story_sources import reading_supplement
    n = narrative(tmp_path)
    n['open_questions'] = ['服装待定']
    original = copy.deepcopy(n)
    result = reading_supplement(n, tmp_path)['narrative']
    assert n == original
    for key in n:
        if key in ('story_paragraphs', 'dialogues'):
            assert result[key] == [{k:v for k,v in row.items() if k != 'text'} for row in n[key]]
        else:
            assert result[key] == n[key]
    # Reconstituting only the omitted text restores the entire source exactly.
    restored = copy.deepcopy(result)
    for key in ('story_paragraphs', 'dialogues'):
        for row, source in zip(restored[key], n[key]):
            row['text'] = source['text']
    assert restored == n


def test_reading_supplement_refuses_inconsistent_original(tmp_path):
    from core.story_sources import reading_supplement
    n = narrative(tmp_path)
    n['story_paragraphs'][0]['text'] = '已被改写的故事。'
    with pytest.raises(ValueError, match='Markdown'):
        reading_supplement(n, tmp_path)


@pytest.mark.parametrize('mutation', ['unknown_asset','unknown_dialogue','summary','duplicate','missing_silent','missing_dialogue'])
def test_invalid_sources_fail(tmp_path, mutation):
    n=narrative(tmp_path);board=sample_package()
    if mutation=='unknown_asset':n['asset_requirements'][0]['source_paragraph_ids']=['STP-MISSING']
    if mutation=='unknown_dialogue':n['dialogues'][0]['source_paragraph_id']='STP-MISSING'
    if mutation=='summary':n['story_paragraphs'][0]['text']='拒绝发生了。'
    if mutation=='duplicate':n['story_paragraphs']*=2
    if mutation=='missing_dialogue':board['scenes'][0]['shots'][0]['source_dialogue_ids']=[]
    if mutation=='missing_silent':
        n['story_paragraphs'].append({'paragraph_id':'STP-02','text':'两剂掉落。'})
        f=tmp_path/'script.md';f.write_text(f.read_text(encoding='utf-8').replace('## 资产需求','### STP-02｜掉落\n\n两剂掉落。\n\n## 资产需求'),encoding='utf-8')
    with pytest.raises(ValueError):validate_story_sources(n,board,project_root=tmp_path)


def test_story_body_uses_shared_relocated_root(tmp_path, monkeypatch):
    n = narrative(tmp_path)
    folder = tmp_path / '02_编导'
    folder.mkdir()
    (tmp_path / 'script.md').rename(folder / 'script.md')
    old_root = r'C:\Users\owner\Desktop\film'
    monkeypatch.setenv('MSB_PROJECT_PATH_MAP', json.dumps({old_root: str(tmp_path)}))
    for value in [r'02_编导\script.md', old_root + r'\02_编导\script.md']:
        n['human_readable_path'] = value
        validate_story_sources(n, project_root=tmp_path)


@pytest.mark.parametrize("name, text", [
    ("director.json", '{"source_asset_requirement_ids": ["AST-UNKNOWN"]}'),
    ("new-reading.md", "# 连续视听正文\n女主走向门口。"),
])
def test_legacy_shot_plan_cannot_pass_unrecognized_format(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="仅检查历史|不按零行通过"):
        validate_shot_plan(path, tmp_path)
