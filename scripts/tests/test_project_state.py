from scripts.core.artifacts import artifact_is_confirmed
from scripts.core.project_state import new_project_state, normalize_project_state, render_project_state_markdown
from scripts.core.project_state import reconcile_current_artifacts
from scripts.core.project_state import validate_project_state


def test_final_edit_receipt_does_not_invent_a_file_or_confirm_raw_artifacts() -> None:
    state = new_project_state(current_stage="completed")
    state["user_confirmations"] = [{
        "kind": "final-edit-confirmation", "status": "user-confirmed", "scope": "artifact",
        "record_path": "00_项目管理/实施记录.md", "final_output_path": None,
    }]
    result = render_project_state_markdown(state)
    assert "completed" in result
    assert "用户已确认当前剪辑版" in result
    assert "用户未提供准确路径" in result
    assert "00_项目管理/实施记录.md" in result
    assert not artifact_is_confirmed({
        "artifact_id": "ART-RAW-VIDEO", "status": "current", "user_approved": True,
    }, state)


def test_platform_success_does_not_imply_final_edit_confirmation() -> None:
    state = new_project_state(current_stage="voiceover-to-video-script")
    state["user_confirmations"] = [{"kind": "authorized-video-execution", "status": "generated"}]
    assert "最终剪辑：" not in render_project_state_markdown(state)


def test_final_edit_display_preserves_existing_finalization_fields() -> None:
    state = new_project_state(current_stage="completed")
    state["project_status"] = "finalized"
    state["finalization"] = {"record_path": "00_项目管理/项目定稿记录.md", "assembly_status": "user_completed"}
    state["user_confirmations"] = [{
        "kind": "final-edit-confirmation", "status": "user-confirmed", "scope": "artifact",
        "record_path": "00_项目管理/实施记录.md", "final_output_path": None,
    }]
    normalized = normalize_project_state(state)
    assert normalized["finalization"] == state["finalization"]
    result = render_project_state_markdown(normalized)
    assert "项目状态：finalized" in result
    assert "定稿记录：00_项目管理/项目定稿记录.md" in result
    assert "用户已确认当前剪辑版" in result


def test_reconcile_replaces_selected_baseline_without_selecting_other_current_artifacts():
    state = new_project_state(current_stage="storyboard-planner")
    state.update(current_artifacts=["OLD", "STALE"],
                 user_confirmations=[{"artifact_ids": ["OLD", "STALE"]}])
    manifest = {"artifacts": [
        {"artifact_id": "OLD", "status": "superseded"},
        {"artifact_id": "NEW", "status": "current"},
        {"artifact_id": "STALE", "status": "stale"},
        {"artifact_id": "UNSELECTED", "status": "current"},
    ]}
    result = reconcile_current_artifacts(state, manifest, replacements={"OLD": "NEW"})
    assert result["current_artifacts"] == ["NEW"]
    assert result["user_confirmations"] == state["user_confirmations"]
    assert result["current_stage"] == state["current_stage"]
    assert state["current_artifacts"] == ["OLD", "STALE"]


def test_workflow_authorizations_preserve_user_confirmations_and_legacy_reading():
    state = new_project_state()
    human = {"scope": "artifact", "artifact_ids": ["OLD"], "approval_ref": "SYNTHETIC ONLY"}
    grant = {"id": "SYNTHETIC-FLOW", "workflow": "review-to-canvas", "status": "active"}
    state.update(user_confirmations=[human], workflow_authorizations=[grant])
    for version in ("stage-1.0", "legacy-synthetic"):
        value = {**state, "schema_version": version}
        normalized = normalize_project_state(value)
        assert normalized["workflow_authorizations"] == [grant]
        assert normalized["user_confirmations"] == [human]
        validate_project_state(normalized)
        assert "流程授权：1" in render_project_state_markdown(normalized)
    old = new_project_state()
    old.pop("workflow_authorizations")
    validate_project_state(old)
    assert normalize_project_state(old)["workflow_authorizations"] == []
