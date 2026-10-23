"""The project validator's shared Python path handles JSON instead of raw escapes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from scripts.core.product_refs import validate_image_documents
from scripts.core.io import write_json


@pytest.mark.parametrize('kind', ['json_fields', 'json_body', 'md', 'txt'])
def test_generic_image_references_exist_then_disappear(tmp_path, kind):
    directory = tmp_path / '06_视频'; directory.mkdir()
    image = tmp_path / '05_分镜/frame.png'; image.parent.mkdir(); image.write_bytes(b'synthetic')
    if kind == 'json_fields': write_json(directory / 'current.json', {'references': [{'path': '05_分镜/frame.png'}]})
    elif kind == 'json_body': write_json(directory / 'current.json', {'prompt': 'Look at 05_分镜/frame.png now.'})
    else: (directory / ('current.' + kind)).write_text('See `05_分镜/frame.png`')
    assert validate_image_documents(tmp_path, directory) == []
    image.unlink()
    assert len(validate_image_documents(tmp_path, directory)) == 1


def test_json_windows_escaped_path_spaces_and_explicit_relocation(tmp_path, monkeypatch):
    directory = tmp_path / '06_视频'; directory.mkdir()
    image = tmp_path / '05_分镜/a frame.png'; image.parent.mkdir(); image.write_bytes(b'fixture')
    old = r'C:\Synthetic\film'
    write_json(directory / 'current.json', {'reference_paths': [old + r'\05_分镜\a frame.png']})
    monkeypatch.setenv('MSB_PROJECT_PATH_MAP', json.dumps({old: str(tmp_path)}))
    assert validate_image_documents(tmp_path, directory) == []
    image.unlink()
    assert validate_image_documents(tmp_path, directory)


def test_project_validator_python_entry_flags_missing_json_image(tmp_path):
    directory = tmp_path / '06_视频'; directory.mkdir()
    write_json(directory / 'current.json', {'references': [{'path': '05_分镜/missing.png'}]})
    result = subprocess.run([sys.executable, '-m', 'scripts.core.product_refs', '--project', str(tmp_path),
        '--directory', str(directory), '--include-images'], capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 1
    assert 'Referenced image does not exist' in result.stdout
