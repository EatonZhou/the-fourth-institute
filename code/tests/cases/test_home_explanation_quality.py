"""Quality/review owner contracts over real HTTP, with isolated fixture principals.

Fake ISA acceptance is not evidence that a live Resident reviewed a movie.
"""
import copy
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlparse

import pytest
from management_portal.home_explanation_review import ReviewService, AuthoringService
from management_portal.home_explanation_media import Media
from management_portal.home_explanation_quality import prepare_plan, policy

AUTHOR = 'resident_111111111111'
CHECKER = 'resident_222222222222'
RID = 'workspace_0123456789ab'
sha = lambda value: hashlib.sha256(value).hexdigest()


class Resources:
    def __init__(self, root):
        self.root, self.denied = root, set()

    def get(self, address, *, actor):
        if actor in self.denied:
            raise PermissionError('denied')
        return {'attributes': {'path': str(self.root), 'directory': {'owner_id': AUTHOR}}}

    def resolve(self, address, *, actor):
        self.get(address, actor=actor)
        self.path(address).stat()
        return {}

    def path(self, address):
        assert address.startswith(RID + '/file/')
        return self.root / address.split('/file/', 1)[1]

    def read_content(self, address, *, actor, max_bytes):
        self.get(address, actor=actor)
        return self.path(address).read_bytes()[:max_bytes], 'application/json', {}

    def notify_source_changed(self, **kw):
        pass


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from management_portal import home_explanation_media as module, home_explanation_http as http
    from management_portal.society_service import lineage_access_policy
    from management_portal import society_service
    from types import SimpleNamespace
    def creation_allowed(resident_id, actor, message_id=''):
        assert message_id == ''
        return resident_id == AUTHOR and actor in {AUTHOR, 'fixture-user'}
    monkeypatch.setattr(society_service, 'get_society_service', lambda: SimpleNamespace(
        resident=SimpleNamespace(reporting_creation_allowed=creation_allowed)))
    root = tmp_path/'workspace'; root.mkdir()
    runtime = tmp_path/'runtime'; runtime.mkdir()
    resources = Resources(root)
    calls = []
    def isa(actor, name, args, key=''):
        calls.append((actor, name, args, key))
        assert name != 'SearchResidents'
        return {'message_id': 'msg_' + sha(key.encode())[:32]}
    def ensure(resident_id, actor, *, kind, reviewer_id=''):
        return {'family_id': 'fbi_111111111111', 'lineage_id': 'lineage_111111111111',
                'reviewer_id': reviewer_id or CHECKER, 'reviewers': {'page': CHECKER, 'video': CHECKER}}
    reviews = ReviewService(root=tmp_path/'reviews', resources=resources, call=isa, ensure=ensure)
    authoring = AuthoringService(root=tmp_path/'authoring', resources=resources, call=isa, ensure=ensure)
    renders = []
    def render(plan, stage, audio):
        renders.append(plan)
        for name, value in [('video.mp4', b'video:' + plan['title'].encode()), ('captions.vtt', b'WEBVTT'), ('transcript.txt', b'transcript')]:
            (stage/name).write_bytes(value)
        return {'duration': 2}
    media = Media(resources, render)
    monkeypatch.setattr(module, 'portal_path', lambda area: runtime)
    monkeypatch.setattr(module.review, 'ReviewService', lambda **kw: reviews)
    monkeypatch.setattr(module.review, 'AuthoringService', lambda **kw: authoring)
    monkeypatch.setattr(http, 'AuthoringService', lambda **kw: authoring)
    monkeypatch.setattr(http, 'ReviewService', lambda **kw: reviews)
    monkeypatch.setattr(http, 'Media', lambda: media)
    monkeypatch.setattr(lineage_access_policy, 'workspace_reaches', lambda actor, *args, **kwargs: actor == AUTHOR and actor not in resources.denied)
    def put(name, content):
        value = json.dumps(content, ensure_ascii=False).encode() if isinstance(content, dict) else content
        (root/name).write_bytes(value)
        return {'address': RID + '/file/' + name, 'sha256': sha(value)}
    def package(n, visual=True, authored=True, qualities=('medium', 'high')):
        audio = put('voice.wav', b'fixed audio')
        scene = {'title': '信息汇聚', 'speech': '来源的信息进入当前对象', 'start': 0, 'end': 2}
        plan = {'schema': 'portal-explanation-video/v1', 'title': '固定版本'+str(n), 'audio': audio,
                'cues': [{'start': 0, 'end': 2, 'text': '来源的信息进入当前对象'}], 'scenes': [scene]}
        if visual:
            scene['visual'] = {'kind': 'sequence', 'condition': '同一来源',
                'data': {'items': [{'id': 'a', 'label': '来源', 'detail': '解释信息的来源'+str(n)}], 'note': '观察后保留画面'},
                'events': [{'at': .1, 'duration': 0, 'stage': 'focus', 'index': 0, 'cue': 0}]}
        else:
            plan['nodes'] = [{'id': 'a', 'label': '来源', 'x': .1, 'y': .2}, {'id': 'b', 'label': '目标', 'x': .8, 'y': .6}]
            scene['edges'] = [{'from': 'a', 'to': 'b', 'weight': 1}]
        ref = put(f'manifest{n}.json', {'schema': 'portal-explanation/v1', 'quality': 'high', 'title': '固定版',
                                       'video_plan': put(f'plan{n}.json', plan)})
        if visual and authored:
            source = package(-n-100, False, False)
            for quality in qualities:
                task = authoring.submit(dict(kind='explanation_video', ref=source['address'], version=source['sha256'],
                    digest=source['sha256'], author=AUTHOR, quality=quality, assets=[source]), AUTHOR)
                media.accept_authoring(**ref, actor=AUTHOR, quality=quality, authoring_id=task['id'])
        return ref
    def done(ref, quality):
        for _ in range(100):
            state = media.status(**ref, actor=AUTHOR, quality=quality)
            if state and state['status'] != 'generating' and not media.active(media.context(**ref, actor=AUTHOR, quality=quality)):
                return state
            time.sleep(.01)
        pytest.fail('media worker did not finish')
    return resources, reviews, media, calls, renders, put, package, done


