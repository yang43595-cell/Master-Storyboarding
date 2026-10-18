# Structured dependency and transfer checks, 2026-10-18

Base: f6a48c16a83572af9851fa705b0206a65ac1d80b, audit/20261017. Changes are source, synthetic tests and documentation only; no story/product assets, credentials, generation, publication or main merge.

## Implemented

- The existing direct-read product_refs CLI now supports --discover-dependencies. Selected JSON path/media fields, exact artifact IDs and selected manifest dependencies are traversed without requiring every reachable input to be manually repeated. Direct file selection also resolves its registered manifest dependencies. Exact artifact selection preserves legitimate same-byte aliases; ambiguous path-only selection fails rather than guessing a revision.
- canvas_readback.validate_submission calls the same discovery automatically, including existing preflight, refreshed readback and revision-control callers. Success reports actual checked files and bounded coverage. Existing roles/entities, current revision/hash, confirmation and candidate production guards remain separate and unchanged.
- Missing, empty, non-file, escaping or unreadable dependencies, malformed JSON, unresolved IDs and local-uncheckable media URLs fail closed. Ordinary webpage provenance and logical asset labels are not treated as local media. URL query/fragment suffixes cannot hide recognized media references. Explicit external roots remain supported only by the direct-read CLI; no root-wide scanning.
- --package on both existing CLIs measures finished file bytes, including archive overhead, and emits SHA-256. The default is strictly less than 19,000,000 bytes per package, a conservative workflow transfer budget rather than a universal platform specification. An explicit verified channel budget can override it. Empty/missing/oversize packages are refused with actionable rebuilding advice; no deletion, splitting or link rewrites occur.

## Verification

Final full Python regression: **1144 passed, 2 skipped, 97 subtests passed in 40.78s**. Used the existing isolated Python test dependencies, explicit existing generic test-method Vault and existing Node zod modules. The initial unconfigured run failed on absent Vault/Node runtime configuration; those failures were not hidden. The first configured run exposed three legitimate same-byte-alias regressions and one error-message compatibility assertion; all four were corrected before the final complete rerun.

Independent focused review: **128 passed**, no unresolved blocking findings. Follow-up focused checks after media-URL and alias corrections: **108 passed**. New tests include selected-file manifest dependencies, nested/cyclic JSON, current/candidate dependencies and read-only snapshots, omitted media/method paths, traversal and symlink loops, external roots, remote query/fragment URLs, actual CLI invocation, self-generated ZIP recovery and actual ZIP sizes 18,999,999 / 19,000,000 / 19,000,001. Python compilation and CRLF-aware diff checks passed. Windows-specific/optional skipped checks are not claimed as executed. No JavaScript implementation changed; a separate Node test run was not performed.

## Boundaries

Discovery does not infer missing business requirements from arbitrary prose, parse Markdown links, validate image/video semantics, prove reading or approval, or fetch remote references. Optional empty paths do not create an unselected sound/reference requirement. Unsupported/undeclared formats and business necessity still need source review. Files remain project-relative under the established manifest convention.

Package checks are delivery checkpoints on explicitly supplied finished package paths, not a general packager, archive-content validator, automatic safe splitter or upload interceptor. Omitting --package does not prove size was checked. Rebuilds require rechecking before transfer; extraction tests use only self-generated synthetic archives. No business confirmation, candidate identity or source content was rewritten.
