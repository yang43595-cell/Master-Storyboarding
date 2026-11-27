"""Small synthetic preview replays; no browser, uploads or provider calls."""
import copy
import pytest

from scripts.canvas_readback import compare, digest, validate_submission
from scripts.core import artifacts, candidate_test
from scripts.core.io import write_json
from scripts.core.project_state import new_project_state, write_project_state
from scripts.tests.test_delivery_preflight import delivery


def seal(frozen):
    auth = frozen["preview_authorization"]
    auth["drafts_sha256"] = digest(frozen["drafts"])
    auth["sources_sha256"] = digest(frozen["source_artifacts"])
    auth["cleanup_sha256"] = digest(frozen.get("cleanup", []))


def preview(root):
    frozen, _ = delivery(root)
    manifest = artifacts.load_manifest(root)
    for item in manifest["artifacts"]:
        item.update(status="candidate", user_approved=False)
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    write_project_state(root, new_project_state())
    candidate_test.configure(root, "TEST", "synthetic-only", ["FRAME", "DIRECTOR"])
    for ref in frozen["source_artifacts"] + [r for d in frozen["drafts"] for r in d["reference_checks"]]:
        item = next(a for a in manifest["artifacts"] if a["artifact_id"] == ref["artifact_id"])
        ref.update(project_path=str(root), path=str(root / item["path_or_url"]), record_sha256=item["checksum"])
    frozen.update(
        target_project_id="exact",
        status="candidate",
        review_status="unreviewed",
        production_blocked=True,
        operations=["upload_references", "save_drafts"],
    )
    for row in frozen["drafts"]:
        for ref, media in zip(row["reference_checks"], row["ordered_media"]):
            ref["limited_use"] = dict(
                conclusion="usable_for_private_preview",
                reviewer="fixture root",
                review_ref="synthetic-review",
                observation="Synthetic blue square only",
                draft_node_ids=["one", "two"],
                entity_ids=["ACTOR"],
                roles=["start_frame", "end_frame"],
                excluded_roles=["identity"],
            )
            ref["upload_binding"] = dict(
                target_url=frozen["target_url"],
                target_project_id="exact",
                **media,
                revision=ref["revision"],
                sha256=ref["sha256"],
            )
    frozen["preview_authorization"] = dict(
        mode="private_candidate_preview",
        project_path=str(root),
        visibility="private",
        target_url=frozen["target_url"],
        target_project_id="exact",
        source_projects=[str(root)],
        evidence=[dict(message_id="synthetic-only", quote="Synthetic private draft permission")],
        allowed_operations=frozen["operations"].copy(),
    )
    seal(frozen)
    return frozen


def observed(frozen):
    value = copy.deepcopy(frozen)
    value.update(phase="after_refresh", frozen_sha256=digest(frozen))
    for row in value["drafts"]:
        row["native_mentions"] = [
            {**m, "entity_type": "fixture-chip", "native_noneditable_entity": True}
            for m in row["ordered_media"]
        ]
    return value


def test_preview_real_bytes_and_readback_never_mutate_or_promote(tmp_path):
    frozen = preview(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = compare(frozen, observed(frozen), tmp_path, private_preview=True)
    assert report["ok"] and report["submission_checks"]["production_blocked"] is True
    assert all(p.read_bytes() == content for p, content in before.items())
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path)