@pytest.fixture
def video_review_package(rig):
    from management_portal import home_explanation_media as module
    resources, reviews, media, calls, renders, put, package, done = rig
    def create(number, **fields):
        original = package(number, authored=False)
        manifest = json.loads(resources.path(original['address']).read_bytes())
        ref = put(f'fixed-review-{number}.json', {**manifest, **fields})
        source = package(-number-1000, False, False)
        task = module.review.AuthoringService().submit(dict(kind='explanation_video',
            ref=source['address'], version=source['sha256'], digest=source['sha256'],
            author=AUTHOR, quality='high', assets=[source]), AUTHOR)
        media.accept_authoring(**ref, actor=AUTHOR, quality='high', authoring_id=task['id'])
        return ref
    return create


def test_video_review_freezes_declared_dependencies_and_not_private_neighbors(rig, video_review_package):
    from management_portal.home_explanation_http import _page_subject
    resources, reviews, media, calls, renders, put, package, done = rig
    alignment = put('alignment.json', {'cues': [0, 2], 'source': 'synthetic fixture'})
    supplement = put('notes.md', b'Explanation and actual timing.')
    interaction = put('diagram.html', b'<p>Observed relation</p>')
    video = {**put('authored.mp4', b'preselected video'),
             'captions': put('authored.vtt', b'WEBVTT\n'), 'transcript': put('authored.txt', b'words')}
    private = put('not-selected.txt', b'private neighbor/provenance, not shared')
    ref = video_review_package(301, supplement=supplement, interaction=interaction, video=video,
                              review_assets=[alignment, supplement], sources=[private])
    reads = []
    original_read = resources.read_content
    def read(address, **kwargs):
        reads.append((address, kwargs['actor']))
        return original_read(address, **kwargs)
    resources.read_content = read
    media.start(**ref, actor=AUTHOR, quality='high')
    ready = done(ref, 'high')
    assert ready['status'] == 'ready' and ready['review']['status'] == 'submitted'
    record = reviews.read(ready['review']['id'], AUTHOR)
    manifest = json.loads(resources.path(ref['address']).read_bytes())
    plan = json.loads(resources.path(manifest['video_plan']['address']).read_bytes())
    expected = [ref, manifest['video_plan'], plan['audio'], supplement, interaction,
                video, video['captions'], video['transcript'], alignment]
    page = _page_subject(media, **ref, actor=AUTHOR, creating=True)
    assert {x['address'] for x in page['assets']} == {x['address'] for x in expected}
    entries = record['handoff']['subject']['entries']
    generated = media._state(media.context(**ref, actor=AUTHOR, quality='high'), AUTHOR)['assets']
    assert {x['address'] for x in entries} == ({x['address'] for x in expected} |
        {RID+'/file/explanation-media/'+ref['sha256']+'-high/'+name for name in generated})
    assert private['address'] not in {address for address, _actor in reads}
    assert all(actor == AUTHOR for _address, actor in reads)
    # Checker cannot read originals; each selected byte is available from the
    # independent copy, even after an original source subsequently changes.
    resources.denied.add(CHECKER)
    with pytest.raises(PermissionError):
        resources.read_content(alignment['address'], actor=CHECKER, max_bytes=1000)
    original_alignment = resources.path(alignment['address']).read_bytes()
    resources.path(alignment['address']).write_bytes(b'changed after submission')
    with reviews.asset(record['id'], alignment['sha256'], CHECKER) as (data, entry, check_live):
        assert data == original_alignment
        check_live()
    with pytest.raises(FileNotFoundError):
        with reviews.asset(record['id'], private['sha256'], CHECKER): pass


