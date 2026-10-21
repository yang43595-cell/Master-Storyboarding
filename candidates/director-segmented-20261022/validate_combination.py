"""Explicit A+B snapshot validation; original B evidence and locks stay intact."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
A_COMMIT = "1b4959656f20e7b9796ccacbb3620a3908552b27"
B_COMMIT = "6cf4e382a46101fc2a72e4e5139e33165c67752c"


def validate():
    lock = json.loads((HERE / "combination-lock.json").read_text(encoding="utf-8"))
    if lock["a_commit"] != A_COMMIT or lock["b_commit"] != B_COMMIT or lock["production_enabled"] is not False:
        raise ValueError("incorrect explicit combination identity")
    for group in ("a_runtime_blobs", "b_candidate_blobs"):
        if len(lock[group]) != (6 if group == "a_runtime_blobs" else 8):
            raise ValueError("incomplete lock")
        for relative, expected in lock[group].items():
            data = (ROOT / relative).read_bytes()
            actual = hashlib.sha1(b"blob " + str(len(data)).encode() + bytes([0]) + data).hexdigest()
            if actual != expected:
                raise ValueError("combination snapshot changed: " + relative)
    spec = importlib.util.spec_from_file_location("original_b_validator", HERE / "validate_candidate.py")
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    try:
        original.validate()
    except ValueError as exc:
        if str(exc) != "Active baseline changed: PROJECT_RULES.md":
            raise
    else:
        raise ValueError("original B baseline must not falsely pass against A")
    meta = json.loads((HERE / "candidate.json").read_text(encoding="utf-8"))
    if meta["enabled_by_default"] is not False or meta["runtime_wiring"] != "none":
        raise ValueError("candidate enabled/wired")
    schema = json.loads((ROOT / "schemas/storyboard-package.schema.json").read_text(encoding="utf-8"))
    def resolve(node):
        while "$ref" in node:
            ref = node["$ref"]
            if not ref.startswith("#/"):
                raise ValueError("nonlocal schema reference")
            node = schema
            for part in ref[2:].split("/"):
                node = node[part]
        return node
    for field_path in meta["schema_field_paths"]:
        node = schema
        for token in field_path.split("."):
            array = token.endswith("[]")
            node = resolve(node)["properties"][token[:-2] if array else token]
            if array:
                node = resolve(node)["items"]
        resolve(node)
    from scripts import stage_context_cli
    collected = stage_context_cli._local_standard_documents(ROOT / "skills/storyboard-planner")
    if any(HERE == p.resolve() or HERE in p.resolve().parents for p in collected):
        raise ValueError("candidate entered mandatory reading")
    return {"ok": True, "a_commit": A_COMMIT, "b_commit": B_COMMIT,
            "original_b_baseline": "expected rejection against A; original lock preserved",
            "locked_a_runtime_files": 6, "locked_b_candidate_files": 8,
            "existing_field_mappings": len(meta["schema_field_paths"]),
            "candidate_excluded_from_mandatory_reading": True,
            "production_enabled": False, "semantic_continuity_verified": False,
            "artistic_benefit_verified": False, "model_calls": 0}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
