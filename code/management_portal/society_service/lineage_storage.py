from __future__ import annotations
from management_common.portal_paths import path as portal_path

import os
import re
import secrets
from management_common.file_lock import lock_stream
from management_common.directory_links import (
    create_directory_link, is_directory_link, remove_directory_link, replace_directory_link,
)
from contextlib import contextmanager
from pathlib import Path


LINEAGE_ID_RE = re.compile(r"^lineage_[0-9a-f]{12}$")
DEFAULT_LINEAGE_FOLDER_ROOT = (portal_path("share_root") / "Agents")


class LineageStorageConflictError(RuntimeError):
    """Raised when one Lineage has two competing canonical folders."""


def configured_lineage_folder_root() -> Path:
    return Path(
        os.getenv(
            "PORTAL_LINEAGE_FOLDER_ROOT",
            str(DEFAULT_LINEAGE_FOLDER_ROOT),
        )
    ).expanduser()


def _lineage_id(value: object) -> str:
    lineage_id = str(value or "").strip().lower()
    if not LINEAGE_ID_RE.fullmatch(lineage_id):
        raise ValueError("invalid Lineage id")
    return lineage_id


def canonical_lineage_folder(
    lineage_id: object,
    *,
    folder_root: str | Path | None = None,
) -> Path:
    root = Path(
        folder_root
        if folder_root is not None
        else configured_lineage_folder_root()
    ).expanduser()
    return root / _lineage_id(lineage_id)


@contextmanager
def _folder_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lineage-folders.lock").open("a+b") as handle, lock_stream(handle):
        yield


def bind_lineage_folder(
    lineage_id: object,
    target: str | Path,
    *,
    folder_root: str | Path | None = None,
) -> Path:
    """Create the deterministic Lineage entry for an existing folder."""

    canonical = canonical_lineage_folder(lineage_id, folder_root=folder_root)
    target_path = Path(target).expanduser().resolve(strict=False)
    canonical_path = canonical.resolve(strict=False)
    if canonical_path == target_path:
        canonical.mkdir(parents=True, exist_ok=True)
        return canonical
    target_path.mkdir(parents=True, exist_ok=True)
    with _folder_lock(canonical.parent):
        if canonical.exists() or is_directory_link(canonical):
            if canonical.resolve(strict=False) != target_path:
                raise LineageStorageConflictError(
                    f"Lineage canonical entry points elsewhere: {canonical}"
                )
            return canonical
        temporary = canonical.with_name(
            f".{canonical.name}.{secrets.token_hex(4)}.tmp"
        )
        try:
            create_directory_link(temporary, target_path)
            replace_directory_link(temporary, canonical)
        finally:
            remove_directory_link(temporary, missing_ok=True)
    return canonical


def relocate_lineage_folder(
    lineage_id: object,
    target: str | Path,
    *,
    folder_root: str | Path | None = None,
) -> Path:
    """Move a Lineage folder and preserve its deterministic entry path."""

    canonical = canonical_lineage_folder(lineage_id, folder_root=folder_root)
    canonical_path = Path(os.path.abspath(canonical.expanduser()))
    target_path = Path(os.path.abspath(Path(target).expanduser()))
    with _folder_lock(canonical.parent):
        if not canonical.exists() and not is_directory_link(canonical):
            raise FileNotFoundError(f"Lineage folder not found: {canonical}")
        source_path = canonical.resolve(strict=True)
        if target_path == canonical_path:
            if source_path == canonical_path:
                return canonical
            if not is_directory_link(canonical):
                raise LineageStorageConflictError(
                    f"Lineage canonical entry is not a symlink: {canonical}"
                )
            if canonical_path.parent.stat().st_dev != source_path.stat().st_dev:
                raise LineageStorageConflictError(
                    "Lineage relocation must stay on one filesystem"
                )
            remove_directory_link(canonical)
            try:
                os.replace(source_path, canonical_path)
            except BaseException:
                create_directory_link(canonical, source_path)
                raise
            return canonical

        resolved_target = target_path.resolve(strict=False)
        if source_path == resolved_target:
            return canonical
        if source_path in resolved_target.parents or resolved_target in source_path.parents:
            raise LineageStorageConflictError(
                "Lineage relocation target cannot contain or be contained by the source"
            )
        if target_path.exists() or is_directory_link(target_path):
            raise LineageStorageConflictError(
                f"Lineage target already exists: {target_path}"
            )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.parent.stat().st_dev != source_path.stat().st_dev:
            raise LineageStorageConflictError(
                "Lineage relocation must stay on one filesystem"
            )

        temporary = canonical.with_name(
            f".{canonical.name}.{secrets.token_hex(4)}.tmp"
        )
        moved = False
        try:
            os.replace(source_path, target_path)
            moved = True
            create_directory_link(temporary, target_path)
            replace_directory_link(temporary, canonical)
        except BaseException:
            remove_directory_link(temporary, missing_ok=True)
            if moved and target_path.exists() and not source_path.exists():
                os.replace(target_path, source_path)
            raise
    return canonical


def unbind_lineage_folder(
    lineage_id: object,
    *,
    folder_root: str | Path | None = None,
    expected_target: str | Path | None = None,
) -> None:
    canonical = canonical_lineage_folder(lineage_id, folder_root=folder_root)
    with _folder_lock(canonical.parent):
        if not is_directory_link(canonical):
            return
        if expected_target is not None and canonical.resolve(strict=False) != Path(
            expected_target
        ).expanduser().resolve(strict=False):
            raise LineageStorageConflictError(
                f"Lineage canonical entry points elsewhere: {canonical}"
            )
        remove_directory_link(canonical)


