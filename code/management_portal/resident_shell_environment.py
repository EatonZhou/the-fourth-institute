"""Service-owned environment supplied to an authorized Resident's local shell."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from management_common.product_environment import mirror_product_env, read_product_env

# What a Resident's local agent process takes from the Worker's environment.
PROCESS_ENVIRONMENT_KEYS = frozenset({
    "PORTAL_INSTANCE_ROOT", "PATH", "HOME", "LANG", "LC_ALL", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    # Windows native bridges need the same user profile as the parent for
    # authenticated local IPC. These are directories, not provider credentials.
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
})


def may_inherit_portal_database(request: Any) -> bool:
    metadata = getattr(request, "metadata", {}) or {}
    scope = metadata.get("filesystem_scope") or {}
    return (
        metadata.get("business_type") == "resident"
        and metadata.get("resident_id") == getattr(request, "agent_id", None)
        and getattr(request, "permission_profile", None) == "owner"
        and scope.get("mode") == "A"
    )


from management_common.portal_database import portal_database_url


def portal_database_environment(enabled: bool) -> dict[str, str]:
    return {"PORTAL_DATABASE_URL": portal_database_url()} if enabled else {}


RESIDENT_IDENTITY_FIELDS = ("PORTAL_RESIDENT_ID", "PORTAL_LINEAGE_ID")
# Fixed wire contract understood by older native adapters.
LEGACY_WORKSPACE_FIELD = re.compile(r"PORTAL_(?:WORKSPACE_[0-9A-F]{12}|(?:RESIDENT|LINEAGE|PROJECT)_WORKSPACE)")
SESSION_WORKSPACE_FIELD = re.compile(r"PORTAL_(?:WORKSPACE_[0-9A-F]{12}|[A-Z][A-Z0-9_]*_WORKSPACE)")


def _resident_environment(identity: Mapping[str, str], *, extensible: bool) -> dict[str, str]:
    mirrored: dict[str, str] = {}
    workspace_field = SESSION_WORKSPACE_FIELD if extensible else LEGACY_WORKSPACE_FIELD
    for key, item in identity.items():
        if not isinstance(key, str) or not isinstance(item, str) or "\x00" in item:
            raise ValueError("Invalid Resident environment entry")
        if key in RESIDENT_IDENTITY_FIELDS:
            continue
        if workspace_field.fullmatch(key):
            if not Path(item).is_absolute() or not identity.get("PORTAL_RESIDENT_ID"):
                raise ValueError("Workspace environment requires an absolute path and Resident identity")
        elif extensible and re.fullmatch(r"PORTAL_CONTEXT_[A-Z][A-Z0-9_]*", key):
            if not identity.get("PORTAL_RESIDENT_ID"):
                raise ValueError("Session context requires Resident identity")
        else:
            raise ValueError("Unexpected Resident environment field")
        mirrored[key] = item
    for key, prefix in (
        ("PORTAL_RESIDENT_ID", "resident"),
        ("PORTAL_LINEAGE_ID", "lineage"),
    ):
        resolved = read_product_env(key, "", environ=identity)
        if resolved and not re.fullmatch(prefix + r"_[0-9a-f]{12}", resolved):
            raise ValueError("Invalid canonical Resident CLI identity")
        if key in identity:
            mirror_product_env(mirrored, key, resolved)
    if bool(mirrored.get("PORTAL_RESIDENT_ID")) != bool(mirrored.get("PORTAL_LINEAGE_ID")):
        raise ValueError("Resident CLI identity must include both IDs")
    return mirrored


def resident_identity_environment(value: Mapping[str, Any] | None) -> dict[str, str]:
    """Validate the original native identity and workspace contract."""
    return _resident_environment({str(k): str(v) for k, v in dict(value or {}).items()}, extensible=False)


def resident_session_environment(value: Mapping[str, str] | None) -> dict[str, str]:
    """Portal owns session data under PORTAL_CONTEXT_* and absolute workspace aliases.

    This namespace is for application data only. Provider login, process settings
    and Portal credentials are supplied by their respective owners.
    """
    if value is not None and not isinstance(value, Mapping):
        raise ValueError("Resident session environment must be a mapping")
    return _resident_environment(value or {}, extensible=True)


def resident_identity_fingerprint(value: Mapping[str, Any] | None) -> dict[str, str]:
    """Use the same canonical identity for processes and session fingerprints."""
    environment = resident_session_environment(value)
    # Preserve fingerprints of existing native sessions. Extension fields have
    # no legacy representation, so their order can be canonical from launch.
    if any(key not in RESIDENT_IDENTITY_FIELDS and not LEGACY_WORKSPACE_FIELD.fullmatch(key)
           for key in environment):
        return dict(sorted(environment.items()))
    return environment


def resident_process_environment(params: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """The environment a Resident's local agent process starts with, and the Resident identity in it.

    ``params`` are the native operation's: ``resident_environment`` names the
    Resident (both IDs or neither), ``inherit_portal_database`` adds the
    Portal database for an authorized Resident (``may_inherit_portal_database``).
    The environment holds secrets: pass it to the process, never to a file.
    """

    environment = {key: value for key, value in os.environ.items() if key in PROCESS_ENVIRONMENT_KEYS}
    identity = resident_identity_fingerprint(params.get("resident_environment"))
    environment.update(identity)
    if params.get("inherit_portal_database"):
        if not identity.get("PORTAL_RESIDENT_ID"):
            raise ValueError("Portal database environment requires Resident identity")
        environment.update(portal_database_environment(True))
    return environment, identity
