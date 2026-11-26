import json
from pathlib import Path

import pytest
from PIL import Image

from scripts import chat_asset_review as review
from scripts.core import artifacts
from scripts.core.io import read_json, sha256_json, write_json
from test_review_text_cli import narrative


def test_incremental_confirmation_checks_bytes_and_does_not_reactivate_earlier_batch(tmp_path):
    project = tmp_path.resolve()
    path, story = narrative(project)
    requirement = story["asset_requirements"][0]
    requirement["image_decision"] = "需要制作"
    write_json(path, story)
    upstream = dict(
        artifact_id="story", checksum=artifacts.digest(path), revision="R01", path_or_url=path.name
    )
    keys = []
    for output in ("PAPER-A", "PAPER-B"):
        image = project / (output + ".png")
        Image.new("RGB", (5, 5)).save(image)
        item = dict(
            target="AST-PROP-PAPER@" + output,
            path=image.name,
            sha256=artifacts.digest(image),
            purpose="纸张外观",
            output_kind="道具",
            candidate_id=output,
            visual_status="failed",
            issue="边角裁切",
            requirement_sha256=sha256_json(
                review.effective_requirements(project, upstream)["AST-PROP-PAPER"]
            ),
        )
        packet = dict(narrative=upstream["checksum"], narrative_id="story", items=[item])
        keys.append(sha256_json(packet))
        write_json(project / review.SHOWN / (keys[-1] + ".json"), packet)

    def confirm(key, ref):
        entries = artifacts.load_manifest(project)["artifacts"]
        message = dict(at="2026-11-22T12:00:00+08:00", quote="全部通过", scope="all_shown", review_ref=ref, shown=[key])
        return review.confirm(project, [key], message, upstream, entries)

    first = confirm(keys[0], "synthetic-1")
    with pytest.raises(ValueError, match="同一条用户消息"):
        confirm(keys[1], "synthetic-1")
    original = image.read_bytes()
    image.write_bytes(original + b"changed")
    with pytest.raises(ValueError, match="字节已变化"):
        confirm(keys[1], "synthetic-2")
    image.write_bytes(original)
    second = confirm(keys[1], "synthetic-2")
    assert confirm(keys[0], "synthetic-1") == first
    assert artifacts.load_project_state(project)["current_artifacts"] == [second["artifact_id"]]
    asset = read_json(Path(second["manifest"]))["assets"][0]
    assert [o["output_id"] for o in asset["confirmed_outputs"]] == ["PAPER-A", "PAPER-B"]
    assert all(
        review.asset_refs._confirmed_output(project, asset["asset_id"], o) for o in asset["confirmed_outputs"]
    )


    old_image, replacement = [review.asset_refs._confirmed_output(project, asset["asset_id"], output)
                              for output in asset["confirmed_outputs"]]
    removed = review.discard(project, {
        "target": "AST-PROP-PAPER@PAPER-A", "path": old_image["path"], "sha256": old_image["sha256"],
        "replacement": "AST-PROP-PAPER@PAPER-B", "quote": "删除 A，用 B 替换",
        "at": "2026-11-25T12:00:00+08:00", "review_ref": "synthetic-discard",
    }, upstream, artifacts.load_manifest(project)["artifacts"])
    assert removed["segment_action"] == "rebind"
    assert removed["replacement_image"] == {field: replacement[field] for field in ("path", "sha256")}

    candidate = {**item, "target": "AST-PROP-PAPER@PAPER-C", "candidate_id": "PAPER-C"}
    packet = {**packet, "items": [candidate]}
    key = sha256_json(packet)
    write_json(project / review.SHOWN / (key + ".json"), packet)
    third = confirm(key, "synthetic-3")
    outputs = read_json(Path(third["manifest"]))["assets"][0]["confirmed_outputs"]
    assert [output["output_id"] for output in outputs] == ["PAPER-B", "PAPER-C"]
    assert review.asset_refs._confirmed_output(project, asset["asset_id"], outputs[0]) == replacement


def round_project(root, outputs=("A", "B")):
    path, story = narrative(root)
    story["asset_requirements"][0]["image_decision"] = "需要制作"
    write_json(path, story)
    upstream = dict(artifact_id="story", checksum=artifacts.digest(path), revision="R01", path_or_url=path.name)
    targets = ["AST-PROP-PAPER@" + output for output in outputs]
    write_json(root / review.stage.REQUEST_PATH, {"asset_review_targets": targets})
    return upstream, targets


