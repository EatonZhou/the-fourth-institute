"""Bearer-token authentication for Portal agent APIs."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from contextlib import contextmanager
from typing import Any

from management_common.portal_database import portal_database
from management_common.database_time import instant, epoch_seconds


_STORE = None
_STORES_LOCK = threading.Lock()
PBKDF2_ROUNDS = 260_000
HASH_ALG = "sha256"
HASH_PREFIX = "agent-token"
LOOKUP_PREFIX = "sha256"

TOKEN_FIELDS = {
    "token_lookup": "TEXT NOT NULL", "token_hash": "TEXT",
    "enabled": "BOOLEAN", "managed_by": "TEXT", "caller_id": "TEXT",
    "required_scope": "TEXT", "expires_at": "TIMESTAMPTZ", "lease_path": "TEXT",
}
TOKEN_DDL = "CREATE TABLE IF NOT EXISTS agent_tokens (agent_id TEXT PRIMARY KEY," + ",".join(
    name + " " + kind for name, kind in TOKEN_FIELDS.items()
) + "); CREATE INDEX IF NOT EXISTS agent_tokens_lookup ON agent_tokens(token_lookup);"


def token_record(row):
    return {name: epoch_seconds(row[name]) if name == "expires_at" else row[name]
            for name in TOKEN_FIELDS if row[name] is not None}


def hash_token(token: str, salt: bytes | None = None) -> str:
    """Return a non-reversible token hash for the authentication registry."""
    if salt is None:
        salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac(HASH_ALG, token.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"{HASH_PREFIX}:{HASH_ALG}:{salt.hex()}:{key.hex()}"


def verify_token_hash(stored: str, token: str) -> bool:
    try:
        prefix, alg, salt_hex, key_hex = stored.split(":")
        if prefix != HASH_PREFIX:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
        actual = hashlib.pbkdf2_hmac(alg, token.encode("utf-8"), salt, PBKDF2_ROUNDS)
        return hmac.compare_digest(actual, expected)
    except (AttributeError, TypeError, ValueError):
        # Not a stored hash of this format (no string, wrong field count, bad hex,
        # unknown algorithm), or a token that is not text.
        return False


def token_lookup(token: str) -> str:
    """Return a fast lookup key for a high-entropy bearer token."""

    digest = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    return f"{LOOKUP_PREFIX}:{digest}"


def generate_token() -> str:
    return secrets.token_urlsafe(32)


class AgentTokenStore:
    def __init__(self):
        self.database = portal_database(schema="portal_identity")
        with self.database.transaction() as connection:
            connection.executescript(TOKEN_DDL)

    @contextmanager
    def transaction(self, agent_id: str):
        with self.database.transaction() as connection:
            lock_key = int.from_bytes(
                hashlib.sha256(("portal-agent-token:" + agent_id).encode()).digest()[:8],
                "big", signed=True,
            )
            connection.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key,))
            yield self

    def get(self, agent_id: str) -> dict[str, Any] | None:
        with self.database.read_current() as connection:
            row = connection.execute(
                'SELECT * FROM agent_tokens WHERE agent_id = %s', (agent_id,)
            ).fetchone()
        return token_record(row) if row else None

    def put(self, agent_id: str, config: dict[str, Any]) -> None:
        if not isinstance(agent_id, str) or not agent_id.strip() or not isinstance(config, dict):
            raise ValueError("Agent id and authentication configuration are required")
        unknown = set(config) - TOKEN_FIELDS.keys()
        if unknown:
            raise ValueError("Unknown Agent authentication fields: " + ", ".join(sorted(unknown)))
        fields = tuple(TOKEN_FIELDS)
        values = {**config, "token_lookup": str(config.get("token_lookup") or "")}
        values["expires_at"] = instant(values.get("expires_at"))
        with self.transaction(agent_id):
            with self.database.read_current() as connection:
                connection.execute(
                    'INSERT INTO agent_tokens (agent_id,' + ','.join(fields) + ') VALUES ('
                    + ','.join(['%s'] * (len(fields) + 1)) + ') ON CONFLICT(agent_id) DO UPDATE SET '
                    + ','.join(name + '=excluded.' + name for name in fields),
                    (agent_id, *(values.get(name) for name in fields)),
                )

    def list(self) -> dict[str, dict[str, Any]]:
        with self.database.read_current() as connection:
            rows = connection.execute("SELECT * FROM agent_tokens ORDER BY agent_id").fetchall()
        return {row['agent_id']: token_record(row) for row in rows}

    def candidates(self, lookup: str):
        with self.database.read_current() as connection:
            rows = connection.execute(
                'SELECT * FROM agent_tokens WHERE token_lookup = %s ORDER BY agent_id',
                (lookup,),
            ).fetchall()
        return [(row['agent_id'], token_record(row)) for row in rows]


def get_agent_token_store() -> AgentTokenStore:
    global _STORE
    with _STORES_LOCK:
        if _STORE is None:
            _STORE = AgentTokenStore()
        return _STORE


def authenticate_bearer_token(token: str, *, scope: str = "") -> dict[str, Any] | None:
    token = token.strip()
    if not token:
        return None
    lookup = token_lookup(token)
    for agent_id, cfg in get_agent_token_store().candidates(lookup):
        if cfg.get("enabled") is False:
            continue
        if cfg.get("required_scope") and cfg["required_scope"] != scope:
            continue
        if cfg.get("managed_by") == "portal_script_run":
            import time
            from pathlib import Path
            expires_at = cfg.get("expires_at")
            if (expires_at is not None and float(expires_at) <= time.time()) or not Path(cfg.get("lease_path") or "/nonexistent").is_file():
                continue
        stored = str(cfg.get("token_hash") or "")
        if stored and verify_token_hash(stored, token):
            return {"agent_id": str(cfg.get("caller_id") or agent_id) if cfg.get("managed_by") == "portal_script_run" else agent_id}
    return None
