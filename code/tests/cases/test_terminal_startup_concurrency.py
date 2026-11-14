"""A pending terminal creation must not monopolize every session's registry."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from management_portal import device_identity_backend, local_terminal_backend as terminal, permission_system


class Process:
    pid = 1234
    def read(self): return b''
    def poll(self): return None
    def close(self): pass


def session(sid, actor):
    now=time.time()
    return terminal.TerminalSession(sid,actor,Process(),now,now,'/tmp')


@pytest.mark.parametrize('device',[False,True])
def test_slow_process_creation_does_not_block_other_terminal_reads(monkeypatch, device, record_property):
    existing=session('existing','other-user')
    created=session('created','requesting-user')
    monkeypatch.setattr(terminal,'_SESSIONS',{'existing':existing})
    entered,release=threading.Event(),threading.Event()
    def spawn(*args,**kwargs):
        entered.set()
        assert release.wait(4), 'test did not release blocked spawn'
        return created
    monkeypatch.setattr(terminal,'_spawn_shell',spawn)
    monkeypatch.setattr(terminal,'_spawn_process',spawn)
    monkeypatch.setattr(permission_system,'allows',lambda actor,op,args: op=='OpenTerminal')
    monkeypatch.setattr(device_identity_backend,'effective_ssh_access',lambda rid:{'via_resource_id':rid,'host':'example.invalid'})
    monkeypatch.setattr(device_identity_backend,'read_device_ssh_password',lambda rid:'')
    with ThreadPoolExecutor(max_workers=2) as executor:
        opening=executor.submit(terminal.open_terminal,'requesting-user',device_id='device_123456789abc' if device else '')
        try:
            assert entered.wait(2)
            reading=executor.submit(terminal.read_terminal,'other-user','existing')
            assert reading.result(timeout=1)['session_id']=='existing'
            assert not opening.done(), 'read must complete while creation is still pending'
            assert 'created' not in terminal._SESSIONS
        finally:
            release.set()
        assert opening.result(timeout=2)['session_id']=='created'
    assert terminal._SESSIONS['created'] is created
    record_property('unrelated_read_completed_before_spawn_release',True)


def test_terminal_permission_reuse_and_failed_spawn_preserve_registry(monkeypatch):
    existing=session('existing','owner')
    monkeypatch.setattr(terminal,'_SESSIONS',{'existing':existing})
    attempts=[]
    def spawn(*args,**kwargs):
        attempts.append(True)
        raise RuntimeError('spawn failed')
    monkeypatch.setattr(terminal,'_spawn_shell',spawn)
    monkeypatch.setattr(permission_system,'allows',lambda *args:False)
    with pytest.raises(PermissionError):terminal.open_terminal('owner')
    assert attempts==[]
    monkeypatch.setattr(permission_system,'allows',lambda *args:True)
    assert terminal.open_terminal('owner',session_id='existing')['session_id']=='existing'
    assert attempts==[]
    with pytest.raises(RuntimeError,match='spawn failed'):terminal.open_terminal('owner')
    assert terminal._SESSIONS=={'existing':existing}
