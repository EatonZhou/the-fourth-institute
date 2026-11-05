"""New deliverables use authored packages; historical snapshots remain readable.

:module: resource.service
:design: decision_e3ced6d30f6b
"""
from urllib.parse import urlparse

import pytest

from management_portal import home_report_http, home_report_review
from management_portal.home_report import build_report
from management_portal.home_report_quality import bind
from management_portal.home_report_store import COLLECTION, ORIGINS, ReportStore
from management_portal.resources.errors import ResourceOperationError, ResourceVersionConflict
from tests.cases.test_home_report_persistence import reports, RID, TARGET, KEY  # noqa: F401
from tests.cases.test_report_explanations import explanations  # noqa: F401
from tests.cases.test_workspace_resource_rename import workspace  # noqa: F401


def historical(store):
    # Seed pre-retirement data, not an alternate public creation entry point.
    # The real registry, exports, current Report/source ACL and revisions remain.
    snapshot = bind(build_report(TARGET, actor='alice', read=store.read), 'high', author='alice')
    store._create_locked(RID, {'origin_key': KEY}, 'alice', snapshot)
    return store.response(RID, 'alice')


def transport(monkeypatch, store, body):
    monkeypatch.setattr(home_report_http, 'ReportStore', lambda: store)
    monkeypatch.setattr(home_report_http.http_request_body, '_read_bounded_json_object', lambda *a, **kw: body)
    monkeypatch.setattr(home_report_http.http_responses, '_serve_json', lambda r, status, value: (status, value))


@pytest.mark.parametrize('actor', ['alice', 'resident_123456789abc'])
@pytest.mark.parametrize('quality', ['low', 'medium', 'high'])
def test_source_target_cannot_create_a_deliverable(reports, monkeypatch, actor, quality):
    store, state = reports
    with pytest.raises(ResourceOperationError) as error:
        store.create(RID, {'target': TARGET, 'quality': quality, 'origin_key': KEY}, actor)
    assert error.value.code == 'report.authored_package_required'
    assert state['reads'] == 0
    assert not store.root.exists()
    assert store.registry.get(COLLECTION, RID) is None
    assert not store.registry.documents(ORIGINS)


def test_reserved_disk_snapshot_cannot_resume_retired_creation(reports):
    store, _ = reports
    directory = store.directory(RID) / '1'
    directory.mkdir(parents=True)
    old = directory / 'report.json'
    old.write_text('{"target": {}}')
    with pytest.raises(ResourceOperationError):
        store.create(RID, {'target': TARGET}, 'alice')
    assert old.read_text() == '{"target": {}}'
    assert store.registry.get(COLLECTION, RID) is None


def test_old_save_and_source_get_do_not_build_or_register(reports, monkeypatch):
    store, state = reports
    transport(monkeypatch, store, {'target': TARGET, 'origin_key': KEY})
    for call in [lambda: home_report_http._save(None, None, 'alice'),
                 lambda: home_report_http._get(None, urlparse('/api/home-report?kind=task&id=' + TARGET['id']), 'alice')]:
        status, error = call()
        assert status == 422, error
        assert 'explanations' in str(error)
    assert not store.root.exists()
    assert state['reads'] == 0


def test_formal_provider_also_rejects_source_target(reports, workspace, monkeypatch):
    from management_portal.resources.providers import report as provider
    store, _ = reports
    monkeypatch.setattr(provider, 'ReportStore', lambda: store)
    with pytest.raises(ResourceOperationError) as error:
        workspace.service.register('report', {'target': TARGET}, actor='alice')
    assert error.value.code == 'report.authored_package_required'
    assert not store.registry.documents(COLLECTION)
    assert not store.root.exists()


def test_existing_origin_save_is_read_only_and_does_not_submit_review(reports, monkeypatch):
    store, state = reports
    original = historical(store)
    before = (store.directory(RID) / '1/report.json').read_bytes()
    transport(monkeypatch, store, {'target': TARGET, 'origin_key': KEY, 'quality': 'high'})
    monkeypatch.setattr(home_report_review, 'submit', lambda *a, **kw: pytest.fail('reopen must not review'))
    status, result = home_report_http._save(None, None, 'alice')
    assert status == 200 and result['report_id'] == original['report_id']
    assert result['version'] == original['version']
    assert store.create(RID, {'target': TARGET, 'quality': 'high'}, 'alice')['resource_id'] == RID
    assert (store.directory(RID) / '1/report.json').read_bytes() == before
    assert store.record(RID)['revision'] == 1
    state['deny'] = True
    with pytest.raises(PermissionError):
        store.reopen_origin({'origin_key': KEY}, 'alice')
    state['deny'] = False
    state['deny_report'] = True
    with pytest.raises(PermissionError):
        store.reopen_origin({'origin_key': KEY}, 'alice')


def test_existing_quality_conflict_delete_and_refresh_contracts_remain(reports, monkeypatch):
    store, _ = reports
    historical(store)
    with pytest.raises(ResourceVersionConflict):
        store.reopen_origin({'origin_key': KEY, 'quality': 'low'}, 'alice')
    store.refresh(RID, 'alice', 1)
    assert store.record(RID)['revision'] == 2
    with pytest.raises(ResourceVersionConflict):
        store.refresh(RID, 'alice', 1)
    store.delete(RID, 'alice', 2)
    with pytest.raises(ResourceOperationError) as error:
        store.reopen_origin({'origin_key': KEY}, 'alice')
    assert error.value.code == 'report.deleted'


def test_existing_package_registration_remains_the_single_creation_path(explanations, monkeypatch):
    e = explanations
    first = e.api.register(e.ref, 'alice')
    assert first['content_kind'] == 'explanation'
    assert e.api.register(e.ref, 'alice')['report_id'] == first['report_id']
    assert e.api.lookup(e.ref, 'alice')['explanation_target'] == e.ref
    assert 'review' not in first and 'media' not in first
