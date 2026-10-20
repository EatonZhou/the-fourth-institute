"""Host-side transient-unit entry point; no credentials cross into bubblewrap."""
from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import threading
import tempfile
import time
import urllib.error
import urllib.request

from management_portal.shared_state import atomic_replace_json
from management_common.local_ipc import LocalListener
from management_common import process_runtime

from . import runtime

MAX_LOG = 10 * 1024 * 1024
MAX_JSON = 65536
RESTART_GRACE_SECONDS = 120


def atomic_json(path: Path, value: dict):
    atomic_replace_json(path, value)


class Relay(socketserver.ThreadingMixIn, socketserver.BaseServer):
    daemon_threads = True
    def __init__(self, path, forward):
        self.forward = forward
        self.listener = LocalListener(path)
        super().__init__(str(path), RelayHandler)

    def fileno(self):
        return self.listener.fileno()

    def get_request(self):
        return self.listener.accept()

    def shutdown_request(self, request):
        request.close()

    def server_close(self):
        self.listener.close()


class RelayHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(120)
        try:
            data = self.rfile.readline(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError('ISA 请求过大')
            request = json.loads(data)
            if not isinstance(request, dict) or set(request) - {'instruction', 'arguments'}:
                raise ValueError('只接受指令名称与参数')
            if not isinstance(request.get('instruction'), str) or not isinstance(request.get('arguments', {}), dict):
                raise ValueError('指令参数无效')
            result = self.server.forward(request)
        except Exception as error:
            result = {'status': 'failed', 'error': {'code': 'instruction.failed', 'message': str(error)}}
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b'\n')


def http_forward(config):
    def forward(payload):
        headers = {'Authorization': 'Bearer ' + config['token'], 'Content-Type': 'application/json'}
        request = urllib.request.Request(config['portal_url'] + '/api/instructions/execute',
                                         data=json.dumps(payload).encode(), headers=headers)
        deadline = time.monotonic() + RESTART_GRACE_SECONDS
        while True:
            try:
                response = urllib.request.urlopen(request, timeout=120)
            except urllib.error.HTTPError as error:
                response = error
            except urllib.error.URLError as error:
                # Portal Web restarts within seconds on every publish.  A refused
                # connection never delivered the request, so trying again cannot
                # run an instruction twice.
                if not isinstance(error.reason, ConnectionRefusedError) or time.monotonic() >= deadline:
                    raise
                time.sleep(2)
                continue
            with response:
                result = json.load(response)
            # The owning service restarting mid-call: a read is simply asked again;
            # a mutation's outcome is unknown and goes back to the script.
            if (isinstance(result, dict) and (result.get('error') or {}).get('code') == 'instruction.owner_unavailable'
                    and time.monotonic() < deadline and _reads_only(payload.get('instruction'))):
                time.sleep(3)
                continue
            return result
    return forward


def _reads_only(instruction) -> bool:
    try:
        from management_portal.hatg.catalog import declaration
        return not declaration(str(instruction)).mutates
    except Exception:
        return False


def _capture(source, destination, flags, name):
    used = 0
    with destination.open('wb') as stream:
        while chunk := source.read(65536):
            available = max(0, MAX_LOG - used)
            stream.write(chunk[:available])
            used += len(chunk)
            if used > MAX_LOG:
                flags[name] = True


