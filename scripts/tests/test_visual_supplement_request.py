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
