"""Project-local CLI integration using synthetic sources and authorizations only."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import stage_context_cli as cli
from scripts.core import agent_acceptance, artifacts
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state, new_project_state, write_project_state
from test_formal_package_submission import board_candidate, confirmed_narrative


ROOT = Path(__file__).resolve().parents[2]
SKILL = "storyboard-planner"


def director_candidate(project):
    narrative, _ = confirmed_narrative(project)
    payload = board_candidate(project, narrative)
    source = project / "03_导演" / "R01.json"
    write_json(source, payload)
    entry = artifacts.register_workspace_artifact(project, {
        "artifact_id": "DIR-R01", "logical_id": "storyboard.package",
        "type": "storyboard.package", "revision": payload["revision"],
        "path_or_url": source.relative_to(project).as_posix(),
        "checksum": artifacts.digest(source), "depends_on": ["narrative.package"],
    })
    return entry


def self_check(project, entry):
    profile_path = ROOT / "skills" / SKILL / "review-profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    report = {
        "schema_version": "1.0", "kind": "agent-self-check",
        "authorization_id": "SYNTHETIC-FLOW", "agent_id": "synthetic-producer",
        "checked_at": "2026-11-05T01:02:03+08:00", "scope": "artifact", "verdict": "pass",
        **agent_acceptance.acceptance_binding(project, entry),
        "review_profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        "checks": [{"id": check["id"], "result": "pass",
                    "observation": "Synthetic fixture observation for " + check["id"],
                    "locator": "03_导演/R01.json:scenes[0]"}
                   for check in profile["production_artifact_checks"]],
    }
    path = project / "00_项目管理" / "synthetic-self-check.json"
    write_json(path, report)
    return path, report


def authorize(project):
    return cli.main(["authorize-workflow", "--project", str(project),
                     "--authorization-id", "SYNTHETIC-FLOW",
                     "--authorization-ref", "synthetic user: this project director workflow only",
                     "--skill", SKILL])


def test_cli_accepts_registered_candidate_without_manufacturing_human_confirmation(tmp_path, capsys):
    entry = director_candidate(tmp_path)
    before_confirmations = copy.deepcopy(load_project_state(tmp_path)["user_confirmations"])
    assert authorize(tmp_path) == 0
    authorization = json.loads(capsys.readouterr().out)
    assert authorization["ok"] is True and authorization["authorization"]["skills"] == [SKILL]
    report_path, _ = self_check(tmp_path, entry)

    assert cli.main(["accept-artifact", "--project", str(tmp_path), "--artifact-id", entry["artifact_id"],
                     "--self-check-json", report_path.relative_to(tmp_path).as_posix()]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    state = load_project_state(tmp_path)
    adopted = next(row for row in artifacts.load_manifest(tmp_path)["artifacts"]
                   if row["artifact_id"] == entry["artifact_id"])
    assert adopted["status"] == "current" and adopted["user_approved"] is False
    assert adopted["agent_acceptance"]
    assert state["user_confirmations"] == before_confirmations
    assert artifacts.artifact_is_confirmed(adopted, state, tmp_path) is False
    assert artifacts.artifact_is_adopted(adopted, state, tmp_path) is True
    _, inputs = cli._confirmed_project_inputs(tmp_path, {**state, "current_artifacts": [entry["artifact_id"]]})
    assert [row["artifact_id"] for row in inputs] == [entry["artifact_id"]]


@pytest.mark.parametrize("skill", ["creative-director", "asset-image-assistant",
                                  "voice-reference-manager", "blender-previs"])
def test_cli_cannot_authorize_human_or_optional_stages(tmp_path, skill):
    write_project_state(tmp_path, new_project_state())
    state_path = tmp_path / "00_项目管理" / "project_state.json"
    before = state_path.read_bytes()
    with pytest.raises(ValueError):
        cli.main(["authorize-workflow", "--project", str(tmp_path),
                  "--authorization-id", "SYNTHETIC-FLOW", "--authorization-ref", "synthetic only",
                  "--skill", skill])
    assert state_path.read_bytes() == before


@pytest.mark.parametrize("change", ["missing_authorization", "wrong_hash", "partial_checks"])
def test_failed_cli_acceptance_keeps_candidate_and_existing_confirmations(tmp_path, capsys, change):
    entry = director_candidate(tmp_path)
    if change != "missing_authorization":
        assert authorize(tmp_path) == 0
        capsys.readouterr()
    report_path, report = self_check(tmp_path, entry)
    if change == "wrong_hash":
        report["artifact"]["sha256"] = "0" * 64
    elif change == "partial_checks":
        report["checks"] = report["checks"][:1]
    write_json(report_path, report)
    before_state = load_project_state(tmp_path)
    before_manifest = artifacts.load_manifest(tmp_path)
    with pytest.raises(ValueError):
        cli.main(["accept-artifact", "--project", str(tmp_path), "--artifact-id", entry["artifact_id"],
                  "--self-check-json", str(report_path)])
    assert load_project_state(tmp_path) == before_state
    assert artifacts.load_manifest(tmp_path) == before_manifest


def test_cli_acceptance_does_not_register_an_unknown_artifact(tmp_path):
    write_project_state(tmp_path, new_project_state())
    report_path = tmp_path / "self-check.json"
    write_json(report_path, {})
    with pytest.raises(ValueError):
        cli.main(["accept-artifact", "--project", str(tmp_path), "--artifact-id", "NOT-REGISTERED",
                  "--self-check-json", str(report_path)])
    assert artifacts.load_manifest(tmp_path)["artifacts"] == []


def test_direct_cli_entry_records_only_explicit_workflow_scope(tmp_path):
    write_project_state(tmp_path, new_project_state())
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts" / "stage_context_cli.py"),
        "authorize-workflow", "--project", str(tmp_path), "--authorization-id", "SYNTHETIC-FLOW",
        "--authorization-ref", "synthetic user: director and prompt stages",
        "--skill", SKILL, "--skill", "voiceover-to-video-script",
    ], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert set(report["authorization"]["skills"]) == {SKILL, "voiceover-to-video-script"}
    state = load_project_state(tmp_path)
    assert state["user_confirmations"] == [] and state["current_artifacts"] == []
