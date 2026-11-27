#!/usr/bin/env python3
"""Check provider updates from a System Lineage script or a manual request."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import psycopg
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_portal.execution.provider_activation import RECONCILE_SECONDS, pending_update_listener
from management_portal.execution.provider_updates import (
    ACTIVATION_WAIT_SECONDS, activate_pending_provider, pending_activation_failures, pending_provider_updates, run_update_pass, runtimes,
    wait_for_installation_unlock,
)
from management_common.instance_manifest import load, instance_runtime_root
from management_common.code_environment import runtime_python
from scripts.provider_model_catalog_sync import run as sync_model_catalogs


def _watch_pending() -> int:
    deadline = time.monotonic() + ACTIVATION_WAIT_SECONDS
    active = {}
    failed = {}
    reader, writer = socket.socketpair()
    with reader, writer, ExitStack() as subscriptions, ThreadPoolExecutor(
        max_workers=max(1, len(runtimes())), thread_name_prefix="provider-activation",
    ) as pool:
        listener = None
        while True:
            if listener is None:
                try:
                    listener = subscriptions.enter_context(pending_update_listener())
                    failed = pending_activation_failures()
                except psycopg.OperationalError:
                    if time.monotonic() >= deadline:
                        return 2
                    time.sleep(min(1, max(0, deadline - time.monotonic())))
                    continue
            for provider, (path, future) in list(active.items()):
                if future.done():
                    result = future.result()
                    print(json.dumps({"provider": provider, **result}, ensure_ascii=False), flush=True)
                    del active[provider]
                    if result["status"] not in {"activated", "superseded"}:
                        failed[provider] = path

            pending = pending_provider_updates()
            if pending is None:
                if not wait_for_installation_unlock(deadline):
                    return 2
                continue
            if time.monotonic() >= deadline:
                return 2 if active or pending else 0
            for provider, path in pending.items():
                if provider not in active and failed.get(provider) != path:
                    future = pool.submit(activate_pending_provider, provider, path, deadline)
                    active[provider] = (path, future)
                    future.add_done_callback(lambda _: writer.sendall(b"x"))
            if not active:
                return 1 if pending else 0

            # A missed notice can delay re-reading the local candidate list
            # by at most one reconciliation interval while work is pending.
            timeout = min(RECONCILE_SECONDS, max(0, deadline - time.monotonic()))
            try:
                ready, _, _ = select.select([listener, reader], [], [], timeout)
                if not ready:
                    failed = pending_activation_failures()
                if reader in ready:
                    reader.recv(4096)
                if listener in ready:
                    for notice in listener.notifies(timeout=0):
                        for provider in json.loads(notice.payload):
                            failed.pop(provider, None)
            except psycopg.OperationalError:
                subscriptions.close()
                listener = None  # Subscribe again before reading the durable queue.


def _start_job(mode: str) -> str:
    """Run updates outside account services, as an instance-owned job."""
    from scripts.instance_services import job_unit
    from scripts.tfi_shutdown import verify_job
    instance = load()
    unit = job_unit(instance, "provider-activation")
    verify_job(unit, instance.root)
    active = subprocess.run(("systemctl", "--user", "is-active", "--quiet", unit), check=False)
    if active.returncode == 0:
        return unit
    subprocess.run(
        ("systemd-run", "--user", "--collect", f"--unit={unit}",
         '--setenv=PORTAL_INSTANCE_ROOT=' + str(instance.root),
         str(runtime_python(instance.code_root())), str(instance.code_root() / 'scripts/update_provider_runtimes.py'), mode),
        check=True, capture_output=True, text=True,
    )
    return unit


def main(arguments):
    if arguments == ["--sync-model-catalogs"]:
        result = sync_model_catalogs()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return 0 if result["status"] in {"ok", "skipped"} else 1
    if arguments == ["--activate-pending"]:
        return _watch_pending()
    if not arguments:
        # A Resident's command belongs to its native account service cgroup.
        # The service being upgraded must not kill its own updater midway.
        unit = _start_job("--run-update")
        print(json.dumps({"accepted": True, "unit": unit,
            "result_path": str(instance_runtime_root() / "provider-update-result.json")}, sort_keys=True), flush=True)
        return 0
    lineage_run = arguments == ["--lineage-update"]
    if not lineage_run and arguments != ["--run-update"]:
        raise SystemExit("Usage: update_provider_runtimes.py [--activate-pending|--lineage-update|--sync-model-catalogs]")
    result = run_update_pass()
    if lineage_run:
        result["model_catalogs"] = sync_model_catalogs()
    catalog_failed = result.get("model_catalogs", {}).get("status") in {"failed", "partial"}
    path = instance_runtime_root() / "provider-update-result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"finished_at": time.time(), **result}, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    if result.get("pending") or result.get("busy"):
        if lineage_run:
            # RunScript already owns an independent process. Keep its daily
            # check finite; waiting for idle providers belongs to the watcher.
            _start_job("--activate-pending")
            return 1 if result.get("errors") or result.get("busy") or catalog_failed else 0
        return _watch_pending()
    return 1 if result.get("errors") or catalog_failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
