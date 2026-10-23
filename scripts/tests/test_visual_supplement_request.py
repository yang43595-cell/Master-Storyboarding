"""Decision contracts allow reuse without inventing an insufficiency."""
import copy
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "schemas/visual-supplement-request.schema.json").read_text(encoding="utf-8"))

def request():
    return {"schema_version":"1.0", "project_id":"test", "request_id":"VS-001",
            "status":"not_needed", "source_storyboard":{"artifact_id":"ART-R03", "logical_id":"storyboard.package", "revision":"R03", "path":"director.json", "status":"confirmed"},
            "source_shot_ids":["S03-035"], "target_state":"open box", "must_appear":["box"],
            "must_not_appear":[], "preserve":["layout"], "existing_references":[{"kind":"confirmed_asset", "ref_id":"AST-BOX/state1", "path":"open.png", "coverage":"open box interior"}],
            "decision":"reuse_existing", "max_images":0, "return_upstream":{"required":False, "target":"none", "reason":""}}

def test_reuse_requires_coverage_but_not_gap():
    value=request(); Draft202012Validator(SCHEMA).validate(value)
    value["existing_references"]=[]
    assert list(Draft202012Validator(SCHEMA).iter_errors(value))

def test_supplement_requires_actual_gap_and_positive_count():
    value=request(); value.update(decision="request_supplement",status="candidate",max_images=1)
    assert list(Draft202012Validator(SCHEMA).iter_errors(value))
    value["gap"]={"type":"critical_state","observed_evidence":"closed reference only","why_existing_is_insufficient":"interior hidden","downstream_consequence":"interior may be invented"}
    Draft202012Validator(SCHEMA).validate(value)
    value["max_images"]=0
    assert list(Draft202012Validator(SCHEMA).iter_errors(value))

def test_return_requires_reason_without_fake_gap():
    value=request();value.update(decision="return_upstream",status="blocked",return_upstream={"required":True,"target":"storyboard-planner","reason":"conflicting action"})
    Draft202012Validator(SCHEMA).validate(value)
    value["return_upstream"]["reason"]=""
    assert list(Draft202012Validator(SCHEMA).iter_errors(value))


def test_spatial_rework_can_return_to_previs_without_authorizing_images():
    value = request()
    value.update(decision='return_upstream', status='blocked', max_images=0,
                 return_upstream={'required': True, 'target': 'blender-previs',
                                  'reason': '已有白模机位没有看见指定承托点；同场景逐镜修订'})
    Draft202012Validator(SCHEMA).validate(value)
    value['max_images'] = 1
    assert list(Draft202012Validator(SCHEMA).iter_errors(value))


# Synthetic isolated runs: artifact adoption identity is not request business status.
def candidate_request(project, decision='return_upstream'):
    __tracebackhide__ = True
    from test_candidate_test_mode import setup, add
    setup(project)
    add(project, 'ART-R03', 'storyboard.package', {'status': 'candidate', 'revision': 'R03'})
    value = request()
    value['source_storyboard'].update(status='candidate', path='ART-R03.json')
    if decision == 'return_upstream':
        value.update(decision=decision, status='blocked', return_upstream={
            'required': True, 'target': 'storyboard-planner', 'reason': 'synthetic unresolved layout'})
    elif decision == 'request_supplement':
        value.update(decision=decision, status='candidate', max_images=1, gap={
            'type': 'space', 'observed_evidence': 'synthetic missing state',
            'why_existing_is_insufficient': 'synthetic incomplete coverage', 'downstream_consequence': 'unresolved'})
    return value


def register_request(project, value, *, request_type='visual-supplement-request'):
    from scripts.core import artifacts, candidate_test
    from scripts.core.io import write_json
    path = project/'request.json'
    write_json(path, value)
    artifacts.register_workspace_artifact(project, dict(artifact_id='VS', logical_id='visual-supplement.request',
        type=request_type, path_or_url=path.name, revision='R01', depends_on=['storyboard.package']))
    candidate_test.configure(project, 'synthetic-run', 'synthetic user: candidate text test only', ['ART-R03', 'VS'])
    return path


@pytest.mark.parametrize('decision,status', [('return_upstream', 'blocked'), ('reuse_existing', 'not_needed'), ('request_supplement', 'candidate')])
def test_candidate_request_keeps_honest_business_outcome(tmp_path, decision, status):
    from scripts.core import artifacts, candidate_test
    from scripts.core.schema_contracts import validate_visual_supplement_request
    value = candidate_request(tmp_path, decision)
    before = copy.deepcopy(value)
    report = validate_visual_supplement_request(value, project_root=tmp_path, candidate_test=True)
    assert report['schema_valid'] and report['business_status'] == status
    assert report['review_status'] == 'unreviewed' and report['production_blocked']
    path = register_request(tmp_path, value)
    resolved = candidate_test.resolve_inputs(tmp_path)
    assert resolved[-1]['status'] == 'candidate' and resolved[-1]['business_status'] == status
    assert value == before and json.loads(path.read_text()) == before
    manifest = artifacts.load_manifest(tmp_path)['artifacts'][-1]
    assert manifest['status'] == 'candidate' and manifest['user_approved'] is False
    assert 'business_status' not in manifest


