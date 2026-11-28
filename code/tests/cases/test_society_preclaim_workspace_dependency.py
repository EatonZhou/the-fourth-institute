"""Durable preclaim dependencies and exact recovery of legacy creation requests."""
from contextlib import contextmanager
from pathlib import Path

import pytest

from management_portal.resources.creation import Creation, creation_dependency_record
from management_portal.resources.workspace_store import get_workspace_store
from management_portal.society_service import resident_domain, resident_registration_domain, workspace_dependencies
from management_portal.society_service.creation_intents import CreationIntent
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.directory_fixture_support import directory_owners  # noqa: F401
from tests.cases.isa_test_support import isolated_instruction_store  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import _new_session_payload
from tests.cases.test_resident_registration_resource_ack import registration  # noqa: F401
from tests.cases.test_lineage_backend import lineage_store  # noqa: F401


TARGET = "workspace_123456789abc"
RESIDENT = "resident_123456789abc"


def classify(workspace_id):
    with get_lineage_data_store().database.transaction() as connection:
        get_workspace_store().cleanup.lock(connection)
        return workspace_dependencies.pending_workspace_dependency(workspace_id, connection=connection)


@pytest.mark.parametrize("kind", ["resident", "create_resident_fork_from_message"])
def test_society_record_blocks_retirement_before_any_creation(registration, tmp_path, kind):
    intent = CreationIntent(kind, "sample_admin", {"fixture": kind}, "preclaim")
    with intent.open(lambda: {}):
        assert classify(TARGET) == "unknown"
        intent.snapshot["record"] = {
            "resident_id": RESIDENT, "created_by": "sample_admin",
            "workspace_path": str(tmp_path / "different-tree"),
            "workspace_bindings": [{"workspace_id": TARGET}],
        }
        intent.save()
        assert classify(TARGET) == "dependent"
        with get_lineage_data_store().database.transaction() as connection:
            intent.complete(connection)
    assert classify(TARGET) == "independent"


def test_pending_default_requires_its_owning_lineage(registration):
    resident, _ = registration
    default = next(binding["workspace_id"] for binding in resident["workspace_bindings"])
    path = get_workspace_store().rows([default])[default]["path"]
    intent = CreationIntent("resident", "sample_admin", {}, "default")
    with intent.open(lambda: {}):
        intent.snapshot["record"] = {
            "resident_id": RESIDENT, "created_by": "sample_admin",
            "lineage_id": resident["lineage_id"], "workspace_path": str(Path(path).parent),
            "workspace_bindings": [],
        }
        intent.save()
        assert classify(default) == "dependent"
        assert classify(TARGET) == "independent"


def test_rejected_unallocated_intent_can_reopen_without_leaving_global_unknown(registration):
    intent = CreationIntent("resident", "sample_admin", {}, "not-yet-admitted")
    with pytest.raises(ValueError, match="invalid request"):
        with intent.open(lambda: {}):
            raise ValueError("invalid request")
    assert classify(TARGET) == "independent"
    with intent.open(lambda: {}):
        assert classify(TARGET) == "unknown"


def test_preparing_handoff_names_source_before_copy_claim(lineage_store, tmp_path, monkeypatch):
    from management_portal.society_service import lineage_handoff_preparation, lineage_handoffs
    from tests.cases.test_handoff_directory_creation import draft

    lineage_id, handoff, _, root = draft(lineage_store, tmp_path)
    source = root["workspace_id"]
    original = lineage_handoff_preparation.prepare_items
    observations = []

    def inspect_pending(*args, **kwargs):
        observations.append(classify(source))
        return original(*args, **kwargs)

    monkeypatch.setattr(lineage_handoff_preparation, "prepare_items", inspect_pending)
    lineage_handoffs.execute_handoff(lineage_id, handoff["id"], "sample_admin")
    assert observations == ["dependent"]
    assert classify(source) == "independent"


