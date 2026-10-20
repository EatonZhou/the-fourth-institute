"""RunScript domain: validation, authorization, atomic admission and observation."""
from __future__ import annotations

from management_common.principal import is_user_or_resident
from management_common import portal_paths
from management_common import process_runtime
from management_common.product_environment import read_product_env

import base64
from copy import deepcopy
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time

from . import runtime, store
from .runner import atomic_json
from scripts.portal_background_jobs import launch_job
from scripts import portal_background_jobs

ACTIVATION = ContextVar('script_run_activation', default={})
TERMINAL = frozenset({'succeeded', 'failed', 'timed_out', 'cancelled', 'lost'})
RUNNER_PATH_KEYS = frozenset({
    'PORTAL_SITE_CONFIG', 'PORTAL_PATHS_CONFIG', 'PORTAL_RELEASE_ROOT',
    *portal_paths.ENV_KEYS.values(),
})


def now():
    return datetime.now(timezone.utc).isoformat()




def _validate(arguments):
    _check_platform()
    args = deepcopy(arguments)
    path = Path(args['path']).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError('脚本必须是可读取文件的绝对路径')
    path = path.resolve()
    runtime = args.get('runtime') or {'.py': 'python', '.js': 'javascript', '.cjs': 'javascript', '.mjs': 'javascript', '.sh': 'bash', '.bash': 'bash'}.get(path.suffix)
    if runtime not in {'python', 'javascript', 'bash'}:
        raise ValueError('请指定 python、javascript 或 bash')
    cwd = Path(args.get('cwd') or path.parent)
    if not cwd.is_absolute() or not cwd.is_dir():
        raise ValueError('cwd 必须是目录的绝对路径')
    argv = args.get('argv', [])
    if not isinstance(argv, list) or len(argv) > 64 or any(not isinstance(v, str) or len(v) > 4000 or '\0' in v for v in argv):
        raise ValueError('argv 最多 64 个字符串，每个最多 4000 字符')
    inputs = args.get('arguments', {})
    if not isinstance(inputs, dict) or len(json.dumps(inputs, ensure_ascii=False, allow_nan=False).encode()) > 65536:
        raise ValueError('arguments 必须是最大 64 KB 的 JSON 对象')
    timeout = args.get('timeout_seconds')
    if timeout is not None and (type(timeout) is not int or timeout < 1):
        raise ValueError('timeout_seconds 必须是正整数')
    if not isinstance(args.get('exclusive_key', ''), str) or len(args.get('exclusive_key', '')) > 120:
        raise ValueError('exclusive_key 最多 120 字符')
    return {**args, 'path': str(path), 'cwd': str(cwd.resolve()), 'runtime': runtime, 'argv': argv, 'arguments': inputs}


def _check_platform():
    if sys.platform not in {'linux', 'darwin', 'win32'}:
        raise NotImplementedError(f'Unsupported ScriptRun platform: {sys.platform}')


def _payload_tree(identifier):
    return 'portal-script-' + identifier + '-payload'


def _issue_token(caller, run_id, private, timeout):
    from management_portal import agent_token_auth as auth
    token = auth.generate_token()
    auth.get_agent_token_store().put(run_id, {
        'token_hash': auth.hash_token(token), 'token_lookup': auth.token_lookup(token), 'enabled': True,
        'managed_by': 'portal_script_run', 'caller_id': caller, 'required_scope': 'instructions',
        'expires_at': time.time() + timeout + 90 if timeout is not None else None, 'lease_path': str(private),
    })
    return token


def revoke(record):
    from management_portal import agent_token_auth as auth
    private = runtime.PRIVATE_ROOT / (record['script_run_id'] + '.json')
    private.unlink(missing_ok=True)
    tokens = auth.get_agent_token_store()
    existing = tokens.get(record['script_run_id'])
    if existing and existing.get('enabled'):
        tokens.put(record['script_run_id'], {**existing, 'enabled': False})


