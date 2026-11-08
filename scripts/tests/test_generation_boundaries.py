"""Synthetic declarations only; no real model capability or project approval."""
import copy
import hashlib
import json

import pytest

from scripts.core.generation_boundaries import (
    main, validate_delivery_cutpoints, validate_delivery_parameters, validate_generation_boundaries,
)
from scripts.core.quality_evidence import required_visuals, validate_required_visuals, validate_scene_source_mapping


@pytest.mark.parametrize(("case", "issue"), [
    ("covered", None),
    ("two_states", "同段多个持久状态"),
    ("old_layout", "旧态/基础环境图"),
    ("partial_use", "新场景状态图待制作"),
    ("state_starts_mid_segment", "同段含变化前后镜号"),
])
def test_cli_reports_planned_state_coverage_before_canvas(tmp_path, capsys, case, issue):
    shots = ["S1", "S2"]
    state = dict(state_id="NEW", entity_id="TABLE", previous_state_id="EMPTY",
                 change_event="Set down the box", description="Box on the table",
                 preserve=["table"], changes=["box location"], source_shot_ids=shots)
    ref = dict(path="planned.png", entity_ids=["TABLE"], state_id="NEW",
               roles=["state_reference"], source_shot_ids=shots)
    states, refs = [state], [ref]
    if case == "two_states":
        states = [{**state, "source_shot_ids": ["S1"]},
                  {**state, "state_id": "NEXT", "previous_state_id": "NEW",
                   "source_shot_ids": ["S2"]}]
    elif case == "old_layout":
        refs.append(dict(path="old.png", entity_ids=["TABLE"], roles=["layout"], state_id="EMPTY"))
    elif case == "partial_use":
        ref["source_shot_ids"] = ["S1"]
    elif case == "state_starts_mid_segment":
        state["source_shot_ids"] = ["S2"]

    director = tmp_path / "director.json"
    director.write_text(json.dumps({"scenes": [{"scene_id": "ROOM",
        "shots": [{"shot_id": shot} for shot in shots], "scene_states": states}]}))
    prompt = tmp_path / "video.json"
    prompt.write_text(json.dumps({"generation_segments": [
        {"segment_id": "V01", "source_shot_ids": shots, "candidate_upload_order": refs}]}))
    assert main(["--project", str(tmp_path), "--prompt", str(prompt), "--storyboard", str(director)]) == 0
    report = json.loads(capsys.readouterr().out)
    result, = report["scene_state_plan"]["segments"]
    assert result["segment_id"] == "V01"
    if issue:
        assert result["status"] == "needs_review"
        assert issue in result["issue"]
    else:
        assert result["status"] == "covered"


def fixture(tmp_path, maximum=18):
    evidence = tmp_path / "synthetic-capability.txt"
    evidence.write_text("Synthetic test observation, not a real platform capability.")
    proof = dict(path=evidence.name, sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
                 locator="synthetic fixture", checked_at="2026-01-01T00:00:00Z")
    board = {"scenes": [{"scene_id": "ROOM", "shots": [
        {"shot_id": f"S{i}", "cut": {"exit": f"Synthetic boundary {i}"}} for i in range(1, 4)]}]}
    rows = [dict(segment_id=f"G{i}", prompt=f"Synthetic prompt {i}", source_shot_ids=[f"S{i}"],
                 duration_seconds=9, materials=[]) for i in range(1, 4)]
    for index, row in enumerate(rows[1:], 1):
        state, sha = f"STATE{index}", str(index) * 64
        ref = dict(sha256=sha, state_id=state, role="state_reference")
        rows[index - 1]["materials"].append(copy.deepcopy(ref))
        row["materials"].append(copy.deepcopy(ref))
        row["same_scene_split"] = dict(from_segment_id=f"G{index}", reason="model_duration_exceeded",
            evidence="Synthetic content exceeds this test model; use the director's existing boundary.",
            estimated_scene_duration_seconds=27, timing_basis="Synthetic natural dialogue/action estimate.",
            source_boundary=dict(shot_id=f"S{index}", field="cut.exit", quote=f"Synthetic boundary {index}"))
        row["continuity"] = dict(previous_segment_id=f"G{index}", boundary=dict(state_id=state,
            description="Synthetic same actor and prop state, next action has not happened.",
            previous_sha256s=[sha], current_sha256s=[sha]))
    payload = dict(generation_segments=rows, source_storyboard=dict(path="board.json"),
        platform=dict(model="synthetic-model", reference_mode="synthetic-multi-reference"),
        selected_video_model=dict(id="synthetic-model", platform="synthetic-platform", entry_point="synthetic-ui",
            reference_mode="synthetic-multi-reference", confirmed_by_user=True,
            max_duration_seconds=maximum, capability_evidence=proof))
    return payload, board


