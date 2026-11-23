"""Local director handoff: frozen choices, precise returns and revision drift."""

from copy import deepcopy

import pytest

from scripts import return_director_segment as cli
from scripts.core import director_inputs as di
from scripts.core.artifacts import digest
from scripts.core.io import read_json, write_json
from scripts.core.recreation_segments import segment_steps


def test_reshoot_prefill_and_segment_return(tmp_path, monkeypatch):
    root = tmp_path
    paragraph = {"paragraph_id": "P", "text": "她递碗。"}
    line = dict(dialogue_id="D", text="给你。", speaker_id="她", source_paragraph_id="P")
    story = dict(
        revision="R01",
        story_paragraphs=[paragraph],
        dialogues=[line],
        asset_requirements=[dict(asset_id="A", name="碗", source_paragraph_ids=["P"])],
    )
    write_json(root / "story.json", story)
    entry = dict(
        artifact_id="N1", revision="R01", path_or_url="story.json", checksum=digest(root / "story.json")
    )
    source = di.sources(root, entry)
    assert source["package_fields"]["source_dialogue_ids"] == ["D"]
    assert source["paragraphs"] == [
        dict(**paragraph, dialogues=[line], assets=[dict(asset_id="A", name="碗")])
    ]
    movie = root / "bowl.mp4"
    movie.write_bytes(b"fixture")
    row = dict(
        id="S",
        frames=[0, 25],
        mode="rewrite",
        motion="实操",
        reference_frames=[0, 25],
        appearance=[],
        paragraph_ids=["P"],
        dialogue_ids=["D"],
        change="改成她先递碗",
    )
    plan = dict(
        narrative_id="N1",
        narrative_sha256=entry["checksum"],
        fps=25,
        first_frame=0,
        end_frame=25,
        segments=[row],
        references={"实操": dict(path=movie.name, sha256=digest(movie), frame_count=25, role="depth")},
    )
    write_json(root / di.PLAN, plan)
    di.dispatch(root, entry, ["S"])
    job = read_json(di.job_path(root, "S"))
    bypass = deepcopy(plan)
    bypass["segments"][0]["mode"] = "depth"
    assert segment_steps(root, story, entry, plan=bypass)[0]["status"] == "waiting"
    monkeypatch.setattr(cli.handoff, "_package", lambda *args: (entry, story))
    monkeypatch.setattr(cli.handoff, "require_production_handoff", lambda *args, **kwargs: None)
    result = dict(
        segment_id="S",
        job_sha256=job["job_sha256"],
        mode="depth",
        motion="实操",
        reference_frames=[0, 25],
        appearance=[],
        decision="她先递碗再说话",
        entry_state="碗在她手中",
        exit_state="对方持碗",
        reference_use="实拍给出递碗动作",
    )
    with pytest.raises(ValueError, match="exit_state"):
        cli.submit(root, dict(result, exit_state=""))
    cli.submit(root, result)
    saved = (root / di.PLAN).read_bytes()
    cli.submit(root, result)
    assert (root / di.PLAN).read_bytes() == saved
    assert segment_steps(root, story, entry)[0]["action"] == "video"
    movie.write_bytes(b"changed")
    with pytest.raises(ValueError, match="参考"):
        cli.submit(root, result)
