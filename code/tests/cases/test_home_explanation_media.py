"""Direct media-owner checks; fixture actors do not stand for User/TAU evidence."""
import hashlib
import io
import json
import os
from pathlib import Path
import threading
import time
import wave

import pytest

from management_portal.home_explanation_media import Media, pointer
from management_portal.home_explanation_plan import validate
from management_portal.home_explanation_http import _file


RID = 'workspace_0123456789ab'


def plan():
    return {'schema': 'portal-explanation-video/v1', 'title': '信息汇聚',
            'audio': {'address': RID+'/file/voice.wav', 'sha256': hashlib.sha256(b'fixed-audio').hexdigest()},
            'cues': [{'start': 0, 'end': 2, 'text': '测试'}],
            'nodes': [{'id': 'a', 'label': '来源', 'x': .1, 'y': .2}, {'id': 'b', 'label': '目标', 'x': .8, 'y': .6}],
            'scenes': [{'title': '匹配与汇聚', 'speech': '信息沿连线流向目标。', 'start': 0, 'end': 2, 'edges': [{'from': 'a', 'to': 'b', 'weight': .8}],
                        'values': [{'node': 'b', 'value': 2}]}]}


def visual_plan():
    value = plan()
    value['scenes'] = [{'title': '信息汇聚', 'speech': '测试', 'start': 0, 'end': 2,
        'visual': {'kind': 'sequence', 'condition': '固定音轨',
                   'data': {'items': [{'id': 'a', 'label': '来源', 'detail': '固定输入'}], 'note': '测试'},
                   'events': [{'at': 0, 'stage': 'focus', 'index': 0, 'duration': 0, 'cue': 0}]}}]
    return value


class Resources:
    def __init__(self, root):
        self.root, self.denied, self.writes, self.notifications = root, set(), True, []

    def path(self, address, actor):
        if actor != 'reader' or address in self.denied:
            raise PermissionError('denied')
        root, suffix = address.split('/file/', 1)
        assert root == RID
        return self.root / suffix

    def read_content(self, address, *, actor, max_bytes):
        path = self.path(address, actor)
        # Production ResourceService owns no-follow reads; mirror this fence.
        from management_common import secure_paths
        with secure_paths.open_target(self.root, tuple(path.relative_to(self.root).parts)) as (fd, _):
            with os.fdopen(os.dup(fd), 'rb') as stream:
                return stream.read(max_bytes), 'application/json', {}

    def get(self, root, *, actor):
        if actor != 'reader' or root in self.denied:
            raise PermissionError('denied')
        return {'attributes': {'path': str(self.root)}}

    def resolve(self, address, *, actor):
        path = self.path(address, actor)
        path.stat()
        return {}

    def notify_source_changed(self, **kwargs):
        self.notifications.append(kwargs)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from management_portal.society_service import lineage_access_policy
    import management_portal.home_explanation_media as module
    root = tmp_path / 'workspace'
    root.mkdir()
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    service = Resources(root)
    monkeypatch.setattr(lineage_access_policy, 'workspace_reaches', lambda actor, *args, **kw: actor == 'reader' and service.writes)
    monkeypatch.setattr(module, 'portal_path', lambda area: runtime)
    # These fixtures isolate byte/lease/permission ownership. The authenticated
    # authoring gate and actual-plan revisions have their own quality contracts.
    monkeypatch.setattr(Media, 'require_authored', lambda *args: True)
    monkeypatch.setattr(module.review, 'discover', lambda *args: 'resident_aaaaaaaaaaaa')

    def put(name, value):
        data = json.dumps(value).encode() if isinstance(value, dict) else value
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {'address': f'{RID}/file/{name}', 'sha256': hashlib.sha256(data).hexdigest()}

    put('voice.wav', b'fixed-audio')
    ref = put('page.json', {'schema': 'portal-explanation/v1', 'video_plan': put('plan.json', visual_plan())})
    return service, ref, put


def fake_render(plan, stage, audio):
    (stage/'video.mp4').write_bytes(b'movie-content')
    (stage/'captions.vtt').write_text('WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n信息流动')
    (stage/'transcript.txt').write_text('信息流动')
    return {'chapters': [{'time': 0, 'title': '信息流动'}], 'duration': 2}


