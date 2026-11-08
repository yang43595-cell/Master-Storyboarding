"""Synthetic transport checks; no real credential or external host is used."""
import io
import base64
import hashlib
import json
import threading
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, build_opener

import pytest

from scripts.core.agent_review_api import (
    AgentReviewClient, AgentReviewError, MAX_RESPONSE_BYTES, MAX_TEXT_BYTES,
    _NoRedirect, effective_approval,
)

TOKEN = 'SYNTHETIC_CLIENT_TEST_TOKEN_ONLY_1234567890'
OP = '024811ca-de9a-42ca-9807-4733f26f23e7'
VERSION = 'a' * 64


def case_response():
    return {'ok': True, 'case': {'id': '12', 'name': '合成测试', 'projectId': 'four-ad-review', 'createdAt': '2026-11-05T00:00:00.000Z'}}


def publish_response():
    return {'ok': True, 'projectId': 'four-ad-review', 'scope': 'live', 'caseId': '12',
            'stage': 'script', 'version': VERSION, 'headGeneration': 1, 'assetId': 'asset-12',
            'operationId': OP, 'url': '/?projectId=four-ad-review&scope=live&caseId=12&stage=script&version=' + VERSION}


def status_response(*, has_head=True):
    return {'case': {'id': '12', 'name': '合成测试', 'projectId': 'four-ad-review', 'status': 'active', 'revision': 0},
            'stage': 'script', 'head': {'version': VERSION, 'generation': 1} if has_head else None,
            'expectations': {'expectedHeadVersion': VERSION if has_head else None,
                             'expectedHeadGeneration': 1 if has_head else 0, 'expectedReviewRevision': 0},
            'sourceScriptVersion': VERSION if has_head else None}


class Response(io.BytesIO):
    def __init__(self, value, status=200):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        super().__init__(raw)
        self.status = status

    def getcode(self):
        return self.status


class Transport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def client(*outcomes):
    transport = Transport(*outcomes)
    return AgentReviewClient(token_reader=lambda: TOKEN, opener=transport), transport


def publish(c, **overrides):
    values = {'case_id': '12', 'name': 'draft.md', 'text': '# 合成稿\n不是业务内容。', 'operation_id': OP}
    values.update(overrides)
    return c.publish(**values)


def test_three_routes_send_documented_fields_and_bearer_only():
    c, transport = client(Response(case_response()), Response(publish_response()), Response(status_response()))
    assert c.create_case(name='合成测试', operation_id=OP)['case']['id'] == '12'
    assert publish(c)['version'] == VERSION
    assert c.status(case_id='12')['head']['version'] == VERSION
    requests = [item[0] for item in transport.requests]
    assert [r.get_method() for r in requests] == ['POST', 'POST', 'GET']
    assert requests[2].full_url.endswith('/api/agent/review/status?caseId=12&stage=script')
    assert requests[0].data == ('{"name":"合成测试","operationId":"' + OP + '"}').encode()
    assert set(json.loads(requests[1].data)) == {'caseId', 'stage', 'name', 'text', 'operationId'}
    for request, timeout in transport.requests:
        headers = {key.lower(): value for key, value in request.header_items()}
        assert headers.pop('authorization') == 'Bearer ' + TOKEN
        assert headers == ({'content-type': 'application/json'} if request.data else {})
        assert timeout == 20


def test_same_operation_retry_is_explicit_and_preserved():
    c, transport = client(Response(publish_response()), Response(publish_response()))
    assert publish(c) == publish(c)
    assert len(transport.requests) == 2
    assert transport.requests[0][0].data == transport.requests[1][0].data


def test_asset_publish_requires_a_receipt_for_the_sent_image():
    image = b'\x89PNG\r\n\x1a\nsynthetic transport bytes'
    expected = publish_response()
    expected.update(stage='assets', url=expected['url'].replace('stage=script', 'stage=assets'),
                    sha256=hashlib.sha256(image).hexdigest())
    c, transport = client(Response(expected), Response({**expected, 'sha256': 'b' * 64}))
    kwargs = dict(case_id='12', name='fixture.png', image=image, purpose='Fixture',
                  operation_id=OP, artifact_id='IMG-1', ast_id='AST-1', output_id='main')
    assert c.publish_asset(**kwargs) == expected
    request = transport.requests[0][0]
    assert request.full_url.endswith('/publish-assets')
    assert base64.b64decode(json.loads(request.data)['dataBase64']) == image
    with pytest.raises(AgentReviewError, match='mismatched receipt'):
        c.publish_asset(**kwargs)


