"""Fixed Report registration proves medium storyboard responsibility.

:module: resource.service
:design: decision_b12782e7f490, decision_e3ced6d30f6b
"""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from management_portal import home_explanation_http as http
from management_portal import home_explanation_review as review
from management_portal import home_report_store, permission_system, resources
from management_portal.home_explanation_media import Media
from management_portal.home_report_explanations import ExplanationReports, fields
from management_portal.home_report_store import ReportStore
from management_portal.society_service import resident_protocol, resident_intake_domain
from tests.cases.permission_test_support import enable
from tests.cases.test_workspace_resource_rename import workspace  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401
from tests.cases.test_family_familize import charters  # noqa: F401
from tests.cases.test_personal_assistant import personal  # noqa: F401

REAL_DELIVER_INPUT = resident_protocol.deliver_resident_input
REAL_INTAKE = resident_intake_domain.deliver_resident_message_content


@pytest.fixture
def authoring_case(personal, workspace, monkeypatch, tmp_path):
    from management_portal.permission_system import personal_ama
    from management_portal.resources.providers import report as provider
    from management_portal.society_service import resident_catalog, lineage_reads, resource_client
    from management_portal.society_service import resident_reporting, resident_reporting_policy

    ama, _assistant = personal
    # Provisioning fixtures suppress charters; restore actual isolated intake
    # for the user-triggered authoring delivery exercised here.
    monkeypatch.setattr(resident_protocol, 'deliver_resident_input', REAL_DELIVER_INPUT)
    monkeypatch.setattr(resident_intake_domain, 'deliver_resident_message_content', REAL_INTAKE)
    author = ama['resident_id']
    record = resident_catalog.get_catalog().get(author)
    graph = lineage_reads.get_lineage(ama['lineage_id'], 'newcomer')
    descriptor = resource_client._resident_resource_projection(record)['descriptor']
    monkeypatch.setattr(personal_ama, '_DESCRIPTORS', SimpleNamespace(get=lambda rid: descriptor if rid == author else None))
    monkeypatch.setattr(personal_ama, '_OWNERS', {})
    permissions = enable(monkeypatch)
    permissions.users('newcomer', 'outsider')
    for kind, item in [('lineage', resource_client._lineage_resource_projection(graph)['descriptor']), ('resident', descriptor)]:
        permission_system.sync_resource(item, resource_type=kind)
    store = ReportStore(root=tmp_path / 'reports')
    monkeypatch.setattr(provider, 'ReportStore', lambda: store)
    monkeypatch.setattr(home_report_store, 'ReportStore', lambda: store)
    monkeypatch.setattr(resources, 'get_resource_service', lambda: workspace.service)
    workspace.service._index().project_registered = lambda *a, **kw: None
    root = Path(record['owned_workspace']['path'])

    def package(name='manifest.json', **values):
        data = json.dumps({'schema': 'portal-explanation/v1', 'title': 'Synthetic fixed page',
                           'quality': 'medium', **values}).encode()
        (root / name).write_bytes(data)
        return {'address': record['owned_workspace_id'] + '/file/' + name,
                'sha256': hashlib.sha256(data).hexdigest()}

    calls, provisions, capability_calls = [], [], []
    original_capability = resident_protocol.reporting_creation_allowed

    def creation_allowed(resident_id, actor, message_id=''):
        capability_calls.append((resident_id, actor, message_id))
        return original_capability(resident_id, actor, message_id)

    # Observe the real authenticated service bridge; do not bypass its policy.
    monkeypatch.setattr(resident_protocol, 'reporting_creation_allowed', creation_allowed)

    def set_policy(initiative):
        resident_reporting.update(author, 'newcomer', {'reporting': {'initiative': initiative, 'quality': 'medium'}})

    set_policy('medium')

    def dispatch(actor, name, args, key=''):
        try:
            result = review.invoke(actor, name, args, key)
        except Exception as exc:
            pytest.fail(f'formal fixture delivery: {type(exc).__name__}: {exc}')
        calls.append((actor, name, args, key, result))
        return result

    def ensure(resident, actor, **kwargs):
        provisions.append((resident, actor, kwargs))
        raise PermissionError('synthetic reviewer provisioning refused')

    reviews = review.ReviewService(root=tmp_path / 'reviews', resources=workspace.service)
    authoring = review.AuthoringService(root=tmp_path / 'authoring', resources=workspace.service,
                                       call=dispatch, ensure=ensure)
    monkeypatch.setattr(review, 'ReviewService', lambda **kwargs: reviews)
    monkeypatch.setattr(review, 'AuthoringService', lambda **kwargs: authoring)
    media = Media(workspace.service, renderer=lambda *a: pytest.fail('authoring must not encode'))
    monkeypatch.setattr(http, 'Media', lambda: media)
    monkeypatch.setattr(http.http_responses, '_serve_json', lambda request, status, body: (status, body))

    def post(ref, actor='newcomer', quality='medium', **extra):
        monkeypatch.setattr(http.http_request_body, '_read_bounded_json_object', lambda *a, **kw: {**ref, 'quality': quality, **extra})
        return http._media(SimpleNamespace(command='POST'), urlparse('/api/home-explanation/media'), actor)

    return SimpleNamespace(author=author, store=store, api=ExplanationReports(store), package=package,
                           permissions=permissions, root=root, source=record['owned_workspace_id'],
                           authoring=authoring, reviews=reviews, media=media, post=post, calls=calls,
                           provisions=provisions, workspace=workspace, set_policy=set_policy,
                           capability_calls=capability_calls, policy_owner=resident_reporting_policy)


