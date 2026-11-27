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
    "core.candidate_test",
    "core.canvas_readback",
    "core.character_boards",
    "core.delivery_files",
    "core.director_inputs",
    "core.draft_delivery",
    "core.generation_boundaries",
    "core.production_handoff",
    "core.quality_evidence",
    "core.recreation_assets",
    "core.recreation_segments",
    "core.review_publication",
    "core.revision_control",
    "core.schema_contracts",
    "core.story_sources",
    "core.visual_readiness",
})

COMPAT_SHIMS = frozenset({
    "visual_asset_choices",
    "visual_asset_product_usage",
    "visual_asset_source",
})

REDFRAME_MODULES = frozenset({
    "core.redframe_creative",
    "core.redframe_recreation",
    "core.redframe_draft_ui",
    "core.redframe_image_ui",
    "core.redframe_workspace",
    "redframe_draft",
})

L0_FOUNDATION_MODULES = frozenset({
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
    upward = {
        m: sorted(adj[m] - L0_FOUNDATION_MODULES)
        for m in sorted(L0_FOUNDATION_MODULES)
        if adj[m] - L0_FOUNDATION_MODULES
    }
    assert upward == {}, f"L0 foundation modules must not import L1-L4 modules: {upward}"


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
    for attr in ("CONTROL_RELATIVE", "guard_action", "show_change", "_generation_segments", "_validate_segment_reconciliation"):
        assert getattr(revision_control, attr) is getattr(revision_store, attr)
    assert candidate_test.validate_context is project_state.validate_candidate_test_context
    assert candidate_test.validate_stage is project_state.validate_candidate_test_stage
    assert candidate_test.require_production is revision_store.require_production

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
