from __future__ import annotations

from management_common.format_tags import schema_matches

import copy
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import identity_contract, lineage_storage, resident_backend
from management_portal.resources.workspace_store import canonical_path
from .state_store import (
    FORBIDDEN_FORK_IDENTITY_FIELDS,
    LineageDataStore,
    get_lineage_data_store,
)


RESIDENT_RECORD_SCHEMA = "tfi.resident.v6"
RESIDENT_WORKSPACE_DIR = "workspace"
_CATALOGS: dict[str, "ResidentCatalog"] = {}
_CATALOGS_LOCK = threading.Lock()


def registration_ready(record: Mapping[str, Any]) -> bool:
    return record.get("registration_state") == "ready"


def owned_workspace_path(
    record: Mapping[str, Any],
    resident_id: str = "",
) -> Path:
    node_resident_id = str(resident_id or record.get("resident_id") or "").strip()
    if not node_resident_id:
        raise ValueError("resident_id missing")
    workspace_path = Path(str(record.get("workspace_path") or "")).expanduser()
    if not workspace_path.is_absolute():
        raise ValueError("Resident workspace_path must be absolute")
    return (
        workspace_path
        / "residents"
        / node_resident_id
        / RESIDENT_WORKSPACE_DIR
    ).resolve(strict=False)


def default_cwd_path(record: Mapping[str, Any]) -> str:
    """The owned Workspace is the sole launch-directory authority."""
    if record.get("deleted_at"):
        return ""
    own = record.get("owned_workspace") or {}
    if not identity_contract.is_valid_id(record.get("owned_workspace_id"), "workspace"):
        return ""
    if own.get("workspace_id") != record["owned_workspace_id"]:
        return ""
    try:
        # Stored paths must already be canonical. Resolving the supplied path
        # here would accept a symlink alias as the Resident's owned directory.
        path = str(own.get("path") or "")
        return path if path == canonical_path(owned_workspace_path(record)) else ""
    except (OSError, ValueError):
        return ""


@dataclass(frozen=True)
class ResidentTarget:
    resident_id: str
    lineage_id: str

    @property
    def execution_binding_id(self) -> str:
        return f"resident:{self.resident_id}"

    def to_payload(self) -> dict[str, Any]:
        return {"resident_id": self.resident_id, "lineage_id": self.lineage_id}


