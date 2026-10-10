from __future__ import annotations

import json

import pytest

from scripts.core.review_delta import fingerprint, revision_delta, section_impact


def setup_round(tmp_path):
    baseline, current = tmp_path / "R01.md", tmp_path / "R02.md"
    baseline.write_text("fixed fact\nwrong ending\n", encoding="utf-8")
    current.write_text("fixed fact\ncorrect ending\n", encoding="utf-8")
    checks = [{"id": "CHECK-1", "text": "keep verified facts"}]
    issue = {"id": "B-012", "check_id": "CHECK-1", "status": "unresolved",
             "target": "R01.md:2", "evidence": "source.md:8", "impact": "ending conflicts with source"}
    review = {"scope": {"skill": "test", "review_kind": "production", "task_basis_path": str(tmp_path / "task_basis.json"),
                         "task_basis_sha256": "basis-test-sha", "required_checks": checks, "manual_checks": [],
                         "artifacts": [fingerprint(baseline)]},
              "reviewer_id": "real-agent-id", "reviewer_lineage": ["first-agent", "real-agent-id"],
              "issue_id_high_watermark": 20, "wording_only_streak": 1,
              "issues": [issue, {"id": "B-015", "status": "resolved", "history": "RESOLVED_SECRET"},
                         {"id": "B-020", "status": "suggestion", "history": "SUGGESTION_SECRET"}],
              "chat_history": "CHAT_SECRET"}
    previous, changes = tmp_path / "review.json", tmp_path / "producer-response.json"
    previous.write_text(json.dumps(review), encoding="utf-8")
    changes.write_text(json.dumps({"responses": [{"id": "B-012", "summary": "corrected ending", "evidence": "R02.md:2",
                                    "chat": "PRODUCTION_CHAT_SECRET"}]}), encoding="utf-8")
    return previous, [baseline], [current], changes, "test", checks, []


def test_revision_uses_only_pending_and_actual_diff(tmp_path):
    args = setup_round(tmp_path)
    delta, _ = revision_delta(*args)
    assert [i["id"] for i in delta["issue_updates"]] == ["B-012"]
    assert delta["issue_updates"][0]["producer_response"]["summary"] == "corrected ending"
    assert "SECRET" not in json.dumps(delta)
    assert delta["issue_id_high_watermark"] == 20
    assert delta["reviewer_lineage"] == ["first-agent", "real-agent-id"]
    assert delta["wording_only_streak"] == 1
    assert "-wrong ending" in delta["actual_diff"][0]["diff"]
    assert "+correct ending" in delta["actual_diff"][0]["diff"]


def test_revision_accepts_inline_producer_response(tmp_path):
    previous, baselines, artifacts, _, skill, checks, manual = setup_round(tmp_path)
    delta, _ = revision_delta(previous, baselines, artifacts, {"responses": [{"id": "B-012", "summary": "inline fix", "evidence": "R02.md:2"}]}, skill, checks, manual)
    assert delta["issue_updates"][0]["producer_response"]["summary"] == "inline fix"


def test_hashes_and_diff_share_one_file_read(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path
    from io import BytesIO
    import hashlib

    args = setup_round(tmp_path)
    paths = [args[0], args[1][0], args[2][0]]
    originals = {p: p.read_bytes() for p in paths}
    counts = dict.fromkeys(paths, 0)
    real_open = Path.open

    @contextmanager
    def changing_open(path, mode='r', *positional, **kwargs):
        if path in originals and mode == 'rb':
            counts[path] += 1
            yield BytesIO(originals[path] if counts[path] == 1 else b'changed between reads')
        else:
            with real_open(path, mode, *positional, **kwargs) as stream:
                yield stream

    monkeypatch.setattr(Path, 'open', changing_open)
    delta, _ = revision_delta(*args)
    assert all(count == 1 for count in counts.values())
    assert delta['previous_review_sha256'] == hashlib.sha256(originals[paths[0]]).hexdigest()
    assert delta['actual_diff'][0]['after_sha256'] == hashlib.sha256(originals[paths[2]]).hexdigest()
    assert '+correct ending' in delta['actual_diff'][0]['diff']


@pytest.mark.parametrize("failure", ["baseline", "missing_change", "resolved_change", "duplicate", "reviewer"])
def test_bad_revision_stops(tmp_path, failure):
    args = list(setup_round(tmp_path))
    if failure == "baseline":
        args[1][0].write_text("changed after review", encoding="utf-8")
    elif failure == "missing_change":
        args[3].write_text('{"responses": []}', encoding="utf-8")
    elif failure == "resolved_change":
        args[3].write_text('[{"id":"B-015","summary":"old fix","evidence":"old:1"}]', encoding="utf-8")
    else:
        review = json.loads(args[0].read_text(encoding="utf-8"))
        if failure == "duplicate":
            review["issues"].append(review["issues"][0])
        else:
            del review["reviewer_id"]
        args[0].write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError):
        revision_delta(*args)


def test_manual_checks_are_supplied_by_the_frozen_task_basis(tmp_path):
    args = list(setup_round(tmp_path))
    review = json.loads(args[0].read_text(encoding="utf-8"))
    manual = [{"id": "MANUAL-001", "text": "project constraint"}]
    args[-1] = manual
    assert revision_delta(*args)[1] == manual


def test_oversize_delta_is_not_silently_truncated(tmp_path):
    args = setup_round(tmp_path)
    args[2][0].write_text("x" * 70000, encoding="utf-8")
    with pytest.raises(ValueError, match="64 KiB"):
        revision_delta(*args)


