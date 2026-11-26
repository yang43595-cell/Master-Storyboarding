"""Scene-by-scene director package writing keeps every write small and checks structure early."""
import json

import pytest

from scripts import director_package as dp


def _story():
    return {
        "project_id": "P",
        "story_paragraphs": [{"paragraph_id": "STP-01"}, {"paragraph_id": "STP-02"}],
        "dialogues": [{"dialogue_id": "DLG-001", "source_paragraph_id": "STP-01"}],
    }


def _scene(scene_id, stp, shot_ids):
    return {"scene_id": scene_id, "source_paragraph_ids": [stp], "shots": [
        {"shot_id": s, "source_dialogue_ids": []} for s in shot_ids]}


@pytest.fixture
def project(tmp_path, monkeypatch):
    entry = {"artifact_id": "NAR-R01", "revision": "R01", "path_or_url": "n.json"}
    monkeypatch.setattr(dp, "_narrative", lambda root: (entry, _story()))
    monkeypatch.setattr(dp, "effective", lambda story, choices: [{"asset_id": "AST-A"}])
    monkeypatch.setattr(dp, "read_choices", lambda root: None)
    monkeypatch.setattr(dp, "_scene_errors", lambda scene: [] if "shots" in scene else ["$: 'shots' is a required property"])
    return tmp_path


def test_init_prefills_sources_and_refuses_overwrite(project):
    result = dp.init(project, "03_导演/R01.json", {"director_rules": {"a": "b"}})
    package = json.loads((project / "03_导演/R01.json").read_text(encoding="utf-8"))
    assert package["source_paragraph_ids"] == ["STP-01", "STP-02"]
    assert package["source_asset_requirement_ids"] == ["AST-A"]
    assert result["remaining_paragraphs"] == ["STP-01", "STP-02"]
    with pytest.raises(ValueError, match="不覆盖"):
        dp.init(project, "03_导演/R01.json", {"director_rules": {"a": "b"}})
    with pytest.raises(ValueError, match="director_rules"):
        dp.init(project, "03_导演/R02.json", {})


def test_put_scene_orders_replaces_and_rejects_duplicates(project):
    dp.init(project, "R01.json", {"director_rules": {"a": "b"}})
    dp.put_scenes(project, "R01.json", _scene("S02", "STP-02", ["S02-001"]))
    result = dp.put_scenes(project, "R01.json", _scene("S01", "STP-01", ["S01-001"]))
    assert result["scenes"] == ["S01", "S02"] and result["complete"]
    assert dp.put_scenes(project, "R01.json", _scene("S01", "STP-01", ["S01-001", "S01-002"]))["shots"] == 3
    with pytest.raises(ValueError, match="镜号重复"):
        dp.put_scenes(project, "R01.json", _scene("S03", "STP-02", ["S02-001"]))
    with pytest.raises(ValueError, match="结构不符合"):
        dp.put_scenes(project, "R01.json", {"scene_id": "S09"})
    with pytest.raises(ValueError, match="没有的段落"):
        dp.put_scenes(project, "R01.json", _scene("S04", "STP-99", ["S04-001"]))


def test_save_plan_and_group_brief_enforce_full_coverage(project, monkeypatch):
    monkeypatch.setattr(dp, "scene_source", lambda root, pars: {"paragraphs": sorted(pars)})
    dp.init(project, "03_导演/R01.json", {"director_rules": {"aspect_and_exhibition": "9:16"}})
    with pytest.raises(ValueError, match="不重不漏"):
        dp.save_plan(project, "03_导演/R01.json", {"groups": [
            {"group_id": "G1", "scenes": [{"scene_id": "S01", "paragraphs": ["STP-01"],
                                           "entry_handoff": "黑场入", "exit_handoff": "硬切"}]}]})
    saved = dp.save_plan(project, "03_导演/R01.json", {"groups": [
        {"group_id": "G1", "shared_space_note": "同房间", "scenes": [
            {"scene_id": "S01", "paragraphs": ["STP-01"], "entry_handoff": "黑场入", "exit_handoff": "切外景"}]},
        {"group_id": "G2", "scenes": [
            {"scene_id": "S02", "paragraphs": ["STP-02"], "entry_handoff": "接室内声", "exit_handoff": "收尾"}]},
    ]})
    assert saved["groups"] == ["G1", "G2"] and saved["scenes"] == 2
    brief = dp.group_brief(project, "03_导演/R01.json", "G1")
    assert brief["director_rules"] == {"aspect_and_exhibition": "9:16"}
    assert brief["sources"] == {"paragraphs": ["STP-01"]}
