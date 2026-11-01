"""Area locations, their usage, and moving one area (protocol「移动区域」).

Moving an area changes one manifest entry. Records keep ``area:relative``
references, so no database row changes. The caller stops the instance's
services first and starts them again afterwards.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import shutil
import stat

from .instance_manifest import AREAS, FIXED_DIRECTORIES, InstanceManifest, absolute_path, load, validate


def directory_size(path: Path) -> int:
    """Bytes of regular files below ``path``; links are not followed."""
    total = 0
    if not path.is_dir() or path.is_symlink():
        return 0
    for parent, directories, files in os.walk(path, onerror=lambda _error: None):
        for name in files:
            try:
                info = os.lstat(os.path.join(parent, name))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def _existing_parent(path: Path) -> Path:
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def _location(path: Path, *, measure: bool) -> dict:
    disk = shutil.disk_usage(_existing_parent(path))
    return {"path": str(path), "exists": path.is_dir(),
            "size": directory_size(path) if measure else None, "free": disk.free}


def area_report(instance: InstanceManifest, *, measure: bool = True) -> dict:
    """The data directory page: instance root, each area and the fixed directories."""
    configured = instance.document.get("areas", {})
    return {
        "root": _location(instance.root, measure=False),
        "areas": [{"name": name, "configured": name in configured,
                   "default": str(instance.root / "data" / name),
                   **_location(instance.area(name), measure=measure)} for name in AREAS],
        "fixed": [{"name": name, **_location(instance.directory(name), measure=measure)}
                  for name in FIXED_DIRECTORIES],
    }


def candidate_manifest(instance: InstanceManifest, area: str, target: Path) -> InstanceManifest:
    """The manifest with ``area`` at ``target``; the default location is not written."""
    document = deepcopy(instance.document)
    areas = dict(document.get("areas", {}))
    if target == instance.root / "data" / area:
        areas.pop(area, None)
    else:
        areas[area] = str(target)
    if areas:
        document["areas"] = areas
    else:
        document.pop("areas", None)
    return validate(instance.root, document)


def validate_target(instance: InstanceManifest, area: str, target: str | Path) -> Path:
    """Check a requested new location before any file moves."""
    from .host_instances import validate_allocation
    if area not in AREAS:
        raise ValueError("Unknown data area")
    target = absolute_path(target)
    source = instance.area(area)
    if target == source:
        raise ValueError("The area is already at this location")
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("The new location must not contain or be inside the current one")
    if target.exists() and (not target.is_dir() or target.is_symlink() or any(target.iterdir())):
        raise ValueError("The new location must be an empty directory or not exist yet")
    # Overlap with code, fixed directories, other areas and other instances.
    validate_allocation(candidate_manifest(instance, area, target))
    parent = _existing_parent(target)
    if source.exists() and parent.stat().st_dev != source.stat().st_dev:
        needed = directory_size(source)
        if shutil.disk_usage(parent).free < needed:
            raise ValueError("Not enough free space at the new location")
    return target


def _fingerprint(root: Path) -> dict[str, tuple]:
    found = {}
    for item in sorted(root.rglob("*")):
        relative = item.relative_to(root).as_posix()
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


def _copy_verified(source: Path, target: Path) -> None:
    def special(directory: str, names: list[str]) -> set[str]:
        return {name for name in names if not (
            stat.S_ISREG(mode := os.lstat(Path(directory) / name).st_mode)
            or stat.S_ISDIR(mode) or stat.S_ISLNK(mode))}
    shutil.copytree(source, target, symlinks=True, ignore=special, dirs_exist_ok=True)
    if _fingerprint(source) != _fingerprint(target):
        raise RuntimeError(f"Copy verification failed: {source}")


def move_area(root: str | Path, area: str, target: str | Path) -> dict:
    """Move one area while the instance's services are stopped.

    The same filesystem renames the directory; another one copies and
    verifies it, and the caller deletes the old copy once the services run
    at the new location. The caller also moves the Provider sessions keyed
    by working directory. A failure before the manifest is switched leaves
    the area where it was.
    """
    from .host_instances import replace_manifest
    instance = load(root)
    target = validate_target(instance, area, target)
    source = instance.area(area)
    candidate = candidate_manifest(instance, area, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    renamed = copied = False
    try:
        if not source.exists():
            target.mkdir(exist_ok=True)
        elif source.stat().st_dev == target.parent.stat().st_dev:
            if target.exists():
                target.rmdir()  # validate_target accepted only an empty directory
            os.rename(source, target)
            renamed = True
        else:
            _copy_verified(source, target)
            copied = True
        replace_manifest(candidate, expected=instance.document)
    except BaseException:
        if renamed:
            os.rename(target, source)
        elif copied:
            shutil.rmtree(target, ignore_errors=True)
        raise
    return {"area": area, "source": str(source), "target": str(target),
            "old_copy_retained": copied}
