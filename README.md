# Master-Storyboarding

Private source repository. Repository setup started on 2026-10-08 UTC.

## Current status

This main branch contains repository setup documentation only. The verified current production source tree and its original Git history have not been recovered here. No recovered candidate is approved or merged as production.

The production baseline reported before recovery was commit `6120d39965f530da489e1756c2e6dc9cd6392c57`, with additional uncommitted work. The corresponding Git objects and dirty working tree are currently unavailable, so this repository cannot yet provide rollback to that state.

## Branch use

- `main`: reserved for a verified, reviewed production baseline.
- `recovered-candidate/2026-10-08`: isolated, filtered historical reconstruction if and when uploaded; read its recovery notice before using it.
- Future fixes should use separate branches, be tested against the accepted baseline, and be merged only with explicit approval. Version tags should identify tested, accepted commits; none is created at setup.

## Source and assets

Source, schemas, contracts, and reproducible tests belong in Git. Credentials, environment secrets, raw footage, customer project inputs, generated media, and asset-library files stay outside this repository. Recovering those external assets is a separate prerequisite for production.

Branch protections, CI, deployments, and credentials are not implied by this setup document.

## Isolated audit branch

On `audit/20261017`, source files are restored from the verified 2026-10-14 mask-r3 historical package. This is not the 2026-10-16 assets-v2 production tree. See [audit provenance and validation](docs/development/audit-20261017.md). Main is not merged or changed.