def done(media, ref, quality='medium'):
    until = time.monotonic()+5
    while time.monotonic() < until:
        value = media.status(**ref, actor='reader', quality=quality)
        if value and value['status'] != 'generating' and not media.active(media.context(**ref, actor='reader', quality=quality)):
            return value
        time.sleep(.01)
    pytest.fail('worker did not finish')


def test_read_is_side_effect_free_and_write_requires_permission(setup):
    service, ref, _ = setup
    media = Media(service, fake_render)
    assert media.status(**ref, actor='reader') is None
    assert not (service.root/'explanation-media').exists()
    service.writes = False
    with pytest.raises(PermissionError):
        media.start(**ref, actor='reader')
    assert not (service.root/'explanation-media').exists()
    with pytest.raises(PermissionError):
        media.status(**ref, actor='other')


def test_concurrent_generation_reopen_and_revocation(setup):
    service, ref, _ = setup
    entered, release = threading.Event(), threading.Event()
    calls = []
    def render(plan, stage, audio):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return fake_render(plan, stage, audio)
    media = Media(service, render)
    first = media.start(**ref, actor='reader')
    assert entered.wait(1)
    second = Media(service, render).start(**ref, actor='reader')
    assert second['job_id'] == first['job_id']
    release.set()
    assert done(media, ref)['status'] == 'ready'
    reopened = Media(service, render)
    assert reopened.start(**ref, actor='reader')['job_id'] == first['job_id']
    assert calls == [1]
    with reopened.file(**ref, name='video.mp4', actor='reader') as (stream, info, mime):
        assert stream.read() == b'movie-content'
        assert mime == 'video/mp4'
    service.denied.add(f'{RID}/file/plan.json')
    with pytest.raises(PermissionError):
        reopened.status(**ref, actor='reader')
    with pytest.raises(PermissionError):
        with reopened.file(**ref, name='video.mp4', actor='reader'):
            pass


def test_hash_change_symlink_and_offline_workspace(setup, tmp_path):
    service, ref, put = setup
    media = Media(service, fake_render)
    (service.root/'plan.json').write_text('{}')
    with pytest.raises(ValueError, match='内容已变化'):
        media.start(**ref, actor='reader')
    put('plan.json', plan())
    outside = tmp_path/'outside'
    outside.mkdir()
    (service.root/'explanation-media').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        media.start(**ref, actor='reader')
    assert not list(outside.iterdir())
    (service.root/'explanation-media').unlink()
    (service.root/'page.json').unlink()
    (service.root/'plan.json').unlink()
    (service.root/'voice.wav').unlink()
    service.root.rmdir()
    with pytest.raises(FileNotFoundError):
        media.start(**ref, actor='reader')
    assert not service.root.exists()


def test_failures_retry_and_worker_lease_release(setup):
    service, ref, _ = setup
    def broken(*args):
        raise RuntimeError('private path must not leak')
    media = Media(service, broken)
    media.start(**ref, actor='reader')
    assert done(media, ref)['status'] == 'failed'
    with pytest.raises(ValueError, match='十秒'):
        media.start(**ref, actor='reader')
    path = service.root/'explanation-media'/ref['sha256']/'state.json'
    state = json.loads(path.read_text())
    assert 'private' not in state['error']
    state['updated_at'] = 0
    path.write_text(json.dumps(state))
    media.renderer = fake_render
    media.start(**ref, actor='reader')
    assert done(media, ref)['status'] == 'ready'


def test_revoked_write_during_render_does_not_publish(setup):
    service, ref, _ = setup
    def revoke(plan, stage, audio):
        service.writes = False
        return fake_render(plan, stage, audio)
    media = Media(service, revoke)
    media.start(**ref, actor='reader')
    assert done(media, ref)['status'] == 'failed'
    assert not list((service.root/'explanation-media').rglob('video.mp4'))


