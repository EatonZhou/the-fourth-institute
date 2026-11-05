"""A routine slow-call sample cannot retain a finished request's stack."""
import collections
import threading
import time
import weakref

from management_portal.hatg import slow_call_profile as profile


def test_sampler_releases_finished_request_locals_without_gc(monkeypatch):
    monkeypatch.setattr(profile, 'SAMPLE_AFTER', 0)
    monkeypatch.setattr(profile, 'INTERVAL', .005)
    ready, finish = threading.Event(), threading.Event()
    reference = []

    class Payload:
        pass

    def request():
        payload = Payload()
        reference.append(weakref.ref(payload))
        with profile.track('test', 'Read'):
            ready.set()
            assert finish.wait(3)

    thread = threading.Thread(target=request)
    thread.start()
    assert ready.wait(2)
    deadline = time.monotonic() + 2
    sampled = False
    while time.monotonic() < deadline:
        with profile._guard:
            sampled = any(call['thread_id'] == thread.ident and call['samples']
                          for call in profile._running.values())
        if sampled:
            break
        time.sleep(.005)
    finish.set()
    thread.join(2)
    assert sampled and not thread.is_alive()
    assert reference[0]() is None, 'Idle sampler retains a completed request frame'


def test_metadata_sampling_does_not_load_source_lines(monkeypatch):
    call = {'thread_id': threading.get_ident(), 'samples': 0,
            'inclusive': collections.Counter(), 'innermost': collections.Counter()}
    monkeypatch.setattr(profile.traceback.linecache, 'getline',
                        lambda *_: (_ for _ in ()).throw(AssertionError('source text lookup')))
    profile._sample_calls([call])
    assert call['samples'] == 1
    assert call['inclusive']


def test_windows_frame_path_uses_the_same_source_boundary(monkeypatch):
    monkeypatch.setattr(profile, '_PORTAL', 'C:/Portal/code/')
    frame = profile.traceback.FrameSummary(r'C:\Portal\code\management_portal\hatg\kernel.py', 25, 'execute', lookup_line=False)
    assert profile._frame(frame) == 'management_portal/hatg/kernel.py:25 execute'
