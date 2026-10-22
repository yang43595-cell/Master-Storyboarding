from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "creative-director"
SCHEMA_PATH = ROOT / "schemas" / "narrative-package.schema.json"


class CreativeDirectorContractTests(unittest.TestCase):
    def test_required_contract_files_exist(self) -> None:
        required = [
            SKILL_DIR / "SKILL.md",
            SKILL_DIR / "modules" / "story-method.md",
            SKILL_DIR / "references" / "dialogue-lineage-contract.md",
            SKILL_DIR / "templates" / "narrative-package-template.md",
            SKILL_DIR / "tests" / "cinematic-pressure-tests.md",
            SKILL_DIR / "knowledge-profile.json",
            SKILL_DIR / "review-profile.json",
            SCHEMA_PATH,
        ]
        self.assertEqual([str(path) for path in required if not path.is_file()], [])

    def test_skill_owns_dialogue_and_stays_out_of_directing(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        dialogue_contract = (SKILL_DIR / "references" / "dialogue-lineage-contract.md").read_text(encoding="utf-8")
        for marker in (
            "JSON 交付与按需导出",
            "<revision>_叙事包.json",
            "STP-*",
            "DLG-*",
            "AST-*",
            "普通候选不登记 checksum",
            "不自行设计景别、机位、构图、运镜、切点",
            "用户已锁定的开场或拍法属于输入约束",
            "原样连同来源保留",
            "`locked_facts`",
            "记录要求不等于本阶段重新设计",
        ):
            self.assertIn(marker, skill)
        for marker in (
            "每条对白至少登记",
            "状态为 `locked` 或 `confirmed` 的对白必须逐字保留",
            "下游只能给对白附加承载方式",
        ):
            self.assertIn(marker, dialogue_contract)
        for legacy in (
            "required_context_" + "manifest",
            "dispatch_" + "envelope",
            "execution_" + "receipt",
            "workflow-" + "orchestrator",
            "short-video-" + "dialogue-polish",
        ):
            self.assertNotIn(legacy, skill)

    def test_v2_schema_models_stable_paragraphs_dialogues_and_assets(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertTrue({"story_paragraphs", "dialogues", "asset_requirements"}.issubset(schema["required"]))
        definitions = schema["$defs"]
        self.assertEqual(definitions["storyParagraph"]["properties"]["paragraph_id"]["pattern"], "^STP-[A-Z0-9-]+$")
        self.assertEqual(definitions["dialogue"]["properties"]["dialogue_id"]["pattern"], "^DLG-[A-Z0-9-]+$")
        self.assertEqual(definitions["assetRequirement"]["properties"]["asset_id"]["pattern"], "^AST-[A-Z0-9-]+$")
        self.assertEqual(definitions["assetRequirement"]["properties"]["entity_id"]["pattern"], "^CHR-[A-Z0-9-]+$")
        schema_text = json.dumps(schema, ensure_ascii=False)
        for forbidden in ("shot_size", "camera", "prompt", "model_selection"):
            self.assertNotIn(forbidden, schema_text)

    def test_knowledge_profile_enforces_directing_boundary(self) -> None:
        profile = json.loads(
            (SKILL_DIR / "knowledge-profile.json").read_text(encoding="utf-8")
        )
        terms = set(profile["forbidden_knowledge_terms"])
        self.assertTrue(
            {
                "景别", "机位", "构图", "运镜", "切点", "镜头表",
                "镜头点", "提示词", "模型选择", "生成路径",
            }.issubset(terms)
        )

    def test_legacy_v1_sample_has_valid_identity_and_references(self) -> None:
        package = {
            "schema_version": "1.0",
            "project_id": "MSB-TEST",
            "revision": "R01",
            "status": "candidate",
            "human_readable_path": "C:/project/R01_编导稿.md",
            "objective": "保留原对白形成自洽短片",
            "locked_facts": [{"fact_id": "FACT-01", "statement": "原对白不得改", "source_ref": "user:request"}],
            "events": [{
                "event_id": "EVT-01", "summary": "角色拒绝牺牲", "start_state": "被要求让步",
                "change": "角色明确拒绝", "end_state": "关系重新谈判", "participants": ["CHR-A"], "depends_on": [],
            }],
            "dialogues": [{
                "dialogue_id": "DLG-01", "speaker_id": "CHR-A", "text": "爱不是牺牲。",
                "source_event_id": "EVT-01", "status": "locked", "source_ref": "source.md:12",
            }],
            "asset_requirements": [{
                "asset_id": "AST-CHAR-A", "entity_id": "CHR-A", "category": "CHARACTER", "name": "主角",
                "narrative_purpose": "承担拒绝与关系转变", "source_event_ids": ["EVT-01"],
                "identity_constraints": ["身份保持一致"], "required_states": ["拒绝前", "拒绝后"],
                "interactions": [], "reference_paths": [], "unknowns": ["外观尚未确认"],
            }],
            "change_summary": [],
            "open_questions": ["主角外观待确认"],
        }
        event_ids = {item["event_id"] for item in package["events"]}
        self.assertEqual(len(event_ids), len(package["events"]))
        self.assertTrue(all(re.fullmatch(r"EVT-[A-Z0-9-]+", item) for item in event_ids))
        self.assertTrue(all(item["source_event_id"] in event_ids for item in package["dialogues"]))
        self.assertTrue(all(set(item["source_event_ids"]).issubset(event_ids) for item in package["asset_requirements"]))
        character_mappings = {
            item["entity_id"]: item["asset_id"]
            for item in package["asset_requirements"] if item["category"] == "CHARACTER"
        }
        self.assertEqual(character_mappings, {"CHR-A": "AST-CHAR-A"})
        referenced_entities = {
            entity_id for event in package["events"] for entity_id in event["participants"]
        } | {item["speaker_id"] for item in package["dialogues"]}
        self.assertTrue(referenced_entities.issubset(character_mappings))
        self.assertEqual(package["dialogues"][0]["text"], "爱不是牺牲。")

        try:
            import jsonschema
        except ImportError:
            return
        schema = json.loads((ROOT / "schemas/narrative-package-v1.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(package, schema)
        incomplete_character_package = json.loads(json.dumps(package))
        del incomplete_character_package["asset_requirements"][0]["entity_id"]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(incomplete_character_package, schema)

    def test_pressure_suite_covers_locked_dialogue_and_boundary_attacks(self) -> None:
        text = (SKILL_DIR / "tests" / "cinematic-pressure-tests.md").read_text(encoding="utf-8")
        for marker in ("对白原样提取", "产品参考不足", "用户同时要求不同景别", "表外资产诱导", "JSON 原文保全与按需导出", "普通候选要求哈希"):
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