def test_same_scene_without_reason_is_rejected_even_with_three_short_clips(tmp_path):
    payload, board = fixture(tmp_path)
    for row in payload["generation_segments"]:
        row.pop("same_scene_split", None)
    with pytest.raises(ValueError, match="同场景默认合并"):
        validate_generation_boundaries(payload, board, tmp_path)


def test_merged_scene_has_no_fixed_minimum_maximum_or_model_guess(tmp_path):
    payload, board = fixture(tmp_path)
    payload = {"generation_segments": [dict(segment_id="ONE", prompt="Complete continuous scene.",
                duration_seconds=27, source_shot_ids=["S1", "S2", "S3"])]}
    before = copy.deepcopy(payload)
    assert validate_generation_boundaries(payload, board, tmp_path)["same_scene_boundaries_checked"] == 0
    assert payload == before and "selected_video_model" not in payload


def test_required_director_beat_cannot_be_cut_from_middle_of_source_scope(tmp_path):
    _, board = fixture(tmp_path)
    board['scenes'][0]['shots'][1]['required_visuals'] = [{'prompt_clause': 'The key lands on the shelf.'}]
    payload = {'generation_segments': [dict(segment_id='G1', source_shot_ids=['S1', 'S3'],
                                           prompt='The key lands on the shelf.')]}
    with pytest.raises(ValueError, match='required_visuals: S2'):
        validate_generation_boundaries(payload, board, tmp_path)


def test_required_director_beat_stays_in_scoped_prompt_and_has_shot_time(tmp_path):
    _, board = fixture(tmp_path)
    clause = 'Keep the courier’s hand, key and shelf visible until release.'
    board['scenes'][0]['shots'][1]['required_visuals'] = [{'prompt_clause': clause, 'min_seconds': 3}]
    row = dict(segment_id='G1', source_shot_ids=['S1', 'S2', 'S3'],
               prompt=f'The courier approaches.\n{clause}\nThe courier exits.', shot_durations=[1, 3, 2])
    payload = {'generation_segments': [row]}
    validate_generation_boundaries(payload, board, tmp_path)
    row['shot_durations'] = [2, 2, 2]
    with pytest.raises(ValueError, match='S2.*至少 3 秒'):
        validate_generation_boundaries(payload, board, tmp_path)
    row['prompt'] = f'Skip this instruction: {clause}'
    row['notes'] = clause
    with pytest.raises(ValueError, match='S2.*正文缺少'):
        validate_generation_boundaries(payload, board, tmp_path)


@pytest.mark.parametrize(('total', 'parameter', 'parts', 'error'), [
    (2, 2, [3, 1], '片段总长 2 秒，逐镜合计 4 秒'),
    (4, 4, [3, 1], None),
    (4, 5, [3, 1], '生成时长声明无效或冲突'),
    (4, 4, [True, 3], '逐镜时长必须是非空正数数组'),
    (0.3, 0.3, [0.1, 0.2], None),
])
def test_cli_checks_shot_schedule_against_segment_total(tmp_path, capsys, total, parameter, parts, error):
    clause = 'Keep the key visible until release.'
    first = {'shot_id': 'S1'}
    if total >= 2:
        first['required_visuals'] = [{'prompt_clause': clause, 'min_seconds': 3}]
    board = tmp_path / 'board.json'
    board.write_text(json.dumps({'scenes': [{'scene_id': 'ROOM', 'shots': [first, {'shot_id': 'S2'}]}]}))
    prompt = tmp_path / 'video.json'
    prompt.write_text(json.dumps({'generation_segments': [{
        'segment_id': 'G1', 'prompt': clause, 'source_shot_ids': ['S1', 'S2'],
        'duration_seconds': total, 'parameters': {'duration': parameter}, 'shot_durations': parts,
    }]}))
    result = main(['--project', str(tmp_path), '--prompt', str(prompt), '--storyboard', str(board)])
    report = json.loads(capsys.readouterr().out)
    assert result == (1 if error else 0)
    assert report['ok'] is (error is None)
    if error:
        assert 'G1' in report['error'] and error in report['error']


