"""Local CLI replays of RedFrame canvas input validation and fixtures."""
import copy
import pytest

from scripts.canvas_readback import digest, validate_canvas_inputs
from scripts.core import artifacts
from scripts.core.io import write_json
from scripts.core.project_kickoff import REQUEST_PATH
from scripts.core.project_state import load_project_state, write_project_state
from scripts.tests.test_delivery_preflight import bind_synthetic_canvas, bind_synthetic_quality, delivery


@pytest.mark.parametrize("duration_key", ["duration", "duration_seconds"])
def test_canvas_checks_duration_aliases_independent_of_prompt(duration_key):
    row = dict(
        node_id="synthetic-draft", ordered_media=[], reference_checks=[],
        edges=[], source_nodes=[], prompt="人物抬手，接着按下按钮。",
        shot_durations=[1, 3], parameters={duration_key: 4},
    )
    validate_canvas_inputs(row)
    row["parameters"][duration_key] = 5
    with pytest.raises(ValueError, match="逐镜合计"):
        validate_canvas_inputs(row)


def redframe_fixture(root):
    frozen, observed = delivery(root)
    frozen["target_project_id"] = observed["target_project_id"] = "synthetic-project"
    for row in frozen["drafts"]:
        row["parameters"].update(model="fixture", aspect_ratio="9:16", resolution="480p")
    write_json(root / REQUEST_PATH, {
        "kickoff": {
            "decision_ref": "synthetic decision only",
            "product": {"mode": "none"},
            "canvas": dict(
                platform="redframe", model="fixture", aspect_ratio="9:16", resolution="480p",
                target_url=frozen["target_url"], target_project_id="synthetic-project",
            ),
        },
    })
    bind_synthetic_canvas(root, frozen, observed)
    bind_redframe_production_sources(root, frozen, observed)
    return frozen, observed


def bind_redframe_production_sources(root, frozen, observed, *, product_assets=()):
    manifest = artifacts.load_manifest(root)
    state = load_project_state(root)

    def register(ident, logical_id, filename, payload):
        write_json(root / filename, payload)
        sha = artifacts.digest(root / filename)
        manifest["artifacts"] = [row for row in manifest["artifacts"] if row["artifact_id"] != ident]
        manifest["artifacts"].append(dict(
            artifact_id=ident, logical_id=logical_id, type=logical_id,
            revision="R01", checksum=sha, path_or_url=filename, status="current", user_approved=True,
        ))
        if ident not in state["current_artifacts"]:
            state["current_artifacts"].append(ident)
        state["user_confirmations"] = [row for row in state["user_confirmations"] if row.get("artifact_ids") != [ident]]
        state["user_confirmations"].append(dict(artifact_ids=[ident], scope="artifact", approval_ref="synthetic only"))
        return dict(artifact_id=ident, revision="R01", sha256=sha)

    narrative = register("NARRATIVE", "narrative.package", "narrative.json", {"fixture": "narrative", "asset_requirements": list(product_assets)})
    capability = root / "synthetic-duration-capability.json"
    model_name = frozen["drafts"][0]["parameters"]["model"]
    write_json(capability, {"model": {"type": "video", "providerId": "fixture", "modelId": "fixture", "label": model_name, "mode": ["fixture-reference"], "durationResolutionMap": [{"resolution": ["480p"], "duration": list(range(2, 9))}]}})
    shots = []
    segments = []
    for row in frozen["drafts"]:
        ident = row["node_id"]
        shot = ident + "-shot"
        row.update(source_segment_id=ident, source_shot_ids=[shot])
        row["parameters"]["reference_mode"] = "fixture-reference"
        shots.append(dict(scene_id=ident, shots=[dict(shot_id=shot, action=f"{shot} starts a new action")]))
        segments.append(dict(
            segment_id=ident, source_shot_ids=[shot], prompt=row["prompt"],
            parameters=copy.deepcopy(row["parameters"]),
            shot_durations=copy.deepcopy(row["shot_durations"]),
            target_edit_duration_seconds=sum(row["shot_durations"]),
            materials=[dict(sha256=ref["sha256"]) for ref in row["reference_checks"]],
        ))
    for previous, current in zip(segments, segments[1:]):
        previous_shot = previous["source_shot_ids"][-1]
        current_shot = current["source_shot_ids"][0]
        current["cross_scene_continuity"] = dict(
            from_segment_id=previous["segment_id"], decision="new_action_or_time_jump",
            basis="The next synthetic scene explicitly begins a new action.",
            previous_source=dict(shot_id=previous_shot, field="action", quote=f"{previous_shot} starts a new action"),
            current_source=dict(shot_id=current_shot, field="action", quote=f"{current_shot} starts a new action"),
        )
    director = register("DIRECTOR", "storyboard.package", "director.json", {"source_narrative": {**narrative, "path": "narrative.json"}, "scenes": shots})
    video = register("VIDEO", "video.prompt", "video.json", {
        "source_storyboard": {**director, "path": "director.json"},
        "generation_segments": segments,
        "selected_video_model": {
            "id": segments[0]["parameters"]["model"], "platform": "redframe", "entry_point": "fixture-editor",
            "reference_mode": "fixture-reference", "confirmed_by_user": True, "min_duration_seconds": 2, "max_duration_seconds": 8,
            "capability_evidence": dict(path=capability.name, sha256=artifacts.digest(capability), locator="synthetic observation", checked_at="2026-11-15T00:00:00Z"),
        },
    })
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    write_project_state(root, state)
    frozen["source_artifacts"] = [video, director]
    bind_synthetic_quality(root, frozen)
    observed["source_artifacts"] = copy.deepcopy(frozen["source_artifacts"])
    observed["drafts"] = copy.deepcopy(frozen["drafts"])
    for row in observed["drafts"]:
        row["native_mentions"] = [dict(**media, entity_type="fixture-chip", native_noneditable_entity=True) for media in row["ordered_media"]]
    observed["frozen_sha256"] = digest(frozen)
