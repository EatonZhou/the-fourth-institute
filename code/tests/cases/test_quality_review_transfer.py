"""Selected review copies use real ResourceService/PAR over isolated PostgreSQL.

HTTP handlers are real; fixture headers select principals and ISA deliveries are
receipts, not a claim that a live model performed an independent review.
"""
import copy
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import Mock

import pytest

from management_portal.home_explanation_review import ReviewService
from tests.cases.permission_test_support import Fact, permissions

AUTHOR = 'resident_111111111111'
CHECKER = 'resident_222222222222'
OTHER = 'resident_333333333333'
SOURCE = 'workspace_111111111111'
PROOF = 'workspace_222222222222'
sha = lambda data: hashlib.sha256(data).hexdigest()


@pytest.fixture
def exchange(tmp_path, monkeypatch, permissions):
    from management_portal.resources.service import ResourceService
    from management_portal.resources.providers import install_resolvers
    from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
    from management_portal.resources.registration import ResourceRegistrationStore
    from management_portal.resources.workspace_store import get_workspace_store
    from management_portal import home_explanation_http as http
    records, roots = {}, {}
    permissions.place(*(Fact('resident', r) for r in (AUTHOR, CHECKER, OTHER)))
    for rid, actor in ((SOURCE, AUTHOR), (PROOF, CHECKER)):
        root = tmp_path / rid; root.mkdir(); roots[rid] = root
        get_workspace_store().ensure(rid, str(root), title='isolated review fixture')
        records[rid] = {'resource_id': rid, 'resource_type': 'workspace', 'title': 'private fixture',
            'source': {}, 'facets': ['workspace', 'collaboration'],
            'attributes': {'path': str(root), 'status': 'active'}, 'url': '/r/' + rid}
        permissions.place(Fact('workspace', rid))
        permissions.grant(actor, rid)
        for denied in ({AUTHOR, CHECKER, OTHER} - {actor}):
            permissions.deny(denied, rid)
    index = Mock()
    index.indexed_types.return_value = frozenset({'workspace'})
    index.has_indexed_type.side_effect = lambda kind: kind == 'workspace'
    index.get_resource.side_effect = lambda rid, **kw: copy.deepcopy(records.get(rid))
    resources = ResourceService(index_provider=index, collaboration_store=CollaborationDescriptorStore(),
        registration_store=ResourceRegistrationStore(), resolver_loader=install_resolvers)
    calls = []
    def isa(actor, instruction, args, key=''):
        calls.append((actor, instruction, args, key))
        if instruction == 'SearchResidents':
            return {'residents': [{'resident_id': args.get('resident_ids', [CHECKER])[0], 'can_send': True,
                                    'title': args.get('q', '')}]}
        return {'message_id': 'msg_' + sha(key.encode())[:32]}
    owner = ReviewService(root=tmp_path / 'reviews', resources=resources, call=isa)
    monkeypatch.setattr(http, 'ReviewService', lambda: owner)
    from management_portal.home_explanation_media import Media
    monkeypatch.setattr(http, 'Media', lambda: Media(resources))
    from management_portal import home_explanation_review as review_module
    authoring = review_module.AuthoringService(root=tmp_path / 'authoring', resources=resources, call=isa)
    monkeypatch.setattr(review_module, 'ReviewService', lambda **kw: owner)
    monkeypatch.setattr(review_module, 'AuthoringService', lambda **kw: authoring)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def handle_request(self):
            parsed = urlparse(self.path)
            actor = self.headers.get('X-Fixture-Actor', '')
            route = http._media if parsed.path.endswith('/media') else http._review_file if parsed.path.endswith('/file') else http._reviews
            route(self, parsed, actor)
        do_GET = do_POST = handle_request
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def request(actor, path, body=None, *, raw=False, headers=None):
        req = Request(f'http://127.0.0.1:{server.server_port}/api/home-explanation/'+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'X-Fixture-Actor': actor, 'Content-Type': 'application/json', **(headers or {})})
        with urlopen(req) as response:
            return response.read() if raw else json.load(response)['media' if path == 'media' else 'review']
    def put(rid, name, data):
        path = roots[rid] / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        return {'address': rid + '/file/' + name, 'sha256': sha(data)}
    artifact = put(SOURCE, 'selected/index.html', b'<button onclick="this.textContent=2">1</button>')
    put(SOURCE, 'unselected/private.txt', b'not shared')
    subject = dict(kind='explanation', ref=artifact['address'], version=artifact['sha256'],
        digest=artifact['sha256'], quality='high', author=AUTHOR, assets=[artifact], task='Operate the button')
    yield owner, resources, subject, roots, put, request, calls
    server.shutdown(); server.server_close(); thread.join()


