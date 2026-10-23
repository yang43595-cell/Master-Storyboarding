"""Synthetic audio and stub inference only: no downloads or real speech."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest
np = pytest.importorskip("numpy", reason="optional local ASR dependency")
from scripts import transcribe_local as asr


@pytest.fixture(autouse=True)
def restore_environment(monkeypatch):
    for key in ('HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_HUB_DISABLE_XET', 'HF_HUB_OFFLINE'):
        monkeypatch.setenv(key, 'prior-value')


@pytest.fixture
def audio(tmp_path):
    if not asr.shutil.which('ffmpeg'):
        pytest.skip('optional FFmpeg audio decoder unavailable')
    source = tmp_path / 'synthetic.wav'
    t = np.arange(8000, dtype=np.float32) / 8000
    mono = (np.sin(2 * np.pi * 440 * t) * 4000).astype('<i2')
    with wave.open(str(source), 'wb') as f:
        f.setnchannels(2); f.setsampwidth(2); f.setframerate(8000)
        f.writeframes(np.column_stack([mono, mono]).tobytes())
    return source


@pytest.fixture
def model(tmp_path):
    folder = tmp_path / 'synthetic-model'; folder.mkdir()
    (folder / 'model.bin').write_bytes(b'not a real model; stub only')
    (folder / 'config.json').write_text('{}')
    return folder


def test_cache_overrides_read_only_defaults_before_import(tmp_path):
    cache = asr.configure_cache(tmp_path / 'cache')
    assert os.environ['HF_HOME'] == str(cache)
    assert os.environ['HF_HUB_CACHE'] == str(cache / 'hub')
    assert os.environ['HF_XET_CACHE'] == str(cache / 'xet')
    assert os.environ['HF_HUB_DISABLE_XET'] == '1'
    assert os.environ['HF_HUB_OFFLINE'] == '1'


def test_unwritable_cache_fails_without_default_fallback(tmp_path, monkeypatch):
    def denied(*a, **kw):
        raise PermissionError('synthetic denied cache')
    monkeypatch.setattr(asr.tempfile, 'TemporaryFile', denied)
    with pytest.raises(asr.TranscriptionError, match='not writable'):
        asr.configure_cache(tmp_path / 'cache')
    assert os.environ['HF_HOME'] == 'prior-value'


def test_real_ffmpeg_resamples_stereo_and_cleans_temporary_audio(audio, tmp_path):
    cache = asr.configure_cache(tmp_path / 'cache')
    data = asr.decode_audio(audio, cache)
    assert data.dtype == np.float32 and data.ndim == 1
    assert len(data) == 16000
    assert np.isfinite(data).all() and np.max(np.abs(data)) > 0.1
    assert not list(cache.glob('asr-decode-*'))


def test_duration_limit_rejects_instead_of_silently_truncating(audio, tmp_path):
    cache = asr.configure_cache(tmp_path / 'cache')
    with pytest.raises(asr.TranscriptionError, match='duration limit'):
        asr.decode_audio(audio, cache, max_seconds=0.5)


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), 86401])
def test_invalid_limit_rejected(audio, tmp_path, value):
    with pytest.raises(asr.TranscriptionError, match='max_seconds'):
        asr.decode_audio(audio, tmp_path, max_seconds=value)


def test_missing_ffmpeg_fails_explicitly(audio, tmp_path, monkeypatch):
    monkeypatch.setattr(asr.shutil, 'which', lambda _: None)
    with pytest.raises(asr.TranscriptionError, match='FFmpeg'):
        asr.decode_audio(audio, tmp_path)


def test_non_audio_input_fails_cleanly(tmp_path):
    if not asr.shutil.which('ffmpeg'):
        pytest.skip('optional FFmpeg audio decoder unavailable')
    source = tmp_path / 'synthetic.txt'; source.write_text('not media')
    with pytest.raises(asr.TranscriptionError, match='could not decode'):
        asr.decode_audio(source, tmp_path)


def test_decode_timeout_is_reported(audio, tmp_path, monkeypatch):
    def timeout(*a, **kw):
        raise asr.subprocess.TimeoutExpired('ffmpeg', 1)
    monkeypatch.setattr(asr.subprocess, 'run', timeout)
    with pytest.raises(asr.TranscriptionError, match='timed out'):
        asr.decode_audio(audio, tmp_path, timeout=1)


def test_array_contract_bypasses_incompatible_av_and_marks_machine_only(audio, model, tmp_path):
    class StubModel:
        def __init__(self, path, **kw):
            assert path == str(model)
            assert kw['local_files_only'] is True
            assert kw['device'] == 'cpu'
            assert os.environ['HF_HUB_OFFLINE'] == '1'
        def transcribe(self, data, **kw):
            # The real faster-whisper branch decodes non-ndarray inputs only.
            assert isinstance(data, np.ndarray) and data.dtype == np.float32
            assert kw == {'language': 'zh', 'beam_size': 5, 'vad_filter': True}
            return iter([SimpleNamespace(start=0, end=1, text='synthetic stub')]), SimpleNamespace(language='zh')
    result = asr.transcribe(audio, model, tmp_path / 'cache', language='zh', model_factory=StubModel)
    assert result['status'] == 'machine_transcription'
    assert result['review_status'] == 'unreviewed'
    assert result['human_listening_verified'] is False
    assert result['timing_status'] == 'asr_locator_only'
    assert result['source']['sha256'] == asr.file_sha256(audio)
    assert result['producer']['model_file_sha256']['model.bin'] == asr.file_sha256(model / 'model.bin')
    assert result['segments'][0]['text'] == 'synthetic stub'


def test_no_remote_model_id_or_incomplete_model_accepted(audio, tmp_path):
    with pytest.raises(FileNotFoundError):
        asr.transcribe(audio, tmp_path / 'remote-model-name', tmp_path / 'cache')
    folder = tmp_path / 'empty-model'; folder.mkdir()
    with pytest.raises(asr.TranscriptionError, match='model.bin'):
        asr.transcribe(audio, folder, tmp_path / 'cache')
    assert not (tmp_path / 'cache').exists()


def test_existing_output_is_preserved_without_running_asr(tmp_path, monkeypatch):
    output = tmp_path / 'result.json'; output.write_text('keep me')
    def forbidden(*a, **kw):
        raise AssertionError('must not run')
    monkeypatch.setattr(asr, 'transcribe', forbidden)
    with pytest.raises(SystemExit):
        asr.main(['--input', 'unused', '--model', 'unused', '--cache', 'unused', '--output', str(output)])
    assert output.read_text() == 'keep me'


def test_output_race_does_not_overwrite(tmp_path, monkeypatch):
    output = tmp_path / 'result.json'
    def race(*a, **kw):
        output.write_text('concurrent result')
        return {'status': 'machine_transcription'}
    monkeypatch.setattr(asr, 'transcribe', race)
    with pytest.raises(SystemExit):
        asr.main(['--input', 'unused', '--model', 'unused', '--cache', 'unused', '--output', str(output)])
    assert output.read_text() == 'concurrent result'


def test_known_faster_whisper_pyav_combination_uses_array_workaround(audio, tmp_path):
    upstream = pytest.importorskip('faster_whisper.audio', reason='optional ASR runtime not installed')
    if (asr.importlib.metadata.version('faster-whisper'), asr.importlib.metadata.version('av')) != ('1.2.1', '19.0.1'):
        pytest.skip('exact historically observed compatibility pair only')
    with pytest.raises(TypeError, match='metadata_errors'):
        upstream.decode_audio(str(audio))
    data = asr.decode_audio(audio, asr.configure_cache(tmp_path / 'cache'))
    assert isinstance(data, np.ndarray) and len(data) == 16000
