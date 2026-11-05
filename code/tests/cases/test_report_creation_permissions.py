"""Report creation and subsequent reads share creator placement under real PAR.

Only source message contents and HTTP transport are fixtures; resource
registration, report snapshots, registry transactions and permission decisions
use their real owners against isolated PostgreSQL.
"""
from types import SimpleNamespace
import json
import hashlib

import pytest

from management_portal import permission_system, home_report_http
from management_portal.hatg.catalog import declaration
from management_portal.hatg.contracts import InstructionContext, InstructionError
from management_portal.home_report_store import ReportStore
from management_portal.permission_system.facts import Fact, fact_from_record, personal_space
from management_portal.permission_system.requests import requests
from tests.cases.permission_test_support import enable
from tests.cases.test_workspace_resource_rename import workspace, WID

AUTHOR = 'resident_123456789abc'
OTHER = 'resident_abcdef123456'
TARGET = {'kind': 'task', 'id': AUTHOR + '/msg_123456789abc'}
KEY = 'home-request/v1:12345678-1234-1234-1234-123456789abc'


@pytest.fixture
def reporting(workspace, monkeypatch, tmp_path):
    workspace.service._index().project_registered = lambda *args: None
    permissions = enable(monkeypatch)
    permissions.users('alice', 'bob')
    permissions.place(Fact('project', 'ReportTeam'),
        Fact('lineage', 'lineage_123456789abc', {'project': 'ReportTeam'}),
        Fact('resident', AUTHOR, {'lineage': 'lineage_123456789abc'}), Fact('resident', OTHER))
    permissions.grant('alice', AUTHOR, '@viewer')
    permissions.place(Fact('workspace', WID))
    permissions.grant(AUTHOR, WID, '@viewer')
    permissions.grant('alice', WID, '@viewer')
    page = b'<html><body>Authored package fixture</body></html>'
    (tmp_path/'page.html').write_bytes(page)
    package = {'schema': 'portal-explanation/v1', 'quality': 'medium', 'title': 'Authored',
        'interaction': {'address': WID+'/file/page.html', 'sha256': hashlib.sha256(page).hexdigest()}}
    data = json.dumps(package).encode()
    (tmp_path/'manifest.json').write_bytes(data)
    ref = {'address': WID+'/file/manifest.json', 'sha256': hashlib.sha256(data).hexdigest()}
    current = {'actor': AUTHOR}

    def read(name, args):
        permissions.implementation.authorize_instruction(declaration(name), args,
            InstructionContext(caller=current['actor'], entry='web'))
        if name == 'GetMessage':
            return {'message': {'content_parts': [{'type': 'text', 'text': 'Actual fixture task'}]}}
        if name == 'GetInputState':
            return {'input_state': {'state': 'completed'}}
        raise AssertionError(name)

    store = ReportStore(root=tmp_path / 'reports', read=read)
    from management_portal.resources.providers import report as provider
    from management_portal import resources
    monkeypatch.setattr(provider, 'ReportStore', lambda: store)
    monkeypatch.setattr(home_report_http, 'ReportStore', lambda: store)
    monkeypatch.setattr(resources, 'get_resource_service', lambda: workspace.service)
    monkeypatch.setattr(home_report_http.http_responses, '_serve_json', lambda request, status, body: (status, body))
    def raise_error(request, exc):
        raise exc
    monkeypatch.setattr(home_report_http.http_responses, '_serve_exception_json', raise_error)
    return SimpleNamespace(permissions=permissions, store=store, current=current, workspace=workspace, ref=ref, package=package)


