"""Shutdown evidence leaves lifecycle and in-flight requests unchanged.

:module: execution.service
:design: decision_25eb93d803a4
"""
import logging
import subprocess
import sys
import threading

from management_portal.execution.shutdown_diagnostics import ShutdownTrace


def test_slow_trace_reports_phase_once_without_locals(tmp_path, caplog):
    sampled = threading.Event()
    with (tmp_path / "stacks").open("w+") as stream:
        trace = ShutdownTrace(slow_seconds=.05, stream=stream)
        original = trace._slow
        def dump():
            original()
            sampled.set()
        trace._slow = dump
        secret_local_value = "MUST_NOT_APPEAR_IN_TRACE"
        try:
            with caplog.at_level(logging.WARNING):
                trace.begin()
                with trace.phase("rpc_threads"):
                    assert sampled.wait(2)
                trace.process_exit()
            stream.seek(0)
            stack = stream.read()
            assert "test_slow_trace_reports_phase_once_without_locals" in stack
            assert secret_local_value not in stack
            assert caplog.text.count("shutdown slow") == 1
            assert "phase=rpc_threads" in caplog.text
            assert "elapsed_seconds=" in caplog.text
        finally:
            trace.cancel()


def test_process_exit_wait_is_observed_without_abandoning_thread():
    code = '''
import threading,time,logging
from management_portal.execution.shutdown_diagnostics import ShutdownTrace
logging.basicConfig(level=logging.WARNING)
def pending_rpc(): time.sleep(.3)
threading.Thread(target=pending_rpc, daemon=False).start()
trace=ShutdownTrace(slow_seconds=.05)
trace.begin()
trace.process_exit()
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert "phase=process_exit" in result.stderr and "shutdown slow" in result.stderr
    assert "pending_rpc" in result.stderr
    assert result.stderr.count("thread stacks follow") == 1