def test_medium_user_request_uses_authenticated_fixed_report_creator_once(authoring_case):
    from management_portal.society_service import resident_conversation_stream
    e = authoring_case
    ref = e.package()
    saved = e.api.register(ref, e.author)
    assert e.store.record(saved['report_id'])['created_by'] == e.author
    assert e.reviews.package_binding(ref['address'], ref['sha256'], 'newcomer') is None
    status, body = e.post(ref)
    assert status == 200
    task = body['media']['authoring']
    assert task['status'] == 'submitted', task
    assert task['subject']['author'] == 'newcomer'
    assert task['subject']['responsible_resident_id'] == e.author
    assert e.provisions == []
    assert len(e.calls) == 1
    actor, name, args, key, receipt = e.calls[0]
    assert (actor, name, args['resident_id']) == ('newcomer', 'DeliverMessage', e.author)
    assert args['business_ref'] == ref['address']
    assert key == 'quality-authoring:' + task['id']
    message = resident_conversation_stream.get_repository().message(e.author, receipt['message_id'])
    payload = json.loads(message['content_parts'][0]['text'])
    assert payload['subject']['quality'] == 'medium'
    assert payload['subject']['digest'] == ref['sha256']
    assert e.post(ref)[1]['media']['authoring']['id'] == task['id']
    assert len(e.calls) == 1


@pytest.mark.parametrize('registered', [False, True])
def test_package_and_request_claims_cannot_nominate_an_author(authoring_case, registered):
    e = authoring_case
    ref = e.package(author=e.author, responsible_resident_id=e.author,
                    authoring_proof={'resident_id': e.author})
    if registered:
        saved = e.api.register(ref, 'newcomer')
        assert e.store.record(saved['report_id'])['created_by'] == 'newcomer'
    status, body = e.post(ref, responsible_resident_id=e.author, author=e.author)
    assert status == 200
    assert body['media']['authoring']['status'] == 'awaiting_author'
    assert e.calls == [] and e.provisions == []


@pytest.mark.parametrize('bound', [False, True])
def test_report_acl_remains_required_for_responsibility(authoring_case, bound):
    e = authoring_case
    ref = e.package()
    saved = e.api.register(ref, e.author)
    if bound:
        e.api.register(ref, 'newcomer')
    e.permissions.deny('newcomer', saved['report_id'])
    if bound:
        with pytest.raises(PermissionError):
            e.media.start(**ref, actor='newcomer')
    else:
        assert e.media.start(**ref, actor='newcomer')['authoring']['status'] == 'awaiting_author'
    assert e.calls == []


