"""Local director handoff: frozen choices, precise returns and revision drift."""

from copy import deepcopy

import pytest

from scripts import return_director_segment as cli
from scripts.core import director_inputs as di
from scripts.core.artifacts import digest
from scripts.core.io import read_json, sha256_json, write_json
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


def test_return_binding_binds_appearance_only_in_order_and_keeps_legacy_motion_digest():
    box, label, clip = ({"sha256": name} for name in ("box", "label", "old-motion"))
    refs = {"box": box, "label": label, "clip": clip}
    row = dict(id="S", frames=[0, 25], paragraph_ids=["P"], dialogue_ids=[], change="展示包装",
               mode="product-display", motion=None, appearance=["box", "label"], reference_frames=[],
               decision="定机", entry_state="包装已放好", exit_state="包装仍在", reference_use="外观")
    fields = ("id", "frames", "paragraph_ids", "dialogue_ids", "change", "mode", "motion",
              "appearance", "reference_frames", "decision", "entry_state", "exit_state", "reference_use")
    bound = di.return_binding(row, refs, "job")
    assert bound == sha256_json(["job", [row[key] for key in fields], [box, label]])
    assert di.return_binding(dict(row, appearance=["label", "box"]), refs, "job") != bound
    assert di.return_binding(row, dict(refs, label={"sha256": "new-label"}), "job") != bound
    assert di.return_binding(row, dict(refs, clip={"sha256": "irrelevant"}), "job") == bound
    for motion_mode in ("depth", "product"):
        legacy = dict(row, mode=motion_mode, motion="clip", reference_frames=[0, 25])
        assert di.return_binding(legacy, refs, "job") == sha256_json(
            ["job", [legacy[key] for key in fields], [clip, box, label]]
        )
    for invalid in (dict(motion="clip"), dict(motion=""), dict(reference_frames=[0, 25]),
                    dict(appearance=[]), dict(appearance="box"), dict(appearance=[None]), dict(mode="depth")):
        with pytest.raises(ValueError):
            di.return_binding(dict(row, **invalid), refs, "job")
    with pytest.raises(KeyError):
        di.return_binding(dict(row, appearance=["missing"]), refs, "job")


def test_packaging_only_director_return_rejects_stale_appearance(tmp_path, monkeypatch):
    root = tmp_path
    story = dict(revision="R01", story_paragraphs=[dict(paragraph_id="P", text="只展示闭盒。")],
                 dialogues=[], asset_requirements=[])
    write_json(root / "story.json", story)
    entry = dict(artifact_id="N1", revision="R01", path_or_url="story.json",
                 checksum=digest(root / "story.json"))
    files = root / "01_原始资料"
    files.mkdir()
    box, alternate = files / "box.png", files / "box-new.png"
    box.write_bytes(b"approved packaging")
    alternate.write_bytes(b"other packaging")
    ref = dict(path=str(box.relative_to(root)), sha256=digest(box))
    newer = dict(path=str(alternate.relative_to(root)), sha256=digest(alternate))
    row = dict(id="S", frames=[0, 25], mode="rewrite", motion=None, reference_frames=[],
               appearance=["包装"], paragraph_ids=["P"], dialogue_ids=[], change="不展示取盒或手部动作")
    plan = dict(narrative_id="N1", narrative_sha256=entry["checksum"], fps=25, first_frame=0, end_frame=25,
                segments=[row], references={"包装": ref},
                product_pack=dict(product_id="fixture-display", packaging=["包装"], source=ref))
    write_json(root / di.PLAN, plan)
    di.dispatch(root, entry, ["S"])
    job = read_json(di.job_path(root, "S"))
    monkeypatch.setattr(cli.handoff, "_package", lambda *args: (entry, story))
    monkeypatch.setattr(cli.handoff, "require_production_handoff", lambda *args, **kwargs: None)
    monkeypatch.setattr("scripts.core.recreation_segments.load_kickoff", lambda _: {
        "product": dict(mode="retain", product_id="fixture-display", product_usage="display")
    })
    assert segment_steps(root, story, entry)[0]["action"] == "director"
    result = dict(segment_id="S", job_sha256=job["job_sha256"], mode="product-display",
                  motion=None, appearance=["包装"], reference_frames=[], decision="成品静放，定机拍摄",
                  entry_state="闭盒在桌面", exit_state="闭盒仍在桌面", reference_use="只借包装外观")
    cli.submit(root, result)
    saved = (root / di.PLAN).read_bytes()
    assert read_json(root / di.PLAN)["segments"][0]["director_return"]
    assert [(s["action"], s["status"]) for s in segment_steps(root, story, entry)] == [("video", "ready")]
    cli.submit(root, result)
    assert (root / di.PLAN).read_bytes() == saved

    # A correct byte hash for a different image is still not this director's approval.
    changed_plan = read_json(root / di.PLAN)
    changed_plan["references"]["包装"] = newer
    changed_plan["product_pack"]["source"] = newer
    assert segment_steps(root, story, entry, plan=changed_plan)[0]["status"] == "waiting"
    assert "局部导演改法" in segment_steps(root, story, entry, plan=changed_plan)[0]["reason"]
    box.write_bytes(b"overwritten packaging")
    assert segment_steps(root, story, entry)[0]["status"] == "waiting"
    with pytest.raises(ValueError, match="参考"):
        cli.submit(root, result)
    assert (root / di.PLAN).read_bytes() == saved
