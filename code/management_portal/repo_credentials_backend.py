from __future__ import annotations
from management_common.product_environment import read_product_env

import hashlib
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from management_common.format_tags import schema_matches
from management_common.portal_paths import path as portal_path
from management_common import file_permissions
from management_common.clock import utc_now_iso


DEFAULT_CREDENTIAL_ROOT = Path(
    read_product_env(
        "PORTAL_REPO_CREDENTIAL_ROOT",
        str(portal_path("secrets_dir") / "repo-git-credentials"),
    )
).expanduser()
DEFAULT_KEY_PATH = Path(
    read_product_env(
        "PORTAL_REPO_CREDENTIAL_KEY_PATH",
        str(portal_path("secrets_dir") / "repo-git-credentials.master-key"),
    )
).expanduser()

_LOCK = threading.RLock()
_SCHEMA = "tfi.repo-git-credential.v1"
_VALID_PROTOCOLS = {"https", "ssh"}
_VALID_AUTH_TYPES = {"token", "password_or_token", "ssh_secret"}


class RepoCredentialStoreError(RuntimeError):
    """The encrypted Repo credential store could not be read safely."""


def _clean_actor(value: object) -> str:
    actor = str(value or "").strip()
    if not actor:
        raise PermissionError("Portal 用户身份不可用，不能读取或保存 Git 凭据。")
    if len(actor) > 200 or any(char in actor for char in "\r\n\0"):
        raise ValueError("invalid Portal user identity")
    return actor


def _clean_context(context: dict[str, Any]) -> dict[str, str]:
    protocol = str(context.get("protocol") or "").strip().casefold()
    host = str(context.get("host") or "").strip().casefold().rstrip(".")
    username = str(context.get("username") or "").strip()
    auth_type = str(context.get("auth_type") or "").strip().casefold()
    if protocol not in _VALID_PROTOCOLS:
        raise ValueError("Git 凭据只允许用于 HTTPS 或 SSH remote。")
    if not host or len(host) > 253 or any(char in host for char in "/\\\r\n\0"):
        raise ValueError("invalid Git credential host")
    if len(username) > 200 or any(char in username for char in "\r\n\0"):
        raise ValueError("invalid Git credential username")
    if not auth_type:
        auth_type = "ssh_secret" if protocol == "ssh" else "password_or_token"
    if auth_type not in _VALID_AUTH_TYPES:
        raise ValueError("invalid Git credential type")
    return {
        "protocol": protocol,
        "host": host,
        "username": username,
        "auth_type": auth_type,
    }


def _actor_directory(actor: str, root: Path) -> Path:
    digest = hashlib.sha256(actor.encode("utf-8")).hexdigest()
    return root / digest


def _ensure_private_directory(path: Path) -> None:
    try:
        file_permissions.ensure_private_directory(path)
    except file_permissions.PrivateFileError as exc:
        raise RepoCredentialStoreError(str(exc)) from exc


def _read_private_bytes(path: Path) -> bytes:
    try:
        return file_permissions.read_private_bytes(path)
    except file_permissions.PrivateFileError as exc:
        raise RepoCredentialStoreError(str(exc)) from exc


