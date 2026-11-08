from __future__ import annotations

import importlib.util
import json
import wave
from pathlib import Path

import pytest
import jsonschema


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("voice_reference_cli", ROOT / "scripts" / "voice_reference_cli.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_wav(path: Path, seconds: int = 3, sample_rate: int = 8000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * sample_rate * seconds)
    return path


def args(**values):
    if "confirmed_by_user" in values:
        values.setdefault("intended_use", ["project production"])
    return type("Args", (), values)()


def add_args(*, library: Path, voice_id: str, audio: Path, name: str | None = None):
    return args(
        library=str(library), voice_id=voice_id, name=name or voice_id, audio=str(audio),
        character_type=[], replace=False, source_kind="user_recording",
        source_ref=f"recording://{voice_id}", speaker_identity_basis="user supplied speaker identity",
        authorization_status="authorized", authorization_ref=f"rights://{voice_id}",
        permitted_use=["project production"],
    )


def test_library_snapshot_and_project_validation(tmp_path: Path) -> None:
    library = tmp_path / "voice-library" / "library.json"
    MODULE.command_init(args(library=str(library)))
    source = write_wav(tmp_path / "source.wav")
    add = add_args(library=library, voice_id="VOICE-A", audio=source, name="温和女声")
    add.character_type = ["成年女性"]
    MODULE.command_add(add)
    project = tmp_path / "project"
    (project / "00_项目管理").mkdir(parents=True)
    (project / "00_项目管理" / "project_state.json").write_text(
        json.dumps({"schema_version": "2.0", "project_id": "P1"}, ensure_ascii=False), encoding="utf-8"
    )

    result = MODULE.command_snapshot(
        args(
            library=str(library),
            project=str(project),
            voice_id="VOICE-A",
            character_id="CHAR-A",
            character_name="角色A",
            confirmed_by_user=True,
            approval_ref="trusted://user-event/VOICE-A",
            replace=False,
        )
    )

    assert result["assignment"]["voice_id"] == "VOICE-A"
    checked = MODULE.validate_project(project)
    assert checked["assignments"][0]["character_id"] == "CHAR-A"
    state = json.loads((project / "00_项目管理" / "project_state.json").read_text(encoding="utf-8"))
    assert state["voice_assignments"] == {"CHAR-A": "VOICE-A"}
    jsonschema.validate(
        json.loads(library.read_text(encoding="utf-8")),
        json.loads((ROOT / "schemas" / "voice-reference-library.schema.json").read_text(encoding="utf-8")),
    )
    jsonschema.validate(
        json.loads(MODULE.project_manifest_path(project).read_text(encoding="utf-8")),
        json.loads((ROOT / "schemas" / "voice-reference-manifest.schema.json").read_text(encoding="utf-8")),
    )


def test_snapshot_requires_explicit_user_confirmation(tmp_path: Path) -> None:
    library = tmp_path / "library.json"
    MODULE.command_init(args(library=str(library)))
    source = write_wav(tmp_path / "source.wav")
    MODULE.command_add(add_args(library=library, voice_id="V1", audio=source, name="声音"))
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(MODULE.VoiceReferenceError, match="confirmed-by-user"):
        MODULE.command_snapshot(
            args(
                library=str(library), project=str(project), voice_id="V1", character_id="C1",
                character_name="角色", confirmed_by_user=False, approval_ref="trusted://user-event/V1", replace=False,
            )
        )


def test_snapshot_rejects_unknown_authorization(tmp_path: Path) -> None:
    library = tmp_path / "library.json"
    MODULE.command_init(args(library=str(library)))
    source = write_wav(tmp_path / "source.wav")
    add = add_args(library=library, voice_id="V1", audio=source)
    add.authorization_status = "unknown"
    add.authorization_ref = "rights://unknown"
    MODULE.command_add(add)
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(MODULE.VoiceReferenceError, match="不是 authorized"):
        MODULE.command_snapshot(
            args(
                library=str(library), project=str(project), voice_id="V1", character_id="C1",
                character_name="角色", confirmed_by_user=True, approval_ref="trusted://user-event/V1", replace=False,
            )
        )


def test_add_requires_explicit_speaker_identity_basis(tmp_path: Path) -> None:
    library = tmp_path / "library.json"
    MODULE.command_init(args(library=str(library)))
    source = write_wav(tmp_path / "named-like-character.wav")
    add = add_args(library=library, voice_id="V1", audio=source)
    add.speaker_identity_basis = ""
    with pytest.raises(MODULE.VoiceReferenceError, match="speaker-identity-basis"):
        MODULE.command_add(add)


def test_replacing_voice_invalidates_only_matching_character_segments(tmp_path: Path) -> None:
    library = tmp_path / "library.json"
    MODULE.command_init(args(library=str(library)))
    for voice_id in ("V1", "V2"):
        source = write_wav(tmp_path / f"{voice_id}.wav")
        MODULE.command_add(add_args(library=library, voice_id=voice_id, audio=source))
    project = tmp_path / "project"
    management = project / "00_项目管理"
    management.mkdir(parents=True)
    plan = {
        "segments": [
            {"id": "S-A", "status": "current", "speaking_characters": [{"character_id": "C1"}]},
            {"id": "S-B", "status": "current", "speaking_characters": [{"character_id": "C2"}]},
        ]
    }
    (management / "production_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    common = dict(
        library=str(library), project=str(project), character_id="C1", character_name="角色1",
        confirmed_by_user=True, approval_ref="trusted://user-event/C1",
    )
    MODULE.command_snapshot(args(**common, voice_id="V1", replace=False))
    result = MODULE.command_snapshot(args(**common, voice_id="V2", replace=True))

    updated = json.loads((management / "production_plan.json").read_text(encoding="utf-8"))
    assert result["invalidated_segments"] == ["S-A"]
    assert updated["segments"][0]["status"] == "stale"
    assert updated["segments"][1]["status"] == "current"


@pytest.mark.parametrize("command", [MODULE.command_snapshot, MODULE.command_replace])
def test_same_voice_changed_audio_preserves_old_snapshot_and_invalidates_dependents(tmp_path: Path, command) -> None:
    library = tmp_path / "library.json"
    MODULE.command_init(args(library=str(library)))
    MODULE.command_add(add_args(library=library, voice_id="V1", audio=write_wav(tmp_path / "first.wav")))
    project = tmp_path / "project"
    management = project / "00_项目管理"
    management.mkdir(parents=True)
    common = dict(library=str(library), project=str(project), voice_id="V1", character_id="C1",
                  character_name="角色1", confirmed_by_user=True, approval_ref="trusted://user-event/first")
    original = MODULE.command_snapshot(args(**common, replace=False))["assignment"]
    original_path = project / original["snapshot_path"]
    original_bytes = original_path.read_bytes()
    manifest = MODULE.load_artifact_manifest(project)
    manifest["artifacts"].extend([
        {"logical_id": "prompt.S-A", "status": "current", "depends_on": ["voice.character.C1"]},
        {"logical_id": "video.S-A", "status": "current", "depends_on": ["prompt.S-A"]},
        {"logical_id": "prompt.S-B", "status": "current", "depends_on": ["voice.character.C2"]},
    ])
    MODULE.write_json(MODULE.artifact_manifest_path(project), manifest)
    MODULE.write_json(management / "production_plan.json", {"segments": [
        {"id": "S-A", "status": "current", "speaking_characters": [{"character_id": "C1"}]},
        {"id": "S-B", "status": "current", "speaking_characters": [{"character_id": "C2"}]},
    ]})
    update = add_args(library=library, voice_id="V1", audio=write_wav(tmp_path / "second.wav", seconds=4))
    update.replace = True
    MODULE.command_add(update)

    with pytest.raises(MODULE.VoiceReferenceError, match="--replace"):
        MODULE.command_snapshot(args(**common, replace=False))
    assert original_path.read_bytes() == original_bytes
    result = command(args(**{**common, "approval_ref": "trusted://user-event/second"}, replace=True))
    new_path = project / result["assignment"]["snapshot_path"]
    assert new_path != original_path
    assert MODULE.file_sha256(new_path) != original["sha256"]
    assert (project / "99_历史与废弃" / "声音母版" / original_path.name).read_bytes() == original_bytes
    assert result["invalidated_segments"] == ["S-A"]
    assert set(result["invalidated_artifacts"]) == {"prompt.S-A", "video.S-A"}
    statuses = {entry["logical_id"]: entry["status"] for entry in MODULE.load_artifact_manifest(project)["artifacts"]}
    assert statuses["prompt.S-B"] == "current"
    assert MODULE.validate_project(project)["assignments"][0]["snapshot_path"] == str(new_path)

    repeated = command(args(**common, replace=True))
    assert repeated["assignment"]["snapshot_path"] == result["assignment"]["snapshot_path"]
    assert repeated["invalidated_artifacts"] == []
    assert repeated["invalidated_segments"] == []


def test_same_voice_audio_change_failed_commit_keeps_current_audio_and_binding(tmp_path: Path, monkeypatch) -> None:
    library = tmp_path / "library.json"
    MODULE.command_init(args(library=str(library)))
    MODULE.command_add(add_args(library=library, voice_id="V1", audio=write_wav(tmp_path / "first.wav")))
    project = tmp_path / "project"
    project.mkdir()
    common = dict(library=str(library), project=str(project), voice_id="V1", character_id="C1",
                  character_name="角色1", confirmed_by_user=True, approval_ref="trusted://user-event/first")
    original = MODULE.command_snapshot(args(**common, replace=False))["assignment"]
    original_path = project / original["snapshot_path"]
    original_bytes = original_path.read_bytes()
    manifest_bytes = MODULE.project_manifest_path(project).read_bytes()
    update = add_args(library=library, voice_id="V1", audio=write_wav(tmp_path / "second.wav", seconds=4))
    update.replace = True
    MODULE.command_add(update)

    def fail_commit(pairs):
        raise MODULE.VoiceReferenceError("simulated JSON commit failure")

    monkeypatch.setattr(MODULE, "commit_json_atomic", fail_commit)
    with pytest.raises(MODULE.VoiceReferenceError, match="simulated JSON"):
        MODULE.command_snapshot(args(**common, replace=True))
    assert original_path.read_bytes() == original_bytes
    assert MODULE.project_manifest_path(project).read_bytes() == manifest_bytes


def test_explicit_cli_scope_does_not_append_project_default():
    parser = MODULE.build_parser()
    required = ["bind", "--library", "library.json", "--project", "project", "--voice-id", "V1",
                "--character-id", "C1", "--character-name", "角色", "--approval-ref", "trusted://user",
                "--intended-use", "project production"]
    assert parser.parse_args(required + ["--applicable-scope", "scene:S1"]).applicable_scope == ["scene:S1"]
    assert parser.parse_args(required).applicable_scope is None


@pytest.mark.parametrize("token,valid", [("project", True), ("scene:S-1", True), ("segment:SEG_1", True),
                                          ("S1", False), ("scene:", False), ("scene:S 1", False)])
def test_voice_scope_schema_and_runtime_accept_the_same_tokens(token, valid):
    schema = json.loads((ROOT / 'schemas/voice-reference-manifest.schema.json').read_text(encoding='utf-8'))['$defs']['scope']
    assert jsonschema.Draft202012Validator(schema).is_valid(token) is valid
    binding = {'applicable_scope': [token], 'excluded_scope': [],
               'dialogue_scope': 'all_confirmed_dialogue_for_character', 'dialogue_ids': []}
    assert (not MODULE.voice_scope_errors(binding)) is valid


@pytest.mark.parametrize("applicable,excluded,scene,segment,ok", [
    (["project"], [], "S1", "SEG1", True),
    (["scene:S1"], [], "S1", "SEG1", True),
    (["scene:S1"], [], "S2", "SEG2", False),
    (["segment:SEG1"], [], "S2", "SEG1", True),
    (["project"], ["segment:SEG1"], "S1", "SEG1", False),
    (["project"], ["scene:S1"], "S1", "SEG1", False),
    (["project"], ["project"], "S1", "SEG1", False),
])
def test_voice_sync_enforces_binding_and_excluded_scope(applicable, excluded, scene, segment, ok):
    binding = {"character_id": "C1", "status": "current", "confirmed_by_user": True,
               "approval_ref": "trusted://user", "authorization": {"status": "authorized",
                   "authorization_ref": "rights://voice", "permitted_uses": ["project production"]},
               "intended_uses": ["project production"],
               "dialogue_scope": "all_confirmed_dialogue_for_character", "dialogue_ids": [],
               "applicable_scope": applicable, "excluded_scope": excluded}
    plan = {"voiceover_lock": {"lines": [{"id": "L-1", "character_id": "C1", "text": "你好"}]},
            "segments": [{"id": segment, "scene_id": scene, "voiceover_line_ids": ["L-1"]}]}
    result = MODULE.derive_voice_sync(plan, {"assignments": [binding]}, "manifest.json")
    assert result["ok"] is ok
    if not ok:
        assert result["errors"][0]["code"] == "VOICE_REFERENCE_SCOPE_VIOLATION"


@pytest.mark.parametrize("old_selection", ["current_ids", "current_objects", "active_artifact_ids"])
def test_scope_revision_invalidates_adopted_dependencies_and_updates_state_selection(tmp_path, old_selection):
    library = tmp_path / "library.json"
    MODULE.command_init(args(library=str(library)))
    MODULE.command_add(add_args(library=library, voice_id="V1", audio=write_wav(tmp_path / "source.wav")))
    project = tmp_path / "project"
    project.mkdir()
    common = dict(library=str(library), project=str(project), voice_id="V1", character_id="C1",
                  character_name="角色1", confirmed_by_user=True, approval_ref="trusted://user")
    old = MODULE.command_snapshot(args(**common, replace=False))["assignment"]
    manifest = MODULE.load_artifact_manifest(project)
    manifest['artifacts'].extend([
        {"artifact_id": "P-OLD", "logical_id": "prompt.S1", "status": "superseded", "depends_on": ["voice.character.C1"]},
        {"artifact_id": "P-NOW", "logical_id": "prompt.S1", "status": "current", "depends_on": ["voice.character.C2"]},
        {"artifact_id": "V-NOW", "logical_id": "video.S1", "status": "current", "depends_on": ["prompt.S1"]},
        {"artifact_id": "P-C1", "logical_id": "prompt.S2", "status": "current", "depends_on": ["voice.character.C1"]},
        {"artifact_id": "V-C1", "logical_id": "video.S2", "status": "current", "depends_on": ["prompt.S2"]},
    ])
    MODULE.write_json(MODULE.artifact_manifest_path(project), manifest)
    state_path = project / "00_项目管理/project_state.json"
    ids = ["P-NOW", "V-NOW", "P-C1", "V-C1"]
    selection = {"current_artifacts": ids}
    if old_selection == "current_objects":
        selection = {"current_artifacts": [{"artifact_id": ident} for ident in ids]}
    elif old_selection == "active_artifact_ids":
        selection = {"active_artifact_ids": ids}
    MODULE.write_json(state_path, {**selection, "retained_fact": "keep"})
    state_path.with_suffix('.md').write_text('旧投影有效产物：4', encoding='utf-8')
    with pytest.raises(MODULE.VoiceReferenceError, match="--replace"):
        MODULE.command_snapshot(args(**common, replace=False, applicable_scope=["scene:S1"]))
    result = MODULE.command_snapshot(args(**common, replace=True, applicable_scope=["scene:S1"]))
    assert result['assignment']['sha256'] == old['sha256']
    assert result['assignment']['snapshot_path'] != old['snapshot_path']
    assert result['invalidated_artifacts'] == ["prompt.S2", "video.S2"]
    assert MODULE.read_json(state_path)['current_artifacts'] == ["P-NOW", "V-NOW"]
    assert MODULE.read_json(state_path)['retained_fact'] == "keep"
    assert '当前有效产物：2' in state_path.with_suffix('.md').read_text(encoding='utf-8')


def test_replacing_same_voice_cannot_silently_accept_drifted_current_snapshot(tmp_path):
    library = tmp_path / "library.json"
    MODULE.command_init(args(library=str(library)))
    MODULE.command_add(add_args(library=library, voice_id="V1", audio=write_wav(tmp_path / "source.wav")))
    project = tmp_path / "project"
    project.mkdir()
    common = dict(library=str(library), project=str(project), voice_id="V1", character_id="C1",
                  character_name="角色1", confirmed_by_user=True, approval_ref="trusted://user")
    original = MODULE.command_snapshot(args(**common, replace=False))["assignment"]
    snapshot = project / original['snapshot_path']
    snapshot.write_bytes(b'DRIFTED')
    with pytest.raises(MODULE.VoiceReferenceError, match="漂移"):
        MODULE.command_replace(args(**common, replace=True))
    assert snapshot.read_bytes() == b'DRIFTED'


@pytest.mark.parametrize("status", ["unknown", "restricted"])
def test_unapproved_rights_remain_candidates_with_empty_uses(tmp_path, status):
    library = tmp_path / "library.json"
    MODULE.command_init(args(library=str(library)))
    add = add_args(library=library, voice_id="V1", audio=write_wav(tmp_path / "source.wav"))
    add.authorization_status, add.permitted_use = status, []
    MODULE.command_add(add)
    assert MODULE.validate_library(library)[0]["authorization_status"] == status
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(MODULE.VoiceReferenceError, match="不是 authorized"):
        MODULE.command_bind(args(library=str(library), project=str(project), voice_id="V1",
            character_id="C1", character_name="角色", confirmed_by_user=True, approval_ref="trusted://user"))
    assert not MODULE.project_manifest_path(project).exists()


@pytest.mark.parametrize("permitted", [[], [""], ["   "], [42], "project production"])
def test_authorized_candidate_rejects_missing_or_malformed_uses_before_copy(tmp_path, permitted):
    library = tmp_path / "library.json"
    MODULE.command_init(args(library=str(library)))
    before = library.read_bytes()
    add = add_args(library=library, voice_id="V1", audio=write_wav(tmp_path / "source.wav"))
    add.permitted_use = permitted
    with pytest.raises(MODULE.VoiceReferenceError, match="permitted_uses"):
        MODULE.command_add(add)
    assert library.read_bytes() == before
    assert not (library.parent / "audio").exists()


def voice_fixture(tmp_path):
    library = tmp_path / "library.json"
    MODULE.command_init(args(library=str(library)))
    for ident in ("V1", "V2"):
        MODULE.command_add(add_args(library=library, voice_id=ident,
                                   audio=write_wav(tmp_path / f"{ident}.wav")))
    project = tmp_path / "project"
    project.mkdir()
    common = dict(library=str(library), project=str(project), character_id="C1", character_name="角色",
                  confirmed_by_user=True, approval_ref="trusted://user", intended_use=["project production"])
    return library, project, common


@pytest.mark.parametrize("use", [None, [], ["commercial publication"], ["PROJECT PRODUCTION"]])
def test_current_binding_requires_explicit_exact_allowed_use(tmp_path, use):
    _library, project, common = voice_fixture(tmp_path)
    with pytest.raises(MODULE.VoiceReferenceError, match="用途|intended_uses"):
        MODULE.command_bind(args(**{**common, "intended_use": use}, voice_id="V1"))
    assert not MODULE.project_manifest_path(project).exists()
    assert not (project / "07_声音与配音").exists()


@pytest.mark.parametrize("field,value", [("authorization_ref", " "), ("permitted_uses", []),
                                         ("permitted_uses", [" "])])
def test_binding_rechecks_tampered_authorization_before_creating_snapshot(tmp_path, field, value):
    library, project, common = voice_fixture(tmp_path)
    raw = MODULE.read_json(library)
    raw["voices"][0]["authorization"][field] = value
    MODULE.write_json(library, raw)
    with pytest.raises(MODULE.VoiceReferenceError, match="authorization_ref|permitted_uses"):
        MODULE.command_bind(args(**common, voice_id="V1"))
    assert not (project / "07_声音与配音").exists()


def test_legacy_snapshot_reports_unchecked_usage_and_explicit_check_is_read_only(tmp_path):
    _library, project, common = voice_fixture(tmp_path)
    MODULE.command_bind(args(**common, voice_id="V1"))
    manifest = MODULE.read_json(MODULE.project_manifest_path(project))
    del manifest["assignments"][0]["intended_uses"]
    MODULE.write_json(MODULE.project_manifest_path(project), manifest)
    before = MODULE.project_manifest_path(project).read_bytes()
    result = MODULE.validate_project(project)
    assert result["usage_checked"] is False and result["limitations"]
    assert MODULE.validate_project(project, ["project production"])["usage_checked"] is True
    with pytest.raises(MODULE.VoiceReferenceError, match="超出"):
        MODULE.validate_project(project, ["commercial publication"])
    assert MODULE.project_manifest_path(project).read_bytes() == before


def modern_prompt_fixture(project, *, dependency=True, voice_selected=True, nested=False,
                          character_id="C1", entity_id="C1"):
    part = {"dialogue_id": "DLG-1", "speaker_id": "C1", "presentation": "ONSCREEN", "text": "你好"}
    narrative = {"revision": "N1", "dialogues": [dict(part)],
                 "asset_requirements": [{"category": "CHARACTER", "entity_id": entity_id}]}
    board = {"revision": "B1", "source_narrative": {"path": "narrative.json", "revision": "N1"},
             "scenes": [{"scene_id": "SC1", "shots": [{"shot_id": "SH1", "audio": {"dialogue": [dict(part)]}}]}]}
    row = {"segment_id": "P1", "prompt": "角色说你好。", "source_shots": ["SH1"],
           "source_audio": [{"shot_id": "SH1", "dialogue": [dict(part)]}]}
    prompt = {"revision": "P1", "source_storyboard": {"path": "board.json", "revision": "B1"}}
    if nested:
        prompt["creative_sections"] = [{"section_id": "SEC1", "generation_segments": [row]}]
    else:
        prompt["generation_segments"] = [row]
    if voice_selected:
        prompt["audio_policy"] = "voice_reference"
        prompt["voice_reference_manifest_path"] = str(MODULE.project_manifest_path(project).relative_to(project))
    manifest = MODULE.load_artifact_manifest(project)
    entries = []
    for logical, filename, payload, deps in [
        ("narrative.package", "narrative.json", narrative, []),
        ("storyboard.package", "board.json", board, ["narrative.package"]),
        ("video.prompt", "prompt.json", prompt, ["storyboard.package"] +
         ([f"voice.character.{character_id}"] if dependency else [])),
    ]:
        path = project / filename
        MODULE.write_json(path, payload)
        entries.append({"artifact_id": logical, "logical_id": logical, "path_or_url": filename,
                        "revision": payload["revision"], "checksum": MODULE.file_sha256(path),
                        "status": "current", "user_approved": True, "depends_on": deps})
    entries.extend([
        {"artifact_id": "VIDEO1", "logical_id": "video.P1", "status": "current", "depends_on": ["video.prompt"]},
        {"artifact_id": "OTHER", "logical_id": "video.OTHER", "status": "current", "depends_on": ["voice.character.C2"]},
    ])
    manifest["artifacts"].extend(entries)
    MODULE.write_json(MODULE.artifact_manifest_path(project), manifest)
    MODULE.write_json(project / "00_项目管理/project_state.json", {
        "current_artifacts": [entry["artifact_id"] for entry in entries],
        "user_confirmations": [{"scope": "artifact", "artifact_ids": [entry["artifact_id"]],
                                "approval_ref": "trusted://fixture"} for entry in entries]})
    return prompt


@pytest.mark.parametrize("change", ["voice", "audio", "scope", "use"])
@pytest.mark.parametrize("nested", [False, True])
def test_modern_prompt_without_retired_plan_invalidates_exact_dependencies(tmp_path, change, nested):
    library, project, common = voice_fixture(tmp_path)
    original = MODULE.command_bind(args(**common, voice_id="V1"))["assignment"]
    old_bytes = (project / original["snapshot_path"]).read_bytes()
    modern_prompt_fixture(project, nested=nested)
    prompt_before = (project / "prompt.json").read_bytes()
    replacement = {**common, "voice_id": "V1"}
    if change == "voice":
        replacement["voice_id"] = "V2"
    elif change == "audio":
        add = add_args(library=library, voice_id="V1", audio=write_wav(tmp_path / "new.wav", seconds=4))
        add.replace = True
        MODULE.command_add(add)
    elif change == "scope":
        replacement["applicable_scope"] = ["scene:SC1"]
    else:
        raw = MODULE.read_json(library)
        raw["voices"][0]["authorization"]["permitted_uses"].append("internal preview")
        MODULE.write_json(library, raw)
        replacement["intended_use"] = ["internal preview"]
    result = MODULE.command_replace(args(**replacement))
    assert result["invalidated_segments"] == ["P1"]
    assert set(result["invalidated_artifacts"]) == {"video.prompt", "video.P1"}
    assert (project / "prompt.json").read_bytes() == prompt_before
    assert (project / "99_历史与废弃/声音母版" / Path(original["snapshot_path"]).name).read_bytes() == old_bytes
    statuses = {item["logical_id"]: item["status"] for item in MODULE.load_artifact_manifest(project)["artifacts"]}
    assert statuses["video.OTHER"] == statuses["storyboard.package"] == "current"
    assert not (project / "00_项目管理/production_plan.json").exists()
    assert MODULE.validate_project(project)["usage_checked"] is True


@pytest.mark.parametrize("failure", ["missing_dependency", "drift", "wrong_revision", "wrong_speaker", "missing_source"])
def test_modern_voice_mapping_failure_leaves_current_and_files_untouched(tmp_path, failure):
    _library, project, common = voice_fixture(tmp_path)
    MODULE.command_bind(args(**common, voice_id="V1"))
    prompt = modern_prompt_fixture(project, dependency=failure != "missing_dependency")
    if failure == "wrong_revision":
        prompt["source_storyboard"]["revision"] = "OLD"
    elif failure == "wrong_speaker":
        prompt["generation_segments"][0]["source_audio"][0]["dialogue"][0]["speaker_id"] = "C2"
    elif failure == "missing_source":
        (project / "board.json").unlink()
    if failure in {"wrong_revision", "wrong_speaker", "drift"}:
        MODULE.write_json(project / "prompt.json", prompt)
        if failure != "drift":
            manifest = MODULE.load_artifact_manifest(project)
            next(item for item in manifest["artifacts"] if item["logical_id"] == "video.prompt")["checksum"] = MODULE.file_sha256(project / "prompt.json")
            MODULE.write_json(MODULE.artifact_manifest_path(project), manifest)
        else:
            (project / "prompt.json").write_text(json.dumps({**prompt, "audio_policy": "generated"}), encoding="utf-8")
    # A rejected attempt may refresh this shared lock's owner timestamp. Every
    # business file, including audio, manifests and project state, must match.
    lock_path = project / "00_项目管理/.revision-control.lock"
    before = {str(path.relative_to(project)): path.read_bytes() for path in project.rglob("*")
              if path.is_file() and path != lock_path}
    with pytest.raises(MODULE.VoiceReferenceError, match="换声契约|现代声音引用"):
        MODULE.command_replace(args(**common, voice_id="V2"))
    after = {str(path.relative_to(project)): path.read_bytes() for path in project.rglob("*")
             if path.is_file() and path != lock_path}
    assert before == after


def test_prompt_without_selected_voice_reference_does_not_create_voice_dependency(tmp_path):
    _library, project, common = voice_fixture(tmp_path)
    MODULE.command_bind(args(**common, voice_id="V1"))
    modern_prompt_fixture(project, voice_selected=False, dependency=False)
    result = MODULE.command_replace(args(**common, voice_id="V2"))
    assert result["invalidated_segments"] == result["invalidated_artifacts"] == []


@pytest.mark.parametrize("character_id", ["C1", "CHR-C1"])
def test_modern_source_character_alias_uses_only_confirmed_entity_mapping(tmp_path, character_id):
    _library, project, common = voice_fixture(tmp_path)
    common["character_id"] = character_id
    MODULE.command_bind(args(**common, voice_id="V1"))
    modern_prompt_fixture(project, entity_id="CHR-C1", character_id=character_id)
    result = MODULE.command_replace(args(**common, voice_id="V2"))
    assert result["invalidated_segments"] == ["P1"]
    assert set(result["invalidated_artifacts"]) == {"video.prompt", "video.P1"}


def test_ambiguous_confirmed_character_alias_blocks_before_new_snapshot(tmp_path):
    _library, project, common = voice_fixture(tmp_path)
    MODULE.command_bind(args(**common, voice_id="V1"))
    modern_prompt_fixture(project, entity_id="CHR-C1")
    narrative = MODULE.read_json(project / "narrative.json")
    narrative["asset_requirements"].append({"category": "CHARACTER", "entity_id": "C1"})
    MODULE.write_json(project / "narrative.json", narrative)
    manifest = MODULE.load_artifact_manifest(project)
    next(item for item in manifest["artifacts"] if item["logical_id"] == "narrative.package")["checksum"] = MODULE.file_sha256(project / "narrative.json")
    MODULE.write_json(MODULE.artifact_manifest_path(project), manifest)
    # Shared lock owner metadata may refresh; all business bytes must stay put.
    lock_path = project / "00_项目管理/.revision-control.lock"
    before = {str(path.relative_to(project)): path.read_bytes() for path in project.rglob("*")
              if path.is_file() and path != lock_path}
    with pytest.raises(MODULE.VoiceReferenceError, match="别名不唯一"):
        MODULE.command_replace(args(**common, voice_id="V2"))
    assert before == {str(path.relative_to(project)): path.read_bytes() for path in project.rglob("*")
                      if path.is_file() and path != lock_path}


def test_modern_voice_mapping_can_follow_source_shots_without_duplicate_audio_table(tmp_path):
    _library, project, common = voice_fixture(tmp_path)
    MODULE.command_bind(args(**common, voice_id="V1"))
    prompt = modern_prompt_fixture(project)
    prompt["generation_segments"][0].pop("source_audio")
    MODULE.write_json(project / "prompt.json", prompt)
    manifest = MODULE.load_artifact_manifest(project)
    next(item for item in manifest["artifacts"] if item["logical_id"] == "video.prompt")["checksum"] = MODULE.file_sha256(project / "prompt.json")
    MODULE.write_json(MODULE.artifact_manifest_path(project), manifest)
    assert MODULE.command_replace(args(**common, voice_id="V2"))["invalidated_segments"] == ["P1"]


@pytest.fixture
def sync_plan_project(tmp_path):
    project = tmp_path / "project"
    plan = project / "workflow" / "production_plan.json"
    manifest = MODULE.project_manifest_path(project)
    state = project / "00_项目管理" / "project_state.json"
    MODULE.write_json(plan, {"segments": [{"id": "SEG-1", "voiceover_line_ids": [],
                                           "speaking_characters": [{"character_id": "C1"}]}]})
    MODULE.write_json(manifest, {"schema_version": "2.0", "assignments": []})
    MODULE.write_json(state, {"schema_version": "2.0", "custom_field": "preserve"})
    return project, plan, manifest, state


@pytest.mark.parametrize("field", ["plan", "manifest"])
@pytest.mark.parametrize("path_kind", ["absolute", "parent", "symlink"])
def test_sync_plan_rejects_project_escape_without_writes(sync_plan_project, field, path_kind):
    project, plan, manifest, state = sync_plan_project
    outside = project.parent / "outside" / f"{field}.json"
    MODULE.write_json(outside, MODULE.read_json(plan if field == "plan" else manifest))
    if path_kind == "absolute":
        escaped = str(outside)
    elif path_kind == "parent":
        escaped = str(Path("..") / "outside" / outside.name)
    else:
        link = project / f"external-{field}.json"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks unavailable: {exc}")
        escaped = link.name
    values = {"project": str(project), "plan": str(plan.relative_to(project)),
              "manifest": str(manifest.relative_to(project))}
    values[field] = escaped
    originals = {path: path.read_bytes() for path in (plan, manifest, state, outside)}

    with pytest.raises(MODULE.VoiceReferenceError, match="必须位于项目内"):
        MODULE.command_sync_plan(args(**values))

    assert {path: path.read_bytes() for path in originals} == originals


@pytest.mark.parametrize("absolute", [False, True])
def test_sync_plan_accepts_paths_inside_project(sync_plan_project, absolute):
    project, plan, manifest, state = sync_plan_project
    plan_arg = plan if absolute else plan.relative_to(project)
    manifest_arg = manifest if absolute else manifest.relative_to(project)
    result = MODULE.command_sync_plan(args(project=str(project), plan=str(plan_arg), manifest=str(manifest_arg)))

    assert Path(result["plan"]) == plan.resolve()
    assert MODULE.read_json(plan)["segments"][0]["speaking_characters"] == []
    assert MODULE.read_json(state)["custom_field"] == "preserve"
    assert MODULE.read_json(state)["voice_reference_manifest_path"] == str(manifest.relative_to(project))


def test_sync_plan_lock_timeout_returns_cli_error(sync_plan_project, monkeypatch, capsys):
    import threading
    from core.io import PROJECT_LOCK_RELATIVE, file_lock

    project, plan, manifest, state = sync_plan_project
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock():
        with file_lock(project / PROJECT_LOCK_RELATIVE):
            acquired.set()
            release.wait(timeout=3)

    owner = threading.Thread(target=hold_lock)
    owner.start()
    try:
        assert acquired.wait(timeout=2)
        monkeypatch.setattr(MODULE, "shared_project_lock", lambda root:
                            file_lock(root / PROJECT_LOCK_RELATIVE, timeout_seconds=0.01))
        originals = {path: path.read_bytes() for path in (plan, manifest, state)}
        result = MODULE.main(["sync-plan", "--project", str(project),
                              "--plan", str(plan), "--manifest", str(manifest)])
        captured = capsys.readouterr()
        assert result == 1
        assert captured.out == ""
        assert captured.err.startswith("ERROR: ")
        assert "无法获取项目声音锁" in captured.err
        assert {path: path.read_bytes() for path in originals} == originals
    finally:
        release.set()
        owner.join(timeout=3)
    assert not owner.is_alive()
