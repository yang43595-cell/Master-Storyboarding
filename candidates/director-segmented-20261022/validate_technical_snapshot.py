"""Verify the explicit technical follow-up; never relax original A/B locks."""
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def blob(path):
    data = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + bytes([0]) + data).hexdigest()


def validate():
    lock = json.loads((HERE / "technical-validation-lock.json").read_text(encoding="utf-8"))
    old_path = HERE / "combination-lock.json"
    old = json.loads(old_path.read_text(encoding="utf-8"))
    if lock["base_combination_commit"] != "5da6d80bd33008ebe4404a874de464eaed825f70" or lock["production_enabled"] is not False:
        raise ValueError("wrong technical follow-up identity")
    if lock["a_commit"] != old["a_commit"] or lock["b_origin_commit"] != old["b_commit"]:
        raise ValueError("original A/B identity changed")
    if blob(old_path) != lock["original_combination_lock_blob"] or lock["a_runtime_blobs"] != old["a_runtime_blobs"]:
        raise ValueError("original combination lock or A constraints changed")
    allowed = {"candidates/director-segmented-20261022/segment_tasks.py",
               "candidates/director-segmented-20261022/test_segment_tasks.py"}
    changed = {p for p, sha in lock["candidate_blobs"].items() if old["b_candidate_blobs"].get(p) != sha}
    if changed != allowed or set(lock["allowed_candidate_changes"]) != allowed or set(lock["candidate_blobs"]) != set(old["b_candidate_blobs"]):
        raise ValueError("unexpected candidate scope or weakened file set")
    for group in ("a_runtime_blobs", "candidate_blobs"):
        for relative, expected in lock[group].items():
            if blob(ROOT / relative) != expected:
                raise ValueError("technical snapshot changed: " + relative)
    spec = importlib.util.spec_from_file_location("prior_combination", HERE / "validate_combination.py")
    prior = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prior)
    try:
        prior.validate()
    except ValueError as exc:
        if str(exc) != "combination snapshot changed: candidates/director-segmented-20261022/segment_tasks.py":
            raise
    else:
        raise ValueError("prior snapshot must reject changed technical files")
    from scripts.core.runtime_deps import require_runtime_deps
    require_runtime_deps(ROOT)
    import jsonschema
    schema = json.loads((ROOT / "schemas/storyboard-package.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    from scripts import stage_context_cli
    documents = stage_context_cli._local_standard_documents(ROOT / "skills/storyboard-planner")
    if any(HERE == p.resolve() or HERE in p.resolve().parents for p in documents):
        raise ValueError("candidate entered mandatory reading")
    return {"ok": True, "base_combination_commit": lock["base_combination_commit"],
            "original_locks_preserved": True, "prior_snapshot": "expected rejection",
            "changed_candidate_files": sorted(changed),
            "schema_definition_valid": True,
            "dependencies": {p: importlib.metadata.version(p) for p in ("jsonschema", "referencing")},
            "production_enabled": False, "real_generation_verified": False,
            "semantic_continuity_verified": False, "online_integration_verified": False}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