def image_batch(root, narrative, targets, name, *, direct=False, missing=()):
    request = root / (name + "-request.json")
    receipt = root / (name + "-receipt.json")
    payload = dict(image_targets=targets, image_output_kinds={t: "道具" for t in targets})
    write_json(request, payload)
    digest = artifacts.digest(request)
    context = None if direct else name
    folder = review.stage._context_path(root, context)
    basis = dict(skill="asset-image-assistant", context_id=context, request_path=str(request),
                 reading_manifest={"project_path": str(root)}, task_request=payload,
                 input_snapshot={"artifacts": [{"record": {"artifact_id": narrative["artifact_id"]},
                                                "file": {"sha256": narrative["checksum"]}}]})
    write_json(folder / "task_basis.json", basis)
    if not direct:
        binding = dict(context_id=context, request=request.name, request_sha256=digest,
                       targets=targets, receipt=receipt.name)
        card = review.stage._ASSET_BATCH_BINDING_PREFIX + json.dumps(binding) + "`\n"
        (folder / "next_stage_handoff.md").write_text(card)
    items = []
    for i, target in enumerate(targets):
        if target in missing:
            continue
        image = root / (target.split("@")[1] + "-" + name + ".png")
        Image.new("RGB", (5, 5), (i, len(name), 1)).save(image)
        items.append(dict(target=target, path=image.name, sha256=artifacts.digest(image),
                          purpose="纸张外观", observer=name, candidate_id=target.split("@")[1], candidate_revision="R01",
                          visual_status="passed", observations="纸张画面完整"))
    write_json(receipt, dict(request_sha256=digest, context_id=context, items=items))
    return receipt


def decision(root, key, upstream, reference, **update):
    message = dict(at="2026-11-22T12:00:00+08:00", quote="全部通过", scope="all_shown", review_ref=reference, shown=[key])
    return review.confirm(root, [key], {**message, **update}, upstream, artifacts.load_manifest(root)["artifacts"])


def test_show_round_across_delegated_and_direct_batches(tmp_path):
    root = tmp_path.resolve()
    upstream, targets = round_project(root, list("ABCDEFGHI"))
    image_batch(root, upstream, targets[:8], "worker")
    direct = image_batch(root, upstream, targets[8:], "direct", direct=True)
    pending = review.show_round(root, ["worker"], upstream)
    assert [r["target"] for r in pending["pending"]] == targets[8:]
    assert not (root / review.SHOWN).exists()
    shown = review.show_round(root, ["worker"], upstream, receipts=[direct])
    assert [r["target"] for r in shown["items"]] == targets
    assert len(list((root / review.SHOWN).glob("*.json"))) == 1
    write_json(root / review.stage.REQUEST_PATH, {"asset_review_targets": targets[:8]})
    with pytest.raises(ValueError, match="目标已变化"):
        decision(root, shown["shown"], upstream, "test")
    write_json(root / review.stage.REQUEST_PATH, {"asset_review_targets": targets})
    recorded = decision(root, shown["shown"], upstream, "test")
    assert recorded["count"] == 9
    assert decision(root, shown["shown"], upstream, "test") == recorded
    assert review.show_round(root, ["worker"], upstream, receipts=[direct])["status"] == "already_confirmed"


def test_show_round_repair_carries_forward_only_approved_versions(tmp_path):
    root = tmp_path.resolve()
    upstream, (a, b) = round_project(root)
    image_batch(root, upstream, [a, b], "pair")
    shown = review.show_round(root, ["pair"], upstream)
    with pytest.raises(ValueError, match="已展示"):
        decision(root, shown["shown"], upstream, "partial", scope="selected", targets=[a + "-unknown"])
    result = decision(root, shown["shown"], upstream, "partial", scope="selected", targets=[a], quote="除了 B 其余通过")
    assert result["count"] == 1
    with pytest.raises(ValueError, match="同一条用户消息"):
        decision(root, shown["shown"], upstream, "partial")
    image_batch(root, upstream, [b], "retry")
    image_batch(root, upstream, [b], "pending", missing=[b])
    replacement = review.show_round(root, ["pending", "retry"], upstream)
    assert [r["target"] for r in replacement["items"]] == [b]
    # The old mixed batch may contribute its remaining candidate to a narrower next round.
    write_json(root / review.stage.REQUEST_PATH, {"asset_review_targets": [b]})
    assert [r["target"] for r in review.show_round(root, ["pair"], upstream)["items"]] == [b]
    with pytest.raises(ValueError, match="多份待审版本"):
        review.show_round(root, ["pair", "retry"], upstream)
    write_json(root / review.stage.REQUEST_PATH, {"asset_review_targets": [a, b]})
    adopted = decision(root, replacement["shown"], upstream, "repaired")
    assert adopted["count"] == 1
    assert {o["output_id"] for o in read_json(Path(adopted["manifest"]))["assets"][0]["confirmed_outputs"]} == {"A", "B"}


