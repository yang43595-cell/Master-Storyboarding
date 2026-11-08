"""Synthetic local export/submission checks; no credentials or network access."""
import base64
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import review_text_cli as cli
from scripts import stage_context_cli as stage_cli
from scripts.core import artifacts, revision_control as rc
from scripts.core.io import write_json
from scripts.core.project_kickoff import REQUEST_PATH
from scripts.visual_asset_choices import RELATIVE as CHOICES_PATH


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


def test_prepare_requires_final_character_spec_and_displays_outfits(tmp_path):
    source, package = narrative(tmp_path)
    package["story_paragraphs"][0]["text"] = "主角在家穿居家服收拾桌面。出门前换上外出服。"
    role = package["asset_requirements"][0]
    role.update(asset_id="AST-LEAD", category="CHARACTER", character_type="HUMAN",
                entity_id="CHR-LEAD", name="主角", image_decision="需要制作",
                image_reason="两次出场须固定身份和两套服装，最终交付 1×4。")
    write_json(source, package)
    with pytest.raises(ValueError, match="必须登记 character_output_kind"):
        prepare(tmp_path)
    assert not (tmp_path / "R01.md").exists()

    role.update(character_output_kind="人物1x4", wardrobe_states=[
        {"name": "居家服", "evidence": "首次出场", "output_id": "HOME-FINAL"},
        {"name": "外出服", "evidence": "第二次出场"},
    ])
    write_json(source, package)
    with pytest.raises(ValueError, match="每套服装.*output_id"):
        prepare(tmp_path)

    role["wardrobe_states"][1]["output_id"] = "OUT-FINAL"
    write_json(source, package)
    prepare(tmp_path)
    reading = (tmp_path / "R01.md").read_text(encoding="utf-8").split("## 完整结构化来源")[0]
    assert "AST-LEAD 主角 · 人物1×4 · 居家服（HOME-FINAL）；外出服（OUT-FINAL）" in reading


def test_script_adoption_uses_current_website_decision(tmp_path, fake_client, monkeypatch):
    from scripts.core.artifacts import load_manifest
    from scripts.core.project_state import load_project_state

    narrative(tmp_path)
    prepare(tmp_path)
    _, response = fake_client
    response.update(stage="script", scope="live", operationId=OPERATION, projectId="fixture")
    cli.run(publish_args(tmp_path))
    status = "pending"

    def decision(_client, **target):
        return {"case": {"projectId": "fixture"}, "decisionStatus": status,
                "validity": {"isCurrent": True, "reviewable": True, "stillEffective": True,
                             "sourceStale": False, "dependencyStale": False, "dependencyUnapproved": False}}

    monkeypatch.setattr(cli.AgentReviewClient, "decision", decision, raising=False)
    def run(filename):
        return cli.run(cli._parser().parse_args(["decision", "--project", str(tmp_path),
            "--publication", "publish.json", "--stage", "script", "--adopt-script-id", "NARRATIVE-R01",
            "--token-file", "/not-read-by-mock", "--receipt", filename]))

    assert "adoption" not in run("pending.json")
    assert not load_manifest(tmp_path)["artifacts"]
    status = "approved"
    run("approved.json")
    assert load_project_state(tmp_path)["current_artifacts"] == ["NARRATIVE-R01"]
    assert run("approved-reread.json")["adoption"]["already_current"] is True


@pytest.fixture
def kickoff_review_site(tmp_path, monkeypatch):
    """Use the real client validator with an in-process synthetic site transport."""
    write_json(tmp_path / REQUEST_PATH, {
        "kickoff": {"decision_ref": "synthetic kickoff", "product": {"mode": "none"},
                    "canvas": {"platform": "redframe", "model": "Seedance 2.5",
                               "aspect_ratio": "9:16", "resolution": "720p", "create_new": True}},
    })
    versions = {}
    head = None
    status = "approved"
    on_decision = None

    def request(_client, method, route, payload=None, *, query=None):
        nonlocal head
        if method == "POST" and route == "publish":
            version = versions.setdefault(payload["operationId"], f"{len(versions) + 1:064x}")
            head = version
            return {"ok": True, "projectId": "fixture", "scope": "live", "caseId": payload["caseId"],
                    "stage": "script", "version": version, "headGeneration": len(versions),
                    "assetId": f"script-{len(versions)}", "url": "/review", "operationId": payload["operationId"]}
        assert method == "GET" and route == "decision"
        if on_decision is not None:
            on_decision()
        current = head == query["version"]
        scope = {**query, "scope": "live", "projectId": "fixture"}
        event = {"decision": "approve", "actorId": "reviewer", "createdAt": "2026-11-08T00:00:00Z"}
        return {"case": {"id": query["caseId"], "projectId": "fixture"}, "stage": "script",
                "version": query["version"], "decisionStatus": status,
                "approval": {"scope": scope, "actorId": event["actorId"], "decidedAt": event["createdAt"]}
                if status == "approved" else None, "latestEvent": event if status == "approved" else None,
                "validity": {"isCurrent": current, "stillEffective": current, "reviewable": current,
                             "sourceStale": False, "dependencyStale": False, "dependencyUnapproved": False}}

    def set_decision(value, callback=None):
        nonlocal status, on_decision
        status, on_decision = value, callback

    monkeypatch.setattr(cli.AgentReviewClient, "_request", request)
    return set_decision