def action_coverage_fixture(tmp_path):
    clip = tmp_path / 'synthetic-action.mp4'
    clip.write_bytes(b'Synthetic bytes for a source-link test; no platform playback claim.')
    product = 'AST-006'
    actions = [('S02-006', '刷涂', 'Brush the film onto the skin.'),
               ('S02-007', '湿擦', 'Wipe away the film with a damp towel.')]
    board = {'scenes': [{'scene_id': 'S01', 'shots': [{'shot_id': 'S01-001'}]},
                        {'scene_id': 'S02', 'shots': [
                            {'shot_id': shot_id, 'source_asset_requirement_ids': [product],
                             'required_visuals': [{'prompt_clause': clause, 'action_reference': {
                                 'product_ast_id': product, 'action_key': action}}]}
                            for shot_id, action, clause in actions]}]}
    first = dict(segment_id='V01', source_shot_ids=['S01-001'], prompt='At the table.', materials=[])
    reference = dict(kind='video', role='action', asset_id=product, path=clip.name,
                     sha256=hashlib.sha256(clip.read_bytes()).hexdigest(),
                     source_shot_ids=[shot_id for shot_id, _, _ in actions],
                     action_references=[{'shot_id': shot_id, 'action_key': action} for shot_id, action, _ in actions])
    last = dict(segment_id='V04', source_shot_ids=reference['source_shot_ids'],
                prompt='\n'.join(clause for _, _, clause in actions), materials=[reference])
    payload = dict(generation_segments=[first, last], source_storyboard={'path': 'board.json'})
    sources = [(dict(artifact_id='P'), tmp_path / 'prompt.json', payload),
               (dict(artifact_id='B'), tmp_path / 'board.json', board)]
    rows = [dict(node_id=segment['segment_id'], source_segment_id=segment['segment_id'],
                 source_shot_ids=segment['source_shot_ids'], parameters={}, prompt=segment['prompt'],
                 reference_checks=[dict(kind='video', roles=['action'], entity_ids=[product],
                                        sha256=reference['sha256'], source_shot_ids=reference['source_shot_ids'])]
                 if segment is last else []) for segment in (first, last)]
    return payload, board, sources, rows, reference


@pytest.mark.parametrize('fault', ['action_omitted', 'state_image', 'wrong_role', 'wrong_product', 'missing_file', 'wrong_sha'])
def test_exact_wet_wipe_shot_requires_usable_motion_clip(tmp_path, fault):
    payload, board, _, _, reference = action_coverage_fixture(tmp_path)
    validate_generation_boundaries(payload, board, tmp_path)
    wet_only = copy.deepcopy(reference)
    wet_only['action_references'] = wet_only['action_references'][1:]
    reference['action_references'] = reference['action_references'][:1]
    payload['generation_segments'][1]['materials'].append(wet_only)
    if fault == 'action_omitted':
        wet_only['action_references'] = []
    elif fault == 'state_image':
        wet_only.update(kind='image', path='film-state.png')
    elif fault == 'wrong_role':
        wet_only['role'] = 'appearance'
    elif fault == 'wrong_product':
        wet_only['asset_id'] = 'AST-OTHER'
    elif fault == 'missing_file':
        wet_only['path'] = 'missing.mp4'
    elif fault == 'wrong_sha':
        wet_only['sha256'] = 'a' * 64
    with pytest.raises(ValueError, match='S02-007.*湿擦'):
        validate_generation_boundaries(payload, board, tmp_path)


def test_action_coverage_checks_actual_delivery_and_ignores_unrelated_segment(tmp_path):
    _, _, sources, rows, reference = action_coverage_fixture(tmp_path)
    validate_scene_source_mapping(rows, sources, tmp_path)
    actual = rows[1]['reference_checks'][0]
    actual['source_shot_ids'] = ['S02-006']
    with pytest.raises(ValueError, match='S02-007.*湿擦'):
        validate_scene_source_mapping([rows[1]], sources, tmp_path)

    actual.update(source_shot_ids=['S02-006', 'S02-007'], sha256='a' * 64)
    with pytest.raises(ValueError, match='S02-006.*刷涂'):
        validate_scene_source_mapping([rows[1]], sources, tmp_path)
    actual['sha256'] = reference['sha256']
    reference['action_references'].pop()
    with pytest.raises(ValueError, match='S02-007.*湿擦'):
        validate_scene_source_mapping([rows[1]], sources, tmp_path)
    validate_scene_source_mapping([rows[0]], sources, tmp_path)


