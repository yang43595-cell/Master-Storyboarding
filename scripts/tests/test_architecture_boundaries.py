"""Automated architecture and branch-boundary guardrails for scripts/."""
from __future__ import annotations

import ast
import collections
import importlib
import re
import sys
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS_ROOT.parent

LEGACY_CORE_SCC = frozenset({
    "core.agent_acceptance",
    "core.artifacts",
    "core.asset_refs",
    "core.canvas_readback",
    "core.character_boards",
    "core.director_inputs",
    "core.draft_delivery",
    "core.production_handoff",
    "core.recreation_assets",
    "core.recreation_segments",
    "core.review_publication",
    "core.revision_control",
    "core.story_sources",
    "core.visual_readiness",
})

COMPAT_SHIMS = frozenset({
    "visual_asset_choices",
    "visual_asset_product_usage",
    "visual_asset_source",
})

REDFRAME_MODULES = frozenset({
    "core.draft_delivery",
    "core.redframe_creative",
    "core.redframe_recreation",
    "core.redframe_draft_ui",
    "core.redframe_image_ui",
    "core.redframe_workspace",
    "redframe_draft",
})

L0_FOUNDATION_MODULES = frozenset({
    "core",
    "core.errors",
    "core.media_types",
    "core.io",
    "core.character_assets",
    "core.character_layout",
    "core.image_technical",
    "core.director_reading",
    "core.depth_reference_review",
    "core.knowledge_runtime",
    "core.native_port_binding",
    "core.native_references",
    "core.runtime_deps",
    "core.scene_realism",
    "core.confirmation_scope",
    "core.agent_review_api",
    "core.redframe_workspace",
})

L1_PRIMITIVE_MODULES = frozenset({
    "core.manifest_store",
    "core.project_state",
    "core.revision_store",
    "core.product_reference_pack",
    "core.project_kickoff",
    "core.endpoint_control",
    "core.prompt_body",
    "core.prompt_scene_control",
    "core.endpoint_states",
})

L2_RULE_CONTRACT_MODULES = frozenset({
    "core.asset_choice_rules",
    "core.asset_batch_receipt",
    "core.visual_asset_product_usage",
    "core.quality_rules",
    "core.boundary_rules",
    "core.delivery_files",
    "core.quality_evidence",
    "core.acceptance_rules",
    "core.schema_contracts",
    "core.candidate_test",
})

BRANCH_A_MODULES = frozenset({
    "director_package",
    "visual_stage_cli",
    "core.visual_readiness",
    "core.redframe_creative",
})
BRANCH_B_MODULES = frozenset({
    "prepare_depth_clips",
    "return_director_segment",
    "core.recreation_assets",
    "core.recreation_segments",
    "core.redframe_recreation",
    "core.depth_reference_review",
})
SHARED_ASSET_MODULES = frozenset({
    "asset_stage_cli",
    "chat_asset_review",
    "redframe_image_revision",
    "core.asset_batch_receipt",
    "core.asset_choice_rules",
    "core.asset_refs",
    "core.character_boards",
    "core.product_refs",
    "core.visual_asset_choices",
    "core.visual_asset_product_usage",
    "core.visual_asset_source",
})
SHARED_REDFRAME_UI_MODULES = frozenset({
    "core.draft_delivery",
    "core.redframe_draft_ui",
    "core.redframe_image_ui",
    "core.redframe_workspace",
})


def _collect_modules() -> tuple[dict[str, Path], dict[str, set[str]]]:
    files = sorted(
        p for p in SCRIPTS_ROOT.rglob("*.py")
        if "tests" not in p.parts and "__pycache__" not in p.parts
    )
    mods: dict[str, Path] = {}
    for path in files:
        rel = path.relative_to(SCRIPTS_ROOT).with_suffix("")
        mod = ".".join(rel.parts)
        if mod.endswith(".__init__"):
            mod = mod[:-9] or "__init__"
        mods[mod] = path

    adj: dict[str, set[str]] = collections.defaultdict(set)
    for mod, path in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias.name.removeprefix("scripts.")
                    if target in mods and target != mod:
                        adj[mod].add(target)
            elif isinstance(node, ast.ImportFrom):
                base = (node.module or "").removeprefix("scripts.")
                if node.level:
                    parts = mod.split(".")[:-node.level]
                    base = ".".join(parts + ([base] if base else []))
                if base in mods and base != mod:
                    adj[mod].add(base)
                for alias in node.names:
                    candidate = f"{base}.{alias.name}" if base else alias.name
                    if candidate in mods and candidate != mod:
                        adj[mod].add(candidate)
    return mods, adj