@pytest.mark.parametrize('missing', ['record', 'continuation', 'id'])
def test_media_missing_review_never_exposes_private_cache(exchange, missing):
    from management_portal.home_explanation_media import Media
    owner, resources, subject, roots, put, request, calls = exchange
    record = owner.submit(subject, AUTHOR, CHECKER)
    cached = {**record, 'status': 'passed', 'evidence': [{'address': 'private'}]}
    if missing == 'record':
        (owner.root / (record['id'] + '.json')).unlink()
    elif missing == 'continuation':
        owner._save({**record, 'continuation_review_id': 'f' * 64})
    else:
        cached.pop('id')
    ctx = {'quality': 'high', 'ref': subject['assets'][0]}
    result = Media(resources).response(ctx, {'status': 'ready', 'review': cached}, OTHER)
    assert result['review']['status'] == 'unavailable'
    assert result['quality_met'] is False
    assert not any(result['review']['capabilities'].values())
    assert not {'id', 'subject', 'handoff', 'evidence'} & result['review'].keys()


def test_real_page_post_uses_authenticated_resident_not_workspace_owner(exchange, permissions):
    owner, resources, subject, roots, put, request, calls = exchange
    # ensure() registered an ordinary Workspace: its owner defaults to its own
    # Workspace ID, not a fabricated Resident as in the old HTTP fixture.
    manifest = put(SOURCE, 'selected/manifest.json', json.dumps({
        'schema': 'portal-explanation/v1', 'quality': 'high',
        'interaction': subject['assets'][0], 'summary': 'Operate the fixed button'
    }).encode())
    record = request(AUTHOR, 'reviews', {**manifest, 'author': OTHER,
        'responsible_resident_id': OTHER})
    assert record['status'] == 'submitted'
    assert record['subject']['author'] == AUTHOR
    assert record['subject']['responsible_resident_id'] == AUTHOR
    assert record['requested_by'] == AUTHOR
    assert request(AUTHOR, 'reviews?' + urlencode(manifest))['id'] == record['id']
    path = 'reviews/file?' + urlencode({'id': record['id'], 'sha256': subject['digest']})
    assert request(CHECKER, path, raw=True).startswith(b'<button')
    with pytest.raises(PermissionError):
        resources.read_content(manifest['address'], actor=CHECKER)
    permissions.place(Fact('user', 'isolated_author'))
    permissions.grant('isolated_author', SOURCE)
    with pytest.raises(HTTPError) as denied:
        request('isolated_author', 'reviews', {**manifest, 'author': AUTHOR})
    assert denied.value.code == 403
    new_manifest = put(SOURCE, 'selected/new-manifest.json', json.dumps({
        'schema': 'portal-explanation/v1', 'quality': 'high',
        'interaction': subject['assets'][0], 'summary': 'Another unbound package'
    }).encode())
    unbound = request('isolated_author', 'reviews', {**new_manifest,
        'author': AUTHOR, 'responsible_resident_id': AUTHOR})
    assert unbound['status'] == 'awaiting_author'
    assert unbound['subject']['author'] == 'isolated_author'
    assert not unbound['subject']['responsible_resident_id']
    assert not unbound.get('handoff')