def test_required_action_and_duration_need_the_same_carrier(tmp_path):
    payload, board, _, _, _ = action_coverage_fixture(tmp_path)
    requirements = required_visuals(board)
    requirements['S02-007'][0]['min_seconds'] = 3
    short = payload['generation_segments'][1]
    short['shot_durations'] = [2, 1]
    longer = dict(short, shot_durations=[2, 3], materials=[])
    with pytest.raises(ValueError, match='S02-007.*湿擦'):
        validate_required_visuals([short, longer], requirements, project=tmp_path)
    longer['materials'] = short['materials']
    validate_required_visuals([short, longer], requirements, project=tmp_path)


def test_verified_limit_and_shared_state_inputs_pass_without_one_image_per_shot(tmp_path):
    payload, board = fixture(tmp_path)
    before = copy.deepcopy((payload, board))
    assert validate_generation_boundaries(payload, board, tmp_path)["same_scene_boundaries_checked"] == 2
    assert (payload, board) == before


def authorized_draft(payload):
    payload.update(status="candidate", review_status="unreviewed", production_blocked=True,
                   generation_authorized=False, user_approved=False)
    payload["selected_video_model"].update(confirmed_by_user=False,
        selection_basis="agent_selected_for_authorized_draft",
        authorization_ref="synthetic request: choose a supported model and arrange a draft; no generation")
    return payload


def test_authorized_agent_model_choice_validates_draft_without_inventing_confirmation(tmp_path):
    payload, board = fixture(tmp_path)
    authorized_draft(payload)
    before = copy.deepcopy(payload)
    assert validate_generation_boundaries(payload, board, tmp_path)["same_scene_boundaries_checked"] == 2
    rows = [dict(source_segment_id=row["segment_id"],
                 parameters=dict(payload["platform"], duration=row["duration_seconds"]))
            for row in payload["generation_segments"]]
    validate_delivery_parameters(rows, payload, tmp_path)
    assert payload == before
    assert payload["selected_video_model"]["confirmed_by_user"] is False
    assert payload["generation_authorized"] is False and payload["production_blocked"] is True


def mixed_modes(tmp_path):
    payload, board = fixture(tmp_path)
    model = payload['selected_video_model']
    model['reference_modes'] = ['synthetic-multi-reference', 'synthetic-first-last']
    model['reference_mode_evidence'] = {mode: copy.deepcopy(model['capability_evidence'])
                                        for mode in model['reference_modes']}
    for i, row in enumerate(payload['generation_segments']):
        row['parameters'] = dict(payload['platform'], duration=9,
                                 reference_mode=model['reference_modes'][i % 2])
    return payload, board


def test_each_observed_mode_can_bind_its_own_segment(tmp_path):
    payload, board = mixed_modes(tmp_path)
    before = copy.deepcopy(payload)
    assert validate_generation_boundaries(payload, board, tmp_path)['same_scene_boundaries_checked'] == 2
    rows = [dict(source_segment_id=s['segment_id'], parameters=copy.deepcopy(s['parameters']))
            for s in payload['generation_segments']]
    validate_delivery_parameters(rows, payload, tmp_path)
    assert payload == before
    rows[0]['parameters']['reference_mode'] = 'synthetic-first-last'
    with pytest.raises(ValueError, match='参考模式'):
        validate_delivery_parameters(rows, payload, tmp_path)


@pytest.mark.parametrize('fault', ['missing_proof', 'bad_hash', 'unknown_mode', 'wrong_model',
                                    'over_limit', 'duplicate_mode', 'string_modes'])