def launch(record, config):
    _check_platform()
    run_dir = runtime.RUN_ROOT / record['script_run_id']
    io = run_dir / 'io'
    (io / 'artifacts').mkdir(parents=True, mode=0o700)
    atomic_json(io / 'input.json', {key: record[key] for key in ('script_run_id', 'arguments', 'caller_id', 'lineage_id', 'trigger_path_id', 'effect_id')})
    runtime.PRIVATE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    private = runtime.PRIVATE_ROOT / (record['script_run_id'] + '.json')
    config = {**config, 'run_dir': str(run_dir), 'script_run_id': record['script_run_id'],
              'process_tree_id': _payload_tree(record['script_run_id']),
              'portal_url': read_product_env('PORTAL_URL', 'http://127.0.0.1:18086').rstrip('/')}
    config['token'] = _issue_token(record['caller_id'], record['script_run_id'], private, config.get('timeout_seconds'))
    atomic_json(private, config)
    private.chmod(0o600)
    # Detached runners do not inherit the launching service's environment.
    # Carry its explicit site paths, never database URLs or service credentials.
    path_environment = {key: os.environ[key] for key in sorted(RUNNER_PATH_KEYS)
                        if key in os.environ}
    if sys.platform in {'darwin', 'win32'}:
        allowed = {"HOME", "USER", "LOGNAME", "PATH", "TMPDIR", "LANG",
                   "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
                   "USERPROFILE", "TEMP", "TMP"}
        runner_environment = {key: value for key, value in os.environ.items()
                              if key in allowed or key.startswith("LC_")}
        runner_environment.update(path_environment)
        return launch_job(
            record['unit'],
            [sys.executable, '-m', 'management_portal.society_service.script_runs.runner', str(private)],
            cwd=runtime.REPOSITORY, log_path=run_dir / 'runner.log', platform=sys.platform,
            environment=runner_environment, inherit_environment=False,
        )
    # The transient unit is owned by the user manager, outside Portal's cgroup.
    command = ['systemd-run', '--user', '--collect', '--quiet', '--unit=' + record['unit'],
               '--property=KillMode=control-group', '--property=TimeoutStopSec=15',
               '--working-directory=' + str(runtime.REPOSITORY),
               *(f'--setenv={key}={value}' for key, value in path_environment.items()),
               sys.executable, '-m',
               'management_portal.society_service.script_runs.runner', str(private)]
    if config.get('timeout_seconds') is not None:
        command.insert(5, '--property=RuntimeMaxSec=' + str(config['timeout_seconds'] + 10))
    result = process_runtime.run_process(command, capture_output=True, text=True, timeout=45)
    if result.returncode:
        revoke(record)
        raise RuntimeError('脚本单元启动失败：' + result.stderr[-2000:])
    return None


def owning_lineage(arguments, caller):
    """The Lineage a RunScript call runs in, and is judged on (decision_8b510cbd9c58).

    Inside an Effect it is the activation's Lineage; otherwise ``lineage_id``,
    or a Resident caller's own Lineage when it omits one. A User must name it.
    """
    lineage_id = str(ACTIVATION.get().get('lineage_id') or arguments.get('lineage_id') or '')
    if not lineage_id and str(caller).startswith('resident_'):
        from ..isa.common import lineage_of_resident
        lineage_id = lineage_of_resident(caller)
    from ..identity_contract import is_valid_id
    if not is_valid_id(lineage_id, 'lineage'):
        raise ValueError('RunScript 需要有效的所属 lineage_id')
    if arguments.get('lineage_id') and arguments['lineage_id'] != lineage_id:
        raise ValueError('RunScript lineage_id 与当前激活不符')
    return lineage_id


def start(arguments, caller, *, idempotency_key='', trigger_path_id=''):
    args = _validate(arguments)
    activation = deepcopy(ACTIVATION.get())
    lineage_id = owning_lineage(arguments, caller)
    source = Path(args['path']).read_bytes()
    admission = hashlib.sha256((caller + '\0' + idempotency_key).encode()).hexdigest() if idempotency_key else ''
    identifier = 'script_run_' + secrets.token_hex(12)
    record = {**args, 'script_run_id': identifier, 'caller_id': caller, 'status': 'running',
                  'lineage_id': lineage_id, 'effect_id': activation.get('effect_id', ''),
                  'trigger_path_id': trigger_path_id, 'identity': activation.get('identity', {}), 'trigger_type': activation.get('trigger_type', ''),
                  'started_at': now(), 'finished_at': '', 'duration_seconds': 0, 'sha256': hashlib.sha256(source).hexdigest(),
                  'exit_code': None, 'outputs': {}, 'error_code': '', 'stderr_tail': '', 'artifacts': [],
                  'unit': '', 'admission': admission, 'notified': False}
    record, admitted = store.admit(record)
    if admitted != 'created':
        return {**_receipt(record), 'status': 'already_running'} if admitted == 'already_running' else _receipt(record)
    # Commit admission before the external launch; crash recovery marks an
    # abandoned admission lost, never starts it twice.
    launch_record = {**record, 'unit': 'portal-script-' + identifier}
    try:
        pid = launch(launch_record, args)
    except Exception as error:
        with store.edit(identifier) as row:
            if row['status'] == 'running':
                row.update(status='failed', finished_at=now(), error_code='script.launch_failed', stderr_tail=str(error))
        revoke(record)
        raise
    # The store has no PID column. Persist the launch receipt with the run's
    # existing files instead of silently dropping it or changing the schema.
    # A persistence failure after launch leaves the original admission running;
    # it cannot report launch failure while the native job continues executing.
    if pid is not None:
        atomic_json(runtime.RUN_ROOT / identifier / 'runner.json',
                    {'script_run_id': identifier, 'pid': pid})
    cancellation_pending = False
    with store.edit(identifier) as row:
        if row['status'] == 'running':
            row['unit'] = launch_record['unit']
            cancellation_pending = bool(row.get('cancel_requested'))
    if cancellation_pending:
        cancel(identifier, caller, lineage_id=lineage_id)
    return _receipt(record)