def test_audio_permission_checked_on_reopen_and_audio_hash_checked_on_start(setup):
    service, _, put = setup
    data = visual_plan()
    audio = put('voice.wav', b'fixed-audio')
    data.update(audio=audio, cues=[{'start': 0, 'end': 2, 'text': '测试'}])
    data['scenes'][0].update(start=0, end=2)
    ref = put('page.json', {'schema': 'portal-explanation/v1', 'video_plan': put('plan.json', data)})
    media = Media(service, fake_render)
    service.denied.add(audio['address'])
    with pytest.raises(PermissionError):
        media.status(**ref, actor='reader')
    service.denied.clear()
    (service.root/'voice.wav').write_bytes(b'changed')
    with pytest.raises(ValueError, match='内容已变化'):
        media.start(**ref, actor='reader')


def test_generation_does_not_copy_private_external_package_into_output(setup):
    service, _, put = setup
    private_plan = put('private/plan.json', plan())
    ref = put('page.json', {'schema': 'portal-explanation/v1', 'video_plan': private_plan})
    media = Media(service, fake_render)
    with pytest.raises(ValueError, match='同一Workspace内容包目录'):
        media.start(**ref, actor='reader')
    assert not (service.root/'explanation-media').exists()
    data = plan()
    data.update(audio=put('private/voice.wav', b'voice'), cues=[{'start': 0, 'end': 2, 'text': '测试'}])
    data['scenes'][0].update(start=0, end=2)
    ref = put('page.json', {'schema': 'portal-explanation/v1', 'video_plan': put('plan.json', data)})
    with pytest.raises(ValueError, match='视频音轨须'):
        media.start(**ref, actor='reader')


def test_integrity_cache_invalidates_modified_media(setup):
    service, ref, _ = setup
    media = Media(service, fake_render)
    media.start(**ref, actor='reader')
    done(media, ref)
    with media.file(**ref, name='video.mp4', actor='reader') as (stream, _, _):
        assert stream.read() == b'movie-content'
    target = service.root/'explanation-media'/ref['sha256']/'video.mp4'
    target.write_bytes(b'other-content')
    with pytest.raises(ValueError, match='媒体文件已变化'):
        with media.file(**ref, name='video.mp4', actor='reader'):
            pass


def test_cross_instance_slot_cap_and_abandoned_job_retry(setup):
    service, ref, _ = setup
    media = Media(service, fake_render)
    first, second = media._slot(), Media(service)._slot()
    try:
        with pytest.raises(RuntimeError, match='繁忙'):
            Media(service)._slot()
    finally:
        os.close(first)
        os.close(second)
    ctx = media.context(**ref, actor='reader')
    media._save(ctx, 'reader', {'status': 'generating', 'job_id': 'abandoned'})
    assert media.status(**ref, actor='reader')['status'] == 'failed'
    media.start(**ref, actor='reader')
    assert done(media, ref)['status'] == 'ready'


@pytest.mark.parametrize('edit', [
    lambda p: p['nodes'][0].update(x=float('nan')),
    lambda p: p['scenes'][0]['edges'][0].update(to='missing'),
    lambda p: p['scenes'][0].update(speech='a'*501)])
def test_plan_rejects_executable_unknown_unbounded_data(edit):
    data = plan()
    edit(data)
    with pytest.raises(ValueError):
        validate(data)


def test_http_byte_range_uses_authorized_pinned_file(setup, monkeypatch):
    from urllib.parse import urlparse
    from management_portal import home_explanation_http
    service, ref, _ = setup
    media = Media(service, fake_render)
    media.start(**ref, actor='reader')
    done(media, ref)
    monkeypatch.setattr(home_explanation_http, 'Media', lambda: media)
    class Request:
        path = '/api/home-explanation/media/file?address='+ref['address']+'&sha256='+ref['sha256']+'&file=video.mp4'
        headers = {'Range': 'bytes=1-4'}
        wfile = io.BytesIO()
        response_headers = {}
        def send_response(self, value): self.status = value
        def send_header(self, key, value): self.response_headers[key] = value
        def end_headers(self): pass
    request = Request()
    _file(request, urlparse(request.path), 'reader')
    assert request.status == 206
    assert request.wfile.getvalue() == b'ovie'
    assert request.response_headers['Content-Range'] == 'bytes 1-4/13'


