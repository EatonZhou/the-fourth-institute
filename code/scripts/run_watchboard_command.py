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
    # exec keeps cancellation, stdout/stderr and exit status owned by RunScript.
    os.execv(sys.executable, [sys.executable, str(entrypoint),
                             *command_words(command, spec), *argument_words(spec, args)])


if __name__ == "__main__":
    main()