def _receipt(record):
    return {key: record[key] for key in ('script_run_id', 'status', 'started_at')}


def _authorize(caller):
    if not is_user_or_resident(caller):
        raise PermissionError('Authenticated User or Resident required')


def public(record):
    return {key: deepcopy(value) for key, value in record.items() if key not in {'unit', 'pid', 'admission', 'notified', 'identity', 'trigger_type', 'cancel_requested'}}


def get(identifier, caller, *, lineage_id):
    row = store.get(identifier)
    if row is None or row.get('lineage_id') != lineage_id:
        raise FileNotFoundError('脚本运行不存在')
    _authorize(caller)
    return public(row)


def list_runs(arguments, caller):
    if not arguments.get('lineage_id'):
        raise ValueError('ListScriptRuns 需要 lineage_id')
    _authorize(caller)
    result = store.list_runs(arguments)
    return {**result, 'runs': [public(row) for row in result['runs']]}


def read_output(arguments, caller):
    row = get(arguments['script_run_id'], caller, lineage_id=arguments['lineage_id'])
    stream = arguments.get('stream', 'stdout')
    root = runtime.RUN_ROOT / row['script_run_id']
    mime = 'text/plain'
    if stream == 'artifact':
        name = arguments.get('name', '')
        artifact = next((a for a in row['artifacts'] if a['name'] == name), None)
        if artifact is None:
            raise FileNotFoundError('产物不存在')
        root = root / 'io/artifacts'
        path = root / name
        mime = artifact['mime_type']
    elif stream in ('stdout', 'stderr'):
        path = root / (stream + '.log')
    else:
        raise ValueError('stream 无效')
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise PermissionError('产物路径越界')
    offset, limit = max(0, int(arguments.get('offset', 0))), min(65536, max(1, int(arguments.get('limit', 65536))))
    data, size = b'', 0
    if path.exists():
        with path.open('rb') as source:
            size = os.fstat(source.fileno()).st_size
            source.seek(offset)
            data = source.read(limit)
    return {'text': data.decode('utf-8', 'replace'), 'base64': base64.b64encode(data).decode(), 'offset': offset,
            'next_offset': offset + len(data), 'size': size, 'eof': offset + len(data) >= size, 'mime_type': mime,
            'truncated': bool(row.get(stream + '_truncated'))}


def _mac_runner_matches(pid: int, identifier: str) -> bool:
    result = process_runtime.run_process(['ps', '-ww', '-p', str(pid), '-o', 'command='],
                                         capture_output=True, text=True, timeout=15)
    return result.returncode == 0 and (
        'management_portal.society_service.script_runs.runner' in result.stdout
        and identifier + '.json' in result.stdout
    )


def _runner_pid(record):
    """Recover the original launch receipt, never launch another runner."""
    if record.get('pid'):
        return record['pid']
    receipt = runtime.RUN_ROOT / record['script_run_id'] / 'runner.json'
    if not receipt.is_file():
        return None
    saved = json.loads(receipt.read_text())
    if not isinstance(saved, dict):
        raise RuntimeError('ScriptRun runner receipt is not an object')
    pid = saved.get('pid')
    if saved.get('script_run_id') != record['script_run_id'] or type(pid) is not int or pid <= 0:
        raise RuntimeError('ScriptRun runner receipt identity does not match')
    return pid