def test_show_round_rejects_omissions_and_wrong_bindings(tmp_path):
    root = tmp_path.resolve()
    upstream, targets = round_project(root)
    write_json(root / "small.json", {"image_targets": targets[:1]})
    with pytest.raises(ValueError, match="不一致"):
        review.show_round(root, [], upstream, "small.json")
    story_path = root / upstream["path_or_url"]
    story = read_json(story_path)
    story["asset_requirements"].append({**story["asset_requirements"][0], "asset_id": "AST-PROP-EXTRA"})
    write_json(story_path, story)
    upstream.update(checksum=artifacts.digest(story_path))
    with pytest.raises(ValueError, match="遗漏.*AST-PROP-EXTRA"):
        review.show_round(root, [], upstream)
    story["asset_requirements"].pop()
    write_json(story_path, story)
    upstream.update(checksum=artifacts.digest(story_path))
    direct = image_batch(root, upstream, targets, "local", direct=True)
    receipt = read_json(direct)
    digest = receipt["request_sha256"]
    index = root / review.stage.CONTEXT_DIR / "直做图片请求" / (digest + ".json")
    binding = dict(skill="asset-image-assistant", request="local-request.json", request_sha256=digest,
                   narrative_id=upstream["artifact_id"], narrative_sha256=upstream["checksum"], targets=targets,
                   image_output_kinds={t:"道具" for t in targets})
    write_json(index, binding)
    assert review.show_round(root, [], upstream, receipts=[direct])["shown"]
    write_json(index, {**binding, "narrative_sha256": "0" * 64})
    with pytest.raises(ValueError, match="冻结的项目"):
        review.show_round(root, [], upstream, receipts=[direct])


def test_return_preserves_candidate_and_feedback_without_adopting(tmp_path):
    root = tmp_path.resolve()
    upstream, targets = round_project(root, ["PAPER"])
    image_batch(root, upstream, targets, "first")
    shown = review.show_round(root, ["first"], upstream)
    message = dict(at="2026-11-22T12:00:00+08:00", quote="纸张换成蓝色", scope="all_shown", review_ref="return-1", shown=[shown["shown"]])
    result = review.return_for_revision(root, [shown["shown"]], message, upstream)
    assert read_json(root / result["record"])["feedback"] == message
    candidate = result["items"][0]
    assert artifacts.digest(root / candidate["path"]) == candidate["sha256"]
    assert not review.asset_refs.confirmed_images(root)