@pytest.mark.parametrize("old_schema", [False, True])
def test_resident_registration_resumes_original_null_creation(registration, tmp_path, monkeypatch, old_schema):
    reference = tmp_path / "reference"
    reference.mkdir()
    get_workspace_store().ensure(TARGET, str(reference))
    original = resident_registration_domain._stage_registration
    keys = []

    @contextmanager
    def stage(record, payload, workspace_path, runtime_settings, actor, intent):
        from management_portal.society_service import resident_record_domain

        target = (resident_record_domain.resident_folder_path(record)
                  if payload.get("lineage_folder_path") else workspace_path)
        old = Creation(record["resident_id"], actor=actor, payload=intent.request, operation_id=intent.key)
        with old.prepare([(target, "directory")]):
            keys.append(old.key)
        if old_schema:
            with old.db.transaction() as connection:
                connection.execute(f"ALTER TABLE {old.table} DROP COLUMN workspace_dependencies")
        assert classify(TARGET) == "unknown"
        with original(record, payload, workspace_path, runtime_settings, actor, intent) as staged:
            assert staged[0].key == old.key
            assert staged[0].workspace_dependencies is None
            yield staged

    monkeypatch.setattr(resident_registration_domain, "_stage_registration", stage)
    record = resident_domain.register_resident(
        {**_new_session_payload("legacy resume"), "workspace_paths": [str(reference)]}, "sample_admin",
    )
    assert TARGET in workspace_dependencies.binding_workspace_ids(record["workspace_bindings"])
    with get_workspace_store().database.transaction() as connection:
        stored = creation_dependency_record(keys[0], connection=connection)
    assert stored["resource_id"] == record["resident_id"]
    assert stored["completed"] and stored["workspace_dependencies"] is None


@pytest.mark.parametrize("field", ["operation_id", "resource_id", "actor", "scope_id", "payload"])
def test_legacy_selection_requires_every_frozen_field(registration, monkeypatch, field):
    from management_portal.resources import creation

    actor, operation_id, payload = "sample_admin", "legacy-proof", {"frozen": True}
    evidence = {
        "operation_id": creation.creation_operation_key(actor, operation_id),
        "resource_id": RESIDENT, "actor": actor, "scope_id": "", "payload": payload,
        "completed": False, "aborted": False, "workspace_dependencies": None,
    }
    evidence[field] = "not the original evidence"
    monkeypatch.setattr(creation, "creation_dependency_record", lambda *_args, **_kwargs: evidence)
    assert workspace_dependencies.recoverable_workspace_dependencies(
        RESIDENT, actor=actor, operation_id=operation_id, payload=payload, workspace_ids=(),
    ) == ()


def test_legacy_commit_checks_actual_workspace_activity(registration, tmp_path):
    from management_portal.resources.workspace_store import now

    root = tmp_path / "retired"
    root.mkdir()
    workspaces = get_workspace_store()
    workspaces.ensure(TARGET, str(root))
    old = Creation(RESIDENT, actor="sample_admin", payload={}, operation_id="old-active-check")
    with old.prepare([(tmp_path / "unpublished", "directory")]):
        with workspaces.database.transaction() as connection:
            workspaces.cleanup.lock(connection)
            workspaces.mark_deleted(TARGET, deleted_at=now(), deleted_by="sample_admin", connection=connection)
        with workspaces.database.transaction() as connection:
            with pytest.raises(ValueError, match="no longer active"):
                workspace_dependencies.validate_creation_bindings(
                    old, [{"workspace_id": TARGET}], connection=connection, legacy=True,
                )
            assert creation_dependency_record(old.key, connection=connection)["workspace_dependencies"] is None


def test_handoff_resumes_old_copy_without_rewriting_its_null_declaration(directory_owners, tmp_path, monkeypatch):
    from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
    from management_portal.society_service.lineage_handoff_backend import materialize_handoff_item

    root = tmp_path / "source"
    root.mkdir()
    (root / "file").write_text("keep")
    get_workspace_store().ensure(TARGET, str(root))
    item = {
        "source": {"workspace_id": TARGET, "path": str(root)},
        "target": {"path": str(tmp_path / "copy")},
        "transfer": {"mode": "copy", "method": "filesystem_copy"},
    }
    options = dict(allowed_target_roots=[tmp_path], actor="sample_admin", checked_at="now",
                   operation_id="legacy-copy", parent_id="lineage_123456789abc")
    keys = []
    materialize = WorkspaceCreationStore.materialize

    def stage_old(store, **arguments):
        arguments.pop("resolve_workspace_dependencies")
        arguments["workspace_dependencies"] = None
        return materialize(store, **arguments)

    def stop_after_prepare(creation, _initialize):
        keys.append(creation.key)
        raise RuntimeError("old attempt interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(WorkspaceCreationStore, "materialize", stage_old)
        patch.setattr(Creation, "publish", stop_after_prepare)
        with pytest.raises(RuntimeError, match="old attempt interrupted"):
            materialize_handoff_item(item, **options)

    assert classify(TARGET) == "unknown"
    result = materialize_handoff_item(item, **options)
    assert (Path(result["target_path"]) / "file").read_text() == "keep"
    with get_workspace_store().database.transaction() as connection:
        stored = creation_dependency_record(keys[0], connection=connection)
    assert stored["completed"] and stored["workspace_dependencies"] is None
