"""Fixed explanation registration uses real Report/Resource owners and isolated PG."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlencode, urlparse

import pytest

from management_portal import home_report_http, permission_system, resources
from management_portal.home_report_explanations import ExplanationReports, fields
from management_portal.home_report_store import ReportStore, COLLECTION, ORIGINS, EXPLANATIONS
from management_portal.permission_system.facts import Fact, personal_space
from management_portal.resources.errors import ResourceVersionConflict, ResourceOperationError
from tests.cases.permission_test_support import enable
from tests.cases.test_workspace_resource_rename import workspace, WID
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401
from tests.cases.test_family_familize import charters  # noqa: F401
from tests.cases.test_personal_assistant import personal  # noqa: F401


@pytest.fixture
def explanations(workspace, monkeypatch, tmp_path):
    workspace.service._index().project_registered = lambda *args, **kw: None
    permissions = enable(monkeypatch)
    permissions.users('alice', 'bob')
    permissions.place(Fact('workspace', WID))
    for actor in ('alice', 'bob'):
        permissions.grant(actor, WID, '@viewer')
    store = ReportStore(root=tmp_path / 'reports')
    from management_portal.resources.providers import report as provider
    monkeypatch.setattr(provider, 'ReportStore', lambda: store)
    monkeypatch.setattr(home_report_http, 'ReportStore', lambda: store)
    monkeypatch.setattr(resources, 'get_resource_service', lambda: workspace.service)
    monkeypatch.setattr(home_report_http.http_responses, '_serve_json', lambda request, status, body: (status, body))

    def put(name, value):
        data = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, dict) else value
        (tmp_path / name).write_bytes(data)
        return {'address': WID + '/file/' + name, 'sha256': hashlib.sha256(data).hexdigest()}

    interaction = put('page.html', b'<html><body>Fixed page</body></html>')
    manifest = {'schema': 'portal-explanation/v1', 'title': 'Fixed explanation', 'summary': 'Fixed summary',
                'quality': 'high', 'revision': 7, 'interaction': interaction,
                'authoring_proof': {'id': 'existing-proof'}, 'result_target': interaction}
    ref = put('manifest.json', manifest)
    return SimpleNamespace(store=store, permissions=permissions, package=manifest, ref=ref,
                           put=put, workspace=workspace, api=ExplanationReports(store))


def test_http_registration_read_lookup_and_original_package_are_fixed(explanations, monkeypatch):
    e = explanations
    original = (e.workspace.root / 'manifest.json').read_bytes()
    get = SimpleNamespace(command='GET')
    parsed = urlparse('/api/home-report/explanations?' + urlencode(e.ref))
    status, _ = home_report_http._explanations(get, parsed, 'alice')
    assert status == 404
    assert not e.store.root.exists()
    monkeypatch.setattr(home_report_http.http_request_body, '_read_bounded_json_object', lambda *a, **kw: e.ref)
    status, first = home_report_http._explanations(SimpleNamespace(command='POST'), parsed, 'alice')
    assert status == 200, first
    assert first['content_kind'] == 'explanation'
    assert first['revision'] == 1 and first['explanation_target'] == e.ref
    assert first['explanation_manifest'] == e.package
    assert 'media' not in first and 'review' not in first
    identifier = first['report_id']
    assert first['reference']['target'] == {'kind': 'report', 'id': identifier, 'revision': 1}
    assert e.store.record(identifier)['created_by'] == 'alice'
    assert sorted(p.name for p in (e.store.directory(identifier) / '1').iterdir()) == ['report.json']
    for _ in range(2):
        assert home_report_http._explanations(get, parsed, 'alice')[1] == first
    detail = home_report_http._get(get, urlparse('/api/home-report?kind=report&id=' + identifier + '&revision=1'), 'alice')
    assert detail == (200, first)
    data, _, _ = e.workspace.service.read_content(first['reference']['address'], actor='alice')
    assert json.loads(data)['explanation_target'] == e.ref
    assert e.api.register(e.ref, 'alice')['report_id'] == identifier
    assert (e.workspace.root / 'manifest.json').read_bytes() == original
    assert not (e.workspace.root / 'explanation-media').exists()


def test_concurrent_post_is_one_report_and_actor_bindings_are_isolated(explanations):
    e = explanations
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: e.api.register(e.ref, 'alice'), range(2)))
    assert values[0]['report_id'] == values[1]['report_id']
    assert len(e.store.registry.documents(COLLECTION)) == 1
    with pytest.raises(FileNotFoundError):
        e.api.lookup(e.ref, 'bob')
    own = e.api.register(e.ref, 'bob')
    assert own['report_id'] != values[0]['report_id']
    assert e.store.record(own['report_id'])['created_by'] == 'bob'
    assert len(e.store.registry.documents(ORIGINS)) == 2


def test_readable_binding_reused_only_post_records_own_origin(explanations):
    e = explanations
    first = e.api.register(e.ref, 'alice')
    identifier = first['report_id']
    e.permissions.grant('bob', identifier, '@viewer')
    origin = e.store.origin_id('bob', fields(e.ref)['origin_key'])
    assert e.api.lookup(e.ref, 'bob')['report_id'] == identifier
    assert e.store.registry.get(ORIGINS, origin) is None
    assert e.api.register(e.ref, 'bob')['report_id'] == identifier
    assert e.store.registry.get(ORIGINS, origin)['report_id'] == identifier
    e.permissions.deny('bob', identifier)
    with pytest.raises(PermissionError):
        e.api.lookup(e.ref, 'bob')
    with pytest.raises(PermissionError):
        e.api.register(e.ref, 'bob')
    e.workspace.call('UnregisterResource', {'resource_id': identifier}).raise_for_error()
    for actor in ('alice', 'bob'):
        with pytest.raises(ResourceOperationError) as error:
            e.api.lookup(e.ref, actor)
        assert error.value.http_status == 410


def test_other_actor_deletion_cannot_deny_or_disclose_binding(explanations):
    e = explanations
    first = e.api.register(e.ref, 'alice')
    e.workspace.call('UnregisterResource', {'resource_id': first['report_id']}).raise_for_error()
    with pytest.raises(FileNotFoundError) as error:
        e.api.lookup(e.ref, 'bob')
    assert first['report_id'] not in str(error.value)
    assert e.store.registry.get(ORIGINS, e.store.origin_id('bob', fields(e.ref)['origin_key'])) is None
    second = e.api.register(e.ref, 'bob')
    assert second['report_id'] != first['report_id']
    assert e.store.registry.get(COLLECTION, first['report_id']) is None
    with pytest.raises(ResourceOperationError) as error:
        e.api.register(e.ref, 'alice')
    assert error.value.http_status == 410


def test_http_and_formal_creation_share_package_lock_and_atomic_index(explanations, monkeypatch):
    from threading import Barrier
    e = explanations
    # Force two different registration receipts into provider.create together.
    original = e.store.create
    arrived = Barrier(2)
    def create(*args, **kwargs):
        arrived.wait(timeout=10)
        return original(*args, **kwargs)
    monkeypatch.setattr(e.store, 'create', create)
    def formal():
        try:
            return e.workspace.service.register('report', {'explanation_target': e.ref},
                                                actor='alice', operation_id='formal-competing')
        except FileExistsError:
            return None  # Explicit conflict is the formal entry's contract.
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(e.api.register, e.ref, 'alice')
        b = pool.submit(formal)
        web, registered = a.result(timeout=20), b.result(timeout=20)
    if registered:
        assert registered['resource_id'] == web['report_id']
    index = e.store.registry.get(EXPLANATIONS, fields(e.ref)['origin_key'])
    assert index['bindings'] == [{'report_id': web['report_id'], 'revision': 1}]
    assert len(e.store.registry.documents(COLLECTION)) == 1
    monkeypatch.setattr(e.store, 'create', original)
    with pytest.raises(FileExistsError):
        e.workspace.service.register('report', {'explanation_target': e.ref},
                                     actor='alice', operation_id='another-formal')


@pytest.mark.parametrize('concurrent', [False, True])
def test_real_personal_ama_and_owner_reuse_same_report(personal, workspace, monkeypatch, tmp_path, concurrent):
    from pathlib import Path
    from management_portal.permission_system import personal_ama
    from management_portal.society_service import resident_catalog, lineage_reads, resource_client
    from management_portal.resources.providers import report as provider
    ama, _assistant = personal
    resident_id = ama['resident_id']
    record = resident_catalog.get_catalog().get(resident_id)
    graph = lineage_reads.get_lineage(ama['lineage_id'], 'newcomer')
    assert record['personal_owner_id'] == 'newcomer'
    assert graph['lineage_membership'] == 'personal'
    # The Society fixture isolates its resource port. Project its actual
    # registration, with no invented personal_owner metadata or grant shortcut.
    descriptor = resource_client._resident_resource_projection(record)['descriptor']
    monkeypatch.setattr(personal_ama, '_DESCRIPTORS', SimpleNamespace(get=lambda rid: descriptor if rid == resident_id else None))
    monkeypatch.setattr(personal_ama, '_OWNERS', {})
    permissions = enable(monkeypatch)
    permissions.users('newcomer', 'outsider')
    for kind, item in [('lineage', resource_client._lineage_resource_projection(graph)['descriptor']),
                       ('resident', descriptor)]:
        permission_system.sync_resource(item, resource_type=kind)
    assert personal_ama.owner_of(resident_id) == 'newcomer'
    store = ReportStore(root=tmp_path / 'reports')
    monkeypatch.setattr(provider, 'ReportStore', lambda: store)
    monkeypatch.setattr(resources, 'get_resource_service', lambda: workspace.service)
    workspace.service._index().project_registered = lambda *a, **kw: None
    root = Path(record['owned_workspace']['path'])
    data = json.dumps({'schema': 'portal-explanation/v1', 'title': 'Private fixed package',
                       'quality': 'medium'}).encode()
    (root / 'manifest.json').write_bytes(data)
    ref = {'address': record['owned_workspace_id'] + '/file/manifest.json',
           'sha256': hashlib.sha256(data).hexdigest()}
    api = ExplanationReports(store)
    if concurrent:
        with ThreadPoolExecutor(max_workers=2) as pool:
            firsts = list(pool.map(lambda actor: api.register(ref, actor), [resident_id, 'newcomer']))
        assert firsts[0]['report_id'] == firsts[1]['report_id']
        first = firsts[0]
    else:
        first = api.register(ref, resident_id)
        assert store.record(first['report_id'])['created_by'] == resident_id
    # GET is read-only even when the owner can read their Resident's Report.
    owner_origin = store.origin_id('newcomer', fields(ref)['origin_key'])
    assert api.lookup(ref, 'newcomer')['report_id'] == first['report_id']
    if not concurrent:
        assert store.registry.get(ORIGINS, owner_origin) is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda actor: api.register(ref, actor), [resident_id, 'newcomer']))
    assert {item['report_id'] for item in values} == {first['report_id']}
    assert len(store.registry.documents(COLLECTION)) == 1
    with pytest.raises(FileNotFoundError):
        api.lookup(ref, 'outsider')
    with pytest.raises(PermissionError):
        api.register(ref, 'outsider')
    permissions.deny('newcomer', record['owned_workspace_id'])
    for actor in ('newcomer', resident_id):
        with pytest.raises(PermissionError):
            api.lookup(ref, actor)
    workspace.call('UnregisterResource', {'resource_id': first['report_id']}, actor='newcomer').raise_for_error()
    for actor in ('newcomer', resident_id):
        with pytest.raises(ResourceOperationError) as error:
            api.register(ref, actor)
        assert error.value.http_status == 410
    assert (root / 'manifest.json').read_bytes() == data


def test_read_and_post_recheck_current_report_and_source_permissions(explanations):
    e = explanations
    result = e.api.register(e.ref, 'alice')
    e.permissions.deny('alice', WID)
    for read in (lambda: e.api.lookup(e.ref, 'alice'), lambda: e.api.register(e.ref, 'alice'),
                 lambda: e.store.file(result['report_id'], 1, 'report.json', 'alice')):
        with pytest.raises(PermissionError):
            read()
    with pytest.raises(PermissionError):
        e.store.response(result['report_id'], 'bob', 1)
    # Creation privileges cannot be borrowed from the package or body.
    e.permissions.deny('bob', personal_space('bob'), '@contributor')
    with pytest.raises(PermissionError):
        e.api.register(e.ref, 'bob')
    assert len(e.store.registry.documents(COLLECTION)) == 1


@pytest.mark.parametrize('changed', ['manifest.json', 'page.html'])
def test_digest_conflict_does_not_rebind_or_rewrite(explanations, changed):
    e = explanations
    result = e.api.register(e.ref, 'alice')
    report_bytes = (e.store.directory(result['report_id']) / '1/report.json').read_bytes()
    (e.workspace.root / changed).write_bytes(b'changed')
    for read in (lambda: e.api.lookup(e.ref, 'alice'), lambda: e.api.register(e.ref, 'alice')):
        with pytest.raises(ResourceVersionConflict):
            read()
    assert (e.store.directory(result['report_id']) / '1/report.json').read_bytes() == report_bytes
    assert len(e.store.registry.documents(COLLECTION)) == 1


def test_dependency_acl_and_initial_invalid_package_prevent_registration(explanations):
    e = explanations
    denied = 'workspace_abcdef123456'
    source = e.workspace.root / 'restricted'
    source.mkdir()
    (source / 'page.html').write_text('Private provenance')
    e.workspace.store.ensure(denied, str(source), title='Restricted source')
    e.permissions.place(Fact('workspace', denied))
    e.permissions.deny('alice', denied)
    assert not e.permissions.holds('alice', '@viewer', denied)
    ref = e.put('private-source.json', {**e.package, 'sources': [{'address': denied + '/file/page.html'}]})
    with pytest.raises(PermissionError):
        e.api.register(ref, 'alice')
    with pytest.raises(ResourceVersionConflict):
        e.api.register({**e.ref, 'sha256': '0' * 64}, 'alice')
    for value in ({**e.ref, 'created_by': 'bob'}, {**e.ref, 'sha256': 'bad'}, {'address': '/tmp/manifest', 'sha256': '0'*64}):
        with pytest.raises(ValueError):
            e.api.register(value, 'alice')
    assert not e.store.registry.documents(COLLECTION)


def test_formal_register_and_delete_keep_receipt_and_never_delete_package(explanations):
    e = explanations
    args = {'resource_type': 'report', 'fields': {'explanation_target': e.ref}}
    result = e.workspace.call('RegisterResource', args, idempotency_key='fixed-explanation').raise_for_error()
    identifier = result['resource_id']
    assert e.api.lookup(e.ref, 'alice')['report_id'] == identifier
    assert e.workspace.call('RegisterResource', args, idempotency_key='fixed-explanation').raise_for_error()['resource_id'] == identifier
    e.workspace.call('UnregisterResource', {'resource_id': identifier}).raise_for_error()
    assert e.store.registry.get(COLLECTION, identifier) is None
    assert (e.workspace.root / 'manifest.json').exists()
    assert (e.workspace.root / 'page.html').exists()
    for action in (lambda: e.api.lookup(e.ref, 'alice'), lambda: e.api.register(e.ref, 'alice')):
        with pytest.raises(ResourceOperationError) as error:
            action()
        assert error.value.http_status == 410
    with pytest.raises(FileNotFoundError):
        e.store.get(identifier, 'alice')


def test_formal_metadata_does_not_disclose_revoked_sources_and_delete_remains_available(explanations):
    e = explanations
    result = e.api.register(e.ref, 'alice')
    identifier = result['report_id']
    e.permissions.deny('alice', WID)
    with pytest.raises(PermissionError):
        e.workspace.service.resolve(identifier, actor='alice')
    # Discovery deliberately exposes only a generic identity, never the fixed
    # source's title/summary, even before the source was revoked.
    from management_portal.home_report_store import REGISTRY_PROJECTION
    projected = REGISTRY_PROJECTION.project(e.store.record(identifier), adapter='registry')
    assert projected['title'] == '汇报' and projected['summary'] == '持久汇报'
    e.workspace.call('UnregisterResource', {'resource_id': identifier}).raise_for_error()
    assert (e.workspace.root / 'manifest.json').exists()


def test_failed_registry_commit_recovers_same_snapshot(explanations, monkeypatch):
    e = explanations
    from management_portal.resources.registry_store import RegistryWriter
    original = RegistryWriter.put
    def fail(writer, collection, identifier, value):
        if collection == EXPLANATIONS:
            raise RuntimeError('interrupted binding commit')
        return original(writer, collection, identifier, value)
    with monkeypatch.context() as patch:
        patch.setattr(RegistryWriter, 'put', fail)
        with pytest.raises(RuntimeError, match='interrupted binding'):
            e.api.register(e.ref, 'alice')
    assert not e.store.registry.documents(COLLECTION)
    assert not e.store.registry.documents(ORIGINS)
    assert not e.store.registry.documents(EXPLANATIONS)
    frozen = next(e.store.root.glob('report_*/1/report.json'))
    before = frozen.read_bytes()
    result = e.api.register(e.ref, 'alice')
    assert result['report_id'] == frozen.parent.parent.name
    assert frozen.read_bytes() == before


def test_explanation_report_cannot_enter_template_mutations(explanations):
    from management_portal.home_report_persistent_media import PersistentMedia
    from management_portal.home_report_review import subject, authoring_subject
    e = explanations
    result = e.api.register(e.ref, 'alice')
    identifier = result['report_id']
    for action in (lambda: subject(e.store, identifier, 1, 'alice'),
                   lambda: authoring_subject(e.store, identifier, 1, 'alice', 'high'),
                   lambda: PersistentMedia(e.store).start(identifier, 1, 'alice', quality='low'),
                   lambda: e.store.refresh(identifier, 'alice', 1),
                   lambda: e.store.attach(identifier, 'alice', 1, WID + '/file/deck.pptx')):
        with pytest.raises(ValueError, match='explanation_target'):
            action()
    assert sorted(p.name for p in (e.store.directory(identifier) / '1').iterdir()) == ['report.json']


def test_standalone_loads_shared_preview_before_report(monkeypatch):
    monkeypatch.setattr(home_report_http.http_auth, '_get_request_actor', lambda request: '')
    monkeypatch.setattr(home_report_http.http_responses, '_serve_payload', lambda req, status, data, mime, **kw: data.decode())
    page = home_report_http._page(None, None, 'alice')
    scripts = ['portal_state.js', 'portal_text.js', 'portal_i18n.js', 'portal_ui_primitives.js',
               'portal_preview_contract.js', 'portal_preview_runtime.js', 'portal_pdf_preview.js',
               'portal_preview_renderers.js', 'home_explanation.js', 'home_report.js']
    positions = [page.index('/static/' + name) for name in scripts]
    assert positions == sorted(positions)
    assert 'data-portal-script-group' not in page
    assert page.index('portalSiteScript') < page.index('/static/portal_resource_chips.js')