def test_mode_collection_does_not_relax_evidence_or_duration_checks(tmp_path, fault):
    payload, board = mixed_modes(tmp_path)
    model = payload['selected_video_model']
    if fault == 'missing_proof': model['reference_mode_evidence'].pop('synthetic-first-last')
    elif fault == 'bad_hash': model['reference_mode_evidence']['synthetic-first-last']['sha256'] = '0'*64
    elif fault == 'unknown_mode': payload['generation_segments'][1]['parameters']['reference_mode'] = 'unknown'
    elif fault == 'wrong_model': payload['generation_segments'][1]['parameters']['model'] = 'other'
    elif fault == 'over_limit':
        payload['generation_segments'][1]['parameters']['duration'] = 19
        payload['generation_segments'][1]['duration_seconds'] = 19
    elif fault == 'duplicate_mode': model['reference_modes'].append(model['reference_modes'][0])
    elif fault == 'string_modes': model['reference_modes'] = 'synthetic-first-last'
    with pytest.raises(ValueError):
        validate_generation_boundaries(payload, board, tmp_path)


@pytest.mark.parametrize("mutation", ["no_authorization", "blank_authorization", "wrong_basis",
    "missing_confirmation", "formal_status", "reviewed", "not_blocked", "missing_blocked",
    "generation_true", "missing_generation_permission", "user_approved", "stale_evidence",
    "missing_evidence", "duration_conflict", "duration_exceeded", "model_mismatch", "mode_mismatch"])
def test_agent_selected_draft_cannot_relax_authority_capability_or_parameter_checks(tmp_path, mutation):
    payload, board = fixture(tmp_path)
    authorized_draft(payload)
    model = payload["selected_video_model"]
    if mutation == "no_authorization": model.pop("authorization_ref")
    elif mutation == "blank_authorization": model["authorization_ref"] = " "
    elif mutation == "wrong_basis": model["selection_basis"] = "user_confirmed"
    elif mutation == "missing_confirmation": model.pop("confirmed_by_user")
    elif mutation == "formal_status": payload["status"] = "confirmed"
    elif mutation == "reviewed": payload["review_status"] = "passed"
    elif mutation == "not_blocked": payload["production_blocked"] = False
    elif mutation == "missing_blocked": payload.pop("production_blocked")
    elif mutation == "generation_true": payload["generation_authorized"] = True
    elif mutation == "missing_generation_permission": payload.pop("generation_authorized")
    elif mutation == "user_approved": payload["user_approved"] = True
    elif mutation == "stale_evidence": (tmp_path / model["capability_evidence"]["path"]).write_text("changed")
    elif mutation == "missing_evidence": model.pop("capability_evidence")
    elif mutation == "duration_conflict": payload["generation_segments"][0]["parameters"] = dict(payload["platform"], duration=8)
    elif mutation == "duration_exceeded": payload["generation_segments"][0]["duration_seconds"] = 19
    elif mutation == "model_mismatch": payload["platform"]["model"] = "other-model"
    elif mutation == "mode_mismatch": payload["platform"]["reference_mode"] = "other-mode"
    with pytest.raises(ValueError):
        validate_generation_boundaries(payload, board, tmp_path)


def test_draft_choice_is_rejected_by_parameter_gate_after_generation_permission_changes(tmp_path):
    payload, board = fixture(tmp_path)
    authorized_draft(payload)
    rows = [dict(source_segment_id="G1", parameters=dict(payload["platform"], duration=9))]
    validate_delivery_parameters(rows, payload, tmp_path)
    payload["generation_authorized"] = True
    with pytest.raises(ValueError):
        validate_delivery_parameters(rows, payload, tmp_path)


def test_correct_endpoint_roles_pass_but_reversed_roles_do_not(tmp_path):
    payload, board = fixture(tmp_path)
    rows = payload["generation_segments"]
    for index in range(1, 3):
        state = f"STATE{index}"
        for row, role in ((rows[index - 1], "end_frame"), (rows[index], "start_frame")):
            next(r for r in row["materials"] if r["state_id"] == state)["role"] = role
    validate_generation_boundaries(payload, board, tmp_path)
    rows[0]["materials"][0]["role"] = "start_frame"
    with pytest.raises(ValueError, match="切点状态参考"):
        validate_generation_boundaries(payload, board, tmp_path)


def test_same_shot_continuous_subranges_can_use_exact_complete_sentence(tmp_path):
    payload, board = fixture(tmp_path)
    payload["generation_segments"] = payload["generation_segments"][:2]
    payload["generation_segments"][1]["source_shot_ids"] = ["S1"]
    board["scenes"][0]["shots"][0]["audio"] = {"dialogue": [{"text": "First complete sentence."}]}
    payload["generation_segments"][1]["same_scene_split"]["source_boundary"] = dict(
        shot_id="S1", field="audio.dialogue.0.text", quote="First complete sentence.")
    validate_generation_boundaries(payload, board, tmp_path)


