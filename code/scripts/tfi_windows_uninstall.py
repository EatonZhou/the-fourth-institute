"""Optional instance removal for the Windows platform uninstaller."""
from __future__ import annotations

from pathlib import Path
import shutil
import stat
import subprocess


def plain_tree(root: Path) -> None:
    """Reject redirections before any recursive deletion, including junctions."""
    pending = [root]
    while pending:
        path = pending.pop()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Removal tree contains a link or reparse point: {path}")
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())


def validate_platform_caches(platform: Path) -> None:
    """Validate caches now; NSIS removes them after this Python process exits."""
    for name in (".build", "dependencies"):
        root = platform / name
        try:
            info = root.lstat()
        except FileNotFoundError:
            continue
        plain_tree(root)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Expected a platform cache directory: {root}")


def removal_plan(platform: Path) -> list[tuple[Path, bool]]:
    """Validate all selected roots before stopping or removing the first one."""
    from scripts.tfi_release_install import owned_state

    platform = platform.resolve(strict=True)
    base = platform / "instance"
    if not base.exists() and not base.is_symlink():
        return []
    plain_tree(base)
    result = []
    for root in sorted(base.iterdir()):
        if not root.is_dir():
            raise ValueError(f"Unrecognized instance entry: {root}")
        state_file = root / "installation.json"
        if state_file.is_file():
            state = owned_state(root, allow_missing_native=True)
            target = state["target"]
            if Path(target["install_root"]).resolve() != root:
                raise ValueError(f"Installation belongs to another directory: {root}")
            if Path(target.get("shared_code_root", "")).resolve() != platform / "code":
                raise ValueError(f"Instance belongs to another platform: {root}")
            for value in target["data_roots"].values():
                if not Path(value).resolve().is_relative_to(root):
                    raise ValueError(f"Instance uses external data; automatic removal refused: {root}")
            result.append((root, True))
        elif root.name == "example_instance" and all(p.name == "seed" for p in root.iterdir()):
            # Seed extraction can finish before the installer creates its state.
            result.append((root, False))
        else:
            raise ValueError(f"Instance has no ownership record; automatic removal refused: {root}")
    return result


def remove_instances(platform: Path) -> None:
    from scripts.tfi_windows_launcher import command

    plan = removal_plan(platform)
    # Both trees are in use here (Python runtime and launcher lock). Validate
    # them before deleting any instance; the native uninstaller owns deletion.
    validate_platform_caches(platform)
    # Stop every initialized instance first. A failed stop preserves all data.
    for root, initialized in plan:
        if initialized:
            subprocess.run(command(platform, "stop", "--root", root), check=True)
    # Lifecycle cleanup owns service registrations and provider cleanup.
    # It rechecks ownership and releases the Windows installation lock itself.
    for root, initialized in plan:
        plain_tree(root)
        if initialized:
            subprocess.run(command(platform, "cleanup", "--root", root), check=True)
        if root.exists():
            # Shared-platform cleanup preserves the seed; explicit uninstall
            # consent includes that seed and its containing instance directory.
            if any(p.name != "seed" for p in root.iterdir()):
                raise ValueError(f"Unexpected files remain after instance cleanup: {root}")
            plain_tree(root)
            shutil.rmtree(root)
    base = platform / "instance"
    if base.exists():
        base.rmdir()
