"""A build-owned process survives Web exit long enough to clean daemon state.

stdin is a lifetime pipe, never build input. Private control files are not mounted
into the container. Unconfirmed cleanup retains the plan for explicit recovery.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid

import psutil

from management_common import process_runtime
from management_common.file_permissions import (
    atomic_write_private_bytes, ensure_private_directory, read_private_bytes,
)
from .store import ProjectionError


def _write(path: Path, value: dict) -> None:
    atomic_write_private_bytes(path, json.dumps(value, ensure_ascii=False).encode())


def _read(path: Path) -> dict:
    return json.loads(read_private_bytes(path))


def run(project: Path, command: list[str], *, config: dict, input=None, mounts=(),
        writable_cache=None, network=False, readonly_project=False, timeout=120, limit=67108864):
    control = Path(tempfile.mkdtemp(prefix='portal-build-owner-'))
    ensure_private_directory(control)
    name = 'portal-app-build-' + uuid.uuid4().hex
    plan = dict(project=str(project.resolve()), command=command, config=config, name=name,
                token=uuid.uuid4().hex, mounts=[(str(p.resolve()), d) for p, d in mounts],
                writable_cache=str(writable_cache.resolve()) if writable_cache else None,
                network=network, readonly_project=readonly_project, timeout=timeout, limit=limit,
                has_input=input is not None, caller={'pid': os.getpid(), 'created_at': psutil.Process().create_time()})
    process = None
    receipt = {}
    try:
        _write(control / 'plan.json', plan)
        if input is not None:
            atomic_write_private_bytes(control / 'input', input)
        environment = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP') if k in os.environ}
        environment.update(PATH=os.defpath, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        process = process_runtime.spawn_process(
            [sys.executable, '-m', __name__, str(control)],
            cwd=str(Path(__file__).resolve().parents[2]), env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            tree_id=name + '-owner', kill_on_owner_exit=False,
        )
        process.wait(timeout=timeout + 150)
        receipt = _read(control / 'result.json')
        if receipt.get('cleanup_complete') is not True:
            raise ProjectionError('projection.application_cleanup', f'构建容器清理未确认，恢复计划：{control}')
        if process.returncode or receipt.get('error'):
            raise ProjectionError(receipt.get('code') or 'projection.application_build',
                                  receipt.get('error') or f'构建进程异常退出（{process.returncode}）')
        output = read_private_bytes(control / 'output')
        if len(output) > limit:
            raise ProjectionError('projection.application_size', '构建输出超出限制')
        return output
    finally:
        if process is not None:
            process.stdin.close()  # Cancellation/parent exit gives the owner EOF.
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process_runtime.terminate_process_tree(process, force=True)
            if (control / 'result.json').is_file():
                receipt = _read(control / 'result.json')
            if receipt.get('cleanup_complete') is not True:
                # Never discard recovery material merely because the CLI died.
                raise ProjectionError('projection.application_cleanup', f'构建容器清理未确认，恢复计划：{control}')
            process_runtime.terminate_process_tree(process, force=True)
        shutil.rmtree(control)


def worker(control: Path) -> int:
    from . import application_build as build
    ensure_private_directory(control)
    plan = _read(control / 'plan.json')
    stopped = threading.Event()
    def watch_parent():
        try:
            while os.read(0, 4096):
                pass
        finally:
            stopped.set()
    # A blocked BufferedReader daemon can abort CPython during shutdown.
    threading.Thread(target=watch_parent, daemon=True).start()
    receipt = {'cleanup_complete': True, 'error': '', 'code': ''}
    try:
        output = build._container_command_owned(
            Path(plan['project']), plan['command'], config=plan['config'], stop=stopped,
            name=plan['name'], token=plan['token'], receipt=receipt,
            input=read_private_bytes(control / 'input') if plan['has_input'] else None,
            mounts=tuple((Path(p), d) for p, d in plan['mounts']),
            writable_cache=Path(plan['writable_cache']) if plan['writable_cache'] else None,
            network=plan['network'], readonly_project=plan['readonly_project'],
            timeout=plan['timeout'], limit=plan['limit'],
        )
        atomic_write_private_bytes(control / 'output', output)
        return 0
    except BaseException as error:
        receipt.update(error=str(error), code=getattr(error, 'code', 'projection.application_build'))
        return 1
    finally:
        _write(control / 'result.json', receipt)
        if stopped.is_set() and receipt['cleanup_complete'] and not _caller_alive(plan):
            shutil.rmtree(control)


def _caller_alive(plan: dict) -> bool:
    try:
        process = psutil.Process(plan['caller']['pid'])
        return process.create_time() == plan['caller']['created_at'] and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        return True


def recover(control: Path) -> None:
    """Explicit recovery of retained daemon state, never re-execute the build."""
    from . import application_build as build
    plan = _read(control / 'plan.json')
    # The owner may still be active. Recovery is only allowed after its receipt.
    receipt = _read(control / 'result.json')
    if receipt.get('cleanup_complete'):
        return
    with tempfile.TemporaryDirectory(prefix='portal-build-recover-') as private:
        config = plan['config']
        env = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP') if k in os.environ}
        env.update(PATH=os.defpath, HOME=private, USERPROFILE=private, DOCKER_CONFIG=private)
        prefix = [config['docker_executable'], '--host', config['docker_endpoint'], '--config', private]
        def cli(args):
            return build._docker_cli([*prefix, *args], env=env, timeout=15, limit=1048576)
        build._remove_owned_container(cli, plan['name'], plan['token'], receipt.get('create_observed', False))
    receipt['cleanup_complete'] = True
    _write(control / 'result.json', receipt)


if __name__ == '__main__':
    if len(sys.argv) == 2:
        raise SystemExit(worker(Path(sys.argv[1])))
    if len(sys.argv) == 3 and sys.argv[1] == '--recover':
        recover(Path(sys.argv[2]))
    else:
        raise SystemExit('usage: application_container_owner [--recover] CONTROL_DIRECTORY')
