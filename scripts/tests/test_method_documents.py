from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.core.method_documents import MethodDocumentError, resolve_method_documents


def _method(vault: Path, *, lifecycle: str = "current", status: str = "enabled") -> Path:
    path = vault / "wiki" / "concepts" / "方法.md"
    path.parent.mkdir(parents=True)
    path.write_text(f'''---
title: "测试方法"
version: 2026-09-09
status: {status}
lifecycle:
  state: {lifecycle}
---

# 测试方法

完整正文，不允许被预算裁剪。
''', encoding="utf-8")
    return path


def _profile(binding: str = "user_confirmed") -> dict[str, object]:
    return {"professional_methods": [{"id": "MSB-METHOD-TEST", "path": "wiki/concepts/方法.md", "binding_status": binding, "applies_when": "测试", "requires": {"skill": "voiceover-to-video-script"}}]}


def test_confirmed_binding_loads_full_method_and_preserves_version(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = _method(vault)
    result = resolve_method_documents(vault, _profile(), {"skill": "voiceover-to-video-script"})
    assert result.pending == []
    assert result.required[0].content == source.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert result.required[0].version == "2026-09-09"


def test_proposed_binding_never_loads_before_user_confirmation(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _method(vault)
    result = resolve_method_documents(vault, _profile("proposed"), {"skill": "voiceover-to-video-script"})
    assert result.required == []
    assert result.pending == [{"id": "MSB-METHOD-TEST", "path": "wiki/concepts/方法.md", "reason": "awaiting_user_confirmation"}]


def test_proposed_binding_can_only_load_through_explicit_test_override(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _method(vault)
    result = resolve_method_documents(
        vault, _profile("proposed"), {"skill": "voiceover-to-video-script"},
        test_proposed_method_ids={"MSB-METHOD-TEST"},
    )
    assert [item.id for item in result.required] == ["MSB-METHOD-TEST"]
    assert result.pending == []


@pytest.mark.parametrize("field,value", [("lifecycle", "needs_review"), ("status", "draft")])
def test_inapplicable_method_is_reported_not_silently_loaded(tmp_path: Path, field: str, value: str) -> None:
    vault = tmp_path / "vault"
    _method(vault, **{field: value})
    with pytest.raises(MethodDocumentError, match="不适用"):
        resolve_method_documents(vault, _profile(), {"skill": "voiceover-to-video-script"})


def test_confirmed_method_requires_explicit_matching_task_conditions(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _method(vault)
    result = resolve_method_documents(vault, _profile(), {"skill": "voiceover-to-video-script"})
    assert len(result.required) == 1
    result = resolve_method_documents(vault, _profile(), {"skill": "storyboard-planner"})
    assert result.required == []
    assert result.pending[0]["reason"] == "applies_when_not_met"


def test_video_profile_keeps_only_direct_read_conditions() -> None:
    profile = json.loads((Path(__file__).resolve().parents[2] / "skills/voiceover-to-video-script/knowledge-profile.json").read_text(encoding="utf-8"))
    methods = {item["id"]: item for item in profile["professional_methods"]}
    assert set(methods) == {"VGEN-PROMPT-FOUNDATION", "VGEN-MATERIAL-RESPONSIBILITY", "VGEN-SEEDANCE25-CONTROLS", "VGEN-CREATOR-POLISHING-MASK-USE"}
    assert all(item["path"].startswith("projects/Master-Storyboarding/应用知识/视频提示词/") for item in methods.values())
    assert all(item["requires"] == {"skill": "voiceover-to-video-script", "direct_read": True} for item in methods.values())
    assert "seedance2.5" in methods["VGEN-SEEDANCE25-CONTROLS"]["direct_read_when"]
    assert "造物者抛光面膜" in methods["VGEN-CREATOR-POLISHING-MASK-USE"]["direct_read_when"]
    assert "optional_experience_scopes" not in profile


@pytest.mark.parametrize(
    ("skill", "method_id", "relative"),
    [
        ("storyboard-planner", "STORYBOARD-PLANNING-METHOD", "projects/Master-Storyboarding/应用知识/导演规划/导演规划方法.md"),
        ("creative-director", "CREATIVE-DIRECTION-METHOD", "projects/Master-Storyboarding/应用知识/编导/编导方法.md"),
    ],
)
def test_second_batch_method_bindings_load_only_for_their_own_skill(
    tmp_path: Path, skill: str, method_id: str, relative: str
) -> None:
    profile_path = Path(__file__).resolve().parents[2] / "skills" / skill / "knowledge-profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    entry = profile["professional_methods"][0]
    assert entry["id"] == method_id
    assert entry["path"] == relative
    assert entry["binding_status"] == "user_confirmed"
    expected = {"skill": skill}
    if skill == "creative-director":
        expected["request.use_story_method"] = True
    assert entry["requires"] == expected

    source = tmp_path / "vault" / relative
    source.parent.mkdir(parents=True)
    source.write_text("""---
title: 测试专项方法
version: 2026-09-11
status: enabled
lifecycle:
  state: current
---

# 测试专项方法

完整正文必须进入本 Skill 的方法快照。
""", encoding="utf-8")
    if skill == 'creative-director':
        import hashlib
        entry['promotion_record'] = dict(basis='user_directed', method_version='2026-09-11',
            method_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), scope='synthetic binding test',
            source_ref='fixture:synthetic method', user_instruction_ref='fixture:synthetic exact revision adoption',
            conflict_resolution='fixture:no conflict', rollback='fixture:discard temporary files', supersedes=[])
    context = {"skill": skill, "request": {"use_story_method": True}}
    loaded = resolve_method_documents(tmp_path / "vault", profile, context)
    if skill == "creative-director":
        assert resolve_method_documents(tmp_path / "vault", profile, {"skill": skill}).required == []
    assert [item.id for item in loaded.required] == [method_id]
    assert "完整正文必须进入" in loaded.required[0].content
    other = "creative-director" if skill == "storyboard-planner" else "storyboard-planner"
    skipped = resolve_method_documents(tmp_path / "vault", profile, {"skill": other})
    assert skipped.required == []
    assert skipped.pending == [{"id": method_id, "path": relative, "reason": "applies_when_not_met"}]


@pytest.mark.parametrize("relative", ["../outside.md", "wiki/../../outside.md", "C:/outside.md", "C:outside.md", "\\\\server\\share\\outside.md", "wiki\\..\\outside.md"])
def test_required_method_rejects_paths_outside_vault(tmp_path: Path, relative: str) -> None:
    profile = _profile()
    profile["professional_methods"][0]["path"] = relative
    with pytest.raises(MethodDocumentError):
        resolve_method_documents(tmp_path / "vault", profile, {"skill": "voiceover-to-video-script"})
