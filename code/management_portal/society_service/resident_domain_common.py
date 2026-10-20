from __future__ import annotations
from management_common.portal_paths import path as portal_path

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from management_common.operation_errors import OperationError
from . import identity_contract


def _new_resident_id(existing_ids: set[str] | None = None) -> str:
    occupied = {str(value or "").strip() for value in existing_ids or set()}
    return identity_contract.new_id("resident", occupied)


def _new_lineage_id(existing_ids: set[str] | None = None) -> str:
    occupied = {str(value or "").strip() for value in existing_ids or set()}
    return identity_contract.new_id("lineage", occupied)


AGENTS_ROOT = (portal_path("share_root") / "Agents")
PROJECTS_ROOT = (portal_path("share_root") / "Projects")
STAGING_ROOT = portal_path("staging_root")
MAX_TIMELINE_ITEMS = 120


class ResidentSessionConflictError(OperationError):
    http_status = 409
    commit_state = "not_committed"

    def __init__(self, message: str, *, code: str, restart: Mapping[str, Any]) -> None:
        super().__init__(message, code=code, details={"restart": copy.deepcopy(dict(restart))})

    def conflict_details(self) -> dict[str, Any]:
        return dict(self.details)


class ResidentTurnCancelled(RuntimeError):
    """The canonical Resident lifecycle accepted cancellation of the turn."""

    def __init__(self, message: str, *, usage: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        # What the stopped turn's execution counted, when it had run.
        self.usage = dict(usage or {})


class ResidentExecutionFailed(RuntimeError):
    """Carry an Execution failure intact to the single completion owner."""

    def __init__(
        self, snapshot: Mapping[str, Any], fallback: str, *, usage: Mapping[str, Any] | None = None,
    ) -> None:
        result = snapshot.get("result")
        result = result if isinstance(result, dict) else {}
        super().__init__(str(result.get("error") or snapshot.get("error") or fallback))
        self.error_code = str(result.get("error_code") or snapshot.get("error_code") or "")
        details = result.get("error_details", snapshot.get("error_details"))
        self.error_details = dict(details) if isinstance(details, dict) else {}
        # What the failed turn's execution counted for its Provider requests.
        self.usage = dict(usage or {})

CODEX_COMPACT_TIMEOUT_SECONDS = int(os.getenv("RESIDENT_AGENT_CODEX_COMPACT_TIMEOUT_SECONDS", "1800"))
RESIDENT_RESOURCE_ACTIVITY_TOTAL_CHARS = 48_000
LINEAGE_FOLDER_SCHEMA_VERSION = "lineage-folder-v3"
RESIDENT_INSTRUCTIONS_FILE = "resident.md"
RESIDENT_DIARY_FILE = "diary.md"
RESIDENT_MAILBOX_FILE = "mailbox.md"
RESIDENT_WORKSPACES_FILE = "workspaces.md"
RESIDENT_WORKSPACE_DIR = "workspace"
RESIDENT_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif"}
RESIDENT_MEDIA_KINDS = {"image", "video", "pdf"}


@dataclass(frozen=True)
class ResidentRuntimeTarget:
    """Resolved runtime state for one exact Resident identity."""

    resident_id: str
    record: dict[str, Any]
