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
from management_portal.home_explanation_review import ReviewService
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
    root = tmp_path/'workspace'; root.mkdir()
    runtime = tmp_path/'runtime'; runtime.mkdir()
    resources = Resources(root)
    calls = []
    def isa(actor, name, args, key=''):
        calls.append((actor, name, args, key))
        if name == 'SearchResidents':
            return {'residents': [{'resident_id': CHECKER, 'can_send': True, 'title': args.get('q', '')}]}
        return {'message_id': 'msg_' + sha(key.encode())[:32]}
    reviews = ReviewService(root=tmp_path/'reviews', resources=resources, call=isa)
    renders = []
    def render(plan, stage, audio):
        renders.append(plan)
        for name, value in [('video.mp4', b'video:' + plan['title'].encode()), ('captions.vtt', b'WEBVTT'), ('transcript.txt', b'transcript')]:
            (stage/name).write_bytes(value)
        return {'duration': 2}
    media = Media(resources, render)
    monkeypatch.setattr(module, 'portal_path', lambda area: runtime)
    monkeypatch.setattr(module.review, 'ReviewService', lambda **kw: reviews)
    monkeypatch.setattr(http, 'ReviewService', lambda **kw: reviews)
    monkeypatch.setattr(http, 'Media', lambda: media)
    monkeypatch.setattr(lineage_access_policy, 'workspace_reaches', lambda actor, *args, **kwargs: actor == AUTHOR and actor not in resources.denied)
    def put(name, content):
        value = json.dumps(content, ensure_ascii=False).encode() if isinstance(content, dict) else content
        (root/name).write_bytes(value)
        return {'address': RID + '/file/' + name, 'sha256': sha(value)}
    def package(n, visual=True):
        audio = put('voice.wav', b'fixed audio')
        scene = {'title': '信息汇聚', 'speech': '来源的信息进入当前对象', 'start': 0, 'end': 2}
        plan = {'schema': 'portal-explanation-video/v1', 'title': '固定版本'+str(n), 'audio': audio,
                'cues': [{'start': 0, 'end': 2, 'text': '来源的信息进入当前对象'}], 'scenes': [scene]}
        if visual:
            scene['visual'] = {'kind': 'sequence', 'condition': '同一来源',
                'data': {'items': [{'id': 'a', 'label': '来源', 'detail': '解释信息的来源'}], 'note': '观察后保留画面'},
                'events': [{'at': .1, 'duration': 0, 'stage': 'focus', 'index': 0, 'cue': 0}]}
        else:
            plan['nodes'] = [{'id': 'a', 'label': '来源', 'x': .1, 'y': .2}, {'id': 'b', 'label': '目标', 'x': .8, 'y': .6}]
            scene['edges'] = [{'from': 'a', 'to': 'b', 'weight': 1}]
        return put(f'manifest{n}.json', {'schema': 'portal-explanation/v1', 'quality': 'high', 'title': '固定版',
                                       'video_plan': put(f'plan{n}.json', plan)})
    def done(ref, quality):
        for _ in range(100):
            state = media.status(**ref, actor=AUTHOR, quality=quality)
            if state and state['status'] != 'generating' and not media.active(media.context(**ref, actor=AUTHOR, quality=quality)):
                return state
            time.sleep(.01)
        pytest.fail('media worker did not finish')
    return resources, reviews, media, calls, renders, put, package, done


def test_quality_binds_artifacts_and_enforces_authored_visual_policy(rig):
    resources, reviews, media, calls, renders, put, package, done = rig
    legacy = package(1, False)
    assert media.status(**legacy, actor=AUTHOR, quality='high') is None
    assert not (resources.root/'explanation-media').exists()
    with pytest.raises(ValueError, match='visual.events'):
        media.start(**legacy, actor=AUTHOR, quality='medium')
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
        ref = package(1)
        request('media', {**ref, 'quality': 'high'})
        state = done(ref, 'high'); review_id = state['review']['id']; digest = state['review']['subject']['digest']
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
        passed = {'review_id': record['id'], 'digest': record['subject']['digest'], 'status': 'passed',
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
        evidence2 = put('review3.md', b'new evidence')
        put('review2.md', b'changed')
        assert reviews.read(record['id'], AUTHOR)['subject']['version'] == changed['sha256']
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
        reviews.verdict(record['id'], CHECKER, digest=record['subject']['digest'], status='passed',
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
    assert done(ref, 'high')['review']['attempts'] == 3
    resources.denied.add(AUTHOR)
    with pytest.raises(PermissionError): media.status(**ref, actor=AUTHOR, quality='high')
