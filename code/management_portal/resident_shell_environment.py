"""Service-owned environment supplied to an authorized Resident's local shell."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from management_common import portal_network
from management_common.portal_paths import resolve_directory
from management_common.site_config import read_site_config
from management_common.product_environment import mirror_product_env, read_product_env

# What a Resident's local agent process takes from the Worker's environment.
PROCESS_ENVIRONMENT_KEYS = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
    "https_proxy", "http_proxy", "all_proxy", "no_proxy",
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


def portal_database_url() -> str:
    url = str(os.getenv("PORTAL_DATABASE_URL") or "").strip()
    if not url:
        if "PORTAL_POSTGRES_ENV_FILE" in os.environ:
            env_file = resolve_directory(os.environ["PORTAL_POSTGRES_ENV_FILE"])
        else:
            deployment = read_site_config().get("deployment", {})
            if not isinstance(deployment, dict):
                raise ValueError("deployment must be an object")
            settings = deployment.get("native_shell", {})
            if not isinstance(settings, dict):
                raise ValueError("deployment.native_shell must be an object")
            if "database_env_file" in settings:
                env_file = resolve_directory(settings["database_env_file"])
            else:
                env_file = Path.home() / ".config" / "tfi" / "portal-postgres.env"
        try:
            lines = env_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise RuntimeError("Portal PostgreSQL environment is unavailable") from exc
        for raw in lines:
            line = raw.strip()
            if line.startswith("#") or not line:
                continue
            key, separator, value = line.partition("=")
            if separator and key.strip() == "PORTAL_DATABASE_URL":
                url = value.strip().strip('"\'')
                break
    if not url:
        raise RuntimeError("PORTAL_DATABASE_URL is required")
    return url


def portal_database_environment(enabled: bool) -> dict[str, str]:
    return {"PORTAL_DATABASE_URL": portal_database_url()} if enabled else {}


def resident_identity_environment(value: Mapping[str, Any] | None) -> dict[str, str]:
    """Validate the canonical Resident and Lineage environment fields."""
    identity = {str(key): str(item) for key, item in dict(value or {}).items()}
    fields = ("PORTAL_RESIDENT_ID", "PORTAL_LINEAGE_ID")
    allowed = set(fields)
    if set(identity) - allowed:
        raise ValueError("Unexpected Resident environment field")
    mirrored: dict[str, str] = {}
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


def resident_identity_fingerprint(value: Mapping[str, Any] | None) -> dict[str, str]:
    """Use the same canonical identity for processes and session fingerprints."""
    return resident_identity_environment(value)


def resident_process_environment(params: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """The environment a Resident's local agent process starts with, and the Resident identity in it.

    ``params`` are the native operation's: ``resident_environment`` names the
    Resident (both IDs or neither), ``inherit_portal_database`` adds the
    Portal database for an authorized Resident (``may_inherit_portal_database``).
    The environment holds secrets: pass it to the process, never to a file.
    """

    environment = {key: value for key, value in os.environ.items() if key in PROCESS_ENVIRONMENT_KEYS}
    managed_proxy = portal_network.saved_proxy_environment()
    if managed_proxy is not None:
        environment.update(managed_proxy)
    original_identity = dict(params.get("resident_environment") or {})
    mirrored_identity = resident_identity_environment(original_identity)
    environment.update(mirrored_identity)
    if params.get("inherit_portal_database"):
        if not mirrored_identity.get("PORTAL_RESIDENT_ID"):
            raise ValueError("Portal database environment requires Resident identity")
        environment.update(portal_database_environment(True))
    # Session fingerprints and native processes use the same canonical fields.
    identity = resident_identity_fingerprint(original_identity)
    return environment, identity
