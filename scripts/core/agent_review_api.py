"""Small Bearer-only client for the documented Agent Review text API.

The caller owns operation IDs, authorization and receipts. Mutations are never
retried here; a timeout means the remote outcome is unknown. No review approval
is inferred from either a publication receipt or a status response.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_BASE_URL = 'https://yangyangyang.click'
MAX_TEXT_BYTES = 100 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
TEXT_STAGES = ('script', 'director', 'prompts')
IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', re.I)
_TOKEN = re.compile(r'[-_A-Za-z0-9]{32,200}')
_VERSION = re.compile(r'[a-f0-9]{64}')
_KNOWN_ERRORS = {
    'AGENT_REVIEW_WRITE_NOT_CONFIGURED', 'AGENT_REVIEW_WRITE_UNAUTHORIZED',
    'AGENT_REVIEW_WRITE_NO_BROWSER_COOKIE', 'AGENT_REVIEW_WRITE_NO_BROWSER_ORIGIN',
    'AGENT_REVIEW_WRITE_METHOD_NOT_ALLOWED', 'AGENT_REVIEW_WRITE_FAILED',
}


class AgentReviewError(ValueError):
    """Fixed, credential-safe diagnostic; server bodies are never echoed."""

    def __init__(self, message, *, code='INVALID_INPUT', http_status=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Even same-origin redirects are unexpected for these fixed API routes.
        return None


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _version(value):
    return isinstance(value, str) and _VERSION.fullmatch(value) is not None


def _invalid(message):
    raise AgentReviewError(message)


def _receipt_error():
    raise AgentReviewError('Agent Review returned an invalid or mismatched receipt; remote outcome requires verification.', code='INVALID_RECEIPT')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _decode(data):
    def invalid_constant(_value):
        raise ValueError('non-finite JSON')
    result = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object,
                        parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError('object required')
    return result


class AgentReviewClient:
    """Origin-bound HTTPS client; ``opener``/``token_reader`` are test seams.

    Provide exactly one credential source. Credentials are read at request time,
    not copied to receipts or configuration. An injected opener must implement
    ``open(request, timeout=...)`` (or be a callable with that signature).
    """

    def __init__(self, base_url=DEFAULT_BASE_URL, token_file=None, timeout=20, *, token_reader=None, opener=None):
        try:
            origin = urlsplit(base_url)
            valid = (isinstance(base_url, str) and origin.scheme == 'https'
                     and bool(origin.hostname) and origin.username is None
                     and origin.password is None and origin.path in ('', '/')
                     and not origin.query and not origin.fragment
                     and not any(c.isspace() or ord(c) < 32 for c in base_url))
            origin.port  # Validate the port before any request.
        except (TypeError, ValueError):
            valid = False
        if not valid:
            _invalid('Agent Review base_url must be a bare HTTPS origin.')
        if (token_file is None) == (token_reader is None):
            _invalid('Provide exactly one Agent Review credential source.')
        if token_reader is not None and not callable(token_reader):
            _invalid('token_reader must be callable.')
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0 or timeout > 120):
            _invalid('Agent Review timeout must be between 0 and 120 seconds.')
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self._token_file = Path(token_file) if token_file is not None else None
        self._token_reader = token_reader
        self._opener = opener if opener is not None else build_opener(_NoRedirect())

    def _token(self):
        try:
            if self._token_reader is not None:
                value = self._token_reader()
            else:
                with self._token_file.open('rb') as stream:
                    value = stream.read(203).decode('ascii')
                if len(value) > 202:
                    raise ValueError()
            if not isinstance(value, str):
                raise ValueError()
            value = value.strip()
            if not _TOKEN.fullmatch(value):
                raise ValueError()
            return value
        except Exception:
            raise AgentReviewError('Cannot read a valid Agent Review credential.', code='CREDENTIAL_ERROR') from None

    @staticmethod
    def _target(case_id, stage, stages=TEXT_STAGES):
        if (not _text(case_id) or len(case_id) > 200
                or any(ord(c) < 32 for c in case_id)):
            _invalid('A nonempty case_id is required.')
        if stage not in stages:
            _invalid('Agent Review text stage must be script, director or prompts.')

    @staticmethod
    def _operation(operation_id):
        if not isinstance(operation_id, str) or not _UUID.fullmatch(operation_id):
            _invalid('operation_id must be a UUID accepted by the Agent Review API.')

    def _request(self, method, route, payload=None, query=None):
        data = None
        if payload is not None:
            try:
                data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
            except (TypeError, ValueError, UnicodeError):
                _invalid('Agent Review request must be valid UTF-8 JSON.')
            limit = 12 * 1024 * 1024 if route == 'publish-assets' else 128 * 1024
            if len(data) > limit:
                _invalid('Encoded Agent Review request exceeds the server ' +
                         ('12 MiB' if route == 'publish-assets' else '128KB') + ' limit.')
        token = self._token()
        headers = {'Authorization': 'Bearer ' + token}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        url = self.base_url + '/api/agent/review/' + route
        if query:
            url += '?' + urlencode(query)
        request = Request(url, data=data, headers=headers, method=method)
        try:
            open_request = getattr(self._opener, 'open', self._opener)
            response = open_request(request, timeout=self.timeout)
            with response:
                status = response.getcode()
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            status = error.code
            try:
                with error:
                    raw = error.read(MAX_RESPONSE_BYTES + 1)
            except Exception:
                raw = b''
        except (URLError, OSError, TimeoutError, HTTPException):
            raise AgentReviewError('Agent Review request failed; remote outcome is unknown. Do not create a new operation ID for a retry.', code='TRANSPORT_ERROR') from None
        if not _integer(status, 100) or status > 599:
            raise AgentReviewError('Agent Review returned an invalid HTTP status; remote outcome requires verification.', code='INVALID_RESPONSE')
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AgentReviewError('Agent Review response exceeded the size limit; remote outcome requires verification.', code='RESPONSE_TOO_LARGE', http_status=status)
        if status < 200 or status >= 300:
            code = 'REDIRECT_BLOCKED' if 300 <= status < 400 else 'HTTP_ERROR'
            try:
                remote_error = _decode(raw).get('error')
                if isinstance(remote_error, str) and remote_error in _KNOWN_ERRORS and token not in remote_error:
                    code = remote_error
            except (ValueError, UnicodeError, RecursionError):
                pass
            raise AgentReviewError('Agent Review HTTP request failed (' + str(status) + ', ' + code + '); remote outcome requires verification.', code=code, http_status=status) from None
        try:
            result = _decode(raw)
            reflects_credential = token in json.dumps(result, ensure_ascii=False)
        except (ValueError, UnicodeError, RecursionError):
            raise AgentReviewError('Agent Review returned invalid JSON; remote outcome requires verification.', code='INVALID_RESPONSE', http_status=status) from None
        if reflects_credential:
            raise AgentReviewError('Agent Review returned an unsafe response; remote outcome requires verification.', code='INVALID_RESPONSE', http_status=status)
        if result.get('ok') is False or 'error' in result:
            raise AgentReviewError('Agent Review returned an unsuccessful response; remote outcome requires verification.', code='API_ERROR', http_status=status)
        return result

    def create_case(self, *, name, operation_id):
        self._operation(operation_id)
        if (not _text(name) or len(name) > 100
                or any(ord(c) < 32 for c in name)):
            _invalid('Case name must contain 1 to 100 characters without control characters.')
        result = self._request('POST', 'create-case', {'name': name, 'operationId': operation_id})
        case = result.get('case')
        if (result.get('ok') is not True or not isinstance(case, dict)
                or not all(_text(case.get(k)) for k in ('id', 'projectId', 'createdAt'))
                or case.get('name') != name.strip()):
            _receipt_error()
        return result

    def publish(self, *, case_id, name, text, operation_id, stage='script', purpose=None):
        self._target(case_id, stage)
        self._operation(operation_id)
        if (not _text(name) or len(name) > 200 or not re.search(r'\.(?:md|txt)$', name, re.I)
                or re.search(r'[\x00-\x1f/\\]', name)):
            _invalid('Publication name must be a .md or .txt filename without path separators.')
        try:
            text_valid = _text(text) and '\0' not in text and len(text.encode('utf-8')) <= MAX_TEXT_BYTES
        except UnicodeError:
            text_valid = False
        if not text_valid:
            _invalid('Publication text must be nonempty valid UTF-8 without NUL, at most 100KB.')
        if purpose is not None and (not _text(purpose) or len(purpose) > 500 or any(ord(c) < 32 for c in purpose)):
            _invalid('Publication purpose must contain 1 to 500 characters without control characters.')
        payload = {'caseId': case_id, 'stage': stage, 'name': name, 'text': text, 'operationId': operation_id}
        if purpose is not None:
            payload['purpose'] = purpose
        result = self._request('POST', 'publish', payload)
        return self._publication(result, case_id, stage, operation_id)

    def publish_asset(self, *, case_id, name, image, purpose, operation_id,
                      artifact_id, ast_id, output_id, references=()):
        self._target(case_id, 'assets', ('assets',))
        self._operation(operation_id)
        if (not _text(name) or len(name) > 200 or re.search(r'[\x00-\x1f/\\]', name)
                or Path(name).suffix.lower() not in IMAGE_TYPES):
            _invalid('Asset must use a PNG, JPEG or WebP filename without path separators.')
        if not isinstance(image, bytes) or not 0 < len(image) <= MAX_IMAGE_BYTES:
            _invalid('Asset image must contain at most 8 MiB.')
        if (not _text(purpose) or len(purpose) > 500 or any(ord(c) < 32 for c in purpose)
                or not all(_text(item) for item in (artifact_id, ast_id, output_id))
                or not isinstance(references, (list, tuple)) or len(references) > 50
                or not all(_text(ref) for ref in references) or len(set(references)) != len(references)):
            _invalid('Asset purpose, identities and references must be explicit.')
        payload = {'caseId': case_id, 'name': name, 'purpose': purpose,
                   'operationId': operation_id, 'stage': 'assets', 'mime': IMAGE_TYPES[Path(name).suffix.lower()],
                   'dataBase64': base64.b64encode(image).decode('ascii'),
                   'artifactId': artifact_id, 'astId': ast_id, 'outputId': output_id,
                   'references': list(references)}
        result = self._request('POST', 'publish-assets', payload)
        self._publication(result, case_id, 'assets', operation_id)
        if result.get('sha256') != hashlib.sha256(image).hexdigest():
            _receipt_error()
        return result

    def _publication(self, result, case_id, stage, operation_id):
        if (result.get('ok') is not True or result.get('caseId') != case_id
                or result.get('stage') != stage or result.get('scope') != 'live'
                or result.get('operationId') != operation_id
                or not all(_text(result.get(k)) for k in ('projectId', 'assetId', 'url'))
                or not _version(result.get('version')) or not _integer(result.get('headGeneration'), 1)):
            _receipt_error()
        # The server currently returns a relative URL. Accept an absolute URL
        # only on this same origin, and check identity fields when present.
        try:
            link = urlsplit(result['url'])
        except ValueError:
            _receipt_error()
        if (link.username is not None or link.password is not None
                or (link.netloc and (link.scheme + '://' + link.netloc) != self.base_url)
                or (link.scheme and link.scheme != 'https')
                or (not link.netloc and not result['url'].startswith('/'))
                or result['url'].startswith('//')
                or any(ord(c) < 32 for c in result['url'])):
            _receipt_error()
        query = parse_qs(link.query, keep_blank_values=True)
        if any(key in query and query[key] != [str(result[key])]
               for key in ('projectId', 'scope', 'caseId', 'stage', 'version')):
            _receipt_error()
        return result

    def status(self, *, case_id, stage='script'):
        self._target(case_id, stage, (*TEXT_STAGES, 'assets'))
        result = self._request('GET', 'status', query={'caseId': case_id, 'stage': stage})
        case, head, expected = result.get('case'), result.get('head'), result.get('expectations')
        if (not isinstance(case, dict) or case.get('id') != case_id
                or not all(_text(case.get(k)) for k in ('name', 'projectId', 'status'))
                or not _integer(case.get('revision')) or result.get('stage') != stage
                or not isinstance(expected, dict) or not _integer(expected.get('expectedReviewRevision'))
                or not {'expectedHeadVersion', 'expectedHeadGeneration', 'expectedReviewRevision'} <= expected.keys()
                or 'head' not in result or 'sourceScriptVersion' not in result):
            _receipt_error()
        if head is None:
            version, generation = None, 0
        elif isinstance(head, dict) and _version(head.get('version')) and _integer(head.get('generation'), 1):
            version, generation = head['version'], head['generation']
        else:
            _receipt_error()
        source = result['sourceScriptVersion']
        if (expected.get('expectedHeadVersion') != version
                or not _integer(expected.get('expectedHeadGeneration'))
                or expected['expectedHeadGeneration'] != generation
                or (source is not None and not _version(source))
                or (stage == 'script' and source != version)):
            _receipt_error()
        return result

    def decision(self, *, case_id, stage, version):
        self._target(case_id, stage, ('script', 'assets'))
        if not _version(version):
            _invalid('Decision reads require an exact published version.')
        result = self._request('GET', 'decision', query={'caseId': case_id, 'stage': stage, 'version': version})
        case, validity = result.get('case'), result.get('validity')
        status = result.get('decisionStatus')
        flags = ('isCurrent', 'sourceStale', 'dependencyStale', 'dependencyUnapproved', 'reviewable', 'stillEffective')
        if (not isinstance(case, dict) or case.get('id') != case_id or not _text(case.get('projectId'))
                or result.get('stage') != stage or result.get('version') != version
                or status not in ('pending', 'changes', 'approved')
                or not isinstance(validity, dict) or any(type(validity.get(flag)) is not bool for flag in flags)):
            _receipt_error()
        approval, event = result.get('approval'), result.get('latestEvent')
        if status == 'approved':
            scope = dict(projectId=case['projectId'], scope='live', caseId=case_id, stage=stage, version=version)
            if (not isinstance(approval, dict) or approval.get('scope') != scope
                    or not _text(approval.get('actorId')) or not _text(approval.get('decidedAt'))
                    or not isinstance(event, dict) or event.get('decision') != 'approve'
                    or event.get('actorId') != approval['actorId'] or event.get('createdAt') != approval['decidedAt']):
                _receipt_error()
        elif approval is not None:
            _receipt_error()
        return result


def effective_approval(decision):
    validity = decision['validity']
    return (decision['decisionStatus'] == 'approved' and validity['isCurrent']
            and validity['stillEffective'] and validity['reviewable']
            and not any(validity[key] for key in ('sourceStale', 'dependencyStale', 'dependencyUnapproved')))
