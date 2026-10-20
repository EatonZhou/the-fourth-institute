"""Idempotent transfer of one Resident directory, outside all state locks."""
from __future__ import annotations

import errno
import hashlib
import json
import shutil
from pathlib import Path

from . import identity_contract, invariant_locks
from .folder_removal import remove_tree


_MARKER = ".portal-resident-transfer.json"


def _paths(source: str, target: str) -> tuple[Path, Path]:
    source_path, target_path = Path(source), Path(target)
    if (
        not source_path.is_absolute() or not target_path.is_absolute()
        or source_path == target_path
        or source_path.name != target_path.name
        or not identity_contract.is_valid_id(source_path.name, "resident")
        or source_path.parent.name != "residents" or target_path.parent.name != "residents"
        or source_path.is_symlink() or target_path.is_symlink()
    ):
        raise ValueError("Workspace transfer requires two exact Resident directories")
    return source_path, target_path


def _claim(directory: Path, proof: str) -> None:
    marker = directory / _MARKER
    if marker.exists():
        if marker.is_symlink() or marker.read_text(encoding="utf-8") != proof:
            raise FileExistsError("Workspace transfer belongs to another operation")
        return
    with marker.open("x", encoding="utf-8") as stream:
        stream.write(proof)


def relocate(operation_id: str, source: str, target: str) -> None:
    """A crash after publish is recognized by an operation-owned target marker.

    Cross-device copy is staged on the destination filesystem. The source is
    retained until the complete tree has been published; retry never recopies
    a partially removed source over the complete destination.
    """
    if invariant_locks.lock_held():
        raise RuntimeError("Workspace transfer must run outside state scopes")
    source_path, target_path = _paths(source, target)
    proof = json.dumps({"operation": operation_id, "source": source, "target": target}, sort_keys=True)
    if target_path.exists():
        marker = target_path / _MARKER
        if not marker.is_file() or marker.is_symlink() or marker.read_text(encoding="utf-8") != proof:
            raise FileExistsError("Workspace transfer target already exists")
    else:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if not source_path.exists():
            raise FileNotFoundError("Workspace transfer source is missing")
        _claim(source_path, proof)
        try:
            source_path.rename(target_path)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            suffix = hashlib.sha256(operation_id.encode()).hexdigest()[:24]
            staging = target_path.with_name(f".{target_path.name}.transfer-{suffix}")
            if staging.is_symlink():
                raise ValueError("Workspace staging must not be a symlink")
            if staging.exists() and not (staging / _MARKER).exists() and any(staging.iterdir()):
                raise FileExistsError("Workspace staging is not owned by this operation")
            staging.mkdir(exist_ok=True)
            _claim(staging, proof)
            shutil.copytree(source_path, staging, dirs_exist_ok=True, symlinks=True)
            staging.rename(target_path)
    if source_path.exists():
        # The validated, exact Resident source is removed only after an
        # operation-owned complete destination is available for recovery.
        remove_tree(source_path)


def finish(operation_id: str, source: str, target: str) -> None:
    """Remove the marker only in a later durable step, after relocate commits."""
    _source_path, target_path = _paths(source, target)
    marker = target_path / _MARKER
    if not marker.exists():
        return
    proof = json.dumps({"operation": operation_id, "source": source, "target": target}, sort_keys=True)
    if marker.is_symlink() or marker.read_text(encoding="utf-8") != proof:
        raise FileExistsError("Workspace transfer belongs to another operation")
    marker.unlink()
