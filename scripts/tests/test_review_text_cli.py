"""Synthetic local export/submission checks; no credentials or network access."""
import copy
import json
from pathlib import Path

import pytest

from scripts import review_text_cli as cli


OPERATION = "e2fc997d-b5f4-4c4d-bb6b-c4eaf43e0454"


def narrative(project, **updates):
    package = {"schema_version": "2.0", "project_id": "SYNTHETIC-REVIEW-ONLY", "revision": "R01",
               "status": "candidate", "objective": "独立合成接口测试；不用于生产。", "locked_facts": [],
               "story_paragraphs": [{"paragraph_id": "STP-01", "text": "合成桌面上摆着一张纸。中文与 ``` 均须原样保留。"}],
               "dialogues": [], "asset_requirements": [{"asset_id": "AST-PROP-PAPER", "category": "PROP",
                    "name": "合成纸张", "narrative_purpose": "承载合成验收动作", "source_paragraph_ids": ["STP-01"],
                    "identity_constraints": [], "required_states": [], "interactions": [], "reference_paths": [],
                    "unknowns": [], "image_decision": "无需独立图", "image_reason": "只验证文本，不制作图片"}],
               "change_summary": ["仅合成文本验收"], "open_questions": []}
    package.update(updates)
    path = project / "R01.json"
    path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, package


def prepare(project):
    return cli.run(cli._parser().parse_args(["prepare", "--project", str(project), "--narrative", "R01.json",
                                             "--output", "R01.md"]))


def publish_args(project, receipt="publish.json"):
    return cli._parser().parse_args(["publish", "--project", str(project), "--narrative", "R01.json",
                                    "--markdown", "R01.md", "--case-id", "012", "--operation-id", OPERATION,
                                    "--token-file", "/not-read-by-mock", "--receipt", receipt,
                                    "--purpose", "独立合成测试"])


@pytest.fixture
def fake_client(monkeypatch):
    calls = []
    response = {"ok": True, "caseId": "012", "version": "synthetic-version", "headGeneration": 1,
                "assetId": "synthetic-asset", "url": "/?caseId=012", "extension": {"preserve": True}}

    class Client:
        def __init__(self, **kwargs):
            self.base_url = kwargs["base_url"]

        def create_case(self, **kwargs):
            calls.append(("create-case", kwargs))
            return copy.deepcopy(response)

        def publish(self, **kwargs):
            calls.append(("publish", kwargs))
            return copy.deepcopy(response)

        def status(self, **kwargs):
            calls.append(("status", kwargs))
            return copy.deepcopy(response)

    monkeypatch.setattr(cli, "AgentReviewClient", Client)
    return calls, response


def test_prepare_is_complete_exclusive_and_needs_no_token(tmp_path, monkeypatch):
    source, package = narrative(tmp_path)
    before = source.read_bytes()
    monkeypatch.setattr(cli, "AgentReviewClient", lambda **kwargs: pytest.fail("prepare must stay local"))
    result = prepare(tmp_path)
    reading = (tmp_path / "R01.md").read_text(encoding="utf-8")
    appendix = reading.split("````json\n", 1)[1].rsplit("\n````", 1)[0]
    assert json.loads(appendix) == package
    assert package["story_paragraphs"][0]["text"] in reading
    assert result["source_json"]["revision"] == "R01"
    assert result["markdown"]["bytes"] > len(reading)
    with pytest.raises(FileExistsError):
        prepare(tmp_path)
    assert source.read_bytes() == before
    assert not (tmp_path / "00_项目管理/project_state.json").exists()
    assert not (tmp_path / "00_项目管理/artifact_manifest.json").exists()


def test_publish_exact_projection_raw_response_and_same_operation_retry(tmp_path, fake_client):
    calls, response = fake_client
    narrative(tmp_path)
    prepare(tmp_path)
    before = {p: p.read_bytes() for p in (tmp_path / "R01.json", tmp_path / "R01.md")}
    first = cli.run(publish_args(tmp_path))
    cli.run(publish_args(tmp_path, "retry.json"))
    assert len(calls) == 2 and calls[0] == calls[1]
    assert calls[0][1] == {"case_id": "012", "stage": "script", "name": "R01.md",
                            "text": (tmp_path / "R01.md").read_bytes().decode("utf-8"),
                            "operation_id": OPERATION, "purpose": "独立合成测试"}
    receipt = json.loads(Path(first["receipt"]).read_text(encoding="utf-8"))
    assert receipt["response"] == response
    assert receipt["request"]["parameters"]["operationId"] == OPERATION
    assert "text" not in receipt["request"]["parameters"]
    assert receipt["request"]["text_bytes"] == len(before[tmp_path / "R01.md"])
    assert receipt["review_approval_inferred"] is False
    assert receipt["formal_adoption_performed"] is False
    assert "token" not in json.dumps(receipt).lower()
    assert all(p.read_bytes() == value for p, value in before.items())
    with pytest.raises(FileExistsError):
        cli.run(publish_args(tmp_path))
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["markdown", "json", "unanswered", "rejected", "superseded"])
def test_drift_and_ineligible_sources_fail_before_network(tmp_path, fake_client, change):
    calls, _ = fake_client
    source, package = narrative(tmp_path)
    prepare(tmp_path)
    if change == "markdown":
        with (tmp_path / "R01.md").open("a", encoding="utf-8") as stream:
            stream.write("独立改动")
    else:
        if change == "json":
            package["revision"] = "R02"
        elif change == "unanswered":
            package["open_questions"] = ["未闭合问题"]
        else:
            package["status"] = change
        source.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        cli.run(publish_args(tmp_path))
    assert not calls and not (tmp_path / "publish.json").exists()


