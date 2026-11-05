"""Creation capability is separate from source ACL and existing artifact reads.

:module: resource.service
:design: decision_8b2fa224616b
"""
from types import SimpleNamespace

import pytest

from management_portal import home_report_creation as admission, home_report_http
from management_portal.home_report_media import MediaStore
from management_portal.home_report_persistent_media import PersistentMedia
from tests.cases.test_home_report_persistence import reports, RID, renderer, wait_ready
from tests.cases.test_home_report_quality import auditing, create, RESPONSIBLE
from tests.cases.test_home_explanation_media import setup
from tests.cases.test_report_explanations import explanations
from tests.cases.test_reporting_creation_bridge import bridge, policy, team
from tests.cases.test_cross_owner_storage_integration import owners
from tests.cases.test_workspace_resource_rename import workspace


@pytest.fixture
def capability(monkeypatch):
    from management_portal import society_service
    calls, decisions = [], {}
    def allowed(rid, actor, message_id=''):
        calls.append((rid, actor, message_id))
        value = decisions.get(rid, False)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(society_service, 'get_society_service', lambda: SimpleNamespace(
        resident=SimpleNamespace(reporting_creation_allowed=allowed)))
    return decisions, calls


@pytest.mark.parametrize('value', [False, None, RuntimeError('private transport detail')])
def test_unknown_off_and_bridge_failure_reject_creation(capability, value):
    decisions, calls = capability
    decisions[RESPONSIBLE] = value
    with pytest.raises(PermissionError) as failure:
        admission.require_creation(RESPONSIBLE)
    assert 'private transport detail' not in str(failure.value)
    assert calls == [(RESPONSIBLE, RESPONSIBLE, '')]


def test_actual_actor_cannot_borrow_enabled_responsible(capability):
    decisions, calls = capability
    actor = 'resident_111111111111'
    decisions[RESPONSIBLE] = True
    with pytest.raises(PermissionError):
        admission.require_creation(actor, responsible=RESPONSIBLE)
    assert (actor, actor, '') in calls
    decisions[actor] = True
    admission.require_creation(actor, responsible=RESPONSIBLE)
    assert (RESPONSIBLE, actor, '') in calls


def test_off_preparation_does_not_create_review_roles(capability, tmp_path):
    from management_portal.home_explanation_review import ReviewService
    service = ReviewService(root=tmp_path / 'reviews', resources=SimpleNamespace(),
        ensure=lambda *a, **k: pytest.fail('off cannot provision review roles'))
    with pytest.raises(PermissionError, match='汇报能力'):
        service.prepare(RESPONSIBLE, kind='page', quality='high', task='Understand a source')
    assert not service.root.exists()


def test_real_society_bridge_controls_business_admission(team, bridge, monkeypatch):
    from management_portal import society_service
    from tests.cases.test_cross_owner_storage_integration import ACTOR
    call, requests = bridge
    monkeypatch.setattr(society_service, 'get_society_service', lambda: SimpleNamespace(
        resident=SimpleNamespace(reporting_creation_allowed=call)))
    rid = team.source['resident_id']
    policy(rid, 'off')
    with pytest.raises(PermissionError):
        admission.require_creation(ACTOR, responsible=rid)
    policy(rid, 'medium')
    admission.require_creation(ACTOR, responsible=rid)
    assert requests[-1]['args'] == [rid, ACTOR]
    assert requests[-1]['kwargs'] == {}


def test_http_check_uses_authenticated_resident_and_rejects_selectors(monkeypatch, capability):
    body = {}
    monkeypatch.setattr(home_report_http.http_request_body, '_read_bounded_json_object', lambda *a, **k: body)
    monkeypatch.setattr(home_report_http.http_responses, '_serve_json', lambda request, code, data: (code, data))
    monkeypatch.setattr(home_report_http.http_responses, '_serve_exception_json',
                        lambda request, exc: (403 if isinstance(exc, PermissionError) else 400, str(exc)))
    decisions, calls = capability
    decisions[RESPONSIBLE] = True
    assert home_report_http._creation_check(None, None, RESPONSIBLE) == (200, {'allowed': True})
    body.update(resident_id=RESPONSIBLE, message_id='msg_forged')
    assert home_report_http._creation_check(None, None, 'alice')[0] == 400
    body.clear()
    assert home_report_http._creation_check(None, None, 'alice')[0] == 403
    decisions[RESPONSIBLE] = False
    assert home_report_http._creation_check(None, None, RESPONSIBLE)[0] == 403
    assert len(calls) == 2


