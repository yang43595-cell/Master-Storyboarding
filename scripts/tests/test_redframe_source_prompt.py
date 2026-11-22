import hashlib
import json
from urllib.parse import quote

import pytest

from scripts.core.quality_evidence import IMAGE_ASPECTS, validate_image_evidence
from scripts.core.native_references import redframe_source_prompt
from scripts.redframe_draft import _native_content


def test_converts_only_declared_ordered_source_mentions():
    materials = [
        {'kind': 'image', 'candidate_mention': '@披肩'},
        {'kind': 'video', 'candidate_mention': '@披肩动作'},
    ]
    assert redframe_source_prompt(
        '@披肩 颜色。\n@披肩动作 运镜。\n披肩盖好后才松手。', materials
    ) == '@图片1 颜色。\n@视频1 运镜。\n披肩盖好后才松手。'


@pytest.mark.parametrize('prompt', [
    '@披肩动作 运镜。\n@披肩 颜色。',
    '@披肩 颜色。\n@视频1 运镜。',
    '@披肩 颜色。\n@披肩动作 运镜。\n@披肩 重复。',
])
def test_rejects_changed_or_mixed_reference_order(prompt):
    materials = [
        {'kind': 'image', 'candidate_mention': '@披肩'},
        {'kind': 'video', 'candidate_mention': '@披肩动作'},
    ]
    with pytest.raises(ValueError):
        redframe_source_prompt(prompt, materials)


def test_narrowing_reviewed_reference_keeps_roles_entities_and_state(tmp_path):
    image = tmp_path / 'reference.png'
    image.write_bytes(b'reference fixture')
    checksum = hashlib.sha256(image.read_bytes()).hexdigest()
    role = {'roles': ['state_reference'], 'entity_ids': ['ACTOR'], 'state_id': 'film'}
    evidence = {
        'status': 'pass', 'observer': 'fixture', 'checked_at': '2026-11-22T12:00:00+00:00',
        'image_sha256': checksum, 'source_files': [{'path': image.name, 'sha256': checksum}],
        'source_shot_ids': ['S1', 'S2'], 'view': {'resolution': 'original', 'locator': 'fixture'},
        'uses': [{**role, 'source_shot_ids': ['S1', 'S2']}],
        'checks': {key: {'result': 'pass', 'observation': 'fixture', 'locator': 'fixture'}
                   for key in IMAGE_ASPECTS},
    }
    with pytest.raises(ValueError, match='用途未核'):
        validate_image_evidence(tmp_path, image, evidence, ['S2'], reference=role)
    assert validate_image_evidence(
        tmp_path, image, evidence, ['S2'], reference=role, allow_narrower_use=True
    )['usage_checked']
    with pytest.raises(ValueError, match='用途未核'):
        validate_image_evidence(
            tmp_path, image, evidence, ['S2'], reference={**role, 'state_id': 'wiped'},
            allow_narrower_use=True,
        )


def test_linked_redframe_prompt_uses_native_identity_when_slots_are_grouped():
    media = [{'node_id': 'draft', 'media_id': 'upload'}, {'node_id': 'face', 'media_id': 'image'}]

    def chip(node, medium, index):
        identity = quote(json.dumps([node, medium]))
        return {'type': 'Custom',
                'html': f'<span class="imageReference" data-reference-id="{identity}" data-value="{{{{ref {index}}}}}"></span>'}

    data = {'outputs': {}, 'videoMode': 'reference', 'model': '["provider","model"]',
            'mode': '"reference"', 'ratio': '9:16', 'resolution': '480p', 'duration': 30,
            'prompt': '{{ref 2}} 桌面。\n{{ref 1}} 女一。',
            'promptModel': [[chip('draft', 'upload', 2)], [chip('face', 'image', 1)]]}
    models = {'reference': ('Seedance', {'providerId': 'provider', 'modelId': 'model', 'mode': ['reference']})}
    prompt, _, mentions = _native_content('draft', data, media, ['image', 'image'], models, linked=True)
    assert prompt == '@图片1 桌面。\n@图片2 女一。'
    assert [(m['node_id'], m['media_id']) for m in mentions] == [('draft', 'upload'), ('face', 'image')]
    with pytest.raises(ValueError, match='实际槽不符'):
        _native_content('draft', data, media, ['image', 'image'], models)
    data['prompt'] = '{{ref 1}} 桌面。\n{{ref 2}} 女一。'
    with pytest.raises(ValueError, match='实际引用实体不一致'):
        _native_content('draft', data, media, ['image', 'image'], models, linked=True)
