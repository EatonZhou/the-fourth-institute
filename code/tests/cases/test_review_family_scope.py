"""Web request timing and frozen-round contracts; Society lifecycle has its own tests.

Only the injected Society boundary is synthetic here. No global directory search
or live/private Family changes are performed by these caller tests.
"""
import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlparse

import pytest

from management_portal.home_explanation_review import ReviewService, AuthoringService
from tests.cases.test_home_explanation_quality import rig, AUTHOR, CHECKER
from tests.cases.test_family_familize import lineage, charters, future_creation_store, resident
from tests.cases.isa_test_support import ACTOR

SECOND = 'resident_333333333333'
SECOND_CHECKER = 'resident_444444444444'
VIDEO_CHECKER = 'resident_555555555555'


@pytest.fixture
def owner(tmp_path):
    events = []
    scopes = {AUTHOR: ('fbi_111111111111', CHECKER), SECOND: ('fbi_222222222222', SECOND_CHECKER)}
    data = b'fixed explanation'
    digest = hashlib.sha256(data).hexdigest()
    resources = SimpleNamespace(resolve=lambda *a, **kw: {},
        read_content=lambda *a, **kw: (data, 'text/plain', {}))
    def ensure(resident_id, actor, *, kind, reviewer_id=''):
        events.append(('ensure', resident_id, actor, kind, reviewer_id))
        family, page = scopes[resident_id]
        reviewers = {'page': page, 'video': VIDEO_CHECKER}
        if reviewer_id and reviewer_id not in reviewers.values():
            raise PermissionError('reviewer must belong to the responsible direct Family')
        return {'family_id': family, 'lineage_id': 'lineage_111111111111',
                'reviewer_id': reviewer_id or reviewers[kind], 'reviewers': reviewers}
    def call(actor, name, args, key=''):
        assert name == 'DeliverMessage'
        events.append(('deliver', actor, args['resident_id']))
        return {'message_id': 'msg_' + hashlib.sha256(key.encode()).hexdigest()[:32]}
    service = ReviewService(root=tmp_path/'reviews', resources=resources, call=call, ensure=ensure)
    authoring = AuthoringService(root=tmp_path/'authoring', resources=resources, call=call, ensure=ensure)
    subject = {'kind': 'explanation', 'quality': 'high', 'author': 'fixture-user',
        'responsible_resident_id': AUTHOR, 'ref': 'workspace_111111111111/file/manifest.json',
        'version': digest, 'digest': digest, 'assets': [
            {'address': 'workspace_111111111111/file/page.html', 'sha256': digest}]}
    return service, authoring, subject, scopes, events


def test_verified_responsible_selects_family_with_actual_caller_and_concurrent_repeat(owner):
    service, _, subject, scopes, events = owner
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda _: service.submit(subject, 'fixture-user'), range(4)))
    assert len({r['id'] for r in records}) == 1
    assert events == [('ensure', AUTHOR, 'fixture-user', 'page', ''),
                      ('deliver', 'fixture-user', CHECKER)]
    other = service.submit({**subject, 'responsible_resident_id': SECOND}, 'fixture-user')
    assert other['reviewer'] == SECOND_CHECKER
    assert other['reviewer_scope']['family_id'] != records[0]['reviewer_scope']['family_id']
    before = copy.deepcopy(events)
    service.read(records[0]['id'], 'fixture-user')
    service.current(other['id'], 'fixture-user')
    assert events == before


def test_explicit_external_reviewer_has_no_snapshot_or_delivery(owner):
    service, _, subject, _, events = owner
    result = service.submit(subject, 'fixture-user', reviewer=SECOND_CHECKER)
    assert result['status'] == 'submission_failed'
    assert 'handoff' not in result and 'reviewer' not in result
    assert events == [('ensure', AUTHOR, 'fixture-user', 'page', SECOND_CHECKER)]


def test_explicit_member_cannot_substitute_the_other_review_role(owner):
    service, _, subject, _, events = owner
    result = service.submit(subject, 'fixture-user', reviewer=VIDEO_CHECKER)
    assert result['status'] == 'submission_failed'
    assert 'handoff' not in result and not any(e[0] == 'deliver' for e in events)


