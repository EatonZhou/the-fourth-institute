"""WALL维护线程创建失败后不发布虚假运行状态，下一次可重试。

:module: workflow.wall
:design: decision_06defc47e960
:risk: no-external-side-effects
"""
import pytest

from management_portal import wall_backend as wall


@pytest.mark.parametrize('failure_stage', ['constructor', 'start'])
def test_wall_runtime_start_failure_leaves_lifecycle_retryable(monkeypatch, failure_stage):
    """构造或启动线程失败均保持未启动状态，停止安全且重试可复用正常生命周期。"""
    monkeypatch.setattr(wall, '_RUNTIME_STARTED', False)
    monkeypatch.setattr(wall, '_RUNTIME_STOP', None)
    monkeypatch.setattr(wall, '_RUNTIME_THREAD', None)
    attempts, threads, joined = [], [], []

    class Thread:
        def __init__(self, **kwargs):
            attempts.append('construct')
            assert kwargs['daemon'] and kwargs['name'] == 'wall-maintenance'
            if failure_stage == 'constructor' and len(attempts) == 1:
                raise RuntimeError('fixture thread constructor unavailable')
            self.started = False
            threads.append(self)

        def start(self):
            if failure_stage == 'start' and len(attempts) == 1:
                raise RuntimeError('fixture thread start unavailable')
            self.started = True

        def join(self, timeout):
            assert self.started, 'joined a thread that never started'
            joined.append(timeout)

    monkeypatch.setattr(wall.threading, 'Thread', Thread)
    with pytest.raises(RuntimeError, match='fixture thread'):
        wall.start_runtime()
    assert not wall._RUNTIME_STARTED
    assert wall._RUNTIME_THREAD is None
    assert wall._RUNTIME_STOP is None
    wall.stop_runtime()
    assert not joined
    wall.start_runtime()
    thread, stop = wall._RUNTIME_THREAD, wall._RUNTIME_STOP
    assert wall._RUNTIME_STARTED and thread.started and not stop.is_set()
    wall.start_runtime()
    assert wall._RUNTIME_THREAD is thread and len(attempts) == 2
    wall.stop_runtime()
    assert stop.is_set() and joined == [6]
    assert not wall._RUNTIME_STARTED and wall._RUNTIME_THREAD is None
