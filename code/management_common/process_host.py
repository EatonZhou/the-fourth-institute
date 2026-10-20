"""Detached daemon host: survive a client handover, own the daemon's children.

The launcher detaches this host. This host keeps the managed tree handle until
the daemon exits, including when it exits before one of its children.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import threading

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.process_runtime import install_shutdown_handlers, restore_shutdown_handlers, spawn_process, terminate_process_tree


def run(command):
    if not command:
        raise ValueError("A daemon command is required")
    stop = threading.Event()
    previous = install_shutdown_handlers(lambda *_: stop.set())
    child = None
    try:
        child = spawn_process(command, stdin=subprocess.DEVNULL)
        while not stop.wait(0.1):
            result = child.poll()
            if result is not None:
                return result
        return 0
    finally:
        try:
            if child is not None:
                terminate_process_tree(child, timeout=15)
        finally:
            restore_shutdown_handlers(previous)


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
