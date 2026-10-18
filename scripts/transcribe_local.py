"""Optional, local-only machine transcription; never a human listening review."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


class TranscriptionError(ValueError):
    pass


def configure_cache(cache: Path) -> Path:
    """Use an explicitly selected writable cache, before importing HF packages."""
    cache = cache.expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryFile(dir=cache) as probe:
            probe.write(b'cache-write-check')
    except OSError as exc:
        raise TranscriptionError('Selected cache is not writable') from exc
    for name in ('hub', 'xet'):
        (cache / name).mkdir(exist_ok=True)
    os.environ.update(HF_HOME=str(cache), HF_HUB_CACHE=str(cache / 'hub'),
                      HF_XET_CACHE=str(cache / 'xet'), HF_HUB_DISABLE_XET='1',
                      HF_HUB_OFFLINE='1')
    return cache


def decode_audio(source: Path, cache: Path, *, ffmpeg: str = 'ffmpeg',
                 max_seconds: float = 3600, timeout: float = 300):
    """Decode with FFmpeg, never faster-whisper's version-sensitive PyAV path."""
    import numpy as np
    if not math.isfinite(max_seconds) or not 0 < max_seconds <= 86400:
        raise TranscriptionError('max_seconds must be in (0, 86400]')
    if not math.isfinite(timeout) or timeout <= 0:
        raise TranscriptionError('timeout must be positive and finite')
    source = source.resolve(strict=True)
    if not source.is_file():
        raise TranscriptionError('Input must be an existing local file')
    executable = shutil.which(ffmpeg)
    if executable is None:
        raise TranscriptionError('FFmpeg is not installed or not on PATH')
    # A private temporary output avoids unbounded stdout buffering. One extra
    # second detects oversized input rather than silently accepting truncation.
    with tempfile.TemporaryDirectory(prefix='asr-decode-', dir=cache) as tmp:
        output = Path(tmp) / 'audio.f32le'
        command = [executable, '-nostdin', '-hide_banner', '-loglevel', 'error',
                   '-protocol_whitelist', 'file,pipe', '-i', str(source),
                   '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000',
                   '-t', str(max_seconds + 1), '-f', 'f32le', str(output)]
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout,
                                    check=False)
        except subprocess.TimeoutExpired as exc:
            raise TranscriptionError('Audio decoding timed out') from exc
        if result.returncode:
            raise TranscriptionError('FFmpeg could not decode the local audio stream')
        size = output.stat().st_size
        if size == 0 or size % 4:
            raise TranscriptionError('Decoded audio is empty or malformed')
        if size > int(max_seconds * 16000) * 4:
            raise TranscriptionError('Audio exceeds the explicit duration limit')
        audio = np.fromfile(output, dtype='<f4').astype(np.float32, copy=False)
    if not np.isfinite(audio).all():
        raise TranscriptionError('Decoded audio contains non-finite samples')
    return audio


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def versions() -> dict:
    result = {}
    for name in ('faster-whisper', 'av', 'numpy', 'ctranslate2', 'huggingface-hub'):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = 'not-installed'
    return result


def transcribe(source: Path, model: Path, cache: Path, *, language: str | None = None,
               max_seconds: float = 3600, timeout: float = 300,
               model_factory=None) -> dict:
    source = source.expanduser().resolve(strict=True)
    model = model.expanduser().resolve(strict=True)
    if not source.is_file() or not model.is_dir():
        raise TranscriptionError('Input must be a local file and model an existing local directory')
    for required in ('model.bin', 'config.json'):
        if not (model / required).is_file():
            raise TranscriptionError(f'Local model is missing {required}')
    source_hash = file_sha256(source)
    model_hashes = {p.name: file_sha256(p) for p in sorted(model.iterdir()) if p.is_file()}
    cache = configure_cache(cache)
    audio = decode_audio(source, cache, max_seconds=max_seconds, timeout=timeout)
    if model_factory is None:
        from faster_whisper import WhisperModel
        model_factory = WhisperModel
    engine = model_factory(str(model), device='cpu', compute_type='int8',
                           local_files_only=True, download_root=str(cache / 'hub'))
    segments, info = engine.transcribe(audio, language=language, beam_size=5,
                                      vad_filter=True)
    rows = [{'id': i, 'start': float(s.start), 'end': float(s.end), 'text': s.text}
            for i, s in enumerate(segments)]
    if file_sha256(source) != source_hash:
        raise TranscriptionError('Input changed during transcription; result not saved')
    return {'schema_version': '1.0', 'status': 'machine_transcription',
            'review_status': 'unreviewed', 'human_listening_verified': False,
            'timing_status': 'asr_locator_only',
            'source': {'name': source.name, 'sha256': source_hash},
            'producer': {'tool': 'faster-whisper', 'model_directory_name': model.name,
                         'model_file_sha256': model_hashes, 'versions': versions(), 'decoder': 'ffmpeg',
                         'sample_rate': 16000, 'channels': 1, 'sample_format': 'float32',
                         'device': 'cpu', 'compute_type': 'int8', 'beam_size': 5,
                         'vad_filter': True, 'local_files_only': True},
            'duration_seconds': len(audio) / 16000,
            'language': info.language, 'segments': rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--model', required=True, type=Path,
                        help='Existing local CTranslate2 model directory, never a remote model ID')
    parser.add_argument('--cache', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--language')
    parser.add_argument('--max-seconds', type=float, default=3600)
    parser.add_argument('--decode-timeout', type=float, default=300)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('Output already exists; select a new revision path')
    try:
        result = transcribe(args.input, args.model, args.cache, language=args.language,
                            max_seconds=args.max_seconds, timeout=args.decode_timeout)
        # Exclusive creation also prevents overwriting a file created during ASR.
        serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(serialized)
    except (OSError, ValueError, ImportError, RuntimeError) as exc:
        parser.exit(2, f'Local transcription failed: {type(exc).__name__}: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
