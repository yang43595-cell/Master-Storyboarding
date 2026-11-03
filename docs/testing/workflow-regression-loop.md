# Evidence-driven engineering regression loop

`scripts/workflow_regression_loop.py` records engineering rounds outside the repository and business project. It does not fix code autonomously, dispatch agents, approve production, or prove that a person read a method. The working sequence is: reproduce on a frozen source → identify the root cause → bounded repair → tests → independent review → fresh blind execution. Repeat when new evidence justifies it, not until an arbitrary retry count expires.

## Freeze the required scope

Create a JSON plan with exactly `case_ids`, `case_kinds`, `pytest_targets`, and `node_targets`. Every case has kind `natural`, `synthetic`, or `risk_retest`; test targets are existing repository-relative paths. Include every required case before starting. Do not remove failed or externally blocked cases. A scope change needs a separately declared plan, with the original results retained.

```sh
python scripts/workflow_regression_loop.py init --workspace /absolute/new-evidence-directory --plan /absolute/plan.json
python scripts/workflow_regression_loop.py begin --workspace /absolute/new-evidence-directory --reason 'Reproduced failure and bounded root repair'
python scripts/workflow_regression_loop.py test --workspace /absolute/new-evidence-directory
```

Use the project's isolated Python environment and documented Node dependencies. The checkout must be committed and clean. `test` runs focused pytest, the complete `scripts/tests tests` suite (including root tests), then the selected Node suite using direct argv and actual exit codes. Logs, timestamps, skipped counts, and individual failure fingerprints remain available. A passing command with skipped tests is not evidence that skipped capabilities were tested: the required coverage must still mark these `not_tested` or `blocked_external` where applicable.

Git commit/tree verification proves source identity only. Attach the actual runtime/import/dependency verification report as external evidence; do not call an old test log a currently loaded runtime. Ordinary test dependency failures remain failures/blocked evidence until diagnosed, never automatically a code bug.

## Independent receipts and fresh execution

`record --phase review|blind --receipt /absolute/receipt.json` accepts a receipt containing:

- exact `snapshot: {commit, tree}`, `phase`, and actual `reviewer`;
- `coverage` with every original case, each classified as `pass`, `fail`, `correctly_blocked`, `blocked_external`, `not_tested`, or `out_of_authorization`;
- nonempty `evidence_files: [{path, sha256}]` identifying actual immutable evidence;
- for blind execution, `fresh_checkout` at the same clean source, a distinct reviewer, and `fresh_execution: true` describing a genuinely fresh execution, not copied old results.

These declarations remain reviewable claims, not a substitute for inspecting the evidence. The controller must not reveal defect explanations or expected answers to fresh natural executors. Partial review/blind coverage stays incomplete; a correctly blocked natural run does not prove its downstream workflow. The runner never turns business authorization or cognitive assertions into a PASS.

## Continue and recover

`status` rechecks source and evidence bytes, including the current round’s reason evidence; changes invalidate old receipts. A new round requires a new frozen source or `--reason-evidence` pointing to materially new evidence (for example restoration of a missing dependency). Changing a reason sentence or copying identical evidence to a different path is insufficient. Evidence content already used for the same commit/tree cannot justify another round, even after intervening rounds or a return to that source. Byte differences are only a necessary identity check; independent review still judges whether the change is materially relevant. Preserve each old round, including failed logs. Interrupted rounds retain completed command logs and must not be overwritten. Copying an archive to a new path is not automatic run-identity migration: inspect saved absolute paths and begin a new explicitly identified round when necessary.

The script does not fetch logs from restricted locations or install software. When external evidence is unavailable, record the exact blocker, continue independent testable work, and resume that case when conditions change. `all_required_evidence_passed` remains false while any required evidence is missing; `production_authorized` is always false.
