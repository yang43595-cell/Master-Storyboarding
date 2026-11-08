# Optional local machine transcription

This helper provides machine ASR candidates, not human listening verification, subtitle correction, speaker identification or confirmed dialogue. It does not replace the external `video-transcript-correction` Skill. Existing source-lineage and user-confirmation rules still apply.

## Setup boundary

Use a separate Python environment. The optional `config/requirements-asr.txt` locks direct and transitive packages with SHA256 hashes from the official Python Package Index; `config/requirements-asr.in` is the maintenance input. Install explicitly when needed with `python -m pip install --index-url https://pypi.org/simple --require-hashes --only-binary=:all: -r config/requirements-asr.txt`, never implicitly during transcription. Keep that environment separate from the visual-library runtime. FFmpeg must already be installed from a reputable official/package-manager source. Record resolved dependency versions for the actual run. Resolution or wheel availability does not establish real-model compatibility or transcription accuracy; this optional lock is not installed by the default CI job. See [dependency checks](dependency-checks.md) for lock maintenance and verification boundaries.

The model must already exist as a local CTranslate2 model directory containing `model.bin` and `config.json`. Obtain models separately through an authorized official source; this command never accepts a remote model ID or downloads a model. It runs on CPU with int8 computation. Missing model files fail explicitly.

Select a writable per-run cache rather than relying on a home-directory cache. Before loading Hugging Face packages, the CLI sets its process-local `HF_HOME`, `HF_HUB_CACHE`, `HF_XET_CACHE`, `HF_HUB_DISABLE_XET=1` and `HF_HUB_OFFLINE=1`. No account settings or global environment are changed. Run the CLI as its own process; the importable helper is not intended to reconfigure an application that has already imported Hugging Face libraries.

Some existing authorized SOCKS network routes require `socksio` when separately provisioning dependencies/models. This is a conditional prerequisite, not a reason to change proxy or network configuration. The transcription command itself is offline.

## Run

```sh
python scripts/transcribe_local.py \
  --input /absolute/path/to/local-media.mp4 \
  --model /absolute/path/to/already-present-model \
  --cache /absolute/path/to/writable-asr-cache \
  --output /absolute/path/to/new-machine-transcript.json \
  --language zh
```

Paths above are placeholders, not historical installation locations. The output parent must exist; existing outputs are never overwritten. Choose a new revision path for reruns. FFmpeg decoding has a 300-second timeout and a one-hour duration bound; `--decode-timeout` and `--max-seconds` change these explicitly. Exceeding the duration bound fails instead of silently producing a truncated transcript. Model inference is local and may take longer than decoding; it has no fixed inference timeout.

FFmpeg decodes the selected local audio stream to 16 kHz mono float32 samples. Only an ndarray is passed to faster-whisper, avoiding its incompatible PyAV file-decoder call. The FFmpeg input protocol allowlist is limited to local file/pipe access. Private decoded audio temporaries are removed after decoding; no source media or transcript is uploaded.

The JSON output contains `status: machine_transcription`, `review_status: unreviewed`, `human_listening_verified: false`, and `timing_status: asr_locator_only`. Segment times are locators, not manually verified precise dialogue timing. It records input SHA-256, local model-file SHA-256 values, actual dependency versions and inference settings. Preserve this original machine output when preparing a separate dialogue evidence record; bind ASR text using the existing source-lineage contract. Do not manufacture reviewer identities or approval records.

## Compatibility evidence and checks

Observed pair: faster-whisper 1.2.1 + PyAV 19.0.1. The upstream file decoder passes `metadata_errors` to `av.open`, which raises TypeError in this pair. The dependency declaration `av>=11` does not prevent this combination. A synthetic-audio reproduction verified the failure and successful FFmpeg ndarray decoding without loading a model or accessing the network. This workaround does not claim that all future package combinations are compatible.

Run `python -m pytest -c config/pytest.ini scripts/tests/test_transcribe_local.py`. Tests use generated tones, stub inference, failed cache/decoder cases, local-only model checks and no-overwrite checks. The exact-version compatibility test is optional when ASR dependencies are absent. No real speech, model weights or user data belongs in the repository. Synthetic decode and stub-inference passes do not establish real-model accuracy or successful human review.

### Repair verification, 2026-10-18

- New focused tests: 17 passed against the observed faster-whisper 1.2.1 / PyAV 19.0.1 runtime. No model loaded, no network used, no runtime package modified.
- Full framework Python regression: 990 passed, 2 skipped (Windows-only project validator and optional ASR-runtime test in the baseline test environment). The optional test was separately run successfully in the observed ASR runtime.
- Existing Node regression: 68 passed, zero failures.
- Python compilation checks passed.
- All original 306 tracked source files remained byte-identical; this repair adds a helper, optional dependency list, focused tests and this document only.
- Test material was generated tones and stub inference. Real-model inference accuracy, human review, paid generation and live service integration are outside this verification.