class ResidentCatalog:
    """PostgreSQL-backed authority for canonical Resident records."""

    def __init__(
        self,
        lineage_root: str | Path,
        state_store: LineageDataStore,
    ) -> None:
        self.root = Path(lineage_root).expanduser().resolve(strict=False)
        self._identity = identity_contract
        self.state_store = state_store

    @property
    def storage_identity(self) -> str:
        return self.state_store.database_identity

    def _normalize(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        record = copy.deepcopy(dict(raw))
        if not schema_matches(record.get("schema"), "tfi.resident.v6"):
            raise ValueError("Resident record does not match current contract")
        resident_id = str(record.get("resident_id") or "").strip()
        self._identity.validate_id(resident_id, "resident")
        lineage_id = str(record.get("lineage_id") or "").strip()
        self._identity.validate_id(lineage_id, "lineage")
        fork_identity_fields = set(record) & FORBIDDEN_FORK_IDENTITY_FIELDS
        if fork_identity_fields:
            raise ValueError(
                "Resident record contains forbidden Fork identity fields: "
                + ", ".join(sorted(fork_identity_fields))
            )
        if str(record.get("status") or "").strip().casefold() == "budded":
            raise ValueError("Resident status cannot encode the Fork operation")
        if str(record.get("session_state") or "").strip().casefold() == "budded":
            raise ValueError(
                "Resident session_state cannot encode the Fork operation"
            )
        source_resident_id = str(record.get("source_resident_id") or "").strip()
        if "principal_hierarchy" in record:
            from .principal_hierarchy import resident_assignment

            resident_assignment(record["principal_hierarchy"])
        if source_resident_id:
            self._identity.validate_id(source_resident_id, "resident")
        try:
            resident_backend.validate_resident_session_identity(record)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if not default_cwd_path(record):
            raise ValueError(
                "Resident default Workspace Binding does not match current contract"
            )
        return record

    def snapshot(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.state_store.all_residents()]

    def all(self) -> list[dict[str, Any]]:
        return self.snapshot()

    def get(self, resident_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        return copy.deepcopy(
            self.state_store.resident_record(
                str(resident_id or "").strip(), include_deleted=include_deleted,
            )
        )

    def get_many(self, resident_ids: Iterable[object]) -> list[dict[str, Any]]:
        requested = list(dict.fromkeys(
            str(resident_id or "").strip()
            for resident_id in resident_ids
            if str(resident_id or "").strip()
        ))
        return [
            copy.deepcopy(item)
            for item in self.state_store.residents_by_id(requested)
        ]

    def get_identity_records(self, resident_ids: Iterable[object]) -> list[dict[str, Any]]:
        """Canonical identity and Family roles without filesystem projections."""
        return self.state_store.resident_identity_records(
            str(item or '').strip() for item in resident_ids
        )

    def get_admission(self, resident_id: str) -> dict[str, Any]:
        """Admission reads identity/configuration, never the execution filesystem."""
        records = self.get_identity_records([resident_id])
        if not records or records[0].get("deleted_at"):
            raise KeyError(resident_id)
        return records[0]

    def get_usage_memberships(self, resident_ids: Iterable[object]) -> list[dict[str, str]]:
        """Current live identities and membership, without full Resident assembly."""
        requested = list(dict.fromkeys(
            str(resident_id or "").strip()
            for resident_id in resident_ids
            if str(resident_id or "").strip()
        ))
        return self.state_store.resident_usage_memberships(requested)

    def get_historical(self, resident_id: str) -> dict[str, Any]:
        """Read retained identity, including deleted Residents, for history owners."""
        return copy.deepcopy(self.state_store.resident_record(
            str(resident_id or "").strip(), include_deleted=True,
        ))

    def for_lineage(self, lineage_id: str) -> list[dict[str, Any]]:
        target = str(lineage_id or "").strip()
        return [
            copy.deepcopy(item)
            for item in self.state_store.residents_for_lineage(target)
        ]


    def target(self, resident_id: str) -> ResidentTarget:
        record = self.get(resident_id)
        return ResidentTarget(
            resident_id=str(record["resident_id"]),
            lineage_id=str(record["lineage_id"]),
        )

    def validate_record(self, record: Mapping[str, Any]) -> None:
        self._normalize(record)

    def upsert(self, record: Mapping[str, Any], *, commit_configuration: bool = False, commit_intake: bool = False) -> None:
        normalized = self._normalize(record)
        self.state_store.put_resident_record(
            str(normalized["resident_id"]),
            normalized,
            commit_configuration=commit_configuration,
            commit_intake=commit_intake,
        )

    def remove(self, tombstones: Iterable[Mapping[str, Any]]) -> list[str]:
        """Mark retained identities deleted without removing Agent history."""
        records = [dict(item) for item in tombstones]
        if any(not str(record.get("deleted_at") or "") for record in records):
            raise ValueError("Resident deletion requires deleted_at")
        from . import resident_mailbox
        from .resident_lifecycle import ResidentLifecycleOperations
        from . import resident_lifecycle_domain
        for record in records:
            resident_lifecycle_domain.ensure_resident_agent(record, actor=str(record.get("deleted_by") or ""))

        def commit() -> list[str]:
            # Its Mailbox closes with the Resident; a repeated deletion finds it closed.
            for record in records:
                resident_id = str(record["resident_id"])
                try:
                    self.get(resident_id)
                except KeyError:
                    pass
                else:
                    resident_mailbox.close(resident_id, str(record["deleted_at"]))
                job = self.state_store.resident_execution_record(resident_id)
                if job.get("execution_id") and not job.get("execution_acknowledged"):
                    ResidentLifecycleOperations(self.state_store).enqueue_cleanup(resident_id, [{
                        "kind": "execution_ack", "execution_id": job["execution_id"],
                        "actor": str(job.get("actor") or record.get("deleted_by") or ""),
                    }])
                self.state_store.delete_resident_execution_record(resident_id)
            return self.state_store.remove_residents(records)

        return self.state_store.transaction(commit)


def get_catalog() -> ResidentCatalog:
    root = lineage_storage.configured_lineage_folder_root().expanduser().resolve(
        strict=False,
    )
    state_store = get_lineage_data_store()
    database = state_store.database_identity
    key = f"{root}\0{database}"
    with _CATALOGS_LOCK:
        catalog = _CATALOGS.get(key)
        if catalog is None:
            catalog = ResidentCatalog(root, state_store)
            _CATALOGS[key] = catalog
        return catalog