def website_decision(project, receipt, *, revision="R01", change_id=None):
    args = ["decision", "--project", str(project), "--stage", "script",
            "--publication", "publish.json" if revision == "R01" else "R02-publish.json",
            "--adopt-script-id", f"N-{revision}", "--token-file", "/not-read-by-mock", "--receipt", receipt]
    if change_id:
        args.extend(["--change-id", change_id])
    return cli.run(cli._parser().parse_args(args))


@pytest.fixture
def asset_review_site(tmp_path, kickoff_review_site, monkeypatch):
    script_request = cli.AgentReviewClient._request
    site = SimpleNamespace(posts=[], publications={}, head=None, status_source=None,
                           source_stale=False, on_status=None, timeout_operation=None)

    def request(client, method, route, payload=None, *, query=None):
        if route == "publish-assets":
            site.posts.append(copy.deepcopy(payload))
            operation = payload["operationId"]
            if operation not in site.publications:
                generation = len(site.publications) + 1
                site.publications[operation] = {
                    "ok": True, "projectId": "fixture", "scope": "live", "caseId": payload["caseId"],
                    "stage": "assets", "version": f"{100 + generation:064x}", "headGeneration": generation,
                    "assetId": f"asset-{generation}", "url": "/review", "operationId": operation,
                    "sha256": hashlib.sha256(base64.b64decode(payload["dataBase64"])).hexdigest(),
                }
                site.head = site.publications[operation]
            if site.timeout_operation == operation:
                site.timeout_operation = None
                raise cli.AgentReviewError("Synthetic timeout after POST", code="TRANSPORT_ERROR")
            return copy.deepcopy(site.publications[operation])
        if route == "status" and query["stage"] == "assets":
            if site.on_status:
                site.on_status()
            head = site.head
            return {"case": {"id": query["caseId"], "projectId": "fixture", "name": "合成案例",
                             "status": "active", "revision": head["headGeneration"]},
                    "stage": "assets", "sourceScriptVersion": site.status_source or f"{1:064x}",
                    "head": {"version": head["version"], "generation": head["headGeneration"]},
                    "expectations": {"expectedHeadVersion": head["version"],
                                     "expectedHeadGeneration": head["headGeneration"], "expectedReviewRevision": 1},
                    "decision": {"validity": {"sourceStale": site.source_stale}}}
        return script_request(client, method, route, payload, query=query)

    monkeypatch.setattr(cli.AgentReviewClient, "_request", request)
    (tmp_path / "candidate.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/Z1sAAAAASUVORK5CYII="))
    return site


def publish_asset_args(project, receipt, *, ast="AST-PROP-PAPER", output_id="paper",
                       operation=OPERATION[:-1] + "6", references=(), script_publication="publish.json"):
    args = ["publish-asset", "--project", str(project), "--script-publication", script_publication,
            "--image", "candidate.png", "--artifact-id", f"IMAGE-{output_id}", "--ast-id", ast,
            "--output-id", output_id, "--purpose", "合成选图用途", "--operation-id", operation,
            "--token-file", "/not-read-by-mock", "--receipt", receipt]
    for ref in references:
        args.extend(["--reference-publication", ref])
    return cli._parser().parse_args(args)


def publish_asset_script(project, *, adopt=True):
    _, package = narrative(project)
    package["asset_requirements"][0].update(image_decision="需要制作", image_reason="合成图片验收")
    path, package = narrative(project, **package)
    prepare(project)
    cli.run(publish_args(project))
    if adopt:
        website_decision(project, "script-approved.json")
    return path, package


