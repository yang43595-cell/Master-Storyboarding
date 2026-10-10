"""用户确认记录范围权威（core.confirmation_scope）的自动验收。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 本文件可单独运行：scripts/ 是 core 包的父目录。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import confirmation_scope as scope
from core import revision_control
from core.artifacts import artifact_is_confirmed


@pytest.mark.parametrize("receipt,expected_kind,whole", [
    ({"artifact_ids": ["ART-X"]}, scope.KIND_UNSCOPED, True),
    ({"scope": "artifact", "artifact_ids": ["ART-X"]}, scope.KIND_ARTIFACT, True),
    ({"scope": "artifact", "scope_description": "整稿", "artifact_ids": ["ART-X"]}, scope.KIND_ARTIFACT, True),
    ({"scope": "artifact", "shot_ids": ["S01-001"]}, scope.KIND_ARTIFACT, False),
    ({"scope": "segment", "shot_ids": ["S01-001"]}, scope.KIND_SEGMENT, False),
    ({"scope": "segment", "scope_description": "第03段"}, scope.KIND_SEGMENT, False),
    ({"scope": "dialogue", "dialogue_ids": ["DLG-008"]}, scope.KIND_DIALOGUE, False),
    ({"scope": "确认第一场拍法"}, scope.KIND_LEGACY_TEXT, False),
    ({"kind": "dialogue-confirmation", "artifact_ids": ["ART-X"]}, scope.KIND_DIALOGUE, False),
    (None, scope.KIND_LEGACY_TEXT, False),
])
def test_classify_is_fail_closed(receipt, expected_kind, whole):
    classification = scope.classify(receipt)
    assert classification.kind == expected_kind
    assert classification.is_whole_artifact is whole
    assert scope.is_whole_artifact(receipt) is whole


@pytest.mark.parametrize("receipt,expected", [
    ({"scope": "artifact"}, []),
    ({"scope": "segment", "shot_ids": ["S01-001"]}, []),
    ({"scope": "segment"}, ["段落确认必须列出该段实际编号（至少一个局部 id 字段）"]),
    ({"scope": "artifact", "shot_ids": ["S01-001"]}, ["整稿确认不得携带局部编号字段：['shot_ids']"]),
    ({"scope": "dialogue", "shot_ids": ["S01-001"]}, ["对白确认必须列出 dialogue_ids"]),
    ({"scope": "artifact", "scope_description": "  "}, ["scope_description 若存在必须是非空字符串"]),
    ({"scope": "确认第一场"}, ["scope 必须是 ['artifact', 'segment', 'dialogue'] 之一，当前为 '确认第一场'"]),
    ({"scope": "segment", "shot_ids": []}, [
        "shot_ids 必须是非空的非空字符串数组",
        "段落确认必须列出该段实际编号（至少一个局部 id 字段）",
    ]),
])
def test_problems_for_write(receipt, expected):
    problems = scope.problems_for_write(receipt)
    assert problems == expected


def test_new_receipt_refuses_illegal_scope():
    assert scope.new_receipt("segment", description="第三段", extra={"shot_ids": ["S01-011"]})["scope"] == "segment"
    with pytest.raises(ValueError):
        scope.new_receipt("segment", description="没有编号的段落")
    with pytest.raises(ValueError):
        scope.new_receipt("artifact", extra={"shot_ids": ["S01-001"]})
    with pytest.raises(ValueError):
        scope.new_receipt("确认第一场")


def test_artifact_is_confirmed_uses_the_authority():
    entry = {"artifact_id": "ART-X", "status": "current", "user_approved": True}
    whole = {"artifact_ids": ["ART-X"]}
    segment = {"artifact_ids": ["ART-X"], "scope": "segment", "shot_ids": ["S01-001"]}
    assert artifact_is_confirmed(entry, {"user_confirmations": [whole]})
    assert not artifact_is_confirmed(entry, {"user_confirmations": [segment]})
    assert artifact_is_confirmed(entry, {"user_confirmations": [segment, whole]})


def test_audit_project_separates_errors_from_legacy_text(tmp_path: Path):
    manage = tmp_path / "00_项目管理"
    manage.mkdir(parents=True)
    (manage / "project_state.json").write_text(json.dumps({
        "schema_version": "stage-1.0",
        "user_confirmations": [
            {"artifact_ids": ["ART-A"]},
            {"artifact_ids": ["ART-B"], "scope": "确认第二场"},
            {"artifact_ids": ["ART-C"], "scope": "segment", "shot_ids": ["S01-002"]},
            {"artifact_ids": ["ART-D"], "scope": {"bad": "type"}},
            {"artifact_ids": ["ART-E"], "scope": "artifact", "shot_ids": "S01-003"},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    report = scope.audit_project(tmp_path)

    assert report["total"] == 5
    assert report["by_kind"][scope.KIND_ARTIFACT] == 1
    assert report["by_kind"][scope.KIND_UNSCOPED] == 1
    assert report["by_kind"][scope.KIND_SEGMENT] == 1
    # 非字符串 scope 与中文旧 scope 都归为"不是范围类别"，但只有后者是待整理的旧记录
    assert report["by_kind"][scope.KIND_LEGACY_TEXT] == 2
    assert [item["artifact_ids"] for item in report["whole_artifact_targets"]] == [["ART-A"]]
    assert [item["where"] for item in report["notes"]] == ["project_state.user_confirmations[1]"]
    messages = [item["message"] for item in report["problems"]]
    assert "scope 不是字符串" in messages
    assert "shot_ids 不是数组" in messages


def _project_with_active_change(tmp_path: Path) -> Path:
    project = tmp_path / "项目"
    (project / "00_项目管理").mkdir(parents=True)
    candidate = project / "candidate.md"
    candidate.write_text("storyboard candidate", encoding="utf-8")
    (project / "00_项目管理" / "artifact_manifest.json").write_text(json.dumps({
        "schema_version": "1.0",
        "artifacts": [{
            "artifact_id": "ART-STORYBOARD-R01", "logical_id": "storyboard.package",
            "type": "storyboard-package", "revision": "R01", "status": "current",
            "path_or_url": "candidate.md",
            "depends_on": [], "user_approved": True,
            "checksum": revision_control.sha256_file(candidate),
        }],
    }, ensure_ascii=False), encoding="utf-8")
    revision_control.open_change(
        project, change_id="REV-1", tier="T0", logical_ids=["storyboard.package"], reason="逐段修订"
    )
    return project


def test_scoped_confirmation_is_recorded_but_never_promotes(tmp_path: Path):
    project = _project_with_active_change(tmp_path)

    receipt = revision_control.record_scoped_confirmation(
        project, change_id="REV-1", scope=scope.SCOPE_SEGMENT,
        approval_ref="user:segment-1", scope_description="第03段拍法",
        scoped_ids={"shot_ids": ["S01-011", "S01-012"]},
    )

    assert receipt["scope"] == "segment"
    state = json.loads((project / "00_项目管理" / "project_state.json").read_text(encoding="utf-8"))
    assert state["user_confirmations"] == [receipt]
    control = json.loads((project / "00_项目管理" / "change_control.json").read_text(encoding="utf-8"))
    assert control["active_change"]["scoped_confirmations"] == [receipt]
    assert control["active_change"]["status"] != "commit_ready"
    entry = {"artifact_id": "ART-STORYBOARD-R01", "status": "current", "user_approved": True}
    assert not artifact_is_confirmed(entry, state)


def test_scoped_confirmation_cannot_commit_a_change(tmp_path: Path):
    project = _project_with_active_change(tmp_path)
    receipt = revision_control.record_scoped_confirmation(
        project, change_id="REV-1", scope=scope.SCOPE_DIALOGUE,
        approval_ref="user:dialogue", scoped_ids={"dialogue_ids": ["DLG-008"]},
    )
    control_path = project / "00_项目管理" / "change_control.json"
    control = json.loads(control_path.read_text(encoding="utf-8"))
    control["active_change"]["status"] = "commit_ready"
    control["active_change"]["candidate_confirmation"] = receipt
    control["active_change"]["candidate_receipt"] = {
        "path": "candidate.md", "sha256": revision_control.sha256_file(project / "candidate.md"),
        "artifact_id": "ART-STORYBOARD-R01", "logical_id": "storyboard.package",
    }
    control_path.write_text(json.dumps(control, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(revision_control.RevisionControlError, match="整稿范围"):
        revision_control.commit_change(project, change_id="REV-1")


def test_scoped_confirmation_rejects_illegal_scope(tmp_path: Path):
    project = _project_with_active_change(tmp_path)
    with pytest.raises(revision_control.RevisionControlError):
        revision_control.record_scoped_confirmation(
            project, change_id="REV-1", scope="artifact", approval_ref="user:x"
        )