def test_old_round_stays_frozen_until_explicit_resume_uses_current_family(owner):
    service, _, subject, scopes, events = owner
    first = service.submit(subject, 'fixture-user')
    old = service.verdict(first['id'], CHECKER, digest=subject['digest'],
                          status='blocked', evidence=[], feedback='Need a real inspection')
    frozen = copy.deepcopy(old)
    scopes[AUTHOR] = ('fbi_222222222222', SECOND_CHECKER)
    before = len(events)
    assert service.submit(subject, 'fixture-user')['reviewer'] == CHECKER
    assert service._load(first['id']) == frozen and len(events) == before
    resumed = service.resume(first['id'], 'fixture-user')
    assert resumed['reviewer'] == SECOND_CHECKER and resumed['round'] == 2
    assert events[before] == ('ensure', AUTHOR, 'fixture-user', 'page', '')
    original = service._load(first['id'])
    assert original.pop('continuation_review_id') == resumed['id']
    original.pop('updated_at', None); frozen.pop('updated_at', None)
    assert original == frozen


def test_retry_cannot_reassign_existing_handoff_to_another_family(owner):
    service, _, subject, scopes, events = owner
    deliver = service.call
    def unavailable(*args, **kwargs):
        raise ConnectionError('isolated delivery failure')
    service.call = unavailable
    first = service.submit(subject, 'fixture-user')
    assert first['status'] == 'submission_failed' and first['reviewer'] == CHECKER
    frozen = copy.deepcopy(first['handoff'])
    scopes[AUTHOR] = ('fbi_222222222222', SECOND_CHECKER)
    service.call = deliver
    retry = service.submit(subject, 'fixture-user')
    assert retry['status'] == 'submission_failed' and retry['handoff'] == frozen
    assert not any(e[0] == 'deliver' for e in events)


@pytest.mark.parametrize('quality', ['medium', 'high'])
def test_authoring_ensures_before_dispatch_only_on_high_request(owner, quality):
    _, authoring, subject, _, events = owner
    result = authoring.submit({**subject, 'kind': 'explanation_video', 'quality': quality}, 'fixture-user')
    assert result['status'] == 'submitted'
    expected = [('ensure', AUTHOR, 'fixture-user', 'video', '')] if quality == 'high' else []
    assert events == expected + [('deliver', 'fixture-user', AUTHOR)]
    before = copy.deepcopy(events)
    authoring.read(result['id'], 'fixture-user')
    authoring.submit({**subject, 'kind': 'explanation_video', 'quality': quality}, 'fixture-user')
    assert events == before


def test_missing_responsible_or_permission_does_not_accept_author_work(owner):
    _, authoring, subject, _, events = owner
    waiting = authoring.submit({**subject, 'responsible_resident_id': ''}, 'fixture-user')
    assert waiting['status'] == 'awaiting_author' and not events
    def denied(*args, **kwargs):
        raise PermissionError('CreateResident denied for actual caller')
    authoring.ensure = denied
    with pytest.raises(PermissionError, match='CreateResident'):
        authoring.submit(subject, 'fixture-user')
    assert not events
    assert authoring._load(authoring.identifier(subject)) is None


