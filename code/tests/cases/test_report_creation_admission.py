"""Report operations enforce resource rights independently of prompt settings.

:module: resource.service
:design: decision_8b2fa224616b
"""
from types import SimpleNamespace
import pytest

from management_portal import home_report_explanations
from management_portal.home_report_persistent_media import PersistentMedia
from management_portal.home_explanation_review import ReviewService
from management_portal.resources.errors import ResourceVersionConflict
from tests.cases.test_home_report_persistence import reports, RID, renderer, wait_ready
from tests.cases.test_home_report_quality import auditing, create, RESPONSIBLE
from tests.cases.test_home_explanation_media import setup, fake_render, done
from tests.cases.test_report_explanations import explanations
from tests.cases.test_workspace_resource_rename import workspace


@pytest.fixture(autouse=True)
def no_prompt_policy_in_business(monkeypatch):
    from management_portal.society_service import resident_reporting_policy
    def forbidden(*args, **kwargs):
        pytest.fail('Report business operations must not consult prompt policy')
    monkeypatch.setattr(resident_reporting_policy, 'effective', forbidden)


def test_preparation_keeps_resident_identity_and_reviewer_role_checks(tmp_path):
    requested = []
    service = ReviewService(root=tmp_path/'reviews', resources=SimpleNamespace())
    def ensure(subject, actor):
        requested.append((subject, actor))
        return {'reviewer_id': 'resident_abcdefabcdef'}
    service.ensure_reviewers = ensure
    result = service.prepare(RESPONSIBLE, kind='page', quality='high', task='Understand the source')
    assert result['status'] == 'prepared' and requested[0][1] == RESPONSIBLE
    with pytest.raises(PermissionError):
        service.prepare('alice', kind='page', quality='high', task='Understand the source')
    assert len(requested) == 1


def test_retired_template_creation_still_requires_authored_package(reports):
    from management_portal.resources.errors import ResourceOperationError
    from tests.cases.test_home_report_quality import TARGET, KEY, ORIGIN
    store, _ = reports
    with pytest.raises(ResourceOperationError) as failure:
        store.create(RID, {'target': TARGET, 'origin_key': KEY, 'origin_context': ORIGIN, 'quality': 'low'}, 'alice')
    assert failure.value.code == 'report.authored_package_required'
    assert store.registry.get('report', RID) is None and not store.directory(RID).exists()


def test_refresh_uses_source_permissions_and_cas_without_prompt_policy(reports, auditing):
    store, state = reports
    first = create(store, 'low')
    original = store.file(RID, 1, 'report.json', 'alice')[0].read_bytes()
    assert create(store, 'low')['report_id'] == first['report_id']
    store.refresh(RID, 'alice', 1, content={'markdown': 'new content'})
    assert store.record(RID)['revision'] == 2
    assert store.file(RID, 1, 'report.json', 'alice')[0].read_bytes() == original
    with pytest.raises(ResourceVersionConflict):
        store.refresh(RID, 'alice', 1)
    state['deny'] = True
    with pytest.raises(PermissionError, match='source revoked'):
        store.refresh(RID, 'alice', 2)


def test_low_media_uses_resource_rights_and_reuses_ready_assets(reports, auditing):
    store, state = reports
    create(store, 'low')
    media = PersistentMedia(store, renderer)
    media.start(RID, 1, 'alice', quality='low')
    ready = wait_ready(media, 'low')
    assert media.start(RID, 1, 'alice', quality='low')['assets'] == ready['assets']
    assert media.file(RID, 1, 'video.mp4', 'alice', quality='low')[0].is_file()
    state['deny'] = True
    with pytest.raises(PermissionError):
        media.file(RID, 1, 'video.mp4', 'alice', quality='low')


def test_explanation_low_preserves_registered_proof_and_source_acl(setup, monkeypatch):
    from management_portal.home_explanation_media import Media
    service, ref, _ = setup
    calls = []
    def proof(target, actor):
        calls.append((target, actor))
        return RESPONSIBLE
    monkeypatch.setattr(home_report_explanations, 'registered_responsible', proof)
    media = Media(service, fake_render)
    media.start(**ref, actor='reader', quality='low')
    assert done(media, ref, quality='low')['status'] == 'ready'
    assert calls == [(ref, 'reader')]
    service.denied.add(ref['address'])
    with pytest.raises(PermissionError, match='denied'):
        media.start(**ref, actor='reader', quality='low')
    assert len(calls) == 1


def test_existing_package_registration_keeps_fixed_identity(explanations):
    e = explanations
    saved = e.api.register(e.ref, 'alice')
    assert e.api.lookup(e.ref, 'alice')['report_id'] == saved['report_id']
    assert e.api.register(e.ref, 'alice')['report_id'] == saved['report_id']
    assert e.store.response(saved['report_id'], 'alice', 1)['explanation_target'] == e.ref