def test_exact_approval_must_be_current_and_tied_to_submitted_event():
    scope = dict(projectId='four-ad-review', scope='live', caseId='12', stage='assets', version=VERSION)
    event = dict(decision='approve', actorId='reviewer', createdAt='2026-11-07T00:00:00Z')
    approval = dict(actorId='reviewer', decidedAt=event['createdAt'], scope=scope)
    validity = dict(isCurrent=True, stillEffective=True, reviewable=True, sourceStale=False,
                    dependencyStale=False, dependencyUnapproved=False)
    decision = dict(case=status_response()['case'], stage='assets', version=VERSION, approval=approval,
                    latestEvent=event, decisionStatus='approved', validity=validity)
    c, _ = client(Response(decision), Response({**decision, 'validity': {**validity, 'isCurrent': False}}),
                  Response({**decision, 'latestEvent': None}))
    params = dict(case_id='12', stage='assets', version=VERSION)
    assert effective_approval(c.decision(**params))
    assert not effective_approval(c.decision(**params))
    with pytest.raises(AgentReviewError, match='mismatched receipt'):
        c.decision(**params)


@pytest.mark.parametrize('value', [URLError(TOKEN), TimeoutError(TOKEN), OSError(TOKEN)])
def test_unknown_transport_outcome_never_retries_or_echoes_secret(value):
    c, transport = client(value)
    with pytest.raises(AgentReviewError) as caught:
        publish(c)
    assert caught.value.code == 'TRANSPORT_ERROR'
    assert TOKEN not in str(caught.value)
    assert len(transport.requests) == 1


@pytest.mark.parametrize('status,body,code', [
    (401, {'error': 'AGENT_REVIEW_WRITE_UNAUTHORIZED'}, 'AGENT_REVIEW_WRITE_UNAUTHORIZED'),
    (409, {'error': 'server echoes ' + TOKEN}, 'HTTP_ERROR'),
    (502, {'error': TOKEN}, 'HTTP_ERROR'),
    (302, {'error': 'redirect'}, 'REDIRECT_BLOCKED'),
])
def test_http_error_diagnostics_do_not_return_arbitrary_body(status, body, code):
    error = HTTPError('https://example.invalid', status, TOKEN, {}, io.BytesIO(json.dumps(body).encode()))
    c, _ = client(error)
    with pytest.raises(AgentReviewError) as caught:
        publish(c)
    assert caught.value.code == code
    assert caught.value.http_status == status
    assert TOKEN not in str(caught.value)
    assert 'server echoes' not in str(caught.value)