@pytest.mark.parametrize('failure', ['outsider', 'revoked_source', 'changed_bytes', 'deleted_report'])
def test_fixed_sources_and_current_permissions_are_not_bypassed(authoring_case, failure):
    e = authoring_case
    ref = e.package()
    saved = e.api.register(ref, e.author)
    actor = 'newcomer'
    error = PermissionError
    if failure == 'outsider':
        actor = 'outsider'
    elif failure == 'revoked_source':
        e.permissions.deny(actor, e.source)
    elif failure == 'changed_bytes':
        (e.root / 'manifest.json').write_text('{}')
        error = ValueError
    else:
        e.workspace.call('UnregisterResource', {'resource_id': saved['report_id']}, actor=actor).raise_for_error()
        assert e.media.start(**ref, actor=actor)['authoring']['status'] == 'awaiting_author'
        assert not e.calls
        return
    with pytest.raises(error):
        e.media.start(**ref, actor=actor)
    assert not e.calls


def test_candidate_for_another_fixed_package_is_rejected(authoring_case):
    from management_portal.home_report_store import EXPLANATIONS
    e = authoring_case
    requested = e.package()
    other = e.package('other.json', title='Different source')
    saved = e.api.register(other, e.author)
    # Model an inconsistent index, not client-authorized provenance. The
    # canonical Report snapshot must still match both address and digest.
    with e.store.writer(saved['report_id']) as writer:
        writer.put(EXPLANATIONS, fields(requested)['origin_key'],
                   {'bindings': [{'report_id': saved['report_id'], 'revision': 1}]})
    with pytest.raises(ValueError, match='固定来源不一致'):
        e.media.start(**requested, actor='newcomer')
    assert not e.calls


def test_authoring_subject_cannot_impersonate_authenticated_requester(authoring_case):
    e = authoring_case
    ref = e.package()
    with pytest.raises(PermissionError, match='当前认证主体'):
        e.authoring.submit({'kind': 'explanation_video', 'ref': ref['address'],
            'version': ref['sha256'], 'digest': ref['sha256'], 'quality': 'medium',
            'author': e.author, 'assets': [ref]}, 'newcomer')
    assert not e.calls


@pytest.mark.parametrize('cancelled', [False, True])
def test_late_registration_preserves_request_identity_and_cancellation(authoring_case, cancelled):
    e = authoring_case
    ref = e.package()
    waiting = e.media.start(**ref, actor='newcomer')['authoring']
    assert waiting['status'] == 'awaiting_author'
    if cancelled:
        e.authoring.cancel(waiting['id'], 'newcomer')
    e.api.register(ref, e.author)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: e.media.start(**ref, actor='newcomer')['authoring'], range(2)))
    assert {result['id'] for result in results} == {waiting['id']}
    assert {result['status'] for result in results} == ({'cancelled'} if cancelled else {'submitted'})
    assert len(e.calls) == (0 if cancelled else 1)


def test_high_still_requires_formal_independent_review_roles(authoring_case):
    e = authoring_case
    ref = e.package()
    e.api.register(ref, e.author)
    with pytest.raises(PermissionError, match='reviewer provisioning refused'):
        e.media.start(**ref, actor='newcomer', quality='high')
    assert e.provisions == [(e.author, 'newcomer', {'kind': 'video', 'reviewer_id': ''})]
    assert e.calls == []


def test_registered_report_rechecks_dependency_acl_before_delivery(authoring_case):
    from management_portal.permission_system.facts import Fact
    from tests.cases.test_workspace_resource_rename import WID
    e = authoring_case
    data = b'<p>Synthetic dependency</p>'
    (e.workspace.root / 'page.html').write_bytes(data)
    e.permissions.place(Fact('workspace', WID))
    for actor in ('newcomer', e.author):
        e.permissions.grant(actor, WID, '@viewer')
    ref = e.package(interaction={'address': WID + '/file/page.html',
                                'sha256': hashlib.sha256(data).hexdigest()})
    e.api.register(ref, e.author)
    e.permissions.deny('newcomer', WID)
    with pytest.raises(PermissionError):
        e.media.start(**ref, actor='newcomer')
    assert e.calls == []