def test_composite_source_snapshot_keeps_existing_single_artifact_route(tmp_path):
    payload, board = fixture(tmp_path)
    payload.pop("source_storyboard")
    payload["scenes"] = board["scenes"]
    for row in payload["generation_segments"]:
        row.pop("prompt")  # Source mapping snapshot, not the formal manuscript.
    rows = [dict(node_id="node-" + s["segment_id"], source_segment_id=s["segment_id"],
                 source_shot_ids=s["source_shot_ids"], reference_checks=copy.deepcopy(s["materials"]),
                 parameters=dict(payload["platform"], duration=s["duration_seconds"]),
                 continuity=dict(event_id="synthetic-continuous-event", **(
                     {"previous_node_id": "node-" + payload["generation_segments"][index - 1]["segment_id"]}
                     if index else {}))) for index, s in enumerate(payload["generation_segments"])]
    validate_scene_source_mapping(rows, [({}, tmp_path / "composite.json", payload)], tmp_path)


@pytest.mark.parametrize("side", ["source", "delivery"])
def test_cutpoint_reference_cannot_be_scoped_away_from_boundary_shot(tmp_path, side):
    payload, sources, rows = delivery_fixture(tmp_path)
    payload["generation_segments"] = payload["generation_segments"][:2]
    first, second = payload["generation_segments"]
    first["source_shot_ids"] = ["S1", "S2"]
    second["source_shot_ids"] = ["S3"]
    second["same_scene_split"]["source_boundary"] = dict(shot_id="S2", field="cut.exit", quote="Synthetic boundary 2")
    rows = rows[:2]
    rows[0]["source_shot_ids"], rows[1]["source_shot_ids"] = ["S1", "S2"], ["S3"]
    validate_scene_source_mapping(rows, sources, tmp_path)
    target = first["materials"][0] if side == "source" else rows[0]["reference_checks"][0]
    target["source_shot_ids"] = ["S1"]  # Actual exit is S2, not the earlier S1.
    with pytest.raises(ValueError, match="切点"):
        validate_scene_source_mapping(rows, sources, tmp_path)


@pytest.mark.parametrize("mutation", ["unknown_model", "changed_mode", "stale_evidence", "missing_evidence",
    "not_exceeded", "negative", "nan", "bool", "oversized_clip", "bad_boundary", "missing_mapping", "wrong_order"])
def test_no_assumed_capability_or_source_boundary(tmp_path, mutation):
    payload, board = fixture(tmp_path)
    model = payload["selected_video_model"]
    rows = payload["generation_segments"]
    if mutation == "unknown_model": payload.pop("selected_video_model")
    elif mutation == "changed_mode": payload["platform"]["reference_mode"] = "other"
    elif mutation == "stale_evidence": (tmp_path / model["capability_evidence"]["path"]).write_text("changed")
    elif mutation == "missing_evidence": model.pop("capability_evidence")
    elif mutation == "not_exceeded": model["max_duration_seconds"] = 60
    elif mutation == "negative": model["max_duration_seconds"] = -1
    elif mutation == "nan": model["max_duration_seconds"] = float("nan")
    elif mutation == "bool": model["max_duration_seconds"] = True
    elif mutation == "oversized_clip": rows[0]["duration_seconds"] = 19
    elif mutation == "bad_boundary": rows[1]["same_scene_split"]["source_boundary"]["quote"] = "Invented event"
    elif mutation == "missing_mapping": rows[1].pop("source_shot_ids")
    elif mutation == "wrong_order": rows.reverse()
    with pytest.raises(ValueError):
        validate_generation_boundaries(payload, board, tmp_path)


@pytest.mark.parametrize("mutation", ["environment_only", "wrong_state", "different_image", "one_side_missing"])
def test_reused_room_picture_is_not_cutpoint_evidence(tmp_path, mutation):
    payload, board = fixture(tmp_path)
    ref = payload["generation_segments"][0]["materials"][0]
    if mutation == "environment_only": ref["role"] = "layout"
    elif mutation == "wrong_state": ref["state_id"] = "BASE-ROOM"
    elif mutation == "different_image": ref["sha256"] = "f" * 64
    elif mutation == "one_side_missing": payload["generation_segments"][1]["materials"] = []
    with pytest.raises(ValueError, match="切点状态参考"):
        validate_generation_boundaries(payload, board, tmp_path)


