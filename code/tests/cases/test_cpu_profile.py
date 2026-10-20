from __future__ import annotations

import json
import os
import signal
import threading
import pytest
import psutil  # Load the host native binding before simulating adapter selection.

from management_common import cpu_profile


def _spin(stop: threading.Event) -> None:
    while not stop.is_set():
        sum(range(2000))


def _idle(stop: threading.Event) -> None:
    stop.wait()


@pytest.mark.parametrize('observer_platform', ['linux', 'darwin', 'win32'])
def test_cpu_profile_counts_only_threads_that_use_cpu(monkeypatch, observer_platform):
    """CPU 采样只记录真正消耗 CPU 的线程和调用栈

    一个线程空转、一个线程等待；采样结果的线程、帧和调用栈只有空转的那个，等待的线程不计。
    """
    monkeypatch.setattr(cpu_profile.sys, 'platform', observer_platform)
    stop = threading.Event()
    busy = threading.Thread(target=_spin, args=(stop,), name="busy-worker-1")
    waiting = threading.Thread(target=_idle, args=(stop,), name="waiting-worker-1")
    busy.start()
    waiting.start()
    try:
        result = cpu_profile.profile(duration=1.0, interval=0.01)
    finally:
        stop.set()
        busy.join()
        waiting.join()
    threads = dict(result["threads"])
    assert threads.get("busy-worker-N", 0) > 0
    assert "waiting-worker-N" not in threads
    frames = dict(result["frames"])
    assert any(frame.endswith(" _spin") for frame in frames)
    assert not any(frame.endswith(" _idle") for frame in frames)
    assert result["sampled_ticks"] == sum(threads.values())


@pytest.mark.skipif(not hasattr(signal, 'SIGUSR2'), reason='Optional POSIX diagnostic trigger')
def test_sigusr2_logs_one_profile_line(monkeypatch, capfd):
    """SIGUSR2 让服务记录一次 CPU 采样

    服务收到 SIGUSR2 后采样一段时间，向 stderr 写一行 “Service CPU profile: …”；采样期间再收到信号不另起一次。
    """
    monkeypatch.setattr(cpu_profile, "DURATION", 0.3)
    previous = signal.getsignal(signal.SIGUSR2)
    cpu_profile.install()
    try:
        os.kill(os.getpid(), signal.SIGUSR2)
        os.kill(os.getpid(), signal.SIGUSR2)
        assert cpu_profile._running.acquire(timeout=5)
        cpu_profile._running.release()
    finally:
        signal.signal(signal.SIGUSR2, previous)
    lines = [line for line in capfd.readouterr().err.splitlines() if line.startswith("Service CPU profile: ")]
    assert len(lines) == 1
    profile = json.loads(lines[0].split(": ", 1)[1])
    assert profile["pid"] == os.getpid() and profile["seconds"] == 0.3
