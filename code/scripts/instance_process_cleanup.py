"""Retirement-only cleanup of orphan processes tagged with the exact instance."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def stop_orphans(root, *, api=None):
    if api is None:
        import psutil as api
    own=api.Process(os.getpid())
    excluded={own.pid,*(p.pid for p in own.parents())}
    targets=[]
    for process in api.process_iter():
        try:
            if process.pid not in excluded and process.environ().get('PORTAL_INSTANCE_ROOT')==str(root):
                targets.append((process,process.create_time()))
        except (api.NoSuchProcess,api.AccessDenied):
            continue
    # Child-first, with identity and ownership checked again immediately before
    # termination. No process name or account directory grants ownership.
    targets.sort(key=lambda item:len(item[0].parents()),reverse=True)
    stopped=[]
    for process,created in targets:
        try:
            if process.create_time()!=created or process.environ().get('PORTAL_INSTANCE_ROOT')!=str(root):
                raise RuntimeError('Process ownership changed during retirement')
            process.terminate()
            stopped.append(process)
        except api.NoSuchProcess:
            pass
    _,alive=api.wait_procs(stopped,timeout=15)
    if alive:
        raise RuntimeError('Instance-owned orphan process did not exit; data retained')
    return {'stopped_orphan_pids':[p.pid for p in stopped]}


if __name__=='__main__':
    from management_common.instance_manifest import current_instance
    print(json.dumps(stop_orphans(current_instance().root)))
