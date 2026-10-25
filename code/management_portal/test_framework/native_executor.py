"""Windows Test executors independent of the Web process's Windows Job.

The lifetime lock only prevents duplicate launchers. RunRecords remains the
authority for claims, recovery, cancellation and results, on every platform.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading

from management_common import process_runtime
from management_common.file_lock import try_lock_descriptor, unlock_descriptor
from management_portal.test_framework.catalog import ROOT


def run_directory(state_root: Path, run_id: str) -> Path:
    if not re.fullmatch(r"testrun_[0-9a-f]{16}", run_id):
        raise ValueError("Invalid Test run identity")
    directory = state_root.resolve() / "run-artifacts" / run_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def executor_command(run_id: str, state_root: Path) -> list[str]:
    return [sys.executable, "-m", __name__, "--state-root", str(state_root.resolve()), run_id]


def executor_environment(state_root: Path, *, tau: bool) -> dict[str, str]:
    from management_portal.test_framework.executor import unit_environment, postgres_environment_file

    # Keep the native runtime's user profile, DLL and executable resolution;
    # never inherit unrelated Provider credentials from the Web environment.
    names = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
             "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "HOME"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in names}
    environment.update(unit_environment(state_root, tau=tau))
    environment.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    if tau:
        from scripts.portal_native_service_child import load_environment_files

        filename = postgres_environment_file()
        if not Path(filename).is_file():
            raise FileNotFoundError("TAU PostgreSQL environment file is missing")
        environment = load_environment_files([filename], environment)
    return environment


def launch(run_id: str, state_root: Path, *, tau: bool) -> None:
    directory = run_directory(state_root, run_id)
    environment = executor_environment(state_root, tau=tau)
    # The child acknowledges its lifetime lock, then redirects output to its
    # own log. Web may exit immediately afterwards without owning this tree.
    with (directory / "executor.log").open("ab") as log:
        os.chmod(log.name, 0o600)
        process = process_runtime.spawn_process(
            executor_command(run_id, state_root), cwd=ROOT, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log,
            creationflags=0x08000000 | 0x00004000,  # NO_WINDOW | BELOW_NORMAL_PRIORITY
            kill_on_owner_exit=False,
        )
    acknowledgements: queue.Queue[bytes] = queue.Queue()

    def receive() -> None:
        try:
            acknowledgements.put(process.stdout.readline())
        finally:
            process.stdout.close()

    threading.Thread(target=receive, daemon=True, name="test-executor-launch").start()
    try:
        acknowledgement = acknowledgements.get(timeout=30)
        if acknowledgement not in {b"ready\n", b"busy\n"}:
            raise RuntimeError("Windows 测试执行器未能启动；请查看本次运行的 executor.log")
    except BaseException:
        process_runtime.terminate_process_tree(process, timeout=3, force=True)
        raise
    # Reap the process while Web is alive. Dropping the handle on Web exit is
    # safe: this independent Job deliberately has no kill-on-close flag.
    threading.Thread(target=process.wait, daemon=True, name="test-executor-reaper").start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Own one native Windows Test executor")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("run_id")
    args = parser.parse_args(argv)
    directory = run_directory(args.state_root, args.run_id)
    with (directory / "executor.lock").open("a+b") as lock:
        if not try_lock_descriptor(lock.fileno()):
            sys.stdout.buffer.write(b"busy\n"); sys.stdout.flush()
            return 0
        try:
            # Import before acknowledging so missing runtime dependencies fail
            # at launch, not as an indefinitely queued Test run.
            from management_portal.test_framework.executor import main as execute

            sys.stdout.buffer.write(b"ready\n"); sys.stdout.flush()
            with (directory / "executor.log").open("ab", buffering=0) as log:
                os.dup2(log.fileno(), sys.stdout.fileno())
                os.dup2(log.fileno(), sys.stderr.fileno())
                return execute(["--state-root", str(args.state_root), args.run_id])
        finally:
            unlock_descriptor(lock.fileno())


if __name__ == "__main__":
    raise SystemExit(main())
