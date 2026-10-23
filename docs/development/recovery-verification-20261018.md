# Isolated framework recovery verification — 2026-10-18

## Scope and provenance

This checkpoint restores and verifies the framework source only. It is **not a full project recovery**, a recovered production environment, or a completed end-to-end case test.

- Repository branch: `audit/20261017`.
- Tested source commit: `af029e1f260c6fa158aa0ea634bef18a6b20ff48`.
- Tested root tree: `ee4fee345fa70b12501d17b819cba8212ecdeefd`.
- Recovered tracked files: **306**.
- Each file was verified against its original Git blob SHA. Reconstructed root tree and commit matched the original objects. Post-test verification found zero tracked-file differences.
- The local checkout is a shallow snapshot of that commit. Earlier commit history was not recovered.
- This record changes documentation only; the tested production interfaces and source files are unchanged.

## Reusable recovery procedure

1. Use a new isolated directory; do not overwrite an existing project.
2. Resolve the authorized branch and pin its commit through an existing authorized repository connection. Do not export credentials.
3. Retrieve the recursive file tree and file bytes. Preserve exact UTF-8 bytes, line endings and file modes.
4. Verify every file using Git's blob hash: SHA-1 of `blob <byte-length>\0<bytes>`. Verify the reconstructed root tree and original commit before adopting the snapshot.
5. Install declared test requirements in an isolated Python environment. Resolve the website validation dependency separately; keep runtime files out of version control.
6. Select the external knowledge root and Node dependency path explicitly. Historical absolute paths in older documents are provenance, not portable setup requirements.
7. Recover any external test fixture only from its verified original source. Do not replace a missing reference with a fabricated image.
8. Run existing checks; report failed, skipped and untested coverage separately. Rehash the source after testing.
9. For a blind test, allow only original inputs and vetted generic knowledge. Keep prior answers, reviews, case archives and excluded historical sources inaccessible to the test operator.

## Verified checks

| Check | Result |
| --- | --- |
| Python regression, `python -m pytest -c config/pytest.ini` | 974 passed, 1 skipped, 0 failed |
| Skipped Python check | Windows-specific project validator |
| Node unit checks, `node --test scripts/tests/*.cjs` | 68 passed, 0 failed |
| Python dependency consistency, `python -m pip check` | No broken requirements |
| Post-test source integrity | 306/306 original blob hashes; clean tracked worktree |
| Remote baseline ref recheck | Still matched the tested source commit before this documentation checkpoint |

Runtime used: Python 3.12.14; pytest 9.1.1; Pillow 12.3.0; jsonschema 4.26.0; referencing 0.37.0; Node 24.19.0; Zod 3.25.76.

The initial test collection stopped because an external product-reference fixture was absent. It was restored from a separately verified original source, excluded from Git, and the entire Python suite was rerun. Missing generic knowledge templates were likewise restored before the final run. No reference media or knowledge text is included in this commit.

## Knowledge isolation boundary

The isolated knowledge audit is **not fully passing**. It reports seven missing-source references across three deliberately excluded historical project/review sources, plus a warning that project record pages are absent. Generic methods and required templates were recovered from verified source files; excluded historical material was not replaced with invented content.

This isolation is intentional for blind testing. A current stage must still resolve every required method and source from the permitted input set. If it cannot, stop that dependent step and identify the exact missing dependency. Passing unit tests does not waive knowledge or confirmation requirements.

## Not established by this checkpoint

- Complete original project history, dirty changes, external assets or all knowledge-library sources.
- Live website behavior, remote media transfer or deployment state.
- Native Blender/MPFB production readiness or a completed render.
- Creative quality, user confirmation, paid image/video generation or end-to-end production success.

No merge, deployment, paid generation, credential change or original-media upload was performed.
