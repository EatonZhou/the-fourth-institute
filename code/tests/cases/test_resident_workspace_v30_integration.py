"""Resident v30 hydrates Workspace facts once and from one committed view.

:module: resident.workspace-memory
"""
import threading

from management_portal.society_service import resident_catalog, resident_domain
from management_portal.society_service import resident_workspace_storage, resource_client
from management_portal.resources.workspace_store import get_workspace_store
from tests.cases.test_resident_folder_creation_contract import _new_session_payload
from tests.cases.test_resident_workspace_binding_identity import (
    future_creation_store, resident_creation_store,
)


def test_list_hydrates_bindings_and_descriptor_paths_in_one_batch(future_creation_store, monkeypatch):
    """批量读取 Resident 时 Workspace 绑定和路径各查询一次，表中不存路径列"""
    records = [resident_domain.register_resident(_new_session_payload(f"Workspace batch {index}"), "sample_admin")
               for index in range(2)]
    catalog = resident_catalog.get_catalog()
    binding_calls, path_calls = [], []
    read_bindings = resident_workspace_storage.bindings_for_residents
    read_paths = resource_client.workspace_paths_many

    def bindings(connection, resident_ids):
        ids = list(resident_ids)
        binding_calls.append(ids)
        return read_bindings(connection, ids)

    def paths(workspace_ids, *, connection=None):
        ids = list(workspace_ids)
        path_calls.append(ids)
        return read_paths(ids, connection=connection)

    monkeypatch.setattr(resident_workspace_storage, "bindings_for_residents", bindings)
    monkeypatch.setattr(resource_client, "workspace_paths_many", paths)
    requested = [record["resident_id"] for record in records]
    loaded = catalog.get_many(requested)
    assert [record["resident_id"] for record in loaded] == requested
    assert len(binding_calls) == len(path_calls) == 1
    assert set(binding_calls[0]) == set(requested)
    assert set(path_calls[0]) == {
        binding["workspace_id"] for record in records for binding in [record["owned_workspace"], *record["workspace_bindings"]]
    }
    for record in loaded:
        assert resident_catalog.default_cwd_path(record) == record["default_cwd"]
    with catalog.state_store.database.read() as connection:
        columns = connection.execute(
            "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=%s "
            "AND table_name IN ('residents','resident_workspace_bindings')", (catalog.state_store.database.schema,),
        ).fetchall()
    assert not ({"path", "workspace_path", "workspace_root", "profile_json", "present_fields_json"}
                & {row["column_name"] for row in columns})


def test_list_keeps_resident_and_workspace_paths_in_one_snapshot(future_creation_store, tmp_path, monkeypatch):
    """读取 Resident 时身份与 Workspace 路径来自同一快照，并发提交的新值下次读取才可见"""
    old_code, new_code = tmp_path / "before-code", tmp_path / "after-code"
    old_code.mkdir()
    new_code.mkdir()
    payload = _new_session_payload("Workspace snapshot before")
    payload["workspace_paths"] = [str(old_code)]
    record = resident_domain.register_resident(payload, "sample_admin")
    resident_id = record["resident_id"]
    workspace_id = next(binding["workspace_id"] for binding in record["workspace_bindings"]
                        if binding["path"] == str(old_code))
    catalog = resident_catalog.get_catalog()
    workspaces = get_workspace_store()
    original_read_paths = resource_client.workspace_paths_many
    updated = []

    def commit_new_facts():
        try:
            def write(connection):
                connection.execute("UPDATE residents SET title=%s WHERE resident_id=%s",
                                   ("Workspace snapshot after", resident_id))
                workspaces.ensure(workspace_id, str(new_code), move=True, connection=connection)
            catalog.state_store.database.write(write)
            updated.append(True)
        except BaseException as error:
            updated.append(error)

    def paths_after_concurrent_commit(workspace_ids, *, connection=None):
        if not updated:
            worker = threading.Thread(target=commit_new_facts)
            worker.start()
            worker.join(timeout=5)
            assert not worker.is_alive(), "Concurrent test writer did not finish"
            assert updated == [True], updated
        return original_read_paths(workspace_ids, connection=connection)

    monkeypatch.setattr(resource_client, "workspace_paths_many", paths_after_concurrent_commit)
    loaded = catalog.get_many([resident_id])[0]
    assert loaded["title"] == "Workspace snapshot before"
    assert str(old_code) in loaded["workspace_paths"]
    later = catalog.get_many([resident_id])[0]
    assert later["title"] == "Workspace snapshot after"
    assert str(new_code) in later["workspace_paths"]


def test_member_move_preserves_workspace_bindings_v30(future_creation_store, monkeypatch):
    """v30 布局下成员迁往另一 Lineage 时先提交图，文件迁移失败后可重试完成"""
    from tests.cases.test_resident_folder_creation_contract import (
        test_member_move_commits_graphs_then_recovers_unlocked_file_transfer,
    )
    test_member_move_commits_graphs_then_recovers_unlocked_file_transfer(future_creation_store, monkeypatch)


def test_workspace_handoff_uses_same_workspace_identity_v30(future_creation_store):
    """v30 布局下 fork 出的 Resident 挂载来源 Workspace 时沿用同一 Workspace ID"""
    from tests.cases.test_resident_folder_creation_contract import (
        test_workspace_handoff_binding_records_the_same_workspace_in_target_markdown,
    )
    test_workspace_handoff_binding_records_the_same_workspace_in_target_markdown(future_creation_store)
