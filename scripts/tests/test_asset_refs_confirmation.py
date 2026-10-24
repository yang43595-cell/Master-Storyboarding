import json
import tempfile
import unittest
from pathlib import Path

from scripts.tests.test_visual_asset_projects import fixture, plan_result

from core import asset_refs, revision_control
import visual_asset_projects as pmod


class AssetReferenceConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.lib, self.project, self.asset = fixture(Path(self.temp.name))
        service = self.lib.projects
        saved = service.get(self.project["id"])
        for row in saved["rows"]:
            row["reference_ids"] = [self.asset["id"]]
        service.save(saved)
        service.apply_plan(saved, saved["rows"], plan_result(), {"mode": "project"})
        for row in service.detail(self.project["id"])["rows"]:
            job = row["job"]
            for index in range(len(job["steps"])):
                if job["steps"][index]["kind"] == "物品":
                    path = self.lib.root / f"object-{index}.png"
                    from PIL import Image
                    Image.new("RGB", (1600, 900), "blue").save(path)
                    selected, _ = self.lib.import_file(path, category="物品")
                else:
                    selected = self.asset
                self.lib.projects.select({"job_id": job["id"], "index": index, "asset_id": selected["id"]})
                self.lib.projects.confirm({"job_id": job["id"], "index": index, "confirm": True})
        self.lib.vault = Path(self.temp.name) / "vault"
        self.lib.vault.mkdir(exist_ok=True)
        from unittest.mock import patch
        import visual_asset_library as library
        with patch.object(library.management, "guard"):
            library.visual_asset_backup.run(self.lib, library.management)
        self.export = self.lib.projects.export({"project_id": self.project["id"], "confirm": True})
        self.root = Path(self.project["path"])
        self.manifest_path = Path(self.export["manifest"])

    def tearDown(self):
        self.temp.cleanup()

    def _legacy_manifest_without_checksum(self):
        # These compatibility tests exercise old manifests without rewriting a
        # new byte-bound export and pretending it is still the adopted version.
        path = self.root / "00_项目管理/artifact_manifest.json"
        registered = pmod.read(path)
        next(a for a in registered["artifacts"]
             if a["artifact_id"] == self.export["artifact_id"]).pop("checksum", None)
        pmod.write(path, registered)

    def _write_decisions(self, decisions):
        path = self.root / asset_refs.OUTPUT_DECISIONS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": "asset-output-applicability-1.0",
            "decisions": decisions,
        }, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _decision(asset_id, output_id, status="rejected", decision_id="DEC-1"):
        return {
            "decision_id": decision_id,
            "asset_id": asset_id,
            "output_id": output_id,
            "status": status,
            "scope": "project",
            "reason": "用户判定该输出不适用于当前项目",
            "user_instruction": "不要再使用这张图",
            "decided_at": "2099-09-20T00:00:00Z",
            "evidence_refs": ["review.md#decision"],
        }

    def test_only_explicit_outputs_are_published_not_candidates(self):
        self._legacy_manifest_without_checksum()
        manifest = pmod.read(self.manifest_path)
        asset = manifest["assets"][0]
        asset["candidates"].append({**asset["candidates"][0], "path": str(self.root / "unconfirmed.png")})
        pmod.write(self.manifest_path, manifest)

        images = asset_refs.confirmed_images(self.root)

        published = images[asset["asset_id"]]
        self.assertNotIn(str(self.root / "unconfirmed.png"), [item["path"] for item in published["outputs"]])
        self.assertEqual({item["output_id"] for item in published["outputs"]},
                         {item["output_id"] for item in asset["confirmed_outputs"]})

    def test_old_cached_list_is_not_reused_and_current_narrative_is_resolved_from_manifest(self):
        stale = {"items": [{"asset_id": "AST-ROOM", "state": "needs_image",
                            "image": {"status": "confirmed", "files": ["candidate.png"]}}]}
        (self.root / asset_refs.ASSET_LIST).parent.mkdir(exist_ok=True)
        (self.root / asset_refs.ASSET_LIST).write_text(json.dumps(stale), encoding="utf-8")

        current = asset_refs.load_asset_list(self.root)

        room = asset_refs.asset_index(current)["AST-ROOM"]
        self.assertIn("outputs", room["image"])
        self.assertNotIn("candidate.png", room["image"]["files"])

    def test_adopting_changed_narrative_invalidates_previously_confirmed_images(self):
        narrative = asset_refs.current_narrative(self.root)
        narrative["revision"] = "R02"
        cup = next(item for item in narrative["asset_requirements"] if item["asset_id"] == "AST-CUP")
        cup.update(name="红色杯子", identity_constraints=["红色杯子"])
        candidate = self.root / "narrative-r02.json"
        pmod.write(candidate, narrative)

        revision_control.open_change(self.root, change_id="REV-CUP", tier="T1",
            logical_ids=["narrative.package"], reason="蓝色杯子改为红色杯子")
        revision_control.confirm_impact(self.root, change_id="REV-CUP", approval_ref="test:user-impact")
        revision_control.record_candidate(self.root, change_id="REV-CUP", artifact_id="narrative-r02",
            logical_id="narrative.package", artifact_type="narrative-package",
            candidate_path=str(candidate), revision="R02")
        revision_control.record_review(self.root, change_id="REV-CUP", verdict="approved", report_ref="test:review")
        with self.assertRaisesRegex(revision_control.RevisionControlError, 'story_paragraphs'):
            revision_control.confirm_candidate(self.root, change_id="REV-CUP", approval_ref="test:user-adopt")

        registered = asset_refs.load_manifest(self.root)
        narrative_candidate = next(item for item in registered["artifacts"] if item["artifact_id"] == "narrative-r02")
        self.assertEqual(narrative_candidate["status"], "candidate")
        self.assertEqual(next(item for item in registered["artifacts"] if item["artifact_id"] == "narrative-r01")["status"], "current")
        visual = next(item for item in registered["artifacts"] if item["artifact_id"] == self.export["artifact_id"])
        self.assertEqual(visual["status"], "current")
        self.assertIn(visual["artifact_id"], asset_refs.load_project_state(self.root)["current_artifacts"])
        current = asset_refs.asset_index(asset_refs.load_asset_list(self.root))["AST-CUP"]
        self.assertEqual(current["name"], "蓝色杯子")
        self.assertTrue(asset_refs.confirmed_images(self.root))

    def test_registered_non_current_manifests_cannot_publish_outputs(self):
        path = self.root / "00_项目管理/artifact_manifest.json"
        registered = pmod.read(path)
        visual = next(item for item in registered["artifacts"] if item["artifact_id"] == self.export["artifact_id"])
        for status in ("stale", "superseded", "rejected", "candidate"):
            with self.subTest(status=status):
                visual["status"] = status
                pmod.write(path, registered)
                self.assertEqual(asset_refs.confirmed_images(self.root), {})

    def test_newer_registered_stale_manifest_does_not_revive_older_current_image(self):
        original = pmod.read(self.manifest_path)
        asset = original["assets"][0]
        folder = self.root / "04_资产/母版/R99_stale"
        folder.mkdir(parents=True)
        newer_path = folder / "visual-asset-manifest.json"
        pmod.write(newer_path, {"assets": [asset]})
        path = self.root / "00_项目管理/artifact_manifest.json"
        registered = pmod.read(path)
        registered["artifacts"].append({
            "artifact_id": "visual-assets-stale", "logical_id": "visual.asset.manifest",
            "type": "visual-asset-manifest", "status": "stale", "user_approved": True,
            "path_or_url": str(newer_path.relative_to(self.root)),
        })
        pmod.write(path, registered)

        images = asset_refs.confirmed_images(self.root)

        self.assertNotIn(asset["asset_id"], images)
        self.assertIn(original["assets"][1]["asset_id"], images)

    def test_unadopted_manifests_do_not_hide_the_current_confirmed_output(self):
        original = pmod.read(self.manifest_path)
        asset = original["assets"][0]
        expected = asset_refs.confirmed_images(self.root)[asset["asset_id"]]
        newer = self.root / "04_资产/母版/R99_pending/visual-asset-manifest.json"
        pmod.write(newer, {"assets": [asset]})
        registered_path = self.root / "00_项目管理/artifact_manifest.json"
        registered = pmod.read(registered_path)
        entry = {"artifact_id": "unadopted-r99", "logical_id": "visual.asset.manifest",
                 "type": "visual-asset-manifest", "status": "candidate", "user_approved": False,
                 "path_or_url": newer.relative_to(self.root).as_posix()}
        registered["artifacts"].append(entry)
        request = asset["asset_id"] + "@" + asset["confirmed_outputs"][0]["output_id"]
        for status in ("candidate", "rejected", "superseded"):
            with self.subTest(status=status):
                entry["status"] = status
                pmod.write(registered_path, registered)
                self.assertEqual(asset_refs.confirmed_images(self.root)[asset["asset_id"]], expected)
                self.assertEqual(asset_refs.require_images(self.root, [request]), (True, []))
        newer.write_text("unfinished candidate", encoding="utf-8")
        self.assertEqual(asset_refs.confirmed_images(self.root)[asset["asset_id"]], expected)
        # A current replacement with missing confirmation must still block;
        # ignoring candidate history is not permission to revive invalid inputs.
        pmod.write(newer, {"assets": [asset]})
        entry["status"] = "current"
        pmod.write(registered_path, registered)
        self.assertFalse(asset_refs.require_images(self.root, [request])[0])

    def test_legacy_candidate_assets_do_not_replace_confirmed_outputs(self):
        asset = pmod.read(self.manifest_path)["assets"][0]
        expected = asset_refs.confirmed_images(self.root)[asset["asset_id"]]
        newer = self.root / "04_资产/母版/R99_legacy/visual-asset-manifest.json"
        for status in ("candidate", "awaiting_user_review", "rejected"):
            with self.subTest(status=status):
                pmod.write(newer, {"assets": [{**asset, "status": status, "confirmed_asset": None}]})
                self.assertEqual(asset_refs.confirmed_images(self.root)[asset["asset_id"]], expected)

    def test_relocated_current_paths_keep_scan_revision_floor_and_hits(self):
        from unittest.mock import patch
        asset = pmod.read(self.manifest_path)["assets"][0]
        registered_path = self.root / "00_项目管理/artifact_manifest.json"
        registered = pmod.read(registered_path)
        folder = self.root / "03_导演"
        folder.mkdir()
        for revision in ("R03", "R04", "R05"):
            (folder / f"{revision}_导演稿.md").write_text("资产：" + asset["asset_id"], encoding="utf-8")
        entry = {"artifact_id": "board-r04", "logical_id": "storyboard.plan", "status": "current"}
        registered["artifacts"].append(entry)
        source_root = r"C:\Users\Owner\Desktop\film"
        expected = {"03_导演/R04_导演稿.md", "03_导演/R05_导演稿.md"}
        for raw in (r"03_导演\R04_导演稿.md", source_root + r"\03_导演\R04_导演稿.md"):
            with self.subTest(path=raw), patch.dict("os.environ", {
                    "MSB_PROJECT_PATH_MAP": json.dumps({source_root: str(self.root)})}):
                entry["path_or_url"] = raw
                pmod.write(registered_path, registered)
                report = asset_refs.scan_references(self.root, [asset["asset_id"]])
                hits = {Path(hit["file"]).as_posix() for hit in report["assets"][0]["reference_hits"]}
                self.assertEqual(hits, expected)

    def test_registered_current_manifest_requires_current_whole_confirmation(self):
        state_path = self.root / "00_项目管理/project_state.json"
        state = pmod.read(state_path)
        original = json.loads(json.dumps(state))
        state["current_artifacts"].remove(self.export["artifact_id"])
        pmod.write(state_path, state)
        self.assertEqual(asset_refs.confirmed_images(self.root), {})

        state = original
        for receipt in state["user_confirmations"]:
            if self.export["artifact_id"] in receipt.get("artifact_ids", []):
                receipt.update(scope="segment", paragraph_ids=["STP-01"])
        pmod.write(state_path, state)
        self.assertEqual(asset_refs.confirmed_images(self.root), {})

    def test_bare_ast_checks_availability_but_specific_output_must_exist(self):
        self._legacy_manifest_without_checksum()
        manifest = pmod.read(self.manifest_path)
        asset = manifest["assets"][0]
        asset["confirmed_asset"] = None
        pmod.write(self.manifest_path, manifest)

        ok, problems = asset_refs.require_images(self.root, [asset["asset_id"]])
        self.assertTrue(ok)
        self.assertEqual(problems, [])
        ok, problems = asset_refs.require_images(self.root, [asset["asset_id"] + "@not-confirmed"])
        self.assertFalse(ok)
        self.assertIn("未找到已确认输出", problems[0])

    def test_replacement_record_does_not_confirm_other_assets(self):
        folder = self.root / "04_资产/母版/R99_replacement"
        folder.mkdir(parents=True)
        frame = self.root / "frame.png"
        frame.write_bytes(b"frame")
        receipt = folder / "参考替换记录.json"
        receipt.write_text(json.dumps({"at": "2099-09-20T00:00:00Z", "frame_path": str(frame)}), encoding="utf-8")
        data = {"assets": [
            {"asset_id": "AST-ROOM", "status": "confirmed", "confirmed_asset": {"path": str(frame)},
             "confirmed_outputs": [{"output_id": "master", "path": str(frame), "user_confirmation_ref": str(receipt)}]},
            {"asset_id": "AST-CUP", "status": "confirmed", "confirmed_asset": None,
             "candidates": [{"path": str(frame)}], "confirmed_outputs": []},
        ]}
        (folder / "visual-asset-manifest.json").write_text(json.dumps(data), encoding="utf-8")

        images = asset_refs.confirmed_images(self.root)

        self.assertIn("AST-ROOM", images)
        # An explicit empty output group is newer evidence of a current gap.  It
        # must not retain an older image or inherit this replacement frame.
        self.assertNotIn("AST-CUP", images)

    def test_legacy_main_requires_its_own_candidate_and_receipt(self):
        base = pmod.read(self.manifest_path)
        asset = base["assets"][0]
        legacy = {key: value for key, value in asset.items() if key != "confirmed_outputs"}
        folder = self.root / "04_资产/母版/R99_legacy"
        folder.mkdir(parents=True)
        legacy["status"] = "confirmed"
        legacy["confirmed_asset"] = dict(asset["confirmed_asset"])
        legacy["candidates"] = [dict(asset["candidates"][0])]
        legacy["confirmed_asset"]["candidate_id"] = legacy["candidates"][0]["candidate_id"]
        legacy["confirmed_asset"]["path"] = legacy["candidates"][0]["path"]
        legacy["confirmed_asset"]["user_confirmation_ref"] = asset["confirmed_outputs"][0]["user_confirmation_ref"]
        (folder / "visual-asset-manifest.json").write_text(json.dumps({"assets": [legacy]}), encoding="utf-8")

        image = asset_refs.confirmed_images(self.root)[asset["asset_id"]]

        self.assertEqual(image["selected_path"], str(Path(legacy["confirmed_asset"]["path"])))
        self.assertEqual(image["outputs"][0]["output_id"], "legacy-main")

    def test_unknown_receipt_fragment_does_not_confirm_output(self):
        self._legacy_manifest_without_checksum()
        manifest = pmod.read(self.manifest_path)
        asset = manifest["assets"][0]
        output = asset["confirmed_outputs"][0]
        receipt = self.root / "unknown-receipt.json"
        receipt.write_text(json.dumps({"unrelated": True}), encoding="utf-8")
        output["user_confirmation_ref"] = str(receipt) + "#not-a-confirmation"
        pmod.write(self.manifest_path, manifest)

        images = asset_refs.confirmed_images(self.root)

        self.assertNotIn(output["output_id"], {
            item["output_id"] for item in images[asset["asset_id"]]["outputs"]
        })

    def test_scope_returns_only_requested_output_and_problem(self):
        manifest = pmod.read(self.manifest_path)
        asset = manifest["assets"][0]
        output = asset["confirmed_outputs"][0]

        entries, problems = asset_refs.scope_assets(
            self.root, [asset["asset_id"] + "@" + output["output_id"], "AST-NOT-REGISTERED"])

        self.assertEqual([item["asset_id"] for item in entries], [asset["asset_id"]])
        self.assertEqual([item["output_id"] for item in entries[0]["outputs"]], [output["output_id"]])
        self.assertEqual(problems, ["AST-NOT-REGISTERED：不在当前资产清单中"])

    def test_exact_rejection_blocks_scope_and_submission_but_not_sibling_output(self):
        manifest = pmod.read(self.manifest_path)
        asset = manifest["assets"][0]
        rejected, sibling = asset["confirmed_outputs"][:2]
        manifest_before = self.manifest_path.read_bytes()
        receipt_path = Path(rejected["user_confirmation_ref"].partition("#")[0])
        receipt_before = receipt_path.read_bytes()
        self._write_decisions([self._decision(asset["asset_id"], rejected["output_id"])])

        entries, problems = asset_refs.scope_assets(
            self.root, [asset["asset_id"] + "@" + rejected["output_id"]])
        rejected_output = entries[0]["outputs"][0]
        self.assertEqual(rejected_output["applicability"]["status"], "unavailable")
        self.assertIn("明确拒用", problems[0])
        ok, problems = asset_refs.require_images(
            self.root, [asset["asset_id"] + "@" + rejected["output_id"]])
        self.assertFalse(ok)
        self.assertIn("用户判定该输出不适用于当前项目", problems[0])

        ok, problems = asset_refs.require_images(
            self.root, [asset["asset_id"] + "@" + sibling["output_id"]])
        self.assertTrue(ok)
        self.assertEqual(problems, [])
        self.assertEqual(self.manifest_path.read_bytes(), manifest_before)
        self.assertEqual(receipt_path.read_bytes(), receipt_before)

    def test_bare_asset_fails_when_every_confirmed_output_is_rejected(self):
        manifest = pmod.read(self.manifest_path)
        asset = manifest["assets"][0]
        self._write_decisions([
            self._decision(
                asset["asset_id"], output["output_id"], decision_id=f"DEC-{index}"
            )
            for index, output in enumerate(asset["confirmed_outputs"], start=1)
        ])

        ok, problems = asset_refs.require_images(self.root, [asset["asset_id"]])
        entries, scope_problems = asset_refs.scope_assets(self.root, [asset["asset_id"]])

        self.assertFalse(ok)
        self.assertIn("没有当前项目可用的确认输出", problems[0])
        self.assertEqual(entries[0]["image_status"], "unavailable")
        self.assertEqual(entries[0]["outputs"], [])
        self.assertEqual(
            len(entries[0]["unavailable_outputs"]), len(asset["confirmed_outputs"])
        )
        self.assertEqual(scope_problems, [])

        rejected = asset["confirmed_outputs"][0]
        ok, problems = asset_refs.require_images(
            self.root, [asset["asset_id"] + "@" + rejected["output_id"]]
        )
        self.assertFalse(ok)
        self.assertIn("明确拒用", problems[0])
        self.assertIn("用户判定该输出不适用于当前项目", problems[0])

    def test_bare_asset_stays_available_when_a_sibling_output_remains(self):
        manifest = pmod.read(self.manifest_path)
        asset = manifest["assets"][0]
        rejected = asset["confirmed_outputs"][0]
        self._write_decisions([
            self._decision(asset["asset_id"], rejected["output_id"])
        ])

        ok, problems = asset_refs.require_images(self.root, [asset["asset_id"]])
        entries, scope_problems = asset_refs.scope_assets(self.root, [asset["asset_id"]])

        self.assertTrue(ok)
        self.assertEqual(problems, [])
        self.assertEqual(entries[0]["image_status"], "confirmed")
        self.assertGreater(len(entries[0]["outputs"]), 0)
        self.assertEqual(len(entries[0]["unavailable_outputs"]), 1)
        self.assertEqual(scope_problems, [])

    def test_ambiguous_decision_is_reported_without_rejecting_every_output(self):
        manifest = pmod.read(self.manifest_path)
        asset = manifest["assets"][0]
        output = asset["confirmed_outputs"][0]
        ambiguous = self._decision(asset["asset_id"], output["output_id"])
        ambiguous.pop("output_id")
        self._write_decisions([ambiguous])

        ok, problems = asset_refs.require_images(
            self.root, [asset["asset_id"] + "@" + output["output_id"]])

        self.assertFalse(ok)
        self.assertIn("范围不明确", problems[0])
        images = asset_refs.confirmed_images(self.root)
        self.assertEqual(
            images[asset["asset_id"]]["outputs"][0]["applicability"]["status"],
            "available",
        )

    def test_later_explicit_adoption_restores_only_the_same_output(self):
        manifest = pmod.read(self.manifest_path)
        asset = manifest["assets"][0]
        output = asset["confirmed_outputs"][0]
        rejected = self._decision(asset["asset_id"], output["output_id"])
        adopted = self._decision(
            asset["asset_id"], output["output_id"], status="adopted", decision_id="DEC-2")
        adopted["reason"] = "用户复核后明确重新采用该输出"
        adopted["user_instruction"] = "这张图重新采用"
        self._write_decisions([rejected, adopted])

        ok, problems = asset_refs.require_images(
            self.root, [asset["asset_id"] + "@" + output["output_id"]])

        self.assertTrue(ok)
        self.assertEqual(problems, [])
        selected = asset_refs.confirmed_images(self.root)[asset["asset_id"]]["outputs"][0]
        self.assertEqual(selected["applicability"]["decision_id"], "DEC-2")

    def test_image_handoff_respects_explicit_rework_freeze(self):
        self.assertTrue(asset_refs.require_images(self.root, ['AST-CUP'])[0])
        revision_control.open_change(self.root, change_id='IMAGE-REWORK', tier='T1',
            logical_ids=['visual.asset.manifest'], reason='explicit fixture rework')
        ok, problems = asset_refs.require_images(self.root, ['AST-CUP'])
        self.assertFalse(ok)
        self.assertIn('冻结', problems[0])
        revision_control.cancel_change(self.root, change_id='IMAGE-REWORK', reason='fixture cancelled')
        self.assertTrue(asset_refs.require_images(self.root, ['AST-CUP'])[0])
