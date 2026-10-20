"""Stop ExecutionService on all supported platforms; hand over FDs on Linux.

On Linux, systemd tracks the ready successor as MainPID. Retiring generations own only
their accepted connections; listening descriptors are inherited, never rebound.
The private socket also relays disposable timing observations (no content).

Windows/macOS replacements belong to the external ``portal_services`` controller:
it drains work and calls ``ServiceControl.restart``. That is a disruptive native
service restart, not an FD handover. The running service must not synchronously
restart itself (Windows service control waits for the old service to stop).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import queue
import socket
import sys
import threading

from management_common import process_runtime

HANDOVER_ENV = 'PORTAL_EXECUTION_HANDOVER'
LOGGER = logging.getLogger(__name__)


def _platform():
    if sys.platform not in {'linux', 'darwin', 'win32'}:
        raise NotImplementedError(f'Unsupported ExecutionService platform: {sys.platform}')
    return sys.platform


def _check_inherited(descriptors):
    if _platform() != 'linux' and descriptors:
        raise NotImplementedError('ExecutionService inherited descriptors are Linux-only; '
                                  'use the external ServiceControl.restart path')


def inherited_descriptors():
    descriptors = json.loads(os.environ.pop(HANDOVER_ENV, '{}'))
    _check_inherited(descriptors)
    return descriptors


def notify(message):
    if _platform() != 'linux':
        return
    address = os.environ.get('NOTIFY_SOCKET')
    if address:
        import array
        import select

        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect('\0' + address[1:] if address.startswith('@') else address)
            sock.sendall(message.encode())
            # Keep the sender alive until systemd has accepted MAINPID. Without
            # the barrier an idle old generation could exit before attribution.
            read_fd, write_fd = os.pipe()
            try:
                sock.sendmsg([b'BARRIER=1'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                             array.array('i', [write_fd]))])
                os.close(write_fd)
                write_fd = -1
                if not select.select([read_fd], [], [], 5)[0]:
                    raise TimeoutError('systemd did not acknowledge ExecutionService handover')
            finally:
                os.close(read_fd)
                if write_fd >= 0:
                    os.close(write_fd)


class TimingRelay:
    def __init__(self, channel):
        self.channel = channel
        self.queue = queue.Queue(maxsize=100000)
        self.thread = threading.Thread(target=self._send, daemon=True, name='timing-handover')
        self.thread.start()

    def put(self, provider, output, timing):
        try:
            self.queue.put_nowait((provider, output, timing))
        except queue.Full:
            pass  # Never backpressure a model response for observation.

    def _send(self):
        try:
            while (item := self.queue.get()) is not None:
                self.channel.sendall(json.dumps(item, separators=(',', ':')).encode() + b'\n')
        except OSError:
            pass
        finally:
            self.channel.close()

    def close(self):
        # No provider transport remains at this point. A broken observation
        # recipient still cannot prevent a retiring process from exiting.
        try:
            self.queue.put(None, timeout=1)
        except queue.Full:
            pass
        self.thread.join(5)
        self.channel.close()


class ServiceLifecycle:
    def __init__(self, server, inherited=None, *, command=None):
        self.server = server
        self.inherited = inherited or {}
        _check_inherited(self.inherited)
        self.command = command or [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / 'scripts/run_portal_shared_component.py'),
            'execution-service',
        ]
        self.reload = threading.Event()
        self.stop = threading.Event()
        self.relay = None
        self.error = None
        self.predecessor = None

    def _serve(self):
        try:
            self.server.serve_forever()
        except BaseException as error:
            self.error = error
            self.stop.set()

    def _receive_timings(self, channel):
        try:
            with channel, channel.makefile('rb') as stream:
                for line in stream:
                    provider, output, value = json.loads(line)
                    if self.server.timing_proxy is not None:
                        self.server.timing_proxy.receive_timing(provider, output, value)
        except (OSError, ValueError, RuntimeError):
            pass  # Observation is not part of execution success.

    def replace(self):
        if _platform() != 'linux':
            raise NotImplementedError(
                'ExecutionService FD handover is Linux-only; use the external '
                'portal_services controller to drain work and call ServiceControl.restart. '
                'Native Windows/macOS restarts do not preserve accepted connections.'
            )
        parent, child = socket.socketpair()
        descriptors = {'rpc': self.server.socket.fileno(), 'control': child.fileno()}
        proxy = (self.server.timing_proxy.handover_descriptors()
                 if self.server.timing_proxy is not None else None)
        if proxy:
            descriptors['proxy'] = proxy
        fds = [descriptors['rpc'], descriptors['control'], *(proxy or {}).values()]
        environment = {**os.environ, HANDOVER_ENV: json.dumps(descriptors)}
        process = None
        try:
            process = process_runtime.spawn_process(
                self.command, env=environment, pass_fds=fds,
                kill_on_owner_exit=False,
            )
            child.close()
            parent.settimeout(60)
            # One-byte readiness avoids consuming timing records from the stream.
            if parent.recv(1) != b'R':
                raise RuntimeError('ExecutionService successor exited before readiness')
            parent.settimeout(None)
            notify(f'MAINPID={process.pid}\nSTATUS=Accepting new requests; previous generation draining')
        except Exception:
            parent.close()
            child.close()
            try:
                if process is not None:
                    process_runtime.terminate_process_tree(process, timeout=5)
            finally:
                if self.server.timing_proxy is not None:
                    self.server.timing_proxy.cancel_handover()
            raise
        self.server.preserve_socket = True
        self.relay = TimingRelay(parent)
        try:
            if self.server.timing_proxy is not None:
                self.server.timing_proxy.relay_timings(self.relay.put)
        except Exception:
            LOGGER.warning('Timing cache relay unavailable', exc_info=True)
        if self.server.timing_proxy is not None:
            self.server.timing_proxy.retire()
        LOGGER.info('ExecutionService handover %s -> %s; draining existing connections', os.getpid(), process.pid)

    def run(self):
        previous = process_runtime.install_shutdown_handlers(
            lambda *_: self.stop.set(),
            reload=(lambda *_: self.reload.set()) if _platform() == 'linux' else None,
        )
        try:
            return self._run()
        finally:
            process_runtime.restore_shutdown_handlers(previous)

    def _run(self):
        thread = threading.Thread(target=self._serve, name='execution-rpc', daemon=True)
        thread.start()
        try:
            while not self.server.started.wait(.1):
                if self.error:
                    raise self.error
                if not thread.is_alive():
                    raise RuntimeError('ExecutionService failed to start')
            if self.inherited.get('control') is not None:
                channel = socket.socket(fileno=self.inherited['control'])
                channel.sendall(b'R')
                self.predecessor = threading.Thread(target=self._receive_timings, args=(channel,), daemon=True,
                                                    name='timing-predecessor')
                self.predecessor.start()
            while not self.stop.wait(.1):
                if self.reload.is_set():
                    self.reload.clear()
                    try:
                        self.replace()
                    except Exception:
                        LOGGER.exception('ExecutionService replacement failed; keeping current generation')
                    else:
                        break
        finally:
            if thread.is_alive():
                self.server.shutdown()
                thread.join()
            if self.server.timing_proxy is not None and self.server.timing_proxy.thread.is_alive():
                self.server.timing_proxy.retire()
            if self.predecessor is not None:
                self.predecessor.join()
            self.server.server_close()
            if self.relay is not None:
                self.relay.close()
        if self.error:
            raise self.error
        return 0
