# Master-Storyboarding

Private source repository. Repository setup started on 2026-10-08 UTC.

## Current status

The `main` branch contains repository setup documentation only. The verified current production source tree and its original Git history have not been recovered here. No recovered candidate is approved or merged as production.

The production baseline reported before recovery was commit `6120d39965f530da489e1756c2e6dc9cd6392c57`, with additional uncommitted work. The corresponding Git objects and dirty working tree are currently unavailable, so this repository cannot yet provide rollback to that state.

## Branch use

- `main`: reserved for a verified, reviewed production baseline.
- `audit/20261017`: verified historical mask-r3 baseline plus the bounded project repairs; current cloud audit work belongs here.
- `audit/review-web-20261017`: separately reviewed website patches; not the project source entry.
- `backup/*` and notices under `recovery/`: historical provenance, not current production entry points.
- Future fixes should use separate branches, be tested against the accepted baseline, and be merged only with explicit approval. Version tags should identify tested, accepted commits; none is created at setup.

## Source and assets

Source, schemas, contracts, and reproducible tests belong in Git. Credentials, environment secrets, raw footage, customer project inputs, generated media, and asset-library files stay outside this repository. Recovering those external assets is a separate prerequisite for production.

Branch protections, CI, deployments, and credentials are not implied by this setup document.

## Isolated audit branch

On `audit/20261017`, source files are restored from the verified 2026-10-14 mask-r3 historical package. This is not the 2026-10-16 assets-v2 production tree. See [audit provenance and validation](docs/development/audit-20261017.md). Main is not merged or changed.

## Current cloud audit working entry

Use `/workspace/msb-recovery/repo` on `audit/20261017` for ongoing cloud work. It was fast-forwarded to the already verified `6f76b2f` repair combination before final regression. Keep `scripts/`, `skills/`, and `schemas/` in place. `/workspace/msb-recovery/history-compat-check` is a retained auxiliary worktree at `6f76b2f`, not a second current source. No production entry or user device was switched.

For this recovered environment, select dependencies explicitly:

- Knowledge: `MSB_VAULT_ROOT=/workspace/msb-recovery/knowledge/recovered_runtime/baseline/vault`. This is the recovered external knowledge baseline with the verified r3 current snapshot overlay, not an assets-v2 recovery.
- Website source for reference: `/workspace/msb-recovery/web-contract/site-source`, from the verified v78 archive. The adapter uses the pinned `references/web-contract-v78/` validation snapshot by default; supply `contractRoot` only for another verified source. Neither directory proves the live deployment version.
- Independent case directories: pass `--project` or `projectRoot`. For historical Windows absolute references use the existing `MSB_PROJECT_PATH_MAP`; do not guess roots or rewrite confirmation history. Media local paths remain explicit transport inputs.

The audit Python runtime is `/workspace/msb-recovery/venv/bin/python`; its Node schema bridge resolves Zod via `NODE_PATH=/workspace/msb-recovery/web-validator-runtime/node_modules`. These cloud paths describe this checkout only, not a required installation layout or a guarantee of persistent hosting. Do not use launchers or absolute production paths from the historical archive.

Final combined regression and the bounded issue-to-commit closure are recorded in [the existing audit report](docs/development/audit-20261017.md). Original production HEAD plus dirty changes, the 2026-10-16 assets-v2 delta, full historical case transfer, Windows111 replay and live website integration remain unverified. No merge, deployment or paid generation follows from passing local tests.
