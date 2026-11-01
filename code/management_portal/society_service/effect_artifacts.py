"""Durable, scoped JSON artifacts for Effect recovery and final outputs.

References belong to one activation. Recovery blobs expire when its terminal
record commits; output blobs expire seven days after that completion.
The caller must already be authorized to read the corresponding Lineage Run.
"""
from __future__ import annotations

from management_common.format_tags import schema_matches
from management_common.portal_paths import path as portal_path

import hashlib
import json
import os
from pathlib import Path
import tempfile
from contextlib import contextmanager
from typing import Any


class ArtifactStore:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else portal_path("features") / "effect_artifacts"

    def _scope(self, lineage_id: str, activation_key: str) -> Path:
        return self.root / hashlib.sha256(lineage_id.encode()).hexdigest() / hashlib.sha256(activation_key.encode()).hexdigest()

    def put(self, lineage_id: str, activation_key: str, value: Any, *, purpose: str) -> dict[str, Any]:
        if purpose not in {"recovery", "output"}:
            raise ValueError("invalid Effect artifact purpose")
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        digest = hashlib.sha256(data).hexdigest()
        directory = self._scope(lineage_id, activation_key) / purpose
        from management_common.directory_retention import RetentionStore
        retention = RetentionStore()
        retention.declare(directory.absolute(), category='runtime_tmp' if purpose == 'recovery' else 'background',
                          owner_id=self._owner(lineage_id, activation_key))
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for parent in (directory.parent, directory.parent.parent, self.root, self.root.parent):
            with _directory(parent) as handle:
                os.fsync(handle)
        path = directory / (digest + ".json")
        descriptor, temporary = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            with _directory(directory) as handle:
                os.fsync(handle)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"schema": "tfi.effect-artifact.v1", "lineage_id": lineage_id,
                "activation_key": activation_key, "purpose": purpose, "sha256": digest,
                "size_bytes": len(data), "content_type": "application/json"}

    def read(self, lineage_id: str, activation_key: str, reference: dict[str, Any]) -> Any:
        digest = str(reference.get("sha256") or "")
        purpose = reference.get("purpose")
        if (not schema_matches(reference.get("schema"), "tfi.effect-artifact.v1") or
                reference.get("lineage_id") != lineage_id or reference.get("activation_key") != activation_key or
                purpose not in {"recovery", "output"} or len(digest) != 64 or
                any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError("invalid or foreign Effect artifact reference")
        data = (self._scope(lineage_id, activation_key) / str(purpose) / (digest + ".json")).read_bytes()
        if len(data) != reference.get("size_bytes") or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Effect artifact integrity check failed")
        return json.loads(data)

    def clear_recovery(self, lineage_id: str, activation_key: str) -> None:
        from management_common.directory_retention import RetentionStore
        from management_common.file_retention import remove_generated
        path = self._scope(lineage_id, activation_key) / "recovery"
        owner = self._owner(lineage_id, activation_key)
        retention = RetentionStore()
        retention.finish(owner)
        retention.sweep(owner_id=owner, categories=['runtime_tmp'])
        # Existing recovery folders predate declarations; their exact scope is
        # still owned by the durable Effect journal, so they can be queued too.
        if path.exists() and not retention.queue.pending_at(path):
            remove_generated(path, owner_id=owner)
        retention.queue.run_pending()

    @staticmethod
    def _owner(lineage_id, activation_key):
        return 'effect:' + lineage_id + ':' + hashlib.sha256(activation_key.encode()).hexdigest()


@contextmanager
def _directory(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield descriptor
    finally:
        os.close(descriptor)
