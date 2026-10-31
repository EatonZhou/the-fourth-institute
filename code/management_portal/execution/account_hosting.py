"""Host paths and lifecycle of registered execution accounts.

Credentials stay in Provider-owned directories or private, atomically replaced
secret files. These helpers never inspect or return credential contents.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import json

from management_common.instance_paths import instance_root, secrets_dir
from management_common.file_permissions import atomic_write_private_bytes, ensure_private_directory
from management_common.provider_account_paths import (
    account_runtime, canonical_directory, default_directory, validate_identity,
)


def validate_account_id(account_id: str) -> str:
    return validate_identity(account_id)


def account_home(provider: str, account_id: str) -> Path:
    from .account_registrations import registration
    row = registration(validate_account_id(account_id))
    if row is None or row["provider"] != provider:
        raise ValueError("Account is not registered for this Provider")
    return registered_home(row)


def registered_home(row: dict) -> Path:
    if row["kind"] == "subscription_login":
        return canonical_directory(row["config"].get("account_dir", ""))
    if row["kind"] == "api_profile" and row["provider"] in {"codex", "kimi"}:
        return account_runtime(row["provider"], row["account_id"]) / "home"
    raise ValueError("Account has no Provider directory")


def prepare_registration_directory(data: dict) -> Path | None:
    """Resolve a default only at creation; existing directories are registered in place."""
    if data["kind"] != "subscription_login":
        return
    config = dict(data.get("config", {}))
    created = None
    if "account_dir" in config:
        path = canonical_directory(config["account_dir"])
        if not path.is_dir():
            raise ValueError("Existing account directory does not exist")
    else:
        path = canonical_directory(default_directory(data["provider"], data["account_id"]))
        ensure_private_directory(path.parent)
        # An existing host directory must be selected explicitly, never adopted
        # because an unrelated instance chose the same registration ID.
        path.mkdir(mode=0o700)
        created = path
        ensure_private_directory(path)
    config["account_dir"] = str(path)
    data["config"] = config
    return created


def codex_launch_path(account_id: str) -> Path:
    return account_runtime("codex", account_id) / "launch.json"


def write_codex_launch(account_id: str) -> Path:
    path = codex_launch_path(account_id)
    atomic_write_private_bytes(path, json.dumps({"account_id": validate_account_id(account_id),
                                               "account_dir": str(account_home("codex", account_id))}).encode())
    return path


def codex_socket_path(account_id: str) -> Path:
    return instance_root() / "sockets" / f"codex-{validate_account_id(account_id)}.sock"


def ensure_account_home(provider: str, account_id: str) -> Path:
    from .account_registrations import registration
    row = registration(account_id)
    if row is None or row["provider"] != provider:
        raise ValueError("Account is not registered for this Provider")
    path = registered_home(row)
    canonical_directory(path)
    if row["kind"] == "subscription_login" and not path.is_dir():
        raise FileNotFoundError("Registered account directory is missing: " + str(path))
    ensure_private_directory(path)
    return path


def remove_account_runtime(provider: str, account_id: str) -> None:
    path = account_runtime(provider, account_id)
    if path.exists():
        shutil.rmtree(path)


def write_secret_file(id: str, secret: str) -> Path:
    path = secrets_dir() / f"{validate_account_id(id)}.key"
    if not isinstance(secret, str) or not secret.strip() or "\0" in secret:
        raise ValueError("Secret must be non-empty text")
    atomic_write_private_bytes(path, secret.encode("utf-8"))
    return path


# The service control plane owns unit installation and systemctl operations.
def install_codex_unit_template() -> Path:
    from scripts.portal_service_units import install_codex_unit_template as install
    return install()


def start_codex_instance(account_id: str) -> dict:
    from scripts.portal_service_units import start_codex_instance as start
    return start(account_id)


def stop_codex_instance(account_id: str) -> dict:
    from scripts.portal_service_units import stop_codex_instance as stop
    return stop(account_id)


def remove_codex_instance(account_id: str) -> None:
    from scripts.portal_service_units import remove_codex_instance as remove
    remove(account_id)


def codex_instance_state(account_id: str) -> dict:
    from scripts.portal_service_units import codex_instance_state as state
    return state(account_id)
