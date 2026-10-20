from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "storyboard-planner"
SCHEMA_V2 = ROOT / "schemas" / "storyboard-package.schema.json"
SCHEMA_V1 = ROOT / "schemas" / "storyboard-package-v1.schema.json"


def sample_package() -> dict[str, object]:
    return {
        "schema_version": "3.0",
        "project_id": "MSB-TEST",
        "revision": "R02",
        "status": "candidate",
        "human_readable_path": "C:/project/R02_导演镜头方案.md",
        "source_narrative": {
            "artifact_id": "ART-NARRATIVE-R01",
            "logical_id": "narrative.package",
            "revision": "R01",
            "path": "C:/project/R01_叙事包.json",
        },
        "source_paragraph_ids": ["STP-01"],
        "source_dialogue_ids": ["DLG-01"],
        "source_asset_requirement_ids": ["AST-CHAR-A"],
        "director_rules": {
            "aspect_and_exhibition": "9:16 手机观看",
            "narrative_viewpoint": "客观观察",
            "camera_principle": "关系变化时切近",
            "editing_rhythm": "在拒绝成立后停留反应",
            "light_color_intent": "继承室内窗光方向",
            "sound_principle": "对白清晰，环境声连续",
            "technical_constraint_status": "unknown_director_intent_only",
        },
        "scenes": [{
            "scene_id": "S01",
            "source_paragraph_ids": ["STP-01"],
            "dramatic_goal": "让拒绝改变关系",
            "start_state": "主角被要求让步",
            "change": "主角明确拒绝",
            "end_state": "双方重新谈判",
            "space_rules": {
                "axis": "两人对话轴",
                "screen_direction": "主角朝画面右侧",
                "subject_positions": "主角左、对方右",
                "key_objects": "桌面保持不动",
                "directional_anchors": "窗位于主角左后方",
                "light_source": "窗光从画面左侧进入",
                "sound_base": "安静室内环境声",
            },
            "beats": [{
                "beat_id": "B01",
                "source_paragraph_ids": ["STP-01"],
                "trigger": "对方要求主角让步",
                "goal_or_relationship_change": "主角夺回决定权",
                "audience_information": "拒绝不是赌气而是边界",
                "end_condition": "对方停止施压",
            }],
            "shots": [{
                "shot_id": "S01-010",
                "beat_id": "B01",
                "source_paragraph_ids": ["STP-01"],
                "source_dialogue_ids": ["DLG-01"],
                "source_asset_requirement_ids": ["AST-CHAR-A"],
                "purpose": "让主角的拒绝成为关系转折",
                "audience_information": "主角主动设立边界",
                "shot_size": "MCU",
                "shot_type": "SINGLE",
                "camera_intent": "眼平观察，保持尊重",
                "composition": "主角保留面向对方的视线空间",
                "blocking": [{
                    "subject": "主角", "start": "坐姿看向对方", "action": "说完后放下手中物",
                    "end": "保持目光", "trigger": "DLG-01 开始",
                }],
                "camera_movement": {
                    "type": "STATIC", "start_frame": "中近景", "end_frame": "保持中近景",
                    "speed_quality": "静止", "reason": "让表演承担关系变化",
                },
                "focus": "主角眼神和放下物品的动作",
                "light_color_intent": "继承场景窗光方向",
                "audio": {
                    "dialogue": [{
                        "dialogue_id": "DLG-01", "presentation": "SYNC",
                        "timing_basis": {"status": "not_evaluable", "source_type": None, "source_path": None, "source_field": None},
                    }],
                    "sfx": ["物品轻放桌面的声音"], "ambience": ["室内环境声"], "music": [],
                },
                "cut": {
                    "entry": "延续对方施压后的停顿", "exit": "主角目光稳定后切出",
                    "trigger": "拒绝的信息完成", "information_handoff": "交给对方的反应",
                },
                "continuity": "主角朝向、手中物和窗光方向延续",
                "deletion_test": "删除后失去主角设立边界的可观察转折",
                "panels": [{
                    "panel_id": "S01-010-A", "function": "SINGLE", "screen_state": "主角看向对方",
                    "action_or_camera_state": "摄影机静止，主角说完放下物品", "audio_or_cut": "DLG-01 完成后停留",
                }],
            }],
        }],
        "continuity_register": [{
            "continuity_id": "CONT-PROP-01", "subject": "手中物", "shot_ids": ["S01-010"],
            "locked_state": "开场握在手中", "allowed_change": "说完后放到桌面",
            "risk_response": "后续镜头保持在桌面",
        }],
        "asset_gaps": [],
        "open_questions": [],
    }


