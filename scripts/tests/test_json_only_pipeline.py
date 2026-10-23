"""Synthetic cross-stage regression, not a production/Windows111 migration replay.

No model, upload, browser or rendering is invoked; confirmations are fixture data.
"""
import copy
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from scripts.canvas_readback import compare, digest, validate_submission
from scripts.core import artifacts, revision_control as rc
from scripts.core.asset_refs import build_asset_list, current_narrative, scan_references
from scripts.core.io import read_json, write_json
from scripts.core.product_refs import validate_documents
from scripts.core.project_state import load_project_state, write_project_state
from scripts.core.story_sources import reading_supplement, validate_story_sources
from scripts.blender_previs.project import director_input
from scripts.visual_asset_source import receive_table
from scripts.tests.test_delivery_preflight import delivery
from test_story_paragraph_sources import narrative
from test_director_compact import compact_package


OLD_ROOT = r'C:\Synthetic\JSONOnlyFilm'


def mapped(relative):
    return OLD_ROOT + '\\' + relative.replace('/', '\\')


def setup_pipeline(root):
    root.mkdir()
    frozen, _ = delivery(root)
    story = narrative(root)
    story.pop('human_readable_path')
    (root / 'script.md').unlink()
    story['story_paragraphs'][0]['text'] = story['story_paragraphs'][0]['text'].replace('人物拒绝', '人物持玻璃碗拒绝')
    story['asset_requirements'][0].update(image_decision='需要制作', image_reason='合成角色身份需稳定')
    product = copy.deepcopy(story['asset_requirements'][0])
    product.pop('entity_id')
    product.update(asset_id='AST-PROD', category='REAL_PRODUCT', name='玻璃碗',
                   narrative_purpose='人物持碗拒绝', image_decision='复用现有素材', image_reason='合成产品真实参考已登记',
                   reference_paths=[mapped('assets/product-references/bowl.png')])
    story['asset_requirements'].append(product)
    story_path = root / '02_编导/story.json'; write_json(story_path, story)
    board = compact_package(); board.pop('human_readable_path'); board['status'] = 'confirmed'
    board['source_narrative'].update(artifact_id='NARRATIVE', path=mapped('02_编导/story.json'))
    board['source_asset_requirement_ids'].append('AST-PROD')
    board['scenes'][0]['shots'][0]['source_asset_requirement_ids'].append('AST-PROD')
    board['scenes'][0]['shots'][0]['take']['process'] = '说完拒绝，把玻璃碗放在桌面'
    write_json(root / 'director.json', board)
    image = root / 'assets/product-references/bowl.png'; image.parent.mkdir(parents=True)
    Image.new('RGB', (8, 8), 'red').save(image)
    manifest = artifacts.load_manifest(root)
    director = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'DIRECTOR')
    director.update(logical_id='storyboard.package', revision=board['revision'],
                    checksum=artifacts.digest(root / 'director.json'), path_or_url=mapped('director.json'))
    director.pop('upload_bindings', None)
    frame = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'FRAME')
    frame['path_or_url'] = mapped('frame.png')
    manifest['artifacts'] += [
        {'artifact_id': 'NARRATIVE', 'logical_id': 'narrative.package', 'revision': story['revision'],
         'status': 'current', 'user_approved': True, 'path_or_url': mapped('02_编导/story.json'),
         'checksum': artifacts.digest(story_path)},
        {'artifact_id': 'PRODUCT-REF', 'logical_id': 'visual.product', 'revision': 'R01',
         'status': 'current', 'user_approved': True, 'path_or_url': mapped('assets/product-references/bowl.png'),
         'checksum': artifacts.digest(image), 'upload_bindings': [{
             'target_url': frozen['target_url'], 'node_id': 'product-node', 'media_id': 'product-media',
             'revision': 'R01', 'sha256': artifacts.digest(image)}]},
    ]
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    state = load_project_state(root)
    ids = [a['artifact_id'] for a in manifest['artifacts']]
    state.update(current_artifacts=ids, user_confirmations=[{
        'scope': 'artifact', 'artifact_ids': ids, 'approval_ref': 'synthetic-fixture-only'}])
    write_project_state(root, state)
    def ref(ident):
        item = next(a for a in manifest['artifacts'] if a['artifact_id'] == ident)
        return {k: item[k] for k in ['artifact_id','revision']} | {'sha256': item['checksum']}
    frozen['source_artifacts'] = [ref('NARRATIVE'), ref('DIRECTOR')]
    frozen['drafts'] = frozen['drafts'][:1]
    row = frozen['drafts'][0]
    shot = board['scenes'][0]['shots'][0]
    row['prompt'] = '；'.join(shot['take'].values()) + '。主角说：' + story['dialogues'][0]['text']
    row['source_asset_requirement_ids'] = shot['source_asset_requirement_ids']
    row['required_entities'] = ['CHR-A', 'AST-PROD']
    row['required_reference_roles'] = {'CHR-A': ['identity','end_frame'], 'AST-PROD': ['appearance']}
    row['reference_checks'][0].update(entity_ids=['CHR-A'], roles=['identity','end_frame'])
    row['reference_checks'][0].pop('role')
    row['ordered_media'].append({'node_id': 'product-node','media_id': 'product-media'})
    row['reference_checks'].append({**ref('PRODUCT-REF'), 'kind': 'image', 'roles': ['appearance'], 'entity_ids': ['AST-PROD']})
    frozen['PRODUCT_VISUAL_INPUT'] = 'REAL_PRODUCT'
    frozen['reference_paths'] = product['reference_paths']
    write_json(root / '06_视频/current.json', frozen)
    write_json(root / '05_分镜/current.json', {'source_asset_requirement_ids': shot['source_asset_requirement_ids'], 'reference_paths': product['reference_paths']})
    remove_md(root)


