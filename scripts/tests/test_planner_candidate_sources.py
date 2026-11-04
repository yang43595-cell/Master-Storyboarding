"""Scoped source checks remain read-only; formal adoption still requires the whole story."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from scripts.core.story_sources import validate_story_sources
from scripts.core import web_content
from scripts import stage_context_cli as cli
from scripts.core import artifacts
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state, new_project_state, write_project_state
from test_director_compact import compact_package
from test_story_paragraph_sources import narrative


def packages(tmp_path):
    n = narrative(tmp_path)
    n.pop('human_readable_path')
    n['asset_requirements'][0].update(image_decision='无需独立图', image_reason='synthetic fixture')
    n['story_paragraphs'].append({'paragraph_id': 'STP-02', 'text': '稍后，主角离开。'})
    p = compact_package()
    p.pop('human_readable_path')
    p['source_narrative']['path'] = 'N.json'
    p['source_paragraph_ids'] = ['STP-01', 'STP-02']
    p['open_questions'] = ['STP-02 待细写；当前段结束状态和下一段入场仍须人工核对。']
    return n, p


def reject(call, text):
    try:
        call()
    except ValueError as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError('expected rejection: ' + text)


def test_scope_and_full_default(tmp_path):
    n, p = packages(tmp_path)
    before = copy.deepcopy((n, p))
    report = validate_story_sources(n, p, project_root=tmp_path, candidate_paragraph_ids=['STP-01'])
    assert report['checked_paragraph_ids'] == ['STP-01']
    assert report['unchecked_paragraph_ids'] == ['STP-02']
    assert report['adjacent_unwritten_paragraph_ids'] == ['STP-02']
    assert report['formal_adoption_allowed'] is False
    assert report['whole_package_validated'] is False
    assert (n, p) == before
    reject(lambda: validate_story_sources(n, p, project_root=tmp_path), '未落实到镜头')
    p['source_paragraph_ids'] = ['STP-01']
    validate_story_sources(n, p, project_root=tmp_path, candidate_paragraph_ids=['STP-01'])
    reject(lambda: validate_story_sources(n, p, project_root=tmp_path), '完整故事段落')
    for scope in ([], ['STP-MISSING'], ['STP-01', 'STP-01']):
        reject(lambda: validate_story_sources(n, p, candidate_paragraph_ids=scope), '局部范围')
    p['status'] = 'confirmed'
    reject(lambda: validate_story_sources(n, p, candidate_paragraph_ids=['STP-01']), 'candidate')


def test_boundary_and_dialogue_fidelity(tmp_path):
    n, p = packages(tmp_path)
    n['asset_requirements'][0]['source_paragraph_ids'].append('STP-02')
    p['scenes'][0]['source_paragraph_ids'].append('STP-02')
    check = lambda: validate_story_sources(n, p, project_root=tmp_path, candidate_paragraph_ids=['STP-01'])
    assert check()['cross_boundary_paragraph_ids'] == ['STP-02']
    p['scenes'][0]['shots'][0]['audio']['dialogue'][0]['text'] = '改了。'
    reject(check, '改写')
    p['scenes'][0]['shots'][0]['audio']['dialogue'][0]['text'] = '不。'
    p['scenes'][0]['shots'][0]['source_paragraph_ids'].append('STP-02')
    reject(check, '跨出本轮校验范围')
    # Complete cross-boundary shot is valid when both paragraphs are checked.
    validate_story_sources(n, p, project_root=tmp_path, candidate_paragraph_ids=['STP-01', 'STP-02'])
    validate_story_sources(n, p, project_root=tmp_path)


def test_gap_foreign_keys_and_empty_assets(tmp_path):
    n, p = packages(tmp_path)
    gap = {'gap_id': 'GAP-ASSET-01', 'source_paragraph_ids': ['STP-01'],
           'affected_shot_ids': ['S01-010'], 'related_asset_ids': [],
           'missing_information': '未知物件', 'impact': '不能确定交互', 'return_required': True}
    p['asset_gaps'] = [gap]
    check = lambda: validate_story_sources(n, p, project_root=tmp_path, candidate_paragraph_ids=['STP-01'])
    check()  # Genuine missing asset need not invent an AST.
    gap['affected_shot_ids'] = ['S99-999']; reject(check, '未知镜头')
    gap['affected_shot_ids'] = ['S01-010']
    gap['related_asset_ids'] = ['AST-MISSING']; reject(check, '未知资产')
    gap['related_asset_ids'] = ['AST-CHAR-A']; check()
    gap['source_paragraph_ids'] = ['STP-02']; reject(check, '镜头与来源段落不对应')
    gap['source_paragraph_ids'] = ['STP-01']
    n['asset_requirements'][0]['source_paragraph_ids'] = ['STP-02']
    p['scenes'][0]['shots'][0]['source_asset_requirement_ids'] = []
    reject(check, '资产与来源段落不对应')


def test_scene_identity_and_same_scene_segments(tmp_path):
    n, p = packages(tmp_path)
    scene = copy.deepcopy(p['scenes'][0])
    scene['shots'][0]['shot_id'] = 'S01-020'
    scene['shots'][0]['source_dialogue_ids'] = []
    scene['shots'][0]['audio']['dialogue'] = []
    p['scenes'].append(scene)
    reject(lambda: validate_story_sources(n, p, candidate_paragraph_ids=['STP-01']), '场景编号重复：S01')
    reject(lambda: web_content.compile_content(json.dumps(p).encode(), stage='director', case_id='01',
           source_script_version='a' * 64, expected_version=None, scope='qa'), '场景编号重复：S01')
    p['scenes'].pop()
    p['scenes'][0]['shots'].append(scene['shots'][0])
    validate_story_sources(n, p, candidate_paragraph_ids=['STP-01'])


def test_local_cli_and_formal_adoption_chain(tmp_path):
    n, p = packages(tmp_path)
    # This test formally adopts a newly authored synthetic narrative. Historical
    # read-only fixtures above intentionally keep their original evidence shape.
    evidence = {'sources': [], 'dialogues': [{
        'dialogue_id': row['dialogue_id'], 'speaker_id': row['speaker_id'],
        'speaker_status': 'verified', 'text': row['text'], 'source_ids': [],
        'origin': {'kind': 'authored_addition', 'reference': 'synthetic original story request',
                   'scope': 'synthetic formal adoption test', 'author': 'synthetic user',
                   'purpose': 'exercise formal narrative and scoped storyboard checks', 'revision': n['revision']},
        'verification': {'scope': 'dialogue', 'dialogue_id': row['dialogue_id'],
                         'speaker_id': row['speaker_id'], 'text': row['text'],
                         'method': 'user_confirmation', 'reference': 'synthetic exact text and speaker approval',
                         'reviewer': 'synthetic user'},
    } for row in n['dialogues']]}
    evidence_path = tmp_path / 'synthetic-dialogue-review.json'
    write_json(evidence_path, evidence)
    n.update(dialogue_evidence_path=evidence_path.name,
             dialogue_evidence_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest())
    write_project_state(tmp_path, new_project_state(current_stage='storyboard-planner'))
    write_json(tmp_path / 'N.json', n)
    write_json(tmp_path / 'B.json', p)
    cli.main(['adopt-package', '--project', str(tmp_path), '--artifact-id', 'N',
              '--logical-id', 'narrative.package', '--path', 'N.json', '--revision', 'R01',
              '--approval-ref', 'synthetic exact narrative approval'])
    p['source_narrative']['artifact_id'] = 'N'
    write_json(tmp_path / 'B.json', p)
    command = [sys.executable, '-B', '-m', 'scripts.core.story_sources', '--project', str(tmp_path),
               '--narrative', str(tmp_path / 'N.json'), '--storyboard', str(tmp_path / 'B.json')]
    result = subprocess.run(command + ['--candidate-paragraph-id', 'STP-01'],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[0])['formal_adoption_allowed'] is False
    full = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    assert full.returncode != 0 and '未落实到镜头' in full.stderr
    reject(lambda: cli.main(['adopt-package', '--project', str(tmp_path), '--artifact-id', 'B',
           '--logical-id', 'storyboard.package', '--path', 'B.json', '--revision', p['revision'],
           '--depends-on', 'narrative.package', '--approval-ref', 'synthetic segment approval']), '稿件保持候选')
    entry = next(a for a in artifacts.load_manifest(tmp_path)['artifacts'] if a['artifact_id'] == 'B')
    assert entry['status'] == 'candidate'
    assert 'B' not in load_project_state(tmp_path)['current_artifacts']
    assert not any('B' in c.get('artifact_ids', []) for c in load_project_state(tmp_path)['user_confirmations'])


def test_local_web_real_contract(tmp_path):
    n, p = packages(tmp_path)
    result = web_content.compile_content(json.dumps(p).encode(), stage='director', case_id='01',
        source_script_version='a' * 64, expected_version=None, scope='qa', project_root=tmp_path,
        director_narrative=n, candidate_paragraph_ids=['STP-01'])
    assert result['request']['content']['reviewable'] is False
    assert result['directorSourceCheck']['unchecked_paragraph_ids'] == ['STP-02']
    ids = [block['id'] for block in result['request']['content']['blocks']]
    assert len(ids) == len(set(ids)) and 'director-validation-scope' in ids
    assert result['published'] is False
    reject(lambda: web_content.compile_content(json.dumps(p).encode(), stage='director', case_id='01',
        source_script_version='a' * 64, expected_version=None, scope='qa', reviewable=True,
        director_narrative=n, candidate_paragraph_ids=['STP-01']), 'reviewable')


def test_silent_scope_and_legacy_default(tmp_path):
    n, p = packages(tmp_path)
    p['scenes'][0]['source_paragraph_ids'] = ['STP-02']
    shot = p['scenes'][0]['shots'][0]
    shot['source_paragraph_ids'] = ['STP-02']
    shot['source_asset_requirement_ids'] = []
    shot['source_dialogue_ids'] = []
    shot['audio']['dialogue'] = []
    p['source_dialogue_ids'] = []
    report = validate_story_sources(n, p, candidate_paragraph_ids=['STP-02'])
    assert report['unchecked_paragraph_ids'] == ['STP-01']
    legacy = compact_package()
    original = narrative(tmp_path)
    validate_story_sources(original, legacy, project_root=tmp_path)
    # Legacy v3 remains accepted by the same strict default path.
    from test_storyboard_planner_contract import sample_package
    validate_story_sources(original, sample_package(), project_root=tmp_path)


def test_web_scope_requires_source_and_cannot_approve(tmp_path):
    n, p = packages(tmp_path)
    kwargs = dict(stage='director', case_id='01', source_script_version='a' * 64,
                  expected_version=None, scope='qa', candidate_paragraph_ids=['STP-01'])
    reject(lambda: web_content.compile_content(json.dumps(p).encode(), **kwargs), '完整原叙事')
    reject(lambda: web_content.compile_content(json.dumps(p).encode(), director_narrative=n,
                  reviewable=True, **kwargs), 'reviewable')
    n['revision'] = 'R99'
    reject(lambda: web_content.compile_content(json.dumps(p).encode(), director_narrative=n,
                  **kwargs), '修订不匹配')
