"""Society's transaction boundary for pending Workspace dependencies."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest

from management_portal.society_service import workspace_dependencies as deps
from tests.cases.directory_fixture_support import directory_owners  # noqa: F401
from tests.cases.isa_test_support import isolated_instruction_store  # noqa: F401
from tests.cases.test_resident_registration_resource_ack import registration  # noqa: F401


PARENT = "workspace_1e3bf2b860ee"
OTHER = "workspace_f1ebf7a4a3e6"


class TestWorkspaceDependency(unittest.TestCase):
    def test_reader_passes_exact_identity_and_original_connection(self):
        from management_portal.resources import creation

        connection = object()
        for outcome in ("dependent", "independent", "unknown"):
            reader = Mock(return_value=outcome)
            with patch.object(creation, "pending_creation_workspace_dependency", reader), patch.object(
                deps, "_pending_resident_dependency", return_value="independent",
            ), patch.object(deps, "_pending_handoff_dependency", return_value="independent"):
                self.assertEqual(
                    deps.pending_workspace_dependency(PARENT, connection=connection), outcome,
                )
            reader.assert_called_once_with(PARENT, connection=connection)

    def test_unknown_diagnostics_merge_only_safe_deduplicated_reasons(self):
        from management_portal.resources import creation
        from management_portal.society_service import creation_intents

        connection = Mock()
        connection.execute.return_value.fetchone.return_value = ("work", "edges", None)
        incomplete = {"record": {"created_by": "hidden-actor"}}
        record = {"resident_id": "resident_0123456789ab", "created_by": "hidden-actor"}
        intent = {
            "record": record, "operation_id": "resident:hidden-operation", "scope_id": "hidden-scope",
            "request": {"path": "/hidden/path"},
        }
        lookup_error = creation.CreationDependencyUnknown("hidden-operation")

        def resource_reader(workspace_id, *, connection, diagnostics):
            diagnostics.extend([
                {"source": "resource_creation", "reason": "missing_declaration", "actor": "hidden-actor"},
                {"source": "resource_creation", "reason": "missing_declaration"},
                {"source": "resource_creation", "reason": "/hidden/path"},
            ])
            return "unknown"

        diagnostics = []
        with patch.object(creation, "pending_creation_workspace_dependency", side_effect=resource_reader) as reader, patch.object(
            creation_intents, "pending_resident_workspace_intents",
            return_value=[incomplete, intent, intent, intent],
        ) as intents, patch.object(
            creation, "creation_dependency_record",
            side_effect=[None, lookup_error, dict.fromkeys(("operation_id", "resource_id", "actor", "scope_id", "payload"))],
        ):
            self.assertEqual(deps.pending_workspace_dependency(PARENT, connection=connection, diagnostics=diagnostics), "unknown")
        reader.assert_called_once()
        self.assertIs(reader.call_args.kwargs["connection"], connection)
        intents.assert_called_once_with(connection)
        self.assertEqual(diagnostics, [
            {"source": "resource_creation", "reason": "missing_declaration"},
            {"source": "resident_creation_intent", "reason": "creation_association_mismatch"},
            {"source": "resident_creation_intent", "reason": "creation_association_unavailable"},
            {"source": "resident_creation_intent", "reason": "intent_snapshot_incomplete"},
            {"source": "resident_creation_intent", "reason": "workspace_binding_evidence_invalid"},
            {"source": "workspace_handoff", "reason": "handoff_store_incomplete"},
        ])

    def test_unknown_without_a_known_reason_uses_fixed_fallback(self):
        from management_portal.resources import creation

        def report_unknown(workspace_id, *, connection, diagnostics):
            diagnostics.extend(["hidden-operation", {"reason": "hidden-actor"}])
            return "unknown"

        diagnostics = []
        with patch.object(creation, "pending_creation_workspace_dependency", side_effect=report_unknown), patch.object(
            deps, "_pending_resident_dependency", return_value="independent",
        ), patch.object(deps, "_pending_handoff_dependency", return_value="independent"):
            self.assertEqual(deps.pending_workspace_dependency(PARENT, connection=object(), diagnostics=diagnostics), "unknown")
        self.assertEqual(diagnostics, [{"source": "resource_creation", "reason": "evidence_unavailable"}])

    def test_affirmative_dependency_suppresses_unrelated_unknown_diagnostics(self):
        from management_portal.resources import creation

        def report_unknown(workspace_id, *, connection, diagnostics):
            diagnostics.append({"source": "resource_creation", "reason": "missing_declaration"})
            return "unknown"

        connection = Mock()
        connection.execute.return_value.fetchone.return_value = ("work", "edges", "items")
        connection.execute.return_value.__iter__ = Mock(
            return_value=iter([{"source_workspace_id": PARENT, "target_workspace_id": None}]),
        )
        diagnostics = []
        with patch.object(creation, "pending_creation_workspace_dependency", side_effect=report_unknown), patch.object(
            deps, "_pending_resident_dependency", return_value="independent",
        ):
            self.assertEqual(deps.pending_workspace_dependency(PARENT, connection=connection, diagnostics=diagnostics), "dependent")
        self.assertEqual(diagnostics, [])

    def test_commit_checks_actual_canonical_ids_in_original_transaction(self):
        connection = object()
        creation = SimpleNamespace(assert_workspace_dependencies=Mock())
        with patch.object(deps, "require_active_workspaces") as active:
            deps.validate_creation_bindings(
                creation, [{"workspace_id": PARENT}, {"workspace_id": OTHER}],
                connection=connection,
            )
        creation.assert_workspace_dependencies.assert_called_once_with(
            {PARENT, OTHER}, connection=connection,
        )
        active.assert_called_once_with({PARENT, OTHER}, connection=connection)

    def test_commit_refuses_an_unannounced_id_before_publication(self):
        creation = SimpleNamespace(
            assert_workspace_dependencies=Mock(side_effect=ValueError("exceed declared")),
        )
        with patch.object(deps, "require_active_workspaces") as active:
            with self.assertRaisesRegex(ValueError, "exceed declared"):
                deps.validate_creation_bindings(
                    creation, [{"workspace_id": PARENT}], connection=object(),
                )
        active.assert_not_called()

    def test_live_check_locks_directory_before_target_rows(self):
        order = []
        connection = SimpleNamespace(
            in_transaction=True,
            execute=lambda *_: order.append("target") or SimpleNamespace(
                fetchall=lambda: [{"workspace_id": OTHER}],
            ),
        )
        workspaces = SimpleNamespace(
            cleanup=SimpleNamespace(lock=lambda _: order.append("directory")), table="workspace.workspaces",
        )
        with patch.object(deps, "get_workspace_store", return_value=workspaces):
            with self.assertRaisesRegex(ValueError, "no longer active"):
                deps.require_active_workspaces([PARENT, OTHER], connection=connection)
        self.assertEqual(order, ["directory", "target"])


@pytest.mark.parametrize("placement", ["child", "outside"])
def test_pending_creation_names_workspace_independently_of_target_path(directory_owners, tmp_path, placement):
    from management_portal.resources.creation import Creation
    from management_portal.resources.workspace_store import get_workspace_store

    parent = tmp_path / "parent"
    parent.mkdir()
    workspaces = get_workspace_store()
    workspaces.ensure(PARENT, str(parent))
    target = (parent if placement == "child" else tmp_path) / "new"
    creation = Creation(
        "resident_0123456789ab", actor="sample_admin", operation_id=placement,
        payload={}, workspace_dependencies=[PARENT],
    )
    with creation.prepare([(target, "directory")]):
        with directory_owners.database.transaction() as connection:
            workspaces.cleanup.lock(connection)
            assert deps.pending_workspace_dependency(PARENT, connection=connection) == "dependent"
            assert deps.pending_workspace_dependency(OTHER, connection=connection) == "independent"
            with pytest.raises(ValueError, match="exceed declared"):
                deps.validate_creation_bindings(creation, [{"workspace_id": OTHER}], connection=connection)
            deps.validate_creation_bindings(creation, [{"workspace_id": PARENT}], connection=connection)


def test_legacy_creation_has_unknown_dependency(directory_owners, tmp_path):
    from management_portal.resources.creation import Creation
    from management_portal.resources.workspace_store import get_workspace_store

    creation = Creation("resident_0123456789ab", actor="sample_admin", operation_id="legacy", payload={})
    with creation.prepare([(tmp_path / "old-target", "directory")]):
        with directory_owners.database.transaction() as connection:
            get_workspace_store().cleanup.lock(connection)
            diagnostics = []
            assert deps.pending_workspace_dependency(PARENT, connection=connection, diagnostics=diagnostics) == "unknown"
            assert diagnostics == [{"source": "resource_creation", "reason": "missing_declaration"}]


def test_copy_handoff_declares_source_before_materializing(directory_owners, tmp_path, monkeypatch):
    from management_portal.resources.creation import Creation
    from management_portal.resources.workspace_store import get_workspace_store
    from management_portal.society_service.lineage_handoff_backend import materialize_handoff_item

    source = tmp_path / "source"
    source.mkdir()
    (source / "keep.txt").write_text("copy")
    workspaces = get_workspace_store()
    workspaces.ensure(PARENT, str(source))
    original = Creation.publish

    def check_dependency(creation, initialize):
        with directory_owners.database.transaction() as connection:
            workspaces.cleanup.lock(connection)
            assert deps.pending_workspace_dependency(PARENT, connection=connection) == "dependent"
        return original(creation, initialize)

    monkeypatch.setattr(Creation, "publish", check_dependency)
    result = materialize_handoff_item(
        {
            "source": {"workspace_id": PARENT, "path": str(source)},
            "target": {"path": str(tmp_path / "copy")},
            "transfer": {"mode": "copy", "method": "filesystem_copy"},
        },
        allowed_target_roots=[tmp_path], checked_at="now", actor="sample_admin",
        operation_id="copy", parent_id="lineage_0123456789ab",
    )
    assert result["target_workspace_id"] != PARENT
    assert (tmp_path / "copy" / "keep.txt").read_text() == "copy"


def test_binding_publication_refuses_a_retired_workspace_even_though_fk_row_exists(registration, tmp_path):
    from management_portal.resources.workspace_store import get_workspace_store, now
    from management_portal.society_service import resident_workspace_storage
    from management_portal.society_service.state_store import get_lineage_data_store

    resident, _ = registration
    state = get_lineage_data_store()
    workspaces = get_workspace_store()
    root = tmp_path / "source"
    root.mkdir()
    workspaces.ensure(PARENT, str(root))
    assert workspaces.rows([PARENT])
    with state.database.transaction() as connection:
        workspaces.cleanup.lock(connection)
        workspaces.mark_deleted(PARENT, deleted_at=now(), deleted_by="sample_admin", connection=connection)
    assert workspaces.rows([PARENT], include_deleted=True)
    with state.database.transaction() as connection:
        previous = resident_workspace_storage.bindings_for_residents(connection, [resident["resident_id"]])
        bindings = [*previous[resident["resident_id"]], {"workspace_id": PARENT, "purpose": ""}]
        with pytest.raises(ValueError, match="no longer active"):
            resident_workspace_storage.replace_bindings(connection, resident["resident_id"], bindings)
        assert resident_workspace_storage.bindings_for_residents(connection, [resident["resident_id"]]) == previous


def test_binding_observes_retirement_that_wins_the_directory_lock(registration, tmp_path, monkeypatch):
    from management_portal.resources.workspace_store import get_workspace_store, now
    from management_portal.society_service import resident_workspace_storage
    from management_portal.society_service.state_store import get_lineage_data_store

    resident, _ = registration
    resident_id = resident["resident_id"]
    state = get_lineage_data_store()
    workspaces = get_workspace_store()
    root = tmp_path / "competing-workspace"
    root.mkdir()
    workspaces.ensure(PARENT, str(root))
    before_lock = Event()
    validate = deps.require_active_workspaces

    def announce(workspace_ids, *, connection):
        before_lock.set()
        return validate(workspace_ids, connection=connection)

    def publish():
        with state.database.transaction() as connection:
            previous = resident_workspace_storage.bindings_for_residents(connection, [resident_id])[resident_id]
            resident_workspace_storage.replace_bindings(
                connection, resident_id, [*previous, {"workspace_id": PARENT, "purpose": ""}],
            )

    monkeypatch.setattr(deps, "require_active_workspaces", announce)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with state.database.transaction() as connection:
            workspaces.cleanup.lock(connection)
            binding = executor.submit(publish)
            assert before_lock.wait(timeout=5)
            workspaces.mark_deleted(PARENT, deleted_at=now(), deleted_by="sample_admin", connection=connection)
        with pytest.raises(ValueError, match="no longer active"):
            binding.result(timeout=5)


def test_resident_registration_protects_reference_before_binding_exists(registration, tmp_path, monkeypatch):
    from management_portal.resources.creation import Creation
    from management_portal.resources.workspace_store import get_workspace_store
    from management_portal.society_service import resident_domain, resident_workspace_storage
    from management_portal.society_service.state_store import get_lineage_data_store
    from tests.cases.test_resident_folder_creation_contract import _new_session_payload

    workspaces = get_workspace_store()
    shared = tmp_path / "reference"
    shared.mkdir()
    workspaces.ensure(PARENT, str(shared))
    original = Creation.publish
    observed = []

    def check_dependency(creation, initialize):
        with get_lineage_data_store().database.transaction() as connection:
            workspaces.cleanup.lock(connection)
            bindings = resident_workspace_storage.bindings_for_residents(connection, [creation.resource_id])
            assert not bindings[creation.resource_id]
            observed.append(deps.pending_workspace_dependency(PARENT, connection=connection))
        return original(creation, initialize)

    monkeypatch.setattr(Creation, "publish", check_dependency)
    payload = {**_new_session_payload("Pending consumer"), "workspace_paths": [str(shared)]}
    resident = resident_domain.register_resident(payload, "sample_admin")
    assert observed == ["dependent"]
    assert PARENT in deps.binding_workspace_ids(resident["workspace_bindings"])
