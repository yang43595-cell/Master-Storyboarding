from copy import deepcopy
from io import StringIO
import json

import pytest

from scripts import stage_context_cli
from scripts.core import product_reference_pack
from scripts.core.artifacts import digest
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


def test_kickoff_advisory_uses_only_an_explicitly_decided_window(kickoff):
    original = deepcopy(kickoff)
    assert product_demo_timing_advisory(kickoff) is None
    assert kickoff == original

    timing = kickoff["creation"]["product_demo_timing"]
    timing["target_seconds"] = 19.28
    assert product_demo_timing_advisory(kickoff) is None

    timing["decision_ref"] = "synthetic user approval to retain the original window"
    advisory = product_demo_timing_advisory(kickoff)
    assert advisory == {
        "status": "conflict", "window_seconds": 19.28, "window_source": "confirmed",
        "estimated_minimum_seconds": 25.88,
        "estimate_basis": "synthetic verified clips, source intervals and required visible actions",
        "shortfall_seconds": 6.6,
    }
    timing.update(target_seconds=26.28, decision_ref="synthetic user approval of a seven-second extension")
    confirmed = product_demo_timing_advisory(kickoff)
    assert confirmed["status"] == "fits" and confirmed["shortfall_seconds"] == 0
    assert (confirmed["window_source"], confirmed["window_seconds"]) == ("confirmed", 26.28)
    assert kickoff["creation"]["target_duration_seconds"] == 224.72


def test_kickoff_advisory_keeps_legacy_missing_evidence_and_other_products_nonblocking(kickoff):
    timing = kickoff["creation"]["product_demo_timing"]
    timing.pop("estimate_basis")
    assert product_demo_timing_advisory(kickoff) is None
    timing.update(target_seconds=26.28, decision_ref="synthetic user choice")
    assert product_demo_timing_advisory(kickoff)["status"] == "unchecked"
    timing.pop("estimated_minimum_seconds")
    assert product_demo_timing_advisory(kickoff)["window_seconds"] == 26.28
    kickoff["creation"].pop("product_demo_timing")
    assert product_demo_timing_advisory(kickoff) is None
    kickoff["creation"].clear()
    assert product_demo_timing_advisory(kickoff) is None
    kickoff["product"]["product_usage"] = "display"
    assert product_demo_timing_advisory(kickoff) is None
    assert product_demo_timing_advisory(None) is None


