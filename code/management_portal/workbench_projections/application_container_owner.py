"""A build-owned process survives Web exit long enough to clean daemon state.

stdin is a lifetime pipe, never build input. Private control files are not mounted
into the container. Unconfirmed cleanup retains the plan for explicit recovery.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import uuid

from contextlib import contextmanager
from hashlib import sha256

from management_common import process_runtime
from management_common.process_observation import process_identity, process_is_alive
from management_common.file_permissions import (
    atomic_write_private_bytes, ensure_private_directory, read_private_bytes,
)
from .store import ProjectionError


def _write(path: Path, value: dict) -> None:
    atomic_write_private_bytes(path, json.dumps(value, ensure_ascii=False).encode())


def _read(path: Path) -> dict:
    return json.loads(read_private_bytes(path))


_PURPOSE = 'application-container-control-v1:'


def _store():
    from management_common.directory_retention import RetentionStore
    return RetentionStore()


def _key(store, operation):
    return int.from_bytes(sha256((store.database.schema + ':' + operation).encode()).digest()[:8],
                          'big', signed=True)


def _metadata(row):
    return json.loads(row['purpose'][len(_PURPOSE):])


def _save(store, row, metadata):
    row['purpose'] = _PURPOSE + json.dumps(metadata)
    store.database.write(lambda conn: conn.execute(
        f'UPDATE {store.queue.reservations} SET purpose=%s WHERE operation_id=%s AND location=%s',
        (row['purpose'], row['operation_id'], row['location'])))


def _forget(store, row):
    with store.database.transaction() as conn:
        store.queue.lock(conn)
        conn.execute(f'DELETE FROM {store.table} WHERE location=%s AND owner_id=%s',
                     (row['location'], row['operation_id']))
        store.queue.release(row['operation_id'], connection=conn)


def _release(store, row):
    from management_common.directory_paths import cleanup_path
    metadata = _metadata(row)
    if metadata['identity'] is None or not metadata['cleanup_complete']:
        raise RuntimeError('Control cleanup requires confirmed ownership and container cleanup')
    with store.database.transaction() as conn:
        identifier = store.enqueue_cleanup(cleanup_path(row['location']), owner_id=row['operation_id'],
            operation_id=row['operation_id'], actor='application-container-owner', connection=conn)
    return store.queue.run_reserved(cleanup_id=identifier, operation_id=row['operation_id'],
                                   identity=metadata['identity'])


@contextmanager
def _control():
    from management_common import secure_paths as fs
    from management_common.directory_paths import cleanup_location, lexical_path
    store = _store()
    current = store.database._current_connection()
    if current is not None and current.in_transaction:
        raise RuntimeError('Control allocation must precede the owner transaction')
    operation = 'application-container:' + uuid.uuid4().hex
    path = lexical_path(Path(tempfile.gettempdir()) / ('portal-build-owner-' + uuid.uuid4().hex))
    metadata = {'identity': None, 'cleanup_complete': False,
                'process_owner': process_identity(), 'ended': False}
    row = dict(operation_id=operation, location=cleanup_location(path), purpose=_PURPOSE + json.dumps(metadata))
    with store.database.advisory_lock(_key(store, operation)):
        reserved = False
        try:
            with fs.open_directory(path.parent) as parent:
                with store.database.transaction() as conn:
                    store.queue.lock(conn)
                    if os.path.lexists(path):
                        raise FileExistsError(str(path))
                    store.declare(path, category='runtime_tmp', owner_id=operation, connection=conn)
                    store.queue.reserve(path, operation_id=operation, purpose=row['purpose'], connection=conn)
                reserved = True
                try:
                    with fs.private_child(parent, path.name, create=True) as directory:
                        fs.sync_directory(parent)
                        info = os.fstat(directory)
                        metadata['identity'] = [info.st_dev, info.st_ino]
                        _save(store, row, metadata)
                except FileExistsError:
                    _forget(store, row)
                    reserved = False
                    raise
            yield path, store, row
        finally:
            if reserved:
                metadata = _metadata(row)
                metadata['ended'] = True
                _save(store, row, metadata)
                if metadata['cleanup_complete']:
                    _release(store, row)


def run(project: Path, command: list[str], *, config: dict, input=None, mounts=(),
        writable_cache=None, network=False, readonly_project=False, timeout=360, limit=67108864):
    with _control() as (control, store, row):
        return _run(control, store, row, project, command, config=config, input=input, mounts=mounts,
                    writable_cache=writable_cache, network=network, readonly_project=readonly_project,
                    timeout=timeout, limit=limit)


def _run(control, store, row, project, command, *, config, input, mounts,
         writable_cache, network, readonly_project, timeout, limit):
    process = None
    receipt = {}
    try:
        name = 'portal-app-build-' + uuid.uuid4().hex
        plan = dict(project=str(project.resolve()), command=command, config=config, name=name,
                    token=uuid.uuid4().hex, mounts=[(str(p.resolve()), d) for p, d in mounts],
                    writable_cache=str(writable_cache.resolve()) if writable_cache else None,
                    network=network, readonly_project=readonly_project, timeout=timeout, limit=limit,
                    has_input=input is not None)
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
        process.wait(timeout=timeout + 450)
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
                process.wait(timeout=270)
            except subprocess.TimeoutExpired:
                process_runtime.terminate_process_tree(process, force=True)
            if (control / 'result.json').is_file():
                receipt = _read(control / 'result.json')
            if receipt.get('cleanup_complete') is not True:
                # Never discard recovery material merely because the CLI died.
                raise ProjectionError('projection.application_cleanup', f'构建容器清理未确认，恢复计划：{control}')
            process_runtime.terminate_process_tree(process, force=True)
        metadata = _metadata(row)
        metadata['cleanup_complete'] = True
        _save(store, row, metadata)


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


def _row(store, control):
    from management_common.directory_paths import cleanup_location
    with store.database.read() as conn:
        row = conn.execute(f'SELECT * FROM {store.queue.reservations} WHERE location=%s '
                           'AND starts_with(purpose,%s)', (cleanup_location(control), _PURPOSE)).fetchone()
        return dict(row) if row else None


def _confirmed(store, row, control):
    metadata = _metadata(row)
    if metadata['cleanup_complete']:
        return True  # Persisted before cleanup can move/delete the receipt itself.
    if metadata['identity'] is None:
        if os.path.lexists(control):
            raise FileExistsError('Unproven control allocation retained: ' + str(control))
        _forget(store, row)
        return False
    info = control.lstat()
    if control.is_symlink() or [info.st_dev, info.st_ino] != metadata['identity']:
        raise ValueError('Control directory ownership changed: ' + str(control))
    try:
        receipt = _read(control / 'result.json')
    except FileNotFoundError:
        return False
    if receipt.get('cleanup_complete') is not True:
        return False
    metadata['cleanup_complete'] = True
    _save(store, row, metadata)
    return True


def recover_completed_controls():
    """Require parent end/death, a free lease, and confirmed container cleanup."""
    from management_common.directory_paths import cleanup_path
    store = _store()
    report = {'active': [], 'cleanup': [], 'errors': []}
    with store.database.read() as conn:
        candidates = conn.execute(f'SELECT * FROM {store.queue.reservations} WHERE starts_with(purpose,%s)',
                                  (_PURPOSE,)).fetchall()
    for candidate in candidates:
        control = cleanup_path(candidate['location'])
        try:
            with store.database.advisory_lock(_key(store, candidate['operation_id']), blocking=False) as acquired:
                if not acquired:
                    report['active'].append(str(control))
                    continue
                row = _row(store, control)
                if row and _parent_active(row):
                    report['active'].append(str(control))
                    continue
                if row and row['operation_id'] == candidate['operation_id'] and _confirmed(store, row, control):
                    outcome = _release(store, row)
                    report['cleanup'].append(outcome)
                    if outcome['last_error']:
                        report['errors'].append(outcome['last_error'])
        except Exception as error:
            report['errors'].append(f'{control}: {error}')
    return report


def _parent_active(row):
    metadata = _metadata(row)
    return metadata.get('ended') is not True and process_is_alive(metadata.get('process_owner'))


def recover(control: Path) -> None:
    """Explicit daemon cleanup, even in the original caller after run has failed."""
    from . import application_build as build
    from management_common.file_retention import temporary_directory
    store = _store()
    row = _row(store, control)
    if row is None:
        if os.path.lexists(control):
            raise ValueError('Control ownership is not registered: ' + str(control))
        return
    operation = row['operation_id']
    with store.database.advisory_lock(_key(store, operation), blocking=False) as acquired:
        if not acquired:
            raise RuntimeError('Control parent is active')
        row = _row(store, control)
        if row is None or row['operation_id'] != operation:
            return
        if _parent_active(row):
            raise RuntimeError('Control parent is active or its process state is unknown')
        if _confirmed(store, row, control):
            _release(store, row)
            return
        # No receipt means the worker has not confirmed its terminal state.
        receipt = _read(control / 'result.json')
        plan = _read(control / 'plan.json')
        with temporary_directory(prefix='portal-build-recover-') as private:
            config = plan['config']
            env = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP') if k in os.environ}
            env.update(PATH=os.defpath, HOME=private, USERPROFILE=private, DOCKER_CONFIG=private)
            prefix = [config['docker_executable'], '--host', config['docker_endpoint'], '--config', private]
            def cli(args):
                return build._docker_cli([*prefix, *args], env=env, timeout=45, limit=1048576)
            build._remove_owned_container(cli, plan['name'], plan['token'], receipt.get('create_observed', False))
        receipt['cleanup_complete'] = True
        _write(control / 'result.json', receipt)
        if _confirmed(store, row, control):
            _release(store, row)


if __name__ == '__main__':
    if len(sys.argv) == 2:
        raise SystemExit(worker(Path(sys.argv[1])))
    if len(sys.argv) == 3 and sys.argv[1] == '--recover':
        recover(Path(sys.argv[2]))
    else:
        raise SystemExit('usage: application_container_owner [--recover] CONTROL_DIRECTORY')
