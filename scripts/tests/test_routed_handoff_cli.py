from pathlib import Path

import pytest

from scripts import stage_context_cli as cli
from scripts.core.io import write_json
from scripts.core.project_state import new_project_state, write_project_state


def test_new_project_routes_initial_creative_and_blocks_early_director(tmp_path):
    write_project_state(tmp_path, new_project_state(current_stage="creative-director"))
    write_json(tmp_path / cli.REQUEST_PATH, {
        "project_mode": "new",
        "kickoff": {"decision_ref": "fixture:kickoff", "product": {"mode": "none"}},
    })
    command = ["handoff", "--project", str(tmp_path)]
    first = cli.handoff(cli.parser().parse_args(command))
    action, = first["next_actions"]
    assert action["skill"] == "creative-director"
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