def test_two_rounds_keep_id_but_drop_resolved_body(tmp_path):
    args = list(setup_round(tmp_path))
    first, _ = revision_delta(*args)
    review = json.loads(args[0].read_text(encoding="utf-8"))
    review["scope"]["artifacts"] = [fingerprint(args[2][0])]
    review["issues"] = [{**{k: v for k, v in first["issue_updates"][0].items() if k != "producer_response"}, "status": "resolved"}]
    args[0].write_text(json.dumps(review), encoding="utf-8")
    args[1] = args[2]
    args[3].write_text('{"responses": []}', encoding="utf-8")
    second, _ = revision_delta(*args)
    assert second["issue_updates"] == []
    assert second["issue_id_high_watermark"] == 20
    assert second["actual_diff"][0]["diff"] == ""
    assert "ending conflicts" not in json.dumps(second)


@pytest.mark.parametrize("failure", ["omitted", "recycled"])
def test_review_cannot_forget_open_id_or_recycle_closed_id(tmp_path, failure):
    args = setup_round(tmp_path)
    review = json.loads(args[0].read_text(encoding="utf-8"))
    review["scope"]["input_issue_ids"] = ["B-012", "B-013"] if failure == "omitted" else ["B-012"]
    review["scope"]["issue_id_high_watermark"] = 20 if failure == "recycled" else 0
    args[0].write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="漏答|复用"):
        revision_delta(*args)


def test_file_level_decisions_have_both_version_ranges():
    before = ["# candidate", "## 对白决定", "old duration", "## SEG-01", "unchanged"]
    after = [*before[:2], "new duration", *before[3:]]
    impact = section_impact(before, after)
    assert impact["changed_sections"] == []
    assert impact["file_level_changes"]["before"] == [{"section": "对白决定", "start_line": 2, "end_line": 3}]
    assert impact["file_level_changes"]["after"] == impact["file_level_changes"]["before"]
    assert impact["file_level_impact_review_required"]
    assert impact["cross_file_context_review_required"]


@pytest.mark.parametrize("removed", [0, 1, 2])
def test_deleted_section_keeps_old_location_and_surviving_neighbors(removed):
    before = ["## SEG-01", "a", "## SEG-02", "b", "## SEG-03", "c"]
    after = before[:removed*2] + before[removed*2+2:]
    impact = section_impact(before, after)
    assert impact["changed_sections"] == []
    assert impact["deleted_sections"] == [{"section": f"SEG-0{removed+1}", "start_line": removed*2+1, "end_line": removed*2+2}]
    assert [r["section"] for r in impact["continuity_neighbors"]] == [f"SEG-0{i+1}" for i in range(3) if i != removed and abs(i-removed) == 1]
    assert impact["cross_file_context_review_required"] == (removed != 1)


def test_partial_deletion_still_selects_surviving_section():
    impact = section_impact(["## SEG-01", "keep", "remove"], ["## SEG-01", "keep"])
    assert impact["changed_sections"][0]["section"] == "SEG-01"
    assert impact["deleted_sections"] == []


def test_fences_and_trailing_file_decision_are_not_fake_segments():
    before = ["## SEG-01", "````text", "## SEG-99", "````", "## 全局决定", "old"]
    impact = section_impact(before, [*before[:-1], "new"])
    assert impact["changed_sections"] == []
    assert impact["file_level_changes"]["after"][0]["section"] == "全局决定"
    impact = section_impact(before, [*before[:2], "## SEG-98", *before[3:]])
    assert [r["section"] for r in impact["changed_sections"]] == ["SEG-01"]


def test_reorder_and_empty_current_do_not_lose_old_objects():
    before = ["## SEG-01", "a", "## SEG-02", "b"]
    impact = section_impact(before, before[2:] + before[:2])
    assert impact["reordered_sections"] == ["SEG-02", "SEG-01"]
    assert impact["deleted_sections"] == []
    impact = section_impact(before, [])
    assert len(impact["deleted_sections"]) == 2
    assert impact["changed_sections"] == []


def test_delta_integrates_scope_without_copying_unchanged_bodies(tmp_path):
    args = setup_round(tmp_path)
    delta, _ = revision_delta(*args)
    change = delta["actual_diff"][0]
    assert change["baseline"] == str(args[1][0])
    assert change["file_level_impact_review_required"]
    args[2][0].write_text(args[1][0].read_text(encoding="utf-8"), encoding="utf-8")
    unchanged = revision_delta(*args)[0]["actual_diff"][0]
    assert not unchanged["cross_file_context_review_required"]
    assert unchanged["file_level_changes"] == {"before": [], "after": []}


def test_multi_file_boundary_does_not_guess_chronological_neighbors(tmp_path):
    args = list(setup_round(tmp_path))
    baseline, current = tmp_path / "other-old.md", tmp_path / "other-new.md"
    for path in (baseline, current):
        path.write_text("## SEG-90\nUNCHANGED_OTHER_BODY\n", encoding="utf-8")
    args[1].append(baseline)
    args[2].append(current)
    review = json.loads(args[0].read_text(encoding="utf-8"))
    review["scope"]["artifacts"].append(fingerprint(baseline))
    args[0].write_text(json.dumps(review), encoding="utf-8")
    delta, _ = revision_delta(*args)
    assert delta["actual_diff"][0]["cross_file_context_review_required"]
    assert delta["actual_diff"][1]["changed_sections"] == []
    assert "UNCHANGED_OTHER_BODY" not in json.dumps(delta)