def test_return_and_new_approval_through_cli_supply_director_shot(tmp_path, monkeypatch, capsys):
    import sys
    from test_formal_package_submission import board_candidate, narrative as formal_story

    root = tmp_path.resolve()
    story = formal_story(root)
    requirement = story["asset_requirements"][0]
    requirement.update(image_decision="需要制作", image_reason="本镜使用人物单图",
                       character_output_kind="人物单图", character_type="HUMAN")
    write_json(root / "N.json", story)

    def adopt(ident, logical, path, *dependencies):
        review.stage.main(["adopt-package", "--project", str(root), "--artifact-id", ident,
                           "--logical-id", logical, "--path", path, "--revision", read_json(root / path)["revision"],
                           "--approval-ref", "synthetic:" + ident, *dependencies])
        capsys.readouterr()

    adopt("N", "narrative.package", "N.json")
    upstream = review.current(artifacts.load_manifest(root)["artifacts"], "narrative.package")
    ast = requirement["asset_id"]
    target = ast + "@MASTER"
    write_json(root / review.stage.REQUEST_PATH, {"asset_review_targets": [target]})

    def cli(*args):
        monkeypatch.setattr(sys, "argv", ["chat_asset_review", "--project", str(root), *args])
        review.main()
        return json.loads(capsys.readouterr().out)

    def show(version):
        receipt = image_batch(root, upstream, [target], version, direct=True)
        row = read_json(receipt)
        row["items"][0].update(candidate_id=version, output_kind="人物单图")
        write_json(receipt, row)
        return cli("show-round", "--receipt", str(receipt))

    first = show("A")
    rejected = {"at": "2026-11-26T08:00:00+08:00", "quote": "换成蓝色", "scope": "all_shown",
                "review_ref": "synthetic:return", "message_id": "msg-1", "shown": [first["shown"]]}
    write_json(root / "return.json", rejected)
    args = ["return", "--shown", first["shown"], "--feedback-json", str(root / "return.json")]
    saved = cli(*args)
    assert cli(*args) == saved
    previous = (root / first["items"][0]["path"]).read_bytes()
    second = show("version-B")
    assert second["shown"] != first["shown"]
    approve = ["confirm", "--shown", second["shown"], "--confirmation-json", str(root / "approve.json")]
    write_json(root / "approve.json", rejected)
    with pytest.raises(ValueError, match="展示编号"):
        cli(*approve)
    write_json(root / "approve.json", {**rejected, "shown": [second["shown"]], "review_ref": "synthetic:other-ref"})
    with pytest.raises(ValueError, match="已用于退回"):
        cli(*approve)
    assert not review.asset_refs.confirmed_images(root)
    accepted = {**rejected, "review_ref": "synthetic:approve", "message_id": "msg-2",
                "quote": "新版通过", "shown": [second["shown"]]}
    write_json(root / "approve.json", accepted)
    export = cli(*approve)
    assert cli(*approve) == export
    with pytest.raises(ValueError, match="其他交审范围"):
        cli("return", "--shown", second["shown"], "--feedback-json", str(root / "approve.json"))
    assert (root / first["items"][0]["path"]).read_bytes() == previous
    assert read_json(root / saved["record"])["feedback"] == rejected

    # The five version fields are compared through the ordinary display command.
    receipt = root / "version-B-receipt.json"
    assert cli("show-round", "--receipt", str(receipt))["status"] == "already_confirmed"
    batch = read_json(receipt)
    batch["items"][0]["source_revision"] = "R01"
    for revision in ("R02", None):
        batch["items"][0]["candidate_revision"] = revision
        write_json(receipt, batch)
        fresh = cli("show-round", "--receipt", str(receipt))
        assert fresh["shown"] != second["shown"]
        assert fresh["items"][0]["candidate_revision"] == revision
        assert review.asset_refs.resolve_final_images(root, [target])[target]["source_candidate_revision"] == "R01"

    board = board_candidate(root, story)
    write_json(root / "director.json", board)
    adopt("DIRECTOR", "storyboard.package", "director.json", "--depends-on", "narrative.package",
          "--depends-on", review.LOGICAL)
    entries = artifacts.load_manifest(root)["artifacts"]
    director = review.current(entries, "storyboard.package")
    assert review.LOGICAL in director["depends_on"]
    shot = read_json(root / director["path_or_url"])["scenes"][0]["shots"][0]
    assert ast in shot["source_asset_requirement_ids"]
    selected = review.asset_refs.resolve_final_images(root, [target])[target]
    assert selected["source_candidate_id"] == "version-B"
    assert Path(selected["path"]).read_bytes() == (root / second["items"][0]["path"]).read_bytes()


def test_another_candidate_with_identical_bytes_needs_its_own_confirmation(tmp_path):
    root = tmp_path.resolve()
    upstream, targets = round_project(root, ["PAPER"])
    receipt = image_batch(root, upstream, targets, "first")
    shown = review.show_round(root, ["first"], upstream)
    decision(root, shown["shown"], upstream, "first-approval")
    assert review.show_round(root, ["first"], upstream)["status"] == "already_confirmed"
    updated = read_json(receipt)
    updated["items"][0]["candidate_id"] = "second-version"
    write_json(receipt, updated)
    next_version = review.show_round(root, ["first"], upstream)
    assert next_version["items"][0]["candidate_id"] == "second-version"
    assert next_version["shown"] != shown["shown"]


