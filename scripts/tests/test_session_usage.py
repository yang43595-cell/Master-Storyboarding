import json
from pathlib import Path

import pytest

from scripts import session_usage as meter
from scripts import stage_context_cli as context


def record(response, input=100, output=7, turn="turn-1"):
    return {"thread_id": "thread", "turn_id": turn, "response_id": response,
            "usage": {"input_tokens": input, "cached_input_tokens": input - 10,
                      "cache_write_input_tokens": 0, "output_tokens": output,
                      "reasoning_output_tokens": 2, "total_tokens": input + output}}


def test_response_identity_survives_compaction_and_turn_resets(tmp_path):
    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    r1, compact, r2 = record("one"), record("compact", 120), record("two", 30)
    events = [
        {"ordinal": 1, "type": "token_usage_record", "payload": r1},
        {"ordinal": 2, "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": 999999}}}},
        {"ordinal": 3, "type": "token_usage_record", "payload": compact},
        {"ordinal": 4, "type": "compacted", "payload": {"latest_token_usage_record": compact,
             "replacement_history": [{"secret": "must never reach report"}]}},
    ]
    first.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    second.write_text("\n".join(json.dumps(e) for e in [events[-1],
        {"ordinal": 5, "type": "token_usage_record", "payload": r2},
        {"ordinal": 6, "type": "token_usage_record", "payload": record("other", turn="turn-2")}
    ]), encoding="utf-8")
    result = meter.measure([first, second], "turn-1")
    assert result["requests"] == 3
    assert result["totals"]["input_tokens"] == 250
    assert result["totals"]["uncached_input_tokens"] == 30
    assert result["totals"]["total_tokens"] == 271
    assert result["calls"][1]["compaction"] is True
    assert "secret" not in json.dumps(result)
    assert meter.measure([first, second], "turn-1", through_ordinal=3)["requests"] == 2
    assert meter.measure([first, second], "turn-1", after_response="compact")["requests"] == 1
    assert not meter.check_budget(result, max_input=120)["ok"]
    assert not meter.check_budget(result, max_requests=3)["ok"]


