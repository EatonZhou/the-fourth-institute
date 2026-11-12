"""测试名额取得与释放异常时完整归还描述符，不使长寿命执行器泄漏并发容量。

:module: operations.test-framework
:design: decision_8273271b67b1
:risk: no-external-side-effects
"""
import os

import pytest

from management_portal.test_framework import slots


def open_fd(fd):
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


@pytest.fixture
def descriptors(tmp_path, monkeypatch):
    monkeypatch.setattr(slots, 'slot_root', lambda: tmp_path)
    monkeypatch.setattr(slots, 'max_concurrency', lambda: 2)
    monkeypatch.setitem(slots.CAPACITY, 'pytest', 2)
    opened, names = [], {}
    original = slots._open_lock

    def tracked(path):
        fd = original(path)
        opened.append(fd)
        names[fd] = path.name
        return fd

    monkeypatch.setattr(slots, '_open_lock', tracked)
    yield opened, names, tracked
    # A red implementation must not leak even its isolated fixture locks.
    for fd in set(opened):
        if open_fd(fd):
            os.close(fd)


@pytest.mark.parametrize('failure', ['global-open', 'global-lock', 'kind-open', 'kind-lock', 'later-kind-open'])
def test_partial_slot_acquisition_failure_closes_all_descriptors(descriptors, monkeypatch, failure):
    """首次/后继open或lock失败均撤销此前取得的整个租约，原异常继续传播。"""
    opened, names, tracked = descriptors
    original_lock = slots.try_lock_descriptor
    fired = False

    def opening(path):
        nonlocal fired
        trigger = ((failure == 'global-open' and path.name == 'global-1.lock') or
                   (failure == 'kind-open' and path.name == 'pytest-0.lock') or
                   (failure == 'later-kind-open' and path.name == 'pytest-1.lock'))
        if not fired and trigger:
            fired = True
            raise OSError('fixture slot open failure')
        return tracked(path)

    def locking(fd):
        nonlocal fired
        trigger = ((failure == 'global-lock' and names[fd] == 'global-1.lock') or
                   (failure == 'kind-lock' and names[fd] == 'pytest-0.lock'))
        if not fired and trigger:
            fired = True
            raise OSError('fixture slot lock failure')
        return original_lock(fd)

    monkeypatch.setattr(slots, '_open_lock', opening)
    monkeypatch.setattr(slots, 'try_lock_descriptor', locking)
    with pytest.raises(OSError, match='fixture slot'):
        slots.acquire('pytest', 2, stop=lambda: True)
    assert fired
    assert not [fd for fd in set(opened) if open_fd(fd)], 'failed acquisition retained descriptors/locks'
    retry = slots.acquire('pytest', 2, stop=lambda: True)
    assert retry is not None and retry.count == 2
    retry.release()
    assert not [fd for fd in set(opened) if open_fd(fd)]


def test_lease_release_attempts_every_descriptor_after_a_close_error(descriptors):
    """一个描述符已失效时仍释放其余锁，保留错误且重复release幂等。"""
    opened, _names, _tracked = descriptors
    lease = slots.acquire('pytest', 2, stop=lambda: True)
    assert lease is not None
    os.close(opened[0])
    with pytest.raises(OSError):
        lease.release()
    assert lease.count == 0
    assert not [fd for fd in set(opened) if open_fd(fd)], 'close error left other leases held'
    lease.release()


def test_interrupt_during_later_lock_attempt_does_not_leak_previous_pair(descriptors, monkeypatch):
    """BaseException中断也撤销正在取得的租约，不只覆盖普通IO错误。"""
    opened, names, _tracked = descriptors
    original = slots.try_lock_descriptor

    def interrupt(fd):
        if names[fd] == 'global-1.lock':
            raise KeyboardInterrupt('fixture interrupted admission')
        return original(fd)

    monkeypatch.setattr(slots, 'try_lock_descriptor', interrupt)
    with pytest.raises(KeyboardInterrupt, match='fixture interrupted'):
        slots.acquire('pytest', 2, stop=lambda: True)
    assert not [fd for fd in set(opened) if open_fd(fd)]
