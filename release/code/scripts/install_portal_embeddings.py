#!/usr/bin/env python3
"""Detached Portal embedding installer started by Portal Management."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from management_portal.embedding_setup import _write_job, job_status, runtime_python  # noqa: E402
from management_common.process_runtime import install_shutdown_handlers, restore_shutdown_handlers, run_process


def main() -> int:
    def interrupted(*_):
        raise InterruptedError("Embedding installation was stopped")
    previous = install_shutdown_handlers(interrupted)
    try:
        current = job_status()
        _write_job({"state": "running", "started_at": current.get("started_at"), "pid": os.getpid()})
        if sys.prefix == sys.base_prefix:
            raise RuntimeError("Start Portal in its Python virtual environment before installing embeddings")
        command = [str(runtime_python()), str(ROOT / "scripts" / "bootstrap_portal"),
                   "install", "embeddings", "--venv", sys.prefix]
        run_process(command, cwd=ROOT, check=True)
        _write_job({"state": "complete", "completed_at": datetime.now(timezone.utc).isoformat()})
        return 0
    except Exception as exc:
        _write_job({"state": "failed", "error": str(exc),
                    "completed_at": datetime.now(timezone.utc).isoformat()})
        return 1
    finally:
        restore_shutdown_handlers(previous)


if __name__ == "__main__":
    raise SystemExit(main())
