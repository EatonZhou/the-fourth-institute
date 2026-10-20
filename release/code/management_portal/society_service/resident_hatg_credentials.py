from __future__ import annotations

import os
from pathlib import Path
from management_common.portal_paths import path as portal_path

from management_portal import agent_token_auth
from management_portal.shared_state import atomic_replace_text
from .identity_contract import is_valid_id


# The bearer token only authenticates the Resident for the command-line client
# of the HATG ISA; what it may do is decided by the permission system.
MANAGED_BY = "portal_resident_hatg"
TOKEN_ROOT = Path(
    os.getenv(
        "PORTAL_RESIDENT_HATG_TOKEN_ROOT",
        str(portal_path("secrets_dir") / "resident_hatg"),
    )
)
def credential_path(resident_id: str) -> Path:
    normalized = str(resident_id or "").strip().lower()
    if not is_valid_id(normalized, "resident"):
        raise ValueError("invalid Resident id for HATG credential")
    return TOKEN_ROOT / f"{normalized}.token"


def _read_token(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_token(path: Path, token: str) -> None:
    atomic_replace_text(path, token + "\n", mode=0o600)


def ensure_resident_credential(
    resident_id: str,
    lineage_id: str,
) -> Path | None:
    """Provision the bearer the command-line client authenticates with.

    Existing records are authoritative, including administrator revocations
    and rotations. Automatic provisioning only creates missing records.
    """

    normalized_resident = str(resident_id or "").strip().lower()
    normalized_lineage = str(lineage_id or "").strip().lower()
    if not is_valid_id(normalized_resident, "resident"):
        return None
    if not is_valid_id(normalized_lineage, "lineage"):
        return None
    token_path = credential_path(normalized_resident)
    store = agent_token_auth.get_agent_token_store()
    with store.transaction(normalized_resident):
        existing = store.get(normalized_resident) or {}
        token = _read_token(token_path)
        stored_hash = str(existing.get("token_hash") or "")
        if existing:
            if existing.get("enabled") is True and token and stored_hash and agent_token_auth.verify_token_hash(
                stored_hash,
                token,
            ):
                return token_path
            return None

        token = agent_token_auth.generate_token()
        store.put(normalized_resident, {
            "token_hash": agent_token_auth.hash_token(token),
            "token_lookup": agent_token_auth.token_lookup(token),
            "enabled": True,
            "managed_by": MANAGED_BY,
        })
        _write_token(token_path, token)
    return token_path


def revoke_resident_credential(resident_id: str) -> bool:
    """Stop a deleted Resident's bearer from authenticating (decision_fb910c43ab07).

    The token record stays for audit with ``enabled`` false, and the token file
    Portal issued is removed. Returns whether the record was still enabled.
    """

    normalized = str(resident_id or "").strip().lower()
    if not is_valid_id(normalized, "resident"):
        return False
    store = agent_token_auth.get_agent_token_store()
    changed = False
    with store.transaction(normalized):
        existing = store.get(normalized)
        if existing is not None and existing.get("enabled") is not False:
            store.put(normalized, {**existing, "enabled": False})
            changed = True
    try:
        credential_path(normalized).unlink()
    except FileNotFoundError:
        pass
    return changed
