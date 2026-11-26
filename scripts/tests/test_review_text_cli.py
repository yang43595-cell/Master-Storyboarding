"""Synthetic local export/submission checks; no credentials or network access."""
import base64
import copy
import hashlib
import io
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


def numbered_reading(reading, package):
    paragraphs = package["story_paragraphs"]
    body = "## 故事正文\n\n" + "\n\n".join(paragraph["text"] for paragraph in paragraphs)
    numbered = "\n\n".join(
        f"## {paragraph['paragraph_id']}\n\n{paragraph['text']}" for paragraph in paragraphs)
    assert body in reading
    return reading.replace(body, numbered, 1)


def status_response(case_id="012", *, version=None, generation=0):
    return {"case": {"id": case_id, "projectId": "fixture", "name": "合成案例",
                     "status": "active", "revision": generation},
            "stage": "script", "sourceScriptVersion": version,
            "head": {"version": version, "generation": generation} if version else None,
            "expectations": {"expectedHeadVersion": version, "expectedHeadGeneration": generation,
                             "expectedReviewRevision": 0}}


def save_preflight(project, response):
    write_json(project / "preflight.json", {"kind": "agent-review-api-receipt", "command": "status",
        "project_path": str(project), "base_url": cli.DEFAULT_BASE_URL,
        "request": {"parameters": {"caseId": "012", "stage": "script"}}, "response": response})


def publish_args(project, receipt="publish.json"):
    if not (project / "preflight.json").exists():
        save_preflight(project, status_response())
    return cli._parser().parse_args(["publish", "--project", str(project), "--narrative", "R01.json",
                                    "--markdown", "R01.md", "--case-id", "012", "--operation-id", OPERATION,
                                    "--token-file", "/not-read-by-mock", "--receipt", receipt,
                                    "--preflight", "preflight.json", "--purpose", "独立合成测试"])


@pytest.fixture
def fake_client(monkeypatch):
    calls = []
    response = {"ok": True, "caseId": "012", "version": "synthetic-version", "headGeneration": 1,
                "assetId": "synthetic-asset", "url": "/?caseId=012", "projectId": "fixture",
                "extension": {"preserve": True}}

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
            return status_response(kwargs["case_id"])

    monkeypatch.setattr(cli, "AgentReviewClient", Client)
    return calls, response


def test_prepare_is_complete_exclusive_and_needs_no_token(tmp_path, monkeypatch):
    source, package = narrative(tmp_path)
    package["story_paragraphs"].append({"paragraph_id": "STP-02", "text": "桌上的纸被翻过去。\n\n灯暗下来。"})
    write_json(source, package)
    before = source.read_bytes()
    monkeypatch.setattr(cli, "AgentReviewClient", lambda **kwargs: pytest.fail("prepare must stay local"))
    result = prepare(tmp_path)
    reading = (tmp_path / "R01.md").read_text(encoding="utf-8")
    appendix = reading.split("````json\n", 1)[1].rsplit("\n````", 1)[0]
    assert json.loads(appendix) == package
    story = reading.split("## 故事正文\n\n", 1)[1].split("\n\n## 完整结构化来源", 1)[0]
    assert story == "\n\n".join(paragraph["text"] for paragraph in package["story_paragraphs"])
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


