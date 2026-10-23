# Redacted online-source candidate-chain verification — 2026-10-19

## Scope and outcome

Conditional pass for an isolated, unreviewed candidate chain and static reading delivery. This is not a complete-source, 1:1 reproduction, production approval, or generated-visual validation. No image/video generation or publication occurred. This report adds no rules and changes no implementation.

Source baseline: `c72a9b4cbb6766ffafa5ff8ef4ba1a79d7ebf091` on `audit/20261017`. The restored checkout's 321 tracked blobs, exact commit/tree, clean status, and Git object integrity were verified before the run.

## Source acquisition limits

- The public source played online. Two media-download attempts were technically cancelled; no local source MP4 or source audio track was acquired.
- A roughly 197-second normal-UI pass captured 227 screenshots at approximately one-second intervals. 198 usable frames were retained; 29 login-overlay frames were excluded.
- 111 subtitle-derived source entries remained unreviewed. Independent review matched them to acquired frames, with bounded additional checks of the initial line and a rapid short utterance.
- Seek-position UI could advance before the displayed frame caught up. Sampling and these additional checks do not establish continuous or subsecond source completeness.
- No audio listening verification, local ASR result, or confirmed dialogue was claimed.

## Candidate and dependency checks

- Candidate projection retained 111 source entries plus five explicitly author-added entries consistently, with 41 planned shots and 20 proposed assets. These are candidate counts, not verified source facts or generated assets.
- All three business artifact registrations remained `candidate`, `unreviewed`, and `production_blocked`. No current-adoption state or user confirmation was manufactured.
- Missing general-method source material correctly blocked preparation. Exact method originals were subsequently restored; method requirements were not disabled.
- One manifest dependency incorrectly used an artifact ID where a logical ID was required. It was corrected and the actual dependency check rerun successfully; the initial failure was preserved.
- Actual discover verification checked 126 dependency files with zero errors.
- Some early prepare/ensure records were explicitly labeled manual summaries rather than original process evidence. The final asset-stage check retained original stdout and exit status zero.
- `source_fidelity_checked=true` describes consistency against the acquired source-entry baseline only; it does not certify source-video completeness.

## Independent review and delivery checks

- Independent review concluded conditional pass within the limited scope above and did not edit business artifacts.
- All 292 frozen file hashes matched.
- All 15 main-HTML links and 227 evidence-page references, including internal anchors, resolved in static checks. Extracted-package HTML links were also checked.
- Browser rendering was blocked by the available rendering tool. No bypass was used; static relative-link checks do not prove browser appearance or behavior.
- Still unverified: source audio and ASR, subsecond completeness, inferred speakers, precise directorial timings, generated visual quality, and browser rendering.

## Package verification

- Main package: 14,608,834 bytes; SHA-256 `b2d7b82fe0502da0ace7e6dc748ee4234efdc7120fb92584d3863e891dd77655`.
- Full backup package: 44,262,062 bytes; SHA-256 `60270bca9d21d8c216e86c5f632a8e3acd804ee1f88a037aa26b1dfbac2e7b08`.
- The actual main-package size-bound check (under 19 MB) and dependency discovery passed. Both packages were persisted in the user's file library.
- The full backup exceeded the chat attachment limit and was not sent as a chat attachment. Library persistence is not equivalent to attachment delivery.

Only this redacted technical report is committed. No source-media bytes, dialogue text, case business artifacts, private filesystem paths, private Library identifiers, credentials, or new policy rules are included. The target is the existing private audit branch only; no main-branch merge or website publication is performed.