def test_user_reads_authors_fixed_quality_and_video_task_returns_to_author(exchange, permissions):
    owner, resources, subject, roots, put, request, calls = exchange
    user = 'isolated_reader'
    permissions.place(Fact('user', user))
    permissions.grant(user, SOURCE)
    manifest = put(SOURCE, 'selected/manifest.json', json.dumps({
        'schema': 'portal-explanation/v1', 'quality': 'high',
        'interaction': subject['assets'][0], 'summary': 'Show the actual fixed button'
    }).encode())
    first = request(AUTHOR, 'reviews', manifest)
    proof = put(PROOF, 'private-review.md', b'Private inspection details')
    owner.verdict(first['id'], CHECKER, digest=manifest['sha256'], status='changes_requested',
                  evidence=[proof], feedback='Make the button label readable on narrow screens.')
    before = len(calls)
    public = request(user, 'reviews?' + urlencode(manifest))
    assert public['id'] == first['id'] and public['status'] == 'changes_requested'
    assert public['feedback'].startswith('Make the button')
    assert not any(public['capabilities'].values())
    assert not {'handoff', 'evidence', 'subject', 'requested_by', 'reviewer'} & public.keys()
    assert len(calls) == before  # Read does not submit or grant anything.
    for path in ('reviews?' + urlencode({'id': first['id']}),
                 'reviews/file?' + urlencode({'id': first['id'], 'sha256': proof['sha256'], 'group': 'evidence'})):
        with pytest.raises(HTTPError) as denied:
            request(user, path)
        assert denied.value.code == 403
    with pytest.raises(HTTPError) as denied:
        request(OTHER, 'reviews?' + urlencode(manifest))
    assert denied.value.code == 403
    result = request(user, 'media', {**manifest, 'quality': 'high', 'author': OTHER,
                                    'responsible_resident_id': OTHER})
    assert result['status'] == 'awaiting_authoring'
    task = result['authoring']
    assert task['status'] == 'submitted' and task['requested_by'] == user
    assert task['subject']['author'] == user
    assert task['subject']['responsible_resident_id'] == AUTHOR
    assert calls[-1][2]['resident_id'] == AUTHOR
    calls_before = len(calls)
    assert request(user, 'media', {**manifest, 'quality': 'high'})['authoring']['id'] == task['id']
    assert len(calls) == calls_before
    permissions.deny(user, SOURCE)
    with pytest.raises(HTTPError) as denied:
        request(user, 'reviews?' + urlencode(manifest))
    assert denied.value.code == 403  # No cached read grant from the index.


def test_package_index_backfills_only_server_records_and_follows_resume(exchange, monkeypatch):
    owner, resources, subject, roots, put, request, calls = exchange
    first = owner.submit(subject, AUTHOR)
    owner.verdict(first['id'], CHECKER, digest=subject['digest'], status='blocked', evidence=[], feedback='Pending inspection')
    binding = owner.package_binding(subject['ref'], subject['version'], AUTHOR)
    store = owner._package_store()
    (store.root / (binding['id'] + '.json')).unlink()
    with monkeypatch.context() as scoped:
        scoped.setattr(resources, 'read_content', lambda *a, **kw: pytest.fail('index must not read private source'))
        assert owner.index_existing_packages() == {'indexed': 1, 'conflicts': []}
        assert owner.index_existing_packages() == {'indexed': 0, 'conflicts': []}
    resumed = owner.resume(first['id'], AUTHOR)
    projected = owner.package_projection(subject['ref'], subject['version'], AUTHOR)
    assert projected['id'] == resumed['id'] and projected['status'] == 'submitted'
    assert projected['capabilities']['can_cancel']
    assert not projected['capabilities']['can_resume']