@pytest.mark.parametrize('mismatch', ['version', 'assets'])
def test_responsibility_cannot_bind_a_different_source_digest(authoring_case, mismatch):
    e = authoring_case
    ref = e.package()
    e.api.register(ref, e.author)
    other = e.package('other.json', title='Other')
    value = {'kind': 'explanation_video', 'ref': ref['address'], 'version': ref['sha256'],
             'digest': ref['sha256'], 'quality': 'medium', 'author': 'newcomer', 'assets': [ref]}
    value[mismatch] = other['sha256'] if mismatch == 'version' else [other]
    with pytest.raises(ValueError, match='同一固定清单'):
        e.authoring.submit(value, 'newcomer')
    assert not e.calls


def test_authoring_dispatch_still_checks_formal_delivery_permission(authoring_case):
    e = authoring_case
    ref = e.package()
    e.api.register(ref, e.author)
    e.permissions.deny('newcomer', e.author, '@message')
    assert not permission_system.allows('newcomer', 'DeliverMessage', {'resident_id': e.author})
    e.authoring.call = review.invoke
    task = e.media.start(**ref, actor='newcomer')['authoring']
    assert task['status'] == 'submission_failed'
    assert 'message_id' not in task
    assert task['attempts'] == 1
    assert not e.provisions


@pytest.mark.parametrize('state', ['off', 'unknown'])
@pytest.mark.parametrize('quality', ['medium', 'high'])
def test_disabled_or_unknown_capability_creates_no_state_or_delivery(authoring_case, monkeypatch, state, quality):
    e = authoring_case
    ref = e.package()
    e.api.register(ref, e.author)
    if state == 'off':
        e.set_policy('off')
    else:
        monkeypatch.setattr(e.policy_owner, 'effective', lambda *a, **kw: {'state': 'unknown'})
    before = sorted(str(p.relative_to(e.root)) for p in e.root.rglob('*'))
    with pytest.raises(PermissionError, match='汇报能力未开启或配置未确认'):
        e.media.start(**ref, actor='newcomer', quality=quality)
    assert not e.authoring.root.exists()
    assert before == sorted(str(p.relative_to(e.root)) for p in e.root.rglob('*'))
    assert e.capability_calls == [(e.author, 'newcomer', '')]
    assert not e.calls and not e.provisions


def test_disabled_capability_does_not_mutate_or_replay_existing_authoring(authoring_case):
    e = authoring_case
    ref = e.package()
    e.api.register(ref, e.author)
    task = e.media.start(**ref, actor='newcomer')['authoring']
    path = e.authoring.root / (task['id'] + '.json')
    original = path.read_bytes()
    e.set_policy('off')
    calls = len(e.capability_calls)
    assert e.authoring.read(task['id'], 'newcomer')['id'] == task['id']
    assert e.media.status(**ref, actor='newcomer')['authoring']['id'] == task['id']
    assert e.media.start(**ref, actor='newcomer')['authoring']['id'] == task['id']
    assert path.read_bytes() == original
    assert len(e.calls) == 1 and len(e.capability_calls) == calls


