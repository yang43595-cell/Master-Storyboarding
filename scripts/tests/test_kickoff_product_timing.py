from copy import deepcopy
import json

import pytest

from scripts import stage_context_cli
from scripts.core.io import write_json
from scripts.core.project_kickoff import load_kickoff, product_demo_timing_advisory
from scripts.init_desktop_project import initialize


@pytest.fixture
def kickoff():
    return {
        "decision_ref": "synthetic source and product choice",
        "creation": {
            "target_duration_seconds": 224.72,
            "product_demo_timing": {
                "original_seconds": 19.28,
                "estimated_minimum_seconds": 25.88,
                "estimate_basis": "synthetic verified clips, source intervals and required visible actions",
            },
        },
        "product": {"mode": "replace", "product_id": "synthetic-mask", "product_usage": "real_use"},
        "canvas": {
            "platform": "redframe", "model": "Seedance 2.5", "resolution": "480p",
            "aspect_ratio": "9:16", "create_new": True,
        },
    }


def test_kickoff_advisory_uses_only_the_original_or_explicitly_decided_window(kickoff):
    original = deepcopy(kickoff)
    advisory = product_demo_timing_advisory(kickoff)
    assert advisory == {
        "status": "conflict", "window_seconds": 19.28, "window_source": "original",
        "estimated_minimum_seconds": 25.88,
        "estimate_basis": "synthetic verified clips, source intervals and required visible actions",
        "shortfall_seconds": 6.6,
    }
    assert kickoff == original

    timing = kickoff["creation"]["product_demo_timing"]
    timing["target_seconds"] = 26.28
    assert product_demo_timing_advisory(kickoff) == advisory

    timing.update(extension_seconds=7, decision_ref="synthetic user approval of a seven-second extension")
    confirmed = product_demo_timing_advisory(kickoff)
    assert confirmed["status"] == "fits" and confirmed["shortfall_seconds"] == 0
    assert (confirmed["window_source"], confirmed["window_seconds"]) == ("confirmed", 26.28)
    assert kickoff["creation"]["target_duration_seconds"] == 224.72


def test_kickoff_advisory_keeps_legacy_missing_evidence_and_other_products_nonblocking(kickoff):
    timing = kickoff["creation"]["product_demo_timing"]
    timing.pop("estimate_basis")
    assert product_demo_timing_advisory(kickoff)["status"] == "unchecked"
    timing.update(target_seconds=26.28, extension_seconds=7, decision_ref="synthetic user choice")
    timing.pop("estimated_minimum_seconds")
    assert product_demo_timing_advisory(kickoff)["window_seconds"] == 26.28
    kickoff["creation"].pop("product_demo_timing")
    assert product_demo_timing_advisory(kickoff) == {"status": "unchecked", "reason": "missing_window"}
    kickoff["creation"].clear()
    assert product_demo_timing_advisory(kickoff) is None
    kickoff["product"]["product_usage"] = "display"
    assert product_demo_timing_advisory(kickoff) is None
    assert product_demo_timing_advisory(None) is None


def test_product_timing_reaches_kickoff_and_the_frozen_creative_task(tmp_path, capsys, kickoff):
    project = initialize(tmp_path / "synthetic")
    answers = tmp_path / "answers.json"
    write_json(answers, kickoff)
    kickoff_args = ["kickoff", "--project", str(project)]
    assert stage_context_cli.main([*kickoff_args, "--answers-json", str(answers)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["product_demo_timing"]["status"] == "conflict"
    assert load_kickoff(project) == kickoff
    assert stage_context_cli.main(kickoff_args) == 0
    assert json.loads(capsys.readouterr().out)["product_demo_timing"] == result["product_demo_timing"]

    stage_args = ["--project", str(project), "--skill", "creative-director",
                  "--actor-id", "test-actor", "--task-id", "test-task"]
    assert stage_context_cli.main(["prepare", *stage_args, "--objective", "verify same project input"]) == 0
    capsys.readouterr()
    assert stage_context_cli.main(["ensure-prepared", *stage_args, "--show-task-basis"]) == 0
    task_basis = json.loads(capsys.readouterr().out)["task_basis"]
    assert task_basis["kickoff"] == kickoff
    assert task_basis["product_demo_timing"] == result["product_demo_timing"]