@pytest.mark.parametrize('actor', [AUTHOR, 'alice'])
def test_http_and_isa_creation_use_authenticated_creator_and_keep_source_checks(reporting, monkeypatch, actor):
    p, store = reporting.permissions, reporting.store
    reporting.current['actor'] = actor
    fields = {'explanation_target': reporting.ref}
    args = {'resource_type': 'report', 'fields': fields}
    parent = AUTHOR if actor == AUTHOR else personal_space(actor)
    assert not p.holds(actor, '@contributor', '@shared')
    assert permission_system.allows(actor, 'RegisterResource', args)
    asked = requests(p.scheme, 'RegisterResource', args, caller=actor)
    assert [r.resource for r in asked] == [parent]
    checked = p.implementation.manage('CheckPermission', {'principal_id': actor,
        'instruction': 'RegisterResource', 'arguments': args}, actor=actor, enforce=False)
    assert checked['allowed']

    monkeypatch.setattr(home_report_http.http_request_body, '_read_bounded_json_object', lambda *a, **kw: reporting.ref)
    status, report = home_report_http._explanations(SimpleNamespace(command='POST'), None, actor)
    assert status == 200, report
    identifier = report['report_id']
    assert store.record(identifier)['created_by'] == actor
    assert fact_from_record(store.metadata(store.record(identifier)), 'report').owners == (
        {'resident': AUTHOR} if actor == AUTHOR else {'personal_space': parent})
    assert permission_system.allows(actor, 'UpdateResource', {'resource_id': identifier})
    assert not permission_system.allows(OTHER, 'UpdateResource', {'resource_id': identifier})
    assert home_report_http._explanations(SimpleNamespace(command='POST'), None, actor)[1]['report_id'] == identifier

    data = json.dumps({**reporting.package, 'title': 'Second authored package'}).encode()
    (reporting.workspace.root/'second.json').write_bytes(data)
    second_ref = {'address': WID+'/file/second.json', 'sha256': hashlib.sha256(data).hexdigest()}
    second = reporting.workspace.call('RegisterResource', {'resource_type': 'report',
        'fields': {'explanation_target': second_ref}}, actor=actor).raise_for_error()
    assert second['resource_id'] != identifier
    p.deny(actor, AUTHOR, '@viewer')
    p.deny(actor, WID, '@viewer')  # Authored source bytes remain protected.
    with pytest.raises((InstructionError, PermissionError)):
        store.get(identifier, actor)
    denied = reporting.workspace.call('RegisterResource', args, actor=actor)
    assert denied.status == 'failed'


def test_payload_cannot_choose_creator_placement_and_deny_is_enforced(reporting):
    p = reporting.permissions
    forged = {'resource_type': 'report', 'fields': {'explanation_target': reporting.ref,
        'created_by': OTHER, 'resident_id': OTHER, 'personal_space': 'personal_space:bob'},
        '$caller': OTHER}
    assert [r.resource for r in requests(p.scheme, 'RegisterResource', forged, caller=AUTHOR)] == [AUTHOR]
    invalid = reporting.workspace.call('RegisterResource', {
        'resource_type': 'report', 'fields': forged['fields']}, actor=AUTHOR)
    assert invalid.status == 'failed'
    p.deny(AUTHOR, AUTHOR, '@contributor')
    assert not permission_system.allows(AUTHOR, 'RegisterResource', {'resource_type': 'report'})
    assert reporting.workspace.call('RegisterResource', {'resource_type': 'report',
        'fields': {'explanation_target': reporting.ref}}, actor=AUTHOR).status == 'failed'
    # Other registerable types retain their original placement.
    assert not permission_system.allows(AUTHOR, 'RegisterResource', {'resource_type': 'device'})
    assert [r.resource for r in requests(p.scheme, 'RegisterResource',
        {'resource_type': 'article', 'fields': {}}, caller=AUTHOR)] == ['@public']


def test_existing_report_facts_use_creator_not_forged_relation_fields(reporting):
    for actor in (AUTHOR, 'alice'):
        row = {'resource_id': 'report_123456789abc', 'created_by': actor,
               'resident_id': OTHER, 'personal_space_id': 'personal_space:bob'}
        expected = {'resident': AUTHOR} if actor == AUTHOR else {'personal_space': personal_space(actor)}
        assert fact_from_record(row, 'report').owners == expected
        assert fact_from_record({'resource_id': row['resource_id'], 'attributes': row}, 'report').owners == expected