def test_production_schema_and_cached_validator_are_not_relaxed(tmp_path):
    from scripts.core.schema_contracts import schema_errors, validate_visual_supplement_request
    value = candidate_request(tmp_path)
    assert schema_errors(value, 'visual-supplement-request.schema.json')
    validate_visual_supplement_request(value, project_root=tmp_path, candidate_test=True)
    assert schema_errors(value, 'visual-supplement-request.schema.json')
    assert list(Draft202012Validator(SCHEMA).iter_errors(value))
    with pytest.raises(ValueError): validate_visual_supplement_request(value)
    confirmed = copy.deepcopy(value); confirmed['source_storyboard']['status'] = 'confirmed'
    assert validate_visual_supplement_request(confirmed)['business_status'] == 'blocked'
    with pytest.raises(ValueError, match='unreviewed'):
        validate_visual_supplement_request(confirmed, project_root=tmp_path)


@pytest.mark.parametrize('project', [None, 'ordinary'])
def test_test_validation_requires_isolated_context(tmp_path, project):
    from scripts.core.schema_contracts import validate_visual_supplement_request
    with pytest.raises(ValueError, match='隔离测试'):
        validate_visual_supplement_request(request(), project_root=tmp_path if project else None, candidate_test=True)


@pytest.mark.parametrize('mutation', ['candidate_lie', 'ready', 'confirmed', 'source_confirmed', 'images', 'reason', 'return_flag', 'unknown_field', 'wrong_type'])
def test_test_mode_does_not_weaken_business_contract(tmp_path, mutation):
    from scripts.core import candidate_test
    value = candidate_request(tmp_path)
    kind = 'visual-supplement-request'
    if mutation == 'candidate_lie': value['status'] = 'candidate'
    elif mutation in {'ready', 'confirmed'}: value['status'] = mutation
    elif mutation == 'source_confirmed': value['source_storyboard']['status'] = 'confirmed'
    elif mutation == 'images': value['max_images'] = 1
    elif mutation == 'reason': value['return_upstream']['reason'] = ''
    elif mutation == 'return_flag': value['return_upstream']['required'] = False
    elif mutation == 'unknown_field': value['extra'] = True
    else: kind = 'narrative.package'
    register_request(tmp_path, value, request_type=kind)
    with pytest.raises(ValueError): candidate_test.resolve_inputs(tmp_path)


@pytest.mark.parametrize('mutation', ['id', 'revision', 'path', 'source_status', 'manifest_status'])
def test_request_still_binds_exact_candidate_upstream(tmp_path, mutation):
    from scripts.core import artifacts, candidate_test
    from scripts.core.io import write_json
    value = candidate_request(tmp_path)
    if mutation in {'id', 'revision', 'path'}:
        value['source_storyboard'][{'id': 'artifact_id', 'revision': 'revision', 'path': 'path'}[mutation]] = {
            'id': 'WRONG', 'revision': 'R99', 'path': 'wrong.json'}[mutation]
    register_request(tmp_path, value)
    if mutation == 'source_status': write_json(tmp_path/'ART-R03.json', {'status': 'blocked', 'revision': 'R03'})
    if mutation == 'manifest_status':
        manifest = artifacts.load_manifest(tmp_path); manifest['artifacts'][-1]['status'] = 'blocked'
        artifacts.save_manifest(tmp_path, manifest)
    with pytest.raises(ValueError): candidate_test.resolve_inputs(tmp_path)


def test_blocked_request_handoff_and_production_exits(tmp_path):
    import argparse
    from scripts import stage_context_cli
    from scripts.core import artifacts, revision_control
    from scripts.canvas_readback import validate_submission
    value = candidate_request(tmp_path); register_request(tmp_path, value)
    result = stage_context_cli.handoff(argparse.Namespace(project=str(tmp_path), to_skill='storyboard-planner', candidate_test=True))
    card = (tmp_path/stage_context_cli.CONTEXT_DIR/'next_stage_handoff.md').read_text()
    assert 'business_status：`blocked`' in card and 'synthetic unresolved layout' in card
    assert result['production_blocked'] and result['review_status'] == 'unreviewed'
    with pytest.raises(ValueError, match='unreviewed'):
        artifacts.promote_candidate(tmp_path, 'visual-supplement.request', 'VS')
    with pytest.raises(ValueError, match='unreviewed'):
        revision_control.confirm_candidate(tmp_path, change_id='none', approval_ref='not approval')
    with pytest.raises(ValueError, match='unreviewed'): validate_submission({}, tmp_path)