def test_asset_publication_checks_exact_final_character_canvas_before_post(tmp_path, asset_review_site):
    from PIL import Image

    source, package = narrative(tmp_path)
    package["asset_requirements"][0].update(
        asset_id="AST-LEAD", category="CHARACTER", character_type="HUMAN",
        entity_id="CHR-LEAD", name="主角", image_decision="需要制作",
        image_reason="固定两次出场的身份与服装", character_output_kind="人物1x4",
        wardrobe_states=[{"name": "居家服", "evidence": "首次出场", "output_id": "HOME-FINAL"}])
    write_json(source, package)
    prepare(tmp_path)
    cli.run(publish_args(tmp_path))
    website_decision(tmp_path, "script-approved.json")

    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (300, 400)).save(candidate)
    final = publish_asset_args(tmp_path, "final.json", ast="AST-LEAD", output_id="HOME-FINAL")
    with pytest.raises(ValueError, match=r"AST-LEAD@HOME-FINAL.*16:9"):
        cli.run(final)
    assert not (tmp_path / "final.json").exists()
    assert not asset_review_site.posts

    with candidate.open("wb") as stream:
        stream.truncate(cli.MAX_IMAGE_BYTES + 1)
    oversized = publish_asset_args(tmp_path, "oversized.json", ast="AST-LEAD", output_id="HOME-FINAL")
    with pytest.raises(cli.AgentReviewError, match="at most 8 MiB"):
        cli.run(oversized)
    assert not asset_review_site.posts

    # The same 3:4 canvas is legitimate for a separate intermediate head image.
    Image.new("RGB", (300, 400)).save(candidate)
    head = publish_asset_args(tmp_path, "head.json", ast="AST-LEAD", output_id="HEAD",
                              operation=OPERATION[:-1] + "7")
    cli.run(head)
    Image.new("RGB", (160, 90)).save(candidate)
    cli.run(final)
    assert [post["outputId"] for post in asset_review_site.posts] == ["HEAD", "HOME-FINAL"]
    assert base64.b64decode(asset_review_site.posts[-1]["dataBase64"]) == candidate.read_bytes()


def test_asset_publication_uses_effective_web_requirements(tmp_path, asset_review_site):
    _, package = publish_asset_script(tmp_path)
    paper = package["asset_requirements"][0]
    added = {**paper, "asset_id": "AST-USER-BOX", "name": "盒子"}
    choices = {"revision": 1, "added": [added], "excluded": {}, "replaced": {},
               "history": [{"operation": "add", "ast_id": added["asset_id"], "user_confirmed": True,
                            "source_revision": package["revision"]}]}
    write_json(tmp_path / CHOICES_PATH, choices)
    output = cli.run(publish_asset_args(tmp_path, "paper.json"))
    assert output["readback"]["status"] == "current"
    assert output["response"]["assetId"] == "asset-1"

    choices["revision"] = 2
    choices["excluded"][paper["asset_id"]] = {"name": paper["name"]}
    choices["history"].append({"operation": "exclude", "ast_id": paper["asset_id"], "user_confirmed": True,
                               "source_revision": package["revision"]})
    write_json(tmp_path / CHOICES_PATH, choices)
    with pytest.raises(ValueError, match="active image AST"):
        cli.run(publish_asset_args(tmp_path, "excluded.json"))
    box_args = dict(ast=added["asset_id"], output_id="box", operation=OPERATION[:-1] + "7")
    with pytest.raises(ValueError, match="Referenced assets must be active"):
        cli.run(publish_asset_args(tmp_path, "excluded-reference.json", references=["paper.json"], **box_args))
    assert len(asset_review_site.posts) == 1
    assert cli.run(publish_asset_args(tmp_path, "box.json", **box_args))["response"]["assetId"] == "asset-2"
    assert asset_review_site.posts[-1]["astId"] == added["asset_id"]

    choices["revision"] = 3
    choices["image_decisions"] = {"source_revision": package["revision"], "items": {
        added["asset_id"]: {"image_decision": "待定", "image_reason": "等待确认用途"},
    }}
    write_json(tmp_path / CHOICES_PATH, choices)
    with pytest.raises(ValueError, match="active image AST"):
        cli.run(publish_asset_args(tmp_path, "disabled.json", **box_args))
    assert len(asset_review_site.posts) == 2


