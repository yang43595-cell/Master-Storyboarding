"""Compare declared native references; no remote collection or authorization.

An empty reference list is a real applicable branch, not a fabricated entity type.
Even that branch requires an actual empty readback, never missing observations.
"""

import re


def valid_native_media(media, identity_mode='media_id', *, character_edges=False):
    if not isinstance(identity_mode, str) or identity_mode not in {'media_id', 'node_port_download', 'first_last_frame_download'} or not isinstance(media, list):
        return False
    if identity_mode == 'first_last_frame_download' and len(media) != 2:
        return False
    for ref in media:
        if not isinstance(ref, dict) or not isinstance(ref.get('node_id'), str) or not ref['node_id']:
            return False
        if identity_mode == 'media_id':
            if character_edges and 'reference_port' in ref:
                if ref['reference_port'] != 'image' or not valid_native_media([ref], 'node_port_download'):
                    return False
                continue
            if not isinstance(ref.get('media_id'), str) or not ref['media_id']:
                return False
        elif (set(ref) != {'node_id', 'reference_port', 'sha256'}
              or not isinstance(ref.get('reference_port'), str)
              or ref.get('reference_port') not in {'image', 'main'}
              or not isinstance(ref.get('sha256'), str)
              or re.fullmatch(r'[0-9a-f]{64}', ref['sha256']) is None):
            return False
    return True


def reference_readback_status(expected_media, mentions, entity_type=None, identity_mode='media_id', *,
                              character_edges=False):
    if identity_mode == 'first_last_frame_download':
        return 'not_applicable_endpoint_slots' if valid_native_media(expected_media, identity_mode) and mentions == [] else 'failed'
    if not valid_native_media(expected_media, identity_mode, character_edges=character_edges) or not isinstance(mentions, list):
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
    if len(mentions) != len(expected_media):
        return 'failed'
    for expected, actual in zip(expected_media, mentions):
        if (not isinstance(actual, dict) or actual.get('entity_type') != entity_type
                or actual.get('native_noneditable_entity') is not True or actual.get('node_id') != expected['node_id']):
            return 'failed'
        if character_edges and 'reference_port' in expected:
            if ('media_id' in actual or actual.get('reference_port') != expected['reference_port']
                    or actual.get('download_sha256') != expected['sha256']):
                return 'failed'
        elif actual.get('media_id') != expected['media_id']:
            return 'failed'
    return 'passed'


def endpoint_readback_status(expected_media, frame_slots, mentions=None):
    """Compare endpoint slots only; callers must separately verify raw control."""
    if not valid_native_media(expected_media, 'first_last_frame_download') or mentions not in (None, []):
        return 'failed'
    expected = [dict(role=role, **media) for role, media in zip(('start_frame', 'end_frame'), expected_media)]
    return 'passed' if frame_slots == expected else 'failed'
