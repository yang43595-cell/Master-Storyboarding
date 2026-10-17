"""Review configuration follows JSON-first delivery without weakening full packages."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_creative_review_profile_uses_json_first_delivery():
    profile = json.loads((ROOT / "skills/creative-director/review-profile.json").read_text())
    checks = {item["id"]: item["text"] for key in ("skill_contract_checks", "production_artifact_checks") for item in profile[key]}
    assert "JSON" in checks["CONTRACT-OUTPUT"]
    assert "JSON" in checks["CREATIVE-FACTS"]
    assert "human_readable_path" in checks["CREATIVE-IDS"]
    assert "准确 JSON 修订" in checks["CREATIVE-MANIFEST"]
    assert "不登记导出为依赖" in checks["CREATIVE-MANIFEST"]
    assert "JSON" in checks["SERIAL-DELIVERY"]
    for obsolete in ("Markdown 确认前", "确认后生成 JSON", "确认才生成交付 JSON", "登记两份产物"):
        assert obsolete not in json.dumps(profile, ensure_ascii=False)


def test_dialogue_review_is_not_a_complete_narrative_package():
    text = (ROOT / "skills/creative-director/references/dialogue-lineage-contract.md").read_text()
    assert "仅对白核对不运行完整叙事包校验" in text
    assert "不登记为 narrative.package" in text
    schema = json.loads((ROOT / "schemas/narrative-package.schema.json").read_text())
    assert schema["properties"]["story_paragraphs"]["minItems"] == 1
    assert schema["$defs"]["dialogue"]["properties"]["source_paragraph_id"]["pattern"] == "^STP-[A-Z0-9-]+$"