def test_real_renderer_with_fixed_audio(tmp_path, monkeypatch):
    """Real encoder, bounded two-second animation; no model/User request."""
    from management_portal.home_explanation_render import render, frame
    from management_portal import home_explanation_render as rendering
    from management_portal.media_tools import capabilities
    tools = capabilities()
    if not tools.get('ffmpeg') or not Path(tools['font']).is_file():
        pytest.skip('local ffmpeg/font unavailable')
    data = plan()
    data.update(audio={'address': RID+'/file/narration.wav', 'sha256': '0'*64},
                cues=[{'start': 0, 'end': 2, 'text': '信息流动'}])
    data['scenes'][0].update(start=0, end=2)
    data = validate(data)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\0'*64000)
    painted_stages = []
    prepare = rendering.prepare_frame
    def counted_prepare(*args):
        painted_stages.append(args[1]['title'])
        return prepare(*args)
    monkeypatch.setattr(rendering, 'prepare_frame', counted_prepare)
    result = render(data, tmp_path, buf.getvalue())
    assert painted_stages == [data['scenes'][0]['title']]
    assert result['duration'] == 2
    assert (tmp_path/'video.mp4').stat().st_size > 1000
    assert '信息流动' in (tmp_path/'captions.vtt').read_text()
    from PIL import ImageFont
    fonts = {size: ImageFont.truetype(tools['font'], size) for size in (16, 20, 24)}
    assert frame(data, data['scenes'][0], .1, .2, fonts).tobytes() != frame(data, data['scenes'][0], .8, 1.6, fonts).tobytes()
    # Masked edges must not carry an animated parcel, even as the clock advances.
    for edge in data['scenes'][0]['edges']:
        edge['weight'] = 0
    assert frame(data, data['scenes'][0], .5, .2, fonts).tobytes() == frame(data, data['scenes'][0], .5, 1.4, fonts).tobytes()


def test_producer_annotations_do_not_gate_audio_or_ready_media(setup):
    service, _, put = setup
    data = visual_plan()
    data['producer_metadata'] = {'evidence': {'address': 'private/nonexistent', 'sha256': '0'*64}}
    data['script'] = 'raise Exception("must never execute")'
    clean = validate(data)
    assert not {'producer_metadata', 'script'} & clean.keys()
    ref = put('page.json', {'schema': 'portal-explanation/v1', 'video_plan': put('plan.json', data)})
    media = Media(service, fake_render)
    media.start(**ref, actor='reader')
    assert done(media, ref)['status'] == 'ready'
    service.writes = False
    with media.file(**ref, name='video.mp4', actor='reader') as (stream, _, _):
        assert stream.read() == b'movie-content'
    service.denied.add(data['audio']['address'])
    with pytest.raises(PermissionError):
        media.status(**ref, actor='reader')


def test_missing_fixed_audio_rejected_before_job_creation(setup):
    service, _, put = setup
    data = plan()
    data.pop('audio'); data.pop('cues')
    ref = put('page.json', {'schema': 'portal-explanation/v1', 'video_plan': put('plan.json', data)})
    with pytest.raises(ValueError, match='固定音轨'):
        Media(service, fake_render).start(**ref, actor='reader', quality='low')
    assert not (service.root/'explanation-media').exists()


def test_renderer_requires_fixed_audio(tmp_path, monkeypatch):
    from management_portal import home_explanation_render as module
    monkeypatch.setattr(module, 'capabilities', lambda: pytest.fail('No media tool may run before audio exists'))
    output = tmp_path/'render'
    output.mkdir()
    with pytest.raises(ValueError, match='固定音轨文件'):
        module.render(validate(plan()), output)
    assert list(output.iterdir()) == []


def test_auto_review_failure_retry_and_delivery_dedup(setup, monkeypatch):
    from management_portal import home_explanation_media as module
    service, ref, _ = setup
    calls = []
    def submission(self, subject, actor):
        calls.append((actor, subject['ref'], subject['digest']))
        if len(calls) == 1:
            raise PermissionError('private permission details')
        return {'status': 'submitted', 'requested_by': actor, 'message_id': 'msg_review_committed'}
    monkeypatch.setattr(module.review.ReviewService, 'submit', submission)
    media = Media(service, fake_render)
    media.start(**ref, actor='reader', quality='high')
    state = done(media, ref, quality='high')
    assert state['status'] == 'ready' and state['review']['status'] == 'submission_failed'
    assert 'private' not in state['review']['error']
    assert state['review']['requested_by'] == 'reader'
    receipt = media.retry_review(**ref, actor='reader', quality='high')
    assert receipt['status'] == 'ready' and receipt['review']['status'] == 'submitted'
    assert receipt['review']['message_id'] == 'msg_review_committed'
    assert calls[0] == calls[1]
    media.retry_review(**ref, actor='reader', quality='high')
    assert len(calls) == 2
    assert 'passed' not in receipt['review'].values()


