"""Optional instance removal for the Windows platform uninstaller.

Instances running this platform's code are retired by the shared installer:
services stopped, definitions deleted, registration removed, instance root
deleted. Areas the user placed outside an instance root are kept and reported.
"""
from __future__ import annotations

import ntpath
import os
from pathlib import Path
import shutil
import stat
import subprocess


def _native_path(path: Path) -> str:
    """Use extended Windows paths without depending on long-path policy."""
    if os.name != "nt":
        return os.fspath(path)
    value = ntpath.abspath(path)
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def plain_tree(root: Path) -> None:
    """Reject redirections before any recursive deletion, including junctions."""
    pending = [Path(_native_path(root))]
    while pending:
        path = pending.pop()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Removal tree contains a link or reparse point: {path}")
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())


def validate_platform_caches(platform: Path) -> None:
    """Validate all caches before deleting instance data or prepared bundles."""
    for name in (".build", "dependencies"):
        root = platform / name
        try:
            info = root.lstat()
        except FileNotFoundError:
            continue
        plain_tree(root)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Expected a platform cache directory: {root}")


def _remove_prepared_cache(platform: Path) -> None:
    """Remove validated deep build trees; keep the live launcher lock and logs."""
    build = Path(_native_path(platform / ".build"))
    if not build.exists():
        return
    for entry in build.iterdir():
        if entry.name == "windows":
            continue
        plain_tree(entry)
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def removal_plan(platform: Path):
    """Validate every selected instance before stopping or removing the first one."""
    from scripts.tfi_windows_launcher import platform_instances
    plan = platform_instances(platform.resolve(strict=True))
    for instance in plan:
        plain_tree(instance.root)
    return plan


def remove_instances(platform: Path) -> list[str]:
    from scripts.tfi_windows_launcher import command

    plan = removal_plan(platform)
    # Runtime and launcher lock are in use. Validate both trees before deletion;
    # leave only their cleanup to NSIS after this helper exits.
    validate_platform_caches(platform)
    # Stop every instance first. A failed stop preserves all data.
    for instance in plan:
        subprocess.run(command(platform, "stop", "--instance", instance.name), check=True)
    # Prepared bundles can exceed MAX_PATH. Remove them through the same Python
    # filesystem APIs that prepared them; failures preserve the installed tools
    # and instance data for diagnosis/retry instead of stranding a deep tree.
    _remove_prepared_cache(platform)
    kept = []
    for instance in plan:
        kept += [str(instance.area(area)) for area in instance.document.get("areas", {})
                 if not instance.area(area).is_relative_to(instance.root)]
        subprocess.run(command(platform, "retire", "--instance", instance.name), check=True)
    return kept