def test_rejected_slot_in_earlier_batch_cannot_return_to_review(tmp_path):
    root = tmp_path.resolve()
    upstream, (layout, replacement) = round_project(root, ["OLD-LAYOUT", "NEW"])
    image_batch(root, upstream, [layout, replacement], "studio")
    earlier = review.show_round(root, ["studio"], upstream)
    record = dict(asset_id="AST-PROP-PAPER", output_id="OLD-LAYOUT", decision_id="decision-1",
                  status="rejected", scope="project", reason="只能参考布局", user_instruction="这张不要作正式资产",
                  decided_at="2026-11-22T12:00:00+08:00", evidence_refs=["synthetic:decision-1"])
    write_json(root / review.asset_refs.OUTPUT_DECISIONS,
               dict(schema_version="asset-output-applicability-1.0", decisions=[record]))
    with pytest.raises(ValueError, match="已排除"):
        decision(root, earlier["shown"], upstream, "after-rejection")
    with pytest.raises(ValueError, match="已排除"):
        review.show_round(root, ["studio"], upstream)
    write_json(root / review.stage.REQUEST_PATH, {"asset_review_targets": [replacement]})
    fresh = review.show_round(root, ["studio"], upstream)
    assert [r["target"] for r in fresh["items"]] == [replacement]


def test_show_round_covers_each_declared_character_outfit(tmp_path):
    root = tmp_path.resolve()
    upstream, _ = round_project(root)
    path = root / upstream["path_or_url"]
    story = read_json(path)
    ast = "AST-CHAR-HOST"
    story["asset_requirements"][0].update(
        asset_id=ast, category="CHARACTER", character_type="HUMAN", character_output_kind=review.boards.BOARD,
        wardrobe_states=[dict(name=outfit, evidence="synthetic", output_id=outfit) for outfit in ("DAY", "NIGHT")])
    write_json(path, story)
    upstream.update(checksum=artifacts.digest(path))
    head, day, night = [ast + "@" + output for output in ("HEAD", "JACKET", "COAT")]

    def batch(name, slots):
        receipt = image_batch(root, upstream, [slot[0] for slot in slots], name)
        packet = read_json(receipt)
        for row, (_, kind, final) in zip(packet["items"], slots, strict=True):
            row.update(output_kind=kind, final_output_id=final)
        write_json(receipt, packet)

    batch("first", [(head, review.boards.HEAD, None), (day, "服装", "DAY")])
    write_json(root / review.stage.REQUEST_PATH, {"asset_review_targets": [head, day]})
    result = review.show_round(root, ["first"], upstream)
    assert [r["target"] for r in result["pending"]] == [ast + "@NIGHT"]
    assert not (root / review.SHOWN).exists()
    batch("second", [(night, "服装", "NIGHT")])
    write_json(root / review.stage.REQUEST_PATH, {"asset_review_targets": [head, day, night]})
    key = review.show_round(root, ["first", "second"], upstream)["shown"]
    decision(root, key, upstream, "head and one outfit", scope="selected", targets=[head, day], quote="头照和日常装通过")
    assert [r["target"] for r in review.show_round(root, ["second"], upstream)["items"]] == [night]
    existing = {ast: {"required_board_ids": ["DAY", "NIGHT"], "image": {"outputs": [
        {"output_id": outfit, "output_kind": review.boards.BOARD} for outfit in ("DAY", "NIGHT")]}}}
    assert not review.boards.round_board_source_gaps(root, existing, [dict(target=head, output_kind=review.boards.HEAD)])


