"""Compare declared native references; no remote collection or authorization.

An empty reference list is a real applicable branch, not a fabricated entity type.
Even that branch requires an actual empty readback, never missing observations.
"""


def reference_readback_status(expected_media, mentions, entity_type=None):
    if not isinstance(expected_media, list) or not isinstance(mentions, list):
        return 'failed'
    if not expected_media:
        return 'not_applicable' if mentions == [] else 'failed'
    if not isinstance(entity_type, str) or not entity_type.strip():
        return 'failed'
    if any(not isinstance(ref, dict) or ref.get('entity_type') != entity_type
           or ref.get('native_noneditable_entity') is not True
           or not isinstance(ref.get('node_id'), str) or not ref['node_id']
           or not isinstance(ref.get('media_id'), str) or not ref['media_id']
           for ref in mentions):
        return 'failed'
    identities = [{'node_id': ref['node_id'], 'media_id': ref['media_id']}
                  for ref in mentions]
    return 'passed' if identities == expected_media else 'failed'