def test_review_revocation_does_not_fail_ready_media(setup, monkeypatch):
    from management_portal import home_explanation_media as module
    service, ref, _ = setup
    def revoked(*args):
        service.writes = False
        raise PermissionError('revoked')
    monkeypatch.setattr(module.review.ReviewService, 'submit', revoked)
    media = Media(service, fake_render)
    media.start(**ref, actor='reader', quality='high')
    state = done(media, ref, quality='high')
    assert state['status'] == 'ready'
    assert state['review']['status'] == 'submission_failed'
    with pytest.raises(PermissionError):
        media.retry_review(**ref, actor='reader', quality='high')
    with media.file(**ref, name='video.mp4', actor='reader', quality='high') as (stream, _, _):
        assert stream.read() == b'movie-content'


def test_review_discovery_and_instruction_use_actual_actor(tmp_path, monkeypatch):
    from management_portal import home_explanation_review as helper
    from management_portal.hatg import routing
    calls = []
    def execute(call, context):
        calls.append((call, context))
        if call.instruction == 'SearchResidents':
            return {'residents': [{'resident_id': 'resident_aaaaaaaaaaaa', 'title': helper.TITLE, 'can_send': True}]}
        return {'message_id': 'msg_review'}
    monkeypatch.setattr(routing, 'execute_or_raise', execute)
    for actor in ('weihao', 'resident_0123456789ab'):
        reviewer = helper.discover(actor)
        from types import SimpleNamespace
        service = helper.ReviewService(root=tmp_path/actor,
            resources=SimpleNamespace(resolve=lambda address, **kwargs: {}))
        subject = {'kind': 'explanation_video', 'ref': RID+'/file/page.json', 'version': 'a'*64,
                   'digest': 'b'*64, 'quality': 'high', 'author': actor,
                   'assets': [{'address': RID+'/file/video.mp4', 'sha256': 'b'*64}]}
        assert service.submit(subject, actor, reviewer)['message_id'] == 'msg_review'
        count = len(calls)
        service.submit(subject, actor, reviewer)
        assert len(calls) == count
        assert calls[-1][1].caller == actor and calls[-1][1].entry == 'web'
        assert 'temporary_credentials' not in calls[-1][0].arguments
        payload = json.loads(calls[-1][0].arguments['message']['content_parts'][0]['text'])
        assert payload['subject']['assets'][0]['sha256'] == 'b'*64
        assert payload['subject']['author'] == actor
    for results in ([], [{'resident_id': 'resident_aaaaaaaaaaaa', 'title': helper.TITLE, 'can_send': True}]*2):
        with pytest.raises(ValueError):
            helper.discover('weihao', call=lambda *args: {'residents': results})
    with pytest.raises(ValueError):
        helper.discover('resident_aaaaaaaaaaaa')


def test_http_review_endpoint_passes_authenticated_actor(monkeypatch):
    from management_portal import home_explanation_http as http
    calls = []
    class Backend:
        def retry_review(self, address, sha256, actor, quality):
            calls.append((address, sha256, actor, quality))
            return {'status': 'ready', 'review': {'status': 'submission_failed'}}
    monkeypatch.setattr(http, 'Media', Backend)
    monkeypatch.setattr(http.http_request_body, '_read_bounded_json_object',
                        lambda *a, **kw: {'address': 'fixed', 'sha256': 'hash', 'actor': 'forged'})
    monkeypatch.setattr(http.http_responses, '_serve_json', lambda request, code, value: (code, value))
    code, body = http._review(object(), None, 'resident_0123456789ab')
    assert calls == [('fixed', 'hash', 'resident_0123456789ab', 'medium')]
    assert code == 200 and body['media']['review']['status'] == 'submission_failed'