def _tarjan_sccs(nodes: set[str], adj: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    sccs: list[list[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = lowlink[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(adj.get(v, ())):
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(sorted(comp))

    for node in sorted(nodes):
        if node not in indices:
            strongconnect(node)
    return sccs


def test_no_core_to_top_level_cli_imports() -> None:
    mods, adj = _collect_modules()
    violations: list[tuple[str, list[str]]] = []
    for mod in sorted(mods):
        if not mod.startswith("core."):
            continue
        top_targets = sorted(
            target for target in adj[mod]
            if "." not in target and target not in {"__init__", "core"}
        )
        if top_targets:
            violations.append((mod, top_targets))
    assert violations == [], f"core.* must not import top-level scripts/*.py: {violations}"


def test_creative_and_recreation_branches_are_isolated() -> None:
    mods, adj = _collect_modules()
    for name, group in (
        ("BRANCH_A_MODULES", BRANCH_A_MODULES),
        ("BRANCH_B_MODULES", BRANCH_B_MODULES),
        ("SHARED_ASSET_MODULES", SHARED_ASSET_MODULES),
        ("SHARED_REDFRAME_UI_MODULES", SHARED_REDFRAME_UI_MODULES),
    ):
        missing = sorted(group - set(mods))
        assert missing == [], f"{name} contains missing modules: {missing}"
    a_to_b = {
        m: sorted(adj[m] & BRANCH_B_MODULES)
        for m in BRANCH_A_MODULES
        if adj[m] & BRANCH_B_MODULES
    }
    b_to_a = {
        m: sorted(adj[m] & BRANCH_A_MODULES)
        for m in BRANCH_B_MODULES
        if adj[m] & BRANCH_A_MODULES
    }
    shared_to_branch = {
        m: sorted(adj[m] & (BRANCH_A_MODULES | BRANCH_B_MODULES))
        for m in (SHARED_ASSET_MODULES | SHARED_REDFRAME_UI_MODULES)
        if adj[m] & (BRANCH_A_MODULES | BRANCH_B_MODULES)
    }
    assert a_to_b == {}, f"Branch A (creative) must not import Branch B (recreation): {a_to_b}"
    assert b_to_a == {}, f"Branch B (recreation) must not import Branch A (creative): {b_to_a}"
    assert shared_to_branch == {}, (
        f"Shared asset and Redframe UI modules must never import Branch A or Branch B: {shared_to_branch}"
    )


def test_redframe_modules_have_no_circular_imports() -> None:
    mods, adj = _collect_modules()
    missing = sorted(REDFRAME_MODULES - set(mods))
    assert missing == [], f"REDFRAME_MODULES contains missing modules: {missing}"
    redframe_adj = {m: adj[m] & REDFRAME_MODULES for m in REDFRAME_MODULES}
    assert _tarjan_sccs(set(REDFRAME_MODULES), redframe_adj) == []


def test_top_level_scripts_are_cli_entrypoints_or_declared_shims() -> None:
    mods, _ = _collect_modules()
    non_cli: list[str] = []
    single_mode_imports: list[str] = []
    for mod, path in sorted(mods.items()):
        if "." in mod or mod in {"__init__", "core"} or mod in COMPAT_SHIMS:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        has_main_def = any(
            isinstance(node, ast.FunctionDef) and node.name == "main"
            for node in tree.body
        )
        has_main_guard = "__main__" in source
        if not (has_main_def or has_main_guard):
            non_cli.append(mod)
        has_sys_path = "sys.path" in source
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level >= 1:
                single_mode_imports.append(f"{mod}:{node.lineno} (unguarded relative import breaks direct CLI)")
            elif not has_sys_path and ((node.module or "") == "core" or (node.module or "").startswith("core.")):
                single_mode_imports.append(f"{mod}:{node.lineno} (unguarded 'from core' breaks python -m scripts.{mod})")
    assert non_cli == [], (
        f"Top-level scripts/*.py must be CLI entrypoints (move shared libs to scripts/core/): {non_cli}"
    )
    assert single_mode_imports == [], (
        f"Top-level CLIs must support both 'python scripts/<cli>.py' and 'python -m scripts.<cli>': {single_mode_imports}"
    )


def test_circular_sccs_do_not_grow_beyond_legacy_core_baseline() -> None:
    mods, adj = _collect_modules()
    missing = sorted(LEGACY_CORE_SCC - set(mods))
    assert missing == [], f"LEGACY_CORE_SCC contains stale missing modules: {missing}"
    sccs = _tarjan_sccs(set(mods), adj)
    assert len(sccs) <= 1, f"Unexpected new circular SCCs introduced: {sccs}"
    if sccs:
        actual = set(sccs[0])
        unknown = actual - LEGACY_CORE_SCC
        assert unknown == set(), f"Modules added to legacy circular SCC: {sorted(unknown)}"
        ratchet_candidates = LEGACY_CORE_SCC - actual
        assert ratchet_candidates == set(), (
            f"Modules left LEGACY_CORE_SCC; shrink LEGACY_CORE_SCC baseline immediately: {sorted(ratchet_candidates)}"
        )


def test_l0_foundation_has_no_upward_dependencies() -> None:
    mods, adj = _collect_modules()
    missing = sorted(L0_FOUNDATION_MODULES - set(mods))
    assert missing == [], f"L0_FOUNDATION_MODULES contains missing modules: {missing}"
    assert adj.get("core", set()) == set(), (
        f"scripts/core/__init__.py must remain zero-import, got: {sorted(adj.get('core', set()))}"
    )
    upward = {
        m: sorted(adj[m] - L0_FOUNDATION_MODULES)
        for m in sorted(L0_FOUNDATION_MODULES)
        if adj[m] - L0_FOUNDATION_MODULES
    }
    assert upward == {}, f"L0 foundation modules must not import L1-L4 modules: {upward}"
    l0_adj = {m: adj[m] & L0_FOUNDATION_MODULES for m in L0_FOUNDATION_MODULES}
    assert _tarjan_sccs(set(L0_FOUNDATION_MODULES), l0_adj) == [], (
        "L0 foundation modules must form an acyclic DAG"
    )


def test_l1_primitives_have_no_upward_dependencies() -> None:
    mods, adj = _collect_modules()
    missing = sorted(L1_PRIMITIVE_MODULES - set(mods))
    assert missing == [], f"L1_PRIMITIVE_MODULES contains missing modules: {missing}"
    allowed = L0_FOUNDATION_MODULES | L1_PRIMITIVE_MODULES
    upward = {
        m: sorted(adj[m] - allowed)
        for m in sorted(L1_PRIMITIVE_MODULES)
        if adj[m] - allowed
    }
    assert upward == {}, f"L1 primitive modules must not import L2-L4 modules: {upward}"
    l1_adj = {m: adj[m] & L1_PRIMITIVE_MODULES for m in L1_PRIMITIVE_MODULES}
    assert _tarjan_sccs(set(L1_PRIMITIVE_MODULES), l1_adj) == [], (
        "L1 primitive modules must form an acyclic DAG"
    )


def test_l2_rule_contracts_have_no_upward_dependencies() -> None:
    mods, adj = _collect_modules()
    missing = sorted(L2_RULE_CONTRACT_MODULES - set(mods))
    assert missing == [], f"L2_RULE_CONTRACT_MODULES contains missing modules: {missing}"
    allowed = L0_FOUNDATION_MODULES | L1_PRIMITIVE_MODULES | L2_RULE_CONTRACT_MODULES
    upward = {
        m: sorted(adj[m] - allowed)
        for m in sorted(L2_RULE_CONTRACT_MODULES)
        if adj[m] - allowed
    }
    assert upward == {}, (
        f"L2 rule/contract modules must not import high-level orchestration or L3-L4 modules: {upward}"
    )
    l2_adj = {m: adj[m] & L2_RULE_CONTRACT_MODULES for m in L2_RULE_CONTRACT_MODULES}
    assert _tarjan_sccs(set(L2_RULE_CONTRACT_MODULES), l2_adj) == [], (
        "L2 rule/contract modules must form an acyclic DAG"
    )
    gb_callers = sorted(
        m for m in mods
        if m.startswith("core.") and m != "core.generation_boundaries" and "core.generation_boundaries" in adj[m]
    )
    assert gb_callers == [], (
        f"core.* modules must import core.boundary_rules / core.quality_rules, not CLI facade core.generation_boundaries: {gb_callers}"
    )


def test_l2_rule_splits_reexports_and_monkeypatch_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for root in (str(REPO_ROOT), str(SCRIPTS_ROOT)):
        if root not in sys.path:
            monkeypatch.syspath_prepend(root)
    from scripts.core import (
        acceptance_rules,
        agent_acceptance,
        artifacts,
        boundary_rules,
        candidate_test,
        generation_boundaries,
        quality_evidence,
        quality_rules,
        revision_store,
        schema_contracts,
    )

    for attr in (
        "IMAGE_ASPECTS",
        "DRAFT_ASPECTS",
        "PACKAGING_ASPECTS",
        "MAX_GENERATION_SEGMENT_SECONDS",
        "fingerprint",
        "draft_binding",
        "validate_draft_evidence",
        "validate_required_visuals",
        "validate_scene_state_coverage",
        "validate_shot_durations",
    ):
        assert getattr(quality_evidence, attr) is getattr(quality_rules, attr)
    for attr in (
        "validate_delivery_cutpoints",
        "validate_director_body",
        "diagnose_scene_state_plan",
    ):
        assert getattr(generation_boundaries, attr) is getattr(boundary_rules, attr)
    assert callable(generation_boundaries.validate_generation_boundaries)
    assert callable(generation_boundaries.validate_delivery_parameters)
    for attr in (
        "MAX_GENERATION_SEGMENT_SECONDS",
        "validate_required_visuals",
        "validate_scene_state_coverage",
        "validate_shot_durations",
    ):
        assert getattr(generation_boundaries, attr) is getattr(quality_rules, attr)
    for attr in (
        "WORKFLOW",
        "SKILLS",
        "ASSET_ADOPTION_SKILL",
        "AUTHORIZATION_SKILLS",
        "CORE_CHECKS",
        "skill_for_artifact",
        "require_workflow_authorization",
        "register_workflow_authorization",
    ):
        assert getattr(agent_acceptance, attr) is getattr(acceptance_rules, attr)
    assert candidate_test.save_manifest is revision_store.save_manifest

    short_schema = importlib.import_module("core.schema_contracts")
    assert short_schema.SchemaContractError is schema_contracts.SchemaContractError

    # Verify save_manifest monkeypatch hooks and formal-history protection invariants
    pkg_file = tmp_path / "02_编导" / "narrative_v1.json"
    pkg_file.parent.mkdir(parents=True, exist_ok=True)
    pkg_file.write_text('{"revision": "v1"}\n', encoding="utf-8")
    pkg_sha = artifacts.digest(pkg_file)
    manifest_file = tmp_path / artifacts.MANIFEST_PATH
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    initial_manifest = {
        "schema_version": "1.0",
        "artifacts": [
            {
                "artifact_id": "NAR-1",
                "logical_id": "narrative.package",
                "type": "narrative-package",
                "revision": "v1",
                "status": "current",
                "path_or_url": "02_编导/narrative_v1.json",
                "checksum": pkg_sha,
                "depends_on": [],
                "user_approved": True,
            }
        ],
    }
    artifacts.write_json(manifest_file, initial_manifest)
    manifest_reads: list[Path] = []
    manifest_writes: list[Path] = []
    orig_read = artifacts.read_json
    orig_write = artifacts.write_json
    monkeypatch.setattr(artifacts, "read_json", lambda p: (manifest_reads.append(Path(p)), orig_read(p))[1])
    monkeypatch.setattr(
        artifacts, "write_json", lambda p, v: (manifest_writes.append(Path(p)), orig_write(p, v))[1]
    )
    updated_manifest = {
        "schema_version": "1.0",
        "artifacts": [
            dict(initial_manifest["artifacts"][0]),
            {
                "artifact_id": "DIR-CAND-1",
                "logical_id": "storyboard.package",
                "type": "storyboard-package",
                "revision": "v1",
                "status": "candidate",
                "path_or_url": "03_导演/storyboard_v1.json",
                "checksum": "a" * 64,
                "depends_on": ["narrative.package"],
                "user_approved": False,
            },
        ],
    }
    artifacts.save_manifest(tmp_path, updated_manifest)
    assert manifest_reads == [manifest_file]
    assert manifest_writes == [manifest_file]
    with pytest.raises(ValueError, match="不能删除 current 正式稿件历史记录"):
        artifacts.save_manifest(tmp_path, {"schema_version": "1.0", "artifacts": []})
    tampered = dict(initial_manifest["artifacts"][0], checksum="b" * 64)
    with pytest.raises(ValueError, match="不能改写 current 正式稿件身份、来源或确认 hash"):
        artifacts.save_manifest(tmp_path, {"schema_version": "1.0", "artifacts": [tampered]})
    unadopted_promotion = dict(updated_manifest["artifacts"][1], status="current")
    with pytest.raises(ValueError, match="不能晋升或恢复正式稿件"):
        artifacts.save_manifest(
            tmp_path,
            {"schema_version": "1.0", "artifacts": [dict(initial_manifest["artifacts"][0]), unadopted_promotion]},
        )

    # Verify agent_acceptance monkeypatch hooks and edge-case validation
    aa_reads: list[Path] = []
    aa_hashes: list[Path] = []
    orig_aa_read = agent_acceptance.read_json
    orig_aa_hash = agent_acceptance.sha256_file
    monkeypatch.setattr(
        agent_acceptance, "read_json", lambda p: (aa_reads.append(Path(p)), orig_aa_read(p))[1]
    )
    monkeypatch.setattr(
        agent_acceptance, "sha256_file", lambda p: (aa_hashes.append(Path(p)), orig_aa_hash(p))[1]
    )
    cand_file = tmp_path / "03_导演" / "storyboard_v1.json"
    cand_file.parent.mkdir(parents=True, exist_ok=True)
    cand_file.write_text('{"revision": "v1"}\n', encoding="utf-8")
    cand_sha = orig_aa_hash(cand_file)
    binding = agent_acceptance.acceptance_binding(
        tmp_path,
        {
            "artifact_id": "DIR-CAND-1",
            "logical_id": "storyboard.package",
            "type": "storyboard-package",
            "revision": "v1",
            "status": "candidate",
            "path_or_url": "03_导演/storyboard_v1.json",
            "checksum": cand_sha,
            "depends_on": ["narrative.package"],
        },
    )
    assert binding["artifact"]["sha256"] == cand_sha
    assert [u["artifact_id"] for u in binding["upstreams"]] == ["NAR-1"]
    assert aa_reads == [manifest_file.resolve()]
    assert aa_hashes == [cand_file.resolve(), pkg_file.resolve()]
    empty_file = tmp_path / "03_导演" / "empty.json"
    empty_file.write_bytes(b"")
    with pytest.raises(ValueError, match="身份、修订或文件为空"):
        agent_acceptance.acceptance_binding(
            tmp_path,
            {
                "artifact_id": "DIR-EMPTY",
                "logical_id": "storyboard.package",
                "type": "storyboard-package",
                "revision": "v1",
                "status": "candidate",
                "path_or_url": "03_导演/empty.json",
                "depends_on": [],
            },
        )

    # Verify review-profile hash hook on agent_acceptance + direct artifacts.{read,validate}_agent_acceptance hooks
    cand_file.write_text('{"revision": "v1", "status": "candidate"}\n', encoding="utf-8")
    cand_sha = orig_aa_hash(cand_file)
    dir_entry = {
        "artifact_id": "DIR-CAND-2",
        "logical_id": "storyboard.package",
        "type": "storyboard.package",
        "revision": "v1",
        "status": "candidate",
        "path_or_url": "03_导演/storyboard_v1.json",
        "checksum": cand_sha,
        "depends_on": ["narrative.package"],
        "user_approved": False,
    }
    state_data = {
        "workflow_authorizations": [
            {
                "id": "AUTH-1",
                "workflow": "review-to-canvas",
                "project_path": str(tmp_path.resolve()),
                "authorization_ref": "user:auto",
                "skills": ["storyboard-planner"],
                "status": "active",
                "created_at": "2026-11-27T00:00:00Z",
            }
        ]
    }
    profile_path = REPO_ROOT / "skills" / "storyboard-planner" / "review-profile.json"
    profile_data = orig_read(profile_path)
    report_path = tmp_path / "03_导演" / "self_check.json"
    orig_write(
        report_path,
        {
            "schema_version": "1.0",
            "kind": "agent-self-check",
            "scope": "artifact",
            "verdict": "pass",
            "agent_id": "tester",
            "checked_at": "2026-11-27T00:01:00Z",
            "authorization_id": "AUTH-1",
            "review_profile_sha256": orig_aa_hash(profile_path),
            **agent_acceptance.acceptance_binding(tmp_path, dir_entry),
            "checks": [
                {"id": item["id"], "result": "pass", "observation": "ok", "locator": "scenes[0]"}
                for item in profile_data["production_artifact_checks"]
            ],
        },
    )
    aa_hashes.clear()
    pointer = agent_acceptance.build_agent_acceptance(tmp_path, dir_entry, report_path, state=state_data)
    assert profile_path.resolve() in [p.resolve() for p in aa_hashes]
    dir_entry["agent_acceptance"] = pointer

    art_hook_digests: list[Path] = []
    orig_art_digest = artifacts.digest
    monkeypatch.setattr(
        artifacts, "digest", lambda p: (art_hook_digests.append(Path(p)), orig_art_digest(p))[1]
    )
    manifest_reads.clear()
    ptr2, _ = artifacts.read_agent_acceptance(tmp_path, dir_entry, report_path, state=state_data)
    assert ptr2 == pointer
    artifacts.validate_agent_acceptance(tmp_path, dir_entry, state=state_data)
    assert profile_path.resolve() in [p.resolve() for p in art_hook_digests]
    assert manifest_reads == [manifest_file.resolve(), manifest_file.resolve()]

    # Verify generation_boundaries.load_kickoff monkeypatch propagates into boundary_rules
    kickoff_calls: list[Path] = []
    monkeypatch.setattr(
        generation_boundaries,
        "load_kickoff",
        lambda root: (kickoff_calls.append(Path(root)), {"product": {"product_id": "creator-polishing-mask-2"}})[1],
    )
    with pytest.raises(ValueError, match="还送高定条膜碗刷"):
        generation_boundaries.validate_generation_boundaries(
            {
                "schema_version": "2.0",
                "generation_segments": [
                    {
                        "segment_id": "SEG-01",
                        "source_shot_ids": ["S01-001"],
                        "prompt": "口播：送高定调膜碗刷",
                        "parameters": {"duration": 5},
                    }
                ],
            },
            {"schema_version": "5.0", "scenes": [{"scene_id": "S01", "shots": [{"shot_id": "S01-001"}]}]},
            tmp_path,
            require_physical=False,
        )
    assert kickoff_calls == [tmp_path.resolve()]
    kickoff_calls.clear()
    with pytest.raises(ValueError, match="真实时长上限不完整"):
        generation_boundaries.validate_delivery_parameters(
            [{"source_segment_id": "SEG-01", "parameters": {"duration": 5}}],
            {
                "generation_segments": [{"segment_id": "SEG-01", "parameters": {"duration": 5}}],
                "selected_video_model": {"confirmed_by_user": False},
            },
            tmp_path,
        )
    assert kickoff_calls == [tmp_path.resolve()]


def test_l1_store_reexports_and_boundary_invariants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.core import (
        artifacts,
        candidate_test,
        manifest_store,
        project_state,
        prompt_body,
        quality_evidence,
        revision_control,
        revision_store,
    )

    for attr in ("MANIFEST_PATH", "ARTIFACT_STATUSES", "load_manifest", "current_artifacts", "project_path", "digest"):
        assert getattr(artifacts, attr) is getattr(manifest_store, attr)
    for attr in ("source_shot_ids", "scene_state_requirements", "reference_shot_ids", "submitted_prompt_body"):
        assert getattr(quality_evidence, attr) is getattr(prompt_body, attr)
    for attr in ("CONTROL_RELATIVE", "CONTROL_SCHEMA_VERSION", "LOCK_RELATIVE", "TIER_VALUES", "_generation_segments"):
        assert getattr(revision_control, attr) is getattr(revision_store, attr)
    assert candidate_test.validate_context is project_state.validate_candidate_test_context
    assert candidate_test.validate_stage is project_state.validate_candidate_test_stage
    assert candidate_test.require_production is revision_store.require_production

    # Verify monkeypatch hooks on artifacts (digest, read_json) and revision_control (read_json)
    bundled_file = tmp_path / "bundled.json"
    bundled_file.write_text('{"ok": true}\n', encoding="utf-8")
    bundled_sha = manifest_store.digest(bundled_file)
    control_path = tmp_path / revision_control.CONTROL_RELATIVE
    control_path.parent.mkdir(parents=True, exist_ok=True)
    revision_control.write_json(
        control_path,
        {
            "schema_version": revision_control.CONTROL_SCHEMA_VERSION,
            "active_change": {"change_id": "CHG-1", "frozen_logical_ids": ["storyboard.package"]},
            "history": [
                {
                    "change_id": "CHG-0",
                    "status": "completed",
                    "candidate_confirmation": {
                        "scope": "artifact",
                        "change_id": "CHG-0",
                        "approval_ref": "user:ok",
                    },
                    "candidate_receipt": {
                        "bundle": [{"artifact_id": "ART-1", "path": "bundled.json", "sha256": bundled_sha}]
                    },
                }
            ],
        },
    )
    rc_reads: list[Path] = []
    orig_rc_read = revision_control.read_json
    monkeypatch.setattr(revision_control, "read_json", lambda p: (rc_reads.append(Path(p)), orig_rc_read(p))[1])
    assert revision_control.show_change(tmp_path)["active_change"]["change_id"] == "CHG-1"
    with pytest.raises(revision_control.RevisionControlError, match="冻结分支"):
        revision_control.guard_action(tmp_path, logical_ids=["storyboard.package"])
    assert rc_reads == [control_path.resolve(), control_path.resolve()]

    art_reads: list[Path] = []
    art_digests: list[Path] = []
    orig_art_read = artifacts.read_json
    orig_art_digest = artifacts.digest
    monkeypatch.setattr(artifacts, "read_json", lambda p: (art_reads.append(Path(p)), orig_art_read(p))[1])
    monkeypatch.setattr(artifacts, "digest", lambda p: (art_digests.append(Path(p)), orig_art_digest(p))[1])
    adopted = artifacts.formal_adoption({"artifact_id": "ART-1", "checksum": bundled_sha}, tmp_path)
    assert adopted == {"change_id": "CHG-0", "approval_ref": "user:ok", "sha256": bundled_sha}
    cached_digest = artifacts.cache_file_digests()
    assert cached_digest(bundled_file) == bundled_sha
    assert cached_digest(bundled_file) == bundled_sha
    assert art_reads == [control_path]
    assert art_digests == [bundled_file.resolve(), bundled_file]

    assert manifest_store.load_manifest(tmp_path) == {"schema_version": "1.0", "artifacts": []}
    with pytest.raises(ValueError, match="重复 artifact_id"):
        manifest_store._assert_unique_artifact_ids({"artifacts": [{"artifact_id": "A1"}, {"artifact_id": "A1"}]})
    with pytest.raises(ValueError, match="事务至少需要一个状态更新"):
        revision_store._commit_json_transaction_unlocked(tmp_path, {}, reason="empty")
    monkeypatch.setenv("MSB_PROJECT_PATH_MAP", '{"C:/src": "' + str(tmp_path) + '"}')
    with pytest.raises(ValueError, match="escapes its configured root"):
        manifest_store.project_path(tmp_path, "C:/src/../outside.json")


def test_compat_shims_are_thin_reexports() -> None:
    mods, _ = _collect_modules()
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    non_thin: list[str] = []
    missing_symbols: dict[str, list[str]] = {}
    for shim in sorted(COMPAT_SHIMS):
        assert shim in mods, f"Declared compat shim not found: {shim}"
        source = mods[shim].read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(mods[shim]))
        has_defs = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for node in tree.body
        )
        if has_defs or len(source.splitlines()) > 30:
            non_thin.append(shim)
        core_mod = importlib.import_module(f"scripts.core.{shim}")
        shim_mod = importlib.import_module(f"scripts.{shim}")
        core_exports = {
            k for k, v in vars(core_mod).items()
            if not k.startswith("__")
            and (
                not callable(v)
                or getattr(v, "__module__", "").startswith("scripts.core.")
            )
            and not isinstance(v, type(sys))
        }
        # Exclude stdlib/typing imports bound in core_mod; check constants + functions defined/re-exported from scripts.core
        core_ast = ast.parse((SCRIPTS_ROOT / "core" / f"{shim}.py").read_text(encoding="utf-8"))
        declared: set[str] = set()
        for node in core_ast.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                declared.add(node.name)
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        declared.add(tgt.id)
            elif isinstance(node, ast.ImportFrom) and node.level:
                for alias in node.names:
                    if alias.name != "*":
                        declared.add(alias.asname or alias.name)
        dropped = sorted((declared & core_exports) - set(vars(shim_mod)))
        if dropped:
            missing_symbols[shim] = dropped
    assert non_thin == [], f"Compat shims must be thin re-exports only (no defs, <=30 lines): {non_thin}"
    assert missing_symbols == {}, f"Compat shims dropped symbols from scripts.core.*: {missing_symbols}"


def test_agents_and_claude_entry_stay_synced_and_link_architecture() -> None:
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert agents == claude, "AGENTS.md and CLAUDE.md must remain byte-for-byte identical"
    lines = agents.splitlines()
    arch_lines = [line for line in lines if "docs/ARCHITECTURE.md" in line]
    assert len(arch_lines) == 1, "AGENTS.md must link docs/ARCHITECTURE.md exactly once"
    assert "常规制作开工不读" in arch_lines[0], (
        "docs/ARCHITECTURE.md must stay under the maintenance-only entry, not regular production startup"
    )


def test_rhtv_js_layering_and_cross_language_boundaries() -> None:
    js_files = {p.name: p for p in sorted(SCRIPTS_ROOT.glob("*.js"))}
    expected_js = {
        "rhtv_rich_editor.js",
        "rhtv_target.js",
        "rhtv_canvas.js",
        "rhtv_cdp.js",
        "rhtv_shot.js",
    }
    assert set(js_files) == expected_js, f"Unexpected or missing scripts/*.js files: {sorted(js_files)}"

    local_require_re = re.compile(r"""require\(\s*['"](\.[^'"]+)['"]\s*\)""")
    any_require_re = re.compile(r"""\brequire\s*\(""")
    esm_import_re = re.compile(r"""(?:^\s*import\s|\bimport\s*\()""", re.MULTILINE)

    for js_name, js_path in js_files.items():
        js_src = js_path.read_text(encoding="utf-8")
        assert not esm_import_re.search(js_src), f"{js_name} must stay CommonJS/DOM-only without ESM import"

    # JS-L0: rhtv_rich_editor.js must stay a pure, zero-dependency browser DOM callback
    rich_src = js_files["rhtv_rich_editor.js"].read_text(encoding="utf-8")
    assert not any_require_re.search(rich_src), "rhtv_rich_editor.js must have zero require() dependencies"
    for forbidden in ("fetch(", "WebSocket", "node:fs", "child_process", "playwright"):
        assert forbidden not in rich_src, f"rhtv_rich_editor.js must remain pure read-only DOM code (found {forbidden})"

    # JS-L1: rhtv_target.js may only require ./rhtv_rich_editor locally and never Playwright/CLIs
    target_src = js_files["rhtv_target.js"].read_text(encoding="utf-8")
    target_local_deps = {Path(m).with_suffix(".js").name for m in local_require_re.findall(target_src)}
    assert target_local_deps <= {"rhtv_rich_editor.js"}, (
        f"rhtv_target.js must only depend on rhtv_rich_editor.js locally, got: {sorted(target_local_deps)}"
    )
    assert "playwright" not in target_src, "rhtv_target.js must stay lightweight CDP without Playwright"

    # JS-L2: CLI scripts must define main() and only require ./rhtv_target locally
    for cli_name in ("rhtv_canvas.js", "rhtv_cdp.js", "rhtv_shot.js"):
        cli_src = js_files[cli_name].read_text(encoding="utf-8")
        assert "function main(" in cli_src, f"{cli_name} must define a CLI main() entrypoint"
        cli_local_deps = {Path(m).with_suffix(".js").name for m in local_require_re.findall(cli_src)}
        assert cli_local_deps <= {"rhtv_target.js"}, (
            f"{cli_name} must only depend on rhtv_target.js locally, got: {sorted(cli_local_deps)}"
        )

    # Cross-language boundary: Python core/* must not shell out to rhtv_* scripts
    cli_invocations: list[str] = []
    for py_path in sorted((SCRIPTS_ROOT / "core").glob("*.py")):
        py_src = py_path.read_text(encoding="utf-8")
        for cli_name in ("rhtv_canvas.js", "rhtv_canvas.cmd", "rhtv_cdp.js", "rhtv_shot.js", "rhtv_target.js"):
            if cli_name in py_src:
                cli_invocations.append(f"{py_path.name} -> {cli_name}")
    assert cli_invocations == [], f"scripts/core/*.py must not shell out to rhtv_* scripts: {cli_invocations}"
