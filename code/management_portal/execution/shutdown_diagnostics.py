"""Low-volume shutdown phase timings and one slow-process thread snapshot."""
from contextlib import contextmanager
import atexit
import faulthandler
import logging
import os
import sys
import threading
import time

LOGGER = logging.getLogger(__name__)


class ShutdownTrace:
    def __init__(self, *, slow_seconds=3.0, stream=None):
        self.slow_seconds = slow_seconds
        self.stream = stream if stream is not None else sys.stderr
        self.current = "not_started"
        self.started_at = None
        self.timer = None

    def begin(self):
        if self.started_at is not None:
            return
        self.started_at = time.monotonic()
        self.timer = threading.Timer(self.slow_seconds, self._slow)
        self.timer.name = "execution-shutdown-trace"
        self.timer.daemon = True
        # Keep watching after run() returns: Python may still be waiting for
        # non-daemon RPC/pool threads before ordinary atexit callbacks run.
        atexit.register(self.cancel)
        self.timer.start()

    def cancel(self):
        if self.timer is not None:
            self.timer.cancel()

    def _slow(self):
        LOGGER.warning("Execution shutdown slow pid=%s phase=%s elapsed_seconds=%.3f; thread stacks follow",
                       os.getpid(), self.current, time.monotonic() - self.started_at)
        # Python file/function/line stacks only; never locals, prompts or args.
        faulthandler.dump_traceback(file=self.stream, all_threads=True)

    @contextmanager
    def phase(self, name):
        parent, self.current = self.current, name
        started = time.monotonic()
        LOGGER.warning("Execution shutdown phase=%s event=begin pid=%s", name, os.getpid())
        try:
            yield
        finally:
            LOGGER.warning("Execution shutdown phase=%s event=end pid=%s elapsed_seconds=%.3f",
                           name, os.getpid(), time.monotonic() - started)
            self.current = parent

    def process_exit(self):
        self.current = "process_exit"
        LOGGER.warning("Execution shutdown phase=process_exit event=begin pid=%s elapsed_seconds=%.3f",
                       os.getpid(), time.monotonic() - self.started_at)