class StoryboardPlannerContractTests(unittest.TestCase):
    def test_required_files_and_versions_exist(self) -> None:
        required = [
            SKILL_DIR / "SKILL.md",
            SKILL_DIR / "modules" / "shot-planning.md",
            SKILL_DIR / "references" / "专业影视分镜执行规范.md",
            SKILL_DIR / "references" / "专业影视分镜审查门槛.md",
            SKILL_DIR / "templates" / "专业影视分镜输出模板.md",
            SKILL_DIR / "tests" / "pressure-tests.md",
            SCHEMA_V2,
            SCHEMA_V1,
        ]
        self.assertEqual([str(path) for path in required if not path.is_file()], [])
        self.assertEqual(json.loads(SCHEMA_V2.read_text(encoding="utf-8"))["properties"]["schema_version"]["const"], "4.0")
        legacy = json.loads(SCHEMA_V1.read_text(encoding="utf-8"))
        self.assertEqual(legacy["properties"]["schema_version"]["const"], "1.0")
        self.assertIn("不得用于新建、修改或回存", legacy["description"])

    def test_skill_closes_v3_and_asset_scope_loopholes(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        contract = (SKILL_DIR / "references" / "专业影视分镜执行规范.md").read_text(encoding="utf-8")
        for marker in (
            "已确认 `narrative.package` revision",
            "对白文本和说话者必须逐字沿用",
            "`storyboard-package-v1.schema.json`、`storyboard-package-v2.schema.json` 与 `storyboard-package-v3.schema.json` 仅用于旧项目的只读兼容",
            "不调用 Blender",
            "不决定视频模型、生成分段",
        ):
            self.assertIn(marker, skill)
        for marker in (
            "Scene、Shot 保留稳定来源引用",
            "不得新增或修改资产需求",
            "缺口必须回指来源段落、受影响镜头和关联 AST",
        ):
            self.assertIn(marker, contract)
        for legacy in (
            "required_context_" + "manifest",
            "workflow-" + "orchestrator",
            "previs.blender-" + "execute",
            "production_plan 3.1",
        ):
            self.assertNotIn(legacy, skill)

    def test_choice_scope_and_expression_review_remain_explicit(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        template = (SKILL_DIR / "templates" / "专业影视分镜输出模板.md").read_text(encoding="utf-8")
        profile = json.loads((SKILL_DIR / "review-profile.json").read_text(encoding="utf-8"))
        trace = next(check["text"] for check in profile["production_artifact_checks"] if check["id"] == "PLAN-TRACE")
        for marker in ("用户明确锁定的拍法", "AI 自选但未经用户确认", "不擅自解锁", "不扩大为全量重设计"):
            self.assertIn(marker, skill)
        self.assertIn("表达审查如实记未完成", template)
        self.assertIn("AI 未确认旧拍法不冒充锁定", trace)
        self.assertIn("PLAN-EXPRESSION", {check["id"] for check in profile["production_artifact_checks"]})
        pressure = (SKILL_DIR / "tests" / "pressure-tests.md").read_text(encoding="utf-8")
        self.assertIn("用户锁定一镜到底，只换服装", pressure)
        self.assertIn("未锁定对白拍法，旧候选却写固定正反打", pressure)

    def test_visual_conception_is_loaded_by_real_preparation_collector(self) -> None:
        from scripts import stage_context_cli
        reference = SKILL_DIR / "references" / "visual-conception.md"
        frozen = stage_context_cli._local_standard_documents(SKILL_DIR)
        self.assertIn(reference.resolve(), frozen)
        self.assertIn("references/visual-conception.md", (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("../references/visual-conception.md", (SKILL_DIR / "modules" / "shot-planning.md").read_text(encoding="utf-8"))
        self.assertTrue(reference.read_text(encoding="utf-8").strip())
        # Wiring only: actual creative quality needs a fresh-scene trial.

    def test_foundations_are_frozen_by_module_links_and_missing_files_block(self) -> None:
        import shutil
        import tempfile
        from unittest.mock import patch
        from scripts import stage_context_cli as cli
        names = ("01_分镜与剪辑组织.md", "02_景别与构图.md", "03_运镜与人物调度.md")
        documents = cli._local_standard_documents(SKILL_DIR)
        for name in names:
            reference = SKILL_DIR / "references" / "foundations" / name
            self.assertIn(reference.resolve(), documents)
            self.assertGreater(len(reference.read_bytes()), 0)
            module = (SKILL_DIR / "modules" / "shot-planning.md").read_text(encoding="utf-8")
            self.assertIn("../references/foundations/" + name, module)
            self.assertIn("--required-reference skills/storyboard-planner/references/foundations/" + name, module)
        # Mutate only a temporary repository, never the active references.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "skills", root / "skills")
            shutil.copytree(ROOT / "schemas", root / "schemas")
            shutil.copyfile(ROOT / "PROJECT_RULES.md", root / "PROJECT_RULES.md")
            target = root / "skills/storyboard-planner/references/foundations" / names[1]
            target.unlink()
            with patch.object(cli, "ROOT", root):
                with self.assertRaisesRegex(cli.StageContextError, "明确引用的本地规范缺失"):
                    cli._local_standard_documents(root / "skills/storyboard-planner")

    def test_v2_schema_references_upstream_assets_without_embedding_requirements(self) -> None:
        schema = json.loads(SCHEMA_V2.read_text(encoding="utf-8"))
        self.assertTrue({"source_narrative", "source_paragraph_ids", "source_dialogue_ids", "source_asset_requirement_ids"}.issubset(schema["required"]))
        schema_text = json.dumps(schema, ensure_ascii=False)
        for forbidden in ("asset_requirements_document", "required_assets", "video_input_role", "production_plan", "model_selection", "video_prompt"):
            self.assertNotIn(forbidden, schema_text)
        self.assertIn("asset_gaps", schema["required"])
        self.assertNotIn("asset_id", schema["$defs"]["assetGap"]["properties"])

    def test_sample_package_is_traceable_and_schema_valid_when_validator_exists(self) -> None:
        package = sample_package()
        event_ids = set(package["source_paragraph_ids"])
        dialogue_ids = set(package["source_dialogue_ids"])
        asset_ids = set(package["source_asset_requirement_ids"])
        for scene in package["scenes"]:
            self.assertTrue(set(scene["source_paragraph_ids"]).issubset(event_ids))
            for beat in scene["beats"]:
                self.assertTrue(set(beat["source_paragraph_ids"]).issubset(event_ids))
            for shot in scene["shots"]:
                self.assertTrue(set(shot["source_paragraph_ids"]).issubset(event_ids))
                self.assertTrue(set(shot["source_dialogue_ids"]).issubset(dialogue_ids))
                self.assertTrue(set(shot["source_asset_requirement_ids"]).issubset(asset_ids))

        try:
            import jsonschema
        except ImportError:
            return
        schema = json.loads(SCHEMA_V2.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        from scripts.core.schema_contracts import require_schema
        require_schema(package, "storyboard-package.schema.json", label="legacy fixture")

    def test_asset_gap_has_no_new_ast_identity(self) -> None:
        package = sample_package()
        package["asset_gaps"] = [{
            "gap_id": "GAP-ASSET-PRODUCT-BACK",
            "source_paragraph_ids": ["STP-01"],
            "affected_shot_ids": ["S01-010"],
            "related_asset_ids": [],
            "missing_information": "产品背面事实缺失",
            "impact": "无法设计需要展示背面的镜头",
            "return_required": True,
        }]
        self.assertNotIn("asset_id", package["asset_gaps"][0])
        self.assertTrue(package["asset_gaps"][0]["return_required"])

    def test_pressure_suite_covers_boundary_attacks(self) -> None:
        text = (SKILL_DIR / "tests" / "pressure-tests.md").read_text(encoding="utf-8")
        for marker in ("顺手补资产", "对白逐句拆镜", "Blender 诱导", "模型和生成分段诱导", "Panel 出图诱导", "v1 回写诱导"):
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