@pytest.mark.parametrize("numbered", [False, True])
@pytest.mark.parametrize("line_ending", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_script_adoption_uses_current_website_decision(tmp_path, fake_client, monkeypatch, numbered, line_ending):
    from scripts.core.artifacts import load_manifest
    from scripts.core.project_state import load_project_state

    _, package = narrative(tmp_path)
    prepare(tmp_path)
    reading = tmp_path / "R01.md"
    text = reading.read_text(encoding="utf-8")
    if numbered:
        text = numbered_reading(text, package)
    reading.write_bytes(text.replace("\n", line_ending).encode("utf-8"))
    calls, response = fake_client
    response.update(stage="script", scope="live", operationId=OPERATION, projectId="fixture")
    cli.run(publish_args(tmp_path))
    status = "pending"

    def decision(_client, **target):
        calls.append(("decision", target))
        return {"case": {"projectId": "fixture"}, "decisionStatus": status,
                "validity": {"isCurrent": True, "reviewable": True, "stillEffective": True,
                             "sourceStale": False, "dependencyStale": False, "dependencyUnapproved": False}}

    monkeypatch.setattr(cli.AgentReviewClient, "decision", decision, raising=False)
    def run(filename):
        return cli.run(cli._parser().parse_args(["decision", "--project", str(tmp_path),
            "--publication", "publish.json", "--stage", "script", "--adopt-script-id", "NARRATIVE-R01",
            "--token-file", "/not-read-by-mock", "--receipt", filename]))

    publication = tmp_path / "publish.json"
    original = publication.read_bytes()
    tampered = json.loads(original)
    wrong = text.replace("合成桌面", "真实桌面").replace("\n", line_ending).encode("utf-8")
    expected_sha = hashlib.sha256(wrong).hexdigest()
    tampered["markdown"].update(sha256=expected_sha, bytes=len(wrong))
    tampered["request"].update(text_sha256=expected_sha, text_bytes=len(wrong))
    write_json(publication, tampered)
    with pytest.raises(ValueError, match="阅读稿与本版编导 JSON 不一致"):
        run("tampered.json")
    assert not any(kind == "decision" for kind, _ in calls)
    publication.write_bytes(original)

    assert "adoption" not in run("pending.json")
    assert not load_manifest(tmp_path)["artifacts"]
    status = "approved"
    run("approved.json")
    assert load_project_state(tmp_path)["current_artifacts"] == ["NARRATIVE-R01"]
    assert run("approved-reread.json")["adoption"]["already_current"] is True


def test_local_adoption_failure_keeps_verified_decision_and_resumes_same_publication(tmp_path, fake_client, monkeypatch, capsys):
    source, _ = narrative(tmp_path)
    prepare(tmp_path)
    calls, response = fake_client
    response.update(stage="script", scope="live", operationId=OPERATION)
    cli.run(publish_args(tmp_path))

    def decision(_client, **target):
        calls.append(("decision", target))
        return {"case": {"projectId": "fixture"}, "decisionStatus": "approved",
                "validity": {"isCurrent": True, "reviewable": True, "stillEffective": True,
                             "sourceStale": False, "dependencyStale": False, "dependencyUnapproved": False}}

    monkeypatch.setattr(cli.AgentReviewClient, "decision", decision, raising=False)
    different = tmp_path / "different.json"
    different.write_bytes(source.read_bytes())
    artifacts.register_workspace_artifact(tmp_path, {"artifact_id": "NARRATIVE-R01", "logical_id": "narrative.package",
        "type": "narrative.package", "path_or_url": different.name, "revision": "R01", "depends_on": [],
        "checksum": artifacts.digest(source), "user_approved": False})
    before = source.read_bytes()
    args = ["decision", "--project", str(tmp_path), "--publication", "publish.json", "--stage", "script",
            "--adopt-script-id", "NARRATIVE-R01", "--token-file", "/not-read-by-mock"]
    with pytest.raises(SystemExit) as blocked:
        cli.main(args + ["--receipt", "approved-but-blocked.json"])
    assert blocked.value.code == 2
    error = json.loads(capsys.readouterr().err)
    assert error["step"] == "local_adoption" and error["website_decision"] == "verified_approved"
    assert error["code"] == "CANDIDATE_ID_CONFLICT"
    assert error["publication"] == "publish.json" and error["website_version"] == response["version"]
    assert error["revision"] == "R01" and error["artifact_id"] == "NARRATIVE-R01"
    receipt = json.loads(Path(error["receipt"]).read_text())
    assert receipt["response"]["decisionStatus"] == "approved" and receipt["local_adoption"] == error
    assert sum(kind == "decision" for kind, _ in calls) == 1

    # Simulate a local registration repair without changing or republishing the source.
    manifest = artifacts.load_manifest(tmp_path)
    assert manifest["artifacts"][0]["status"] == "candidate"
    manifest["artifacts"][0]["path_or_url"] = source.name
    write_json(tmp_path / "00_项目管理/artifact_manifest.json", manifest)
    assert cli.main(args + ["--receipt", "resumed.json"]) == 0
    assert json.loads(capsys.readouterr().out)["adoption"]["artifact"]["artifact_id"] == "NARRATIVE-R01"
    sent = [parameters for kind, parameters in calls if kind == "decision"]
    assert len(sent) == 2 and sent[0] == sent[1]
    assert sum(kind == "publish" for kind, _ in calls) == 1
    assert source.read_bytes() == before
    assert artifacts.load_manifest(tmp_path)["artifacts"][0]["status"] == "current"


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
        if route == "status":
            return status_response(query["caseId"], version=head, generation=len(versions))
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


def chat_args(project, source, revision="R01", *, change_id=None):
    message = project / f"{revision}-message.json"
    write_json(message, {"scope": "artifact", "quote": f"编导稿 {revision} 整稿通过",
                         "at": "2026-11-15T16:00:00+08:00", "review_ref": f"synthetic message {revision}"})
    args = ["confirm-script-in-chat", "--project", str(project), "--artifact-id", f"N-{revision}",
            "--path", source.name, "--revision", revision, "--sha256", artifacts.digest(source),
            "--confirmation-json", str(message)]
    if change_id:
        args.extend(["--change-id", change_id])
    return stage_cli.parser().parse_args(args)


@pytest.mark.parametrize("handoff_before_revision", [True, False])
def test_chat_approval_adopts_initial_and_revised_script_for_downstream(
    tmp_path, kickoff_review_site, capsys, monkeypatch, handoff_before_revision,
):
    from scripts.visual_asset_source import source_project

    def unexpected_website_request(*args, **kwargs):
        pytest.fail("Chat approval must finish without contacting the review website")

    monkeypatch.setattr(cli.AgentReviewClient, "_request", unexpected_website_request)
    stage_cli.register_workflow_authorization(tmp_path, authorization_id="FLOW-R01",
        authorization_ref="synthetic prior user authorization", skills=["storyboard-planner"])
    source, package = narrative(tmp_path)
    args = chat_args(tmp_path, source)
    monkeypatch.setattr(stage_cli.sys, "stdin", io.StringIO(Path(args.confirmation_json).read_text(encoding="utf-8")))
    assert stage_cli.main(["confirm-script-in-chat", "--project", str(tmp_path), "--artifact-id", args.artifact_id,
                           "--path", args.path, "--revision", args.revision, "--sha256", args.sha256,
                           "--confirmation-json", "-"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["confirmation"]["channel"] == "chat_confirmation"
    assert first["confirmation"]["quote"] == "编导稿 R01 整稿通过"
    assert first["confirmation"]["sha256"] == args.sha256
    assert source_project(tmp_path)[1]["artifact_id"] == "N-R01"
    assert stage_cli.confirm_script_in_chat(args)["already_current"]

    before = artifacts.load_project_state(tmp_path)
    assert before["current_stage"] == "" and before["next_stage"] is None
    handoff = stage_cli.parser().parse_args(["handoff", "--project", str(tmp_path), "--to-skill", "storyboard-planner"])
    if handoff_before_revision:
        report = stage_cli.handoff(handoff)
        assert report["artifact_ids"] == ["N-R01"] and report["from_stage"] == "creative-director"
        assert stage_cli.handoff(handoff) == report
        assert artifacts.load_project_state(tmp_path) == before

    package["revision"] = "R02"
    package["story_paragraphs"][0]["text"] += "第二版修订。"
    revised = tmp_path / "R02.json"
    write_json(revised, package)
    dependent = {"artifact_id": "OLD-ASSETS", "logical_id": "visual.asset.manifest",
                 "type": "visual.asset.manifest", "status": "current", "revision": "R01",
                 "depends_on": ["narrative.package"], "path_or_url": "old-assets.json", "user_approved": True}
    manifest_path = tmp_path / artifacts.MANIFEST_PATH
    manifest = artifacts.load_manifest(tmp_path)
    manifest["artifacts"].append(dependent)
    write_json(manifest_path, manifest)
    change_id = "CHAT-REVISION"
    rc.open_change(tmp_path, change_id=change_id, tier="T1", logical_ids=["narrative.package"], reason="synthetic")
    rc.confirm_impact(tmp_path, change_id=change_id, approval_ref="synthetic impact")
    rc.record_candidate(tmp_path, change_id=change_id, artifact_id="N-R02", logical_id="narrative.package",
                        artifact_type="narrative.package", candidate_path=revised.name, revision="R02")
    args = chat_args(tmp_path, revised, "R02", change_id=change_id)
    wrong_candidate = copy.copy(args)
    wrong_candidate.revision = "R03"
    with pytest.raises(ValueError, match="与对话确认范围不一致"):
        stage_cli.confirm_script_in_chat(wrong_candidate)
    assert source_project(tmp_path)[1]["artifact_id"] == "N-R01"
    result = stage_cli.confirm_script_in_chat(args)
    assert result["confirmation"]["channel"] == "chat_confirmation"
    assert result["confirmation"]["change_id"] == change_id
    assert stage_cli.confirm_script_in_chat(args)["already_current"]
    assert source_project(tmp_path)[1]["artifact_id"] == "N-R02"
    statuses = {row["artifact_id"]: row["status"] for row in artifacts.load_manifest(tmp_path)["artifacts"]}
    assert statuses == {"N-R01": "superseded", "OLD-ASSETS": "stale", "N-R02": "current"}
    receipts = artifacts.load_project_state(tmp_path)["user_confirmations"]
    assert [row["revision"] for row in receipts if row.get("channel") == "chat_confirmation"] == ["R01", "R02"]
    report = stage_cli.handoff(handoff)
    assert report["artifact_ids"] == ["N-R02"]
    current = artifacts.load_project_state(tmp_path)
    assert (current["current_stage"], current["next_stage"]) == ("", None)
    status = stage_cli.status(handoff)
    assert status["production_allowed"] and status["next_allowed_stage"] == "storyboard-planner"
    [action] = status["next_actions"]
    assert action["action"] == "director" and action["artifact_ids"] == ["N-R02"]


@pytest.mark.parametrize("workflow, route_action", [
    ("pending", None),
    ("creative", "director"),
    ("depth-guided-recreation", "recreation"),
])
def test_adopted_script_hands_off_assets_and_the_selected_route_together(
    tmp_path, kickoff_review_site, workflow, route_action,
):
    request_path = tmp_path / REQUEST_PATH
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["kickoff"].update(creation={"workflow": workflow}, image_generation={
        "provider": "redframe", "model": "Doubao Seedream 5.5", "fee_scope": "existing_quota",
        "reference_upload_authorized": True, "authorization_ref": "synthetic image authorization",
        "scope": "required_assets_and_supplements",
    })
    target = "AST-PROP-PAPER@view"
    request["asset_review_targets"] = [target]
    write_json(request_path, request)
    stage_cli.register_workflow_authorization(
        tmp_path, authorization_id="FLOW-R01", authorization_ref="synthetic director authorization",
        skills=["storyboard-planner"],
    )
    source, package = narrative(tmp_path)
    package["asset_requirements"][0].update(image_decision="需要制作", image_reason="验证道具缺图交接")
    write_json(source, package)
    cli.run(cli._parser().parse_args([
        "prepare", "--project", str(tmp_path), "--narrative", source.name,
        "--output", "R01.md", "--artifact-id", "N-R01", "--chat-review",
    ]))
    handoff = stage_cli.parser().parse_args(["handoff", "--project", str(tmp_path)])
    assert stage_cli.handoff(handoff)["next_actions"] == []

    approval = chat_args(tmp_path, source)
    stage_cli.confirm_script_in_chat(approval)
    result = stage_cli.handoff(handoff)
    actions = {item["action"]: item for item in result["next_actions"]}
    assert set(actions) == ({"assets", route_action} if route_action else {"assets"})
    assert actions["assets"]["output_ids"] == [target]
    assert len({item["context_id"] for item in actions.values()}) == len(actions)
    for action in actions.values():
        assert action["artifact_ids"] == ["N-R01"]
        assert approval.sha256 in Path(action["handoff_path"]).read_text(encoding="utf-8")
    if workflow == "pending":
        [director] = [step for step in result["steps"] if step["action"] == "director"]
        assert director["status"] == "waiting"


@pytest.mark.parametrize("blocker, reason", [
    ("no_grant", "缺有效导演流程授权"),
    ("wrong_scope", "缺有效导演流程授权"),
    ("revoked", "缺有效导演流程授权"),
    ("legacy_conflicting_stage", "下一阶段不一致"),
    ("changed_bytes", "准确文件字节"),
    ("active_revision", "动作触及冻结分支"),
])
def test_initial_chat_handoff_requires_existing_scope_and_current_script(tmp_path, kickoff_review_site, blocker, reason):
    from scripts.core.project_state import write_project_state

    source, _ = narrative(tmp_path)
    stage_cli.confirm_script_in_chat(chat_args(tmp_path, source))
    if blocker != "no_grant":
        stage_cli.register_workflow_authorization(tmp_path, authorization_id="FLOW-R01",
            authorization_ref="synthetic prior user authorization",
            skills=["voiceover-to-video-script" if blocker == "wrong_scope" else "storyboard-planner"])
    state = artifacts.load_project_state(tmp_path)
    if blocker == "revoked":
        state["workflow_authorizations"][0]["status"] = "revoked"
    if blocker == "legacy_conflicting_stage":
        state["next_stage"] = "storyboard-director"
        request = tmp_path / REQUEST_PATH
        write_json(request, {**json.loads(request.read_text(encoding="utf-8")),
                             "local_references": [stage_cli.LEGACY_RULE]})
    if blocker == "changed_bytes":
        source.write_bytes(source.read_bytes() + b" ")
    write_project_state(tmp_path, state)
    if blocker == "active_revision":
        rc.open_change(tmp_path, change_id="CHAT-REVISION", tier="T1", logical_ids=["narrative.package"], reason="synthetic")

    handoff = stage_cli.parser().parse_args(["handoff", "--project", str(tmp_path), "--to-skill", "storyboard-planner"])
    with pytest.raises(stage_cli.StageContextError, match=reason):
        stage_cli.handoff(handoff)
    assert artifacts.load_project_state(tmp_path) == state
    assert not stage_cli._handoff_path(tmp_path).exists()


def test_chat_approval_rejects_partial_scope_and_same_revision_byte_drift(tmp_path, kickoff_review_site):
    source, package = narrative(tmp_path)
    args = chat_args(tmp_path, source)
    message_path = Path(args.confirmation_json)
    original = message_path.read_bytes()
    write_json(message_path, {**json.loads(original), "scope": "segment"})
    with pytest.raises(ValueError, match="整稿"):
        stage_cli.confirm_script_in_chat(args)

    message_path.write_bytes(original)
    package["story_paragraphs"][0]["text"] += "审阅后改写。"
    write_json(source, package)
    with pytest.raises(ValueError, match="文件字节"):
        stage_cli.confirm_script_in_chat(args)
    assert not artifacts.load_manifest(tmp_path)["artifacts"]
    assert not artifacts.load_project_state(tmp_path)["user_confirmations"]


@pytest.mark.parametrize("numbered", [False, True])
@pytest.mark.parametrize("line_ending", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_chat_whole_script_consumes_disclosed_hard_subtitles_without_a_second_decision(
    tmp_path, kickoff_review_site, monkeypatch, capsys, numbered, line_ending,
):
    import sys
    from scripts.core import story_sources
    from test_dialogue_origins import bind, fixture

    record = fixture(tmp_path, isolated=False)
    record['dialogues'].pop(1)
    package = bind(tmp_path, record)
    package['open_questions'] = []
    source = tmp_path / 'R01.json'
    write_json(source, package)
    args = chat_args(tmp_path, source)
    kickoff = (tmp_path / REQUEST_PATH).read_bytes()
    with pytest.raises(ValueError, match='未核对白证据'):
        stage_cli.confirm_script_in_chat(args)

    reading = tmp_path / 'R01.md'
    monkeypatch.setattr(sys, 'argv', ['story_sources', '--project', str(tmp_path), '--narrative', str(source),
                                      '--review-ready', '--chat-review', '--export-markdown', str(reading)])
    story_sources.main()
    review = json.loads(capsys.readouterr().out.splitlines()[0])
    ids = review['awaiting_subtitle_approval_ids']
    assert ids == ['DLG-01', 'DLG-02']
    expected = reading.read_text()
    if numbered:
        expected = numbered_reading(expected, package)
    message = Path(args.confirmation_json)
    approval = json.loads(message.read_text())

    def bind_reading(text):
        reading.write_bytes(text.replace('\n', line_ending).encode('utf-8'))
        write_json(message, {**approval, 'review_markdown': {'path': reading.name,
                                                            'sha256': artifacts.digest(reading)}})

    bind_reading(expected.replace('甲句。', '改了原文。', 1))
    with pytest.raises(ValueError, match='阅读正文'):
        stage_cli.confirm_script_in_chat(args)
    bind_reading(expected.replace('"schema_version": "2.0"', '"schema_version": "3.0"', 1))
    with pytest.raises(ValueError, match='技术附录'):
        stage_cli.confirm_script_in_chat(args)
    bind_reading(expected.replace('DLG-01、DLG-02', 'DLG-01'))
    with pytest.raises(ValueError, match='未核对白证据.*DLG-02'):
        stage_cli.confirm_script_in_chat(args)
    assert not artifacts.load_project_state(tmp_path)['user_confirmations']
    # The historical export kept an environment table between story and technical appendix.
    bind_reading(expected.replace('## 完整结构化来源', '## 环境资产需求\n\n| 样例环境 |\n\n## 完整结构化来源'))
    adopted = stage_cli.confirm_script_in_chat(args)
    assert adopted['confirmation']['subtitle_review_ids'] == ids
    assert adopted['confirmation']['review_markdown']['sha256'] == artifacts.digest(reading)
    assert (tmp_path / REQUEST_PATH).read_bytes() == kickoff
    assert story_sources.validate_dialogue_review(record, tmp_path)['unresolved_ids'] == ids
    story_sources.validate_story_sources(package, project_root=tmp_path, narrative_path=source,
                                         require_dialogue_evidence=True)
    assert stage_cli.confirm_script_in_chat(args)['already_current']
    other_ending = '\n' if line_ending == '\r\n' else '\r\n'
    reading.write_bytes(reading.read_text().replace('\n', other_ending).encode('utf-8'))
    with pytest.raises(ValueError, match='审阅时的文件字节'):
        stage_cli.confirm_script_in_chat(args)


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
            if site.head and site.on_status:
                site.on_status()
            head = site.head or {"version": None, "headGeneration": 0}
            return {"case": {"id": query["caseId"], "projectId": "fixture", "name": "合成案例",
                             "status": "active", "revision": head["headGeneration"]},
                    "stage": "assets", "sourceScriptVersion": site.status_source or f"{1:064x}",
                    "head": {"version": head["version"], "generation": head["headGeneration"]} if site.head else None,
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
    from scripts import chat_asset_review as review
    image = project / "candidate.png"
    source = next((e for e in artifacts.load_manifest(project)["artifacts"]
                   if e["logical_id"] == "narrative.package" and e["status"] == "current"), None)
    shown = (review.store_shown(project, source, [dict(target=ast + "@" + output_id,
             candidate_id=f"IMAGE-{output_id}", candidate_revision="R01", path=image.name,
             sha256=artifacts.digest(image), purpose="合成选图用途")])["shown"] if source else "unused")
    args = ["publish-asset", "--shown", shown, "--project", str(project), "--script-publication", script_publication,
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
    with pytest.raises(ValueError, match=r"AST-LEAD@HOME-FINAL.*横向拼版"):
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
    cli.run(publish_asset_args(tmp_path, "final.json", ast="AST-LEAD", output_id="HOME-FINAL"))
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
    with pytest.raises(ValueError, match="当前有效且已采用"):
        cli.run(publish_asset_args(tmp_path, "unadopted.json"))
    website_decision(tmp_path, "script-approved.json")

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(source.read_bytes())
    cli.run(cli._parser().parse_args(["prepare", "--project", str(tmp_path), "--narrative", duplicate.name,
                                      "--output", "duplicate.md"]))
    args = publish_args(tmp_path, "duplicate-publish.json")
    save_preflight(tmp_path, status_response(version=f"{1:064x}", generation=1))
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
    saved = json.loads((tmp_path / "first.json").read_text())
    assert saved["response"] == first["response"]
    assert saved["request"]["parameters"]["artifactId"] == asset_review_site.posts[0]["artifactId"] == "IMAGE-paper"

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


def test_unbound_old_site_operation_cannot_claim_another_same_byte_candidate(tmp_path, asset_review_site):
    publish_asset_script(tmp_path)
    cli.run(publish_asset_args(tmp_path, "first.json"))
    original = json.loads((tmp_path / "first.json").read_text())
    cli.submission_path(tmp_path, original).unlink()  # workspace predating local operation bindings
    with pytest.raises(cli.AgentReviewError, match="操作结果"):
        cli.run(publish_asset_args(tmp_path, "retry.json", output_id="other"))
    assert json.loads((tmp_path / "retry.json").read_text())["response"] is None
    result = cli.run(publish_asset_args(tmp_path, "fresh.json", output_id="other", operation=OPERATION[:-1] + "9"))
    assert result["response"]["assetId"] != original["response"]["assetId"]


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
        "user_approved": False})
    with pytest.raises(ValueError):
        stage_cli.main(["adopt-package", "--project", str(tmp_path), "--artifact-id", "N-R01",
                        "--logical-id", "narrative.package", "--path", source.name, "--revision", "R01",
                        "--approval-ref", pending])
    with pytest.raises(ValueError):
        artifacts.adopt_initial_package(tmp_path, "N-R01", pending)
    assert not artifacts.load_project_state(tmp_path)["user_confirmations"]
    handoff = stage_cli.parser().parse_args(["handoff", "--project", str(tmp_path), "--to-skill", "storyboard-planner"])
    with pytest.raises(stage_cli.StageContextError, match="等待准确版本的编导整稿确认及正式采用"):
        stage_cli.handoff(handoff)

    kickoff_review_site("approved")
    website_decision(tmp_path, "approved.json")
    [entry] = artifacts.load_manifest(tmp_path)["artifacts"]
    assert (entry["status"], entry["user_approved"], entry["checksum"]) == ("current", True, artifacts.digest(source))
    assert website_decision(tmp_path, "approved-reread.json")["adoption"]["already_current"] is True
    before = artifacts.load_project_state(tmp_path)
    assert before["next_stage"] is None and not before["workflow_authorizations"]
    kickoff_review_site("approved", lambda: pytest.fail("handoff must reuse the saved decision"))
    bad_decision = json.loads((tmp_path / "approved.json").read_text())
    bad_decision["response"]["approval"]["scope"]["version"] = "other-version"
    original_decision = (tmp_path / "approved.json").read_bytes()
    write_json(tmp_path / "approved.json", bad_decision)
    with pytest.raises(stage_cli.StageContextError, match="缺有效导演流程授权"):
        stage_cli.handoff(handoff)
    (tmp_path / "approved.json").write_bytes(original_decision)
    assert stage_cli.handoff(handoff)["artifact_ids"] == ["N-R01"]
    assert artifacts.load_project_state(tmp_path) == before
    assert stage_cli.status(handoff)["next_allowed_stage"] == "storyboard-planner"


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
    save_preflight(tmp_path, status_response(version=f"{1:064x}", generation=1))
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
    posts = [call for call in calls if call[0] == "publish"]
    assert [name for name, _ in calls] == ["status", "publish", "status", "publish"]
    assert posts[0] == posts[1]
    assert posts[0][1] == {"case_id": "012", "stage": "script", "name": "R01.md",
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
    assert len(calls) == 4


@pytest.mark.parametrize("change", ["case", "project", "head", "review_revision"])
def test_publish_refuses_stale_or_mismatched_preflight_before_post(tmp_path, fake_client, change):
    calls, _ = fake_client
    narrative(tmp_path)
    prepare(tmp_path)
    args = publish_args(tmp_path)
    saved = json.loads((tmp_path / "preflight.json").read_text())
    if change == "case":
        args.case_id = "013"
    elif change == "project":
        saved["response"]["case"]["projectId"] = "another-project"
    elif change == "head":
        saved["response"] = status_response(version=f"{1:064x}", generation=1)
    else:
        saved["response"]["expectations"]["expectedReviewRevision"] += 1
    write_json(tmp_path / "preflight.json", saved)
    with pytest.raises(ValueError, match="不符|已变化"):
        cli.run(args)
    assert [name for name, _ in calls] == ([] if change == "case" else ["status"])
    assert not (tmp_path / "publish.json").exists()


def test_asset_publication_refuses_a_moved_head_before_post(tmp_path, asset_review_site):
    publish_asset_script(tmp_path)
    cli.run(publish_asset_args(tmp_path, "first.json"))
    cli.run(cli._parser().parse_args(["status", "--project", str(tmp_path),
        "--case-id", "012", "--stage", "assets", "--token-file", "/not-read-by-mock",
        "--receipt", "asset-preflight.json"]))
    args = publish_asset_args(tmp_path, "second.json", operation=OPERATION[:-1] + "7")
    args.preflight = Path("asset-preflight.json")
    assert cli.run(args)["response"]["assetId"] == "asset-2"

    args = publish_asset_args(tmp_path, "stale.json", operation=OPERATION[:-1] + "8")
    args.preflight = Path("asset-preflight.json")
    with pytest.raises(ValueError, match="当前版本或审核状态已变化"):
        cli.run(args)
    assert len(asset_review_site.posts) == 2
    assert not (tmp_path / "stale.json").exists()


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
    assert json.loads((tmp_path / "status.json").read_text())["response"] == status_response()


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
    assert calls[1][1]["operation_id"] == args.operation_id
    story, appendix = calls[1][1]["text"].split("## 完整结构化来源（只读技术附录）", 1)
    assert "## STP-01" not in story
    assert '"paragraph_id": "STP-01"' in appendix
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
    assert not (tmp_path / "publish.json").exists()


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
    save_preflight(tmp_path, status_response())
    args = ["publish", "--project", str(tmp_path), "--narrative", "R01.json", "--markdown", "R01.md",
            "--case-id", "012", "--operation-id", OPERATION, "--token-file", "/not-read-by-mock",
            "--preflight", "preflight.json", "--receipt", "failed.json"]
    with pytest.raises(SystemExit) as stopped:
        cli.main(args)
    assert stopped.value.code == 2 and [name for name, _ in calls] == ["status", "publish"]
    receipt = json.loads((tmp_path / "failed.json").read_text())
    assert receipt["response"] is None
    assert receipt["error"] == {"code": "TRANSPORT_ERROR", "http_status": None}
    assert "SECRET" not in capsys.readouterr().err + json.dumps(receipt)


def test_chat_delivery_registers_disclosed_candidate_and_consumes_first_decision(tmp_path, kickoff_review_site):
    from test_dialogue_origins import bind, fixture

    record = fixture(tmp_path, isolated=False)
    record['dialogues'].pop(1)
    record['open_items'] = ['电话处有一声未上字幕的短音']
    package = bind(tmp_path, record)
    source = tmp_path / 'R01.json'
    args = cli._parser().parse_args(['prepare', '--project', str(tmp_path), '--narrative', source.name,
                                     '--output', 'review.md', '--artifact-id', 'N-R01', '--chat-review'])
    package['open_questions'] = ['人物关系尚未确定']
    write_json(source, package)
    with pytest.raises(ValueError, match='原声疑点'):
        cli.run(args)
    assert not artifacts.load_manifest(tmp_path)['artifacts']
    package['open_questions'] = record['open_items']
    write_json(source, package)
    receipt = cli.run(args)
    assert receipt == cli.run(args)
    assert artifacts.load_manifest(tmp_path)['artifacts'][0]['status'] == 'candidate'
    assert record['open_items'][0] in (tmp_path / 'review.md').read_text().split('## 完整结构化来源')[0]
    result = stage_cli.confirm_script_in_chat(chat_args(tmp_path, source))
    assert result['artifact']['status'] == 'current'
    assert result['confirmation']['subtitle_review_ids'] == ['DLG-01', 'DLG-02']


def test_chat_candidate_edit_reuses_source_reading_and_directory(tmp_path, kickoff_review_site, capsys):
    from scripts.core.project_state import new_project_state, write_project_state

    write_project_state(tmp_path, new_project_state(current_stage="creative-director"))
    source, package = narrative(tmp_path)
    command = ["prepare", "--project", str(tmp_path), "--narrative", source.name, "--chat-review"]

    def deliver():
        assert cli.main([*command, "--output", "R01.md", "--artifact-id", "N-R01"]) == 0
        return json.loads(capsys.readouterr().out)

    def paths():
        return {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    first = deliver()
    old_confirmation = chat_args(tmp_path, source)
    baseline = paths()
    for line in ("补一处场景动作。", "再调一句旁白。"):
        package["story_paragraphs"][0]["text"] += line
        write_json(source, package)
        result = deliver()
        assert result["source_json"]["revision"] == "R01"
        assert result["source_json"]["sha256"] != first["source_json"]["sha256"]
        assert line in (tmp_path / "R01.md").read_text(encoding="utf-8")
        assert result == deliver()
        assert paths() == baseline

    for ident, output, issue in [
        ("N-R01", "copy/R01.md", "沿用原阅读稿路径"),
        ("N-other", "copy/R02.md", "已有本轮编导候选"),
    ]:
        with pytest.raises(ValueError, match=issue):
            cli.run(cli._parser().parse_args([*command, "--output", output, "--artifact-id", ident]))
        assert paths() == baseline

    handoff = stage_cli.handoff(stage_cli.parser().parse_args(["handoff", "--project", str(tmp_path)]))
    assert any(step["action"] == "creative-review" and step["artifact_ids"] == ["N-R01"]
               for step in handoff["steps"])
    assert paths() == baseline
    with pytest.raises(ValueError, match="字节|SHA-256"):
        stage_cli.confirm_script_in_chat(old_confirmation)
    assert stage_cli.confirm_script_in_chat(chat_args(tmp_path, source))["artifact"]["status"] == "current"


def test_website_candidate_reuses_reading_before_publication_and_preserves_published_copy(tmp_path, fake_client):
    source, package = narrative(tmp_path)
    args = cli._parser().parse_args(["prepare", "--project", str(tmp_path), "--narrative", source.name,
                                     "--output", "R01.md", "--artifact-id", "N-R01"])
    cli.run(args)
    baseline = set(tmp_path.rglob("*"))
    package["story_paragraphs"][0]["text"] += "发布前修改同一候选。"
    write_json(source, package)
    refreshed = cli.run(args)
    assert refreshed == cli.run(args)
    assert baseline == set(tmp_path.rglob("*"))
    published_source = source.read_bytes()
    published_reading = (tmp_path / "R01.md").read_bytes()
    cli.run(publish_args(tmp_path))
    package["story_paragraphs"][0]["text"] += "发布后另有修改意见。"
    write_json(source, package)
    with pytest.raises(ValueError, match="核对审核渠道和原回执"):
        cli.run(args)
    assert (tmp_path / "R01.md").read_bytes() == published_reading
    source.write_bytes(published_source)
    assert cli.run(args) == refreshed


def test_replace_returned_candidate_keeps_user_edits_and_blocks_late_approval(tmp_path, kickoff_review_site):
    from scripts.core.project_state import new_project_state, write_project_state

    write_project_state(tmp_path, new_project_state(current_stage="creative-director"))
    old, package = narrative(tmp_path)
    command = ["prepare", "--project", str(tmp_path), "--chat-review"]
    def deliver(revision, *extra):
        return cli.run(cli._parser().parse_args([*command, "--narrative", revision + ".json", "--output",
                                                revision + ".md", "--artifact-id", "N-" + revision, *extra]))
    deliver("R01")
    original = (tmp_path / "R01.md").read_bytes()
    edited = original + "用户手改一句".encode()
    (tmp_path / "R01.md").write_bytes(edited)
    request = tmp_path / "revise.json"
    write_json(request, {"revise_candidate_id": "N-R01", "brief": "保留手改，删广告尾段"})
    stage = stage_cli.parser().parse_args(["prepare", "--project", str(tmp_path), "--skill", "creative-director",
                                         "--request-json", str(request), "--objective", "返修待审稿"])
    assert stage_cli.prepare(stage)["valid"]
    handoff = stage_cli.handoff(stage_cli.parser().parse_args(["handoff", "--project", str(tmp_path)]))
    step, = handoff["next_actions"]
    assert step["skill"] == "creative-director" and step["revision_candidate_ids"] == ["N-R01"]
    assert stage_cli.prepare(stage)["valid"]
    with pytest.raises(ValueError, match="已收到返修"):
        stage_cli.confirm_script_in_chat(chat_args(tmp_path, old))
    package["revision"] = "R02"
    write_json(tmp_path / "R02.json", package)
    with pytest.raises(ValueError, match="--replaces"):
        deliver("R02")
    write_json(tmp_path / "R02.json", {**package, "project_id": "UNRELATED-PROJECT"})
    pending_manifest = artifacts.load_manifest(tmp_path)
    with pytest.raises(ValueError, match="项目身份"):
        deliver("R02", "--replaces", "N-R01")
    assert artifacts.load_manifest(tmp_path) == pending_manifest
    assert not (tmp_path / "R02.md").exists() and not (tmp_path / "99_历史与废弃").exists()
    write_json(tmp_path / "R02.json", package)
    first = deliver("R02", "--replaces", "N-R01")
    assert first == deliver("R02", "--replaces", "N-R01")
    assert first["artifact_id"] == "N-R02"
    assert first["replaces"] == "N-R01"
    assert first["revision_changes"] == {"other_fields": ["revision"]}
    previous, current = artifacts.load_manifest(tmp_path)["artifacts"]
    assert previous["status"] == "superseded" and current["status"] == "candidate"
    assert previous["superseded_by"] == current["artifact_id"] and current["replaces"] == previous["artifact_id"]
    assert (tmp_path / previous["reading_copy"]["path"]).read_bytes() == original
    assert (tmp_path / previous["user_edited_reading"]["path"]).read_bytes() == edited
    assert (tmp_path / "R01.md").read_bytes() == edited
    write_json(tmp_path / "R03.json", {**package, "revision": "R03"})
    with pytest.raises(ValueError, match="不再是待审"):
        deliver("R03", "--replaces", "N-R01")
    assert not (tmp_path / "R03.md").exists()
    assert stage_cli.confirm_script_in_chat(chat_args(tmp_path, tmp_path / "R02.json", "R02"))["artifact"]["status"] == "current"


def test_returned_script_delta_lists_changed_content_and_reordering():
    before = dict(story_paragraphs=[{'paragraph_id': key, 'text': key} for key in ['A', 'B', 'C']],
                  dialogues=[], asset_requirements=[], open_questions=[])
    after = dict(before, story_paragraphs=[{'paragraph_id': 'B', 'text': '新'},
                                            {'paragraph_id': 'D', 'text': '增'}, before['story_paragraphs'][0]],
                 open_questions=['疑点'])
    assert cli._revision_changes(before, after) == {
        'story_paragraphs': {'added': ['D'], 'removed': ['C'], 'updated': ['B'], 'reordered': True},
        'other_fields': ['open_questions'],
    }

    legacy = {'story_paragraphs': [{'text': 'old'}]}
    assert cli._revision_changes(legacy, before) == {
        'other_fields': ['asset_requirements', 'dialogues', 'open_questions', 'story_paragraphs']}