@pytest.mark.parametrize('overrides', [
    {'operation_id': 'not-uuid'}, {'operation_id': '00000000-0000-0000-0000-000000000000'},
    {'stage': 'assets'}, {'case_id': ''}, {'name': '../draft.md'}, {'name': 'draft.json'},
    {'text': ''}, {'text': ' \n'}, {'text': '正文\0'}, {'text': '\ud800'},
    {'text': '中' * (MAX_TEXT_BYTES // 3 + 1)}, {'purpose': '\n'}
])
def test_invalid_publish_is_rejected_before_credential_read_or_network(overrides):
    reads = []
    transport = Transport()
    c = AgentReviewClient(token_reader=lambda: reads.append(True), opener=transport)
    with pytest.raises(AgentReviewError):
        publish(c, **overrides)
    assert not reads and not transport.requests


def test_byte_limit_accepts_exact_100kb_and_bounds_encoded_request():
    c, transport = client(Response(publish_response()))
    publish(c, text='x' * MAX_TEXT_BYTES)
    assert len(json.loads(transport.requests[0][0].data)['text'].encode()) == MAX_TEXT_BYTES
    # Escaping makes a valid 100KB text exceed the separate 128KB JSON limit.
    with pytest.raises(AgentReviewError, match='128KB'):
        publish(c, text='\t' * (MAX_TEXT_BYTES - 1) + 'x')
    assert len(transport.requests) == 1


@pytest.mark.parametrize('base', ['http://example.invalid', 'https://u:p@example.invalid',
                                 'https://example.invalid/path', 'https://example.invalid?x=1',
                                 'https://example.invalid/#fragment', 'https://example.invalid:bad',
                                 'https://exam ple.invalid', None])
def test_only_bare_https_origin_is_accepted(base):
    with pytest.raises(AgentReviewError):
        AgentReviewClient(base, token_reader=lambda: TOKEN)


def test_local_token_is_read_only_when_request_is_made(tmp_path):
    token_file = tmp_path / 'synthetic.token'
    transport = Transport(Response(status_response(has_head=False)))
    c = AgentReviewClient(token_file=token_file, opener=transport)
    token_file.write_text(TOKEN + '\n')
    assert c.status(case_id='12')['head'] is None
    token_file.write_text('bad\r\nAuthorization: injection')
    with pytest.raises(AgentReviewError) as caught:
        c.status(case_id='12')
    assert caught.value.code == 'CREDENTIAL_ERROR'
    assert len(transport.requests) == 1


@pytest.mark.parametrize('raw,code', [
    (b'not JSON', 'INVALID_RESPONSE'), (b'[]', 'INVALID_RESPONSE'),
    (b'{"head":null,"head":null}', 'INVALID_RESPONSE'),
    (b'{"value":NaN}', 'INVALID_RESPONSE'),
    (b'x' * (MAX_RESPONSE_BYTES + 1), 'RESPONSE_TOO_LARGE'),
    (b'{"ok":false}', 'API_ERROR'),
    (json.dumps({'secret': TOKEN}).encode(), 'INVALID_RESPONSE'),
    (('{"secret":"\\u0053' + TOKEN[1:] + '"}').encode(), 'INVALID_RESPONSE'),
])
def test_bad_or_oversize_responses_fail_with_fixed_messages(raw, code):
    c, _ = client(Response(raw))
    with pytest.raises(AgentReviewError) as caught:
        c.status(case_id='12')
    assert caught.value.code == code


@pytest.mark.parametrize('key,value', [
    ('caseId', '13'), ('stage', 'director'), ('scope', 'private'), ('version', 'old'),
    ('headGeneration', True), ('headGeneration', 0), ('projectId', ''), ('assetId', ''),
    ('operationId', 'other'), ('url', 'https://another.invalid/path'),
    ('url', '//another.invalid/path'), ('url', '/?caseId=13'),
    ('url', 'https://['), ('url', '/?caseId=12\n'),
])
def test_publication_receipt_must_match_the_request(key, value):
    result = publish_response()
    result[key] = value
    c, _ = client(Response(result))
    with pytest.raises(AgentReviewError) as caught:
        publish(c)
    assert caught.value.code == 'INVALID_RECEIPT'


def test_status_rejects_inconsistent_head_and_cas():
    result = status_response()
    result['expectations']['expectedHeadGeneration'] = 2
    c, _ = client(Response(result))
    with pytest.raises(AgentReviewError) as caught:
        c.status(case_id='12')
    assert caught.value.code == 'INVALID_RECEIPT'


def test_redirect_is_blocked_before_bearer_can_reach_destination():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, dict(self.headers)))
            self.send_response(302)
            self.send_header('Location', '/credential-destination')
            self.end_headers()

        def log_message(self, *_args):
            pass

    class LoopbackTransport(HTTPSHandler):
        # Test-only transport: retain HTTPS origin checks, use local plain HTTP.
        def https_open(self, req):
            return self.do_open(HTTPConnection, req)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        c = AgentReviewClient('https://127.0.0.1:' + str(server.server_port),
                              token_reader=lambda: TOKEN,
                              opener=build_opener(_NoRedirect(), LoopbackTransport()))
        with pytest.raises(AgentReviewError) as caught:
            c.status(case_id='12')
        assert caught.value.code == 'REDIRECT_BLOCKED'
        assert len(seen) == 1
        assert seen[0][0].startswith('/api/agent/review/status?')
        assert seen[0][1]['Authorization'] == 'Bearer ' + TOKEN
        assert not {'cookie', 'origin'} & {key.lower() for key in seen[0][1]}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