@pytest.mark.parametrize('failure', ['digest', 'permission'])
def test_video_review_rejects_unreadable_or_changed_explicit_dependency(rig, video_review_package, failure):
    resources, reviews, media, calls, renders, put, package, done = rig
    dependency = put('alignment-private.json', {'start': 0, 'end': 2})
    ref = video_review_package(302, review_assets=[dependency])
    if failure == 'digest':
        resources.path(dependency['address']).write_bytes(b'tampered')
    else:
        original_resolve = resources.resolve
        def resolve(address, *, actor):
            if address == dependency['address']:
                assert actor == AUTHOR
                raise PermissionError('selected file is not shared with submitter')
            return original_resolve(address, actor=actor)
        resources.resolve = resolve
    before = len([x for x in calls if x[1] == 'DeliverMessage' and x[2].get('resident_id') == CHECKER])
    media.start(**ref, actor=AUTHOR, quality='high')
    ready = done(ref, 'high')
    assert ready['status'] == 'ready' and ready['review']['status'] in {'submission_failed', 'unavailable'}
    stored = media._state(media.context(**ref, actor=AUTHOR, quality='high'), AUTHOR)
    assert stored['review']['status'] == 'submission_failed'
    assert not ready['quality_met']
    assert len([x for x in calls if x[1] == 'DeliverMessage' and x[2].get('resident_id') == CHECKER]) == before
    assert not list(reviews.root.glob('*-subject'))


