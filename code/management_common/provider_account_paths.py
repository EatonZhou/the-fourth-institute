"""Host-owned subscription directories and instance-owned Provider control state."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re

from .file_permissions import ensure_private_directory

def canonical_directory(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value) or any(c in str(value) for c in "\0\r\n"):
        raise ValueError("Account directory must be a canonical absolute path")
    path = Path(value)
    if not path.is_absolute() or path != path.resolve() or str(path) != str(value):
        raise ValueError("Account directory must be canonical and must not contain symbolic links")
    if path.exists() and not path.is_dir():
        raise ValueError("Account directory is not a directory")
    return path


def validate_identity(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("Invalid account or secret identifier")
    return value


def runtime_root() -> Path:
    from . import portal_paths
    return portal_paths.path('runtime') / 'provider-accounts'


def default_account_directory_prefix(provider: str) -> str:
    """Host paths for new subscriptions, projected from this instance's storage."""
    from .instance_manifest import current_instance
    # Credentials outlive registration and instance retirement; keep defaults
    # outside disposable instance areas, with an explicit per-instance scope.
    root = Path.home() / '.tfi-provider-accounts' / validate_identity(current_instance().instance_id) / validate_identity(provider)
    return str(root.resolve()) + os.sep


def default_account_directory(provider: str, account_id: str) -> str:
    return default_account_directory_prefix(provider) + validate_identity(account_id)


def account_runtime(provider: str, account_id: str) -> Path:
    return runtime_root() / validate_identity(provider) / validate_identity(account_id)


def registered_home(row: dict) -> Path:
    if row["kind"] == "subscription_login":
        return canonical_directory(row["config"].get("account_dir", ""))
    if row["kind"] == "api_profile" and row["provider"] in {"codex", "kimi"}:
        return account_runtime(row["provider"], row["account_id"]) / "home"
    raise ValueError("Account has no Provider directory")


def prepare_registration_directory(data: dict) -> Path | None:
    """Create or reuse the resolved host directory for the new registration."""
    if data["kind"] != "subscription_login":
        return
    config = dict(data.get("config", {}))
    path = canonical_directory(config.get("account_dir", ""))
    created = None
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            canonical_directory(path)
        else:
            created = path
    # Validate existing directories before committing their registration too.
    # Hosting performs the same check; discovering an unsafe ACL only there
    # would report not_committed after the database row had already been saved.
    ensure_private_directory(path)
    config["account_dir"] = str(path)
    data["config"] = config
    return created


def directory_runtime(directory: Path) -> Path:
    """Control state for a directory when no registration ID is in scope."""
    return runtime_root() / "directories" / hashlib.sha256(os.fsencode(canonical_directory(directory))).hexdigest()
