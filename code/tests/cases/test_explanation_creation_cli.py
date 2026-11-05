"""Authoring uses existing authenticated services, without a settings admission API."""
import hashlib
import io
import json
import sys
from urllib.error import HTTPError

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


REFERENCE = {'view': 'report', 'target': {
    'kind': 'report', 'id': 'report_0123456789ab', 'revision': 1}}
ADDRESS = 'workspace_0123456789ab/file/lesson'


def build_args(tmp_path, monkeypatch, quality):
    (tmp_path / 'body.md').write_text('Fixture body')
    monkeypatch.setattr(sys, 'argv', ['build_explanation_package.py', str(tmp_path),
        '--address', ADDRESS, '--title', 'Fixture', '--quality', quality,
        '--task', 'Understand', '--resident-id', ACTOR, '--supplement', 'body.md'])


@pytest.mark.parametrize('quality', ['low', 'medium', 'high'])
def test_build_uses_existing_registration_and_requested_quality(tmp_path, monkeypatch, transport, capsys, quality):
    calls, install = transport
    install({'reference': REFERENCE})
    from scripts import submit_explanation_review as review
    reviews = []
    def submit(address, digest, task, actor, **kwargs):
        assert quality == 'high', 'lower quality must not request independent review'
        assert calls == [], 'review precedes report registration'
        assert actor == ACTOR and task == 'Understand'
        assert address == ADDRESS + '/manifest.json'
        assert digest == hashlib.sha256((tmp_path / 'manifest.json').read_bytes()).hexdigest()
        reviews.append((address, digest))
        return {'status': 'submitted'}
    monkeypatch.setattr(review, 'submit', submit)
    build_args(tmp_path, monkeypatch, quality)
    builder.main()
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    assert manifest['quality'] == quality
    assert manifest['supplement']['sha256'] == hashlib.sha256((tmp_path / 'body.md').read_bytes()).hexdigest()
    request, = calls
    assert request.full_url == client.isa.DEFAULT_PORTAL_URL + '/api/home-report/explanations'
    assert request.get_method() == 'POST'
    assert request.get_header('Authorization') == 'Bearer fixture-token'
    assert request.get_header('X-portal-lineage-id') == LINEAGE
    assert json.loads(request.data) == {'address': ADDRESS + '/manifest.json',
        'sha256': hashlib.sha256((tmp_path / 'manifest.json').read_bytes()).hexdigest()}
    output = json.loads(capsys.readouterr().out)
    assert output['target'] == REFERENCE['target']
    assert output['review']['status'] == ('submitted' if quality == 'high' else 'not_required')
    assert len(reviews) == (1 if quality == 'high' else 0)


def test_injected_identity_cannot_be_overridden(transport):
    calls, install = transport
    install({'reference': REFERENCE})
    with pytest.raises(RuntimeError):
        client.register(ADDRESS + '/manifest.json', 'a' * 64, 'resident_abcdefabcdef')
    assert calls == []


def test_source_permission_denial_retains_fixed_package_without_claiming_registration(tmp_path, monkeypatch, transport, capsys):
    calls, install = transport
    install(HTTPError('http://fixture/explanations', 403, 'Source denied', {}, None))
    build_args(tmp_path, monkeypatch, 'medium')
    with pytest.raises(SystemExit) as stopped:
        builder.main()
    assert stopped.value.code == 1
    output = capsys.readouterr()
    assert output.out == ''
    assert 'report registration pending' in output.err and '403' in output.err
    fixed = (tmp_path / 'manifest.json').read_bytes()
    assert json.loads(fixed)['quality'] == 'medium'
    assert [request.full_url.rsplit('/', 1)[-1] for request in calls] == ['explanations']
    with pytest.raises(FileExistsError):
        builder.main()
    assert (tmp_path / 'manifest.json').read_bytes() == fixed
    assert len(calls) == 1


def test_existing_package_registration_uses_existing_endpoint(transport):
    calls, install = transport
    install({'reference': REFERENCE})
    assert client.register(ADDRESS + '/manifest.json', 'a' * 64, ACTOR)['reference'] == REFERENCE
    assert [request.full_url.rsplit('/', 1)[-1] for request in calls] == ['explanations']
