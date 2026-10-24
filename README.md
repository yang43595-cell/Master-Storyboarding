# Master-Storyboarding

Private source repository. Repository setup started on 2026-10-08 UTC.

## Current status

`main` is the formal source branch. Check out its current commit with `git rev-parse HEAD`; this README does not pin a commit that becomes stale as soon as `main` advances. A source branch or passing local checks does not by itself mean production adoption, deployment, or a project artifact has been confirmed.

## Branch use

- `main`: formal source branch; review and test proposed changes on a separate branch before adoption.
- `audit/review-web-20261017`: separately reviewed website patches; not the project source entry.
- `backup/*` and notices under `recovery/`: historical provenance, not current production entry points.
- Future fixes should use separate branches, be tested against the accepted baseline, and be merged only with explicit approval. Version tags should identify tested, accepted commits; none is created at setup.

## Source and assets

Source, schemas, contracts, and reproducible tests belong in Git. Credentials, environment secrets, raw footage, customer project inputs, generated media, and asset-library files stay outside this repository. Recovering those external assets is a separate prerequisite for production.

Branch protections, CI, deployments, and credentials are not implied by this setup document.

Runtime paths, vaults, browser sessions and external project data are environment-specific and must be passed explicitly. The adapter's pinned local website contract is for offline validation; it is not evidence of the live deployment version.
