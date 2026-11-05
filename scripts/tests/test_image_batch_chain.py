"""Offline image-worker handoff through real adoption and canvas consumers.

Pixels, observations, authorizations and readback are synthetic fixtures. No
generator, website, user approval or actual visual acceptance is exercised.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from scripts import canvas_readback, stage_context_cli as cli
from scripts.core import artifacts, asset_refs, quality_evidence as quality
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state
from test_quality_evidence_consumers import (
    adopt_agent_fixture_entry, agent_board_fixture, observations,
)


def prepare_worker(project, batch, shot):
    request = project / "05_分镜" / batch / "request.json"
    write_json(request, {"image_targets": ["IMG-" + batch], "source_shot_ids": [shot]})
    common = ["--project", str(project), "--skill", "storyboard-director",
              "--context-id", batch, "--actor-id", "worker-" + batch,
              "--task-id", "task-" + batch]
    prepared = cli.prepare(cli.parser().parse_args([
        "prepare", *common, "--objective", "Synthetic independent image batch",
        "--request-json", str(request),
    ]))
    assert prepared["valid"]
    folder = project / cli.CONTEXT_DIR / "批次" / batch
    basis = json.loads((folder / "task_basis.json").read_text(encoding="utf-8"))
    # Tests exercise the declaration/binding machinery, not cognitive reading.
    methods = basis["reading_manifest"]["methods"]
    assert (folder / "method_reading_packet.md").read_text(encoding="utf-8")
    args = ["record-method-reading", *common]
    for method in methods:
        args += ["--method", method["id"]]
    assert cli.record_method_reading(cli.parser().parse_args(args))["ok"]
    assert cli.ensure_prepared(cli.parser().parse_args(["ensure-prepared", *common]))["ok"]
    return request, folder


def chain(project):
    board = agent_board_fixture(project)
    human_receipts = copy.deepcopy(load_project_state(project)["user_confirmations"])
    shots = ["S01-010", "S01-020"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        prepared = list(pool.map(lambda pair: prepare_worker(project, *pair),
                                 zip(["a", "b"], shots)))
    url = "https://example.com/canvas/synthetic-image-batches"
    references = []
    for index, ((request, folder), shot) in enumerate(zip(prepared, shots)):
        ident = "IMG-" + chr(65 + index)
        path = request.parent / "R01.png"
        Image.new("RGB", (32, 32), ["blue", "green"][index]).save(path)
        sha = artifacts.digest(path)
        ref = {"artifact_id": ident, "revision": "R01", "sha256": sha,
               "kind": "image", "roles": ["identity"], "entity_ids": [ident],
               "source_shot_ids": [shot]}
        evidence = observations(quality.IMAGE_ASPECTS)
        evidence.update(observer="synthetic-worker-" + ident, image_sha256=sha,
            source_shot_ids=[shot], uses=[quality.image_use(ref, [shot])],
            source_files=[{"path": request.relative_to(project).as_posix(),
                           "sha256": artifacts.digest(request)},
                          {"path": board["path_or_url"], "sha256": board["checksum"]}],
            view={"resolution": "original", "locator": str(path) + ": synthetic pixels only"})
        entry = artifacts.register_workspace_artifact(project, {
            "artifact_id": ident, "logical_id": "image." + ident,
            "type": "visual-supplement-image", "revision": "R01",
            "path_or_url": path.relative_to(project).as_posix(), "checksum": sha,
            "depends_on": ["storyboard.package"], "quality_evidence": evidence,
            "upload_bindings": [{"target_url": url, "node_id": ident,
                                 "media_id": "media-" + ident,
                                 "revision": "R01", "sha256": sha}],
        })
        adopted = adopt_agent_fixture_entry(project, entry, usage={"source_shot_ids": [shot]})
        assert adopted["user_approved"] is False
        assert asset_refs.require_references(project, [ident], [shot]) == (True, [])
        references.append(ref)
        assert (folder / "method_reading_evidence.json").is_file()
    director = json.loads((project / board["path_or_url"]).read_text(encoding="utf-8"))
    segment = {"segment_id": "G1", "prompt": "Synthetic two-shot scene.",
        "source_shot_ids": shots, "duration_seconds": 5,
        "source_audio": [{"shot_id": shot["shot_id"],
                          "dialogue": copy.deepcopy(shot["audio"]["dialogue"])}
                         for shot in director["scenes"][0]["shots"]],
        "materials": copy.deepcopy(references)}
    video = {"revision": "R01", "source_storyboard": {
        "artifact_id": board["artifact_id"], "revision": board["revision"],
        "path": board["path_or_url"]}, "generation_segments": [segment]}
    video_path = project / "06_视频" / "R01.json"
    write_json(video_path, video)
    video_entry = artifacts.register_workspace_artifact(project, {
        "artifact_id": "VIDEO", "logical_id": "video.prompt", "type": "video.prompt",
        "revision": "R01", "path_or_url": video_path.relative_to(project).as_posix(),
        "checksum": artifacts.digest(video_path),
        "depends_on": ["storyboard.package", "image.IMG-A", "image.IMG-B"],
    })
    video_entry = adopt_agent_fixture_entry(project, video_entry)
    draft = {"node_id": "D1", "source_segment_id": "G1", "source_shot_ids": shots,
        "prompt": segment["prompt"], "parameters": {"duration": 5},
        "duration_bounds": [1, 10], "shot_durations": [2.5, 2.5],
        "native_entity_type": "media", "ordered_media": [
            {"node_id": r["artifact_id"], "media_id": "media-" + r["artifact_id"]}
            for r in references], "reference_checks": references,
        "required_entities": [r["artifact_id"] for r in references],
        "required_reference_roles": {r["artifact_id"]: ["identity"] for r in references},
        "continuity": {"event_id": "synthetic-scene"}}
    draft["quality_evidence"] = observations(quality.DRAFT_ASPECTS)
    draft["quality_evidence"]["checks"]["packaging_identity"].update(
        scope="none", items=[], result="not_applicable")
    draft["quality_evidence"]["input_sha256"] = quality.fingerprint(quality.draft_binding(draft))
    frozen = {"target_url": url, "revision": "R01", "source_artifacts": [
        {"artifact_id": item["artifact_id"], "revision": item["revision"],
         "sha256": item["checksum"]} for item in (board, video_entry)], "drafts": [draft]}
    assert load_project_state(project)["user_confirmations"] == human_receipts
    return frozen


def test_parallel_image_batches_reach_video_adoption_and_canvas_readback(tmp_path):
    frozen = chain(tmp_path)
    report = canvas_readback.validate_submission(frozen, tmp_path)
    assert report["ok"] and report["visual_quality_verified"] is False
    observed = copy.deepcopy(frozen)
    observed.update(phase="after_refresh", frozen_sha256=canvas_readback.digest(frozen))
    observed["drafts"][0]["native_mentions"] = [
        dict(item, entity_type="media", native_noneditable_entity=True)
        for item in frozen["drafts"][0]["ordered_media"]]
    assert canvas_readback.compare(frozen, observed, tmp_path)["ok"]


@pytest.mark.parametrize("fault", ["file", "usage", "report"])
def test_handoff_never_reuses_changed_image_use_or_self_check(tmp_path, fault):
    frozen = chain(tmp_path)
    entry = next(item for item in artifacts.load_manifest(tmp_path)["artifacts"]
                 if item["artifact_id"] == "IMG-A")
    if fault == "file":
        Image.new("RGB", (32, 32), "red").save(tmp_path / entry["path_or_url"])
    elif fault == "usage":
        frozen["drafts"][0]["reference_checks"][0]["roles"] = ["layout"]
    else:
        (tmp_path / entry["agent_acceptance"]["report_path"]).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        canvas_readback.validate_submission(frozen, tmp_path)