def test_bound_dialogue_evidence_required_before_submission(tmp_path, fake_client):
    _, package = narrative(tmp_path)
    package["story_paragraphs"][0]["text"] = 'ACTOR（DLG-01）："合成发言。"'
    package["dialogues"] = [{"dialogue_id": "DLG-01", "speaker_id": "ACTOR", "text": "合成发言。",
                              "source_paragraph_id": "STP-01", "status": "draft", "source_ref": "合成测试"}]
    narrative(tmp_path, **package)
    with pytest.raises(ValueError, match="dialogue_evidence"):
        prepare(tmp_path)
    assert not fake_client[0]


@pytest.mark.parametrize("field", ["narrative", "markdown", "receipt"])
def test_project_escape_rejected_before_network(tmp_path, fake_client, field):
    narrative(tmp_path)
    prepare(tmp_path)
    args = publish_args(tmp_path)
    setattr(args, field, Path("../outside"))
    with pytest.raises(ValueError, match="inside"):
        cli.run(args)
    assert not fake_client[0]


def test_create_and_status_are_explicit_calls_with_raw_receipts(tmp_path, fake_client):
    calls, response = fake_client
    common = ["--project", str(tmp_path), "--token-file", "/not-read-by-mock"]
    cli.main(["create-case", *common, "--name", "独立合成测试", "--operation-id", OPERATION,
              "--receipt", "create.json"])
    cli.main(["status", *common, "--case-id", "012", "--receipt", "status.json"])
    assert calls == [("create-case", {"name": "独立合成测试", "operation_id": OPERATION}),
                     ("status", {"case_id": "012", "stage": "script"})]
    assert json.loads((tmp_path / "status.json").read_text())["response"] == response


def test_new_json_revision_gets_new_reading_without_rewriting_old_revision(tmp_path, fake_client):
    calls, _ = fake_client
    _, package = narrative(tmp_path)
    prepare(tmp_path)
    original = (tmp_path / "R01.md").read_bytes()
    package["revision"] = "R02"
    package["story_paragraphs"][0]["text"] += "第二修订保留同一段落编号。"
    (tmp_path / "R02.json").write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    cli.run(cli._parser().parse_args(["prepare", "--project", str(tmp_path), "--narrative", "R02.json",
                                      "--output", "R02.md"]))
    args = publish_args(tmp_path, "R02-publish.json")
    args.narrative, args.markdown = Path("R02.json"), Path("R02.md")
    args.operation_id = "e2fc997d-b5f4-4c4d-bb6b-c4eaf43e0455"
    cli.run(args)
    receipt = json.loads((tmp_path / "R02-publish.json").read_text())
    assert receipt["source_json"]["revision"] == "R02"
    assert calls[0][1]["operation_id"] == args.operation_id
    assert "STP-01" in calls[0][1]["text"]
    assert (tmp_path / "R01.md").read_bytes() == original


def test_multibyte_limit_is_rejected_without_reading_credential_or_network(tmp_path):
    _, package = narrative(tmp_path)
    package["story_paragraphs"][0]["text"] = "中" * 22000
    narrative(tmp_path, **package)
    prepare(tmp_path)
    raw = (tmp_path / "R01.md").read_bytes()
    assert len(raw.decode("utf-8")) < 100 * 1024 < len(raw)
    # The real HTTP client rejects before opening this deliberately missing token.
    with pytest.raises(cli.AgentReviewError) as stopped:
        cli.run(publish_args(tmp_path))
    assert stopped.value.code == "INVALID_INPUT"
    assert json.loads((tmp_path / "publish.json").read_text())["response"] is None


def test_symlink_cannot_route_receipt_outside_project(tmp_path, fake_client):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "link").symlink_to(outside, target_is_directory=True)
    narrative(project)
    prepare(project)
    with pytest.raises(ValueError, match="inside"):
        cli.run(publish_args(project, "link/publish.json"))
    assert not fake_client[0] and not (outside / "publish.json").exists()


def test_unknown_remote_outcome_keeps_safe_receipt_and_does_not_retry(tmp_path, fake_client, monkeypatch, capsys):
    calls, _ = fake_client
    narrative(tmp_path)
    prepare(tmp_path)

    def fail(self, **kwargs):
        calls.append(("publish", kwargs))
        raise cli.AgentReviewError("SECRET_MUST_NOT_ECHO", code="TRANSPORT_ERROR")

    monkeypatch.setattr(cli.AgentReviewClient, "publish", fail)
    args = ["publish", "--project", str(tmp_path), "--narrative", "R01.json", "--markdown", "R01.md",
            "--case-id", "012", "--operation-id", OPERATION, "--token-file", "/not-read-by-mock",
            "--receipt", "failed.json"]
    with pytest.raises(SystemExit) as stopped:
        cli.main(args)
    assert stopped.value.code == 2 and len(calls) == 1
    receipt = json.loads((tmp_path / "failed.json").read_text())
    assert receipt["response"] is None
    assert receipt["error"] == {"code": "TRANSPORT_ERROR", "http_status": None}
    assert "SECRET" not in capsys.readouterr().err + json.dumps(receipt)
