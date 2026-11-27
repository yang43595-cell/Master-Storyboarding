"""Synthetic candidate helpers and production-isolation checks."""
from __future__ import annotations

import pytest
from scripts.core import artifacts, candidate_test as mode
from scripts.core.io import write_json
from scripts.core.project_state import new_project_state, write_project_state


def setup(project):
    project.mkdir(exist_ok=True)
    state = new_project_state(current_stage="creative-director")
    write_project_state(project, state)
    mode.configure(project, "synthetic-run", "synthetic user: candidate text test only", [])
    return project


def add(project, ident, logical, payload, dependencies=()):
    path = project / (ident + ".json")
    write_json(path, payload)
    artifacts.register_workspace_artifact(
        project,
        dict(
            artifact_id=ident,
            logical_id=logical,
            type=logical,
            path_or_url=path.name,
            revision=payload.get("revision", "R01"),
            depends_on=list(dependencies),
        ),
    )
    selected = mode.context(project)["input_artifact_ids"] + [ident]
    mode.configure(project, "synthetic-run", "synthetic user: candidate text test only", selected)
    return path


@pytest.mark.parametrize("confirmed,adopted,allowed", [(False, False, True), (True, False, False), (False, True, False)])
def test_unconfirmed_product_reference_is_evidence_not_package_approval(tmp_path, confirmed, adopted, allowed):
    project = setup(tmp_path / "product-reference")
    write_json(project / "ref.json", dict(revision="R01", status="reference_only_unconfirmed", user_confirmed=confirmed, formal_adoption=adopted))
    artifacts.register_workspace_artifact(project, dict(artifact_id="REF", logical_id="reference.product", type="reference", path_or_url="ref.json", revision="R01"))
    mode.configure(project, "synthetic-run", "synthetic user: candidate text test only", ["REF"])
    if allowed:
        assert mode.resolve_inputs(project)
        with pytest.raises(ValueError):
            mode.require_production(project)
    else:
        with pytest.raises(ValueError):
            mode.resolve_inputs(project)