@pytest.mark.parametrize('enabled', [False, True])
def test_retired_template_create_denied_without_record_or_exports(reports, auditing, enabled):
    store, state = reports
    state['reporting_enabled'] = enabled
    from management_portal.resources.errors import ResourceOperationError
    from tests.cases.test_home_report_quality import TARGET, KEY, ORIGIN
    with pytest.raises(ResourceOperationError) as failure:
        store.create(RID, {'target': TARGET, 'origin_key': KEY, 'origin_context': ORIGIN, 'quality': 'low'}, 'alice')
    assert failure.value.code == 'report.authored_package_required'
    assert store.registry.get('report', RID) is None
    assert not store.directory(RID).exists()


def test_off_keeps_report_read_and_origin_but_blocks_new_revision(reports, auditing):
    store, state = reports
    first = create(store, 'low')
    original = store.file(RID, 1, 'report.json', 'alice')[0].read_bytes()
    state['reporting_enabled'] = False
    assert create(store, 'low')['report_id'] == first['report_id']
    assert store.response(RID, 'alice', 1)['version'] == first['version']
    with pytest.raises(PermissionError, match='汇报能力'):
        store.refresh(RID, 'alice', 1, content={'markdown': 'new content'})
    assert store.record(RID)['revision'] == 1
    assert store.file(RID, 1, 'report.json', 'alice')[0].read_bytes() == original
    state['deny'] = True
    with pytest.raises(PermissionError, match='source revoked'):
        store.refresh(RID, 'alice', 1)


def test_low_media_off_prevents_new_render_but_ready_remains_readable(reports, auditing):
    store, state = reports
    create(store, 'low')
    media = PersistentMedia(store, renderer)
    state['reporting_enabled'] = False
    with pytest.raises(PermissionError, match='汇报能力'):
        media.start(RID, 1, 'alice', quality='low')
    assert media.status(RID, 1, 'alice', quality='low') is None
    state['reporting_enabled'] = True
    media.start(RID, 1, 'alice', quality='low')
    ready = wait_ready(media, 'low')
    state['reporting_enabled'] = False
    assert media.start(RID, 1, 'alice', quality='low')['assets'] == ready['assets']
    assert media.file(RID, 1, 'video.mp4', 'alice', quality='low')[0].is_file()
    state['deny'] = True
    with pytest.raises(PermissionError):
        media.file(RID, 1, 'video.mp4', 'alice', quality='low')


def test_explanation_low_checks_fixed_responsible_after_source_acl(setup, monkeypatch, capability):
    from management_portal.home_explanation_media import Media
    service, ref, _put = setup
    decisions, calls = capability
    proof_calls = []
    def proof(target, actor):
        proof_calls.append((target, actor))
        return RESPONSIBLE
    monkeypatch.setattr(admission, 'explanation_responsible', proof)
    media = Media(service, lambda *a: pytest.fail('off cannot render'))
    with pytest.raises(PermissionError, match='汇报能力'):
        media.start(**ref, actor='reader', quality='low')
    assert proof_calls == [(ref, 'reader')]
    assert calls == [(RESPONSIBLE, 'reader', '')]
    assert not list(service.root.rglob('explanation-media'))
    service.denied.add(ref['address'])
    with pytest.raises(PermissionError, match='denied'):
        media.start(**ref, actor='reader', quality='low')
    assert len(calls) == len(proof_calls) == 1


def test_legacy_resident_generation_denied_without_cache(tmp_path, capability):
    media = MediaStore(tmp_path / 'cache', renderer=lambda *a: pytest.fail('off cannot render'))
    with pytest.raises(PermissionError):
        media.start({'version': 'fixed-source'}, RESPONSIBLE)
    assert not media.root.exists()


def test_existing_package_registration_and_read_do_not_create_again(explanations, monkeypatch):
    e = explanations
    monkeypatch.setattr(admission, 'require_creation', lambda *a, **k: pytest.fail('registration is not creation'))
    saved = e.api.register(e.ref, 'alice')
    assert e.api.lookup(e.ref, 'alice')['report_id'] == saved['report_id']
    assert e.api.register(e.ref, 'alice')['report_id'] == saved['report_id']
    assert e.store.response(saved['report_id'], 'alice', 1)['explanation_target'] == e.ref