def test_unknown_is_not_zero_and_conflicting_duplicates_fail(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"type":"event_msg"}\n{"partial":', encoding="utf-8")
    result = meter.measure([path], "turn-1")
    assert result["totals"]["input_tokens"] is None
    assert not meter.check_budget(result, max_total=500)["ok"]
    path.write_text("\n".join(json.dumps({"type": "token_usage_record", "payload": r})
                    for r in [record("same"), record("same", 101)]), encoding="utf-8")
    with pytest.raises(ValueError, match="冲突用量"):
        meter.measure([path], "turn-1")


def test_explicit_boundaries_sum_turns_deduplicate_and_preserve_unknown(tmp_path):
    one, two = tmp_path / "one.jsonl", tmp_path / "two.jsonl"
    one.write_text(json.dumps({"type": "token_usage_record", "payload": record("one", 100, turn="turn-1")}), encoding="utf-8")
    two.write_text("\n".join(json.dumps({"type": "token_usage_record", "payload": item}) for item in [record("one", 100, turn="turn-1"), record("two", 200, turn="turn-2")]), encoding="utf-8")
    report = meter.measure_boundaries([{"turn_id": "turn-1", "logs": [str(one), str(two)]}, {"turn_id": "turn-2", "logs": [str(two)]}])
    assert report["requests"] == 2 and report["totals"]["input_tokens"] == 300
    assert not meter.check_budget(report, max_total=314)["ok"]
    broken = tmp_path / "broken.jsonl"; broken.write_text('{"partial":', encoding="utf-8")
    unknown = meter.measure_boundaries([{"turn_id": "turn-x", "logs": [str(broken)]}])
    assert unknown["totals"]["total_tokens"] is None and not meter.check_budget(unknown, max_total=1)["ok"]


@pytest.mark.parametrize("incomplete", ["missing_turn", "truncated_tail"])
def test_partial_boundary_does_not_report_known_subtotal_as_complete(tmp_path, incomplete):
    path = tmp_path / "usage.jsonl"
    event = json.dumps({"type": "token_usage_record", "payload": record("one")})
    path.write_text(event + ('\n{"partial":' if incomplete == "truncated_tail" else "\n"), encoding="utf-8")
    boundaries = [{"turn_id": "turn-1", "logs": [str(path)]}]
    if incomplete == "missing_turn":
        boundaries.append({"turn_id": "turn-2", "logs": [str(path)]})
    report = meter.measure_boundaries(boundaries)
    assert report["requests"] == 1  # Observed request is retained, not the full task count.
    assert report["calls"][0]["usage"]["total_tokens"] == 107
    assert report["unknown"]
    assert all(value is None for value in report["totals"].values())
    assert report["peak_input_tokens"] is None
    for budget in ({"max_total": 1000}, {"max_input": 1000}, {"max_requests": 10}):
        assert not meter.check_budget(report, **budget)["ok"]


def test_boundaries_cli_exports_report_without_overwriting_sources(tmp_path, capsys):
    one, two = tmp_path / "one.jsonl", tmp_path / "two.jsonl"
    for path, turn, tokens in ((one, "turn-1", 100), (two, "turn-2", 200)):
        path.write_text(json.dumps({"type": "token_usage_record", "payload": record(turn, tokens, turn=turn)}), encoding="utf-8")
    boundary_path = tmp_path / "boundaries.json"
    boundary_path.write_text(json.dumps([
        {"turn_id": "turn-1", "logs": [str(one)]},
        {"turn_id": "turn-2", "logs": [str(two)]},
    ]), encoding="utf-8")
    originals = {path: path.read_bytes() for path in (one, two, boundary_path)}
    output = tmp_path / "report.json"
    args = ["--boundaries-json", str(boundary_path), "--output", str(output)]
    assert meter.main(args) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["totals"]["total_tokens"] == 314
    assert len(report["calls"]) == 2
    assert "calls" not in json.loads(capsys.readouterr().out)
    assert meter.main(args + ["--max-total", "314"]) == 2
    assert not json.loads(output.read_text(encoding="utf-8"))["budget"]["ok"]
    for source in originals:
        with pytest.raises(ValueError, match="不得覆盖"):
            meter.main(["--boundaries-json", str(boundary_path), "--output", str(source)])
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_single_turn_cli_export_and_source_protection_still_work(tmp_path):
    source, output = tmp_path / "usage.jsonl", tmp_path / "report.json"
    source.write_text(json.dumps({"type": "token_usage_record", "payload": record("one")}), encoding="utf-8")
    original = source.read_bytes()
    args = ["--log", str(source), "--turn-id", "turn-1"]
    assert meter.main(args + ["--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["totals"]["total_tokens"] == 107
    with pytest.raises(ValueError, match="不得覆盖"):
        meter.main(args + ["--output", str(source)])
    assert source.read_bytes() == original


def test_prepare_stops_before_writing_on_exhausted_budget(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text(json.dumps({"type": "token_usage_record", "payload": record("one", 147003)}), encoding="utf-8")
    args = context.parser().parse_args(["prepare", "--project", str(tmp_path), "--skill", "storyboard-planner",
        "--objective", "导演稿试跑", "--usage-log", str(path), "--usage-turn-id", "turn-1", "--max-input", "80000"])
    with pytest.raises(context.StageContextError, match="执行预算停止"):
        context.prepare(args)
    assert not (tmp_path / context.CONTEXT_DIR).exists()


def test_cli_gate_failure_is_nonzero(monkeypatch, tmp_path):
    monkeypatch.setattr(context, "ensure_prepared", lambda args: {"ok": False, "method_reading_evidence": "required"})
    assert context.main(["ensure-prepared", "--project", str(tmp_path), "--skill", "voiceover-to-video-script"]) == 2
