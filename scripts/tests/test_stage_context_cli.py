"""Lean tests for stage_context_cli after retiring the prepare/mark-read check-in ceremony."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_stage_context_cli_help_excludes_retired_prepare_ceremony() -> None:
    res = subprocess.run(
        [sys.executable, str(REPO / "scripts/stage_context_cli.py"), "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    for kept in ("status", "kickoff", "authorize-workflow", "open-change", "accept-artifact", "adopt-package", "confirm-script-in-chat", "handoff"):
        assert kept in res.stdout
    for retired in ("ensure-prepared", "record-method-reading", "review-pack"):
        assert retired not in res.stdout


def test_kickoff_can_register_workflow_authorization_and_status_exits_cleanly(tmp_path: Path) -> None:
    proj = tmp_path / "demo_proj"
    subprocess.run(
        [sys.executable, str(REPO / "scripts/init_desktop_project.py"), "--path", str(proj)],
        cwd=REPO,
        check=True,
    )
    answers = proj / "answers.json"
    answers.write_text(json.dumps({
        "decision_ref": "user-kickoff-test-ref",
        "creation": {"workflow": "creative", "creative_scope": "测试项目", "target_duration_seconds": 60, "target_duration_note": "60s"},
        "product": {"mode": "none"},
        "canvas": {
            "platform": "redframe",
            "model": "Seedance 2.5",
            "aspect_ratio": "9:16",
            "resolution": "480p",
            "target_url": "https://redframe-ai.xmpaohong.com:28888/#/workspace?canvasId=test-canvas",
            "target_project_id": "newProject00000000000000000000000000000000",
        },
        "image_generation": {"provider": "redframe", "model": "Seedream 5.0 Pro", "fee_scope": "existing_quota", "reference_upload_authorized": True, "scope": "required_assets_and_supplements", "authorization_ref": "user-kickoff-test-ref"},
    }, ensure_ascii=False), encoding="utf-8")
    res = subprocess.run(
        [
            sys.executable, str(REPO / "scripts/stage_context_cli.py"),
            "kickoff", "--project", str(proj),
            "--answers-json", str(answers),
            "--authorization-id", "FLOW-TEST",
            "--authorize-skill", "storyboard-planner",
            "--authorize-skill", "storyboard-director",
            "--authorize-skill", "voiceover-to-video-script",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(res.stdout)
    assert data["authorization"]["id"] == "FLOW-TEST"
    st = subprocess.run(
        [sys.executable, str(REPO / "scripts/stage_context_cli.py"), "status", "--project", str(proj)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    st_json = json.loads(st.stdout)
    assert st_json["read_only"] is True
    assert "steps" in st_json