def test_new_return_of_legacy_confirmation_in_mixed_project(tmp_path):
    root = tmp_path.resolve()
    upstream, targets = round_project(root, ["A", "KEEP"])
    batch = image_batch(root, upstream, targets, "batch")
    shown = review.show_round(root, ["batch"], upstream)["shown"]
    items = read_json(batch)["items"]
    ast = targets[0].split("@")[0]
    confirmations, outputs = [], []
    for row in items:
        output = row["target"].split("@")[1]
        evidence = dict(id=output, asset_id=row["candidate_id"], image_sha256=row["sha256"])
        if output == "KEEP":
            evidence.update(source_candidate=row["path"], candidate_revision=row["candidate_revision"])
        confirmations.append(dict(ast_id=ast, output_id=output, confirmation=evidence))
        outputs.append(dict(output_id=output, path=row["path"], purpose=row["purpose"],
                            candidate_id="export-" + output, user_confirmation_ref="approval.json#" + output))
    approval = root / "approval.json"
    other = "AST-PROP-OTHER"
    confirmations.append({**confirmations[0], "ast_id": other})
    write_json(approval, {"items": confirmations})
    manifest = root / review.asset_refs.MASTER_DIR / "old" / "visual-asset-manifest.json"
    package = {"assets": [dict(asset_id=ast, status="confirmed", confirmed_outputs=outputs),
                          dict(asset_id=other, status="confirmed", confirmed_outputs=[dict(outputs[0])])]}
    write_json(manifest, package)
    before = {p: p.read_bytes() for p in [approval, manifest, *(root / r["path"] for r in items)]}

    def available():
        images = review.asset_refs.confirmed_images(root)
        assert images[other]["outputs"][0]["confirmation_ref"] == "approval.json#A"
        return [o["output_id"] for o in images[ast]["outputs"]]

    assert available() == ["A", "KEEP"]
    feedback = dict(scope="selected", targets=targets[:1], at="2026-11-23T08:00:00Z",
                    quote="退回 A", review_ref="return-a", shown=[shown])
    result = review.return_for_revision(root, [shown], feedback, upstream)
    record = root / result["record"]
    saved = read_json(record)
    assert saved["revoked_confirmations"] == ["approval.json#A"]
    assert saved["feedback"] == feedback and available() == ["KEEP"]
    write_json(record, {**saved, "revoked_confirmations": []})
    assert available() == ["KEEP"]
    assert all(p.read_bytes() == raw for p, raw in before.items())

    # A later confirmation of B can even reuse A's bytes on the same output.
    replacement = {"items": [dict(ast_id=ast, output_id="A", confirmation=dict(
        id="B", asset_id="B", image_sha256=items[0]["sha256"]))]}
    write_json(root / "replacement.json", replacement)
    outputs[0]["user_confirmation_ref"] = "replacement.json#B"
    write_json(manifest, package)
    assert review.return_for_revision(root, [shown], feedback, upstream)["record"] == result["record"]
    assert available() == ["A", "KEEP"]
    assert read_json(record)["feedback"] == feedback and approval.read_bytes() == before[approval]


@pytest.mark.parametrize(("second_confirmed", "first_slot", "revision"), [
    ("B", "legacy-main", "R01"), ("C", "legacy-main", "R01"),
    ("C", "main", "R01"), ("B", "legacy-main", "R02"),
])
def test_batch_return_preserves_original_candidate_identity_in_legacy_main(tmp_path, second_confirmed, first_slot, revision):
    root = tmp_path.resolve()
    upstream, targets = round_project(root, [first_slot])
    paper = "AST-PROP-PAPER"
    box = "AST-PROP-BOX"
    unknown = "AST-PROP-UNKNOWN"
    targets.append(box + "@legacy-main")
    story_path = root / upstream["path_or_url"]
    story = read_json(story_path)
    for asset_id in (box, unknown):
        story["asset_requirements"].append({**story["asset_requirements"][0], "asset_id": asset_id, "name": asset_id})
    write_json(story_path, story)
    upstream["checksum"] = artifacts.digest(story_path)
    review.stage.main(["adopt-package", "--project", str(root), "--artifact-id", upstream["artifact_id"],
                       "--logical-id", "narrative.package", "--path", upstream["path_or_url"],
                       "--revision", "R01", "--approval-ref", "synthetic:approved-story"])
    write_json(root / review.stage.REQUEST_PATH, {"asset_review_targets": targets})
    batch = image_batch(root, upstream, targets, "batch")
    packet = read_json(batch)
    for candidate, color in [("A", "red"), ("B", "blue"), ("C", "green")]:
        Image.new("RGB", (5, 5), color).save(root / (candidate + ".png"))
    for row, candidate in zip(packet["items"], ("A", "B")):
        row.update(candidate_id=candidate, path=candidate + ".png", sha256=artifacts.digest(root / (candidate + ".png")))
    write_json(batch, packet)
    shown = review.show(root, "batch", upstream)["shown"]
    ref = "approval.json#approved"
    approvals = []
    assets = []
    for ast, candidate in [(paper, "A"), (box, second_confirmed), (unknown, "C")]:
        path = candidate + ".png"
        evidence = dict(id="approved", image_sha256=artifacts.digest(root / path))
        if ast == box:
            evidence.update(candidate_id=candidate, source_candidate=path, candidate_revision=revision)
        item = dict(ast_id=ast, output_id=first_slot if ast == paper else "legacy-main", confirmation=evidence)
        if ast == paper:
            if first_slot == "legacy-main":
                evidence["asset_id"] = candidate  # Per-step receipt from the old workbench.
            else:
                item["library_asset_id"] = candidate  # Library selection on the exact export item.
        approvals.append(item)
        selected = dict(candidate_id=ast + "-C01", path=path, user_confirmation_ref=ref)
        asset = dict(asset_id=ast, status="confirmed", confirmed_asset=selected, candidates=[dict(selected)])
        if ast == box:
            asset["confirmed_outputs"] = [{**selected, "output_id": "legacy-main"}]
        assets.append(asset)
    manifest = root / review.asset_refs.MASTER_DIR / "old" / "visual-asset-manifest.json"
    write_json(manifest, {"assets": assets})
    approval = root / "approval.json"
    write_json(approval, {"items": approvals})
    preserved = {p: p.read_bytes() for p in [manifest, approval, *(root / (c + ".png") for c in "ABC")]}

    refs = review.asset_refs
    images = refs.confirmed_images(root)
    assert set(images) == {paper, box}
    assert images[paper]["outputs"][0]["source_candidate_id"] == "A"
    assert images[paper]["outputs"][0]["candidate_id"] == paper + "-C01"
    formal = [paper + "@legacy-main", targets[1]]
    assert refs.resolve_final_images(root, formal)[formal[1]]["source_candidate_id"] == second_confirmed
    assert not refs.require_final_images(root, [unknown + "@legacy-main"])[0]

    feedback = dict(scope="all_shown", at="2026-11-26T12:00:00Z", quote="退回 A 和 B",
                    review_ref="return-ab", shown=[shown])
    result = review.return_for_revision(root, [shown], feedback, upstream)
    record = root / result["record"]
    original = record.read_bytes()
    assert review.return_for_revision(root, [shown], feedback, upstream) == result
    assert record.read_bytes() == original and read_json(record)["feedback"] == feedback
    returned_second = second_confirmed == "B" and revision == "R01"
    assert read_json(record)["revoked_confirmations"] == ([ref, ref] if returned_second else [ref])
    for pointers in (read_json(record)["revoked_confirmations"], []):
        write_json(record, {**read_json(record), "revoked_confirmations": pointers})
        assert set(refs.confirmed_images(root)) == (set() if returned_second else {box})
        assert not refs.require_final_images(root, formal[:1])[0]
        if not returned_second:
            selected = refs.resolve_final_images(root, targets[1:])[targets[1]]
            assert (selected["path"], selected["source_candidate_revision"]) == (str(root / (second_confirmed + ".png")), revision)
    assert all(p.read_bytes() == raw for p, raw in preserved.items())