def result_fields(io: Path):
    output_path = io / 'output.json'
    outputs = {}
    if output_path.exists() or output_path.is_symlink():
        if output_path.is_symlink() or not output_path.is_file() or output_path.stat().st_size > MAX_JSON:
            raise ValueError('script.invalid_output')
        outputs = json.loads(output_path.read_text(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if not isinstance(outputs, dict):
            raise ValueError('script.invalid_output')
    artifacts = []
    size = 0
    root = io / 'artifacts'
    if root.is_symlink():
        raise ValueError('script.invalid_artifacts')
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('script.invalid_artifacts')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('script.invalid_artifacts')
        size += path.stat().st_size
        if len(artifacts) >= 200 or size > 50 * 1024 * 1024:
            raise ValueError('script.invalid_artifacts')
        name = path.relative_to(root).as_posix()
        artifacts.append({'name': name, 'path': name, 'size': path.stat().st_size,
                          'mime_type': mimetypes.guess_type(name)[0] or 'application/octet-stream'})
    return outputs, artifacts


def run(config: dict, run_dir: Path, *, forward=None):
    with tempfile.TemporaryDirectory(prefix="portal-isa-") as socket_dir:
        return _run(config, run_dir, socket_path=Path(socket_dir) / "isa.sock", forward=forward)


def _run(config: dict, run_dir: Path, *, socket_path: Path, forward=None):
    started = time.time()
    result = {'status': 'failed', 'exit_code': None, 'outputs': {}, 'artifacts': [], 'error_code': '',
              'stdout_truncated': False, 'stderr_truncated': False}
    relay = Relay(socket_path, forward or http_forward(config))
    thread = threading.Thread(target=relay.serve_forever, daemon=True)
    thread.start()
    process = None
    drains = []
    cancelled = threading.Event()
    def cancel(signum, frame):
        # A signal can arrive inside spawn/wait. Keep the handler nonblocking;
        # the bounded wait below observes it as soon as we own the child.
        cancelled.set()
    previous_handlers = {}
    if threading.current_thread() is threading.main_thread():
        previous_handlers = process_runtime.install_shutdown_handlers(cancel)
    failure = None
    receipt = run_dir / 'payload.json'
    identity = {'script_run_id': config.get('script_run_id', run_dir.name)}
    try:
        command, environment = runtime.command(config, run_dir, socket_path=socket_path)
        if not cancelled.is_set():
            # Do not claim "not started" across the spawn/persist crash window.
            atomic_json(receipt, {**identity, 'state': 'starting'})
            process = process_runtime.spawn_process(
                command, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=config['cwd'],
                tree_id=config.get('process_tree_id') or 'portal-script-' + run_dir.name + '-payload',
            )
            atomic_json(receipt, {**identity, 'state': 'running', 'pid': process.pid})
            drains = [threading.Thread(target=_capture, daemon=True,
                      args=(stream, run_dir / (name + '.log'), result, name + '_truncated'))
                      for name, stream in [('stdout', process.stdout), ('stderr', process.stderr)]]
            for drain in drains:
                drain.start()
            timeout = config.get('timeout_seconds')
            deadline = time.monotonic() + timeout if timeout is not None else None
            while not cancelled.is_set():
                remaining = deadline - time.monotonic() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    result['status'] = 'timed_out'
                    break
                try:
                    result['exit_code'] = process.wait(timeout=min(.1, remaining) if remaining is not None else .1)
                    result['status'] = 'succeeded' if result['exit_code'] == 0 else 'failed'
                    break
                except subprocess.TimeoutExpired:
                    continue
        if cancelled.is_set():
            result['status'] = 'cancelled'
    except Exception as error:
        failure = error
    finally:
        try:
            if isinstance(failure, process_runtime.ProcessCleanupError):
                # spawn_process did not return ownership; an unsuccessful
                # rollback is not evidence that no process remains.
                raise failure
            if process is not None:
                # Descendants may outlive a successfully exited leader and keep
                # stdout/stderr open. A terminal result requires the whole owned
                # tree to be gone, on success as well as timeout/cancellation.
                process_runtime.terminate_process_tree(process, timeout=5, force=True)
                result['exit_code'] = process.wait(timeout=5)
            for drain in drains:
                drain.join(timeout=5)
                if drain.is_alive():
                    raise TimeoutError('ScriptRun output pipe did not close after process tree cleanup')
            atomic_json(receipt, {**identity, 'state': 'stopped'})
        finally:
            relay.shutdown()
            relay.server_close()
            process_runtime.restore_shutdown_handlers(previous_handlers)
    if failure is not None:
        result['status'] = 'failed'
        result['error_code'] = 'script.runner_failed'
        with (run_dir / 'stderr.log').open('a') as stream:
            stream.write(type(failure).__name__ + ': ' + str(failure))
    else:
        try:
            result['outputs'], result['artifacts'] = result_fields(run_dir / 'io')
        except (ValueError, OSError) as error:
            result['error_code'] = str(error) if str(error).startswith('script.') else 'script.invalid_output'
            if result['status'] == 'succeeded':
                result['status'] = 'failed'
    result['duration_seconds'] = round(time.time() - started, 3)
    from datetime import datetime, timezone
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    stderr = run_dir / 'stderr.log'
    if stderr.exists():
        with stderr.open('rb') as stream:
            stream.seek(max(0, stderr.stat().st_size - 8192))
            result['stderr_tail'] = stream.read().decode('utf-8', 'replace')
    return result


def main():
    private = Path(sys.argv[1])
    config = json.loads(private.read_text())
    try:
        # This receipt repairs the launch/PID-persistence window after a Society
        # restart; it does not authorize signalling an unverified/reused PID.
        atomic_json(Path(config['run_dir']) / 'runner.json', {
            'script_run_id': config.get('script_run_id', private.stem),
            'pid': os.getpid(),
        })
        result = run(config, Path(config['run_dir']))
    finally:
        # Authentication checks this lease on every call, including while the
        # coordinator is unavailable. Deleting it revokes the per-run token.
        private.unlink(missing_ok=True)
    # A terminal result is observable by the coordinator immediately. Revoke
    # the credential lease before publishing it, including on cancellation.
    atomic_json(Path(config['run_dir']) / 'result.json', result)


if __name__ == '__main__':
    main()
