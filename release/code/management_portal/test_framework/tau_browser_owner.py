"""Persistent TAU browser ownership. Only process_runtime controls process trees.

The short launch client may exit; the owner keeps the browser's Job handle open.
A helper reconnects using the same lease. Cleanup never treats a bare PID as a
Windows Job, or an unverified reused POSIX PID as the original session.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import sys
import threading
import time
import uuid

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from management_common import process_runtime
from management_common.file_permissions import ensure_private_directory, set_created_file_permissions
from management_portal.shared_state import atomic_replace_json

LEASE = 'tau-browser-lease.json'


def _write(path: Path, value: dict) -> None:
    atomic_replace_json(path, value, mode=0o600)


def _identity(pid: int) -> dict:
    import psutil
    return {'pid': pid, 'created_at': psutil.Process(pid).create_time()}


def _same(identity: dict) -> bool:
    import psutil
    try:
        process = psutil.Process(int(identity['pid']))
        return process.create_time() == identity['created_at'] and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def _tree_id(evidence: Path, token: str) -> str:
    if uuid.UUID(token).hex != token:
        raise ValueError('Invalid browser lease token')
    return 'tau-browser-' + hashlib.sha256(str(evidence.resolve()).encode()).hexdigest() + '-' + token


def _profile_path(evidence: Path, token: str) -> Path:
    return Path(tempfile.gettempdir()).resolve() / ('tau-' + hashlib.sha256(str(evidence.resolve()).encode()).hexdigest()[:12] + '-' + token)


def _remove_profile(evidence: Path, lease: dict) -> None:
    if not lease.get('profile_root'):
        return
    expected = _profile_path(evidence, lease['token'])
    if Path(lease['profile_root']) != expected or expected.is_symlink():
        raise RuntimeError('TAU browser profile ownership mismatch')
    if expected.exists():
        shutil.rmtree(expected)


def read_lease(evidence: Path, token: str | None = None) -> dict:
    evidence = evidence.resolve()
    lease = json.loads((evidence / LEASE).read_text(encoding='utf-8'))
    if (lease.get('evidence_dir') != str(evidence)
            or lease.get('tree_id') != _tree_id(evidence, lease['token'])
            or (token is not None and token != lease['token'])):
        raise RuntimeError('TAU browser lease ownership mismatch')
    return lease


def stop_tree(evidence: Path, token: str) -> None:
    lease = read_lease(evidence, token)
    if sys.platform == 'win32':
        process_runtime.terminate_process_tree(None, timeout=3, tree_id=lease['tree_id'])
        if process_runtime.process_tree_alive(tree_id=lease['tree_id']):
            raise RuntimeError('TAU browser Job remains active')
        return
    identity = lease.get('host')
    if not identity:
        if lease.get('state') == 'closed':
            return
        raise RuntimeError('TAU browser launch ownership is not yet recorded')
    if not _same(identity):
        # A reused PID is never authority to signal the replacement session.
        # An orphaned group with a vanished leader needs manual recovery.
        if process_runtime.process_tree_alive(identity['pid']):
            raise RuntimeError('TAU browser session identity changed; refusing to signal')
        return
    process_runtime.terminate_process_tree(identity['pid'], timeout=3, tree_id=lease['tree_id'])


def stop_browser(evidence: Path, token: str | None = None, timeout: float = 15) -> dict:
    lease = read_lease(evidence, token)
    if lease.get('state') == 'closed':
        return lease
    _write(evidence / ('tau-browser-stop-' + lease['token'] + '.json'), {'token': lease['token']})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lease = read_lease(evidence, lease['token'])
        if lease.get('state') == 'closed':
            return lease
        if lease.get('owner') and not _same(lease['owner']):
            stop_tree(evidence, lease['token'])
            _remove_profile(evidence, lease)
            lease.update(state='closed', reason='owner exited')
            _write(evidence / LEASE, lease)
            return lease
        time.sleep(0.05)
    raise RuntimeError('TAU browser owner did not confirm cleanup; lease retained')


def start_browser(evidence: Path, node: str, headless: str) -> dict:
    evidence = evidence.resolve()
    lock = evidence / 'tau-browser-launch.lock'
    if (evidence / LEASE).exists():
        previous = read_lease(evidence)
        if previous.get('state') != 'closed' and previous.get('owner') and _same(previous['owner']):
            return {**previous, 'reused': True}
    if lock.exists():
        # A crashed launch client must not prevent reconnect/recovery. Unknown
        # ownership remains a visible failure; never steal a live launch lock.
        holder = json.loads(lock.read_text(encoding='utf-8'))
        if _same(holder):
            raise RuntimeError('TAU browser launch is still in progress')
        lock.unlink()
    with lock.open('x', encoding='utf-8') as stream:
        set_created_file_permissions(lock, 0o600)
        json.dump(_identity(os.getpid()), stream)
    owner = None
    try:
        if (evidence / LEASE).exists():
            old = read_lease(evidence)
            if old.get('state') != 'closed':
                if old.get('owner') and _same(old['owner']):
                    return {**old, 'reused': True}
                stop_browser(evidence, old['token'])
        token = uuid.uuid4().hex
        lease = {'schema': 'tfi.tau-browser-lease.v1', 'token': token,
                 'tree_id': _tree_id(evidence, token), 'evidence_dir': str(evidence),
                 'python': sys.executable, 'state': 'starting'}
        _write(evidence / LEASE, lease)  # Intent precedes any child.
        owner = process_runtime.spawn_process(
            [sys.executable, str(Path(__file__).resolve()), 'own', str(evidence), token, node, headless],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            tree_id=lease['tree_id'] + '-owner', kill_on_owner_exit=False)
        lease['owner'] = _identity(owner.pid)
        _write(evidence / LEASE, lease)
        owner.stdin.write(b'start\n'); owner.stdin.flush(); owner.stdin.close()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            lease = read_lease(evidence, token)
            if lease.get('host') or lease.get('state') == 'closed':
                # Keep a waiter while this interpreter lives; the launcher may
                # itself exit immediately. The owner Job is intentionally detached.
                threading.Thread(target=owner.wait, daemon=True).start()
                return {**lease, 'reused': False}
            if owner.poll() is not None:
                raise RuntimeError('TAU browser owner exited before readiness')
            time.sleep(0.05)
        raise RuntimeError('TAU browser owner startup timed out')
    except BaseException:
        if owner is not None:
            try:
                stop_browser(evidence, timeout=5)
            finally:
                process_runtime.terminate_process_tree(owner, force=True)
        raise
    finally:
        lock.unlink()


def own(evidence: Path, token: str, node: str, headless: str) -> int:
    if sys.stdin.buffer.readline() != b'start\n':
        return 1
    lease = read_lease(evidence, token)
    stopping = threading.Event()
    previous = process_runtime.install_shutdown_handlers(lambda *_: stopping.set())
    process = None
    error = ''
    try:
        profile = _profile_path(evidence, token)
        ensure_private_directory(profile)
        lease['profile_root'] = str(profile)
        _write(evidence / LEASE, lease)
        process = process_runtime.spawn_process(
            [node, str(Path(__file__).with_name('tau_browser_host.js')), str(evidence), headless, token, sys.executable, str(profile)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            tree_id=lease['tree_id'])
        lease.update(host=_identity(process.pid), state='owned')
        _write(evidence / LEASE, lease)
        process.stdin.write(b'start\n'); process.stdin.flush()
        deadline = time.monotonic() + 60
        while process.poll() is None and not stopping.wait(0.05):
            if (evidence / ('tau-browser-stop-' + token + '.json')).exists():
                break
            try:
                host = json.loads((evidence / 'tau-browser-host.json').read_text())
            except (OSError, ValueError):
                host = {}
            if host.get('token') == token and host.get('state') == 'running':
                deadline = float('inf')
            if time.monotonic() >= deadline:
                raise RuntimeError('TAU browser startup timed out')
    except BaseException as exc:
        error = str(exc)
        raise
    finally:
        try:
            if process is not None:
                process_runtime.terminate_process_tree(process, timeout=3)
                process.stdin.close()
            _remove_profile(evidence, lease)
            lease.update(state='closed', error=error)
            _write(evidence / LEASE, lease)
        finally:
            process_runtime.restore_shutdown_handlers(previous)
    return 0


def cleanup_browsers(run_dir: Path) -> list[int]:
    stopped = []
    for path in run_dir.rglob(LEASE):
        evidence = path.parent.resolve()
        if path.is_symlink() or not evidence.is_relative_to(run_dir.resolve()):
            raise RuntimeError('TAU browser lease escapes this run')
        lease = stop_browser(evidence)
        if lease.get('host'):
            stopped.append(lease['host']['pid'])
        # Owner publishes closed after reaping the browser, then exits. Avoid
        # treating its brief exit tail as an unmanaged Windows helper.
        deadline = time.monotonic() + 5
        while lease.get('owner') and _same(lease['owner']) and time.monotonic() < deadline:
            time.sleep(0.05)
        if lease.get('owner') and _same(lease['owner']):
            raise RuntimeError('TAU browser owner remains alive')
    return stopped


if __name__ == '__main__':
    action, directory, *arguments = sys.argv[1:]
    directory = Path(directory)
    if action == 'start':
        print(json.dumps(start_browser(directory, *arguments)))
    elif action == 'stop':
        print(json.dumps(stop_browser(directory, *arguments)))
    elif action == 'stop-tree':
        stop_tree(directory, *arguments)
    elif action == 'own':
        raise SystemExit(own(directory, *arguments))
    else:
        raise SystemExit('Unknown browser owner action')