def remove_md(root):
    for path in root.rglob('*.md'):
        path.unlink()


def run_pipeline(root):
    assert not list(root.rglob('*.md'))
    story = current_narrative(root)
    board = read_json(root / 'director.json')
    validate_story_sources(story, board, project_root=root)
    assert reading_supplement(story, root)['narrative'] == story
    assert receive_table(root, artifacts.load_manifest(root)['artifacts'],
                         {'revision': story['revision']}, load_project_state(root), story,
                         root / '02_编导/story.json') is None
    listing = build_asset_list(root, story)
    assert {a['asset_id'] for a in listing['items']} == {'AST-CHAR-A','AST-PROD'}
    for directory in ['05_分镜','06_视频']:
        assert validate_documents(root, root / directory) == []
    hits = scan_references(root, ['AST-PROD'], listing)['assets'][0]['reference_hits']
    assert {h['file'].split('/')[0] for h in hits} >= {'05_分镜', '06_视频'}
    assert all(h.get('json_path') for h in hits)
    source = director_input(root, 'S01-010')
    assert source['source_director_text'] is None
    assert source['shot'] == board['scenes'][0]['shots'][0]
    frozen = read_json(root / '06_视频/current.json')
    assert validate_submission(frozen, root)['ok']
    observed = copy.deepcopy(frozen)
    observed.update(phase='after_refresh', frozen_sha256=digest(frozen))
    for row in observed['drafts']:
        row['native_mentions'] = [{**m, 'entity_type': row['native_entity_type'],
                                  'native_noneditable_entity': True} for m in row['ordered_media']]
    assert compare(frozen, observed, root)['ok']
    assert not list(root.rglob('*.md'))


@pytest.mark.parametrize('relocate,interrupt', [(False,False),(True,False),(False,True),(True,True)])
def test_json_only_pipeline_relocation_and_interrupted_retry(tmp_path, monkeypatch, relocate, interrupt):
    root = tmp_path / 'before'
    monkeypatch.setenv('MSB_PROJECT_PATH_MAP', json.dumps({OLD_ROOT: str(root)}))
    setup_pipeline(root)
    run_pipeline(root)
    original_story = (root / '02_编导/story.json').read_bytes()
    original_confirmations = load_project_state(root)['user_confirmations']
    if relocate:
        destination = tmp_path / 'after'
        shutil.move(root, destination); root = destination
        # Old aliases are deliberately unavailable until the explicit map is updated.
        with pytest.raises((ValueError, OSError)): current_narrative(root)
        monkeypatch.setenv('MSB_PROJECT_PATH_MAP', json.dumps({OLD_ROOT: str(root)}))
    if interrupt:
        manifest = artifacts.load_manifest(root)
        state = load_project_state(root)
        manifest['synthetic_retry_marker'] = 'after-interruption'
        state['accepted_issues'] = [{'fixture_only': 'after-interruption'}]
        with pytest.raises(RuntimeError, match='fault injection'):
            rc._commit_json_transaction_unlocked(root, {
                root / artifacts.MANIFEST_PATH: manifest,
                root / '00_项目管理/project_state.json': state,
            }, reason='synthetic interrupted equivalent state write', fail_after=1)
        assert artifacts.load_manifest(root)['synthetic_retry_marker'] == 'after-interruption'
        assert load_project_state(root)['accepted_issues'] == []
        assert rc.recover_transactions(root)[0]['state'] == 'committed'
        assert load_project_state(root)['accepted_issues'] == [{'fixture_only': 'after-interruption'}]
        assert rc.recover_transactions(root) == []
        remove_md(root)  # Management projections are optional too.
    run_pipeline(root)
    assert (root / '02_编导/story.json').read_bytes() == original_story
    assert load_project_state(root)['user_confirmations'] == original_confirmations


def test_legacy_dual_file_reading_still_checks_exact_text(tmp_path):
    story = narrative(tmp_path)
    board = compact_package()
    validate_story_sources(story, board, project_root=tmp_path)
    supplement = reading_supplement(story, tmp_path)['narrative']
    assert 'text' not in supplement['story_paragraphs'][0]
    path = tmp_path / story['human_readable_path']
    original = path.read_bytes()
    path.write_text('drifted old approved story')
    with pytest.raises(ValueError, match='Markdown'):
        validate_story_sources(story, board, project_root=tmp_path)
    path.write_bytes(original)
    validate_story_sources(story, board, project_root=tmp_path)