def _atomic_write(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    if mode != 0o600:
        raise ValueError("Git credential files require mode 0600")
    try:
        file_permissions.atomic_write_private_bytes(path, payload)
    except file_permissions.PrivateFileError as exc:
        raise RepoCredentialStoreError(str(exc)) from exc


def _fernet(
    key_path: Path,
    *,
    create: bool,
) -> Fernet | None:
    try:
        key = _read_private_bytes(key_path).strip()
    except FileNotFoundError:
        if not create:
            return None
        _ensure_private_directory(key_path.parent)
        generated = Fernet.generate_key()
        try:
            with file_permissions.create_private_file(key_path) as handle:
                handle.write(generated)
                handle.flush()
                # Durability is unchanged; the helper owns permissions only.
                os.fsync(handle.fileno())
        except FileExistsError:
            key = _read_private_bytes(key_path).strip()
        except file_permissions.PrivateFileError as exc:
            raise RepoCredentialStoreError(str(exc)) from exc
        else:
            key = generated
    try:
        return Fernet(key)
    except (TypeError, ValueError) as exc:
        raise RepoCredentialStoreError("Git credential encryption key is invalid") from exc


def _record_path(actor: str, credential_id: str, root: Path) -> Path:
    if not credential_id.startswith("gitcred-") or not credential_id[8:].isalnum():
        raise ValueError("invalid Git credential id")
    return _actor_directory(actor, root) / f"{credential_id}.credential"


def _read_record(path: Path, cipher: Fernet) -> dict[str, Any]:
    try:
        decrypted = cipher.decrypt(_read_private_bytes(path))
        record = json.loads(decrypted.decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise RepoCredentialStoreError(
            f"Git credential record cannot be decrypted: {path.name}"
        ) from exc
    if not isinstance(record, dict) or not schema_matches(record.get("schema"), "tfi.repo-git-credential.v1"):
        raise RepoCredentialStoreError(
            f"Git credential record has an unsupported schema: {path.name}"
        )
    return record


def _write_record(path: Path, record: dict[str, Any], cipher: Fernet) -> None:
    encoded = json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    _atomic_write(path, cipher.encrypt(encoded))


def _records(
    actor: str,
    *,
    root: Path,
    key_path: Path,
) -> list[tuple[Path, dict[str, Any]]]:
    cipher = _fernet(key_path, create=False)
    actor_dir = _actor_directory(actor, root)
    if cipher is None or not actor_dir.is_dir():
        return []
    output: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(actor_dir.glob("gitcred-*.credential")):
        record = _read_record(path, cipher)
        if record.get("actor") != actor:
            raise RepoCredentialStoreError(
                f"Git credential actor mismatch: {path.name}"
            )
        output.append((path, record))
    return output


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "credential_id": str(record.get("credential_id") or ""),
        "protocol": str(record.get("protocol") or ""),
        "host": str(record.get("host") or ""),
        "username": str(record.get("username") or ""),
        "auth_type": str(record.get("auth_type") or ""),
        "status": str(record.get("status") or "invalid"),
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
        "last_used_at": str(record.get("last_used_at") or ""),
    }


def list_credentials(
    actor: str,
    *,
    protocol: str = "",
    host: str = "",
    root: str | Path | None = None,
    key_path: str | Path | None = None,
) -> dict[str, Any]:
    clean_actor = _clean_actor(actor)
    clean_protocol = str(protocol or "").strip().casefold()
    clean_host = str(host or "").strip().casefold().rstrip(".")
    store_root = Path(root or DEFAULT_CREDENTIAL_ROOT).expanduser()
    store_key = Path(key_path or DEFAULT_KEY_PATH).expanduser()
    with _LOCK:
        records = [
            _metadata(record)
            for _path, record in _records(
                clean_actor,
                root=store_root,
                key_path=store_key,
            )
            if (not clean_protocol or record.get("protocol") == clean_protocol)
            and (not clean_host or record.get("host") == clean_host)
        ]
    records.sort(
        key=lambda item: (
            item.get("status") != "active",
            str(item.get("host") or ""),
            str(item.get("username") or ""),
        )
    )
    return {
        "schema": "tfi.repo-git-credentials.v1",
        "credentials": records,
    }


def find_credential(
    actor: str,
    context: dict[str, Any],
    *,
    root: str | Path | None = None,
    key_path: str | Path | None = None,
) -> dict[str, Any] | None:
    clean_actor = _clean_actor(actor)
    clean_context = _clean_context(context)
    store_root = Path(root or DEFAULT_CREDENTIAL_ROOT).expanduser()
    store_key = Path(key_path or DEFAULT_KEY_PATH).expanduser()
    with _LOCK:
        matches = [
            record
            for _path, record in _records(
                clean_actor,
                root=store_root,
                key_path=store_key,
            )
            if record.get("status") == "active"
            and record.get("protocol") == clean_context["protocol"]
            and record.get("host") == clean_context["host"]
            and record.get("secret")
        ]
    requested_username = clean_context["username"]
    if requested_username:
        exact = [
            record
            for record in matches
            if record.get("username") == requested_username
        ]
        if len(exact) == 1:
            return dict(exact[0])
        if exact:
            exact.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            return dict(exact[0])
    if len(matches) == 1:
        return dict(matches[0])
    return None


def save_credential(
    actor: str,
    context: dict[str, Any],
    *,
    username: str,
    secret: str,
    root: str | Path | None = None,
    key_path: str | Path | None = None,
) -> dict[str, Any]:
    clean_actor = _clean_actor(actor)
    merged_context = {**context, "username": username}
    clean_context = _clean_context(merged_context)
    clean_secret = str(secret or "")
    if not clean_secret:
        raise ValueError("Git 凭据不能为空。")
    if len(clean_secret) > 16_384 or any(char in clean_secret for char in "\r\n\0"):
        raise ValueError("Git 凭据包含不支持的字符。")
    if clean_context["protocol"] == "https" and not clean_context["username"]:
        raise ValueError("HTTPS Git 凭据需要用户名。")
    if clean_context["protocol"] == "ssh" and not clean_context["username"]:
        clean_context["username"] = "git"

    store_root = Path(root or DEFAULT_CREDENTIAL_ROOT).expanduser()
    store_key = Path(key_path or DEFAULT_KEY_PATH).expanduser()
    with _LOCK:
        cipher = _fernet(store_key, create=True)
        assert cipher is not None
        existing_path: Path | None = None
        existing_record: dict[str, Any] | None = None
        for path, record in _records(
            clean_actor,
            root=store_root,
            key_path=store_key,
        ):
            if (
                record.get("protocol") == clean_context["protocol"]
                and record.get("host") == clean_context["host"]
                and record.get("username") == clean_context["username"]
            ):
                existing_path = path
                existing_record = record
                break
        credential_id = (
            str(existing_record.get("credential_id") or "")
            if existing_record
            else f"gitcred-{secrets.token_hex(12)}"
        )
        now = utc_now_iso()
        record = {
            "schema": _SCHEMA,
            "credential_id": credential_id,
            "actor": clean_actor,
            **clean_context,
            "status": "active",
            "secret": clean_secret,
            "created_at": (
                str(existing_record.get("created_at") or now)
                if existing_record
                else now
            ),
            "updated_at": now,
            "last_used_at": now,
        }
        target = existing_path or _record_path(clean_actor, credential_id, store_root)
        _write_record(target, record, cipher)
        return _metadata(record)


def mark_credential_invalid(
    actor: str,
    credential_id: str,
    *,
    root: str | Path | None = None,
    key_path: str | Path | None = None,
) -> dict[str, Any] | None:
    clean_actor = _clean_actor(actor)
    store_root = Path(root or DEFAULT_CREDENTIAL_ROOT).expanduser()
    store_key = Path(key_path or DEFAULT_KEY_PATH).expanduser()
    with _LOCK:
        cipher = _fernet(store_key, create=False)
        if cipher is None:
            return None
        path = _record_path(clean_actor, credential_id, store_root)
        if not path.is_file():
            return None
        record = _read_record(path, cipher)
        if record.get("actor") != clean_actor:
            raise PermissionError("不能修改其他 Portal 用户的 Git 凭据。")
        record["status"] = "invalid"
        record["secret"] = ""
        record["updated_at"] = utc_now_iso()
        _write_record(path, record, cipher)
        return _metadata(record)


def mark_credential_used(
    actor: str,
    credential_id: str,
    *,
    root: str | Path | None = None,
    key_path: str | Path | None = None,
) -> dict[str, Any] | None:
    clean_actor = _clean_actor(actor)
    store_root = Path(root or DEFAULT_CREDENTIAL_ROOT).expanduser()
    store_key = Path(key_path or DEFAULT_KEY_PATH).expanduser()
    with _LOCK:
        cipher = _fernet(store_key, create=False)
        if cipher is None:
            return None
        path = _record_path(clean_actor, credential_id, store_root)
        if not path.is_file():
            return None
        record = _read_record(path, cipher)
        if record.get("actor") != clean_actor:
            raise PermissionError("不能修改其他 Portal 用户的 Git 凭据。")
        now = utc_now_iso()
        record["last_used_at"] = now
        record["updated_at"] = now
        _write_record(path, record, cipher)
        return _metadata(record)


def delete_credential(
    actor: str,
    credential_id: str,
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    clean_actor = _clean_actor(actor)
    store_root = Path(root or DEFAULT_CREDENTIAL_ROOT).expanduser()
    with _LOCK:
        path = _record_path(clean_actor, credential_id, store_root)
        deleted = path.is_file()
        path.unlink(missing_ok=True)
    return {
        "credential_id": credential_id,
        "deleted": deleted,
    }