def test_historical_feedback_stays_private_after_indexing(exchange, permissions):
    owner, resources, subject, roots, put, request, calls = exchange
    user = 'historical-reader'
    permissions.grant(user, SOURCE)
    record = owner.submit(subject, AUTHOR)
    owner.verdict(record['id'], CHECKER, digest=subject['digest'],
                  status='changes_requested', evidence=[], feedback='private proof path: /private/checker.md')
    legacy = owner._load(record['id'])
    legacy.pop('feedback_audience')
    legacy.pop('feedback_contract')
    legacy['error'] = 'private legacy diagnostic'
    owner._save(legacy)
    binding = owner.package_binding(subject['ref'], subject['version'], AUTHOR)
    (owner._package_store().root / (binding['id'] + '.json')).unlink()
    assert owner.index_existing_packages() == {'indexed': 1, 'conflicts': []}
    projected = owner.package_projection(subject['ref'], subject['version'], user)
    assert projected['status'] == 'changes_requested'
    assert not {'feedback', 'error', 'evidence', 'handoff'} & projected.keys()
    assert owner.package_projection(subject['ref'], subject['version'], AUTHOR)['feedback'] == legacy['feedback']
    assert 'feedback_audience' not in owner._load(record['id'])


def test_legacy_delivery_retry_cannot_upgrade_feedback_audience(exchange):
    owner, resources, subject, roots, put, request, calls = exchange
    record = owner.submit(subject, AUTHOR)
    legacy = owner._load(record['id'])
    legacy.pop('feedback_audience')
    legacy.pop('feedback_contract')
    legacy['status'] = 'submission_failed'
    owner._save(legacy)
    original_call = owner.call
    def replay(actor, instruction, args, key=''):
        if instruction == 'DeliverMessage':
            assert key == 'quality-review:' + record['id']
            return {'message_id': 'msg_original_private_delivery'}
        return original_call(actor, instruction, args, key)
    owner.call = replay
    retried = owner.submit(subject, AUTHOR)
    assert retried['status'] == 'submitted'
    assert 'feedback_audience' not in retried and 'feedback_contract' not in retried
    owner.call = original_call
    owner.verdict(record['id'], CHECKER, digest=subject['digest'], status='blocked', evidence=[], feedback='Old private feedback')
    resumed = owner.resume(record['id'], AUTHOR)
    assert resumed['feedback_audience'] == 'artifact_readers'
    assert calls[-1][3].endswith(':artifact-readers-v1')
    assert 'feedback_audience' not in owner._load(record['id'])


def test_package_author_conflict_never_dispatches_a_second_author(exchange):
    owner, resources, subject, roots, put, request, calls = exchange
    owner.submit(subject, AUTHOR)
    count = len(calls)
    # Same real principal cannot silently replace the original author identity.
    conflicting = {**subject, 'author': OTHER, 'responsible_resident_id': OTHER}
    # Exercise the business owner with isolated source authorization; the HTTP
    # boundary independently checks the actual source write permission.
    original = owner._authorize
    owner._authorize = lambda s, a, verify=False: original(s, AUTHOR, verify)
    with pytest.raises(ValueError, match='不同的认证作者'):
        owner.submit(conflicting, OTHER)
    assert len(calls) == count
    assert owner.package_binding(subject['ref'], subject['version'], AUTHOR)['author'] == AUTHOR


def test_private_source_http_range_export_and_return_evidence(exchange, tmp_path):
    owner, resources, subject, roots, put, request, calls = exchange
    with pytest.raises(PermissionError):
        resources.read_content(subject['ref'], actor=CHECKER)
    record = owner.submit(subject, AUTHOR)
    assert record['status'] == 'submitted'
    path = 'reviews/file?' + urlencode({'id': record['id'], 'sha256': subject['digest']})
    assert request(CHECKER, path, raw=True, headers={'Range': 'bytes=1-4'}) == b'butt'
    with pytest.raises(HTTPError) as error:
        request(OTHER, path, raw=True)
    assert error.value.code == 403
    from scripts.submit_explanation_review import export
    export(record['id'], tmp_path/'checker-copy', CHECKER, call=request)
    assert (tmp_path/'checker-copy/assets/selected/index.html').read_bytes().startswith(b'<button')
    assert not (tmp_path/'checker-copy/assets/unselected').exists()
    original = request(CHECKER, path, raw=True)
    roots[SOURCE].joinpath('selected/index.html').write_bytes(b'changed source')
    assert request(CHECKER, path, raw=True) == original
    evidence = put(PROOF, 'review.md', b'Clicked the actual fixed button; it changed from 1 to 2.')
    with pytest.raises(PermissionError):
        resources.read_content(evidence['address'], actor=AUTHOR)
    verdict = request(CHECKER, 'reviews/verdict', {'review_id': record['id'], 'digest': subject['digest'],
        'status': 'passed', 'evidence': [evidence], 'feedback': 'Verified fixed copy.'})
    assert verdict['status'] == 'passed'
    assert owner.read(record['id'], AUTHOR)['status'] == 'passed'
    assert owner.current(record['id'], AUTHOR)['status'] == 'invalidated'
    proof_path = 'reviews/file?' + urlencode({'id': record['id'], 'sha256': evidence['sha256'], 'group': 'evidence'})
    assert request(AUTHOR, proof_path, raw=True).startswith(b'Clicked')
    with pytest.raises(HTTPError) as error:
        request(CHECKER, path, raw=True)
    assert error.value.code == 403  # Terminal verdict closes active checker asset access.
    with pytest.raises(PermissionError):
        resources.read_content(subject['ref'], actor=CHECKER)
    with pytest.raises(PermissionError):
        resources.read_content(evidence['address'], actor=AUTHOR)


