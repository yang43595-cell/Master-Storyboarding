# Recovery and delivery chain: bounded synthetic audit, 2026-10-18

Base: bae5d5ba8c9452b8de3d15409eccef1f2fa54ea9, audit/20261017.
Scope: one isolated checkout, synthetic inputs, no old story answers, production mutation, generation, publishing, or main merge.

## Findings and minimal change

1. An applicable bound external method already fails before startup when missing; optional methods remain optional. The direct video workflow intentionally has no prepare endpoint. Its manually read method/product/action files were not collectively checked before writing. Added repeatable --require-file to the existing scripts.core.product_refs command, with explicit --input-root for external absolute inputs. It checks readability/nonempty regular files and resolved containment, handles directory/empty/missing/escaping/symlink-loop inputs, and writes nothing. Existing --directory and --include-images calls remain compatible. The video Skill now calls this check before output for actual applicable inputs; it creates no new manifest. This is an explicit-input checkpoint, not automatic discovery of undeclared dependencies, proof of reading, version correctness, content truth, or approval.
2. Generated synthetic ZIP and moved project preserve relative links, byte hashes and business files. Candidate execution still refuses the copied absolute run identity, including attempts to configure it at the new location. This is intentional authorization protection, not silently repaired. Existing build-image-review-delivery emits absolute folder/document links; these are not portable merely because the payload ZIP moved. Reading copies/explicit existing migration mapping remain necessary. No production receipts were rewritten, no automatic identity rebinding added, no untrusted ZIP importer claimed.
3. Existing candidate consumption rejects missing files, relative traversal, symlink escape, stale upstream revision and mismatched registered hashes. All failure cases preserve the exact on-disk business state. Formal promotion remains blocked after move/extract. Synthetic hash metadata is deliberately invalid test input and never represents a user confirmation.
4. A synthetic archive can be measured against an explicit test byte budget before any transfer. There is no general automated export-budget/partition tool. Updated the existing delivery instructions to check selected payload bytes and actual finished archive size against a separately verified receiving-channel budget, then verify extracted relative paths/hashes. The test threshold is not a claimed platform upload limit. Automatic archive budgeting and discovery of undeclared external files remain gaps.

## Validation and environmental findings

Initial focused run: 9 passed, 1 failed because the test incorrectly expected copied candidate run authorization to carry over. Corrected the expectation without changing that guard. Focused stage/method/artifact/candidate regression: 153 passed at the first patch revision. Final new recovery tests plus product-reference tests: 32 passed, 1 skipped. Independent review found a Python 3.12 symlink-loop RuntimeError; fixed and covered file/root/project loops, then independently verified the real CLI returns failure without traceback.

Initial full collection exposed existing PYTHONPATH and gitignored product-fixture requirements. Used the existing interpreter and Node dependency directory read-only, and created only a 2x2 gray synthetic PNG in this isolated ignored fixture path (not committed). With those configured but no Vault root, full suite: 58 failed, 1054 passed, 2 skipped, 62 subtests passed; representative failures were missing external method/template files at the default home directory. Selecting the existing explicit generic Vault root restored full regression: 1106 passed, 2 skipped, 97 subtests passed before the final four added cases. No product/story media was copied into GitHub.

Final code regression result is recorded below after completion. Separate Node suite was not rerun because no JavaScript changed; Python integration tests use the existing NODE_PATH. Windows-only/optional runtime skips are not claimed as validated. Compilation and CRLF-aware diff checks are required before save.

Reproduce from repository root using its installed test dependencies:

    MSB_VAULT_ROOT=<explicit generic test-method Vault> PYTHONPATH=.:scripts NODE_PATH=<existing zod modules> python -m pytest -q scripts/tests
    python -m pytest -q scripts/tests/test_recovery_delivery.py scripts/tests/test_product_refs.py

For normal direct-read readiness:

    python -m scripts.core.product_refs --project <project> --input-root <selected Vault> --require-file <absolute required method> --require-file <project-relative product or action file>

Do not allow a whole filesystem root to bypass input scope. Missing/unselected optional sources must not become mandatory. Byte checks do not authorize upload, adoption, generation or publication.

Final validation on the saved code: **1110 passed, 2 skipped, 97 subtests passed (42.22s)**. Focused final check: **32 passed, 1 skipped (0.32s)**. Compilation and CRLF-aware diff checks passed. Original source checkout remained at bae5d5b with a clean working tree.
