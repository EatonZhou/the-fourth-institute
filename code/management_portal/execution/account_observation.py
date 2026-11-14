"""Account observation lifetime; cancellation never owns a model execution."""
from contextlib import contextmanager
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
import threading
import socket

_current = ContextVar("account_observation", default=None)


class ObservationLifetime:
    def __init__(self):
        self.stopped = threading.Event()
        self._lock = threading.Lock()
        self._callbacks = set()

    def close(self):
        with self._lock:
            self.stopped.set()
            callbacks = list(self._callbacks)
        if callbacks:
            # Reap independent owned observers concurrently, and wait for all.
            with ThreadPoolExecutor(max_workers=len(callbacks)) as pool:
                list(pool.map(lambda callback: callback(), callbacks))

    @contextmanager
    def scope(self):
        token = _current.set(self)
        try:
            check_cancelled()
            yield
        finally:
            _current.reset(token)


def cancelled():
    owner = _current.get()
    return owner is not None and owner.stopped.is_set()


def active():
    return _current.get() is not None


def check_cancelled():
    if cancelled():
        raise InterruptedError("Account observation stopped")


def interrupt_socket(sock):
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


@contextmanager
def urlopen(request, *, timeout):
    """Own the complete HTTP observation, including DNS, TCP and TLS setup.

    A short-lived process makes even an OS resolver that ignores socket
    timeouts cancellable. It runs only account observations, never model work.
    Credentials and response bytes travel through pipes, never argv or files.
    """
    from urllib.request import Request
    from management_common.loopback_http import urlopen as ordinary_open
    from urllib.error import HTTPError
    from urllib.response import addinfourl
    from io import BytesIO
    import base64
    import json
    import subprocess
    import sys
    from pathlib import Path
    from management_common.process_runtime import spawn_process, terminate_process_tree
    if _current.get() is None:
        with ordinary_open(request, timeout=timeout) as response:
            yield response
        return
    check_cancelled()
    req = Request(request) if isinstance(request, str) else request
    payload = json.dumps({"url": req.full_url, "headers": dict(req.header_items()),
        "method": req.get_method(), "body": base64.b64encode(req.data).decode() if req.data is not None else None,
        "timeout": timeout}).encode()
    process = spawn_process([sys.executable, "-c",
        "from management_portal.execution.account_observation import _http_main; _http_main()"],
        cwd=str(Path(__file__).resolve().parents[2]), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True)
    try:
        with cancel_resource(lambda: terminate_process_tree(process, timeout=1)):
            try:
                output, _error = process.communicate(payload, timeout=timeout)
            except subprocess.TimeoutExpired:
                raise TimeoutError("Account HTTP observation timed out") from None
            check_cancelled()
        if process.returncode:
            raise OSError("Account HTTP observation failed")
        result = json.loads(output)
        if result.get("failure") == "timeout":
            raise TimeoutError("Account HTTP observation timed out")
        if result.get("failure"):
            raise OSError("Account HTTP observation failed")
        raw = BytesIO(base64.b64decode(result["body"]))
        if result.get("error"):
            raise HTTPError(req.full_url, result["status"], "Account HTTP request failed", result["headers"], raw)
        with addinfourl(raw, result["headers"], req.full_url, result["status"]) as response:
            yield response
    finally:
        terminate_process_tree(process, timeout=1)
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()


def _http_main():
    import base64
    import json
    import sys
    from urllib.request import Request
    from management_common.loopback_http import urlopen
    from urllib.error import HTTPError
    spec = json.load(sys.stdin)
    request = Request(spec["url"], data=base64.b64decode(spec["body"]) if spec["body"] is not None else None,
                      headers=spec["headers"], method=spec["method"])
    error = False
    try:
        try:
            response = urlopen(request, timeout=spec["timeout"])
        except HTTPError as failure:
            response, error = failure, True
        with response:
            result = {"status": response.code, "headers": dict(response.headers), "error": error,
                      "body": base64.b64encode(response.read()).decode()}
    except Exception as failure:
        # The private pipe carries classification, never native diagnostics.
        result = {"failure": "timeout" if isinstance(failure, TimeoutError) or
                  isinstance(getattr(failure, "reason", None), TimeoutError) else "unavailable"}
    sys.stdout.write(json.dumps(result))


def login_status(command, *, env):
    """Own and reap a CLI credential status probe, never a login flow."""
    import subprocess
    from management_common.process_runtime import spawn_process, terminate_process_tree
    check_cancelled()
    process = spawn_process(command, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        with cancel_resource(lambda: terminate_process_tree(process, timeout=1)):
            output, error = process.communicate(timeout=60)
            check_cancelled()
            return subprocess.CompletedProcess(command, process.returncode, output, error)
    finally:
        terminate_process_tree(process, timeout=1)
        for pipe in (process.stdout, process.stderr):
            pipe.close()


@contextmanager
def cancel_resource(callback):
    owner = _current.get()
    if owner is None:
        yield
        return
    with owner._lock:
        stopping = owner.stopped.is_set()
        if not stopping:
            owner._callbacks.add(callback)
    try:
        if stopping:
            callback()
        check_cancelled()
        yield
    finally:
        with owner._lock:
            owner._callbacks.discard(callback)