def test_selection_immutability_tamper_expiry_and_cancel(exchange):
    owner, resources, subject, roots, put, request, calls = exchange
    record = owner.submit(subject, AUTHOR)
    extra = put(SOURCE, 'selected/extra.txt', b'extra')
    with pytest.raises(ValueError, match='资产清单'):
        owner.submit({**subject, 'assets': [*subject['assets'], extra]}, AUTHOR)
    for asset in ({**extra, 'address': SOURCE+'/file/../private.txt'}, subject['assets'][0]):
        with pytest.raises(ValueError):
            owner.subject({**subject, 'assets': [subject['assets'][0], asset]})
    with pytest.raises(FileNotFoundError):
        with owner.asset(record['id'], extra['sha256'], CHECKER): pass
    blob = owner.root / (record['id']+'-subject') / subject['digest']
    blob.write_bytes(b'X' * blob.stat().st_size)
    with pytest.raises(ValueError, match='摘要'):
        with owner.asset(record['id'], subject['digest'], CHECKER): pass
    record['handoff']['expires_at'] = time.time() - 1; owner._save(record)
    with pytest.raises(PermissionError):
        owner.read(record['id'], CHECKER)
    # Idempotent submit neither reopens the deadline nor dispatches again.
    before = len(calls)
    assert owner.submit(subject, AUTHOR)['handoff']['expires_at'] < time.time()
    assert len(calls) == before
    owner.cancel(record['id'], AUTHOR)
    with pytest.raises(ValueError, match='已结束'):
        owner.verdict(record['id'], CHECKER, digest=subject['digest'], status='passed', evidence=[extra])


def test_blocked_same_bytes_resume_has_new_round_and_old_verdict_is_immutable(exchange):
    owner, resources, subject, roots, put, request, calls = exchange
    old = owner.submit(subject, AUTHOR)
    owner.verdict(old['id'], CHECKER, digest=subject['digest'], status='blocked', evidence=[], feedback='Cannot inspect yet')
    with pytest.raises(PermissionError):
        owner.resume(old['id'], CHECKER)
    new = request(AUTHOR, 'reviews/resume', {'review_id': old['id']})
    assert new['id'] != old['id'] and new['round'] == 2 and new['status'] == 'submitted'
    assert new['resumes_review_id'] == old['id']
    assert owner.read(old['id'], AUTHOR)['status'] == 'blocked'
    assert owner.current(old['id'], AUTHOR)['id'] == new['id']
    count = len(calls)
    assert owner.resume(old['id'], AUTHOR)['id'] == new['id'] and len(calls) == count
    with pytest.raises(ValueError, match='已结束'):
        owner.verdict(old['id'], CHECKER, digest=subject['digest'], status='passed', evidence=[subject['assets'][0]])
    assert owner.read(new['id'], AUTHOR)['status'] == 'submitted'
    # A read in progress and cancellation share the review lock.
    started, finished = threading.Event(), threading.Event()
    def cancel():
        started.set(); request(AUTHOR, 'reviews/cancel', {'review_id': old['id']}); finished.set()
    with owner.asset(new['id'], subject['digest'], CHECKER):
        thread = threading.Thread(target=cancel); thread.start(); started.wait(1)
        assert not finished.wait(.05)
    thread.join(2); assert finished.is_set()
    with pytest.raises(PermissionError):
        with owner.asset(new['id'], subject['digest'], CHECKER): pass
    with pytest.raises(ValueError, match='取消'):
        owner.resume(old['id'], AUTHOR)
    assert owner.current(old['id'], AUTHOR)['status'] == 'cancelled'