def test_withdrawn_revision_and_earlier_display_cannot_resume_delivery(tmp_path):
    root = tmp_path.resolve()
    upstream, targets = round_project(root, ["PAPER"])
    review.stage.main(["adopt-package", "--project", str(root), "--artifact-id", upstream["artifact_id"],
                       "--logical-id", "narrative.package", "--path", upstream["path_or_url"],
                       "--revision", "R01", "--approval-ref", "synthetic script approval"])
    receipt = image_batch(root, upstream, targets, "batch")
    shown = review.show_round(root, ["batch"], upstream)["shown"]
    decision(root, shown, upstream, "approve-a", message_id="a")
    feedback = dict(scope="all_shown", at="2026-11-23T08:00:00Z", quote="退回", review_ref="feedback", shown=[shown])
    result = review.return_for_revision(root, [shown], feedback, upstream)
    record = root / result["record"]
    saved = read_json(record)
    assert saved.pop("revoked_confirmations")
    with pytest.raises(ValueError):
        review.asset_refs.resolve_final_images(root, targets)
    write_json(record, saved)  # historical return predating explicit withdrawal pointers
    with pytest.raises(ValueError):
        review.asset_refs.resolve_final_images(root, targets)
    batch = read_json(receipt)
    batch["items"][0]["candidate_revision"] = "R02"
    write_json(receipt, batch)
    newer = review.show_round(root, ["batch"], upstream)["shown"]
    decision(root, newer, upstream, "approve-b", message_id="b")
    with pytest.raises(ValueError):
        decision(root, shown, upstream, "approve-old", message_id="old")
    review.return_for_revision(root, [shown], feedback, upstream)
    assert review.asset_refs.resolve_final_images(root, targets)[targets[0]]["source_candidate_revision"] == "R02"
    with pytest.raises(ValueError, match="消息 ID"):
        review.prior_channel_decision(root, {**feedback, "review_ref": "changed"}, [], "approved", from_web=True)
