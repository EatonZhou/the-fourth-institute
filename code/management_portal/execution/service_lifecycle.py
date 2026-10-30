"""Run and stop ExecutionService on all supported platforms.

A new build replaces ExecutionService through the native service manager
(systemd, launchd or the Windows SCM) restarting it. The running service never
starts its own successor; Workers hand their executions over separately.
"""
from __future__ import annotations

import sys
import threading

from management_common import process_runtime


def _platform():
    if sys.platform not in {'linux', 'darwin', 'win32'}:
        raise NotImplementedError(f'Unsupported ExecutionService platform: {sys.platform}')
    return sys.platform


class ServiceLifecycle:
    def __init__(self, server):
        _platform()
        self.server = server
        self.stop = threading.Event()
        self.error = None

    def _serve(self):
        try:
            self.server.serve_forever()
        except BaseException as error:
            self.error = error
            self.stop.set()

    def run(self):
        previous = process_runtime.install_shutdown_handlers(lambda *_: self.stop.set())
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
            # A bounded wait lets the main thread run signal handlers on every platform.
            while not self.stop.wait(.1):
                pass
        finally:
            if thread.is_alive():
                self.server.shutdown()
                thread.join()
            self.server.server_close()
        if self.error:
            raise self.error
        return 0
