"""Synthetic regressions for the persisted-backend adapter finding at 427b761.

No backend mutation or production approval; the exact integration probe remains
owned by the website task. ID limits follow its existing FeedbackRequest schema.
"""
import copy

import pytest

from scripts.core.web_content import WebContentError, effective_feedback
from test_web_content_adapter import state_response


def receipt_response(ident='synthetic-approval'):
    version, response = state_response()
    receipt = {'id': ident, 'scope': 'qa', 'caseId': '05', 'stage': 'director',
               'version': version, 'decision': 'approve', 'comment': '', 'attachments': []}
    response['currentFeedback'] = copy.deepcopy(receipt)
    response['currentState']['currentFeedback'] = copy.deepcopy(receipt)
    return version, response


def read(response, version):
    return effective_feedback(response, scope='qa', case_id='05', stage='director', version=version)


@pytest.mark.parametrize('ident', [None, '', ' ', '\t\n', 0, 1.5, True, [], {},
                                  'short', 'a' * 101, 'invalid/id', ' spaced-id', '中文回执编号'])
def test_invalid_receipt_identity_is_rejected(ident):
    version, response = receipt_response(ident)
    with pytest.raises(WebContentError, match='id'):
        read(response, version)


def test_exact_integration_missing_id_mutation_is_rejected_without_writes():
    version, response = receipt_response()
    del response['currentFeedback']['id']
    del response['currentState']['currentFeedback']['id']
    before = copy.deepcopy(response)
    with pytest.raises(WebContentError, match='id'):
        read(response, version)
    assert response == before


@pytest.mark.parametrize('feedback', [[], 'synthetic', 1, True])
def test_feedback_must_be_an_object(feedback):
    version, response = receipt_response()
    response['currentFeedback'] = feedback
    response['currentState']['currentFeedback'] = feedback
    with pytest.raises(WebContentError):
        read(response, version)


@pytest.mark.parametrize('field,value', [('scope', 'live'), ('caseId', '01'),
    ('stage', 'assets'), ('version', '0' * 64)])
def test_receipt_must_belong_to_the_exact_state_identity(field, value):
    version, response = receipt_response()
    response['currentFeedback'][field] = value
    response['currentState']['currentFeedback'][field] = value
    with pytest.raises(WebContentError):
        read(response, version)


@pytest.mark.parametrize('other', [None, {}, {'id': 'different-current'}])
def test_explicit_top_level_feedback_cannot_disagree_with_current_state(other):
    version, response = receipt_response()
    response['currentFeedback'] = other
    with pytest.raises(WebContentError):
        read(response, version)


@pytest.mark.parametrize('ident', ['valid_01', 'a' * 100])
def test_valid_schema_id_boundaries_preserve_approval_and_receipt(ident):
    version, response = receipt_response(ident)
    before = copy.deepcopy(response)
    result = read(response, version)
    assert result['effectiveApproval'] == 'approved'
    assert result['currentFeedback'] == response['currentFeedback']
    assert response == before


def test_authoritative_state_alone_can_supply_valid_receipt():
    version, response = receipt_response()
    del response['currentFeedback']
    assert read(response, version)['effectiveApproval'] == 'approved'


@pytest.mark.parametrize('change,expected', [({'isCurrent': False}, 'historical'),
    ({'currentVersion': '0' * 64}, 'historical'), ({'sourceStale': True}, 'stale'),
    ({'packageSourceScriptVersion': '0' * 64}, 'stale'), ({'reviewable': False}, 'not_reviewable')])
def test_valid_receipt_cannot_override_currentness_or_source_validity(change, expected):
    version, response = receipt_response()
    response['currentState'].update(change)
    assert read(response, version)['effectiveApproval'] == expected
