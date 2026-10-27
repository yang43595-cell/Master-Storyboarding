"""Synthetic local receipts only: passing is not provenance or approval."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.check_video_prompt_focal_intent import inspect_document
from scripts.core import quality_evidence as q

BODY = 'Synthetic action, not real project content.'


def verified_payload(root, bodies):
    media = [{'node_id': 'SYNTHETIC-M1', 'media_id': 'SYNTHETIC-MEDIA'}]
    row = dict(segment_id='SYNTHETIC-G1', node_id='SYNTHETIC-D1', **bodies,
               parameters={'duration': 5}, ordered_media=media, native_entity_type='media')
    url = 'https://example.com/synthetic-canvas-never-contacted'
    observed = dict(phase='after_refresh', revision='SYNTHETIC-R1', target_url=url,
                    drafts=[dict(node_id=row['node_id'], prompt=BODY,
                                 parameters=copy.deepcopy(row['parameters']), ordered_media=copy.deepcopy(media),
                                 native_mentions=[dict(**m, native_noneditable_entity=True, entity_type='media') for m in media])])
    evidence = root / 'SYNTHETIC-readback.json'
    evidence.write_text(json.dumps(observed), encoding='utf-8')
    row['native_binding'] = dict(status='persisted_readback_verified', revision='SYNTHETIC-R1',
                                input_sha256=q.fingerprint(q.native_input_binding(row)),
                                evidence_path=evidence.name,
                                evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
                                canvas_url=url, node_id=row['node_id'])
    return dict(revision='SYNTHETIC-R1', target_url=url, generation_segments=[row])


def inspect_payload(root, payload):
    path = root / 'SYNTHETIC-input.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return inspect_document(path, project=root)


@pytest.mark.parametrize('bodies', [{'prompt': BODY}, {'prompt_text': BODY},
                                   {'prompt': BODY, 'prompt_text': BODY}],
                         ids=['prompt-only', 'prompt-text-only', 'both-equal'])
def test_matching_prompt_aliases_pass_native_gate_and_real_cli(tmp_path, bodies):
    payload = verified_payload(tmp_path, bodies)
    before = copy.deepcopy(payload)
    receipt = (tmp_path / 'SYNTHETIC-readback.json').read_bytes()
    q.validate_native_binding_claims(payload, tmp_path)
    assert inspect_payload(tmp_path, payload) == (1, [])
    script = Path(__file__).resolve().parents[1] / 'check_video_prompt_focal_intent.py'
    result = subprocess.run([sys.executable, str(script), str(tmp_path / 'SYNTHETIC-input.json'),
                             '--project', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert payload == before
    assert (tmp_path / 'SYNTHETIC-readback.json').read_bytes() == receipt


@pytest.mark.parametrize('bodies', [
    {}, {'prompt_text': ''}, {'prompt_text': None},
    {'prompt': BODY, 'prompt_text': BODY + ' conflicting'},
    {'prompt': BODY, 'prompt_text': BODY + ' '},
    {'prompt': BODY, 'prompt_text': ''}, {'prompt': BODY, 'prompt_text': None},
    {'prompt': BODY, 'prompt_text': 42}, {'prompt': '', 'prompt_text': BODY},
    {'prompt': None, 'prompt_text': BODY},
], ids=['missing', 'empty', 'null', 'conflicting', 'whitespace-conflict',
        'empty-alias', 'null-alias', 'numeric-alias', 'empty-prompt', 'null-prompt'])
def test_invalid_alias_cannot_hide_behind_valid_prompt(tmp_path, bodies):
    payload = verified_payload(tmp_path, bodies)
    with pytest.raises(ValueError):
        q.validate_native_binding_claims(payload, tmp_path)
    count, errors = inspect_payload(tmp_path, payload)
    assert count == 0 and errors


@pytest.mark.parametrize('change', ['body', 'parameters', 'ordered_media', 'stale_hash'])
def test_prompt_text_alias_does_not_relax_exact_binding(tmp_path, change):
    payload = verified_payload(tmp_path, {'prompt_text': BODY})
    row = payload['generation_segments'][0]
    if change in {'body', 'stale_hash'}:
        row['prompt_text'] += ' changed'
    elif change == 'parameters':
        row['parameters']['duration'] = 6
    else:
        row['ordered_media'][0]['media_id'] = 'OTHER-MEDIA'
    if change != 'stale_hash':
        row['native_binding']['input_sha256'] = q.fingerprint(q.native_input_binding(row))
    with pytest.raises(ValueError, match='旧verified声明失效' if change == 'stale_hash' else '回读不一致'):
        q.validate_native_binding_claims(payload, tmp_path)


def test_readback_still_requires_canonical_prompt_field(tmp_path):
    payload = verified_payload(tmp_path, {'prompt_text': BODY})
    path = tmp_path / 'SYNTHETIC-readback.json'
    observed = json.loads(path.read_text())
    observed['drafts'][0]['prompt_text'] = observed['drafts'][0].pop('prompt')
    path.write_text(json.dumps(observed))
    payload['generation_segments'][0]['native_binding']['evidence_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='回读不一致'):
        q.validate_native_binding_claims(payload, tmp_path)
