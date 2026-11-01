"""Own one instance daemon process tree without locking its shared account home."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.process_runtime import (
    spawn_process, terminate_process_tree, install_shutdown_handlers, restore_shutdown_handlers,
)


def run(command):
    stopped = threading.Event()
    previous = install_shutdown_handlers(lambda *_: stopped.set())
    try:
        child = spawn_process(command)
        try:
            while not stopped.wait(.1):
                code = child.poll()
                if code is not None:
                    return code
            return 0
        finally:
            terminate_process_tree(child, timeout=30)
    finally:
        restore_shutdown_handlers(previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A daemon command is required")
    return run(command)


if __name__ == "__main__":
    raise SystemExit(main())
