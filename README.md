# Master-Storyboarding

Private, self-contained source repository for the Master-Storyboarding workflow.

New computer or agent: start with [standalone setup](docs/development/standalone-repository.md), then read `AGENTS.md` and the selected Skill. Production uses each Skill’s repository-owned rules, templates, methods, and references. The selected knowledge bundle is versioned in `knowledge/runtime/`.

For website review of the script and asset images, followed by agent self-checks and a saved canvas draft, use the [review-to-canvas entry](docs/development/review-to-canvas-entry.md). It records the agreed SOP and the integration work still required; it is not a working upload API or an automatic runner.

Image production runs in clean-context child agents, with up to 8 target images per batch. Each worker reads its inputs, methods and references, writes prompts, generates, inspects and corrects within authorization, then hands back exact files and usage evidence. Independent batches may run in parallel after shared references are ready; stage owners keep shared state and adoption. See [image batch rules](docs/rules/execution.md#图片生产批次) and [isolated preparation](skills/_shared/knowledge-access.md#图片批次的隔离准备). Asset images still require website human review; storyboard supplements retain their authorized agent acceptance path.

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

The default knowledge root is bundled with this repository. Explicit external knowledge roots, browser sessions, output libraries and business project data remain environment-specific; they are not silently discovered from an old computer. The adapter's pinned local website contract is for offline validation; it is not evidence of the live deployment version.