def test_new_dependency_selection_does_not_expand_old_blocked_resume(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    ref = package(303)
    media.start(**ref, actor=AUTHOR, quality='high')
    new = reviews.read(done(ref, 'high')['review']['id'], AUTHOR)
    old_subject = copy.deepcopy(new['subject'])
    old_subject['assets'] = [x for x in old_subject['assets'] if '/explanation-media/' in x['address']]
    old_subject['digest'] = sha(json.dumps(old_subject['assets'], sort_keys=True).encode())
    old = reviews.submit(old_subject, AUTHOR)
    reviews.verdict(old['id'], CHECKER, digest=old_subject['digest'], status='blocked', evidence=[], feedback='Missing fixed plan and audio.')
    old = reviews.read(old['id'], AUTHOR)
    snapshot = copy.deepcopy(old['handoff']['subject'])
    ctx = media.context(**ref, actor=AUTHOR, quality='high')
    media._save(ctx, AUTHOR, {**media._state(ctx, AUTHOR), 'review': old})
    before = len(calls)
    retried = media.retry_review(**ref, actor=AUTHOR, quality='high')['review']
    assert retried['id'] == old['id'] and retried['status'] == 'blocked'
    assert len(calls) == before
    resumed = reviews.resume(old['id'], AUTHOR)
    assert resumed['id'] != new['id'] and resumed['subject']['assets'] == old_subject['assets']
    assert resumed['handoff']['subject'] == snapshot
    retained = reviews.read(old['id'], AUTHOR)
    assert retained['status'] == 'blocked' and retained['handoff']['subject'] == snapshot
    assert retained['feedback'] == 'Missing fixed plan and audio.'
    assert len(new['handoff']['subject']['entries']) > len(snapshot['entries'])


def test_review_dependency_selection_rejects_conflicts_paths_and_limits(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    ref = package(304)
    ctx = media.context(**ref, actor=AUTHOR)
    for extras in ([{**ref, 'sha256': '0'*64}], [{**ref, 'address': RID+'/file/../secret'}], [ref]*33, 'all'):
        with pytest.raises(ValueError):
            media.review_assets({**ctx, 'manifest': {**ctx['manifest'], 'review_assets': extras}})
    with pytest.raises(ValueError, match='40'):
        media.review_assets(ctx, [{'address': RID+f'/file/generated-{i}', 'sha256': 'a'*64} for i in range(40)])


def test_video_delivery_retry_retains_preexisting_fixed_selection(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    ref = package(305)
    media.start(**ref, actor=AUTHOR, quality='high')
    new = reviews.read(done(ref, 'high')['review']['id'], AUTHOR)
    old_subject = copy.deepcopy(new['subject'])
    old_subject['assets'] = [x for x in old_subject['assets'] if '/explanation-media/' in x['address']]
    old_subject['digest'] = sha(json.dumps(old_subject['assets'], sort_keys=True).encode())
    original_call = reviews.call
    def fail(actor, name, args, key=''):
        if name == 'DeliverMessage':
            raise RuntimeError('temporary delivery failure')
        return original_call(actor, name, args, key)
    reviews.call = fail
    old = reviews.submit(old_subject, AUTHOR)
    assert old['status'] == 'submission_failed'
    snapshot = copy.deepcopy(old['handoff']['subject'])
    ctx = media.context(**ref, actor=AUTHOR, quality='high')
    media._save(ctx, AUTHOR, {**media._state(ctx, AUTHOR), 'review': old})
    reviews.call = original_call
    retried = media.retry_review(**ref, actor=AUTHOR, quality='high')['review']
    assert retried['id'] == old['id'] and retried['status'] == 'submitted'
    stored = reviews.read(old['id'], AUTHOR)
    assert stored['subject']['assets'] == old_subject['assets']
    assert stored['handoff']['subject'] == snapshot


def test_page_review_identity_and_planless_video_request(rig):
    from management_portal.home_explanation_http import _page_subject
    resources, reviews, media, calls, renders, put, package, done = rig
    ref = put('page-only.json', {'schema': 'portal-explanation/v1', 'quality': 'high',
        'title': '先理解因果', 'supplement': put('reading.md', b'Explain the cause.')})
    subject = _page_subject(media, **ref, actor=AUTHOR)
    assert reviews.read(reviews.identifier(subject), AUTHOR) is None
    assert not calls and not renders
    submitted = reviews.submit(subject, AUTHOR)
    # The fixed target lookup and author submission identify the same review.
    assert reviews.read(reviews.identifier(_page_subject(media, **ref, actor=AUTHOR)), AUTHOR)['id'] == submitted['id']
    count = len(calls)
    with pytest.raises(ValueError, match='内容已变化'):
        _page_subject(media, ref['address'], '0'*64, AUTHOR)
    resources.denied.add(CHECKER)
    with pytest.raises(PermissionError):
        _page_subject(media, **ref, actor=CHECKER)
    assert len(calls) == count
    result = media.start(**ref, actor=AUTHOR, quality='high')
    assert result['status'] == 'awaiting_authoring' and result['authoring']['status'] == 'submitted'
    assert calls[-1][2]['resident_id'] == AUTHOR and not renders


def test_quality_binds_artifacts_and_enforces_authored_visual_policy(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    legacy = package(1, False)
    assert media.status(**legacy, actor=AUTHOR, quality='high') is None
    assert not (resources.root/'explanation-media').exists()
    request = media.start(**legacy, actor=AUTHOR, quality='medium')
    assert request['status'] == 'awaiting_authoring' and request['authoring']['status'] == 'submitted'
    payload = json.loads(calls[-1][2]['message']['content_parts'][0]['text'])
    assert payload['policy']['quality'] == 'medium' and '3Blue1Brown' in payload['policy']['guidance']
    assert '实际创作或修改分镜' in payload['requirements']
    assert calls[-1][2]['resident_id'] == AUTHOR
    before_author = len(calls)
    assert media.status(**legacy, actor=AUTHOR, quality='medium')['authoring']['id'] == request['authoring']['id']
    media.start(**legacy, actor=AUTHOR, quality='medium')
    assert len(calls) == before_author
    media.start(**legacy, actor=AUTHOR, quality='low')
    low = done(legacy, 'low')
    assert low['status'] == 'ready' and low['review']['status'] == 'not_required'
    ref = package(2)
    medium = media.start(**ref, actor=AUTHOR, quality='medium')
    done(ref, 'medium')
    high = media.start(**ref, actor=AUTHOR, quality='high')
    high_ready = done(ref, 'high')
    assert medium['job_id'] != high['job_id']
    assert high_ready['review']['status'] == 'submitted' and not high_ready['quality_met']
    assert [p['_quality'] for p in renders] == ['low', 'medium', 'high']
    assert renders[1]['scenes'][0]['visual']['events'][0]['cue'] == 0
    before = len(calls), len(renders)
    assert media.start(**ref, actor=AUTHOR, quality='high')['job_id'] == high['job_id']
    assert media.status(**ref, actor=AUTHOR, quality='high')['job_id'] == high['job_id']
    assert (len(calls), len(renders)) == before
    assert 'quality=high' in high_ready['video_url']
    assert '3Blue1Brown' in policy('medium')['guidance']


def test_http_rejection_author_notification_new_version_rereview_and_recovery(rig, monkeypatch):
    from management_portal import home_explanation_http as http
    resources, reviews, media, calls, renders, put, package, done = rig
    # Real TCP HTTP and production handlers; fixture principals are explicit.
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def handle_request(self):
            parsed = urlparse(self.path)
            actor = self.headers.get('X-Fixture-Actor', '')
            if parsed.path.startswith('/api/home-explanation/reviews'):
                http._reviews(self, parsed, actor)
            elif parsed.path.startswith('/api/home-explanation/authoring'):
                http._authoring(self, parsed, actor)
            elif parsed.path.endswith('/media/authoring'):
                http._accept_authoring(self, parsed, actor)
            else:
                http._media(self, parsed, actor)
        do_GET = do_POST = handle_request
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def request(path, body=None, actor=AUTHOR):
        req = Request(f'http://127.0.0.1:{server.server_port}/api/home-explanation/'+path,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={'Content-Type': 'application/json', 'X-Fixture-Actor': actor})
        with urlopen(req) as response: return json.load(response)
    try:
        page_ref = put('page-http.json', {'schema': 'portal-explanation/v1', 'quality': 'high',
            'title': 'A fixed explanation', 'supplement': put('page-http.md', b'Cause and effect.')})
        page_query = 'reviews?address='+page_ref['address']+'&sha256='+page_ref['sha256']
        before_page = len(calls)
        assert request(page_query)['review'] is None
        assert len(calls) == before_page
        submitted_page = request('reviews', page_ref)['review']
        after_page = len(calls)
        assert request(page_query)['review']['id'] == submitted_page['id']
        assert len(calls) == after_page
        resources.denied.add(CHECKER)
        with pytest.raises(HTTPError) as forbidden_page:
            request(page_query, actor=CHECKER)
        assert forbidden_page.value.code == 403
        resources.denied.remove(CHECKER)
        legacy = package(0, False)
        author_request = request('media', {**legacy, 'quality': 'medium'})['media']['authoring']
        assert author_request['status'] == 'submitted'
        count = len(calls)
        assert request('authoring?id='+author_request['id'])['authoring']['subject']['quality'] == 'medium'
        assert len(calls) == count
        ref = package(1, qualities=('high',))
        completion = {**ref, 'quality': 'medium', 'authoring_id': author_request['id']}
        with pytest.raises(HTTPError) as not_author:
            request('media/authoring', completion, CHECKER)
        assert not_author.value.code == 403
        assert request('media/authoring', completion)['authoring']['authoring_proof']['resident_id'] == AUTHOR
        request('media', {**ref, 'quality': 'high'})
        state = done(ref, 'high'); review_id = state['review']['id']; digest = state['review']['digest']
        evidence = put('review1.md', b'Actually inspected fixture: missing explanation of cause; needs revision.')
        verdict = {'review_id': review_id, 'digest': digest, 'status': 'changes_requested', 'evidence': [evidence], 'feedback': 'Add a concrete cause and contrast.'}
        with pytest.raises(HTTPError) as denied:
            request('reviews/verdict', verdict)
        assert denied.value.code == 403
        rejected = request('reviews/verdict', verdict, CHECKER)['review']
        assert rejected['feedback_delivery']['status'] == 'delivered'
        assert calls[-1][0] == CHECKER and calls[-1][2]['resident_id'] == AUTHOR
        assert 'previous_review_id' in calls[-1][2]['message']['content_parts'][0]['text']
        count = len(calls)
        request('reviews/verdict', verdict, CHECKER)
        assert len(calls) == count
        changed = package(2)
        request('media', {**changed, 'quality': 'high', 'previous_review_id': review_id})
        next_state = done(changed, 'high'); record = next_state['review']
        assert record['id'] != review_id and record['round'] == 2
        assert not next_state['quality_met']
        passed = {'review_id': record['id'], 'digest': record['digest'], 'status': 'passed',
                  'evidence': [put('review2.md', b'Fixed fixture version inspected: cause and contrast present.')], 'feedback': 'Fixed.'}
        with pytest.raises(HTTPError):
            request('reviews/verdict', {**passed, 'digest': digest}, CHECKER)
        request('reviews/verdict', passed, CHECKER)
        assert done(changed, 'high')['quality_met']
        before = len(calls)
        assert request('reviews?id='+review_id)['review']['status'] == 'changes_requested'
        assert request('reviews?id='+record['id'])['review']['status'] == 'passed'
        assert len(calls) == before
        # Exact digest/old pass cannot certify a changed file.
        private_subject = reviews.read(record['id'], AUTHOR)['subject']
        resources.path(private_subject['assets'][0]['address']).write_bytes(b'tampered')
        assert reviews.read(record['id'], AUTHOR)['status'] == 'passed'  # immutable handoff audit
        assert reviews.current(record['id'], AUTHOR)['status'] == 'invalidated'
        assert reviews.submit(private_subject, AUTHOR)['status'] == 'invalidated'
        assert not done(changed, 'high')['quality_met']
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_cancel_late_worker_and_review_cannot_overwrite(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    started, resume = threading.Event(), threading.Event()
    original = media.renderer
    def slow(*args):
        started.set(); resume.wait(3); return original(*args)
    media.renderer = slow
    ref = package(1)
    calls.clear()  # The package fixture has already completed its authoring requests.
    media.start(**ref, actor=AUTHOR, quality='high'); started.wait(1)
    assert media.cancel(**ref, actor=AUTHOR, quality='high')['status'] == 'cancelled'
    resume.set()
    assert done(ref, 'high')['status'] == 'cancelled'
    assert not calls
    media.renderer = original
    ref = package(2); media.start(**ref, actor=AUTHOR, quality='high')
    record = done(ref, 'high')['review']
    reviews.cancel(record['id'], AUTHOR)
    with pytest.raises(ValueError, match='已结束'):
        reviews.verdict(record['id'], CHECKER, digest=record['digest'], status='passed',
                        evidence=[put('proof.md', b'read')])
    assert reviews.read(record['id'], AUTHOR)['status'] == 'cancelled'


def test_permissions_missing_reviewer_and_retries_remain_failure(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    ref = package(1)
    reviews.call = lambda *args: {'residents': []}
    media.start(**ref, actor=AUTHOR, quality='high')
    state = done(ref, 'high')
    assert state['status'] == 'ready' and state['review']['status'] == 'submission_failed'
    for _ in range(5): media.retry_review(**ref, actor=AUTHOR, quality='high')
    assert reviews.read(done(ref, 'high')['review']['id'], AUTHOR)['attempts'] == 3
    resources.denied.add(AUTHOR)
    with pytest.raises(PermissionError): media.status(**ref, actor=AUTHOR, quality='high')


def test_rejected_versions_continue_past_three_rounds_and_require_real_predecessor(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    def subject(n, previous=None):
        asset = put(f'bounded{n}.md', f'Actual changed content {n}'.encode())
        return dict(kind='explanation', ref=asset['address'], version=n, digest=asset['sha256'],
                    quality='high', author=AUTHOR, assets=[asset], **({'previous_review_id': previous} if previous else {}))
    with pytest.raises(ValueError, match='不存在'):
        reviews.submit(subject(0, '0'*64), AUTHOR)
    original_ensure = reviews.ensure
    reviews.ensure = lambda *args, **kwargs: {'family_id': 'fbi_111111111111',
        'lineage_id': 'lineage_111111111111', 'reviewer_id': AUTHOR, 'reviewers': {'page': AUTHOR, 'video': AUTHOR}}
    assert reviews.submit(subject(0), AUTHOR)['status'] == 'submission_failed'
    reviews.ensure = original_ensure
    previous = None
    evidence = put('bounded-review.md', b'Reviewer requires a new concrete explanatory contrast.')
    for n in range(1, 4):
        record = reviews.submit(subject(n, previous), AUTHOR)
        assert record['round'] == n and record['status'] == 'submitted'
        reviews.verdict(record['id'], CHECKER, digest=record['subject']['digest'],
                        status='changes_requested', evidence=[evidence], feedback='Modify the actual content.')
        previous = record['id']
    fourth = reviews.submit(subject(4, previous), AUTHOR)
    assert fourth['status'] == 'submitted' and fourth['round'] == 4
    passed = reviews.verdict(fourth['id'], CHECKER, digest=fourth['subject']['digest'],
        status='passed', evidence=[evidence], feedback='The corrected fixed version now meets the fixture criteria.')
    assert passed['status'] == 'passed' and passed['feedback_delivery']['status'] == 'delivered'
    assert reviews.read(fourth['id'], AUTHOR)['status'] == 'passed'
    assert reviews.read(previous, AUTHOR)['status'] == 'changes_requested'


def test_feedback_failure_is_readable_without_get_retry_and_can_retry_same_verdict(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    asset = put('feedback.md', b'A fixed explanation requiring corrections.')
    record = reviews.submit(dict(kind='explanation', ref=asset['address'], version=1,
        digest=asset['sha256'], quality='high', author=AUTHOR, assets=[asset]), AUTHOR)
    verdict = dict(digest=asset['sha256'], status='changes_requested', evidence=[], feedback='Clarify the actual cause.')
    original_call = reviews.call
    def fail_notice(*args):
        if args[1] == 'DeliverMessage':
            raise PermissionError('Fixture denies feedback delivery')
        return original_call(*args)
    reviews.call = fail_notice
    failed = reviews.verdict(record['id'], CHECKER, **verdict)
    assert failed['status'] == 'changes_requested' and failed['feedback_delivery']['status'] == 'failed'
    for _ in range(3):
        assert reviews.read(record['id'], AUTHOR)['feedback_delivery']['attempts'] == 1
    reviews.call = original_call
    delivered = reviews.verdict(record['id'], CHECKER, **verdict)
    assert delivered['feedback_delivery']['status'] == 'delivered'
    assert delivered['feedback_delivery']['attempts'] == 2
    assert calls[-1][0:2] == (CHECKER, 'DeliverMessage')
    assert calls[-1][2]['resident_id'] == AUTHOR
    before = len(calls)
    reviews.verdict(record['id'], CHECKER, **verdict)
    assert len(calls) == before


def test_verified_responsible_resident_is_not_the_user_and_cannot_review(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    asset = put('user-report.md', b'User-owned report with a domain-verified author association.')
    value = dict(kind='report', ref=asset['address'], version=1, digest=asset['sha256'],
        quality='high', author='fixture-user', assets=[asset])
    waiting = reviews.submit(value, 'fixture-user')
    assert waiting['status'] == 'awaiting_author' and not calls
    value['responsible_resident_id'] = AUTHOR
    rejected = reviews.submit(value, 'fixture-user', reviewer=AUTHOR)
    assert rejected['status'] == 'submission_failed'
    record = reviews.submit(value, 'fixture-user')
    assert record['status'] == 'submitted'
    with pytest.raises(PermissionError):
        reviews.verdict(record['id'], AUTHOR, digest=asset['sha256'], status='passed', evidence=[asset])
    result = reviews.verdict(record['id'], CHECKER, digest=asset['sha256'],
        status='changes_requested', evidence=[asset], feedback='Add the missing explanation.')
    assert result['subject']['author'] == 'fixture-user'
    assert calls[-1][2]['resident_id'] == AUTHOR and 'user_id' not in calls[-1][2]
    assert result['feedback_delivery']['status'] == 'delivered'
    assert result['status'] == 'changes_requested'  # Delivery cannot complete author work.
    assert asset['address'] not in json.dumps(calls[-1][2])
    feedback_args = next(call[2] for call in reversed(calls) if call[2].get('user_id') == 'fixture-user')
    feedback = json.loads(feedback_args['message']['content_parts'][0]['text'])
    assert feedback['feedback'] == 'Add the missing explanation.'
    with pytest.raises(PermissionError):
        reviews.read(record['id'], AUTHOR)
    assert 'CheckPermission' in feedback['next_action'] and '具体blocked' in feedback['next_action']
    assert 'temporary_credentials' not in feedback_args and 'token' not in feedback_args
    from management_portal import home_explanation_media as module
    creation = module.review.AuthoringService().submit({**value, 'kind': 'report_video', 'quality': 'medium'}, 'fixture-user')
    assert creation['status'] == 'submitted' and creation['requested_by'] == 'fixture-user'
    author_args = calls[-1][2]
    instructions = json.loads(author_args['message']['content_parts'][0]['text'])
    assert author_args['resident_id'] == AUTHOR and 'user_id' not in author_args
    assert 'CheckPermission' in instructions['requirements'] and '具体blocked' in instructions['requirements']
    assert 'UpdateResource' in instructions['requirements'] and 'visual.events' not in instructions['requirements']
    assert 'temporary_credentials' not in author_args and 'token' not in author_args


def test_medium_author_request_can_be_cancelled_without_replay(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    ref = package(1, False)
    result = media.start(**ref, actor=AUTHOR, quality='medium')
    assert result['status'] == 'awaiting_authoring' and not renders
    assert media.cancel(**ref, actor=AUTHOR, quality='medium')['status'] == 'cancelled'
    count = len(calls)
    assert media.start(**ref, actor=AUTHOR, quality='medium')['status'] == 'cancelled'
    assert len(calls) == count and not renders
    from management_portal import home_explanation_media as module
    changed = package(2)
    ctx = media.context(**changed, actor=AUTHOR, quality='medium')
    proof = media._state(ctx, AUTHOR)['authoring_proof']
    original_render = media.renderer
    def cancelled_during_render(*args):
        module.review.AuthoringService().cancel(proof['id'], AUTHOR)
        return original_render(*args)
    media.renderer = cancelled_during_render
    media.start(**changed, actor=AUTHOR, quality='medium')
    assert done(changed, 'medium')['status'] == 'failed'
    assert not (ctx['root'].joinpath(*ctx['output'])/'video.mp4').exists()


def test_review_module_has_no_unconditional_unix_lock_import(monkeypatch):
    import builtins
    import runpy
    import management_portal.home_explanation_review as module
    original = builtins.__import__
    def no_unix(name, *args, **kwargs):
        if name == 'fcntl':
            raise ImportError('Windows fixture has no fcntl')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_unix)
    loaded = runpy.run_path(module.__file__)
    assert loaded['ReviewService'] and loaded['lock_stream']


def test_valid_old_plan_needs_actual_author_revision_for_new_quality(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    original = package(41, authored=False)
    pending = media.start(**original, actor=AUTHOR, quality='medium')
    assert pending['status'] == 'awaiting_authoring' and not renders
    task = pending['authoring']
    old_manifest = json.loads(resources.path(original['address']).read_text())
    retag = put('retagged.json', {**old_manifest, 'title': 'Only renamed title'})
    with pytest.raises(ValueError, match='必须修改实际'):
        media.accept_authoring(**retag, actor=AUTHOR, quality='medium', authoring_id=task['id'])
    changed = package(42, authored=False)
    with pytest.raises(PermissionError):
        media.accept_authoring(**changed, actor=CHECKER, quality='medium', authoring_id=task['id'])
    accepted = media.accept_authoring(**changed, actor=AUTHOR, quality='medium', authoring_id=task['id'])
    assert accepted['status'] == 'authored'
    assert accepted['authoring_proof']['plan_digest'] != media.plan_digest(media.context(**original, actor=AUTHOR)['plan'])
    linked = media.status(**original, actor=AUTHOR, quality='medium')
    assert linked['status'] == 'authored' and linked['result_target'] == changed
    media.start(**changed, actor=AUTHOR, quality='medium')
    assert done(changed, 'medium')['status'] == 'ready'
    linked = media.status(**original, actor=AUTHOR, quality='medium')
    assert linked['status'] == 'ready' and linked['result_target'] == changed
    assert changed['sha256'] in linked['video_url'] and original['sha256'] not in linked['video_url']
    assert media.status(**original, actor=AUTHOR, quality='high') is None
    # Older completed tasks have a target-side proof but no source result link.
    # The same authenticated completion repairs the link without touching media.
    from management_portal import home_explanation_media as module
    owner = module.review.AuthoringService()
    record = owner.read(task['id'], AUTHOR); record.pop('result'); owner._save(record)
    assert media.status(**original, actor=AUTHOR, quality='medium')['status'] == 'awaiting_authoring'
    ctx = media.context(**changed, actor=AUTHOR, quality='medium')
    before = copy.deepcopy(media._state(ctx, AUTHOR))
    media.accept_authoring(**changed, actor=AUTHOR, quality='medium', authoring_id=task['id'])
    assert media._state(ctx, AUTHOR) == before
    assert media.status(**original, actor=AUTHOR, quality='medium')['job_id'] == before['job_id']
    with pytest.raises(ValueError, match='另一固定结果'):
        media.accept_authoring(**package(43, authored=False), actor=AUTHOR, quality='medium', authoring_id=task['id'])
    read = resources.read_content
    def denied_target(address, **kwargs):
        if address == changed['address']: raise PermissionError('target revoked')
        return read(address, **kwargs)
    resources.read_content = denied_target
    with pytest.raises(PermissionError, match='target revoked'):
        media.status(**original, actor=AUTHOR, quality='medium')
    resources.read_content = read
    count = len(calls)
    assert media.status(**changed, actor=AUTHOR, quality='medium')['status'] == 'ready'
    assert len(calls) == count
    high = media.start(**changed, actor=AUTHOR, quality='high')
    assert high['status'] == 'awaiting_authoring' and high['authoring']['id'] != task['id']
    assert len(renders) == 1
