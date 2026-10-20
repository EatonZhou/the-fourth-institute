from __future__ import annotations

import hashlib
from management_common.file_lock import lock_stream
import hmac
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .harness_content_recall import search_text, read_text_slice
from .harness_history import canonical_json


ARTIFACT_HANDLE_PREFIX = "ha1_"
ARTIFACT_HANDLE_RE = re.compile(r"^ha1_[0-9a-f]{64}$")
MAX_ARTIFACT_READ_CHARS = 32_000
MAX_HARNESS_ARTIFACT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class HarnessArtifactReference:
    handle: str
    sha256: str
    content_bytes: int
    content_chars: int
    content_kind: str


class HarnessArtifactStore:
    """Persist exact offloaded values behind unguessable capability handles."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        self._key: bytes | None = None

    def put(
        self,
        *,
        scope: str,
        value: object,
    ) -> HarnessArtifactReference:
        clean_scope = str(scope or "").strip()
        if not clean_scope:
            raise ValueError("Harness artifact scope is required")
        content = (
            value
            if isinstance(value, str)
            else canonical_json(value, pretty=True)
        )
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > MAX_HARNESS_ARTIFACT_BYTES:
            raise ValueError(
                "Harness artifact exceeds the 64 MiB retention limit"
            )
        digest = hashlib.sha256(content_bytes).hexdigest()
        handle = self._handle(clean_scope, digest)
        reference = HarnessArtifactReference(
            handle=handle,
            sha256=digest,
            content_bytes=len(content_bytes),
            content_chars=len(content),
            content_kind="text" if isinstance(value, str) else "json",
        )
        envelope = {
            "schema": "portal.harness-artifact",
            "schema_version": 1,
            "handle": handle,
            "sha256": digest,
            "content_bytes": reference.content_bytes,
            "content_chars": reference.content_chars,
            "content_kind": reference.content_kind,
            "content": content,
        }
        with self._lock, self._retention_lock():
            self._ensure_root()
            artifact_path = self._artifact_path(handle)
            if artifact_path.exists():
                self._validate_envelope(
                    self._read_envelope(artifact_path),
                    expected_handle=handle,
                )
            else:
                self._atomic_write(
                    artifact_path,
                    json.dumps(
                        envelope,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                )
            self._grant_locked(clean_scope, handle)
            os.utime(artifact_path, None)
        return reference

    def grant(self, *, scope: str, handles: set[str] | tuple[str, ...]) -> int:
        clean_scope = str(scope or "").strip()
        if not clean_scope:
            return 0
        granted = 0
        with self._lock, self._retention_lock():
            self._ensure_root()
            for handle in handles:
                clean_handle = self._validate_handle(handle)
                if not self._artifact_path(clean_handle).is_file():
                    continue
                self._grant_locked(clean_scope, clean_handle)
                os.utime(self._artifact_path(clean_handle), None)
                granted += 1
        return granted

    def has_scope(self, scope: str) -> bool:
        clean_scope = str(scope or "").strip()
        if not clean_scope:
            return False
        scope_dir = self._scope_dir(clean_scope)
        if not scope_dir.is_dir():
            return False
        try:
            return any(
                ARTIFACT_HANDLE_RE.fullmatch(entry.name)
                for entry in scope_dir.iterdir()
                if entry.is_file()
            )
        except OSError:
            return False

    def read(
        self,
        handle: str,
        *,
        scope: str = "",
        offset: int = 0,
        limit: int = 16_000,
    ) -> dict[str, Any]:
        clean_handle = self._validate_handle(handle)
        clean_scope = str(scope or "").strip()
        if (
            clean_scope
            and not (self._scope_dir(clean_scope) / clean_handle).is_file()
        ):
            raise KeyError("Harness artifact is not granted to this session")
        try:
            clean_offset = int(offset)
            clean_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("Artifact offset and limit must be integers") from exc
        clean_limit = max(1, min(MAX_ARTIFACT_READ_CHARS, clean_limit))
        envelope = self._recall_envelope(clean_handle)
        return {
            "handle": clean_handle,
            "sha256": str(envelope["sha256"]),
            "content_kind": str(envelope["content_kind"]),
            **read_text_slice(str(envelope["content"]), offset=clean_offset, limit=clean_limit),
        }

    def search(
        self,
        handle: str,
        *,
        scope: str = "",
        query: object,
        max_hits: object = 5,
        context_chars: object = 600,
    ) -> dict[str, Any]:
        clean_handle = self._validate_handle(handle)
        clean_scope = str(scope or "").strip()
        if (
            clean_scope
            and not (self._scope_dir(clean_scope) / clean_handle).is_file()
        ):
            raise KeyError("Harness artifact is not granted to this session")
        envelope = self._recall_envelope(clean_handle)
        result = search_text(
            str(envelope["content"]),
            query,
            max_hits=max_hits,
            context_chars=context_chars,
        )
        return {
            "handle": clean_handle,
            "sha256": str(envelope["sha256"]),
            "content_kind": str(envelope["content_kind"]),
            **result,
        }

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        (self.root / "artifacts").mkdir(mode=0o700, exist_ok=True)
        (self.root / "scopes").mkdir(mode=0o700, exist_ok=True)

    @contextmanager
    def _retention_lock(self):
        self._ensure_root()
        with (self.root / ".retention-lock").open("a") as lock:
            with lock_stream(lock):
                yield

    def _recall_envelope(self, handle: str) -> dict[str, Any]:
        with self._lock, self._retention_lock():
            path = self._artifact_path(handle)
            envelope = self._read_envelope(path)
            self._validate_envelope(envelope, expected_handle=handle)
            os.utime(path, None)
            return envelope

    def prune(self, *, before: float, referenced: set[str], limit: int = 256) -> int:
        removed = set()
        with self._lock, self._retention_lock():
            for path in (self.root / "artifacts").glob("*/*.json"):
                if not ARTIFACT_HANDLE_RE.fullmatch(path.stem) or path.stem in referenced:
                    continue
                if path.stat().st_mtime >= before:
                    continue
                path.unlink()
                removed.add(path.stem)
                if len(removed) >= max(1, limit):
                    break
            if removed:
                for directory in (self.root / "scopes").iterdir():
                    if directory.is_dir():
                        for handle in removed:
                            (directory / handle).unlink(missing_ok=True)
        return len(removed)

    def _capability_key(self) -> bytes:
        with self._lock:
            if self._key is not None:
                return self._key
            self._ensure_root()
            path = self.root / ".capability-key"
            candidate = path.with_name(
                f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
            )
            key = secrets.token_bytes(32)
            descriptor = os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                os.write(descriptor, key)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            try:
                try:
                    os.link(candidate, path)
                except FileExistsError:
                    pass
                key = path.read_bytes()
            finally:
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
            if len(key) < 32:
                raise RuntimeError(
                    "Harness artifact capability key is invalid"
                )
            self._key = key
            return key

    def _handle(self, scope: str, digest: str) -> str:
        payload = f"{scope}\0{digest}".encode("utf-8")
        signature = hmac.new(
            self._capability_key(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return f"{ARTIFACT_HANDLE_PREFIX}{signature}"

    @staticmethod
    def _validate_handle(handle: object) -> str:
        clean = str(handle or "").strip().lower()
        if not ARTIFACT_HANDLE_RE.fullmatch(clean):
            raise ValueError("Invalid Harness artifact handle")
        return clean

    def _artifact_path(self, handle: str) -> Path:
        clean = self._validate_handle(handle)
        directory = self.root / "artifacts" / clean[4:6]
        return directory / f"{clean}.json"

    def _scope_dir(self, scope: str) -> Path:
        digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        return self.root / "scopes" / digest

    def _grant_locked(self, scope: str, handle: str) -> None:
        scope_dir = self._scope_dir(scope)
        scope_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = scope_dir / handle
        if marker.exists():
            return
        self._atomic_write(marker, b"")

    @staticmethod
    def _read_envelope(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError("Harness artifact was not found") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Harness artifact could not be read") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Harness artifact envelope is invalid")
        return payload

    @staticmethod
    def _validate_envelope(
        envelope: dict[str, Any],
        *,
        expected_handle: str,
    ) -> None:
        if (
            envelope.get("schema") != "portal.harness-artifact"
            or int(envelope.get("schema_version") or 0) != 1
            or str(envelope.get("handle") or "") != expected_handle
        ):
            raise RuntimeError("Harness artifact identity is invalid")
        content = str(envelope.get("content") or "")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest != str(envelope.get("sha256") or ""):
            raise RuntimeError("Harness artifact content digest is invalid")

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
