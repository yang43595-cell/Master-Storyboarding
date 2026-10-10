# Recovery provenance

This branch is a source-only backup candidate, not a restored production project.

## Reconstruction

The source was reconstructed from a 320-file 2026-10-07 root-cause package based on an older 2026-10-05 20:51 snapshot. Archive SHA-256: `c93c4d969b32fe69ba2dc9245b036a2cf9548df89c445cd4e3cd81ca7048653a`.

A boundary-repair package (archive SHA-256 `0fde0dd41d570e601bb103dfe430904023199f7ebe6d8043734df49d0e33e34d`) supplied 58 changes whose before hashes matched and five verified new files. One video-script entry used two exact sentence replacements because of source drift. An unrelated knowledge-maintainer drift was left unchanged.

A later 2026-10-08 minimal repair changed eight files. The reconstruction contained 325 files before this repository's source-only filtering. No source Git commit or historical commit chain accompanied it.

## What this backup includes

Scripts and tests, project skill contracts, schemas, requirements, knowledge wiring, root rules, and three basic project documentation files. Original source bytes are preserved except the recovery warning prepended to README.md and additional upload exclusions in .gitignore.

The source-only allowlist retained 266 of the reconstructed files before adding this notice. It deliberately omitted 59 files, including product-reference images and brief, examples, historical reports and token/audit evidence. The SHA-256 inventory records the selected source bytes before the README and .gitignore setup changes. Omitted assets are not deleted from the recovery source.

## Verification limits

The repair author reported 12 targeted checks, 282 existing non-voice Python unittest checks, and 68 Node checks passing on the unfiltered candidate. Full pytest, actual-media integration and independent review were not completed. Those reported checks do not establish that this filtered backup can run without its omitted dependencies.

A source-limited heuristic scan found no matching private-key, common provider-token, cloud-access-key, bearer-literal, credential-URL, or quoted secret-assignment patterns in the selected 266 files. This is a safeguard, not a guarantee that all possible sensitive material has been found. No environment or credential stores were read.

Do not merge this candidate into main, mark it as the recovered production HEAD, create a release, or claim arbitrary-version rollback based on this snapshot. First recover and compare the actual production source and dirty changes, restore necessary assets separately, resolve divergence, run appropriate checks, and obtain acceptance.

## Backup completion on 2026-10-10

Source archive: Library libfile_9a507399ab9481918eb5bc1366f0c000, version 1, dated 2026-10-08 18:46 UTC. Archive SHA-256: 5eb6e2801e54a0c7c02da9500c3a46ed52237403558beb1359bf942448d21b10. All 369 entries in DELIVERY_MANIFEST.json were byte-hash checked successfully before backup. The archive contains 325 candidate files and does not contain the original Git history.

The previous unreferenced tree held 266 blobs: 264 selected source files plus this provenance and SOURCE_SHA256.json. It lacked two of the selected source files. The exact archived Three.js core (1462228 bytes; Git blob fa5caa10416e878196050215ae1ed5c0cac995d9) and module (621364 bytes; Git blob 886d41b48268efad82b511dd0e82b3f85338db79) have now been uploaded. With those additions this backup contains 266 selected source files plus two backup metadata files, 268 total.

The SOURCE_SHA256.json inventory matched all 266 source entries in the archive. Its README and .gitignore entries intentionally describe original source bytes, before backup notices and exclusions. The other 262 already-uploaded source entries matched the archived Git blob hashes exactly. README and this provenance were updated to document this recovery accurately; application code was not changed.

The new backup commit's parent is the repository initialization commit only. This relation does not reconstruct any missing original production history. No merge into main, release, deployment, knowledge publication, media generation, or production adoption is part of this backup.
