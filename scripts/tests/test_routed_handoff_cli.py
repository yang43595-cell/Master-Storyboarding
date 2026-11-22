from pathlib import Path

import pytest

from scripts import stage_context_cli as cli
from scripts.core.io import write_json
from scripts.core.project_state import new_project_state, write_project_state


def test_new_project_routes_initial_creative_and_blocks_early_director(tmp_path):
    state = new_project_state(current_stage="creative-director")
    state["next_stage"] = "storyboard-planner"
    write_project_state(tmp_path, state)
    write_json(tmp_path / "00_项目管理/artifact_manifest.json", {"artifacts": []})
    write_json(tmp_path / cli.REQUEST_PATH, {
        "project_mode": "new",
        "kickoff": {"decision_ref": "fixture:kickoff", "product": {"mode": "none"}},
    })
    command = ["handoff", "--project", str(tmp_path)]
    before_status = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    status = cli.status(cli.parser().parse_args(["status", "--project", str(tmp_path)]))
    assert status["next_allowed_stage"] == "creative-director"
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before_status
    first = cli.handoff(cli.parser().parse_args(command))
    action, = first["next_actions"]
    assert action["skill"] == "creative-director"
    assert status["steps"] == first["steps"]
    handoff_path = Path(action["handoff_path"])
    before = handoff_path.stat().st_mtime_ns
    assert cli.handoff(cli.parser().parse_args(command)) == first
    assert handoff_path.stat().st_mtime_ns == before

    with pytest.raises(ValueError, match="director 当前未就绪"):
        cli.handoff(cli.parser().parse_args([*command, "--to-skill", "storyboard-planner"]))
    prepare = ["prepare", "--project", str(tmp_path), "--objective", "编导开工"]
    with pytest.raises(ValueError, match="director 当前未就绪"):
        cli.prepare(cli.parser().parse_args([*prepare, "--skill", "storyboard-planner"]))
    result = cli.prepare(cli.parser().parse_args([
        *prepare, "--skill", "creative-director", "--context-id", action["context_id"],
    ]))
    assert result["valid"]
    assert "程序交接范围" in Path(result["path"]).read_text(encoding="utf-8")

    tx = tmp_path / "00_项目管理/revision_transactions/REV-TX-fixture"
    write_json(tx / "transaction.json", {"files": []})
    write_json(tx / "commit_intent.json", {"files": []})
    status = cli.status(cli.parser().parse_args(["status", "--project", str(tmp_path)]))
    assert not status["production_allowed"] and not status["next_actions"]
    assert status["steps"] == cli.handoff(cli.parser().parse_args(command))["steps"]
    with pytest.raises(cli.StageContextError, match="需显式恢复"):
        cli.handoff(cli.parser().parse_args([*command, "--to-skill", "creative-director"]))
    with pytest.raises(ValueError, match="需显式恢复"):
        cli.prepare(cli.parser().parse_args([
            *prepare, "--skill", "creative-director", "--context-id", action["context_id"],
        ]))
    assert not (tx / "result.json").exists()
