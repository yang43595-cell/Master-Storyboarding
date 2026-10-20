# Knowledge sync candidate CLI

An opt-in local export importer and fixed-cloud-snapshot runner. Not a production default, Mac connector, scheduler, or permission sandbox. No source knowledge, real exports, user dialogue, credentials, runtime receipts, or device configuration belongs in this source directory.

## Dependencies and tests

Python 3.10+ standard library. Build and baseline import reuse this repository's `scripts/core/knowledge_blocks.py` (`build_knowledge_index`, `load_policy`) and `references/workflow/knowledge-policy.json`. Do not copy or fork the parser. Use one repository revision per Python process; imported parser modules are process-cached.

From the repository root:

```sh
python tools/knowledge_sync/test_knowledge_sync.py
python tools/knowledge_sync/knowledge_sync.py --help
```

The 20 synthetic tests locate the repository relative to this directory. For an isolated source staging directory only, set `MSB_TEST_REPO` to the repository under test. Test fixtures are generated in temporary directories and removed afterward. No Mac access or original knowledge is required.

## Local export contract

The caller must already have authorization for the selected source. A CLI argument is not a grant of permission. New sources must be inside the authorized wiki directory; the caller supplies its exact root using required `--source-root`. No personal source root is stored in source code or default configuration. Each external store binds to one exact source root on its first valid import attempt; another root is rejected even for identical bytes. Wiki source root is also part of snapshot identity. A wiki-bearing base with a different or missing source-root identity is rejected. Only the verified historical project-only baseline can acquire its first wiki root. This is not multi-source support; older P1 wiki snapshots require explicit migration review rather than silent reuse. Runtime source-root bindings remain private external state, not committed configuration.

The local directory contains `export.json` and `payload/wiki/...`. Manifest fields:

- `schema_version`: `1.0`
- `transport`: `authorized_mac_task`, `library_delivery`, or `synthetic_fixture`
- `source_root`: exact authorized absolute directory ending in `wiki`
- `exported_at`: timezone-qualified timestamp for non-synthetic exports
- `complete`: explicit boolean; `scope`: exact `wiki/` file paths
- `files`: records containing `path`, `source_id`, `sha256`, `size`, `mtime`
- `base_snapshot`: selected base ID, or null when the source exporter has no cloud base
- `removals`: explicit paths, normally empty; preserve any `missing_requested` information

A path-based source ID is not a stable identity across renames. Missing invalidation data is unknown, not proof that no invalidation exists. Do not place real export directories inside the repository.

```sh
python tools/knowledge_sync/knowledge_sync.py inspect --export "$EXPORT_DIR" --source-root "$AUTHORIZED_WIKI_ROOT"
python tools/knowledge_sync/knowledge_sync.py build --export "$EXPORT_DIR" --source-root "$AUTHORIZED_WIKI_ROOT" --store "$EXTERNAL_STORE" --repo .
```

For an incremental update add `--base "$SNAPSHOT_ID"`. Build validates bytes and rule identities before publishing a candidate. Partial exports retain other sources; identical content reuses the same version. Explicit removals write a pending-review marker and reject the build: dependency-aware retirement is not implemented. Failed imports preserve the last successful snapshot and record failure; freshness never claims a live Mac connection.

`seed-baseline` is a narrowly scoped migration operation requiring a `verified_P0_baseline` receipt. It accepts only historical `projects/Master-Storyboarding/` paths with matching bytes, not a second channel for new project knowledge.

## Existing execution mechanism

`run` validates a snapshot, pins a project/task ID, exports `MSB_VAULT_ROOT`, then invokes the supplied existing command. It does not replace `scripts/stage_context_cli.py`, prepare/reading/ensure, Skill gates, or the deployment's launcher. Preserve whichever normal launcher the environment requires; this source package deliberately does not invent one. Project/task path arguments in the wrapped command must match the pin.

```sh
python tools/knowledge_sync/knowledge_sync.py run --store "$EXTERNAL_STORE" --snapshot "$SNAPSHOT_ID" --project "$ABSOLUTE_PROJECT" --task-id "$TASK_ID" -- "$NORMAL_LAUNCHER" python scripts/stage_context_cli.py prepare --project "$ABSOLUTE_PROJECT" --skill storyboard-planner --actor-id "$ACTOR_ID" --task-id "$TASK_ID" --candidate-test --objective "$OBJECTIVE" --request-json "$REQUEST_JSON"
```

Candidate-test state must already satisfy the original CLI's requirements. Each new author/task must actually read its own prepared packet before recording reading. Successful ensure does not create user confirmation or production authorization.

Snapshots are conventionally immutable ordinary files, checked at entry points. This is not an OS lock or sandbox; in-flight external edits are detected only at the next check. Direct calls bypassing this wrapper remain possible. Deletion automation, concurrent writer stress tests, live transport, and production completeness are not established by this suite.

## Portability adaptation and recovery

This source candidate adapts the separately delivered P1 prototype by replacing a private hardcoded wiki root with a mandatory exact-match parameter, and resolving the test repository relative to this location. Source-root mismatch and malformed-root rejection are checked inside the existing path test. The portability review also found and fixed cross-root provenance collisions introduced by parameterization: store binding and snapshot identity now enforce one source root, covered by two additional negative tests. Original P1 artifacts and production defaults remain untouched; their runtime evidence is not automatically evidence for this adapted entry point.

Stop invoking this opt-in CLI to return to the existing workflow. Do not delete snapshots, overwrite project output, or change active bindings as part of source installation or rollback. Keep runtime stores outside Git. Run records may contain private absolute paths and should never be committed without separate review.