@pytest.mark.parametrize('state', ['off', 'unknown'])
def test_disabled_capability_rejects_failed_delivery_retry_without_mutation(authoring_case, monkeypatch, state):
    e = authoring_case
    ref = e.package()
    e.api.register(ref, e.author)
    e.permissions.deny('newcomer', e.author, '@message')
    attempted_delivery = []

    def dispatch(actor, name, args, key=''):
        attempted_delivery.append((actor, name, args['resident_id'], key))
        return review.invoke(actor, name, args, key)

    e.authoring.call = dispatch
    status, body = e.post(ref, message_id='msg_untrusted_body_selector')
    assert status == 200
    task = body['media']['authoring']
    assert task['status'] == 'submission_failed'
    expected_call = (e.author, 'newcomer', '')
    assert e.capability_calls and all(call == expected_call for call in e.capability_calls)
    initial_checks = len(e.capability_calls)
    assert attempted_delivery == [('newcomer', 'DeliverMessage', e.author, 'quality-authoring:' + task['id'])]
    original = {str(p): p.read_bytes() for p in e.authoring.root.rglob('*') if p.is_file()}
    original_media = {str(p): p.read_bytes() for p in e.root.rglob('*') if p.is_file()}
    if state == 'off':
        e.set_policy('off')
    else:
        monkeypatch.setattr(e.policy_owner, 'effective', lambda *a, **kw: {'state': 'unknown'})
    with pytest.raises(PermissionError, match='汇报能力未开启或配置未确认'):
        e.media.start(**ref, actor='newcomer')
    assert original == {str(p): p.read_bytes() for p in e.authoring.root.rglob('*') if p.is_file()}
    assert original_media == {str(p): p.read_bytes() for p in e.root.rglob('*') if p.is_file()}
    assert e.authoring.read(task['id'], 'newcomer')['attempts'] == 1
    assert len(e.capability_calls) > initial_checks
    assert all(call == expected_call for call in e.capability_calls)
    assert len(attempted_delivery) == 1
    assert e.calls == [] and e.provisions == []


@pytest.mark.parametrize('quality', ['medium', 'high'])
def test_creation_bridge_exception_fails_before_new_state_or_dispatch(authoring_case, monkeypatch, quality):
    e = authoring_case
    ref = e.package()
    e.api.register(ref, e.author)
    observed = []

    def unavailable(resident_id, actor, message_id=''):
        observed.append((resident_id, actor, message_id))
        raise RuntimeError('synthetic creation bridge unavailable')

    monkeypatch.setattr(resident_protocol, 'reporting_creation_allowed', unavailable)
    before = {str(p): p.read_bytes() for p in e.root.rglob('*') if p.is_file()}
    with pytest.raises(PermissionError, match='无法确认汇报创作能力') as denied:
        e.media.start(**ref, actor='newcomer', quality=quality)
    assert 'synthetic creation bridge unavailable' not in str(denied.value)
    assert observed == [(e.author, 'newcomer', '')]
    assert not e.authoring.root.exists()
    assert before == {str(p): p.read_bytes() for p in e.root.rglob('*') if p.is_file()}
    assert not e.calls and not e.provisions


def test_unavailable_creation_bridge_is_not_used_to_read_submitted_task(authoring_case, monkeypatch):
    e = authoring_case
    ref = e.package()
    e.api.register(ref, e.author)
    task = e.media.start(**ref, actor='newcomer')['authoring']
    original = {str(p): p.read_bytes() for p in e.authoring.root.rglob('*') if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail('existing authoring must not query the creation bridge')

    monkeypatch.setattr(resident_protocol, 'reporting_creation_allowed', forbidden)
    assert e.authoring.read(task['id'], 'newcomer')['id'] == task['id']
    assert e.media.status(**ref, actor='newcomer')['authoring']['id'] == task['id']
    assert e.media.start(**ref, actor='newcomer')['authoring']['id'] == task['id']
    assert original == {str(p): p.read_bytes() for p in e.authoring.root.rglob('*') if p.is_file()}
    assert len(e.calls) == 1 and not e.provisions


@pytest.mark.parametrize('denied', ['source', 'report'])
def test_source_and_proof_authorization_precedes_creation_bridge(authoring_case, monkeypatch, denied):
    e = authoring_case
    ref = e.package()
    saved = e.api.register(ref, e.author)
    e.api.register(ref, 'newcomer')
    e.permissions.deny('newcomer', e.source if denied == 'source' else saved['report_id'])

    def forbidden(*args, **kwargs):
        pytest.fail('creation bridge must not precede source and Report authorization')

    monkeypatch.setattr(resident_protocol, 'reporting_creation_allowed', forbidden)
    with pytest.raises(PermissionError):
        e.media.start(**ref, actor='newcomer')
    assert not e.authoring.root.exists()
    assert not e.calls and not e.provisions
