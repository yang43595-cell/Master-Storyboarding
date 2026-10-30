from __future__ import annotations

import atexit
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.production_plan import (
    PreflightIssue,
    _check_v32_duration_decomposition,
    validate_production_plan,
)  # noqa: E402


_TEMP = tempfile.mkdtemp(prefix="msb-preflight-")
PROJECT = Path(_TEMP)
atexit.register(shutil.rmtree, _TEMP, ignore_errors=True)

# These tests inspect path/availability and segment policy, not image pixels.
# Keep the fixture inside the synthetic project; never depend on a private photo
# or treat these non-image bytes as product/visual evidence.
_PRODUCT_REF = PROJECT / "assets/product-references/SYNTHETIC-NONIMAGE-FIXTURE.png"
_PRODUCT_REF.parent.mkdir(parents=True, exist_ok=True)
_PRODUCT_REF.write_bytes(b"SYNTHETIC contract fixture only; not an image or product reference")


def _request_file() -> Path:
    req = PROJECT / "00_项目管理" / "project_request.json"
    req.parent.mkdir(parents=True, exist_ok=True)
    req.write_text(
        json.dumps({"source_copy": "我是你的毛孔。\n补水就是抛光。", "target_duration": "30秒"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return req


def _voiceover_lock() -> dict[str, object]:
    return {
        "source_path": "00_项目管理/project_request.json",
        "sha256": hashlib.sha256(_request_file().read_bytes()).hexdigest(),
        "lines": [
            {"id": "L01", "text": "我是你的毛孔"},
            {"id": "L02", "text": "补水就是抛光"},
        ],
    }


def make_plan(**overrides: object) -> dict[str, object]:
    plan: dict[str, object] = {
        "schema_version": "1.0",
        "revision": "R01",
        "voiceover_lock": _voiceover_lock(),
        "audio_policy": "post_dub_no_lipsync",
        "product_policy": "ending_only",
        "segments": [
            {
                "id": "V01", "order": 1, "voiceover_line_ids": ["L01"], "event_id": "pore_to_surface",
                "shot_strategy": ["close_up"], "start_state": "oil_in_pore", "end_state": "hydration_started",
                "reference_images": [], "prompt_text": "纯机制画面，不生成角色语音，无口型同步，只有动作音效。",
                "product_appearance": False, "status": "current", "duration_seconds": 8,
            },
            {
                "id": "V02", "order": 2, "voiceover_line_ids": ["L02"], "event_id": "hydration_ready",
                "shot_strategy": ["wide_macro"], "start_state": "hydration_started", "end_state": "hydration_ready",
                "reference_images": [], "prompt_text": "纯机制画面，不生成角色语音，无口型同步，只有动作音效。",
                "product_appearance": False, "status": "current", "duration_seconds": 10,
            },
        ],
    }
    plan.update(overrides)
    if plan.get("schema_version") == "3.1":
        segments = plan.get("segments") or []
        for segment in segments:
            if isinstance(segment, dict):
                segment.setdefault("scene_id", str(segment.get("event_id") or segment.get("id") or "UNKNOWN"))
        ids = [str(segment.get("id")) for segment in segments if isinstance(segment, dict)]
        plan.setdefault(
            "segment_registry",
            {
                "ordered_segment_ids": ids,
                "current_segment_id": ids[0] if ids else None,
                "next_segment_id": ids[1] if len(ids) > 1 else None,
                "final_segment_id": ids[-1] if ids else None,
            },
        )
    return plan


def plan_with_duplicate_v01_v02() -> dict[str, object]:
    plan = make_plan()
    plan["segments"][1]["event_id"] = plan["segments"][0]["event_id"]
    plan["segments"][1]["start_state"] = plan["segments"][0]["start_state"]
    plan["segments"][1]["end_state"] = plan["segments"][0]["end_state"]
    return plan


def plan_with_order(order: list[str]) -> dict[str, object]:
    plan = make_plan()
    segs: list[dict[str, object]] = []
    for i, kind in enumerate(order, start=1):
        if kind == "PRODUCT":
            segs.append(
                {
                    "id": f"V{i:02d}", "order": i, "voiceover_line_ids": [], "event_id": "product_hero",
                    "shot_strategy": ["wide_macro"], "start_state": "hydrated", "end_state": "product_hero",
                    "reference_images": [str(_PRODUCT_REF)], "prompt_text": "产品英雄，无台词。",
                    "product_appearance": True, "status": "current", "duration_seconds": 6,
                }
            )
        elif kind == "CLEANUP":
            segs.append(
                {
                    "id": f"V{i:02d}", "order": i, "voiceover_line_ids": [], "event_id": "cleanup",
                    "shot_strategy": ["close_up"], "start_state": "product_hero", "end_state": "clean",
                    "reference_images": [], "prompt_text": "无台词。", "product_appearance": False,
                    "status": "current", "duration_seconds": 6,
                }
            )
        else:
            segs.append(
                {
                    "id": f"V{i:02d}", "order": i, "voiceover_line_ids": ["L01", "L02"], "event_id": "intro",
                    "shot_strategy": ["close_up"], "start_state": "a", "end_state": "b",
                    "reference_images": [], "prompt_text": "纯机制画面，不生成角色语音，无口型同步。",
                    "product_appearance": False, "status": "current", "duration_seconds": 6,
                }
            )
    plan["segments"] = segs
    return plan


def plan_with_prompt(text: str) -> dict[str, object]:
    plan = make_plan()
    plan["segments"][0]["prompt_text"] = text
    return plan


def plan_missing_line(unmapped_id: str) -> dict[str, object]:
    plan = make_plan()
    lines = list(_voiceover_lock()["lines"])
    lines.append({"id": unmapped_id, "text": "没被覆盖的行"})
    plan["voiceover_lock"] = {
        "source_path": "00_项目管理/project_request.json",
        "sha256": _voiceover_lock()["sha256"],
        "lines": lines,
    }
    return plan


class ProductionPreflightTests(unittest.TestCase):
    def test_ready_intent_contract_and_current_revision_evidence_are_accepted(self) -> None:
        management = PROJECT / "00_项目管理"
        intent_path = management / "prompt_intent.json"
        evidence_path = management / "revision_evidence.json"
        intent_path.write_text(
            json.dumps(
                {
                    "intent_status": "ready",
                    "goal": "局部修复 V01",
                    "content_mode": "剧情短片改编模式",
                    "output_spec": {"duration_seconds": 30},
                    "preserve": ["已确认对白"],
                    "allowed_changes": ["动作方向"],
                    "forbidden_changes": ["产品外观"],
                    "acceptance_criteria": ["动作方向正确"],
                    "open_questions": [],
                    "conflicts": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (management / "artifact_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "job_id": "TEST",
                    "artifacts": [
                        {
                            "artifact_id": "ART-VIDEO-V01",
                            "logical_id": "video.v01",
                            "type": "video",
                            "status": "current",
                            "revision": "R01",
                            "path_or_url": "outputs/video-v01.mp4",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "evidence_id": "REV-EVID-V01",
                    "artifact_ref": "artifact://ART-VIDEO-V01",
                    "base_revision": "R01",
                    "status": "ready",
                    "segment_id": "V01",
                    "observed": "动作方向相反",
                    "expected": "动作向右",
                    "preserve": ["角色外观"],
                    "allowed_changes": ["动作方向"],
                    "forbidden_changes": ["对白"],
                    "acceptance_criteria": ["主体最终位于右侧"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        plan = make_plan(
            intent_contract_ref="00_项目管理/prompt_intent.json",
            revision_evidence_refs=["00_项目管理/revision_evidence.json"],
        )

        report = validate_production_plan(plan, PROJECT)

        self.assertNotIn("INTENT_CONTRACT_INVALID", report.error_codes)
        self.assertNotIn("REVISION_EVIDENCE_INVALID", report.error_codes)
        self.assertNotIn("REVISION_EVIDENCE_STALE", report.error_codes)
        self.assertNotIn("REVISION_EVIDENCE_ARTIFACT_MISSING", report.error_codes)

    def test_revision_evidence_must_reference_registered_current_artifact(self) -> None:
        evidence_path = PROJECT / "00_项目管理" / "missing_artifact_evidence.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "evidence_id": "REV-EVID-MISSING",
                    "artifact_ref": "artifact://ART-NOT-REGISTERED",
                    "base_revision": "R01",
                    "status": "ready",
                    "segment_id": "V01",
                    "observed": "动作方向相反",
                    "expected": "动作向右",
                    "preserve": [],
                    "allowed_changes": ["动作方向"],
                    "forbidden_changes": [],
                    "acceptance_criteria": ["动作方向正确"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        report = validate_production_plan(
            make_plan(revision_evidence_refs=["00_项目管理/missing_artifact_evidence.json"]),
            PROJECT,
        )
        self.assertIn("REVISION_EVIDENCE_ARTIFACT_MISSING", report.error_codes)

    def test_malformed_manifest_fails_closed_for_revision_evidence(self) -> None:
        management = PROJECT / "00_项目管理"
        (management / "artifact_manifest.json").write_text(
            json.dumps({"schema_version": "1.0", "job_id": "TEST", "artifacts": None}),
            encoding="utf-8",
        )
        evidence_path = management / "bad_manifest_evidence.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "evidence_id": "REV-EVID-BAD-MANIFEST",
                    "artifact_ref": "artifact://ART-VIDEO-V01",
                    "base_revision": "R01",
                    "status": "ready",
                    "segment_id": "V01",
                    "observed": "错误",
                    "expected": "正确",
                    "preserve": [],
                    "allowed_changes": ["动作"],
                    "forbidden_changes": [],
                    "acceptance_criteria": ["通过"],
                }
            ),
            encoding="utf-8",
        )
        report = validate_production_plan(
            make_plan(revision_evidence_refs=["00_项目管理/bad_manifest_evidence.json"]),
            PROJECT,
        )
        self.assertIn("REVISION_EVIDENCE_ARTIFACT_MISSING", report.error_codes)

    def test_intent_and_evidence_refs_cannot_escape_project_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="msb-external-contract-") as external:
            external_path = Path(external) / "prompt_intent.json"
            external_path.write_text(
                json.dumps(
                    {
                        "intent_status": "ready",
                        "goal": "越界合同",
                        "content_mode": "剧情短片改编模式",
                        "output_spec": {"duration_seconds": 30},
                        "preserve": [],
                        "allowed_changes": ["镜头"],
                        "forbidden_changes": [],
                        "acceptance_criteria": ["可验收"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report = validate_production_plan(
                make_plan(intent_contract_ref=str(external_path)),
                PROJECT,
            )
        self.assertIn("INTENT_CONTRACT_REF_MISSING", report.error_codes)

    def test_stale_revision_evidence_is_rejected(self) -> None:
        evidence_path = PROJECT / "00_项目管理" / "stale_revision_evidence.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0", "evidence_id": "REV-EVID-STALE",
                    "artifact_ref": "artifact://video.v01", "base_revision": "R00",
                    "status": "ready", "segment_id": "V01", "observed": "错误",
                    "expected": "正确", "preserve": [], "allowed_changes": ["动作"],
                    "forbidden_changes": [], "acceptance_criteria": ["通过"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        report = validate_production_plan(
            make_plan(revision_evidence_refs=["00_项目管理/stale_revision_evidence.json"]),
            PROJECT,
        )
        self.assertIn("REVISION_EVIDENCE_STALE", report.error_codes)

    def test_malformed_revision_evidence_is_rejected_by_runtime_schema(self) -> None:
        evidence_path = PROJECT / "00_项目管理" / "malformed_revision_evidence.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "evidence_id": "bad",
                    "artifact_ref": "",
                    "base_revision": "R01",
                    "status": "ready",
                    "segment_id": 123,
                    "observed": [],
                    "expected": {},
                    "preserve": "not-array",
                    "allowed_changes": "not-array",
                    "forbidden_changes": 42,
                    "acceptance_criteria": "not-array",
                    "unknown": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        report = validate_production_plan(
            make_plan(revision_evidence_refs=["00_项目管理/malformed_revision_evidence.json"]),
            PROJECT,
        )
        self.assertIn("REVISION_EVIDENCE_INVALID", report.error_codes)

    def test_baseline_plan_is_clean(self) -> None:
        report = validate_production_plan(make_plan(), PROJECT)
        assert report.ok
        assert report.error_codes == []
        assert report.warning_codes == []

    def test_adjacent_duplicate_event_without_state_delta_is_warning(self) -> None:
        report = validate_production_plan(plan_with_duplicate_v01_v02(), PROJECT)
        assert "ADJACENT_DUPLICATE_EVENT" in report.warning_codes

    def test_product_before_cleanup_is_error_when_ending_only(self) -> None:
        report = validate_production_plan(plan_with_order(["V01", "PRODUCT", "CLEANUP"]), PROJECT)
        assert "PRODUCT_NOT_AT_END" in report.error_codes

    def test_post_dub_no_lipsync_rejects_lipsync_prompt_residue(self) -> None:
        report = validate_production_plan(plan_with_prompt("角色说出台词并同步口型"), PROJECT)
        assert "AUDIO_POLICY_CONFLICT" in report.error_codes

    def test_unmapped_voiceover_line_is_warning_not_silent_drop(self) -> None:
        report = validate_production_plan(plan_missing_line("L11"), PROJECT)
        assert "VOICEOVER_LINE_UNMAPPED" in report.warning_codes

    def test_product_segment_without_real_product_ref_is_error(self) -> None:
        plan = make_plan()
        plan["segments"].append(
            {
                "id": "V99", "order": 99, "voiceover_line_ids": [], "event_id": "product_hero",
                "shot_strategy": ["wide_macro"], "start_state": "hydrated", "end_state": "product_hero",
                "reference_images": [], "prompt_text": "产品英雄。", "product_appearance": True,
                "status": "current", "duration_seconds": 6,
            }
        )
        report = validate_production_plan(plan, PROJECT)
        assert "PRODUCT_NOT_AT_END" in report.error_codes

    def test_missing_reference_file_is_error(self) -> None:
        plan = make_plan()
        plan["segments"][0]["reference_images"] = ["02_正式分镜图/K99.png"]
        report = validate_production_plan(plan, PROJECT)
        assert "REFERENCE_FILE_MISSING" in report.error_codes

    def test_asset_and_previsualization_inputs_need_no_storyboard_images(self) -> None:
        asset = PROJECT / "06_参考帧与临时素材" / "资产母版" / "character.png"
        previs = PROJECT / "06_参考帧与临时素材" / "导演预演" / "V01.mp4"
        asset.parent.mkdir(parents=True, exist_ok=True)
        previs.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"asset")
        previs.write_bytes(b"previs")
        plan = make_plan()
        plan["production_standard"] = "asset-previs-prompt-v1"
        plan["visual_anchor_policy"] = "adaptive"
        plan["segments"][0]["reference_inputs"] = [
            {"path": "06_参考帧与临时素材/资产母版/character.png", "type": "image", "role": "character"},
            {"path": "06_参考帧与临时素材/导演预演/V01.mp4", "type": "video", "role": "previsualization"},
        ]
        report = validate_production_plan(plan, PROJECT)
        assert report.ok
        assert "REFERENCE_FILE_MISSING" not in report.error_codes

    def test_missing_previsualization_input_is_error(self) -> None:
        plan = make_plan()
        plan["segments"][0]["reference_inputs"] = [
            {"path": "06_参考帧与临时素材/导演预演/missing.mp4", "type": "video", "role": "previsualization"}
        ]
        report = validate_production_plan(plan, PROJECT)
        assert "REFERENCE_FILE_MISSING" in report.error_codes

    def test_duplicate_current_logical_id_is_error(self) -> None:
        plan = make_plan()
        plan["segments"][1]["id"] = plan["segments"][0]["id"]
        report = validate_production_plan(plan, PROJECT)
        assert "DUPLICATE_CURRENT_LOGICAL_ID" in report.error_codes

    def test_voiceover_hash_change_without_revision_is_error(self) -> None:
        plan = make_plan()
        plan["voiceover_lock"]["sha256"] = "0" * 64
        report = validate_production_plan(plan, PROJECT)
        assert "VOICEOVER_HASH_CHANGED" in report.error_codes

    def _write_voiceover_source(self, text: str) -> Path:
        source = PROJECT / "00_项目管理" / "voiceover-src.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(text.encode("utf-8"))
        return source

    def test_voiceover_hash_lf_crlf_equivalent(self) -> None:
        """LF 与 CRLF 内容等价：换行差异不得触发 VOICEOVER_HASH_CHANGED（跨环境检出修复）。"""
        text = "我是你的毛孔。\n补水就是抛光。\n"
        source = self._write_voiceover_source(text)
        lock = _voiceover_lock()
        lock["source_path"] = "00_项目管理/voiceover-src.txt"
        # 权威记录为归一化哈希（hash_mode=normalized_text_v1）。
        lock["sha256"] = hashlib.sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()
        lock["hash_mode"] = "normalized_text_v1"
        plan = make_plan(voiceover_lock=lock)

        report_lf = validate_production_plan(plan, PROJECT)
        assert "VOICEOVER_HASH_CHANGED" not in report_lf.error_codes
        source.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        report_crlf = validate_production_plan(plan, PROJECT)
        assert "VOICEOVER_HASH_CHANGED" not in report_crlf.error_codes

    def test_voiceover_hash_content_change_still_detected(self) -> None:
        """归一化后仍能检出真实内容变化，不过度放宽门禁。"""
        text = "我是你的毛孔。\n补水就是抛光。\n"
        self._write_voiceover_source(text)
        lock = _voiceover_lock()
        lock["source_path"] = "00_项目管理/voiceover-src.txt"
        lock["sha256"] = hashlib.sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()
        lock["hash_mode"] = "normalized_text_v1"
        plan = make_plan(voiceover_lock=lock)

        self._write_voiceover_source("我是你的毛孔。\n补水就是抛光。加一句。\n")
        report = validate_production_plan(plan, PROJECT)
        assert "VOICEOVER_HASH_CHANGED" in report.error_codes

    def test_voiceover_hash_legacy_record_tolerates_crlf_checkout(self) -> None:
        """legacy 记录（无 hash_mode，按仓库 LF 规范记录原始哈希）在 CRLF 检出下仍通过。"""
        text = "我是你的毛孔。\n补水就是抛光。\n"
        # legacy 记录 = LF 原始字节哈希（与当前仓库/夹具的记录方式一致）。
        lock = _voiceover_lock()
        lock["source_path"] = "00_项目管理/voiceover-src.txt"
        lock["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        plan = make_plan(voiceover_lock=lock)
        # 干净检出后磁盘文件为 CRLF。
        self._write_voiceover_source(text.replace("\n", "\r\n"))
        report = validate_production_plan(plan, PROJECT)
        assert "VOICEOVER_HASH_CHANGED" not in report.error_codes

    def test_voiceover_hash_bom_insensitive(self) -> None:
        """带 UTF-8 BOM 的相同文本不触发 VOICEOVER_HASH_CHANGED（BOM 属编码伪差异）。"""
        text = "我是你的毛孔。\n补水就是抛光。\n"
        self._write_voiceover_source(text)
        lock = _voiceover_lock()
        lock["source_path"] = "00_项目管理/voiceover-src.txt"
        lock["sha256"] = hashlib.sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()
        lock["hash_mode"] = "normalized_text_v1"
        plan = make_plan(voiceover_lock=lock)

        # Windows 记事本/.NET/Excel 导出常见的「带 BOM + CRLF」形态。
        self._write_voiceover_source(b"\xef\xbb\xbf".decode("utf-8") + text.replace("\n", "\r\n"))
        report = validate_production_plan(plan, PROJECT)
        assert "VOICEOVER_HASH_CHANGED" not in report.error_codes

    def test_raw_duration_over_target_is_warning(self) -> None:
        plan = make_plan()
        plan["segments"][0]["duration_seconds"] = 25
        plan["segments"][1]["duration_seconds"] = 25
        report = validate_production_plan(plan, PROJECT)
        assert "RAW_EXCEEDS_TARGET_DURATION" in report.warning_codes

    def test_product_segment_with_temp_ref_dir_is_acceptable(self) -> None:
        """产品段引用 06_参考帧与临时素材 下的文件，不再误报缺产品参考图。"""
        temp_ref = PROJECT / "06_参考帧与临时素材" / "demo-ref.png"
        temp_ref.parent.mkdir(parents=True, exist_ok=True)
        temp_ref.write_bytes(b"demo")
        plan = make_plan(
            segments=[
                {
                    "id": "V01", "order": 1, "voiceover_line_ids": ["L01"], "event_id": "intro",
                    "shot_strategy": ["close_up"], "start_state": "a", "end_state": "b",
                    "reference_images": [], "prompt_text": "纯机制画面，无口型同步。",
                    "product_appearance": False, "status": "current", "duration_seconds": 6,
                },
                {
                    "id": "V02", "order": 2, "voiceover_line_ids": [], "event_id": "product_hero",
                    "shot_strategy": ["wide_macro"], "start_state": "b", "end_state": "product_hero",
                    "reference_images": ["06_参考帧与临时素材/demo-ref.png"],
                    "prompt_text": "产品英雄，无台词。", "product_appearance": True,
                    "status": "current", "duration_seconds": 6,
                },
            ]
        )
        report = validate_production_plan(plan, PROJECT)
        assert "PRODUCT_NOT_AT_END" not in report.error_codes

    def test_missing_product_directory_reference_still_fails(self) -> None:
        plan = plan_with_order(["PRODUCT"])
        missing = _PRODUCT_REF.parent / "SYNTHETIC-definitely-missing.png"
        assert not missing.exists()
        plan["segments"][0]["reference_images"] = [str(missing)]
        report = validate_production_plan(plan, PROJECT)
        assert "REFERENCE_FILE_MISSING" in report.error_codes

    def test_product_reference_inputs_field_is_acceptable(self) -> None:
        plan = make_plan()
        plan["segments"].append(
            {
                "id": "V99", "order": 99, "voiceover_line_ids": [], "event_id": "product_hero",
                "shot_strategy": ["wide_macro"], "start_state": "hydrated", "end_state": "product_hero",
                "reference_inputs": [{"path": str(_PRODUCT_REF), "type": "image", "role": "product"}],
                "prompt_text": "产品英雄，无台词。", "product_appearance": True,
                "status": "current", "duration_seconds": 6,
            }
        )
        report = validate_production_plan(plan, PROJECT)
        assert "PRODUCT_NOT_AT_END" not in report.error_codes

    def test_manifest_duplicate_current_is_error(self) -> None:
        """清单中同一 logical_id 双 current → DUPLICATE_CURRENT_LOGICAL_ID（不再只看段）。"""
        manage = PROJECT / "00_项目管理"
        manage.mkdir(parents=True, exist_ok=True)
        manifest_path = manage / "artifact_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "job_id": "",
                    "artifacts": [
                        {"artifact_id": "video.V01.v1", "logical_id": "video.V01", "status": "current"},
                        {"artifact_id": "video.V01.v2", "logical_id": "video.V01", "status": "current"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        try:
            report = validate_production_plan(make_plan(), PROJECT)
            assert "DUPLICATE_CURRENT_LOGICAL_ID" in report.error_codes
        finally:
            manifest_path.unlink(missing_ok=True)

    def test_voiceover_source_missing_is_reference_error(self) -> None:
        """口播源 source_path 指向不存在的文件 → REFERENCE_FILE_MISSING。"""
        plan = make_plan()
        plan["voiceover_lock"]["source_path"] = "00_项目管理/nonexistent-voiceover.wav"
        report = validate_production_plan(plan, PROJECT)
        assert "REFERENCE_FILE_MISSING" in report.error_codes

    def test_empty_audio_policy_is_warning_not_silent(self) -> None:
        """audio_policy 为空 → WARN AUDIO_POLICY_UNSPECIFIED，不再静默关掉口型门禁。"""
        report = validate_production_plan(make_plan(audio_policy=""), PROJECT)
        assert "AUDIO_POLICY_UNSPECIFIED" in report.warning_codes

    def test_cli_reports_utf8_chinese_pass_message(self) -> None:
        """CLI 在无 BOM 环境强制 UTF-8 输出，中文 PASS 文案不得因 GBK 控制台乱码。"""
        plan_dir = PROJECT / "00_项目管理"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plan_dir / "production_plan.json"
        plan_file.write_text(json.dumps(make_plan(), ensure_ascii=False), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate-production-plan.py"), "--project-path", str(PROJECT), "--mode", "shadow"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("PASS(有警告)", result.stdout)

    def test_legacy_schema_is_migration_warning_in_shadow_and_error_in_hard_mode(self) -> None:
        self.assertIn("LEGACY_SCHEMA_MIGRATION_REQUIRED", validate_production_plan(make_plan(), PROJECT, mode="shadow").warning_codes)
        self.assertIn("LEGACY_SCHEMA_MIGRATION_REQUIRED", validate_production_plan(make_plan(), PROJECT, mode="enforce-hard").error_codes)

    def test_style_aware_fixture_rejects_lost_source_budget_and_static_risk(self) -> None:
        plan = make_plan(schema_version="3.1", production_mode="narrative_dialogue", editing_model="hybrid", dialogue_mode="visible_dialogue", style_requirements=["cinematic", "multiple_framings", "non_livestream"], source_shots=[{"shot_id": f"K{i:02d}"} for i in range(1, 14)])
        plan["segments"] = [{"id": "V02", "order": 1, "event_id": "merged", "source_event_ids": ["E1", "E2", "E3"], "source_shot_ids": [f"K{i:02d}" for i in range(1, 14)], "shot_strategy": ["wide"], "start_state": "a", "end_state": "b", "status": "current", "duration_seconds": 24, "space_switches": 3, "main_actions": ["a", "b", "c", "d"], "time_budget": {"total_seconds": 24, "visible_dialogue_seconds": 12, "serial_action_seconds": 10, "transition_seconds": 4, "result_hold_seconds": 2}, "shot_contracts": [{"shot_id": "V02-S01", "source_shot_id": "K01", "time_range": [0, 24], "purpose": "压缩", "must_show": ["a"], "start_framing": "正面", "end_framing": "正面", "camera_policy": "固定镜头", "subject_actions": ["a"], "entry_transition": {"type": "match"}, "exit_transition": {"type": "match"}, "continuity_requirements": ["a"], "control_source": "文字", "qc_expectations": ["a"]}], "coverage_decisions": [{"source_shot_id": "K01", "decision": "retained", "reason": "示例"}], "prompt_text": "无台词"}]
        report = validate_production_plan(plan, PROJECT, mode="enforce-hard")
        self.assertIn("SOURCE_CONTRACT_LOST", report.error_codes)
        self.assertIn("SEGMENT_TIME_BUDGET_EXCEEDED", report.error_codes)
        self.assertIn("TRANSITION_PAIR_INCOMPLETE", report.error_codes)
        self.assertIn("STATIC_TALKING_DEMO_RISK", report.error_codes)

    def test_legal_profiles_keep_fixed_talking_head_single_take_product_mechanism_and_experimental_paths(self) -> None:
        def plan_for(mode: str, editing: str = "continuous_take") -> dict[str, object]:
            end_state = "done"
            contract = {"shot_id": "S01", "source_shot_id": "K01", "time_range": [0, 8], "purpose": "验证", "must_show": [end_state], "start_framing": "中景", "end_framing": "中景", "camera_policy": "固定镜头", "camera_start": "中景", "camera_path": "稳定路径", "camera_end": "中景", "subject_actions": ["演示"], "entry_transition": "none", "exit_transition": "none", "continuity_requirements": ["连续"], "control_source": "预演", "qc_expectations": ["因果步骤可见"]}
            if mode == "single_take":
                contract.update({"subject_path": "人物从门口走到桌边", "focus_transitions": ["人物到道具"], "internal_beats": ["进入", "停顿", "完成动作"]})
            if mode == "product_demo": contract["must_show"] = [end_state, "产品操作状态"]
            return make_plan(schema_version="3.1", production_mode=mode, editing_model=editing, dialogue_mode="none", source_shots=[{"shot_id": "K01"}], segments=[{"id": "V01", "order": 1, "event_id": "E01", "source_event_ids": ["E01"], "source_shot_ids": ["K01"], "coverage_decisions": [{"source_shot_id": "K01", "decision": "retained", "reason": "保留"}], "shot_contracts": [contract], "time_budget": {"total_seconds": 8, "serial_action_seconds": 2, "result_hold_seconds": 1}, "shot_strategy": ["medium"], "start_state": "start", "end_state": end_state, "status": "current", "duration_seconds": 8, "prompt_text": "无台词"}])
        for mode in ("talking_head", "single_take", "product_demo", "mechanism_animation", "experimental_graphic", "documentary_observational"):
            report = validate_production_plan(plan_for(mode), PROJECT, mode="enforce-hard")
            self.assertEqual([], report.error_codes, f"{mode}: {report.error_codes}")
        montage = plan_for("montage", "editorial_montage")
        segment = montage["segments"][0]
        segment["source_shot_ids"].append("K02")
        segment["coverage_decisions"].append({"source_shot_id": "K02", "decision": "merged", "reason": "蒙太奇信息接力"})
        transition = {"type": "hard_cut", "out_framing": "中景", "in_framing": "近景", "direction": "主体保持画面左侧", "speed": "直接切换", "composition": "注意力由主体转到细节", "action_state": "第一项信息已经交付", "prop_state": "道具位置不变", "cut_condition": "第一项信息可读后切出", "arrival_state": "近景细节立即可读"}
        segment["shot_contracts"][0]["time_range"] = [0, 4]
        segment["shot_contracts"][0]["exit_transition"] = dict(transition)
        second = dict(segment["shot_contracts"][0], shot_id="S02", source_shot_id="K02", time_range=[4, 8], camera_policy="快速切换", entry_transition=dict(transition), exit_transition="none")
        segment["shot_contracts"].append(second)
        montage["source_shots"].append({"shot_id": "K02"})
        self.assertEqual([], validate_production_plan(montage, PROJECT, mode="enforce-hard").error_codes)

    def test_time_budget_does_not_double_count_hand_prop_window_inside_serial_action(self) -> None:
        plan = make_plan(schema_version="3.1", production_mode="talking_head", editing_model="continuous_take", dialogue_mode="none", source_shots=[{"shot_id": "K01"}], segments=[{"id": "V01", "order": 1, "event_id": "E01", "shot_strategy": ["medium"], "start_state": "start", "end_state": "done", "status": "current", "duration_seconds": 8, "coverage_decisions": [{"source_shot_id": "K01", "decision": "retained", "reason": "保留"}], "time_budget": {"total_seconds": 8, "serial_action_seconds": 5, "hand_prop_seconds": 5, "result_hold_seconds": 2, "edit_margin_seconds": 1}, "shot_contracts": [{"shot_id": "S01", "source_shot_id": "K01", "time_range": [0, 8], "purpose": "操作", "must_show": ["done"], "start_framing": "中景", "end_framing": "中景", "camera_policy": "固定", "subject_actions": ["拿起道具"], "entry_transition": "none", "exit_transition": "none", "continuity_requirements": ["连续"], "control_source": "预演", "qc_expectations": ["完成"]}], "prompt_text": "无台词"}])
        self.assertNotIn("SEGMENT_TIME_BUDGET_EXCEEDED", validate_production_plan(plan, PROJECT, mode="enforce-hard").error_codes)

    def test_end_state_components_can_be_covered_as_separate_must_show_items(self) -> None:
        plan = make_plan(schema_version="3.1", production_mode="talking_head", editing_model="continuous_take", dialogue_mode="none", source_shots=[{"shot_id": "K01"}], segments=[{"id": "V01", "order": 1, "event_id": "E01", "shot_strategy": ["medium"], "start_state": "start", "end_state": "hydration_ready; product_hero", "status": "current", "duration_seconds": 8, "coverage_decisions": [{"source_shot_id": "K01", "decision": "retained", "reason": "保留"}], "time_budget": {"total_seconds": 8}, "shot_contracts": [{"shot_id": "S01", "source_shot_id": "K01", "time_range": [0, 8], "purpose": "结果", "must_show": ["hydration_ready", "product_hero"], "start_framing": "中景", "end_framing": "中景", "camera_policy": "固定", "subject_actions": ["展示"], "entry_transition": "none", "exit_transition": "none", "continuity_requirements": ["连续"], "control_source": "预演", "qc_expectations": ["完成"]}], "prompt_text": "无台词"}])
        self.assertNotIn("REQUIRED_STATE_UNCOVERED", validate_production_plan(plan, PROJECT, mode="enforce-hard").error_codes)

    def test_single_take_requires_subject_path_focus_transitions_and_internal_beats(self) -> None:
        plan = make_plan(schema_version="3.1", production_mode="single_take", editing_model="continuous_take", dialogue_mode="none", source_shots=[{"shot_id": "K01"}], segments=[{"id": "V01", "order": 1, "event_id": "E01", "shot_strategy": ["single"], "start_state": "start", "end_state": "done", "status": "current", "duration_seconds": 8, "coverage_decisions": [{"source_shot_id": "K01", "decision": "retained", "reason": "保留"}], "time_budget": {"total_seconds": 8}, "shot_contracts": [{"shot_id": "S01", "source_shot_id": "K01", "time_range": [0, 8], "purpose": "长镜", "must_show": ["done"], "start_framing": "全景", "end_framing": "近景", "camera_policy": "连续", "camera_start": "门口", "camera_path": "跟随", "camera_end": "桌边", "subject_actions": ["行走"], "entry_transition": "none", "exit_transition": "none", "continuity_requirements": ["连续"], "control_source": "预演", "qc_expectations": ["完成"]}], "prompt_text": "无台词"}])
        self.assertIn("CAMERA_PLAN_MISSING", validate_production_plan(plan, PROJECT, mode="enforce-hard").error_codes)

    def test_shot_variety_is_checked_per_segment_not_across_whole_plan(self) -> None:
        plan = make_plan(schema_version="3.1", production_mode="montage", editing_model="editorial_montage", dialogue_mode="none", source_shots=[])
        segments = []
        for index in (1, 2):
            source_id = f"K{index:02d}"
            plan["source_shots"].append({"shot_id": source_id})
            segments.append({"id": f"V{index:02d}", "order": index, "event_id": f"E{index:02d}", "shot_strategy": ["montage"], "start_state": "start", "end_state": "done", "status": "current", "duration_seconds": 8, "coverage_decisions": [{"source_shot_id": source_id, "decision": "retained", "reason": "保留"}], "time_budget": {"total_seconds": 8}, "shot_contracts": [{"shot_id": f"S{index:02d}", "source_shot_id": source_id, "time_range": [0, 8], "purpose": "蒙太奇", "must_show": ["done"], "start_framing": "中景", "end_framing": "近景", "camera_policy": "切换", "subject_actions": ["动作"], "entry_transition": "none", "exit_transition": "none", "continuity_requirements": ["连续"], "control_source": "预演", "qc_expectations": ["完成"]}], "prompt_text": "无台词"})
        plan["segments"] = segments
        self.assertEqual(2, validate_production_plan(plan, PROJECT, mode="enforce-hard").error_codes.count("SHOT_VARIETY_INSUFFICIENT"))

    def test_approved_long_take_exception_is_valid_but_unjustified_exception_fails(self) -> None:
        plan = make_plan(schema_version="3.1", production_mode="montage", editing_model="editorial_montage", dialogue_mode="none", source_shots=[{"shot_id": "K01"}], intentional_exceptions=[{"kind": "long_take", "reason": "让动作完整发生", "scope": "V01", "acceptance_criteria": "路径与内部节拍均可辨", "approval_ref": "contract:R01"}], segments=[{"id": "V01", "order": 1, "event_id": "E01", "shot_strategy": ["long_take"], "start_state": "start", "end_state": "done", "status": "current", "duration_seconds": 8, "coverage_decisions": [{"source_shot_id": "K01", "decision": "retained", "reason": "保留"}], "time_budget": {"total_seconds": 8}, "shot_contracts": [{"shot_id": "S01", "source_shot_id": "K01", "time_range": [0, 8], "purpose": "长镜", "must_show": ["done"], "start_framing": "全景", "end_framing": "近景", "camera_policy": "连续", "subject_actions": ["动作"], "entry_transition": "none", "exit_transition": "none", "continuity_requirements": ["连续"], "control_source": "预演", "qc_expectations": ["完成"]}], "prompt_text": "无台词"}])
        self.assertNotIn("SHOT_VARIETY_INSUFFICIENT", validate_production_plan(plan, PROJECT, mode="enforce-hard").error_codes)
        plan["intentional_exceptions"][0]["reason"] = ""
        self.assertIn("INTENTIONAL_EXCEPTION_UNJUSTIFIED", validate_production_plan(plan, PROJECT, mode="enforce-hard").error_codes)

    def test_31_requires_complete_segment_registry(self) -> None:
        plan = make_plan(schema_version="3.1", production_mode="talking_head", editing_model="continuous_take", dialogue_mode="none", source_shots=[])
        plan.pop("segment_registry")
        self.assertIn("SEGMENT_REGISTRY_MISSING", validate_production_plan(plan, PROJECT, mode="enforce-hard").error_codes)

    def test_registry_makes_current_next_and_final_unambiguous(self) -> None:
        plan = make_plan(schema_version="3.1", production_mode="talking_head", editing_model="continuous_take", dialogue_mode="none", source_shots=[])
        plan["segment_registry"]["next_segment_id"] = "V99"
        self.assertIn("SEGMENT_POSITION_AMBIGUOUS", validate_production_plan(plan, PROJECT, mode="enforce-hard").error_codes)

    def test_same_scene_split_without_justification_is_blocked(self) -> None:
        plan = self._same_scene_split_plan()
        plan["segments"][1].pop("same_scene_split")
        self.assertIn("SAME_SCENE_SPLIT_UNJUSTIFIED", validate_production_plan(plan, PROJECT, mode="enforce-hard").error_codes)

    def test_duration_split_requires_user_confirmed_model_and_real_overflow(self) -> None:
        plan = self._same_scene_split_plan()
        plan["segments"][1]["same_scene_split"] = {
            "from_segment_id": "V01",
            "reason": "model_duration_exceeded",
            "evidence": "完整动作估算 31 秒",
            "estimated_scene_duration_seconds": 31,
        }
        plan["selected_video_model"] = {"id": "seedance2.5", "max_duration_seconds": 30, "confirmed_by_user": False}
        report = validate_production_plan(plan, PROJECT, mode="enforce-hard")
        self.assertIn("SAME_SCENE_SPLIT_MODEL_UNCONFIRMED", report.error_codes)
        plan["selected_video_model"]["confirmed_by_user"] = True
        self.assertNotIn("SAME_SCENE_SPLIT_UNJUSTIFIED", validate_production_plan(plan, PROJECT, mode="enforce-hard").error_codes)

    def test_same_scene_split_transition_state_must_match(self) -> None:
        plan = self._same_scene_split_plan()
        plan["segments"][1]["shot_contracts"][0]["entry_transition"]["speed"] = "快速"
        self.assertIn("SAME_SCENE_TRANSITION_MISMATCH", validate_production_plan(plan, PROJECT, mode="enforce-hard").error_codes)

    def test_core_load_never_justifies_same_scene_split_within_model_duration(self) -> None:
        plan = self._same_scene_split_plan()
        plan["segments"][1]["same_scene_split"] = {
            "from_segment_id": "V01",
            "reason": "high_core_acceptance_load",
            "evidence": "拿取、贴脸、连续擦拭和结果停留都是核心验收项，但完整场景仍只有 16 秒",
            "estimated_scene_duration_seconds": 16,
        }
        report = validate_production_plan(plan, PROJECT, mode="enforce-hard")
        self.assertIn("SAME_SCENE_SPLIT_UNJUSTIFIED", report.error_codes)

    def test_duration_overflow_split_with_paired_transition_passes(self) -> None:
        report = validate_production_plan(self._same_scene_split_plan(), PROJECT, mode="enforce-hard")
        self.assertNotIn("SAME_SCENE_SPLIT_UNJUSTIFIED", report.error_codes)
        self.assertNotIn("SAME_SCENE_TRANSITION_INCOMPLETE", report.error_codes)
        self.assertNotIn("SAME_SCENE_TRANSITION_MISMATCH", report.error_codes)

    def test_merged_director_shots_require_paired_internal_transition(self) -> None:
        plan = self._merged_internal_shots_plan()
        plan["segments"][0]["shot_contracts"][0]["exit_transition"] = "none"
        report = validate_production_plan(plan, PROJECT, mode="enforce-hard")
        self.assertIn("INTERNAL_TRANSITION_INCOMPLETE", report.error_codes)

    def test_merged_director_shots_with_paired_internal_transition_pass(self) -> None:
        report = validate_production_plan(self._merged_internal_shots_plan(), PROJECT, mode="enforce-hard")
        self.assertNotIn("INTERNAL_TRANSITION_INCOMPLETE", report.error_codes)
        self.assertNotIn("INTERNAL_TRANSITION_MISMATCH", report.error_codes)
        self.assertNotIn("INTERNAL_SHOT_TIMELINE_INVALID", report.error_codes)

    def test_merged_director_shots_preserve_source_order_and_fit_duration(self) -> None:
        plan = self._merged_internal_shots_plan()
        plan["segments"][0]["shot_contracts"].reverse()
        plan["segments"][0]["shot_contracts"][0]["time_range"] = [8, 17]
        report = validate_production_plan(plan, PROJECT, mode="enforce-hard")
        self.assertIn("INTERNAL_SHOT_SOURCE_ORDER_INVALID", report.error_codes)
        self.assertIn("INTERNAL_SHOT_TIMELINE_INVALID", report.error_codes)

    def _merged_internal_shots_plan(self) -> dict[str, object]:
        plan = self._same_scene_split_plan()
        left, right = plan["segments"]
        first = left["shot_contracts"][0]
        second = right["shot_contracts"][0]
        first["time_range"] = [0, 8]
        second["time_range"] = [8, 16]
        left["source_event_ids"] = ["E01", "E02"]
        left["source_shot_ids"] = ["K01", "K02"]
        left["coverage_decisions"] = [
            {"source_shot_id": "K01", "decision": "retained", "reason": "保留开场动作"},
            {"source_shot_id": "K02", "decision": "merged", "reason": "同场景连续动作，由同一次模型调用完成"},
        ]
        left["shot_contracts"] = [first, second]
        left["time_budget"] = {"total_seconds": 16, "serial_action_seconds": 12, "transition_seconds": 1, "result_hold_seconds": 2, "edit_margin_seconds": 1}
        left["duration_seconds"] = 16
        left["end_state"] = right["end_state"]
        left["prompt_text"] = "0-8 秒完成第一镜；动作切；8-16 秒完成第二镜；无台词。"
        plan["segments"] = [left]
        plan["segment_registry"] = {
            "ordered_segment_ids": ["V01"],
            "current_segment_id": "V01",
            "next_segment_id": None,
            "final_segment_id": "V01",
        }
        return plan

    def _same_scene_split_plan(self) -> dict[str, object]:
        transition = {
            "type": "action_match",
            "out_framing": "中近景右侧留白",
            "in_framing": "中近景右侧留白",
            "direction": "人物由左向右",
            "speed": "匀速",
            "composition": "人物位于左三分线",
            "action_state": "毛巾接触左脸颊",
            "prop_state": "毛巾由右手持握且已贴脸",
            "cut_condition": "毛巾刚接触脸颊时切出",
            "arrival_state": "从同一接触姿态继续擦拭",
        }

        def segment(segment_id: str, order: int, source_id: str, start: str, end: str) -> dict[str, object]:
            return {
                "id": segment_id,
                "order": order,
                "scene_id": "S01",
                "event_id": f"E{order:02d}",
                "source_event_ids": [f"E{order:02d}"],
                "source_shot_ids": [source_id],
                "coverage_decisions": [{"source_shot_id": source_id, "decision": "retained", "reason": "保留连续动作"}],
                "shot_contracts": [{
                    "shot_id": f"{segment_id}-S01",
                    "source_shot_id": source_id,
                    "time_range": [0, 8],
                    "purpose": "完成连续动作",
                    "must_show": [end],
                    "start_framing": "中近景",
                    "end_framing": "中近景",
                    "camera_policy": "稳定跟随",
                    "subject_actions": ["连续擦拭"],
                    "entry_transition": dict(transition) if order == 2 else "none",
                    "exit_transition": dict(transition) if order == 1 else "none",
                    "continuity_requirements": ["方向、速度、构图、动作与道具状态连续"],
                    "control_source": "导演预演",
                    "qc_expectations": ["连续动作无跳变"],
                }],
                "time_budget": {"total_seconds": 8, "serial_action_seconds": 5, "result_hold_seconds": 1},
                "shot_strategy": ["medium"],
                "start_state": start,
                "end_state": end,
                "status": "current",
                "duration_seconds": 8,
                "prompt_text": "无台词",
            }

        segments = [segment("V01", 1, "K01", "准备擦脸", "毛巾接触左脸颊"), segment("V02", 2, "K02", "毛巾接触左脸颊", "擦拭完成")]
        segments[1]["same_scene_split"] = {
            "from_segment_id": "V01",
            "reason": "model_duration_exceeded",
            "evidence": "完整场景估算 31 秒，超过用户已确认模型 30 秒上限",
            "estimated_scene_duration_seconds": 31,
        }
        return make_plan(
            schema_version="3.1",
            production_mode="talking_head",
            editing_model="continuous_take",
            dialogue_mode="none",
            source_shots=[{"shot_id": "K01"}, {"shot_id": "K02"}],
            selected_video_model={"id": "seedance2.5", "max_duration_seconds": 30, "confirmed_by_user": True},
            segments=segments,
        )


class TestV32DurationDecomposition(unittest.TestCase):
    """3.2 时长自洽：模型按内容语义填的四段分解须自洽，并对白秒数须与对白登记吻合。"""

    def _registered(self) -> dict[str, dict[str, object]]:
        return {
            "DLG-001": {"dialogue_id": "DLG-001", "natural_duration_seconds": 4.0},
            "DLG-002": {"dialogue_id": "DLG-002", "natural_duration_seconds": 5.0},
        }

    def _segment(self, decomposition: dict[str, object], declared: float) -> dict[str, object]:
        return {
            "segment_id": "SEG-01",
            "source_dialogue_ids": ["DLG-001", "DLG-002"],
            "planned_generation_duration_seconds": declared,
            "duration_decomposition": decomposition,
        }

    def test_consistent_decomposition_no_warn(self) -> None:
        warnings: list[PreflightIssue] = []
        _check_v32_duration_decomposition(
            [self._segment({"dialogue_seconds": 9, "action_seconds": 2, "establishing_seconds": 1, "transition_seconds": 1, "rationale": "对白9s+动作2s+空镜1s+转场1s"}, 13)],
            self._registered(),
            warnings,
        )
        self.assertEqual([], [w.code for w in warnings])

    def test_declared_less_than_component_sum_warns(self) -> None:
        warnings: list[PreflightIssue] = []
        _check_v32_duration_decomposition(
            [self._segment({"dialogue_seconds": 9, "action_seconds": 5, "establishing_seconds": 3, "transition_seconds": 2, "rationale": "对白9+动作5+空镜3+转场2=19"}, 13)],
            self._registered(),
            warnings,
        )
        self.assertIn("DURATION_DECOMPOSITION_INCONSISTENT", [w.code for w in warnings])

    def test_stated_dialogue_mismatch_register_warns(self) -> None:
        warnings: list[PreflightIssue] = []
        _check_v32_duration_decomposition(
            [self._segment({"dialogue_seconds": 3, "action_seconds": 2, "establishing_seconds": 0, "transition_seconds": 0, "rationale": "对白误算为3s（真实9s）"}, 13)],
            self._registered(),
            warnings,
        )
        codes = [w.code for w in warnings]
        self.assertIn("DURATION_DECOMPOSITION_DIALOGUE_MISMATCH", codes)
        self.assertNotIn("DURATION_DECOMPOSITION_INCONSISTENT", codes)

    def test_absent_decomposition_is_skipped(self) -> None:
        """可选字段：旧计划没有 duration_decomposition 就不检查、不误报。"""
        segment = {"segment_id": "SEG-01", "source_dialogue_ids": ["DLG-001"], "planned_generation_duration_seconds": 13}
        warnings: list[PreflightIssue] = []
        _check_v32_duration_decomposition([segment], self._registered(), warnings)
        self.assertEqual([], [w.code for w in warnings])

    def test_dialogue_action_overlap_preserves_full_dialogue_duration(self) -> None:
        decomposition = {"dialogue_seconds": 9, "action_seconds": 5,
                         "establishing_seconds": 1, "transition_seconds": 1,
                         "dialogue_action_overlap_seconds": 4,
                         "rationale": "按导演包，第二句对白中的4秒同时走到桌边，其余动作单独完成"}
        warnings: list[PreflightIssue] = []
        _check_v32_duration_decomposition([self._segment(decomposition, 12)], self._registered(), warnings)
        self.assertEqual([], warnings)
        decomposition.pop("dialogue_action_overlap_seconds")
        _check_v32_duration_decomposition([self._segment(decomposition, 12)], self._registered(), warnings)
        self.assertIn("DURATION_DECOMPOSITION_INCONSISTENT", [w.code for w in warnings])

    def test_invalid_overlap_cannot_hide_overloaded_segment(self) -> None:
        for overlap in (-1, 6):
            with self.subTest(overlap=overlap):
                warnings: list[PreflightIssue] = []
                decomposition = {"dialogue_seconds": 9, "action_seconds": 5,
                                 "establishing_seconds": 1, "transition_seconds": 1,
                                 "dialogue_action_overlap_seconds": overlap, "rationale": "错误重叠"}
                _check_v32_duration_decomposition([self._segment(decomposition, 9)], self._registered(), warnings)
                self.assertIn("DURATION_OVERLAP_INVALID", [w.code for w in warnings])
                self.assertIn("DURATION_DECOMPOSITION_INCONSISTENT", [w.code for w in warnings])


if __name__ == "__main__":
    unittest.main()
