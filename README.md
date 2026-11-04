# Master-Storyboarding

Private, self-contained source repository for the Master-Storyboarding workflow.

New computer or agent: start with [standalone setup](docs/development/standalone-repository.md), then read `AGENTS.md` and the selected Skill. The selected method runtime is versioned in `knowledge/runtime/`; Obsidian and a Mac-mounted Vault are not startup prerequisites.

## Current status

`main` is the formal source branch. Check out its current commit with `git rev-parse HEAD`; this README does not pin a commit that becomes stale as soon as `main` advances. A source branch or passing local checks does not by itself mean production adoption, deployment, or a project artifact has been confirmed.

## Branch use

- `main`: formal source branch; review and test proposed changes on a separate branch before adoption.
- `audit/review-web-20261017`: separately reviewed website patches; not the project source entry.
- `backup/*` and notices under `recovery/`: historical provenance, not current production entry points.
- Future fixes should use separate branches, be tested against the accepted baseline, and be merged only with explicit approval. Version tags should identify tested, accepted commits; none is created at setup.

## Source and assets

Source, schemas, contracts, and reproducible tests belong in Git. Credentials, environment secrets, raw footage, customer project inputs, generated media, and asset-library files stay outside this repository. Recovering those external assets is a separate prerequisite for production.

The proposed [repository checks](docs/development/dependency-checks.md) run existing synthetic Python/Node tests on pull requests, once the workflow is published and Actions use is authorized. Local passes do not establish a GitHub Actions result. Branch protections, deployments, and credentials are not configured by this source change.

The default knowledge root is bundled with this repository. Explicit external knowledge roots, browser sessions, output libraries and business project data remain environment-specific; they are not silently discovered from an old computer. The adapter's pinned local website contract is for offline validation; it is not evidence of the live deployment version.
