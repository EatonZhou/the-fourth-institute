#!/usr/bin/env python3
"""Move one data area of a registered instance (protocol「移动区域」).

``submit`` checks the request and launches an independent control job, so
the job outlives the Web process it stops. The job stops the instance,
moves the area and switches its manifest entry, starts PostgreSQL, moves
the Provider sessions keyed by working directory, starts the instance and
then deletes a copied original. Its record is the instance's control record
(``runtime/portal-control.json``), which also serializes it with start/stop.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

CODE = Path(__file__).resolve().parents[1]
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))
sys.dont_write_bytecode = True

from management_common import host_instances
from management_common.code_environment import runtime_python
from management_common.configuration_files import atomic_json
from management_common.filesystem_paths import filesystem_path
from management_common.instance_areas import move_area, validate_target
from management_common.instance_manifest import absolute_path, load
from scripts import instance_services
from scripts.tfi_instance_manager import control_lock, control_path, operation, read_json, supervisor_environment

RECOVERY_RECORD = 'runtime/area-relocation.json'


def recovery_record(root):
    path = root / RECOVERY_RECORD
    if path.is_symlink():
        raise ValueError('Area relocation record cannot be redirected')
    return read_json(path) if path.is_file() else None


def _launch(root: Path, record: dict) -> None:
    from management_common.instance_manifest import load
    instance = load(root)
    args = [str(runtime_python(instance.code_root())), str(Path(__file__).resolve()), "_run",
            "--name", instance.name, "--operation-id", record["id"]]
    env = {**supervisor_environment(), "PORTAL_INSTANCE_ROOT": str(instance.root)}
    log = root / "runtime/portal-control.log"
    if log.is_symlink():
        raise ValueError("Invalid instance control log")
    if sys.platform.startswith("linux"):
        subprocess.run(["systemd-run", "--user", "--collect", "--quiet",
                        "--unit", record["unit"], "--property=Type=exec",
                        "--property=UMask=0077", f"--property=WorkingDirectory={CODE}",
                        f"--property=StandardOutput=append:{log}",
                        f"--property=StandardError=append:{log}",
                        "--setenv=PORTAL_INSTANCE_ROOT=" + str(instance.root), *args],
                       env=env, check=True, capture_output=True, timeout=45)
    elif sys.platform == "win32":
        with log.open("ab") as output:
            child = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL,
                                     stdout=output, stderr=output, close_fds=True,
                                     creationflags=subprocess.DETACHED_PROCESS
                                     | subprocess.CREATE_NEW_PROCESS_GROUP
                                     | subprocess.CREATE_BREAKAWAY_FROM_JOB)
            record["pid"] = child.pid
    elif sys.platform == "darwin":
        subprocess.run(["launchctl", "submit", "-l", record["unit"],
                        "-o", str(log), "-e", str(log), "--", *args],
                       env=env, check=True, capture_output=True, timeout=45)
    else:
        raise ValueError("Area moves are not supported on this platform")


def submit(name: str, area: str, target: str) -> dict:
    instance = host_instances.find(name)
    root = instance.root
    with control_lock(root):
        prior = operation(root)
        if prior and prior["status"] in {"queued", "running"}:
            raise ValueError("An operation is already running for this instance")
        recovery = recovery_record(root)
        if recovery:
            resolved = absolute_path(target)
            if (recovery['instance_id'] != instance.instance_id or recovery['area'] != area
                    or Path(recovery['target']) != resolved):
                raise ValueError('Finish the pending area move with its original area and target')
            if instance.area(area) not in {Path(recovery['source']), resolved}:
                raise ValueError('Area location changed since the pending move')
        else:
            resolved = validate_target(instance, area, target)
        identifier = uuid.uuid4().hex
        record = {"id": identifier, "unit": f"tfi-control-{identifier}", "action": "move-area",
                  "area": area, "source": recovery['source'] if recovery else str(instance.area(area)), "target": str(resolved),
                  "status": "queued", "stage": "queued", "created_at": time.time()}
        atomic_json(control_path(root), record)
        try:
            _launch(root, record)
            atomic_json(control_path(root), record)
        except Exception:
            record.update(status="failed", stage="failed", error="Unable to launch the independent area move")
            atomic_json(control_path(root), record)
            raise
    return {"name": name, "operation": record}


def _stage(root: Path, record: dict, stage: str, **fields) -> None:
    record.update(stage=stage, **fields)
    atomic_json(control_path(root), record)


def _start_database(root: Path) -> None:
    from management_common.instance_manifest import load
    instance = load(root)
    instance_services.controller(instance).start(instance_services.unit_name(instance, "postgres"))
    instance_services.wait_database(instance)


def execute(name: str, identifier: str) -> dict:
    # Allow the Web server to deliver its response before stopping itself.
    time.sleep(2)
    root = host_instances.find(name).root
    with control_lock(root):
        record = read_json(control_path(root))
        if record["id"] != identifier or record["status"] != "queued" or record["action"] != "move-area":
            raise ValueError("Area move no longer matches")
        record.update(status="running", pid=os.getpid())
        atomic_json(control_path(root), record)
        return _execute_locked(root, record)


def _execute_locked(root, record):
    instance = load(root)
    recovery = {key: record[key] for key in ('area', 'source', 'target')}
    recovery.update(instance_id=instance.instance_id, status='pending')
    current = instance.area(record['area'])
    if current not in {Path(record['source']), Path(record['target'])}:
        raise ValueError('Area location changed before the move')
    stopped = False
    moved = current == Path(record['target'])
    # Persist the original prefix before moving files: a later retry must not
    # mistake the manifest's new location for the sessions' old directory.
    atomic_json(root / RECOVERY_RECORD, recovery)
    try:
        _stage(root, record, "stopping")
        instance_services.lifecycle("stop", root)
        stopped = True
        _stage(root, record, "moving")
        result = ({'source': record['source'], 'target': record['target'],
                   'old_copy_retained': Path(record['source']).exists()} if moved
                  else move_area(root, record["area"], record["target"]))
        moved = True
        _stage(root, record, "starting")
        _start_database(root)
        from management_portal.project_rename import relocate_provider_sessions
        sessions = relocate_provider_sessions(result["source"], result["target"])
        record['provider_sessions'] = sessions
        if sessions['errors'] or sessions['conflicts']:
            raise RuntimeError('Provider session relocation is incomplete; retry this area and target')
        atomic_json(root / RECOVERY_RECORD, {**recovery, 'status': 'completed'})
        instance_services.lifecycle("start", root)
        if result["old_copy_retained"]:
            _stage(root, record, "cleaning")
            import shutil
            shutil.rmtree(filesystem_path(result["source"]))
        (root / RECOVERY_RECORD).unlink()
        record.update(status="completed", stage="completed")
    except Exception as exc:
        record.update(status="failed", stage="failed", error=str(exc), area_moved=moved)
        if stopped:
            try:
                if not moved:
                    (root / RECOVERY_RECORD).unlink(missing_ok=True)
                instance_services.lifecycle("stop" if moved else "start", root)
            except Exception as restart:
                record["restart_error"] = str(restart)
    record["finished_at"] = time.time()
    atomic_json(control_path(root), record)
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("submit", "_run"))
    parser.add_argument("--name", required=True)
    parser.add_argument("--area")
    parser.add_argument("--target")
    parser.add_argument("--operation-id")
    args = parser.parse_args(argv)
    try:
        result = (execute(args.name, args.operation_id) if args.action == "_run"
                  else submit(args.name, args.area, args.target))
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get('status') == 'failed' else 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
