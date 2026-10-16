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
                "--character-id", "C1", "--character-name", "角色", "--approval-ref", "trusted://user"]
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
               "approval_ref": "trusted://user", "authorization": {"status": "authorized"},
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
