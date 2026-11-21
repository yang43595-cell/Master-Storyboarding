"""Compare declared native references; no remote collection or authorization.

An empty reference list is a real applicable branch, not a fabricated entity type.
Even that branch requires an actual empty readback, never missing observations.
"""

from collections import Counter
import re


_REDFRAME_LABEL_TYPES = {'image': '图片', 'video': '视频', 'audio': '音频'}
_REDFRAME_TEXT_LABEL = re.compile(r'@[ \t]*(?:图片|视频|音频)[ \t]*\d+')
_REDFRAME_RAW_TOKEN = re.compile(r'\{\{[ \t]*ref\b')


def redframe_reference_labels(kinds):
    """Number references per media kind, in the order of the actual slots."""
    counts = Counter()
    labels = []
    for kind in kinds:
        if kind not in _REDFRAME_LABEL_TYPES:
            raise ValueError(f'红帧引用素材类型无效：{kind}')
        counts[kind] += 1
        labels.append(f'@{_REDFRAME_LABEL_TYPES[kind]}{counts[kind]}')
    return labels


def validate_redframe_reference_labels(prompt, refs):
    """Check new-plan placeholders before any native references can be created."""
    if _REDFRAME_RAW_TOKEN.search(prompt):
        raise ValueError('红帧上传前正文不能使用平台原生 {{ref}} 标记')
    labels = redframe_reference_labels(ref.get('kind') for ref in refs)
    expected = [label for label, ref in zip(labels, refs) if ref['canvas_binding']['prompt_reference']]
    actual = _REDFRAME_TEXT_LABEL.findall(prompt)
    if actual != expected:
        raise ValueError(
            f"红帧上传前正文引用须按有序素材槽逐项对应，应为 {'、'.join(expected) or '无'}，"
            f"实为 {'、'.join(actual) or '无'}")


def valid_native_media(media, identity_mode='media_id'):
    if not isinstance(identity_mode, str) or identity_mode not in {'media_id', 'node_port_download', 'first_last_frame_download'} or not isinstance(media, list):
        return False
    if identity_mode == 'first_last_frame_download' and len(media) != 2:
        return False
    for ref in media:
        if not isinstance(ref, dict) or not isinstance(ref.get('node_id'), str) or not ref['node_id']:
            return False
        if identity_mode == 'media_id':
            if not isinstance(ref.get('media_id'), str) or not ref['media_id']:
                return False
        elif (set(ref) != {'node_id', 'reference_port', 'sha256'}
              or not isinstance(ref.get('reference_port'), str)
              or ref.get('reference_port') not in {'image', 'main'}
              or not isinstance(ref.get('sha256'), str)
              or re.fullmatch(r'[0-9a-f]{64}', ref['sha256']) is None):
            return False
    return True


def reference_readback_status(expected_media, mentions, entity_type=None, identity_mode='media_id'):
    if identity_mode == 'first_last_frame_download':
        return 'not_applicable_endpoint_slots' if valid_native_media(expected_media, identity_mode) and mentions == [] else 'failed'
    if not valid_native_media(expected_media, identity_mode) or not isinstance(mentions, list):
        return 'failed'
    if not expected_media:
        return 'not_applicable' if mentions == [] else 'failed'
    if not isinstance(entity_type, str) or not entity_type.strip():
        return 'failed'
    if identity_mode == 'node_port_download':
        if entity_type != 'image':
            return 'failed'
        if any(not isinstance(ref, dict) or 'media_id' in ref
               or ref.get('entity_type') != entity_type
               or ref.get('native_noneditable_entity') is not True for ref in mentions):
            return 'failed'
        identities = [{'node_id': ref.get('node_id'), 'reference_port': ref.get('reference_port'),
                       'sha256': ref.get('download_sha256')} for ref in mentions]
        return 'passed' if identities == expected_media else 'failed'
    if any(not isinstance(ref, dict) or ref.get('entity_type') != entity_type
           or ref.get('native_noneditable_entity') is not True
           or not isinstance(ref.get('node_id'), str) or not ref['node_id']
           or not isinstance(ref.get('media_id'), str) or not ref['media_id']
           for ref in mentions):
        return 'failed'
    identities = [{'node_id': ref['node_id'], 'media_id': ref['media_id']}
                  for ref in mentions]
    expected_identities = [{'node_id': ref['node_id'], 'media_id': ref['media_id']}
                           for ref in expected_media]
    return 'passed' if identities == expected_identities else 'failed'


def endpoint_readback_status(expected_media, frame_slots, mentions=None):
    """Compare endpoint slots only; callers must separately verify raw control."""
    if not valid_native_media(expected_media, 'first_last_frame_download') or mentions not in (None, []):
        return 'failed'
    expected = [dict(role=role, **media) for role, media in zip(('start_frame', 'end_frame'), expected_media)]
    return 'passed' if frame_slots == expected else 'failed'
