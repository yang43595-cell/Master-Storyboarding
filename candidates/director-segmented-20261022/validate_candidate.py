"""Validate isolation and existing-field mappings; does not run creative trials."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def validate():
    meta = json.loads((HERE / "candidate.json").read_text(encoding="utf-8"))
    if meta["enabled_by_default"] is not False or meta["runtime_wiring"] != "none":
        raise ValueError("Candidate must remain disabled and unwired")
    for relative, expected in meta["unchanged_baseline_blobs"].items():
        data = (ROOT / relative).read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + bytes([0]) + data).hexdigest()
        if actual != expected:
            raise ValueError("Active baseline changed: " + relative)
    schema = json.loads((ROOT / "schemas/storyboard-package.schema.json").read_text(encoding="utf-8"))
    def resolve(node):
        while "$ref" in node:
            ref = node["$ref"]
            if not ref.startswith("#/"):
                raise ValueError("Nonlocal schema reference")
            node = schema
            for part in ref[2:].split("/"):
                node = node[part]
        return node
    for field_path in meta["schema_field_paths"]:
        node = schema
        for token in field_path.split("."):
            array = token.endswith("[]")
            key = token[:-2] if array else token
            node = resolve(node)["properties"][key]
            if array:
                node = resolve(node)["items"]
        resolve(node)
    from scripts import stage_context_cli
    collected = stage_context_cli._local_standard_documents(ROOT / "skills/storyboard-planner")
    if any(HERE == p.resolve() or HERE in p.resolve().parents for p in collected):
        raise ValueError("Candidate unexpectedly enters required runtime reading")
    workflow = HERE / meta["workflow"]
    if not workflow.is_file() or not workflow.read_text(encoding="utf-8").strip():
        raise ValueError("Missing workflow")
    return {
        "ok": True, "baseline": meta["baseline_commit"],
        "unchanged_runtime_files": len(meta["unchanged_baseline_blobs"]),
        "existing_field_mappings": len(meta["schema_field_paths"]),
        "collector_excludes_candidate": True,
        "creative_trials_run": False, "production_switched": False,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