def test_expired_round_can_only_resume_explicitly_and_replaces_checker(exchange):
    owner, resources, subject, roots, put, request, calls = exchange
    old = owner.submit(subject, AUTHOR)
    old['handoff']['expires_at'] = time.time() - 1; owner._save(old)
    count = len(calls)
    owner.submit(subject, AUTHOR)
    assert len(calls) == count
    new = owner.resume(old['id'], AUTHOR, reviewer=OTHER)
    assert owner.read(old['id'], AUTHOR)['status'] == 'blocked'
    assert new['reviewer'] == OTHER and new['status'] == 'submitted'
    with pytest.raises(PermissionError):
        with owner.asset(new['id'], subject['digest'], CHECKER): pass
    with owner.asset(new['id'], subject['digest'], OTHER) as (data, _, check_live):
        assert data.startswith(b'<button')
        # Expiry is checked again while a response is streaming.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr('management_portal.home_explanation_review.time.time', lambda: new['handoff']['expires_at']+1)
            with pytest.raises(PermissionError): check_live()


def test_snapshot_tamper_and_source_symlink_never_become_a_pass(exchange):
    owner, resources, subject, roots, put, request, calls = exchange
    file = roots[SOURCE] / 'selected/index.html'
    other = roots[SOURCE] / 'target.html'; other.write_bytes(file.read_bytes())
    file.unlink(); file.symlink_to(other)
    with pytest.raises(ValueError, match='symlink'):
        owner.submit(subject, AUTHOR)
    assert owner._load(owner.identifier(subject)) is None
    assert not any(call[1] == 'DeliverMessage' for call in calls)


def test_expired_checker_can_only_record_assetless_blocked(exchange):
    owner, resources, subject, roots, put, request, calls = exchange
    record = owner.submit(subject, AUTHOR)
    record['handoff']['expires_at'] = time.time()-1; owner._save(record)
    proof = put(PROOF, 'proof.md', b'private proof')
    for verdict, evidence in [('passed', [proof]), ('changes_requested', []), ('blocked', [proof])]:
        with pytest.raises(PermissionError):
            owner.verdict(record['id'], CHECKER, digest=subject['digest'], status=verdict, evidence=evidence)
    result = owner.verdict(record['id'], CHECKER, digest=subject['digest'], status='blocked', evidence=[], feedback='expired')
    assert result['status'] == 'blocked'


def test_responsible_third_party_gets_no_private_feedback_or_evidence(exchange):
    owner, resources, subject, roots, put, request, calls = exchange
    record = owner.submit({**subject, 'responsible_resident_id': OTHER}, AUTHOR)
    evidence = put(PROOF, 'private-opinion.md', b'selected private proof')
    result = owner.verdict(record['id'], CHECKER, digest=subject['digest'], status='changes_requested',
        evidence=[evidence], feedback='private-feedback-needle')
    notices = [call[2] for call in calls if call[1] == 'DeliverMessage']
    full = [n for n in notices if n.get('resident_id') == AUTHOR][-1]
    assert 'private-feedback-needle' in json.dumps(full)
    minimal = [n for n in notices if n.get('resident_id') == OTHER][-1]
    assert not any(word in json.dumps(minimal) for word in ['private-feedback-needle', 'private-opinion', SOURCE, PROOF, 'business_ref'])
    with pytest.raises(PermissionError): owner.read(record['id'], OTHER)
