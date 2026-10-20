"""Retention of Harness evidence and unreferenced recall artifacts."""
from __future__ import annotations

import re
import time
from pathlib import Path

REFERENCE_LOCK = "harness-artifact-references"
_HANDLES = re.compile(rb"ha1_[0-9a-f]{64}")


def retention_seconds() -> float:
    """The administrator's Harness retention period (Portal Management → 执行设置)."""
    from .service_settings import ExecutionSettings
    return ExecutionSettings().get()["harness_retention_days"] * 86400.0


def protect_artifact_references(connection, content: bytes) -> None:
    """Serialize new recall references with the retention snapshot."""
    if _HANDLES.search(content):
        connection.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))", (REFERENCE_LOCK,))


def prune_artifacts(agent_data, root: Path, *, retention: float, now: float | None = None, limit: int = 256) -> int:
    """Keep all handles present in retained conversation content or records.

    Content is immutable. A shared transaction lock on reference writers closes
    the gap between the reference snapshot and unlinking old files.
    """
    from .harness_artifacts import ARTIFACT_HANDLE_RE, HarnessArtifactStore
    if not (root / "artifacts").exists():
        return 0
    before = (time.time() if now is None else now) - retention
    # Most runs have nothing old enough to delete. Avoid scanning every
    # database payload and holding the reference lock in that case.
    if not any(
        ARTIFACT_HANDLE_RE.fullmatch(path.stem) and path.stat().st_mtime < before
        for path in (root / "artifacts").glob("*/*.json")
    ):
        return 0
    with agent_data.database.read() as connection:
        acquired = connection.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired", (REFERENCE_LOCK,)).fetchone()
        if not acquired["acquired"]:
            return 0
        referenced: set[str] = set()
        # Check canonical content, including archived traces. Binary payloads
        # are searched as bytes, so an attachment need not be valid UTF-8.
        for row in connection.execute(
            "SELECT body FROM payloads WHERE position(%s::bytea in body)>0 "
            "AND payload_ref IN (SELECT content_ref FROM segments)", (b"ha1_",),
        ):
            referenced.update(item.decode("ascii") for item in _HANDLES.findall(bytes(row["body"])))
        for row in connection.execute("SELECT data_json::text AS data_json FROM agent_operations WHERE data_json::text LIKE %s", ("%ha1_%",)):
            referenced.update(item.decode("ascii") for item in _HANDLES.findall(row["data_json"].encode()))
        return HarnessArtifactStore(root).prune(
            before=before,
            referenced=referenced, limit=limit,
        )


def maintain(agent_data) -> None:
    from .config import execution_state_root
    from .harness_effectiveness import HarnessEffectivenessStore
    from .harness_evolution import HarnessEvolutionStore
    retention = retention_seconds()
    before = time.time() - retention
    HarnessEffectivenessStore.shared().prune(before=before)
    # Hourly collection prunes recorded calls only while recording is on.
    HarnessEvolutionStore().write_turn_calls([], keep_since=before)
    prune_artifacts(agent_data, execution_state_root() / "harness-artifacts", retention=retention)