def test_asset_publication_requires_locally_adopted_exact_script(tmp_path, asset_review_site):
    source, _ = publish_asset_script(tmp_path, adopt=False)
    artifacts.register_workspace_artifact(tmp_path, {"artifact_id": "N-R01", "logical_id": "narrative.package",
        "type": "narrative.package", "path_or_url": source.name, "revision": "R01", "depends_on": [],
        "checksum": artifacts.digest(source), "user_approved": False})
    with pytest.raises(ValueError, match="当前有效且已由你确认"):
        cli.run(publish_asset_args(tmp_path, "unadopted.json"))
    website_decision(tmp_path, "script-approved.json")

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(source.read_bytes())
    cli.run(cli._parser().parse_args(["prepare", "--project", str(tmp_path), "--narrative", duplicate.name,
                                      "--output", "duplicate.md"]))
    args = publish_args(tmp_path, "duplicate-publish.json")
    args.narrative, args.markdown, args.operation_id = duplicate, Path("duplicate.md"), OPERATION[:-1] + "5"
    cli.run(args)
    with pytest.raises(ValueError, match="exact locally adopted"):
        cli.run(publish_asset_args(tmp_path, "duplicate-asset.json", script_publication="duplicate-publish.json"))
    assert not asset_review_site.posts


def test_asset_post_receipt_survives_status_failure_and_is_reusable(tmp_path, asset_review_site):
    publish_asset_script(tmp_path)
    first_op = OPERATION[:-1] + "6"

    def interrupted_readback():
        saved = json.loads((tmp_path / "first.json").read_text())
        assert saved["response"]["assetId"] == "asset-1"
        raise cli.AgentReviewError("Synthetic status timeout", code="TRANSPORT_ERROR")

    asset_review_site.on_status = interrupted_readback
    first = cli.run(publish_asset_args(tmp_path, "first.json"))
    assert first["readback"]["status"] == "unknown"
    assert first["readback"]["error"]["code"] == "TRANSPORT_ERROR"
    assert json.loads((tmp_path / "first.json").read_text())["response"] == first["response"]

    asset_review_site.on_status = None
    second = cli.run(publish_asset_args(tmp_path, "second.json", output_id="second",
                                        operation=OPERATION[:-1] + "7", references=["first.json"]))
    assert second["readback"]["status"] == "current"
    assert asset_review_site.posts[-1]["references"] == ["asset-1"]

    asset_review_site.status_source = f"{25:064x}"
    retry = cli.run(publish_asset_args(tmp_path, "first-retry.json", operation=first_op))
    assert retry["response"] == first["response"]
    assert retry["readback"]["status"] == "historical"
    asset_review_site.status_source = None
    final = cli.run(publish_asset_args(tmp_path, "final.json", output_id="final", operation=OPERATION[:-1] + "8",
                                       references=["first-retry.json", "second.json"]))
    assert final["readback"]["status"] == "current"
    assert asset_review_site.posts[-1]["references"] == ["asset-1", "asset-2"]
    assert len(asset_review_site.publications) == 3
    assert not json.loads((tmp_path / "final.json").read_text())["review_approval_inferred"]


def test_asset_exact_retry_after_unknown_post_recovers_historical_receipt(tmp_path, asset_review_site):
    publish_asset_script(tmp_path)
    first_op = OPERATION[:-1] + "6"
    asset_review_site.timeout_operation = first_op
    with pytest.raises(cli.AgentReviewError, match="timeout"):
        cli.run(publish_asset_args(tmp_path, "unknown-post.json"))
    assert json.loads((tmp_path / "unknown-post.json").read_text())["response"] is None
    cli.run(publish_asset_args(tmp_path, "new-head.json", output_id="second", operation=OPERATION[:-1] + "7"))
    recovered = cli.run(publish_asset_args(tmp_path, "recovered.json", operation=first_op))
    assert recovered["response"]["assetId"] == "asset-1"
    assert recovered["readback"]["status"] == "historical"
    assert asset_review_site.posts[0] == asset_review_site.posts[2]
    assert len(asset_review_site.publications) == 2


@pytest.mark.parametrize("stale_flag", [False, True])
def test_same_head_with_invalid_source_saves_post_but_blocks_reference(tmp_path, asset_review_site, stale_flag):
    publish_asset_script(tmp_path)
    if stale_flag:
        asset_review_site.source_stale = True
    else:
        asset_review_site.status_source = f"{25:064x}"
    first = cli.run(publish_asset_args(tmp_path, "mismatched.json"))
    assert first["response"]["assetId"] == "asset-1"
    assert first["readback"]["status"] == "source_mismatch"
    assert json.loads((tmp_path / "mismatched.json").read_text())["response"] == first["response"]
    with pytest.raises(ValueError, match="stale or wrong source script"):
        cli.run(publish_asset_args(tmp_path, "later.json", output_id="second", operation=OPERATION[:-1] + "7",
                                   references=["mismatched.json"]))
    assert len(asset_review_site.posts) == 1