def test_product_timing_reaches_kickoff_and_the_frozen_creative_task(tmp_path, capsys, monkeypatch, kickoff):
    project = initialize(tmp_path / "synthetic")
    request_path = project / stage_context_cli.REQUEST_PATH
    request = json.loads(request_path.read_text(encoding="utf-8"))
    source = "01_原始资料/原始文案.txt"
    (project / source).write_text("synthetic source", encoding="utf-8")
    write_json(request_path, {**request, "source_files": [source]})
    kickoff.pop("canvas")
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(kickoff)))
    kickoff_args = ["kickoff", "--project", str(project)]
    assert stage_context_cli.main([*kickoff_args, "--answers-json", "-"]) == 0
    assert "product_demo_timing" not in json.loads(capsys.readouterr().out)

    timing = kickoff["creation"]["product_demo_timing"]
    timing.update(target_seconds=19.28, decision_ref="synthetic user locks the window")
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(kickoff)))
    assert stage_context_cli.main([*kickoff_args, "--answers-json", "-"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["product_demo_timing"]["status"] == "conflict"
    assert load_kickoff(project) == kickoff
    assert stage_context_cli.main(kickoff_args) == 0
    assert json.loads(capsys.readouterr().out)["product_demo_timing"] == result["product_demo_timing"]

    stage_args = ["--project", str(project), "--skill", "creative-director",
                  "--actor-id", "test-actor", "--task-id", "test-task"]
    assert stage_context_cli.main(["prepare", *stage_args, "--objective", "verify same project input",
                                   "--request-json", str(request_path)]) == 0
    capsys.readouterr()
    assert stage_context_cli.main(["ensure-prepared", *stage_args, "--show-task-basis"]) == 0
    task_basis = json.loads(capsys.readouterr().out)["task_basis"]
    assert task_basis["kickoff"] == kickoff
    assert task_basis["product_demo_timing"] == result["product_demo_timing"]
    assert task_basis["input_versions"]["source_files"] == [source]


@pytest.mark.parametrize("another_product", [False, True])
def test_kickoff_lists_products_and_only_pins_the_users_choice(tmp_path, capsys, monkeypatch, kickoff, another_product):
    catalog = tmp_path / "catalog"
    monkeypatch.setattr(product_reference_pack, "PACK_ROOT", catalog)
    options = [("cream", "面霜")]
    if another_product:
        options.append(("mask", "面膜"))
    for ident, name in options:
        path = catalog / ident / "R01"
        path.mkdir(parents=True)
        media = path / "box.jpg"
        media.write_text(name)
        write_json(path / "manifest.json", dict(
            product_id=ident, product_name=name, revision="R01",
            references=[dict(id="box", role="packaging", usages=["real_use"], path=media.name,
                             source="fixture", coverage="wrapper", sha256=digest(media))]))
    project = initialize(tmp_path / "project")
    args = ["kickoff", "--project", str(project)]
    assert stage_context_cli.main(args) == 2
    pending = json.loads(capsys.readouterr().out)
    assert pending["kickoff"] is None
    assert [p["product_id"] for p in pending["product_choices"]] == [ident for ident, _ in options]
    assert load_kickoff(project) is None
    assert stage_context_cli.main(["handoff", "--project", str(project)]) == 0
    handoff = json.loads(capsys.readouterr().out)
    assert handoff["steps"][0]["product_choices"] == pending["product_choices"]
    assert handoff["next_actions"] == []
    kickoff["product"]["product_id"] = "cream"
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(kickoff)))
    assert stage_context_cli.main([*args, "--answers-json", "-"]) == 0
    selected = json.loads(capsys.readouterr().out)
    assert selected["product_reference_pack"]["product_id"] == "cream"
    assert load_kickoff(project)["product"]["reference_pack_revision"] == "R01"


def test_superseded_pack_is_hidden_for_new_projects_and_remains_resolvable(tmp_path, capsys, monkeypatch, kickoff):
    catalog = tmp_path / "catalog"
    monkeypatch.setattr(product_reference_pack, "PACK_ROOT", catalog)
    project = initialize(tmp_path / "existing")
    kickoff["product"]["product_id"] = "mask"

    def make_pack(revision, previous=None):
        folder = catalog / "mask" / revision
        folder.mkdir(parents=True)
        photo = folder / "box.jpg"
        photo.write_text(revision)
        manifest = dict(product_id="mask", revision=revision, product_name="测试商品",
                        references=[dict(id="box", role="packaging", usages=["real_use"],
                                         path=photo.name, source="fixture", coverage="wrapper", sha256=digest(photo))])
        if previous:
            manifest["supersedes_for_new_selection"] = previous
        write_json(folder / "manifest.json", manifest)

    def register(root, answers):
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(answers)))
        return stage_context_cli.main(["kickoff", "--project", str(root), "--answers-json", "-"])

    make_pack("R04")
    assert register(project, kickoff) == 0
    locked = json.loads(capsys.readouterr().out)["kickoff"]
    assert locked["product"]["reference_pack_revision"] == "R04"
    make_pack("R05", "R04")
    assert [v["revision"] for v in product_reference_pack.product_catalog()[0]["versions"]] == ["R05"]
    assert register(project, locked) == 0
    assert json.loads(capsys.readouterr().out)["product_reference_pack"]["revision"] == "R04"
    fresh = initialize(tmp_path / "fresh")
    with pytest.raises(stage_context_cli.StageContextError, match="历史产品包"):
        register(fresh, locked)
    assert register(fresh, kickoff) == 0
    assert json.loads(capsys.readouterr().out)["product_reference_pack"]["revision"] == "R05"