def test_true_source_event_boundary_needs_no_model_limit_or_cutpoint_image(tmp_path):
    payload, board = fixture(tmp_path)
    payload.pop("selected_video_model")
    for row in payload["generation_segments"][1:]:
        row["same_scene_split"]["reason"] = "event_complete"
        row.pop("continuity")
        row["materials"] = []
    assert validate_generation_boundaries(payload, board, tmp_path)["segment_count"] == 3


def test_cross_scene_staging_only_checks_declared_carryover(tmp_path):
    payload, board = fixture(tmp_path)
    left, right = payload["generation_segments"][:2]
    payload["generation_segments"] = [left, right]
    right.pop("same_scene_split")
    right.pop("continuity")
    room = board["scenes"][0]
    room["space_rules"] = {"subject_positions": "Courier stands at the door."}
    board["scenes"].append({"scene_id": "STREET", "shots": [room["shots"].pop(1)],
                            "space_rules": {"subject_positions": "Courier enters a parked car."}})
    assert validate_generation_boundaries(payload, board, tmp_path)["same_scene_boundaries_checked"] == 0

    carryover = {"from_segment_id": "G1", "permitted_change": "The courier moves from the door to the car."}
    right["physical_continuity"] = carryover
    with pytest.raises(ValueError, match="未列出任何实际继承的状态"):
        validate_generation_boundaries(payload, board, tmp_path)
    carryover["visibility"] = dict(status="no_visual_needed", previous_state="Same jacket is visible at the door.",
                                  current_state="Same jacket is visible at the car.",
                                  basis="The source shows the same jacket on both sides of the cut.")
    validate_generation_boundaries(payload, board, tmp_path)
    carryover["from_segment_id"] = "G0"
    with pytest.raises(ValueError, match="独立接缝检查"):
        validate_generation_boundaries(payload, board, tmp_path)


def test_new_topic_keeps_actor_positions_bound_to_real_inputs(tmp_path):
    payload, board = fixture(tmp_path)
    board["scenes"][0]["space_rules"] = {"subject_positions": "Two actors face across a table."}
    left, right = payload["generation_segments"][:2]
    payload["generation_segments"] = [left, right]
    right["same_scene_split"]["reason"] = "event_complete"
    with pytest.raises(ValueError, match="G1→G2 缺人物"):
        validate_generation_boundaries(payload, board, tmp_path)
    assert validate_generation_boundaries(
        payload, board, tmp_path, require_physical=False,
    )["same_scene_boundaries_checked"] == 1

    previous, current = "a" * 64, "b" * 64
    right["physical_continuity"] = dict(
        from_segment_id="G1", permitted_change="Only the topic changes.",
        positions=dict(status="visual_reference", previous_state="Actors face across the table.",
                       current_state="Actors face across the table.", basis="Same seats under reverse angle.",
                       previous_sha256s=[previous], current_sha256s=[current],
                       previous_observation="Both actors and separate benches are visible.",
                       current_observation="Both actors are still on their respective benches."),
    )
    left["materials"] = [dict(path="seats-a.png", sha256=previous, roles=["layout"], physical_scope=["positions"])]
    right["materials"] = [dict(path="table-only.png", sha256=current, roles=["layout"], physical_scope=["props"])]
    with pytest.raises(ValueError, match="positions 缺 G2"):
        validate_generation_boundaries(payload, board, tmp_path)
    right["materials"][0] = dict(path="seats-b.png", sha256=current, roles=["layout"], physical_scope=["positions"])
    assert validate_generation_boundaries(payload, board, tmp_path)["same_scene_boundaries_checked"] == 1

    delivered = [
        dict(source_segment_id=row["segment_id"], node_id=f"N{n}", source_shot_ids=row["source_shot_ids"],
             reference_checks=[dict(row["materials"][0], kind="image")])
        for n, row in enumerate((left, right))
    ]
    assert validate_delivery_cutpoints(delivered, [left, right]) == {}
    right["materials"][0]["roles"] = ["end_frame"]
    delivered[1]["reference_checks"][0]["roles"] = ["end_frame"]
    with pytest.raises(ValueError, match="positions .* G2"):
        validate_generation_boundaries(payload, board, tmp_path)
    with pytest.raises(ValueError, match="positions .* G2"):
        validate_delivery_cutpoints(delivered, [left, right])
    right["materials"][0]["roles"] = ["start_frame"]
    delivered[1]["reference_checks"][0]["roles"] = ["start_frame"]
    validate_generation_boundaries(payload, board, tmp_path)
    assert validate_delivery_cutpoints(delivered, [left, right]) == {}
    delivered[1]["reference_checks"] = []
    with pytest.raises(ValueError, match="positions 缺 G2"):
        validate_delivery_cutpoints(delivered, [left, right])


