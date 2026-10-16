"""Historical dual-file input stays strict; new business JSON needs no MD."""
import copy
import json

import pytest

from core.story_sources import validate_story_sources, reading_supplement, export_markdown
from test_story_paragraph_sources import narrative
from test_director_compact import compact_package
from visual_asset_source import receive_table


def test_json_only_narrative_and_director_keep_complete_reading(tmp_path):
    source = narrative(tmp_path)
    source.pop('human_readable_path')
    board = compact_package()
    board.pop('human_readable_path')
    (tmp_path / 'script.md').unlink()
    validate_story_sources(source, board, project_root=tmp_path)
    assert reading_supplement(source, tmp_path)['narrative'] == source
    # An obsolete optional export cannot become a hidden business dependency.
    assert receive_table(tmp_path, [{'logical_id': 'narrative.assets', 'revision': 'R01'}],
                         {'revision': 'R01'}, {}, source, tmp_path / 'source.json') is None
    output = tmp_path / 'reading.md'
    original = copy.deepcopy(source)
    export_markdown(source, output)
    body = output.read_text().split('```json\n', 1)[1].rsplit('\n```', 1)[0]
    assert json.loads(body) == source == original
    with pytest.raises(FileExistsError):
        export_markdown(source, output)


def test_legacy_markdown_drift_is_not_silently_ignored(tmp_path):
    source = narrative(tmp_path)
    (tmp_path / 'script.md').write_text('wrong old story')
    with pytest.raises(ValueError, match='Markdown'):
        validate_story_sources(source, project_root=tmp_path)
