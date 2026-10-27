#!/usr/bin/env python3
"""Move Portal data while its services are stopped; run in a separate user unit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_common import portal_paths
from scripts.portal_background_jobs import job_alive, launch_job


JOBS_DIR = portal_paths.CONFIG_PATH.parent / "portal_path_jobs"
LOCK_PATH = JOBS_DIR / "active.lock"


def _atomic_json(target: Path, payload: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _job_path(job_id: str) -> Path:
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("Invalid migration ID")
    return JOBS_DIR / f"{job_id}.json"


def job_status(job_id: str) -> dict:
    return json.loads(_job_path(job_id).read_text(encoding="utf-8"))


def _update(job_id: str, **fields) -> dict:
    item = job_status(job_id)
    item.update(fields, updated_at=time.time())
    _atomic_json(_job_path(job_id), item)
    return item


def validate(target: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    if not isinstance(target, dict) or set(target) != set(portal_paths.KEYS):
        raise ValueError("Specify every Portal data directory")
    current = {key: str(value) for key, value in portal_paths.paths().items()}
    resolved = {key: str(portal_paths.resolve_directory(target[key])) for key in portal_paths.KEYS}
    roots = [Path(value) for value in resolved.values()]
    sources = [Path(value) for value in current.values()]
    for index, root in enumerate(roots):
        if any(root == other or root.is_relative_to(other) or other.is_relative_to(root)
               for other in roots[index + 1:]):
            raise ValueError("Portal data directories must not overlap")
        if root == ROOT or ROOT.is_relative_to(root):
            raise ValueError("Portal source code must not be inside a data directory")
        if portal_paths.CONFIG_PATH.is_relative_to(root):
            raise ValueError("Portal path configuration must remain outside data directories")
        if any(root == other or root.is_relative_to(other) or other.is_relative_to(root)
               for other in sources if other != sources[index]):
            raise ValueError("A destination overlaps another current data directory")
    for key in portal_paths.KEYS:
        source, destination = Path(current[key]), Path(resolved[key])
        if source == destination:
            continue
        if source.is_relative_to(destination) or destination.is_relative_to(source):
            raise ValueError(f"{key}: source and destination overlap")
        if source.exists() and source.is_mount():
            raise ValueError(f"{key}: the current directory is a mount point; move the mount before changing this path")
        if source.exists() and not source.is_dir():
            raise ValueError(f"{key}: the current path is not a directory")
        if destination.exists() and any(destination.iterdir()):
            raise ValueError(f"{key}: destination is not empty: {destination}")
    if current == resolved:
        raise ValueError("No directory changed")
    return current, resolved


def _clear_abandoned_lock() -> bool:
    try:
        old_id = LOCK_PATH.read_text(encoding="utf-8").strip()
        if not old_id or time.time() - LOCK_PATH.stat().st_mtime < 60:
            return False
        item = job_status(old_id)
        if job_alive(f"portal-path-migration-{old_id}", item.get("pid")):
            return False
        if item.get("state") not in {"completed", "failed"}:
            _update(old_id, state="failed", error="Background migration ended unexpectedly; inspect the service journal before retrying")
        if LOCK_PATH.read_text(encoding="utf-8").strip() == old_id:
            LOCK_PATH.unlink()
            return True
    except (OSError, ValueError, KeyError):
        return False
    return False


def _installation_root() -> Path:
    from management_common.instance_config import installed_root
    root = installed_root(portal_paths.CONFIG_PATH)
    if root is None:
        raise RuntimeError("Portal path migration requires an installed TFI instance")
    return root


def queue_migration(target: dict[str, str]) -> dict:
    current, resolved = validate(target)
    _installation_root()
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        if not _clear_abandoned_lock():
            raise RuntimeError("Another Portal path migration is active") from exc
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    job_id = uuid.uuid4().hex
    with os.fdopen(fd, "w", encoding="utf-8") as lock:
        lock.write(job_id)
    item = {"id": job_id, "state": "queued", "source": current, "target": resolved,
            "created_at": time.time(), "updated_at": time.time()}
    try:
        _atomic_json(_job_path(job_id), item)
        variables = ("PORTAL_PATHS_CONFIG", *portal_paths.ENV_KEYS.values())
        pid = launch_job(
            f"portal-path-migration-{job_id}",
            [sys.executable, str(Path(__file__).resolve()), "run", job_id],
            cwd=ROOT, log_path=JOBS_DIR / f"{job_id}.log",
            environment={name: os.environ[name] for name in variables if name in os.environ},
        )
        if pid is not None:
            item = _update(job_id, pid=pid)
    except Exception:
        LOCK_PATH.unlink(missing_ok=True)
        _job_path(job_id).unlink(missing_ok=True)
        raise
    return item


def _fingerprint(root: Path) -> dict[str, tuple]:
    if not root.exists():
        return {}
    found = {}
    for item in sorted(root.rglob("*")):
        relative = str(item.relative_to(root))
        if item.is_symlink():
            found[relative] = ("link", os.readlink(item))
        elif item.is_file():
            digest = hashlib.sha256()
            with item.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            found[relative] = ("file", item.stat().st_size, digest.hexdigest())
        elif item.is_dir():
            found[relative] = ("dir",)
    return found


def _copy_and_verify(source: Path, destination: Path) -> None:
    if not source.exists():
        destination.mkdir(parents=True, exist_ok=True)
        return
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"Data root is not a directory: {source}")
    def ignore_special(directory: str, names: list[str]) -> set[str]:
        return {name for name in names if not (
            stat.S_ISREG(mode := os.lstat(Path(directory) / name).st_mode)
            or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)
        )}

    shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True, ignore=ignore_special)
    if _fingerprint(source) != _fingerprint(destination):
        raise RuntimeError(f"Copy verification failed: {source}")


def run_installed(job_id, root):
    """Installed sites use the deployment owner and never create old-path aliases."""
    from management_common.instance_config import read_installation, configuration_digest
    from scripts import tfi_release_install as api
    from scripts.tfi_release_upgrade import check_quiescent
    from scripts.tfi_instance_config import apply
    item = job_status(job_id)
    state = read_installation(root)
    original = state['target']
    if original['data_roots'] != item['source']:
        raise ValueError('Instance data roots changed after migration was queued')
    candidate = {**original, 'data_roots': item['target']}
    # Refuse active work before the bounded shutdown; no execution is cancelled
    # to make a storage change succeed.
    stopped = switched = False
    try:
        check_quiescent(api, original)
        _update(job_id, state='stopping')
        api.lifecycle('stop', root)
        stopped = True
        _update(job_id, state='copying')
        for key in portal_paths.KEYS:
            source, destination = Path(item['source'][key]), Path(item['target'][key])
            if source != destination:
                _copy_and_verify(source, destination)
        _update(job_id, state='switching')
        apply(root, candidate, expected_sha256=configuration_digest(original))
        switched = True
        _update(job_id, state='restarting')
        api.lifecycle('start', root)
        _update(job_id, state='completed', old_directories_retained=True, aliases_created=False)
    except Exception as exc:
        # A partially applied configuration has its own durable journal. Do not
        # start consumers or overwrite it until its explicit recovery completes.
        journal = Path(root) / 'maintenance/configuration-update.json'
        interrupted = journal.exists() and json.loads(journal.read_text())['status'] in {'applying', 'interrupted'}
        if switched:
            api.lifecycle('stop', root)
            apply(root, original, expected_sha256=configuration_digest(candidate))
        if stopped and not interrupted:
            api.lifecycle('start', root)
        _update(job_id, state='failed', error=str(exc), configuration_recovery_required=interrupted)
        raise
    finally:
        if LOCK_PATH.exists() and LOCK_PATH.read_text().strip() == job_id:
            LOCK_PATH.unlink()


def run(job_id: str) -> None:
    if LOCK_PATH.read_text(encoding="utf-8") != job_id:
        raise RuntimeError("Migration lock no longer belongs to this job")
    try:
        root = _installation_root()
    except RuntimeError as exc:
        _update(job_id, state="failed", error=str(exc))
        LOCK_PATH.unlink(missing_ok=True)
        raise
    return run_installed(job_id, root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("job_id")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(job_status(args.job_id), ensure_ascii=False))
    else:
        run(args.job_id)