def running_state(record):
    """Recover a run without relaunching it or guessing from a vanished PID.

    The background owner observes the native unit. Named Jobs (Windows) and
    persisted runner/payload groups (POSIX) separately prove tree liveness.
    Missing/ambiguous receipts remain unknown, including legacy runs.
    """
    _check_platform()
    identifier = record['script_run_id']
    try:
        pid = _runner_pid(record)
        if not record['unit'] and pid is None:
            return 'unknown'
        unit = record['unit'] or 'portal-script-' + identifier
        native_alive = portal_background_jobs.job_alive(unit, pid)
        if sys.platform == 'win32':
            # The background owner's PID can be reused, and the root may exit
            # with descendants alive. Named Jobs, not that PID, decide here.
            trees = [process_runtime.process_tree_alive(pid, tree_id=name)
                     for name in (unit, _payload_tree(identifier))]
            if any(trees):
                return 'running'
        else:
            if native_alive:
                return 'running'
            if pid is None:
                return 'unknown'
            if process_runtime.process_tree_alive(pid):
                return 'running'
        path = runtime.RUN_ROOT / identifier / 'payload.json'
        if not path.is_file():
            return 'unknown'
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict) or payload.get('script_run_id') != identifier:
            return 'unknown'
        if payload.get('state') == 'stopped':
            return 'stopped'
        payload_pid = payload.get('pid')
        if payload.get('state') != 'running' or type(payload_pid) is not int or payload_pid <= 1:
            return 'unknown'
        if sys.platform == 'win32':
            return 'stopped'  # Both named Jobs were observed empty above.
        return 'running' if process_runtime.process_tree_alive(payload_pid) else 'stopped'
    except (OSError, ValueError, RuntimeError):
        return 'unknown'


def _stop_run(record, result_file):
    _check_platform()
    pid = _runner_pid(record)
    if not record['unit'] and pid is None:
        # An admission is durable before native launch. start() will perform
        # this cancellation when launch completes, including systemd's None PID.
        return False
    unit = record['unit'] or 'portal-script-' + record['script_run_id']
    if sys.platform == 'linux':
        stopped = process_runtime.run_process(
            ['systemctl', '--user', 'stop', unit], capture_output=True, text=True, timeout=45)
        if stopped.returncode and not result_file.is_file():
            raise RuntimeError('脚本单元停止失败：' + stopped.stderr[-2000:])
    elif sys.platform == 'win32':
        # The background-job owner names the detached Job with record.unit.
        # A second named Job owns the payload. Parent exit alone proves neither
        # tree empty; reopening both identities survives Society restarts.
        process_runtime.terminate_process_tree(pid, timeout=15, tree_id=unit)
        process_runtime.terminate_process_tree(pid, timeout=15, force=True,
                                               tree_id=_payload_tree(record['script_run_id']))
        if not result_file.is_file() and running_state(record) != 'stopped':
            raise RuntimeError('ScriptRun stopped without confirmed payload cleanup')
    else:
        if pid is None or not process_runtime.process_alive(pid) or not _mac_runner_matches(pid, record['script_run_id']):
            if result_file.is_file():
                return True
            raise RuntimeError('ScriptRun runner identity could not be verified')
        process_runtime.terminate_process_tree(pid, timeout=15)
        # The payload has its own POSIX session. Only the runner's result,
        # written after verified payload cleanup, proves it is safe to finish.
        if not result_file.is_file():
            raise RuntimeError('ScriptRun stopped without confirmed payload cleanup')
    return True


def cancel(identifier, caller, *, lineage_id):
    with store.edit(identifier) as row:
        if row is None or row.get('lineage_id') != lineage_id:
            raise FileNotFoundError('脚本运行不存在')
        _authorize(caller)
        if row['status'] in TERMINAL:
            return public(row)
        result_file = runtime.RUN_ROOT / identifier / 'result.json'
        completed = result_file.is_file()
        if completed:
            row.update(json.loads(result_file.read_text()))
        else:
            row['cancel_requested'] = True
        record = deepcopy(row)
    if completed:
        revoke(row)
        return public(row)
    if not _stop_run(record, result_file):
        return public(row)
    with store.edit(identifier) as row:
        if row['status'] in TERMINAL:
            return public(row)
        result_file = runtime.RUN_ROOT / identifier / 'result.json'
        if result_file.is_file():
            row.update(json.loads(result_file.read_text()))
        else:
            row.update(status='cancelled', finished_at=row.get('finished_at') or now(),
                       duration_seconds=row.get('duration_seconds') or time.time()-datetime.fromisoformat(row['started_at']).timestamp())
    revoke(row)
    return public(row)