def test_kickoff_rejects_pending_receipt_in_manual_adoption_and_accepts_live_approval(tmp_path, kickoff_review_site):
    source, _ = narrative(tmp_path)
    prepare(tmp_path)
    cli.run(publish_args(tmp_path))
    kickoff_review_site("pending")
    assert "adoption" not in website_decision(tmp_path, "pending.json")
    pending = str(tmp_path / "pending.json")
    artifacts.register_workspace_artifact(tmp_path, {"artifact_id": "N-R01", "logical_id": "narrative.package",
        "type": "narrative.package", "path_or_url": source.name, "revision": "R01", "depends_on": [],
        "checksum": artifacts.digest(source), "user_approved": False})
    with pytest.raises(ValueError):
        stage_cli.main(["adopt-package", "--project", str(tmp_path), "--artifact-id", "N-R01",
                        "--logical-id", "narrative.package", "--path", source.name, "--revision", "R01",
                        "--approval-ref", pending])
    with pytest.raises(ValueError):
        artifacts.adopt_initial_package(tmp_path, "N-R01", pending)
    assert not artifacts.load_project_state(tmp_path)["user_confirmations"]

    kickoff_review_site("approved")
    website_decision(tmp_path, "approved.json")
    [entry] = artifacts.load_manifest(tmp_path)["artifacts"]
    assert (entry["status"], entry["user_approved"], entry["checksum"]) == ("current", True, artifacts.digest(source))
    assert website_decision(tmp_path, "approved-reread.json")["adoption"]["already_current"] is True


def test_kickoff_rechecks_published_bytes_after_live_approval(tmp_path, kickoff_review_site):
    source, package = narrative(tmp_path)
    published = source.read_bytes()
    prepare(tmp_path)
    cli.run(publish_args(tmp_path))
    package["story_paragraphs"][0]["text"] += "同一修订号，送审后改写。"
    kickoff_review_site("approved", lambda: write_json(source, package))
    with pytest.raises(ValueError):
        website_decision(tmp_path, "drifted.json")
    assert source.read_bytes() != published
    assert artifacts.load_project_state(tmp_path)["current_artifacts"] == []
    assert artifacts.load_project_state(tmp_path)["user_confirmations"] == []

    source.write_bytes(published)
    kickoff_review_site("approved")
    website_decision(tmp_path, "exact.json")
    [entry] = artifacts.load_manifest(tmp_path)["artifacts"]
    assert entry["status"] == "current" and entry["checksum"] == artifacts.digest(source)


def test_kickoff_revision_requires_its_own_effective_website_approval(tmp_path, kickoff_review_site):
    _, package = narrative(tmp_path)
    prepare(tmp_path)
    cli.run(publish_args(tmp_path))
    website_decision(tmp_path, "R01-approved.json")
    package["revision"] = "R02"
    package["story_paragraphs"][0]["text"] += "第二版修订。"
    source = tmp_path / "R02.json"
    write_json(source, package)
    cli.run(cli._parser().parse_args(["prepare", "--project", str(tmp_path), "--narrative", source.name,
                                      "--output", "R02.md"]))
    args = publish_args(tmp_path, "R02-publish.json")
    args.narrative, args.markdown = Path("R02.json"), Path("R02.md")
    args.operation_id = "e2fc997d-b5f4-4c4d-bb6b-c4eaf43e0455"
    cli.run(args)
    change_id = "SCRIPT-REVISION"
    rc.open_change(tmp_path, change_id=change_id, tier="T1", logical_ids=["narrative.package"], reason="synthetic")
    rc.confirm_impact(tmp_path, change_id=change_id, approval_ref="synthetic scope decision")
    rc.record_candidate(tmp_path, change_id=change_id, artifact_id="N-R02", logical_id="narrative.package",
                        artifact_type="narrative.package", candidate_path=source.name, revision="R02")

    assert "adoption" not in website_decision(tmp_path, "R01-now-historical.json")
    with pytest.raises(ValueError):
        old = str(tmp_path / "R01-approved.json")
        rc.record_review(tmp_path, change_id=change_id, verdict="approved", report_ref=old)
        rc.confirm_candidate(tmp_path, change_id=change_id, approval_ref=old)
    assert artifacts.load_project_state(tmp_path)["current_artifacts"] == ["N-R01"]
    kickoff_review_site("pending")
    assert "adoption" not in website_decision(tmp_path, "R02-pending.json", revision="R02", change_id=change_id)
    assert artifacts.load_project_state(tmp_path)["current_artifacts"] == ["N-R01"]

    kickoff_review_site("approved")
    website_decision(tmp_path, "R02-approved.json", revision="R02", change_id=change_id)
    first, second = artifacts.load_manifest(tmp_path)["artifacts"]
    assert first["status"] == "superseded"
    assert (second["status"], second["checksum"]) == ("current", artifacts.digest(source))
    assert artifacts.load_project_state(tmp_path)["current_artifacts"] == ["N-R02"]


