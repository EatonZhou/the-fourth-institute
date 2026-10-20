"""Progress records on stderr, separate from installer JSON responses."""
import json
import sys
import threading
from contextlib import contextmanager

PREFIX = "@TFI_PROGRESS "


def report(message):
    print(PREFIX + json.dumps({"message": message}, ensure_ascii=False),
          file=sys.stderr, flush=True)


def progress_message(line):
    if line.startswith(PREFIX):
        try:
            value = json.loads(line[len(PREFIX):])
            message = value.get("message") if isinstance(value, dict) else None
            if isinstance(message, str) and len(message) <= 512:
                return message
        except ValueError:
            pass
    return None


@contextmanager
def relay_log_progress(path):
    """Forward only progress records from a child log, including its last line."""
    stopped = threading.Event()
    position = path.stat().st_size if path.exists() else 0

    def relay():
        pending = b""
        with path.open("rb") as stream:
            stream.seek(position)
            while True:
                # Observe shutdown before reading: a child may append its last
                # record between an EOF read and the stop signal.
                draining = stopped.is_set()
                chunk = stream.read(64 * 1024)
                if chunk:
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        message = progress_message(line.decode("utf-8", errors="replace"))
                        if message is not None:
                            report(message)
                    if len(pending) > 65536:
                        pending = b""
                elif draining:
                    break
                else:
                    stopped.wait(0.2)

    thread = threading.Thread(target=relay, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join()
