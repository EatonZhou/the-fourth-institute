#!/usr/bin/env python3
"""Run one manifest command through RunScript's execution lifecycle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_portal.watchboards.commands import argument_words, command_words


def main():
    from portal_script import inputs

    root = Path(sys.argv[1]).resolve()
    command = sys.argv[2]
    event = inputs().get("arguments") or {}
    spec = event.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("command definition must be supplied by the Effect")
    entrypoint = (root / event["entrypoint"]).resolve()
    entrypoint.relative_to(root)
    args = json.loads(event.get("prompt") or "{}")
    if not isinstance(args, dict):
        raise ValueError("command arguments must be an object")
    os.chdir(root)
    argv = [sys.executable, str(entrypoint),
            *command_words(command, spec), *argument_words(spec, args)]
    if sys.platform == "win32":
        # Windows exec exits the original process before its replacement ends.
        # RunScript would then finish and reap that replacement's enclosing Job.
        # Wait in the owned wrapper and inherit stdout/stderr; nested Jobs keep
        # cancellation and descendants inside the original ScriptRun lifetime.
        from management_common.process_runtime import run_process
        raise SystemExit(run_process(argv).returncode)
    # POSIX exec preserves the process identity and its owned process group.
    os.execv(sys.executable, argv)


if __name__ == "__main__":
    main()