@pytest.mark.parametrize("kind", ["authored_addition", "authorized_revision"])
def test_new_creative_dialogue_waits_for_approval_in_whole_script_review(tmp_path, fake_client, kind):
    from scripts.core import artifacts, story_sources
    from test_dialogue_origins import bind, origin, verified_package

    _, record = verified_package(tmp_path, isolated=False)
    if kind == "authored_addition":
        row = record["dialogues"][1]
    else:
        row = record["dialogues"][0]
        row.update(text="获准改编的新台词。", origin=origin(kind),
                   editorial_proposal={"original_text": row["text"], "proposed_text": "获准改编的新台词。",
                                       "reason": "synthetic adaptation", "source_ids": row["source_ids"]})
    row.pop("verification")
    package = bind(tmp_path, record)
    package["open_questions"] = []
    source, _ = narrative(tmp_path, **package)
    before = source.read_bytes(), (tmp_path / "review.json").read_bytes()

    with pytest.raises(ValueError, match="未核对白证据"):
        story_sources.validate_story_sources(package, project_root=tmp_path, require_dialogue_evidence=True)
    assert prepare(tmp_path)["source_json"]["awaiting_creative_approval_ids"] == [row["dialogue_id"]]
    assert cli.run(publish_args(tmp_path))["response"]["version"] == "synthetic-version"
    assert before == (source.read_bytes(), (tmp_path / "review.json").read_bytes())
    assert not artifacts.load_project_state(tmp_path).get("user_confirmations")

    artifacts.register_workspace_artifact(tmp_path, {
        "artifact_id": "N-R01", "logical_id": "narrative.package", "type": "narrative.package",
        "revision": package["revision"], "path_or_url": source.name, "depends_on": [],
    })
    adoption = artifacts.adopt_initial_package(tmp_path, "N-R01", "synthetic:whole-script-human-approval")
    assert adoption["confirmation"]["sha256"] == artifacts.digest(source)
    story_sources.validate_story_sources(package, project_root=tmp_path, narrative_path=source, require_dialogue_evidence=True)
    assert before == (source.read_bytes(), (tmp_path / "review.json").read_bytes())
    other = tmp_path / "other.json"
    other.write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match="未核对白证据"):
        story_sources.validate_story_sources(package, project_root=tmp_path, narrative_path=other, require_dialogue_evidence=True)
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="未核对白证据"):
        story_sources.validate_story_sources(package, project_root=tmp_path, narrative_path=source, require_dialogue_evidence=True)


@pytest.mark.parametrize("fault", ["source_unverified", "speaker_unknown", "missing_origin"])
def test_review_preparation_still_blocks_unresolved_sources_or_identity(tmp_path, fault):
    from test_dialogue_origins import bind, verified_package

    _, record = verified_package(tmp_path)
    if fault == "source_unverified":
        record["dialogues"][0].pop("verification")
    else:
        row = record["dialogues"][1]
        row.pop("verification")
        if fault == "speaker_unknown":
            row["speaker_status"] = "inferred_unverified"
        else:
            row.pop("origin")
    package = bind(tmp_path, record)
    package["open_questions"] = []
    narrative(tmp_path, **package)
    with pytest.raises(ValueError, match="未核对白证据|缺少精确原始证据"):
        prepare(tmp_path)
    assert not (tmp_path / "R01.md").exists()


def test_isolated_candidate_cannot_publish_review_text(tmp_path, fake_client):
    from test_candidate_test_mode import setup

    setup(tmp_path)
    narrative(tmp_path)
    prepare(tmp_path)
    with pytest.raises(ValueError, match="候选测试禁止"):
        cli.run(publish_args(tmp_path))
    assert not fake_client[0]
    assert not (tmp_path / "publish.json").exists()


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
