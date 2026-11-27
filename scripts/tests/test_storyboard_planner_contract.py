from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "storyboard-planner"
SCHEMA_V5 = ROOT / "schemas" / "storyboard-package.schema.json"


def sample_package() -> dict[str, object]:
    return {
        "schema_version": "5.0",
        "project_id": "MSB-TEST",
        "revision": "R02",
        "status": "candidate",
        "source_narrative": {
            "artifact_id": "ART-NARRATIVE-R01",
            "logical_id": "narrative.package",
            "revision": "R01",
            "path": "02_编导/R01_叙事包.json",
        },
        "source_paragraph_ids": ["STP-01"],
        "source_dialogue_ids": ["DLG-01"],
        "source_asset_requirement_ids": ["AST-CHAR-A"],
        "director_rules": {
            "aspect_and_exhibition": "9:16 手机竖屏观看",
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
            "audiovisual_flow": "S01-010 主角明确拒绝并放下手中物品，稳住视线等待对方反应。",
            "space_rules": {
                "axis": "两人对话轴",
                "screen_direction": "主角朝画面右侧",
                "subject_positions": "主角左、对方右",
                "key_objects": "桌面保持不动",
                "directional_anchors": "窗位于主角左后方",
                "light_source": "窗光从画面左侧进入",
                "sound_base": "安静室内环境声",
            },
            "defaults": {
                "light_color_intent": "左侧窗光",
                "ambience": ["室内底声"],
                "music": [],
            },
            "shots": [{
                "shot_id": "S01-010",
                "source_paragraph_ids": ["STP-01"],
                "source_dialogue_ids": ["DLG-01"],
                "source_asset_requirement_ids": ["AST-CHAR-A"],
                "purpose": "让主角的拒绝成为关系转折",
                "camera": {
                    "framing": "中近景，眼高，人物偏左保留面向对方的视线空间",
                    "movement": "固定",
                    "focus": "主角眼神和放下物品的动作",
                },
                "action": "主角坐姿持物看向对方，坚定说出拒绝后将物品轻放在桌面并松手。",
                "cut": {
                    "exit": "物品落稳桌面、主角目光稳定后切出",
                    "handoff": "交给对方的反应",
                },
                "rhythm": "约4秒｜拒绝后停半拍，接对方反应",
                "audio": {
                    "dialogue": [{
                        "dialogue_id": "DLG-01",
                        "speaker_id": "CHR-A",
                        "text": "不。",
                        "presentation": "SYNC",
                    }],
                    "sfx": ["物品轻放桌面的声音"],
                },
            }],
        }],
        "asset_gaps": [],
        "open_questions": [],
        "timing_basis": {
            "status": "not_evaluable",
            "source_type": None,
            "source_path": None,
            "source_field": None,
        },
    }


class StoryboardPlannerContractTests(unittest.TestCase):
    def test_required_files_and_versions_exist(self) -> None:
        required = [
            SKILL_DIR / "SKILL.md",
            SKILL_DIR / "modules" / "shot-planning.md",
            SKILL_DIR / "references" / "专业影视分镜审查门槛.md",
            SKILL_DIR / "templates" / "专业影视分镜输出模板.md",
            SKILL_DIR / "tests" / "pressure-tests.md",
            SCHEMA_V5,
        ]
        self.assertEqual([str(path) for path in required if not path.is_file()], [])
        self.assertEqual(json.loads(SCHEMA_V5.read_text(encoding="utf-8"))["properties"]["schema_version"]["const"], "5.0")

    def test_skill_closes_asset_scope_loopholes(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        contract = (SKILL_DIR / "modules" / "shot-planning.md").read_text(encoding="utf-8")
        for marker in (
            "已确认 `narrative.package` revision",
            "对白文本和说话者必须逐字沿用",
            "不决定视频模型、生成分段",
        ):
            self.assertIn(marker, skill)
        for marker in (
            "Scene、Shot 保留稳定来源引用",
            "不得新增或修改资产需求",
            "缺口必须回指来源段落、受影响镜头和关联 AST",
        ):
            self.assertIn(marker, contract)

    def test_v5_schema_references_upstream_assets_without_embedding_requirements(self) -> None:
        schema = json.loads(SCHEMA_V5.read_text(encoding="utf-8"))
        self.assertTrue({"source_narrative", "source_paragraph_ids", "source_dialogue_ids", "source_asset_requirement_ids"}.issubset(schema["required"]))
        schema_text = json.dumps(schema, ensure_ascii=False)
        for forbidden in ("asset_requirements_document", "required_assets", "video_input_role", "production_plan", "model_selection", "video_prompt"):
            self.assertNotIn(forbidden, schema_text)
        self.assertIn("asset_gaps", schema["required"])
        self.assertNotIn("asset_id", schema["$defs"]["assetGap"]["properties"])

    def test_sample_package_is_traceable_and_schema_valid(self) -> None:
        package = sample_package()
        from scripts.core.schema_contracts import require_schema
        require_schema(package, "storyboard-package.schema.json", label="v5 fixture")

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


if __name__ == "__main__":
    unittest.main()
