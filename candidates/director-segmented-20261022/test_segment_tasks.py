"""Synthetic structural tests only; no director creation or model calls."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("segment_tasks", HERE / "segment_tasks.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class SegmentTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / "run"
        self.state = {key: "synthetic boundary" for key in tool.STATE_KEYS}
        self.source = {"project_id": "SYNTHETIC", "revision": "R01", "story_paragraphs": [
            {"paragraph_id": "STP-01", "text": "synthetic A"},
            {"paragraph_id": "STP-02", "text": "synthetic B"}],
            "dialogues": [{"dialogue_id": "DLG-01", "speaker_id": "CHAR-A", "text": "fixture"}]}
        self.base = {"schema_version": "4.0", "project_id": "SYNTHETIC", "revision": "R02", "status": "candidate",
                     "source_narrative": {"artifact_id": "ART-01", "logical_id": "narrative.package", "revision": "R01", "path": "source.json"},
                     "source_paragraph_ids": ["STP-01", "STP-02"], "source_dialogue_ids": ["DLG-01"],
                     "source_asset_requirement_ids": [], "director_rules": {"editing_rhythm": "synthetic"},
                     "scenes": [{"scene_id": "S01", "shots": [], "audiovisual_flow": "fixture"}],
                     "asset_gaps": [], "open_questions": [],
                     "timing_basis": {"status": "not_evaluable", "source_type": None, "source_path": None, "source_field": None}}
        self.plan = {"source_revision": "R01", "source_artifact_id": "ART-01", "director_rules": self.base["director_rules"],
                     "narrative_purpose": "synthetic", "information_emotion_route": "synthetic", "style": "synthetic",
                     "timing_status": "estimate", "total_budget_seconds": 4, "initial_state": self.state,
                     "segments": [{"segment_id": "SEG-01", "scene_id": "S01", "source_paragraph_ids": ["STP-01"],
                                   "source_dialogue_ids": ["DLG-01"], "budget_seconds": 2, "target_state": self.state},
                                  {"segment_id": "SEG-02", "scene_id": "S01", "source_paragraph_ids": ["STP-02"],
                                   "source_dialogue_ids": [], "budget_seconds": 2, "target_state": self.state}]}
        self.source_path = self.root / "source.json"
        self.base_path = self.root / "base.json"
        self.plan_path = self.root / "plan.json"
        self.prepare()

    def prepare(self):
        for path, value in ((self.source_path, self.source), (self.base_path, self.base), (self.plan_path, self.plan)):
            path.write_text(json.dumps(value), encoding="utf-8")

    def init(self):
        tool.init(self.run, self.source_path, self.plan_path, self.base_path)

    def result(self, sid):
        ref = tool.read(self.run / "manifest.json")["tasks"][sid]
        task = tool.read(self.run / ref["path"])
        first = sid == "SEG-01"
        dialogue = [{"dialogue_id": "DLG-01", "speaker_id": "CHAR-A", "text": "fixture", "presentation": "SYNC"}] if first else []
        return {"production_blocked": True, "segment_id": sid, "source_revision": "R01", "scene_id": "S01",
                "task_sha256": ref["sha256"], "predecessor_sha256": task["predecessor_sha256"],
                "start_state": task["start_state"], "end_state": deepcopy(self.state), "boundary_review": "synthetic structural fixture",
                "audiovisual_flow": "synthetic flow", "chosen_option_id": "only-route",
                "options": [{"id": "only-route", "hard_conflicts": [], "evidence": "synthetic evidence", "tradeoffs": "not measured"}],
                "shots": [{"shot_id": "S01-001" if first else "S01-002", "source_paragraph_ids": ["STP-01" if first else "STP-02"],
                           "source_dialogue_ids": ["DLG-01"] if first else [], "source_asset_requirement_ids": [],
                           "audio": {"dialogue": dialogue, "sfx": []}}]}

    def record(self, sid, value=None):
        path = self.root / (sid + "-input.json")
        path.write_text(json.dumps(value or self.result(sid)), encoding="utf-8")
        return tool.record(self.run, sid, path)

    def complete(self):
        self.init()
        for sid in ("SEG-01", "SEG-02"):
            tool.task(self.run, sid, "synthetic test")
            self.record(sid)

    def test_order_missing_and_frozen_source(self):
        self.init()
        with self.assertRaisesRegex(ValueError, "predecessor"):
            tool.task(self.run, "SEG-02", "test")
        with self.assertRaisesRegex(ValueError, "missing/stale"):
            tool.assemble(self.run)
        self.source_path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "source bytes changed"):
            tool.progress(self.run)

    def test_init_rejects_wrong_revision_and_plan_omission(self):
        self.plan["source_revision"] = "R99"
        self.prepare()
        with self.assertRaisesRegex(ValueError, "revision"):
            self.init()
        self.plan["source_revision"] = "R01"
        self.plan["segments"].pop()
        self.prepare()
        with self.assertRaisesRegex(ValueError, "omits"):
            self.init()

    def test_manual_cli_entry_creates_inspectable_tasks_without_model_calls(self):
        def invoke(*args):
            output = io.StringIO()
            with redirect_stdout(output):
                tool.main(["--run", str(self.run), *args])
            return json.loads(output.getvalue())
        created = invoke("init", "--source", str(self.source_path), "--plan", str(self.plan_path), "--base", str(self.base_path))
        self.assertFalse(created["model_called"])
        self.assertFalse(invoke("status")["all_current"])
        ref = invoke("task", "--segment", "SEG-01", "--reason", "synthetic CLI test")
        self.assertFalse(ref["model_called"])
        task = tool.read(self.run / ref["path"])
        self.assertEqual(task["paragraphs"], self.source["story_paragraphs"][:1])
        self.assertEqual(task["next_target_state"], self.state)
        result = self.root / "result.json"
        result.write_text(json.dumps(self.result("SEG-01")))
        recorded = invoke("record", "--segment", "SEG-01", "--result", str(result))
        self.assertFalse(recorded["semantic_continuity_verified"])
        self.assertTrue(invoke("status")["segments"][0]["current"])
    def test_carry_task_revision_and_hard_conflict(self):
        self.init()
        tool.task(self.run, "SEG-01", "test")
        value = self.result("SEG-01")
        value["start_state"]["props"] = "wrong"
        with self.assertRaisesRegex(ValueError, "carry"):
            self.record("SEG-01", value)
        value = self.result("SEG-01")
        value["options"][0]["hard_conflicts"] = ["missing line"]
        with self.assertRaisesRegex(ValueError, "hard conflicts"):
            self.record("SEG-01", value)
        value = self.result("SEG-01")
        value["task_sha256"] = "stale"
        with self.assertRaisesRegex(ValueError, "binding"):
            self.record("SEG-01", value)

    def test_revise_keeps_history_and_invalidates_dependencies(self):
        self.complete()
        old = tool.read(self.run / "manifest.json")["results"].copy()
        self.assertTrue(tool.progress(self.run)["all_current"])
        tool.task(self.run, "SEG-01", "local revision")
        self.assertFalse(any(s["current"] for s in tool.progress(self.run)["segments"]))
        self.record("SEG-01")
        rows = tool.progress(self.run)["segments"]
        self.assertTrue(rows[0]["current"])
        self.assertFalse(rows[1]["current"])
        for ref in old.values():
            self.assertEqual(tool.digest(self.run / ref["path"]), ref["sha256"])

    def test_frozen_task_tampering_rejected(self):
        self.init()
        ref = tool.task(self.run, "SEG-01", "test")
        path = self.run / ref["path"]
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "changed task"):
            self.record("SEG-01")

    def test_assemble_never_claims_schema_or_semantic_success(self):
        self.complete()
        # Synthetic fixture intentionally lacks full shot fields. A real schema
        # validator must reject it; simulate the unavailable-dependency branch
        # solely to exercise composition, never to assert business-schema PASS.
        import builtins
        original = builtins.__import__
        def without_schema(name, *args, **kwargs):
            if name in ("jsonschema", "referencing"):
                raise ImportError("synthetic dependency absence")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=without_schema):
            receipt = tool.assemble(self.run)
        output = tool.read(self.run / receipt["output"])
        self.assertEqual([s["shot_id"] for s in output["scenes"][0]["shots"]], ["S01-001", "S01-002"])
        self.assertTrue(receipt["schema_check"].startswith("unavailable"))
        self.assertFalse(receipt["semantic_continuity_verified"])
        self.assertTrue(receipt["production_blocked"])
        with patch("builtins.__import__", side_effect=without_schema), self.assertRaises(FileExistsError):
            tool.assemble(self.run)

    def test_assemble_rejects_changed_dialogue_and_duplicate_shot(self):
        self.complete()
        tool.task(self.run, "SEG-02", "test duplicate")
        value = self.result("SEG-02")
        value["shots"][0]["shot_id"] = "S01-001"
        self.record("SEG-02", value)
        with self.assertRaisesRegex(ValueError, "duplicate assembled"):
            tool.assemble(self.run)
        tool.task(self.run, "SEG-01", "test changed source text")
        value = self.result("SEG-01")
        value["shots"][0]["audio"]["dialogue"][0]["text"] = "changed"
        self.record("SEG-01", value)
        tool.task(self.run, "SEG-02", "recheck after predecessor")
        self.record("SEG-02")
        with self.assertRaisesRegex(ValueError, "dialogue text changed"):
            tool.assemble(self.run)


if __name__ == "__main__":
    unittest.main()