def test_high_generation_checks_family_before_encoding_and_get_never_provisions(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    ref = package(801)
    before = media.status(**ref, actor=AUTHOR, quality='high')
    ensured = []
    def denied(*args, **kwargs):
        ensured.append((args, kwargs))
        raise PermissionError('CreateResident revoked')
    original = reviews.ensure
    reviews.ensure = denied
    assert media.status(**ref, actor=AUTHOR, quality='high') == before and not ensured
    with pytest.raises(PermissionError, match='CreateResident'):
        media.start(**ref, actor=AUTHOR, quality='high')
    assert not renders and len(ensured) == 1
    assert ensured[0] == ((AUTHOR, AUTHOR), {'kind': 'video', 'reviewer_id': ''})
    assert media.status(**ref, actor=AUTHOR, quality='high') == before
    reviews.ensure = original
    media.start(**ref, actor=AUTHOR, quality='high')
    assert done(ref, 'high')['status'] == 'ready'


def test_high_authoring_through_real_society_service_creates_both_roles(lineage, charters, tmp_path, monkeypatch):
    from management_portal import society_service
    from management_portal.society_service.server import LocalSocietyService
    from management_portal.society_service.resident_record_domain import find_resident
    from tests.cases.test_isa_lineage_lifecycle import graph
    responsible = resident(lineage, 'Verified author for high Web request')
    monkeypatch.setattr(society_service, 'get_society_service', LocalSocietyService)
    data = b'fixed request scope'
    digest = hashlib.sha256(data).hexdigest()
    resources = SimpleNamespace(resolve=lambda *a, **kw: {},
        read_content=lambda *a, **kw: (data, 'text/plain', {}))
    deliveries = []
    def deliver(actor, name, args, key=''):
        assert name == 'DeliverMessage' and actor == ACTOR
        # At author dispatch (before any render), both real members exist.
        record = service._load(service.identifier(subject))
        roles = record['reviewer_scope']['reviewers']
        assert len(set(roles.values())) == 2
        for rid in roles.values():
            assert find_resident(rid)['family']['family_id'] == record['reviewer_scope']['family_id']
        deliveries.append(args['resident_id'])
        return {'message_id': 'msg_fixture_delivery'}
    service = AuthoringService(root=tmp_path/'authoring', resources=resources, call=deliver)
    subject = {'kind': 'explanation_video', 'quality': 'high', 'author': ACTOR,
        'responsible_resident_id': responsible, 'ref': 'workspace_111111111111/file/manifest.json',
        'version': digest, 'digest': digest,
        'assets': [{'address': 'workspace_111111111111/file/manifest.json', 'sha256': digest}]}
    record = service.submit(subject, ACTOR)
    assert record['status'] == 'submitted' and deliveries == [responsible]
    before = graph(lineage)
    assert service.submit(subject, ACTOR)['id'] == record['id']
    assert graph(lineage) == before and deliveries == [responsible]


def test_prepare_http_before_any_artifact_uses_only_authenticated_self(lineage, charters, tmp_path, monkeypatch):
    from management_portal import society_service, home_explanation_http as http, http_auth
    from management_portal.society_service.server import LocalSocietyService
    from management_portal.society_service import family_quality_reviewers as family
    from tests.cases.test_isa_lineage_lifecycle import graph
    actor = resident(lineage, 'Page author before writing any file')
    denied_actor = resident(lineage, 'Author without create permission')
    monkeypatch.setattr(society_service, 'get_society_service', LocalSocietyService)
    service = ReviewService(root=tmp_path/'reviews', resources=SimpleNamespace(),
        call=lambda *a, **kw: pytest.fail('Preparation must not dispatch a review'))
    monkeypatch.setattr(http, 'ReviewService', lambda: service)
    # The real principal-route wrapper consumes an isolated auth adapter. This
    # is not a live token/User/browser acceptance claim.
    monkeypatch.setattr(http_auth, '_get_page_api_actor', lambda req: req.headers.get('X-Fixture-Actor'))
    route = next(r for r in http.routes() if r.path.endswith('/reviews/prepare'))
    assert route.method == 'POST' and route.phase == 'dual-auth'
    assert not any(r.method == 'GET' and r.path == route.path for r in http.routes())
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self): route.handler(self, urlparse(self.path), 'ignored-untrusted-actor')
        def do_GET(self): self.send_error(404)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def request(who, body=None):
        req = Request(f'http://127.0.0.1:{server.server_port}{route.path}',
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json', **({'X-Fixture-Actor': who} if who else {})})
        with urlopen(req) as response:
            return json.load(response)['review']
    body = {'quality': 'high', 'kind': 'page', 'task': 'Understand the core dynamical relation'}
    try:
        for who, payload, status in [(None, body, 401), ('fixture-user', body, 403),
                (actor, {**body, 'quality': 'medium'}, 400),
                (actor, {**body, 'kind': ['page']}, 400),
                (actor, {**body, 'responsible_resident_id': denied_actor}, 400)]:
            before = graph(lineage)
            with pytest.raises(HTTPError) as error: request(who, payload)
            assert error.value.code == status and graph(lineage) == before
        result = request(actor, body)
        assert result['status'] == 'prepared' and len(set(result['reviewers'].values())) == 2
        block = next(b for b in graph(lineage)['function_blocks'] if b['id'] == result['family_id'])
        assert set(block['family']['members']) == {actor, *result['reviewers'].values()}
        before = graph(lineage)
        assert request(actor, body) == result and graph(lineage) == before
        with pytest.raises(HTTPError) as error: request(actor)
        assert error.value.code == 404 and graph(lineage) == before
        original = family.permission_system.allows
        monkeypatch.setattr(family.permission_system, 'allows',
            lambda current, name, args: False if name == 'CreateResident' and current == denied_actor else original(current, name, args))
        with pytest.raises(HTTPError) as error: request(denied_actor, body)
        assert error.value.code == 403 and graph(lineage) == before
        assert not service.root.exists()  # No artifact/review records or delivery.
    finally:
        server.shutdown(); server.server_close(); thread.join()
