"""Host paths and lifecycle of registered execution accounts.

Credentials stay in Provider-owned directories or private, atomically replaced
secret files. These helpers never inspect or return credential contents.
"""
from __future__ import annotations

from pathlib import Path
import re
import shutil

from management_common.instance_paths import accounts_root, instance_root, secrets_dir
from management_common.file_permissions import atomic_write_private_bytes, ensure_private_directory


def validate_account_id(account_id: str) -> str:
    if not isinstance(account_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", account_id):
        raise ValueError("Invalid account or secret identifier")
    return account_id


def account_home(provider: str, account_id: str) -> Path:
    if provider not in {"codex", "claude", "kimi", "antigravity"}:
        raise ValueError("Provider has no subscription account directory")
    return accounts_root() / provider / validate_account_id(account_id)


def codex_socket_path(account_id: str) -> Path:
    return instance_root() / "sockets" / f"codex-{validate_account_id(account_id)}.sock"


def _check_home(path: Path) -> None:
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Account directories must not be symbolic links")


def ensure_account_home(provider: str, account_id: str) -> Path:
    path = account_home(provider, account_id)
    _check_home(path)
    ensure_private_directory(path)
    return path


def remove_account_home(provider: str, account_id: str) -> None:
    path = account_home(provider, account_id)
    _check_home(path)
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
