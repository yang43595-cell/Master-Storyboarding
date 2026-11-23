import pytest

from scripts.core.artifacts import digest
from scripts.core.io import write_json
from scripts.core.recreation_segments import PLAN, PROMPTS, segment_steps


def test_route_only_rewrites_the_affected_segment_and_checks_prompt_sources(tmp_path):
    entry = {"artifact_id": "narrative-r1", "checksum": "r1"}
    narrative = {"story_paragraphs": [{"paragraph_id": "p"}], "dialogues": []}
    movie = tmp_path / "depth.mp4"
    movie.write_bytes(b"synthetic fixture")
    plan = dict(
        narrative_id="narrative-r1",
        narrative_sha256="r1",
        fps=25,
        first_frame=0,
        end_frame=50,
        references={
            "depth": dict(
                role="depth", path=movie.name, sha256=digest(movie), frame_count=25, source_start_frame=0
            )
        },
        segments=[
            dict(
                id="first",
                mode="depth",
                frames=[0, 25],
                motion="depth",
                reference_frames=[0, 25],
                paragraph_ids=["p"],
                dialogue_ids=[],
                appearance=[],
            ),
            dict(
                id="second",
                mode="rewrite",
                change="喷雾替代刷子，重排两人互动",
                frames=[25, 50],
                paragraph_ids=["p"],
                dialogue_ids=[],
            ),
        ],
    )

    def steps():
        write_json(tmp_path / PLAN, plan)
        return [
            (s["action"], s["status"], s["segment_ids"]) for s in segment_steps(tmp_path, narrative, entry)
        ]

    assert steps() == [("video", "ready", ["first"]), ("director", "ready", ["second"])]
    write_json(
        tmp_path / PROMPTS,
        dict(
            narrative_id=entry["artifact_id"],
            plan_sha256=digest(tmp_path / PLAN),
            segments=[dict(id="first", prompt="@depth 跟随进门动作。")],
        ),
    )
    assert steps()[0] == ("video", "complete", ["first"])
    movie.write_bytes(b"changed")
    assert steps()[0] == ("video", "waiting", ["first"])
    plan["segments"][0]["frames"][1] = 800
    with pytest.raises(ValueError, match="30秒"):
        steps()


def test_product_segment_requires_the_chosen_pack_and_canvas_readback(tmp_path):
    entry = {"artifact_id": "source", "checksum": "source-rev"}
    narrative = {"story_paragraphs": [{"paragraph_id": "P"}], "dialogues": []}
    refs = {}
    for key, name in {"act": "use.mp4", "box": "packing.jpg", "evidence": "source.json"}.items():
        path = tmp_path / name
        path.write_text(key)
        refs[key] = {"path": name, "sha256": digest(path)}
    refs["act"].update(role="product", frame_count=25)
    segment = dict(id="S", mode="product", motion="act", frames=[0, 25],
                   reference_frames=[0, 25], paragraph_ids=["P"], dialogue_ids=[], appearance=[])
    plan = dict(narrative_id="source", narrative_sha256="source-rev", fps=25, first_frame=0,
                end_frame=25, references=refs, segments=[segment])
    kickoff = {"product": {"mode": "retain", "product_id": "fixture-mask", "product_usage": "real_use"},
               "canvas": {"target_url": "https://example.com/canvas", "target_project_id": "target"}}

    def run(decision=None):
        write_json(tmp_path / PLAN, plan)
        return segment_steps(tmp_path, narrative, entry, kickoff=decision)

    assert "请用户先确认" in run()[0]["reason"]
    assert "产品包" in run(kickoff)[0]["reason"]
    plan["product_pack"] = dict(product_id="wrong-mask", packaging=["box"], operations=["act"],
                                source=refs["evidence"])
    assert run(kickoff)[0]["status"] == "waiting"
    plan["product_pack"]["product_id"] = "fixture-mask"
    assert run(kickoff)[0]["status"] == "ready"
    write_json(tmp_path / PROMPTS, dict(narrative_id="source", plan_sha256=digest(tmp_path / PLAN),
                                       segments=[dict(id="S", prompt="见 @act。")]))
    assert [(s["action"], s["status"]) for s in run(kickoff)] == [
        ("video", "complete"), ("product-canvas", "ready")]

    receipt = {**kickoff["canvas"], "phase": "after_refresh", "plan_sha256": digest(tmp_path / PLAN),
               "segments": {"S": {"reference_identities": {"act": ["ref", "draft", "input"]},
                                  "verified_product_inputs": {"act": refs["act"]["sha256"]}}},
               "product_pack": {"product_id": "fixture-mask", "packaging": {}}}
    path = tmp_path / "06_视频/画布交付回执.json"
    write_json(path, receipt)
    assert run(kickoff)[-1]["status"] == "ready"
    receipt["product_pack"]["packaging"]["box"] = {"node_id": "packing", "sha256": refs["box"]["sha256"]}
    write_json(path, receipt)
    assert run(kickoff)[-1]["status"] == "complete"
    (tmp_path / "packing.jpg").write_text("changed")
    assert run(kickoff)[0]["status"] == "waiting"
