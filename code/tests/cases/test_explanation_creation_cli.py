"""The author CLI checks current authenticated policy before touching a package."""
import io
import json
import sys
from urllib.error import HTTPError, URLError

import pytest

from scripts import build_explanation_package as builder
from scripts import register_explanation_report as client

ACTOR = 'resident_0123456789ab'
LINEAGE = 'lineage_0123456789ab'


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(client.isa, 'read_product_env', lambda key: ACTOR if key == 'PORTAL_RESIDENT_ID' else '')
    monkeypatch.setattr(client.isa, '_token', lambda actor: 'fixture-token')
    def execute(actor, instruction, **kwargs):
        assert actor == ACTOR
        assert instruction['instruction'] == 'GetResident'
        assert instruction['arguments'] == {'resident_id': ACTOR}
        return {'result': {'resident': {'resident_id': ACTOR, 'lineage_id': LINEAGE}}}
    monkeypatch.setattr(client.isa, 'execute', execute)
    calls = []
    def install(value):
        def send(request, **kwargs):
            calls.append(request)
            if isinstance(value, Exception):
                raise value
            return io.BytesIO(json.dumps(value).encode())
        monkeypatch.setattr(client, 'urlopen', send)
    return calls, install


def test_creation_check_uses_authenticated_identity_and_empty_body(transport):
    calls, install = transport
    install({'allowed': True})
    builder.require_creation(ACTOR)
    request, = calls
    assert request.full_url == client.isa.DEFAULT_PORTAL_URL + '/api/home-report/creation-check'
    assert request.get_method() == 'POST'
    assert json.loads(request.data) == {}
    assert request.get_header('Authorization') == 'Bearer fixture-token'
    assert request.get_header('X-portal-lineage-id') == LINEAGE


@pytest.mark.parametrize('quality', ['low', 'medium', 'high'])
@pytest.mark.parametrize('response', [
    {'allowed': False}, {}, {'allowed': 'true'}, None,
    HTTPError('http://fixture/creation-check', 403, 'Disabled', {}, None),
    URLError('bridge unavailable'),
])
def test_denied_or_unknown_creation_never_builds_writes_or_submits(tmp_path, monkeypatch, transport, quality, response):
    calls, install = transport
    install(response)
    tmp_path = tmp_path / 'package'
    tmp_path.mkdir()
    original = b'existing fixed package'
    (tmp_path / 'existing.json').write_bytes(original)
    def forbidden(*args, **kwargs):
        pytest.fail('creation continued after a failed policy check')
    monkeypatch.setattr(builder, 'build', forbidden)
    monkeypatch.setattr(builder, 'register_report', forbidden)
    monkeypatch.setattr(sys, 'argv', ['build_explanation_package.py', str(tmp_path),
        '--address', 'workspace_0123456789ab/file/lesson', '--title', 'Fixture',
        '--quality', quality, '--task', 'Understand', '--resident-id', ACTOR])
    with pytest.raises(SystemExit) as stopped:
        builder.main()
    assert stopped.value.code == 1
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == [tmp_path / 'existing.json']
    assert (tmp_path / 'existing.json').read_bytes() == original


def test_injected_identity_cannot_be_overridden(transport):
    calls, install = transport
    install({'allowed': True})
    with pytest.raises(RuntimeError):
        builder.require_creation('resident_abcdefabcdef')
    assert calls == []


@pytest.mark.parametrize('quality', ['low', 'medium'])
def test_allowed_creation_checks_before_build_and_does_not_request_review(tmp_path, monkeypatch, transport, quality):
    calls, install = transport
    install({'allowed': True})
    (tmp_path / 'body.md').write_text('Fixture body')
    original_build = builder.build
    def build(*args, **kwargs):
        assert len(calls) == 1 and calls[0].full_url.endswith('/creation-check')
        return original_build(*args, **kwargs)
    monkeypatch.setattr(builder, 'build', build)
    from scripts import submit_explanation_review as review
    monkeypatch.setattr(review, 'submit', lambda *args, **kwargs: pytest.fail('lower quality must not request independent review'))
    monkeypatch.setattr(builder, 'register_report', lambda *args: {'reference': {
        'view': 'report', 'target': {'kind': 'report', 'id': 'report_0123456789ab', 'revision': 1}}})
    monkeypatch.setattr(sys, 'argv', ['build_explanation_package.py', str(tmp_path),
        '--address', 'workspace_0123456789ab/file/lesson', '--title', 'Fixture',
        '--quality', quality, '--task', 'Understand', '--resident-id', ACTOR, '--supplement', 'body.md'])
    builder.main()
    assert json.loads((tmp_path / 'manifest.json').read_text())['quality'] == quality


def test_existing_package_registration_does_not_require_creation_check(transport):
    calls, install = transport
    install({'reference': {'view': 'report', 'target': {
        'kind': 'report', 'id': 'report_0123456789ab', 'revision': 1}}})
    client.register('workspace_0123456789ab/file/lesson/manifest.json', 'a' * 64, ACTOR)
    assert [request.full_url.rsplit('/', 1)[-1] for request in calls] == ['explanations']
