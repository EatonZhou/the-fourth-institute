"""Counting and exact checkpoint I/O for the single anticipatory job."""
import hashlib
import json
import time
from pathlib import Path
import numpy as np


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, data):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def save_packet(path, env, controller, metadata):
    arrays = {}
    def pack(value, key):
        if isinstance(value, np.ndarray):
            arrays[key] = value
            return {'array':key}
        if isinstance(value, dict):
            return {k:pack(v,key+'__'+k) for k,v in value.items()}
        if isinstance(value,(list,tuple)):
            return [pack(v,key+'__'+str(i)) for i,v in enumerate(value)]
        if isinstance(value,np.generic):
            return value.item()
        return value
    payload = dict(metadata=metadata,env=pack(env.checkpoint(),'env'),
                   controller=pack(controller.checkpoint(),'controller'))
    arrays['metadata'] = np.asarray(json.dumps(payload,ensure_ascii=False,allow_nan=False))
    np.savez_compressed(path,**arrays)
    return {'path':str(path),'sha256':sha(path)}


class PhysicalBudget:
    """Count attempted calls even when the unique host raises an exception."""
    def __init__(self, max_steps, max_worker_seconds, threads=2):
        self.max_steps = int(max_steps)
        self.max_worker_seconds = float(max_worker_seconds)
        self.threads = int(threads)
        self.attempted = self.committed = 0
        self.started = time.monotonic()

    def check(self):
        if self.attempted >= self.max_steps:
            raise RuntimeError('physical step cap reached; no retry or replacement')
        # Include imports/packing via a conservative launch margin in the plan.
        if (time.monotonic()-self.started)*self.threads >= self.max_worker_seconds:
            raise RuntimeError('allocated-worker time cap reached')

    def step(self, env, *args, **kwargs):
        self.check()
        self.attempted += 1
        result = env.step(*args,**kwargs)
        self.committed += 1
        return result

    def record(self):
        return dict(attempted=self.attempted,committed=self.committed,
                    failed=self.attempted-self.committed,
                    script_allocated_worker_seconds=(time.monotonic()-self.started)*self.threads,
                    runner_slot_usage_finalized_separately=True)