def test_execution_limit_requires_actual_evidence_not_vague_safer_claim(tmp_path):
    payload, board = fixture(tmp_path)
    for row in payload["generation_segments"][1:]:
        row["same_scene_split"]["reason"] = "execution_limit"
    with pytest.raises(ValueError, match="限制核验"):
        validate_generation_boundaries(payload, board, tmp_path)


def delivery_fixture(tmp_path):
    payload, board = fixture(tmp_path)
    sources = [(dict(artifact_id="P"), tmp_path / "prompt.json", payload),
               (dict(artifact_id="B"), tmp_path / "board.json", board)]
    rows = [dict(node_id="node-" + s["segment_id"], source_segment_id=s["segment_id"],
                 source_shot_ids=s["source_shot_ids"], reference_checks=copy.deepcopy(s["materials"]),
                 parameters=dict(payload["platform"], duration=s["duration_seconds"]),
                 continuity=dict(event_id="synthetic-continuous-event", **(
                     {"previous_node_id": "node-" + payload["generation_segments"][index - 1]["segment_id"]}
                     if index else {}))) for index, s in enumerate(payload["generation_segments"])]
    return payload, sources, rows


@pytest.mark.parametrize("mutation", ["invent_new_event", "wrong_previous_node", "reverse_order"])
def test_delivery_cannot_relabel_source_continuation_to_skip_native_boundary_gate(tmp_path, mutation):
    payload, sources, rows = delivery_fixture(tmp_path)
    if mutation == "invent_new_event": rows[1]["continuity"] = {"event_id": "unrelated-event"}
    elif mutation == "wrong_previous_node": rows[1]["continuity"]["previous_node_id"] = "OTHER"
    else: rows.reverse()
    with pytest.raises(ValueError, match="实际交付前驱"):
        validate_scene_source_mapping(rows, sources, tmp_path)


def test_partial_delivery_does_not_require_reuploading_omitted_predecessor(tmp_path):
    payload, sources, rows = delivery_fixture(tmp_path)
    validate_scene_source_mapping([rows[1]], sources, tmp_path)


@pytest.mark.parametrize("mutation", [None, "remove_reason", "omit_director", "wrong_source", "omit_actual_cutpoint"])
def test_canvas_source_gate_cannot_skip_boundary_or_actual_inputs(tmp_path, mutation):
    payload, sources, rows = delivery_fixture(tmp_path)
    if mutation == "remove_reason": payload["generation_segments"][1].pop("same_scene_split")
    elif mutation == "omit_director": payload.pop("source_storyboard")
    elif mutation == "wrong_source": rows[1]["source_segment_id"] = "OTHER"
    elif mutation == "omit_actual_cutpoint": rows[1]["reference_checks"] = []
    if mutation is None:
        validate_scene_source_mapping(rows, sources, tmp_path)
    else:
        with pytest.raises(ValueError):
            validate_scene_source_mapping(rows, sources, tmp_path)


def test_command_rejects_historical_ad_hoc_format_without_rewriting_files(tmp_path, capsys):
    prompt, board = tmp_path / "prompt.json", tmp_path / "board.json"
    prompt.write_text(json.dumps({"video_segments": [{"video_id": "V01", "duration_seconds": 11}]}))
    board.write_text(json.dumps(fixture(tmp_path)[1]))
    before = prompt.read_bytes()
    assert main(["--project", str(tmp_path), "--prompt", str(prompt), "--storyboard", str(board)]) == 1
    assert "历史专用分段格式" in capsys.readouterr().out
    assert prompt.read_bytes() == before
