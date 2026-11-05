"""Retirement uses runtime ownership and process identity, never process names."""
from types import SimpleNamespace
import os
import pytest
from scripts.instance_process_cleanup import stop_orphans


class Process:
    def __init__(self,pid,root,*,ancestors=(),changed=False):
        self.pid=pid;self.root=root;self.ancestors=ancestors;self.changed=changed;self.reads=0;self.stopped=False
    def parents(self):return list(self.ancestors)
    def environ(self):return {'PORTAL_INSTANCE_ROOT':self.root} if self.root else {}
    def create_time(self):
        self.reads+=1
        return self.reads if self.changed else 1
    def terminate(self):self.stopped=True


def api_for(processes):
    return SimpleNamespace(Process=lambda pid:next(p for p in processes if p.pid==pid),
        process_iter=lambda:iter(processes),NoSuchProcess=ProcessLookupError,AccessDenied=PermissionError,
        wait_procs=lambda stopped,timeout:(stopped,[]))


def test_retirement_stops_tagged_orphan_and_preserves_installer_ancestors_and_other_instances():
    parent=Process(9991,'target')
    own=Process(os.getpid(),'target',ancestors=[parent])
    orphan=Process(9992,'target')
    other=Process(9993,'other-instance')
    unowned=Process(9994,None)
    result=stop_orphans('target',api=api_for([own,parent,orphan,other,unowned]))
    assert result['stopped_orphan_pids']==[orphan.pid]
    assert orphan.stopped and not any(p.stopped for p in [own,parent,other,unowned])


def test_reused_process_identity_is_refused_without_termination():
    own=Process(os.getpid(),'target')
    changed=Process(9992,'target',changed=True)
    with pytest.raises(RuntimeError,match='ownership changed'):
        stop_orphans('target',api=api_for([own,changed]))
    assert not changed.stopped
