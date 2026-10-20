"""Shared message authentication for Portal's local service sockets."""
from __future__ import annotations
from management_common.format_tags import schema_matches
from management_common.site_compatibility import compatibility_value

import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping

from .instance_paths import instance_root
from .file_permissions import create_private_file, ensure_private_directory, read_private_bytes

# Readers authenticate the received tag; new requests use the current namespace.
RPC_AUTH_SCHEMA = "tfi.resource-rpc-auth.v1"
RPC_AUTH_MAX_AGE_SECONDS = 30

def rpc_signing_key_file(
    instance: str = "",
    *,
    socket_path: Path | str | None = None,
    platform: str | None = None,
) -> Path:
    clean_instance = str(
        instance or os.getenv("PORTAL_INSTANCE", "dev") or "dev"
    ).strip()
    configured = str(
        os.getenv("PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE", "") or ""
    ).strip()
    if configured:
        return Path(configured).expanduser()
    if (platform or sys.platform) == "win32":
        logical_parent = Path(socket_path).parent if socket_path is not None else instance_root() / clean_instance
        namespace = hashlib.sha256(str(logical_parent.resolve()).encode("utf-8")).hexdigest()[:20]
        local_data = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        root = Path(compatibility_value('rpc_keys_root', str(local_data / "TFI" / "Portal" / "rpc-keys"))).expanduser()
        return root / namespace / "resource-rpc-signing.key"
    if socket_path is not None:
        return Path(socket_path).with_name("resource-rpc-signing.key")
    return Path(
        f'{instance_root()}/{clean_instance}/resource-rpc-signing.key'
    )


def load_rpc_signing_key(
    *,
    required: bool,
    create: bool = False,
    path: Path | None = None,
    platform: str | None = None,
) -> bytes | None:
    key_path = path or rpc_signing_key_file()
    try:
        key = read_private_bytes(key_path)
    except FileNotFoundError:
        if not create:
            if required:
                raise RuntimeError("Portal RPC signing key is unavailable")
            return None
        ensure_private_directory(key_path.parent)
        generated = secrets.token_bytes(32)
        try:
            with create_private_file(key_path) as stream:
                stream.write(generated)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            key = read_private_bytes(key_path)
        else:
            key = generated
    except OSError as exc:
        if required:
            raise RuntimeError(
                "Portal RPC signing key is unavailable"
            ) from exc
        return None
    try:
        permissions = key_path.stat().st_mode & 0o777
    except OSError as exc:
        raise RuntimeError(
            "Portal RPC signing key metadata is unavailable"
        ) from exc
    # Windows st_mode reflects the read-only attribute, not NTFS ACLs.  The
    # default Windows key lives under the current user's LocalAppData tree.
    if (platform or sys.platform) != "win32" and permissions & 0o077:
        raise RuntimeError(
            "Portal RPC signing key permissions are too broad"
        )
    if len(key) < 32:
        raise RuntimeError("Portal RPC signing key must be at least 32 bytes")
    return key


def _signature_payload(
    method: str,
    params: Mapping[str, Any],
    *,
    actor: str,
    issued_at: int,
    nonce: str,
    schema: str = RPC_AUTH_SCHEMA,
) -> bytes:
    params_bytes = json.dumps(
        dict(params),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload = {
        "schema": schema,
        "method": str(method or ""),
        "actor": str(actor or "").strip(),
        "issued_at": int(issued_at),
        "nonce": str(nonce or ""),
        "params_sha256": hashlib.sha256(params_bytes).hexdigest(),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_rpc_auth(
    key: bytes,
    method: str,
    params: Mapping[str, Any],
    *,
    actor: str,
    issued_at: int | None = None,
    nonce: str = "",
) -> dict[str, Any]:
    timestamp = int(time.time()) if issued_at is None else int(issued_at)
    request_nonce = str(nonce or secrets.token_hex(16))
    signature = hmac.new(
        key,
        _signature_payload(
            method,
            params,
            actor=actor,
            issued_at=timestamp,
            nonce=request_nonce,
        ),
        hashlib.sha256,
    ).hexdigest()
    return {
        "schema": RPC_AUTH_SCHEMA,
        "actor": str(actor or "").strip(),
        "issued_at": timestamp,
        "nonce": request_nonce,
        "signature": signature,
    }


def verify_rpc_auth(
    key: bytes,
    method: str,
    params: Mapping[str, Any],
    raw_auth: object,
    *,
    now: int | None = None,
) -> tuple[str, str, int]:
    if not isinstance(raw_auth, dict):
        raise PermissionError("authenticated RPC actor required")
    if not schema_matches(raw_auth.get("schema"), "tfi.resource-rpc-auth.v1"):
        raise PermissionError("invalid Portal RPC authentication")
    actor = str(raw_auth.get("actor") or "").strip()
    nonce = str(raw_auth.get("nonce") or "").strip()
    signature = str(raw_auth.get("signature") or "").strip()
    try:
        issued_at = int(raw_auth.get("issued_at"))
    except (TypeError, ValueError) as exc:
        raise PermissionError("invalid Portal RPC authentication") from exc
    current = int(time.time()) if now is None else int(now)
    if (
        not nonce
        or len(nonce) > 128
        or abs(current - issued_at) > RPC_AUTH_MAX_AGE_SECONDS
    ):
        raise PermissionError("invalid Portal RPC authentication")
    expected = hmac.new(
        key,
        _signature_payload(
            method,
            params,
            actor=actor,
            issued_at=issued_at,
            nonce=nonce,
            schema=raw_auth["schema"],
        ),
        hashlib.sha256,
    ).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise PermissionError("invalid Portal RPC authentication")
    return actor, nonce, issued_at



def request_auth(method: str, params: Mapping[str, Any], *, socket_path: Path, actor: str = "") -> dict[str, Any]:
    key = load_rpc_signing_key(required=True, path=rpc_signing_key_file(socket_path=socket_path))
    return sign_rpc_auth(key, method, params, actor=actor)


class RpcAuthenticator:
    """One receiving service owns signature and replay validation for its socket."""

    def __init__(self, socket_path: Path, *, key: bytes | None = None):
        self.key = key if key is not None else load_rpc_signing_key(required=True, create=True,
                                        path=rpc_signing_key_file(socket_path=socket_path))
        self._lock = threading.Lock()
        self._nonces: OrderedDict[str, int] = OrderedDict()

    def authenticate(self, method: str, params: Mapping[str, Any], auth: object) -> str:
        actor, nonce, issued_at = verify_rpc_auth(self.key, method, params, auth)
        with self._lock:
            current = int(time.time())
            oldest = current - RPC_AUTH_MAX_AGE_SECONDS
            while self._nonces:
                first = next(iter(self._nonces))
                if self._nonces[first] >= oldest:
                    break
                self._nonces.popitem(last=False)
            if nonce in self._nonces:
                raise PermissionError("replayed Portal RPC authentication")
            self._nonces[nonce] = current
        return actor
