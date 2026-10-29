from __future__ import annotations
from tests.cases.agent_data_test_support import register_session, session_route_id
from management_portal.society_service import resident_backend
from management_portal.society_service import resident_catalog
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service.domain_ports import execution_port
from management_portal.society_service.resident_domain_common import MAX_TIMELINE_ITEMS
from management_portal.portal_access_control import get_portal_access_store

from tests.cases.society_service_test_support import install_resource_boundary

import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from management_portal.execution.agent_data_store import (
    get_agent_data_store,
)
from management_portal.society_service import (
    invariant_locks,
    lineage_domain,
    resident_domain,
    resident_lineage,
    resident_execution_work,
    resource_client,
)
from management_portal.society_service import resident_execution_domain
from management_portal.society_service import resident_fork_domain
from management_portal.society_service import resident_record_domain
from management_portal.society_service import resident_runtime_domain


REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_RESIDENT_TARGETS = lineage_domain._resident_targets


from management_portal.society_service import (
    project_domain,
    resident_access,
    resident_conversation_projection_domain,
    resident_domain_common,
    resident_input_queue_domain,
    resident_lifecycle_domain,
    resident_lifecycle_steps_domain,
    resident_registration_domain,
    resident_session_backend_domain,
    resident_workspace_domain,
)
from management_portal.society_service import lineage_aggregate
from management_portal.society_service import project_folders

@pytest.fixture()
def future_creation_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    lineage_root = tmp_path / "portal_lineages"
    lineage_root.mkdir()
    monkeypatch.setattr(lineage_aggregate, "DATA_ROOT", lineage_root)
    monkeypatch.setattr(lineage_domain, "LINEAGE_FOLDER_ROOT", lineage_root / "lineages")
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(lineage_root / "lineages"))
    # Deleted Residents' quota overrides are forgotten in the Execution store.
    monkeypatch.setattr(execution_port, "forget_binding_quota_overrides", lambda binding_ids: {"removed": 0})
    install_resource_boundary(monkeypatch, synthetic_residents=False)
    monkeypatch.setattr(resident_domain_common, "STAGING_ROOT", tmp_path)
    # A Resident created in a Project defaults to the Project directory as its workspace.
    project_root = tmp_path / "project_test"
    project_root.mkdir(exist_ok=True)
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _project_id: project_root)
    monkeypatch.setattr(project_domain, "PROJECTS_ROOT", tmp_path)
    return lineage_root


def _new_session_payload(title: str) -> dict:
    return {
        "title": title,
        "project_id": "project_test",
        "backend": "codex",
        "mode": "new_session",
    }


def _delete_member(resident_id: str, actor: str = "sample_admin") -> dict:
    """Delete one Lineage member the way DeleteResident does."""
    lineage_id = resident_catalog.get_catalog().get(resident_id)["lineage_id"]
    return lineage_domain.delete_lineage_graph_objects(
        lineage_id, {"resident_ids": [resident_id]}, actor, include_lineage=False,
    )


@pytest.mark.parametrize("member", [False, True])
def test_registration_creates_identity_without_synthetic_message(
    future_creation_store, monkeypatch, member,
):
    """注册 Resident 或添加 Lineage 成员只创建身份和图节点，不写入合成消息"""
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    parent = resident_domain.register_resident(_new_session_payload("Parent"), "sample_admin")
    payload = _new_session_payload("Registered without task")
    if member:
        created = lineage_domain.create_lineage_resident(parent["lineage_id"], payload, "sample_admin")
    else:
        created = resident_domain.register_resident(payload, "sample_admin")
    resident = created.get("resident") or created
    lineage = created.get("lineage") or lineage_domain.get_lineage(
        resident["lineage_id"], "sample_admin"
    )
    assert any(
        item["id"] == resident["resident_id"] for item in lineage["nodes"]
    )
    assert resident_conversation_stream.get_repository().read_messages(
        resident["resident_id"]
    ) == []


@pytest.mark.parametrize("member", [False, True])
def test_registration_does_not_hydrate_unrelated_residents(
    future_creation_store, monkeypatch, member,
):
    """注册 Resident 或添加成员时不加载全部 Resident"""
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    parent = resident_domain.register_resident(_new_session_payload("Existing parent"), "sample_admin")

    def unexpected_catalog_hydration():
        pytest.fail("Ordinary registration must not load all Residents")

    monkeypatch.setattr(resident_lineage, "all", unexpected_catalog_hydration)
    payload = _new_session_payload("Scoped registration")
    if member:
        created = lineage_domain.create_lineage_resident(parent["lineage_id"], payload, "sample_admin")["resident"]
    else:
        created = resident_domain.register_resident(payload, "sample_admin")
    assert created["resident_id"] != parent["resident_id"]
    assert resident_lineage.find(parent["resident_id"])["title"] == "Existing parent"


def test_registration_candidate_collision_preserves_existing_resident(
    future_creation_store, monkeypatch,
):
    """候选 Resident ID 与已有 Resident 冲突时改用新 ID，已有 Resident 不被覆盖"""
    parent = resident_domain.register_resident(_new_session_payload("Collision owner"), "sample_admin")
    fresh_id = resident_domain_common.new_resident_id()
    candidates = iter([parent["resident_id"], fresh_id])
    monkeypatch.setattr(resident_domain_common, "new_resident_id", lambda *args: next(candidates))
    created = resident_domain.register_resident(_new_session_payload("After collision"), "sample_admin")
    assert created["resident_id"] == fresh_id
    assert resident_lineage.find(parent["resident_id"])["title"] == "Collision owner"


@pytest.mark.parametrize("member", [False, True])
def test_slow_registration_preparation_does_not_block_other_residents(
    future_creation_store, monkeypatch, member,
):
    """一次注册的工作区准备很慢时不持有锁，其他 Resident 的注册照常完成"""
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    parent = resident_domain.register_resident(_new_session_payload("Parent"), "sample_admin")
    entered, release = threading.Event(), threading.Event()
    prepare = resident_workspace_domain.ensure_resident_workspace

    def slow(record):
        if record["title"] == "Slow preparation":
            assert not invariant_locks.lock_held(), "Preparation must not hold graph/catalog state locks"
            entered.set()
            assert release.wait(5)
        return prepare(record)

    monkeypatch.setattr(resident_workspace_domain, "ensure_resident_workspace", slow)

    def register(title):
        payload = _new_session_payload(title)
        if member:
            return lineage_domain.create_lineage_resident(parent["lineage_id"], payload, "sample_admin")["resident"]
        return resident_domain.register_resident(payload, "sample_admin")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(register, "Slow preparation")
        try:
            if not entered.wait(2):
                first.result(timeout=1)
                pytest.fail("Registration did not reach workspace preparation")
            second = pool.submit(register, "Independent preparation")
            assert second.result(timeout=2)["resident_id"]
        finally:
            release.set()
        assert first.result(timeout=3)["resident_id"]


def test_resident_registration_commits_different_lineages_concurrently(
    future_creation_store, monkeypatch,
):
    """不同 Lineage 的 Resident 注册可以并发提交"""
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    entered, release = threading.Event(), threading.Event()
    save = resident_record_domain.save_resident_record

    def slow_first_commit(record):
        if record["title"] == "Slow commit":
            assert invariant_locks.lock_held()
            entered.set()
            assert release.wait(5)
        return save(record)

    monkeypatch.setattr(resident_record_domain, "save_resident_record", slow_first_commit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            resident_domain.register_resident,
            _new_session_payload("Slow commit"),
            "sample_admin",
        )
        try:
            if not entered.wait(3):
                first.result(timeout=1)
                pytest.fail("First registration did not reach its commit")
            second = pool.submit(
                resident_domain.register_resident,
                _new_session_payload("Independent commit"),
                "sample_admin",
            )
            assert second.result(timeout=3)["resident_id"]
        finally:
            release.set()
        assert first.result(timeout=3)["resident_id"]


def test_hatg_registration_keeps_a_creation_committed_during_its_preparation(future_creation_store, monkeypatch):
    """准备期间提交的另一次创建不影响本次 Resident 注册

    验证 Resident 准备工作区期间同一 Lineage 提交了另一次创建时，本次注册照常提交，两个新节点都保留。

    Another Resident created while this one prepares does not fail the registration; both stay."""

    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    parent = resident_domain.register_resident(_new_session_payload("Parent"), "sample_admin")
    lineage_id = parent["lineage_id"]
    prepared = []
    prepare = resident_workspace_domain.ensure_resident_workspace

    def graph_changed_during_preparation(record):
        assert not invariant_locks.lock_held()
        prepare(record)
        if record["title"] == "Prepared creation":
            prepared.append(record["resident_id"])
            lineage_domain.create_lineage_resident(lineage_id, _new_session_payload("Concurrent node"), "sample_admin")

    monkeypatch.setattr(resident_workspace_domain, "ensure_resident_workspace", graph_changed_during_preparation)
    created = lineage_domain.create_lineage_resident(
        lineage_id, _new_session_payload("Prepared creation"), "sample_admin",
    )["resident"]
    assert prepared == [created["resident_id"]]
    assert resident_catalog.get_catalog().get(created["resident_id"])["lineage_id"] == lineage_id
    titles = {node["id"]: node["title"] for node in lineage_aggregate.read_lineage(lineage_id, actor="sample_admin")["nodes"]}
    assert created["resident_id"] in titles
    assert "Concurrent node" in titles.values()
    assert titles[parent["resident_id"]] == "Parent"


def test_member_move_commits_graphs_then_recovers_unlocked_file_transfer(future_creation_store, monkeypatch):
    """成员迁往另一 Lineage 时先提交两张图，锁外文件迁移失败后可重试完成"""
    from management_portal.society_service import resident_workspace_transfer

    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    source = resident_domain.register_resident(_new_session_payload("Move source"), "sample_admin")
    destination = resident_domain.register_resident(_new_session_payload("Move destination"), "sample_admin")
    member = lineage_domain.create_lineage_resident(
        source["lineage_id"], _new_session_payload("Moving member"), "sample_admin",
    )["resident"]
    resident_id = member["resident_id"]
    original = resident_record_domain.resident_folder_path(member)
    (original / "keep.txt").write_text("preserve", encoding="utf-8")
    result = resident_domain.update_resident(resident_id, "sample_admin", {
        "target_lineage_id": destination["lineage_id"],
    })
    assert result["workspace_pending"] is True
    assert original.exists()
    moved = resident_record_domain.find_resident(resident_id)
    target = resident_record_domain.resident_folder_path(moved)
    assert not target.exists()
    assert moved["lineage_id"] == destination["lineage_id"]
    old_graph = lineage_aggregate.lineage_catalog_store().get(source["lineage_id"])
    new_graph = lineage_aggregate.lineage_catalog_store().get(destination["lineage_id"])
    assert resident_id not in {item["id"] for item in old_graph["nodes"]}
    assert resident_id in {item["id"] for item in new_graph["nodes"]}
    operations = resident_record_domain.lifecycle_operations()
    relocate = resident_workspace_transfer.relocate

    def fail(*args):
        assert not invariant_locks.lock_held()
        raise OSError("temporary filesystem failure")

    monkeypatch.setattr(resident_workspace_transfer, "relocate", fail)
    assert not operations.run(result["operation_id"], resident_lifecycle_steps_domain.apply_resident_lifecycle_step)
    assert operations.pending(resident_id)
    assert original.exists()
    assert resident_lifecycle_domain.resident_has_pending_work(resident_id)
    monkeypatch.setattr(resident_workspace_transfer, "relocate", relocate)
    operations.store.database.write(lambda c: c.execute(
        'UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s', (result["operation_id"],),
    ))
    assert operations.run(result["operation_id"], resident_lifecycle_steps_domain.apply_resident_lifecycle_step) == resident_id, operations.health()
    assert not original.exists()
    assert (target / "keep.txt").read_text() == "preserve"
    assert not operations.pending(resident_id)


@pytest.mark.parametrize("project_id", ["project_test", "project_next", ""])
def test_project_edit_does_not_move_the_lineage_owned_folder(future_creation_store, monkeypatch, project_id):
    """修改 Resident 的 project_id 不移动 Lineage 文件夹，改成其他 Project 被拒绝"""
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    record = resident_domain.register_resident(_new_session_payload("Project ownership"), "sample_admin")
    original = record["workspace_path"]
    if project_id == record["project_id"]:
        changed = resident_domain.update_resident(record["resident_id"], "sample_admin", {"project_id": project_id})
        assert changed["workspace_path"] == original
        assert changed["project_id"] == record["project_id"]
    else:
        with pytest.raises(ValueError, match="由 Lineage 派生"):
            resident_domain.update_resident(record["resident_id"], "sample_admin", {"project_id": project_id})
    assert Path(original).is_dir()
    reloaded = resident_record_domain.find_resident(record["resident_id"])
    assert reloaded["workspace_path"] == original
    assert reloaded["project_id"] == record["project_id"]
    assert lineage_domain.get_lineage(record["lineage_id"], "sample_admin")["project_id"] == record["project_id"]


def test_reset_commits_intent_before_archiving_conversation(future_creation_store, monkeypatch):
    """重置上下文先提交重置意图，对话归档由生命周期操作随后完成"""
    record = resident_domain.register_resident(_new_session_payload("Reset boundary"), "sample_admin")
    resident_id = record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    old_message = resident_input_queue_domain.resident_user_message(
        resident_id, "before reset", "sample_admin", message_id="msg_11111111111111111111111111111111",
    )
    repository.commit_user_message(resident_id, old_message)
    operations = resident_record_domain.lifecycle_operations()
    enqueue = operations.enqueue

    def failed_enqueue(*args, **kwargs):
        raise RuntimeError("reset intent could not commit")

    monkeypatch.setattr(operations, "enqueue", failed_enqueue)
    monkeypatch.setattr(resident_record_domain, "lifecycle_operations", lambda: operations)
    with pytest.raises(RuntimeError, match="reset intent could not commit"):
        resident_domain.reset_resident_context(resident_id, "sample_admin")
    assert old_message["message_id"] in repository.context_reset_boundary(resident_id)
    monkeypatch.setattr(operations, "enqueue", enqueue)
    result = resident_domain.reset_resident_context(resident_id, "sample_admin")
    assert result["cleanup_pending"]
    assert operations.pending(resident_id)
    # Acceptance never performs the archive synchronously under the state lock.
    assert old_message["message_id"] in repository.context_reset_boundary(resident_id)
    new_message = resident_input_queue_domain.resident_user_message(
        resident_id, "after reset", "sample_admin", message_id="msg_22222222222222222222222222222222",
    )
    repository.commit_user_message(resident_id, new_message)
    assert operations.run(result["operation_id"], resident_lifecycle_steps_domain.apply_resident_lifecycle_step) == resident_id, operations.health()
    assert repository.context_reset_boundary(resident_id) == [new_message["message_id"]]


def test_registration_failure_rolls_back_graph_and_identity(future_creation_store, monkeypatch):
    """注册时身份提交失败会回滚新建的 Lineage 和 Resident 目录"""
    before = resident_catalog.get_catalog().snapshot()
    lineage_ids = []
    initialize = lineage_domain.initialize_resident_lineage

    def capture(record, actor, **options):
        lineage_ids.append(record["lineage_id"])
        return initialize(record, actor, **options)

    def fail(record):
        raise RuntimeError("identity commit failed")

    monkeypatch.setattr(lineage_domain, "initialize_resident_lineage", capture)
    monkeypatch.setattr(resident_record_domain, "save_resident_record", fail)
    with pytest.raises(RuntimeError, match="identity commit failed"):
        resident_domain.register_resident(_new_session_payload("Rollback registration"), "sample_admin")
    assert resident_catalog.get_catalog().snapshot() == before
    assert len(lineage_ids) == 1
    with pytest.raises(KeyError):
        lineage_aggregate.lineage_catalog_store().get(lineage_ids[0])


def test_failed_registration_archives_only_new_resident_folder(future_creation_store, monkeypatch):
    """成员注册失败后只归档新 Resident 的文件夹，已有成员的文件保留"""
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    parent = resident_domain.register_resident(_new_session_payload("Kept parent"), "sample_admin")
    parent_folder = resident_record_domain.resident_folder_path(parent)
    keep = parent_folder / "user-file.txt"
    keep.write_text("existing data", encoding="utf-8")
    created = []
    prepare = resident_workspace_domain.ensure_resident_workspace

    def interrupted(record):
        prepare(record)
        folder = resident_record_domain.resident_folder_path(record)
        (folder / "partial.txt").write_text("partial preparation", encoding="utf-8")
        created.append(folder)
        raise RuntimeError("preparation interrupted")

    monkeypatch.setattr(resident_workspace_domain, "ensure_resident_workspace", interrupted)
    with pytest.raises(RuntimeError, match="preparation interrupted"):
        lineage_domain.create_lineage_resident(
            parent["lineage_id"], _new_session_payload("Unpublished member"), "sample_admin",
        )
    assert created[0].exists(), "Failure must leave recovery evidence, not rmtree"
    operations = resident_record_domain.lifecycle_operations()
    keys = operations.ready()
    assert len(keys) == 1
    assert operations.run(keys[0], resident_lifecycle_steps_domain.apply_resident_lifecycle_step)
    assert not created[0].exists()
    archives = list(created[0].parent.glob(f".aborted-{created[0].name}-*"))
    assert len(archives) == 1
    assert (archives[0] / "partial.txt").read_text(encoding="utf-8") == "partial preparation"
    assert keep.read_text(encoding="utf-8") == "existing data"
    assert resident_catalog.get_catalog().get(parent["resident_id"])


def _add_created_resident_to_lineage(
    root: dict,
    created_resident_id: str,
    actor: str = "sample_admin",
) -> None:
    created_resident_record = resident_catalog.get_catalog().get(created_resident_id)
    created_resident_record["workspace_path"] = root["workspace_path"]
    updated = lineage_domain.add_registered_resident_node(
        created_resident_record,
        actor,
    )
    assert updated is not None


def _append_conversation_turn(
    resident_id: str,
    index: int,
) -> None:
    store = get_agent_data_store()
    record = resident_record_domain.find_resident(resident_id)
    if not resident_backend.record_session_id(record):
        record = _activate_created_resident(resident_id)
    session = store.ensure_provider_session(
        provider='codex', backend_route_id=session_route_id(record),
        backend_session_id=resident_backend.record_session_id(record),
        agent_id=resident_id, actor_ref='sample_admin',
    )
    execution_id = f'execution-fixture-{resident_id}-{index}'
    turn_id = f'01a00000-0000-7000-8000-{index + 1:012d}'
    store.begin_execution(execution_id=execution_id, agent_id=resident_id,
                          actor_ref='sample_admin', provider='codex', started_at=f'2026-08-20T00:{index:02d}:00Z')
    store.bind_execution_provider_session(
        execution_id,
        provider_session_ref=session['provider_session_ref'],
    )
    user_message_id = _context_user_message_id(index)
    accepted = store.display_chat_message(resident_id, {
        "execution_id": execution_id, "provider_turn_id": turn_id,
        "message_id": user_message_id, "role": "user", "author_id": "sample_admin",
        "content_parts": [{"type": "text", "text": f"question {index}"}],
        "created_at": f"2026-08-20T00:{index:02d}:00Z",
    })
    answer = {
        "execution_id": execution_id, "provider_turn_id": turn_id,
        "message_id": f"msg_{index + 10_000:032x}", "role": "agent",
        "content_parts": [{"type": "text", "text": f"answer {index}"}],
        "reply_to_message_id": user_message_id,
        "created_at": f"2026-08-20T00:{index:02d}:30Z",
    }
    from management_portal.execution.message_projection import message_facts
    answer_segment = store.put_segment(segment_type="model", content=f"answer {index}",
        execution_id=execution_id, created_by_agent_id=resident_id,
        metadata={"role": "assistant", "kind": "final_answer", "message": message_facts(answer)})
    store.append_agent_segments(resident_id, [answer_segment], actor_ref="sample_admin")
    store.finish_execution(execution_id, status='completed', provider_turn_id=turn_id)


def _context_user_message_id(index: int) -> str:
    return f"msg_{index + 1:032x}"


def _activate_created_resident(resident_id: str) -> dict:
    record = resident_record_domain.find_resident(resident_id)
    assert record is not None
    register_session(
        record,
        f"01a00000-0000-7000-8000-{resident_id[-12:]}",
        backend_route_id="zero",
    )
    activated = resident_record_domain.find_resident(resident_id)
    assert activated is not None
    return activated


def test_fork_5_history_reaches_the_created_residents_next_work(
    future_creation_store: Path,
) -> None:
    """fork 最近 5 轮时，新 Resident 下一次工作的 prompt 只含最近 5 轮问答"""
    del future_creation_store
    source = resident_domain.register_resident(
        _new_session_payload("Recent context source"),
        "sample_admin",
    )
    for index in range(6):
        _append_conversation_turn(source["resident_id"], index)

    result = resident_domain.create_resident_context_fork(
        source["resident_id"],
        "sample_admin",
        5,
    )
    created = _activate_created_resident(result["resident_id"])
    prompt = _trace_prompt(created, {
        "actor": "sample_admin",
        "message": "perform the next task",
    })

    assert created["fork_mode"] == "recent-5"
    assert "<resident_trace_context>" in prompt
    assert "question 0" not in prompt
    assert "answer 0" not in prompt
    for index in range(1, 6):
        assert f"question {index}" in prompt
        assert f"answer {index}" in prompt
    assert "perform the next task" in prompt


def test_fork_from_here_provider_history_reaches_the_created_residents_next_work(
    future_creation_store: Path,
) -> None:
    """从某条消息 fork 时，新 Resident 下一次工作的 prompt 只含该消息及之前的问答"""
    del future_creation_store
    source = resident_domain.register_resident(
        _new_session_payload("Message context source"),
        "sample_admin",
    )
    for index in range(3):
        _append_conversation_turn(source["resident_id"], index)

    result = resident_domain.create_resident_fork_from_message(
        source["resident_id"],
        "sample_admin",
        {"message_id": _context_user_message_id(1)},
    )
    created = _activate_created_resident(result["resident_id"])
    prompt = _trace_prompt(created, {
        "actor": "sample_admin",
        "message": "continue from the selected point",
    })

    assert created["fork_mode"] == "message"
    assert "<resident_trace_context>" in prompt
    assert "question 0" in prompt
    assert "answer 0" in prompt
    assert "question 1" in prompt
    assert "answer 1" in prompt
    assert "question 2" not in prompt
    assert "answer 2" not in prompt
    assert "continue from the selected point" in prompt




def test_registering_existing_session_preserves_context_metadata(
    future_creation_store: Path,
) -> None:
    """重复登记同一 Provider 会话时保留上下文注入元数据"""
    del future_creation_store
    record = resident_domain.register_resident(
        _new_session_payload("Provider binding context"),
        "sample_admin",
    )
    backend_session_id = "019e63b0-031d-7913-9dc8-0d972899bed4"
    register_session(
        record,
        backend_session_id,
        backend_route_id="zero",
    )
    store = get_agent_data_store()
    binding_id = resident_backend.execution_binding_id(record)
    binding = store.provider_binding(binding_id)
    store.bind_provider_session(
        binding_id,
        str(binding["provider_session_ref"]),
        context_fingerprint="sha256:resident-context",
        context_injected_at="2026-08-24T01:02:03Z",
        context_injection_count=3,
        harness_tool_names=("apply_patch", "exec_command"),
    )

    register_session(
        record,
        backend_session_id,
        backend_route_id="zero",
    )

    preserved = store.provider_binding(binding_id)
    assert preserved["context_fingerprint"] == "sha256:resident-context"
    assert preserved["context_injected_at"] == "2026-08-24T01:02:03Z"
    assert preserved["context_injection_count"] == 3
    assert preserved["harness_tool_names"] == (
        "apply_patch",
        "exec_command",
    )


def test_resident_lineage_initialization_preserves_project(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """初始化 Resident 的 Lineage 时保留其 Project"""
    project_root = future_creation_store / "projects" / "RoboLab"
    project_root.mkdir(parents=True)
    monkeypatch.setattr(
        resource_client,
        "project_roots",
        lambda **_kwargs: {"RoboLab": project_root},
    )
    persisted: list[dict] = []
    monkeypatch.setattr(
        lineage_domain,
        "_persist_lineage_state",
        lambda _previous, lineage, _reason: persisted.append(lineage),
    )

    lineage = lineage_domain.initialize_resident_lineage(
        {
            "resident_id": "resident_123456789abc",
            "lineage_id": "lineage_123456789abc",
            "title": "Project Bud",
            "project_id": "RoboLab",
            "lineage_project_id": "RoboLab",
            "backend": "codex",
        },
        "sample_admin",
    )

    assert lineage["project_id"] == "RoboLab"
    assert persisted[0]["project_id"] == "RoboLab"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("codex_thread_id", "thread-forbidden"),
        ("backend_session_id", "session-forbidden"),
        ("session_id", "session-forbidden"),
    ),
)
def test_resident_registration_rejects_backend_session_identity(
    future_creation_store: Path,
    field: str,
    value: str,
) -> None:
    """注册 Resident 时拒绝 payload 中的后端会话 ID 字段"""
    del future_creation_store
    payload = _new_session_payload("Forbidden Session identity")
    payload[field] = value

    with pytest.raises(ValueError, match="does not accept backend Session IDs"):
        resident_domain.register_resident(payload, "sample_admin")


def test_new_resident_creates_one_complete_canonical_lineage(
    future_creation_store: Path,
) -> None:
    """新建 Resident 生成完整的 Lineage 文件夹，自身是唯一节点和 Leader"""
    lineage_root = future_creation_store

    record = resident_domain.register_resident(
        _new_session_payload("Folder contract root"),
        "sample_admin",
    )

    lineage_folder = lineage_root / "lineages" / record["lineage_id"]
    resident_folder = lineage_folder / "residents" / record["resident_id"]
    owned_workspace = resident_folder / "workspace"
    lineage = lineage_aggregate.lineage_catalog_store().get(record["lineage_id"])

    assert Path(record["workspace_path"]) == lineage_folder
    assert "lineage_storage" not in record
    assert Path(record["default_cwd"]) == owned_workspace
    assert {item.name for item in lineage_folder.iterdir()} >= {
        "LINEAGE.md",
        "README.md",
        "residents",
    }
    assert {item.name for item in resident_folder.iterdir()} >= {
        "diary.md",
        "mailbox.md",
        "resident.md",
        "runtime",
        "workspace",
        "workspaces.md",
    }
    assert owned_workspace.is_dir()
    assert lineage["leader_resident_id"] == record["resident_id"]
    assert [node["id"] for node in lineage["nodes"]] == [record["resident_id"]]


def test_new_resident_lineage_uses_the_registration_transaction_identity(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """注册 Resident 时先规范化 Lineage 图，再在同一事务中提交 Resident 身份"""
    observed_catalog_state: list[bool] = []
    normalize = lineage_domain._normalize_lineage_payload

    def capture_registration_order(payload, previous, actor, **options):
        resident_id = str((payload.get("nodes") or [{}])[0].get("resident_id") or "")
        observed_catalog_state.append(
            resident_id in {
                str(item.get("resident_id") or "")
                for item in resident_catalog.get_catalog().snapshot()
            }
        )
        return normalize(payload, previous, actor, **options)

    monkeypatch.setattr(
        lineage_domain,
        "_normalize_lineage_payload",
        capture_registration_order,
    )

    record = resident_domain.register_resident(
        _new_session_payload("Atomic registration identity"),
        "sample_admin",
    )

    assert observed_catalog_state == [False]
    assert resident_catalog.get_catalog().get(record["resident_id"])[
        "resident_id"
    ] == record["resident_id"]


def test_lineage_project_move_reprojects_resident_and_workspace_resources(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lineage 换 Project 后，Resident 与 Workspace 资源按新 Project 重新投影

    :design: decision_3e07c6d35cac"""
    published = []
    sync_bundles = resource_client.sync_resident_resource_bundles

    def capture_bundles(records, **kwargs):
        records = list(records)
        published.extend(json.loads(json.dumps(records)))
        return sync_bundles(records, **kwargs)

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", capture_bundles)
    from management_portal.society_service.resident_resource_projection import ResidentResourceProjectionWorker

    store = resident_catalog.get_catalog().state_store
    worker = ResidentResourceProjectionWorker(store, resource_client)
    project_root = future_creation_store / "projects" / "OSForAgent"
    second_project_root = future_creation_store / "projects" / "RoboLab"
    project_root.mkdir(parents=True)
    second_project_root.mkdir(parents=True)
    monkeypatch.setattr(
        project_domain.project_registry,
        "load",
        lambda: {"projects": [{"id": "OSForAgent", "path": str(project_root)}, {"id": "RoboLab", "path": str(second_project_root)}]},
    )
    roots = {"OSForAgent": project_root, "RoboLab": second_project_root}
    monkeypatch.setattr(resource_client, "project_roots", lambda **kwargs: roots)
    # The move resolves these Projects; registration keeps the fixture's Project.
    fixture_root = project_folders.project_root_for_id
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda project_id: roots.get(project_id) or fixture_root(project_id))
    payload = _new_session_payload("System projection root")
    record = resident_domain.register_resident(payload, "sample_admin")
    lineage_id = record["lineage_id"]
    resident_id = record["resident_id"]
    canonical = lineage_domain._lineage_dir(lineage_id)
    stored = resident_catalog.get_catalog().get(resident_id)

    moved = lineage_domain.update_lineage_metadata(
        lineage_id,
        {
            "expected_revision": 1,
            "project_id": "OSForAgent",
        },
        "sample_admin",
    )
    project_folder = (
        project_root / "knowledge_base" / "agents" / lineage_id
    )
    projected = resident_catalog.get_catalog().get(resident_id)
    owned_workspace = canonical / "residents" / resident_id / "workspace"

    assert moved["project_id"] == "OSForAgent"
    assert canonical.is_symlink()
    assert canonical.resolve() == project_folder.resolve()
    assert "kind" not in projected and "lineage_kind" not in projected
    assert projected["project_id"] == "OSForAgent"
    assert projected["lineage_project_id"] == "OSForAgent"
    assert Path(projected["workspace_path"]) == canonical.resolve()
    assert Path(projected["default_cwd"]) == owned_workspace.resolve()
    assert Path(projected["owned_workspace"]["path"]) == owned_workspace.resolve()
    assert projected["owned_workspace_id"] == stored["owned_workspace_id"]
    assert resident_catalog.default_cwd_path(projected)
    assert resource_client.workspace_paths_many([projected["owned_workspace_id"]]) == {
        projected["owned_workspace_id"]: str(owned_workspace.resolve()),
    }
    assert store.resident_resource_projection_pending(resident_id)
    assert worker.drain_once() == 1
    queued = next(item for item in reversed(published) if item["resident_id"] == resident_id)
    assert queued["project_id"] == "OSForAgent"
    assert str(owned_workspace.resolve()) in {
        str(binding.get("path") or "")
        for binding in [queued["owned_workspace"], *queued.get("workspace_bindings", [])]
    }

    remapped = lineage_domain.update_lineage_metadata(
        lineage_id,
        {
            "expected_revision": moved["revision"],
            "project_id": "RoboLab",
        },
        "sample_admin",
    )
    remapped_record = resident_catalog.get_catalog().get(resident_id)
    second_project_folder = (
        second_project_root / "knowledge_base" / "agents" / lineage_id
    )

    assert remapped["project_id"] == "RoboLab"
    assert canonical.resolve() == second_project_folder.resolve()
    assert Path(remapped_record["workspace_path"]) == canonical.resolve()
    assert Path(remapped_record["default_cwd"]) == owned_workspace.resolve()
    assert Path(remapped_record["owned_workspace"]["path"]) == owned_workspace.resolve()
    assert remapped_record["owned_workspace_id"] == stored["owned_workspace_id"]
    assert resource_client.workspace_paths_many([remapped_record["owned_workspace_id"]]) == {
        remapped_record["owned_workspace_id"]: str(owned_workspace.resolve()),
    }
    assert resident_catalog.default_cwd_path(remapped_record)
    assert remapped_record["owned_workspace_id"]
    assert store.resident_resource_projection_pending(resident_id)
    assert worker.drain_once() == 1
    queued = next(item for item in reversed(published) if item["resident_id"] == resident_id)
    assert queued["project_id"] == "RoboLab"
    assert str(owned_workspace.resolve()) in {
        str(binding.get("path") or "")
        for binding in [queued["owned_workspace"], *queued.get("workspace_bindings", [])]
    }

def test_leader_resident_rename_preserves_lineage_identity_and_refreshes_hatg(
    future_creation_store: Path,
) -> None:
    """Leader Resident 改名保留 Lineage 与 Workspace 绑定，HATG 图节点显示新名称"""
    record = resident_domain.register_resident(
        _new_session_payload("Before rename"),
        "sample_admin",
    )
    lineage_id = record["lineage_id"]
    resident_id = record["resident_id"]
    # Prime the Lineage read cache before changing the Resident projection.
    initial = lineage_domain.get_lineage(lineage_id, "sample_admin")
    assert initial["nodes"][0]["title"] == "Before rename"

    development_workspace = future_creation_store / "development-workspace"
    reference_workspace = future_creation_store / "reference-workspace"
    development_workspace.mkdir()
    reference_workspace.mkdir()
    stored = resident_catalog.get_catalog().get(resident_id)
    stored["workspace_bindings"] = []
    resident_workspace_domain.set_resident_reference_bindings(stored, [str(development_workspace), str(reference_workspace)])
    resident_record_domain.save_resident_record(stored)
    bindings_before = resident_catalog.get_catalog().get(resident_id)["workspace_bindings"]

    updated = resident_domain.update_resident(
        resident_id,
        "sample_admin",
        {"title": "After rename"},
    )

    assert updated["resident_id"] == resident_id
    assert updated["lineage_id"] == lineage_id
    assert str(development_workspace) in updated["workspace_paths"]
    assert set(updated["workspace_paths"]) == {str(reference_workspace), str(development_workspace)}
    assert updated["workspace_bindings"] == bindings_before
    assert resident_catalog.get_catalog().get(resident_id)["workspace_bindings"] == bindings_before
    assert [
        item["lineage_id"]
        for item in lineage_domain.list_lineages("sample_admin")["lineages"]
    ] == [lineage_id]

    refreshed = lineage_domain.get_lineage(lineage_id, "sample_admin")
    resident_node = next(
        item
        for item in refreshed["nodes"]
        if item["id"] == resident_id
    )
    assert resident_node["title"] == "After rename"
    persisted_lineage = lineage_aggregate.lineage_catalog_store().get(lineage_id)
    persisted_resident_node = next(
        item
        for item in persisted_lineage["nodes"]
        if item["id"] == resident_id
    )
    assert persisted_resident_node["title"] == "After rename"


def _rename(name, record, title, *, key, version=None, caller="sample_admin", entry="web"):
    from tests.cases.isa_test_support import run

    if name == "UpdateResident":
        arguments = {"lineage_id": record["lineage_id"], "resident_id": record["resident_id"], "title": title}
    else:
        arguments = {"lineage_id": record["lineage_id"], "changes": {"title": title}}
    return run(name, arguments, actor=caller, key=key, version=version, entry=entry)


@pytest.mark.parametrize("name", ["UpdateResident", "UpdateLineage"])
@pytest.mark.parametrize("entry", ["web", "harness"])
def test_rename_instructions_update_canonical_title_and_replay(future_creation_store, name, entry):
    """UpdateResident 改 Resident 名，UpdateLineage 改 Lineage 名，重放无变化"""
    from tests.cases.isa_test_support import reset_kernels

    reset_kernels()
    record = resident_domain.register_resident(_new_session_payload("Original name"), "sample_admin")
    resident_id, lineage_id = record["resident_id"], record["lineage_id"]
    before = lineage_domain.get_lineage(lineage_id, "sample_admin")
    caller = resident_id if entry == "harness" else "sample_admin"
    result = _rename(name, record, "Renamed identity", key="rename-once",
                     version={lineage_id: before["revision"]}, caller=caller, entry=entry)
    assert result.status == "committed", result.error
    after = lineage_domain.get_lineage(lineage_id, "sample_admin")
    stored = resident_catalog.get_catalog().get(resident_id)
    node = next(node for node in after["nodes"] if node["id"] == resident_id)
    assert after["revision"] == before["revision"] + (name == "UpdateLineage")
    assert after["leader_resident_id"] == before["leader_resident_id"]
    assert after["project_id"] == before["project_id"]
    if name == "UpdateResident":
        assert result.result["resident"]["title"] == "Renamed identity"
        assert stored["title"] == node["title"] == node["resident_title"] == "Renamed identity"
        assert stored["title_origin"] == "custom"
        assert after["title"] == before["title"]
    else:
        assert after["title"] == "Renamed identity"
        assert stored["title"] == record["title"]
    replay = _rename(name, record, "Renamed identity", key="rename-once",
                     version={lineage_id: before["revision"]}, caller=caller, entry=entry)
    assert replay.replayed is True
    assert lineage_domain.get_lineage(lineage_id, "sample_admin")["revision"] == after["revision"]


def test_rename_is_refused_when_the_authorization_system_denies_the_target(future_creation_store, monkeypatch):
    """权限系统拒绝目标时 UpdateResident 改名失败，标题不变

    权限系统开启时，Resident 只能管理自己所在 Project 的 Lineage；给另一个 Project 的 Lineage 中的 Resident 改名返回 instruction.forbidden，标题不变。

    :design: decision_b820faf42218
    """
    from tests.cases import permission_test_support
    from tests.cases.isa_test_support import reset_kernels
    from tests.cases.permission_test_support import Fact

    reset_kernels()
    caller = resident_domain.register_resident(_new_session_payload("Caller"), "sample_admin")
    other = resident_domain.register_resident(_new_session_payload("Other lineage"), "sample_admin")
    assert caller["lineage_id"] != other["lineage_id"]
    permissions = permission_test_support.enable(monkeypatch)
    for project, record in (("CallerProject", caller), ("OtherProject", other)):
        permissions.place(Fact("project", project), Fact("lineage", record["lineage_id"], {"project": project}),
                          Fact("resident", record["resident_id"], {"lineage": record["lineage_id"]}))
    result = _rename("UpdateResident", other, "Wrong target", key="wrong-target",
                     caller=caller["resident_id"], entry="harness")
    assert result.status == "failed"
    assert result.error["code"] == "instruction.forbidden"
    assert resident_catalog.get_catalog().get(other["resident_id"])["title"] == "Other lineage"
    own = _rename("UpdateResident", caller, "Own lineage", key="own-target",
                  caller=caller["resident_id"], entry="harness")
    assert own.status == "committed", own.error


def test_a_resident_can_rename_another_member_of_its_lineage(future_creation_store):
    """Resident 可以用 UpdateResident 给同一 Lineage 的其他成员改名"""
    from tests.cases.isa_test_support import reset_kernels

    reset_kernels()
    caller = resident_domain.register_resident(_new_session_payload("Caller"), "sample_admin")
    member = lineage_domain.create_lineage_resident(
        caller["lineage_id"], _new_session_payload("Member"), "sample_admin",
    )["resident"]
    result = _rename("UpdateResident", member, "Renamed member", key="rename-member",
                     caller=caller["resident_id"], entry="harness")
    assert result.status == "committed", result.error
    assert result.result["resident"]["title"] == "Renamed member"
    assert resident_catalog.get_catalog().get(caller["resident_id"])["title"] == "Caller"


def test_rename_rolls_back_when_identity_commit_fails(future_creation_store, monkeypatch):
    """UpdateResident 改名时身份提交失败，Resident 标题和 Lineage 图都回滚"""
    from tests.cases.isa_test_support import reset_kernels

    reset_kernels()
    record = resident_domain.register_resident(_new_session_payload("Keep both titles"), "sample_admin")
    catalog = lineage_aggregate.lineage_catalog_store()
    before = catalog.get(record["lineage_id"])
    store = resident_catalog.get_catalog()
    upsert = store.upsert

    def fail_after_write(definition, **kwargs):
        upsert(definition, **kwargs)
        raise RuntimeError("rename projection failed")

    monkeypatch.setattr(store, "upsert", fail_after_write)
    result = _rename("UpdateResident", record, "Rolled back", key="failed-rename",
                     version={record["lineage_id"]: before["revision"]})
    assert result.status == "failed" and "rename projection failed" in result.error["message"]
    assert resident_catalog.get_catalog().get(record["resident_id"])["title"] == "Keep both titles"
    after = catalog.get(record["lineage_id"])
    assert after["revision"] == before["revision"]
    assert after["nodes"] == before["nodes"]


def test_resident_rename_rolls_back_when_projection_callback_fails(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """改名时 Lineage 标题投影失败，Resident 标题和 Lineage revision 保持原样"""
    record = resident_domain.register_resident(
        _new_session_payload("Atomic rename source"),
        "sample_admin",
    )
    resident_id = str(record["resident_id"])
    lineage_id = str(record["lineage_id"])
    catalog = lineage_aggregate.lineage_catalog_store()
    previous_lineage = catalog.get(lineage_id)
    previous_revision = int(previous_lineage["revision"])
    from management_portal.society_service.domain_ports import resident_lineage_host_port

    def projection_failure(*args, **kwargs) -> None:
        raise RuntimeError("injected lineage write failure")

    monkeypatch.setattr(resident_lineage_host_port, "commit_resident_title_projection", projection_failure)

    with pytest.raises(RuntimeError, match="injected lineage write failure"):
        resident_domain.rename_resident_title(
            resident_id,
            "sample_admin",
            "Atomic rename target",
        )

    persisted_resident = resident_catalog.get_catalog().get(
        resident_id
    )
    persisted_lineage = catalog.get(lineage_id)
    assert persisted_resident["title"] == "Atomic rename source"
    assert int(persisted_lineage["revision"]) == previous_revision
    assert next(
        node
        for node in persisted_lineage["nodes"]
        if node.get("id") == resident_id
    )["title"] == "Atomic rename source"


def test_lineage_originated_resident_rename_syncs_resource_identity(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 改名后资源投影队列中的 Resident 身份带新标题"""
    record = resident_domain.register_resident(
        _new_session_payload("Lineage rename source"),
        "sample_admin",
    )
    renamed = resident_domain.rename_resident_title(
        record["resident_id"],
        "sample_admin",
        "Lineage rename target",
    )

    assert renamed["lineage_id"] == record["lineage_id"]
    claims = resident_catalog.get_catalog().state_store.claim_resident_resource_projections()
    projected = next(
        item["payload"]
        for item in claims
        if item["resident_id"] == record["resident_id"]
    )
    assert projected["title"] == "Lineage rename target"
    assert projected["lineage_id"] == record["lineage_id"]


def test_lineage_originated_rename_updates_the_exact_resident_fact(
    future_creation_store: Path,
) -> None:
    """给 fork 出的 Resident 改名只更新该 Resident 与其图节点的标题，来源记为 custom"""
    record = resident_domain.register_resident(
        _new_session_payload("Fork rename source"),
        "sample_admin",
    )
    _append_conversation_turn(record['resident_id'], 0)
    detail = resident_domain.create_resident_full_context_fork(
        record["resident_id"],
        "sample_admin",
        "lineage:test:rename-fork",
        defer_backend_start=True,
    )
    created_resident_id = detail["resident_id"]
    _add_created_resident_to_lineage(record, created_resident_id)
    resident_domain.rename_resident_title(
        created_resident_id,
        "sample_admin",
        "Fork 1 2026-08-02",
    )

    renamed = resident_catalog.get_catalog().get(created_resident_id)
    assert renamed["resident_id"] == created_resident_id
    assert renamed["title"] == "Fork 1 2026-08-02"
    assert renamed["title_origin"] == "custom"
    persisted_lineage = lineage_aggregate.lineage_catalog_store().get(
        record["lineage_id"]
    )
    persisted_node = next(
        node
        for node in persisted_lineage["nodes"]
        if node.get("id") == created_resident_id
    )
    assert persisted_node["title"] == "Fork 1 2026-08-02"


def test_hatg_graph_rename_updates_the_exact_resident_title(
    future_creation_store: Path,
) -> None:
    """在 HATG 图中修改节点标题会更新对应 Resident 的标题"""
    record = resident_domain.register_resident(
        _new_session_payload("Graph rename source"),
        "sample_admin",
    )
    _append_conversation_turn(record['resident_id'], 0)
    detail = resident_domain.create_resident_full_context_fork(
        record["resident_id"],
        "sample_admin",
        "lineage:test:graph-rename-fork",
        defer_backend_start=True,
    )
    created_resident_id = detail["resident_id"]
    _add_created_resident_to_lineage(record, created_resident_id)

    current = lineage_domain.get_lineage(record["lineage_id"], "sample_admin")
    child = next(
        node for node in current["nodes"] if node.get("id") == created_resident_id
    )
    child["title"] = "Evidence review"
    updated = lineage_domain.update_lineage(
        record["lineage_id"],
        {
            "lineage": current,
            "expected_revision": current["revision"],
        },
        "sample_admin",
    )

    canonical = resident_catalog.get_catalog().get(created_resident_id)
    assert canonical["title"] == "Evidence review"
    assert canonical["title_origin"] == "custom"
    assert next(
        node
        for node in updated["nodes"]
        if node.get("id") == created_resident_id
    )["title"] == "Evidence review"

    reloaded = lineage_domain.get_lineage(record["lineage_id"], "sample_admin")
    assert next(
        node
        for node in reloaded["nodes"]
        if node.get("id") == created_resident_id
    )["title"] == "Evidence review"


def test_hatg_rename_rolls_back_resident_when_graph_write_fails(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HATG 图写入失败时节点改名回滚，Resident 标题不变"""
    record = resident_domain.register_resident(
        _new_session_payload("Atomic graph source"),
        "sample_admin",
    )
    _append_conversation_turn(record['resident_id'], 0)
    detail = resident_domain.create_resident_full_context_fork(
        record["resident_id"],
        "sample_admin",
        "lineage:test:atomic-graph-rename",
        defer_backend_start=True,
    )
    created_resident_id = str(detail["resident_id"])
    _add_created_resident_to_lineage(record, created_resident_id)
    lineage_id = str(record["lineage_id"])
    current = lineage_domain.get_lineage(lineage_id, "sample_admin")
    next(
        node
        for node in current["nodes"]
        if node.get("id") == created_resident_id
    )["title"] = "Atomic graph target"
    catalog = lineage_aggregate.lineage_catalog_store()
    original_upsert = catalog.upsert

    def persist_then_fail(definition: dict) -> None:
        original_upsert(definition)
        raise RuntimeError("injected graph write failure")

    # Restore the instance dictionary, so teardown cannot pin a bound method
    # over later class-level fixture patches on the cached catalog.
    monkeypatch.setitem(catalog.__dict__, "upsert", persist_then_fail)

    with pytest.raises(RuntimeError, match="injected graph write failure"):
        lineage_domain.update_lineage(
            lineage_id,
            {
                "lineage": current,
                "expected_revision": current["revision"],
            },
            "sample_admin",
        )

    persisted_resident = resident_catalog.get_catalog().get(
        created_resident_id
    )
    persisted_lineage = catalog.get(lineage_id)
    assert persisted_resident["title"] != "Atomic graph target"
    assert next(
        node
        for node in persisted_lineage["nodes"]
        if node.get("id") == created_resident_id
    )["title"] != "Atomic graph target"


def test_exact_resident_send_returns_the_selected_derived_identity(
    future_creation_store: Path,
) -> None:
    """向 fork 出的 Resident 发送消息时返回该 Resident 的 ID"""
    record = resident_domain.register_resident(
        _new_session_payload("Exact send identity source"),
        "sample_admin",
    )
    _append_conversation_turn(record['resident_id'], 0)
    detail = resident_domain.create_resident_full_context_fork(
        record["resident_id"],
        "sample_admin",
        "lineage:test:exact-send-identity",
        defer_backend_start=True,
    )
    created_resident_id = detail["resident_id"]
    _add_created_resident_to_lineage(record, created_resident_id)

    sent = resident_domain.deliver_resident_message_content(
        created_resident_id,
        "sample_admin",
        "Send a normal message to the selected Resident.",
        activation_policy="enqueue_and_trigger",
    )

    assert sent["resident_id"] == created_resident_id
    assert sent["resident_id"] == created_resident_id


def test_lineage_fork_creation_returns_lightweight_result_without_family_rewrite(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """full-context fork 返回只含身份与标题的轻量结果，节点元数据不含会话和运行状态"""
    record = resident_domain.register_resident(
        _new_session_payload("Fork creation source"),
        "sample_admin",
    )
    _append_conversation_turn(record['resident_id'], 0)
    result = resident_domain.create_resident_full_context_fork(
        record["resident_id"],
        "sample_admin",
        "lineage:test:lightweight-fork-result",
        defer_backend_start=True,
        fork_title="最终节点名称",
    )

    assert result["schema"] == "tfi.resident-creation-result.v1"
    assert result["title"] == "最终节点名称"
    assert "family_residents" not in result
    assert "messages" not in result
    registry = resident_lineage.snapshot(
        resident_record_domain.find_resident(record["resident_id"])
    )
    created = resident_lineage.find_in_snapshot(
        registry,
        result["resident_id"],
    )
    assert created["title"] == "最终节点名称"
    assert created["title_origin"] == "custom"

    _add_created_resident_to_lineage(record, result["resident_id"])

    detail = resident_domain.get_lineage_node_metadata(
        result["resident_id"],
        "sample_admin",
    )
    assert detail["schema"] == "tfi.lineage-node-metadata.v1"
    assert detail["resident_id"] == result["resident_id"]
    assert "view" not in detail
    assert "fork_id" not in detail
    assert "family_residents" not in detail
    assert "fork_session_logs" not in detail
    assert "token_usage" not in detail
    assert "messages" not in detail
    assert "resident_state" not in detail
    assert "runtime_activity" not in detail
    assert "queue" not in detail
    assert "input_states" not in detail
    assert "chat_workspace" not in detail
    assert "runtime_settings" not in detail
    assert "provider_session" not in detail
    assert detail["resident"] == {
        "resident_id": result["resident_id"],
        "title": "最终节点名称",
        "backend": "codex",
    }


def test_fork_operation_commits_created_resident_and_resource_outbox(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fork 创建的 Resident 与其资源投影 outbox 条目字段一致"""
    del future_creation_store
    record = resident_domain.register_resident(
        _new_session_payload("Fork ordering"),
        "sample_admin",
    )
    _append_conversation_turn(record['resident_id'], 0)
    result = resident_domain.create_resident_full_context_fork(
        record["resident_id"],
        "sample_admin",
        defer_backend_start=True,
    )

    created_resident_id = result["resident_id"]
    persisted = resident_catalog.get_catalog().get(created_resident_id)
    claims = resident_catalog.get_catalog().state_store.claim_resident_resource_projections()
    queued = next(item["payload"] for item in claims if item["resident_id"] == created_resident_id)
    for field in ("resident_id", "lineage_id", "source_resident_id", "context_source_resident_id",
                  "title", "fork_mode"):
        assert queued[field] == persisted[field]
    assert queued["execution_settings"]["model"] == persisted["execution_settings"]["model"]
    assert queued["harness_config"] == persisted["harness_config"]




def test_cross_lineage_context_fork_keeps_external_source_as_provenance(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨 Lineage 从消息 fork 时外部 Resident 记为上下文来源，新 Resident 继承所选轨迹"""
    del future_creation_store
    source = resident_domain.register_resident(
        _new_session_payload("External context source"),
        "sample_admin",
    )
    target = resident_domain.register_resident(
        _new_session_payload("Context target"),
        "sample_admin",
    )
    _append_conversation_turn(source['resident_id'], 0)
    source_creation_result = resident_domain.create_resident_full_context_fork(
        source["resident_id"],
        "sample_admin",
        defer_backend_start=True,
    )
    source_resident_id = source_creation_result["resident_id"]
    _add_created_resident_to_lineage(source, source_resident_id)
    source_session_id = "019e63b0-031d-7913-9dc8-0d972899beef"
    source_resident_record = resident_record_domain.find_resident(source_resident_id)
    register_session(
        source_resident_record,
        source_session_id,
        backend_route_id="zero",
    )
    external_message_id = "msg_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    external_reply_id = "msg_ffffffffffffffffffffffffffffffff"
    timeline = [
        {
            "message_id": external_message_id,
            "role": "user",
            "author_id": "sample_admin",
            "content_parts": [{
                "type": "text",
                "text": "Carry this context into the target Lineage.",
            }],
            "created_at": "2026-08-04T02:00:00Z",
            "provider_turn_id": "019e63b0-031d-7913-9dc8-0d972899bedd",
        },
        {
            "message_id": external_reply_id,
            "role": "agent",
            "content_parts": [{
                "type": "text",
                "text": "Context acknowledged.",
            }],
            "created_at": "2026-08-04T02:00:01Z",
            "reply_to_message_id": external_message_id,
            "provider_turn_id": "019e63b0-031d-7913-9dc8-0d972899bedd",
        },
    ]
    agent_store = get_agent_data_store()
    source_session = agent_store.ensure_provider_session(
        provider='codex', backend_route_id='zero', backend_session_id=source_session_id,
        agent_id=source_resident_id, actor_ref='sample_admin',
    )
    context_execution = f'execution-external-context-{source_resident_id}'
    agent_store.begin_execution(execution_id=context_execution, agent_id=source_resident_id,
                                actor_ref='sample_admin', provider='codex', started_at='2026-08-04T02:00:00Z')
    agent_store.bind_execution_provider_session(
        context_execution, provider_session_ref=source_session['provider_session_ref'],
    )
    from management_portal.execution.message_projection import message_facts, wrap_message
    context_segments = []
    for message in timeline:
        body = message['content_parts'][0]['text']
        segment = agent_store.put_segment(
            segment_type='input' if message['role'] == 'user' else 'model',
            content=wrap_message(body) if message['role'] == 'user' else body,
            execution_id=context_execution,
            metadata={'role': message['role'], 'message': message_facts(message)}, created_by_agent_id=source_resident_id,
        )
        context_segments.append(segment)
        message['segment_id'] = segment
    # Fork inherits the selected trace window, including context inherited by
    # this source before its current Provider session was registered.
    inherited_segments = agent_store.sequence_segment_ids(agent_store.trace_sequence_id(source_resident_id))
    agent_store.append_agent_segments(source_resident_id, context_segments, actor_ref='sample_admin')
    agent_store.finish_execution(context_execution, status='completed', provider_turn_id=timeline[0]['provider_turn_id'])
    for index in range(MAX_TIMELINE_ITEMS + 1):
        agent_store.display_chat_message(source_resident_id, {
            "message_id": f"msg_{index + 100_000:032x}",
            "role": "user",
            "author_id": "sample_admin",
            "content_parts": [{
                "type": "text",
                "text": f"Newer question {index}",
            }],
            "created_at": f"2026-08-05T02:{index // 60:02d}:{index % 60:02d}Z",
        })
    assert external_message_id not in {
        item["message_id"]
        for item in resident_conversation_projection_domain.resident_conversation_messages(
            source_resident_record,
            limit=MAX_TIMELINE_ITEMS,
        )
    }
    result = resident_domain.create_resident_fork_from_message(
        source_resident_id,
        "sample_admin",
        {"message_id": external_message_id},
        target_lineage_resident_id=target["resident_id"],
    )

    target_record = resident_record_domain.find_resident(target["resident_id"])
    registry = resident_lineage.snapshot(target_record)
    created = resident_lineage.find_in_snapshot(
        registry,
        result["resident_id"],
    )
    assert created["source_resident_id"] == target["resident_id"]
    assert created["context_source_resident_id"] == source_resident_id
    assert "source_fork_id" not in created
    assert "native_codex_fork" not in created
    assert created["fork_mode"] == "message"
    assert get_agent_data_store().sequence_segment_ids(get_agent_data_store().trace_sequence_id(created['resident_id'])) == inherited_segments + context_segments
    native_operations: list[dict] = []
    monkeypatch.setattr(
        resident_session_backend_domain,
        "run_backend_session_operation",
        lambda **kwargs: (
            native_operations.append(kwargs)
            or {
                "backend_session_id": "019e63b0-031d-7913-9dc8-0d972899beee",
                "backend_route_id": "zero",
            }
        ),
    )

    exact_created = resident_record_domain.find_resident(result["resident_id"])
    assert exact_created is not None
    resident_session_backend_domain.create_exact_resident_session(
        exact_created,
        {
            'execution_id': "job_cross_lineage_native_fork",
            'operation': "context_fork_create",
            "actor": "sample_admin",
        },
    )

    assert native_operations[0]['operation'] == 'create'
    assert native_operations[0]["binding_id"] == (
        f"resident:{result['resident_id']}"
    )
    prepared = resident_execution_domain._portable_context_execution(exact_created, {
        'actor': 'sample_admin', 'raw_message': 'What was acknowledged?',
    })
    assert 'Carry this context into the target Lineage.' not in prepared['actual_sent_prompt']
    from management_portal.execution.codex_history import response_items_from_segments
    prompt = json.dumps(response_items_from_segments(get_agent_data_store().fork_context_segments(
        prepared['fork_context_input']['segments'],
    )))
    assert 'Carry this context into the target Lineage.' in prompt
    assert 'Context acknowledged.' in prompt
    assert 'Newer question' not in prompt


def test_generated_fork_title_can_adopt_the_existing_lineage_name(
    future_creation_store: Path,
) -> None:
    """自动生成的 fork 标题只能被图名采用一次，之后标题来源记为 lineage"""
    record = resident_domain.register_resident(
        _new_session_payload("方案主节点"),
        "sample_admin",
    )
    _append_conversation_turn(record['resident_id'], 0)
    detail = resident_domain.create_resident_full_context_fork(
        record["resident_id"],
        "sample_admin",
        "lineage:test:adopt-title",
        defer_backend_start=True,
    )
    created_resident_id = detail["resident_id"]
    _add_created_resident_to_lineage(record, created_resident_id)
    identities = [
        (record["resident_id"], ""),
        (record["resident_id"], created_resident_id),
    ]
    initial_registry = resident_lineage.snapshot(
        resident_record_domain.find_resident(record["resident_id"])
    )
    initial_resident = resident_lineage.find_in_snapshot(initial_registry, created_resident_id)

    before = resident_domain.resident_title_map(identities)
    adopted = resident_domain.adopt_generated_resident_title(
        created_resident_id,
        "sample_admin",
        "证据核对",
    )
    adopted_again = resident_domain.adopt_generated_resident_title(
        created_resident_id,
        "sample_admin",
        "另一个图名",
    )
    after = resident_domain.resident_title_map(identities)
    adopted_registry = resident_lineage.snapshot(
        resident_record_domain.find_resident(record["resident_id"])
    )
    adopted_resident = resident_lineage.find_in_snapshot(adopted_registry, created_resident_id)

    assert before[(record["resident_id"], "")] == "方案主节点"
    assert initial_resident["title_origin"] == "generated"
    assert resident_domain.is_generated_derived_resident_title(
        before[(record["resident_id"], created_resident_id)]
    )
    assert adopted is True
    assert adopted_again is False
    assert after[(record["resident_id"], created_resident_id)] == "证据核对"
    assert adopted_resident["title_origin"] == "lineage"


def test_new_codex_resident_persists_account_route_without_session_identity(
    future_creation_store: Path,
) -> None:
    """新建 codex Resident 保存 backend_route_id 账号路由，不带会话 ID"""
    record = resident_domain.register_resident(
        {
            **_new_session_payload("Routed resident"),
            "backend_route_id": "zero",
        },
        "sample_admin",
    )

    persisted = resident_catalog.get_catalog().get(
        record["resident_id"]
    )

    assert record["backend_route_id"] == "zero"
    assert persisted["backend_route_id"] == "zero"
    assert "session_history" not in record
    assert "codex_thread_id" not in record
    assert "backend_session_id" not in record


def test_full_context_fork_deferred_creation_has_no_independent_start_gate(
    future_creation_store: Path,
) -> None:
    """延后启动的 full-context fork 没有单独的启动闸门，可直接提交输入

    :module: resident.fork-handoff"""
    record = resident_domain.register_resident(
        _new_session_payload("Deferred Handoff fork"),
        "sample_admin",
    )

    _append_conversation_turn(record['resident_id'], 0)
    detail = resident_domain.create_resident_full_context_fork(
        record["resident_id"],
        "sample_admin",
        "lineage:test:workspace-handoff",
        defer_backend_start=True,

    )
    created_resident_id = detail["resident_id"]
    _add_created_resident_to_lineage(record, created_resident_id)
    registry = resident_lineage.snapshot(record)
    derived_resident = next(
        item for item in registry["residents"]
        if item["resident_id"] == created_resident_id
    )

    expected_workspace = (
        Path(record["workspace_path"])
        / "residents"
        / created_resident_id
        / "workspace"
    ).resolve(strict=False)
    assert "backend_start_deferred" not in derived_resident
    assert "backend_start_gate" not in derived_resident
    assert "workspace_binding_origin" not in derived_resident
    assert derived_resident["default_cwd"] == str(expected_workspace)
    assert derived_resident["owned_workspace"]["path"] == str(expected_workspace)
    assert all(binding["path"] != record["default_cwd"]
               for binding in derived_resident["workspace_bindings"])
    exact_resident = resident_record_domain.find_resident(created_resident_id)
    assert exact_resident is not None
    assert resident_execution_work.read(exact_resident) == {}

    resident_domain.deliver_resident_message_content(
        created_resident_id,
        "sample_admin",
        "Handoff ready 后正常提交输入",
        activation_policy="enqueue_and_trigger",
    )
    registry = resident_lineage.snapshot(record)
    released = next(
        item for item in registry["residents"]
        if item["resident_id"] == created_resident_id
    )
    assert "backend_start_deferred" not in released
    assert "backend_start_gate" not in released


def test_workspace_handoff_binding_records_the_same_workspace_in_target_markdown(
    future_creation_store: Path,
) -> None:
    """fork 出的 Resident 挂载来源 Workspace 后，workspaces.md 记下其路径和 ID"""
    record = resident_domain.register_resident(
        _new_session_payload("Same Workspace Handoff"),
        "sample_admin",
    )
    _append_conversation_turn(record['resident_id'], 0)
    detail = resident_domain.create_resident_full_context_fork(
        record["resident_id"],
        "sample_admin",
        "lineage:test:continue-existing-workspace",
        defer_backend_start=True,

    )
    created_resident_id = detail["resident_id"]
    _add_created_resident_to_lineage(record, created_resident_id)
    source_binding = record["owned_workspace"]
    source_path = str(source_binding["path"])
    source_workspace_id = str(source_binding["workspace_id"])

    mounted = resident_domain.add_resident_reference_workspace(
        created_resident_id,
        source_path,
        "sample_admin",
        workspace_id=source_workspace_id,
    )

    registry = resident_lineage.snapshot(record)
    derived_resident = next(
        item for item in registry["residents"]
        if item["resident_id"] == created_resident_id
    )
    continued = next(
        item for item in derived_resident["workspace_bindings"]
        if item["path"] == source_path
    )
    workspaces_markdown = (
        Path(record["workspace_path"])
        / "residents"
        / created_resident_id
        / "workspaces.md"
    ).read_text(encoding="utf-8")

    assert mounted["added"] is True
    assert continued["workspace_id"] == source_workspace_id
    assert source_path in workspaces_markdown
    assert source_workspace_id in workspaces_markdown
    assert continued["workspace_id"] != derived_resident["owned_workspace_id"]
    assert "backend_start_deferred" not in derived_resident
    assert "backend_start_gate" not in derived_resident


def test_delete_exact_resident_tombstones_only_its_owned_workspace(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除 Resident 只给它自有的 Workspace 打删除标记，共享 Workspace 保留"""
    record = resident_domain.register_resident(
        _new_session_payload("Fork workspace delete"),
        "sample_admin",
    )
    _append_conversation_turn(record['resident_id'], 0)
    detail = resident_domain.create_resident_full_context_fork(
        record["resident_id"],
        "sample_admin",
        "lineage:test:delete-workspace",
        defer_backend_start=True,

    )
    created_resident_id = detail["resident_id"]
    _add_created_resident_to_lineage(record, created_resident_id)
    registry = resident_lineage.snapshot(record)
    derived_resident = next(item for item in registry["residents"] if item["resident_id"] == created_resident_id)
    owned_workspace_id = derived_resident["owned_workspace_id"]
    shared_workspace_id = record["workspace_bindings"][0]["workspace_id"]
    resident_domain.add_resident_reference_workspace(
        created_resident_id,
        record["workspace_bindings"][0]["path"],
        "sample_admin",
        workspace_id=shared_workspace_id,
    )
    published = []
    sync_bundles = resource_client.sync_resident_resource_bundles

    def record_bundles(records, *, updated_by="society-service"):
        records = list(records)
        published.extend(records)
        return sync_bundles(records, updated_by=updated_by)

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", record_bundles)
    _delete_member(created_resident_id)

    assert not resident_catalog.get_catalog().state_store.resident_resource_projection_pending(created_resident_id)
    tombstone = next(item for item in published if item["resident_id"] == created_resident_id)
    projections = resource_client._resident_resource_bundle_projections(tombstone)
    from management_portal.resources.workspace_store import get_workspace_store
    workspaces = get_workspace_store().rows([owned_workspace_id, shared_workspace_id], include_deleted=True)
    assert workspaces[owned_workspace_id]["deleted_at"]
    assert workspaces[shared_workspace_id]["deleted_at"] is None
    assert all(not item["descriptor"]["resource_id"].startswith("workspace_") for item in projections)


def test_deleted_resident_cleanup_failure_retries_without_resurrecting_identity(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除 Resident 后文件夹清理失败会重试完成，期间 Resident 不会复活"""
    record = resident_domain.register_resident(
        _new_session_payload("Fork move failure"),
        "sample_admin",
    )
    _append_conversation_turn(record['resident_id'], 0)
    detail = resident_domain.create_resident_full_context_fork(
        record["resident_id"],
        "sample_admin",
        "lineage:test:delete-move-failure",
        defer_backend_start=True,

    )
    created_resident_id = detail["resident_id"]
    _add_created_resident_to_lineage(record, created_resident_id)
    derived_resident = next(
        item
        for item in resident_lineage.snapshot(record)["residents"]
        if item["resident_id"] == created_resident_id
    )
    from management_portal.resources.workspace_store import get_workspace_store
    workspace_id = derived_resident["owned_workspace_id"]
    folder = resident_record_domain.resident_folder_path(resident_catalog.get_catalog().get(created_resident_id))
    _delete_member(created_resident_id)
    assert resident_record_domain.find_resident(created_resident_id) is None
    operations = resident_record_domain.lifecycle_operations()
    operation_id, = operations.ready()
    with monkeypatch.context() as failure:
        from management_portal.society_service import resident_workspace_retirement
        failure.setattr(resident_workspace_retirement, "remove_retired_folder",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("remove failed")))
        assert not resident_domain.resume_resident_lifecycle_operation(operation_id)
    assert "remove failed" in operations.health()["last_error"]
    assert resident_record_domain.find_resident(created_resident_id) is None
    assert folder.is_dir()
    assert get_workspace_store().rows([workspace_id], include_deleted=True)[workspace_id]["deleted_at"]
    operations.store.database.write(lambda connection: connection.execute(
        'UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s', (operation_id,),
    ))
    assert resident_domain.resume_resident_lifecycle_operation(operation_id)
    # Deletion removes the folder; nothing is archived (decision_d19b9909a674).
    assert not folder.exists()
    assert not operations.pending(created_resident_id)


def test_delete_source_resident_preserves_child_provenance(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除 fork 来源 Resident 后，子 Resident 的 source_resident_id 保持不变"""
    record = resident_domain.register_resident(
        _new_session_payload("Fork child reparent"),
        "sample_admin",
    )
    _append_conversation_turn(record['resident_id'], 0)
    parent_detail = resident_domain.create_resident_full_context_fork(
        record["resident_id"],
        "sample_admin",
        "lineage:test:parent",
        defer_backend_start=True,
    )
    parent_id = parent_detail["resident_id"]
    _add_created_resident_to_lineage(record, parent_id)
    registry = resident_lineage.snapshot(record)
    parent = next(
        item for item in registry["residents"] if item["resident_id"] == parent_id
    )
    register_session(
        parent,
        "019e63b0-031d-7913-9dc8-0d972899bed4",
    )
    parent["session_state"] = "active"
    parent.pop("backend_start_deferred", None)
    parent.pop("backend_start_gate", None)
    resident_fork_domain._persist_derived_resident(record, parent)

    _append_conversation_turn(parent_id, 0)
    child_detail = resident_domain.create_resident_full_context_fork(
        parent_id,
        "sample_admin",
        "lineage:test:child",
        defer_backend_start=True,
    )
    child_id = child_detail["resident_id"]
    _add_created_resident_to_lineage(record, child_id)

    _delete_member(parent_id)

    registry = resident_lineage.snapshot(record)
    child = next(
        item for item in registry["residents"] if item["resident_id"] == child_id
    )
    assert child["source_resident_id"] == parent_id


def test_new_hatg_member_uses_identity_from_publication_transaction(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HATG 新增成员的 Resident 身份在图发布事务中即可解析"""
    # The generic fixture accepts arbitrary Resident IDs. Use the production
    # resolver so a separate committed read cannot invent an unpublished member.
    monkeypatch.setattr(lineage_domain, "_resident_targets", CANONICAL_RESIDENT_TARGETS)
    first = resident_domain.register_resident(_new_session_payload("First member"), "sample_admin")
    graph = lineage_domain.get_lineage(first["lineage_id"], "sample_admin")

    result = lineage_domain.create_lineage_resident(
        first["lineage_id"],
        {**_new_session_payload("Second member"), "expected_revision": graph["revision"]},
        "sample_admin",
    )

    new_id = result["resident"]["resident_id"]
    assert set(CANONICAL_RESIDENT_TARGETS([first["resident_id"], new_id])) == {
        first["resident_id"], new_id,
    }
    assert {node["id"] for node in result["lineage"]["nodes"]} == {
        first["resident_id"], new_id,
    }


def test_lineage_node_creation_reuses_the_same_lineage_folder(
    future_creation_store: Path,
) -> None:
    """在已有 Lineage 中创建 Resident 节点沿用该 Lineage 文件夹，不另建 Lineage"""
    lineage_root = future_creation_store
    created = lineage_domain.create_lineage(
        {"title": "Atomic node creation", "nodes": [], "edges": []},
        "sample_admin",
    )

    result = lineage_domain.create_lineage_resident(
        created["lineage_id"],
        {
            **_new_session_payload("Independent node"),
            "backend_route_id": "two",
            "expected_revision": created["revision"],
            "x": 42,
            "y": 36,
        },
        "sample_admin",
    )

    record = result["resident"]
    node = result["node"]
    lineage_folder = lineage_root / "lineages" / created["lineage_id"]
    resident_folder = lineage_folder / "residents" / record["resident_id"]

    assert record["lineage_id"] == created["lineage_id"]
    assert record["backend_route_id"] == "two"
    assert Path(record["workspace_path"]) == lineage_folder
    assert Path(record["default_cwd"]) == resident_folder / "workspace"
    assert node["id"] == record["resident_id"]
    assert result["lineage"]["revision"] == created["revision"] + 1
    assert sorted(
        path.name
        for path in (lineage_root / "lineages").glob("lineage_*")
    ) == [created["lineage_id"]]


def test_direct_lineage_resident_creation_uses_default_account_unless_overridden(
    future_creation_store: Path,
) -> None:
    """在 Lineage 中直接创建 Resident 使用默认账号，显式指定 backend_route_id 时用指定账号"""
    del future_creation_store
    source_payload = _new_session_payload("Account inheritance source")
    source_payload["backend_route_id"] = "two"
    source = resident_domain.register_resident(source_payload, "sample_admin")
    lineage = lineage_domain.get_lineage(source["lineage_id"], "sample_admin")

    inherited = lineage_domain.create_lineage_resident(
        source["lineage_id"],
        {
            **_new_session_payload("Default account child"),
            "expected_revision": lineage["revision"],
        },
        "sample_admin",
    )
    overridden = lineage_domain.create_lineage_resident(
        source["lineage_id"],
        {
            **_new_session_payload("Explicit account child"),
            "backend_route_id": "zero",
            "expected_revision": inherited["lineage"]["revision"],
        },
        "sample_admin",
    )

    assert inherited["resident"]["backend_route_id"] == "one"
    assert overridden["resident"]["backend_route_id"] == "zero"


def test_lineage_member_creation_adds_a_resident_without_an_edge(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """添加 Lineage 成员只新增节点不加边，Project 沿用 Lineage 的 Project"""
    project_root = future_creation_store / "projects" / "project_test"
    project_root.mkdir(parents=True)
    monkeypatch.setattr(
        resource_client,
        "project_roots",
        lambda **_kwargs: {"project_test": project_root},
    )
    root = resident_domain.register_resident(
        _new_session_payload("Lineage leader"),
        "sample_admin",
    )

    result = lineage_domain.create_lineage_resident(
        root["lineage_id"],
        {
            "title": "Independent Lineage member",
            "backend": "codex",
            "backend_route_id": "two",
            "mode": "new_session",
        },
        "sample_admin",
    )

    child = result["resident"]
    assert "source_resident_id" not in child
    assert "kind" not in child
    assert child.get("project_id") == root.get("project_id")
    assert child["backend_route_id"] == "two"
    assert resident_conversation_stream.get_repository().read_messages(
        child["resident_id"]
    ) == []
    assert any(
        item["id"] == child["resident_id"]
        for item in result["lineage"]["nodes"]
    )
    assert not any(
        item.get("source") == root["resident_id"]
        and item.get("target") == child["resident_id"]
        for item in result["lineage"]["edges"]
    )


def test_non_leader_creates_and_manages_residents_through_hatg(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 Leader 成员可经 HATG 创建、跨 Lineage 创建并删除 Resident

    :design: decision_3e07c6d35cac"""
    from tests.cases.isa_test_support import reset_kernels, run

    reset_kernels()
    leader = resident_domain.register_resident(
        _new_session_payload("HATG permission leader"), "sample_admin",
    )
    member = lineage_domain.create_lineage_resident(
        leader["lineage_id"], _new_session_payload("HATG permission member"), "sample_admin",
    )
    caller_id = member["resident"]["resident_id"]
    lineage = member["lineage"]
    assert lineage["leader_resident_id"] != caller_id
    def call(instruction, arguments, *, key, version=None):
        return run(instruction, arguments, actor=caller_id, entry="harness", key=key, version=version)

    creation = {"lineage_id": lineage["lineage_id"], "title": "Created by a member", "provider": "codex"}
    created = call("CreateResident", creation, key="member-create-resident",
                   version={lineage["lineage_id"]: lineage["revision"]})
    assert created.status == "committed", created.error
    child_id = created.result["resident_id"]
    assert created.result["lineage_id"] == lineage["lineage_id"]
    replay = call("CreateResident", creation, key="member-create-resident",
                  version={lineage["lineage_id"]: lineage["revision"]})
    assert replay.replayed is True
    assert replay.result["resident_id"] == child_id
    assert resident_access.can_manage_resident(leader, caller_id) is True

    foreign = resident_domain.register_resident(
        _new_session_payload("Foreign HATG permission leader"), "sample_admin",
    )
    cross_creation = {**creation, "lineage_id": foreign["lineage_id"], "near_resident_id": foreign["resident_id"]}
    # Exempt cross-Lineage creation preserves the caller's existing membership.
    allowed = call("CreateResident", cross_creation, key="member-cross-lineage-authorized")
    assert allowed.status == "committed", allowed.error
    assert allowed.result["lineage_id"] == foreign["lineage_id"]
    assert resident_catalog.get_catalog().get(caller_id)["lineage_id"] == lineage["lineage_id"]
    deleted = call("DeleteResident", {"lineage_id": lineage["lineage_id"], "resident_id": child_id}, key="member-delete-resident")
    assert deleted.status == "committed", deleted.error
    assert resident_record_domain.find_resident(child_id) is None
    remaining = lineage_domain.get_lineage(lineage["lineage_id"], caller_id)
    assert {node["id"] for node in remaining["nodes"]} == {
        leader["resident_id"], caller_id,
    }

@pytest.mark.parametrize("scope", ["same", "foreign", "new", "deleted", "wrong_project"])
def test_resident_creation_uses_current_membership_without_project_role(
    future_creation_store, monkeypatch, scope,
):
    """Resident 凭当前成员身份即可创建 Resident，调用者已删除或目标 Lineage 不存在时被拒绝

    :design: decision_3e07c6d35cac"""
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    leader = resident_domain.register_resident(_new_session_payload("Authority root"), "sample_admin")
    caller = lineage_domain.create_lineage_resident(
        leader["lineage_id"], _new_session_payload("Ordinary member"), "sample_admin",
    )["resident"]
    foreign = resident_domain.register_resident(_new_session_payload("Foreign authority root"), "sample_admin")
    caller_id = caller["resident_id"]
    if scope == "deleted":
        _delete_member(caller_id)
    target = foreign if scope == "foreign" else leader
    payload = {**_new_session_payload("Membership-authorized child"), "lineage_id": target["lineage_id"]}
    if scope == "new":
        payload["lineage_id"] = "lineage_999999999999"
    if scope == "wrong_project":
        payload["project_id"] = "different_project"
    before = {record["resident_id"] for record in resident_catalog.get_catalog().snapshot()}
    payload["lineage_folder_path"] = target["workspace_path"]

    def operation(payload, actor):
        return resident_domain.register_resident(payload, actor, lineage_node={})
    if scope in {"same", "foreign"}:
        created = operation(payload, caller_id)
        child_id = created["resident_id"]
        stored = resident_catalog.get_catalog().get(child_id)
        assert stored["lineage_id"] == target["lineage_id"]
        assert stored["project_id"] == target["project_id"]
        assert stored["created_by"] == caller_id
        assert resident_access.can_manage_resident(stored, caller_id)
    else:
        with pytest.raises((PermissionError, KeyError, ValueError)):
            operation(payload, caller_id)
        assert before == {record["resident_id"] for record in resident_catalog.get_catalog().snapshot()}




@pytest.mark.parametrize("origin", [(20, 35), (300, -250)])
def test_lineage_resident_created_team_uses_compact_default_hatg_positions(
    future_creation_store: Path,
    origin: tuple[float, float],
) -> None:
    """连续创建的团队成员在 HATG 中围绕 Leader 紧凑排布且互不重叠"""
    root = resident_domain.register_resident(
        _new_session_payload("Positioned root"),
        "sample_admin",
    )
    lineage = lineage_domain.get_lineage(root["lineage_id"], "sample_admin")
    lineage_domain.update_lineage_layout(root["lineage_id"], {
        "positions": [{"id": root["resident_id"], "x": origin[0], "y": origin[1]}],
    }, "sample_admin")
    for member_number in range(1, 10):
        created = lineage_domain.create_lineage_resident(
            root["lineage_id"],
            {
                **_new_session_payload(f"Team member {member_number}"),
                "expected_revision": lineage["revision"],
            },
            "sample_admin",
        )
        lineage = created["lineage"]

    positions = [
        (node["x"], node["y"])
        for node in lineage["nodes"]
    ]
    assert len(positions) == 10
    assert len(set(positions)) == 10
    assert (next(node for node in lineage["nodes"] if node["id"] == root["resident_id"])["x"],
            next(node for node in lineage["nodes"] if node["id"] == root["resident_id"])["y"]) == origin
    assert all(math.dist(position, origin) < 55 for position in positions)
    assert lineage["edges"] == []
    for index, position in enumerate(positions):
        for other in positions[index + 1:]:
            assert (
                abs(position[0] - other[0]) >= (154 + 24) / 12.8
                or abs(position[1] - other[1]) >= (94 + 24) / 9
            )


def test_independent_resident_uses_requested_anchor_and_validates_before_registration(
    future_creation_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """指定 near_resident_id 时新 Resident 放在锚点附近且不重叠，无效放置参数在注册前被拒绝"""
    root = resident_domain.register_resident(_new_session_payload("Leader"), "sample_admin")
    source = lineage_domain.create_lineage_resident(root["lineage_id"], {
        **_new_session_payload("Handoff source"), "x": 300, "y": -250,
    }, "sample_admin")["node"]
    sizes = {source["id"]: {"width": 32, "height": 28}}
    for index in range(5):
        result = lineage_domain.create_lineage_resident(root["lineage_id"], {
            **_new_session_payload(f"Handoff recipient {index}"),
            "near_resident_id": source["id"],
            "placement": {"node_sizes": sizes, "child_size": {"width": 32, "height": 28}},
        }, "sample_admin")
        child = result["node"]
        assert math.dist((child["x"], child["y"]), (source["x"], source["y"])) < 90
        for other in result["lineage"]["nodes"]:
            if other["id"] in sizes:
                assert abs(child["x"] - other["x"]) >= 32 + 24 / 12.8 or abs(child["y"] - other["y"]) >= 28 + 24 / 9
        sizes[child["id"]] = {"width": 32, "height": 28}
        assert result["lineage"]["edges"] == []
        assert not result["resident"].get("source_resident_id")
        assert "near_resident_id" not in child
        assert "placement" not in result["resident"]

    def unexpected_registration(*_args, **_kwargs):
        pytest.fail("Invalid placement must fail before allocating a Resident")

    monkeypatch.setattr(resident_registration_domain, "register_resident", unexpected_registration)
    for invalid in [
        {"near_resident_id": "resident_111111111111"},
        {"placement": {"child_size": {"width": 0, "height": 28}}},
    ]:
        with pytest.raises(ValueError):
            lineage_domain.create_lineage_resident(root["lineage_id"], {
                **_new_session_payload("Invalid placement"), **invalid,
            }, "sample_admin")



def test_existing_resident_id_cannot_cross_lineage_boundaries(future_creation_store: Path) -> None:
    """已有 Resident 不能作为其他 Lineage 的主体，也不能经图补丁加入其他 Lineage"""
    first = lineage_domain.create_lineage({"title": "First", "nodes": [], "edges": []}, "sample_admin")
    existing = lineage_domain.create_lineage_resident(first["lineage_id"], _new_session_payload("First resident"), "sample_admin")
    second = lineage_domain.create_lineage({"title": "Second", "nodes": [], "edges": []}, "sample_admin")
    resident_id = existing["resident"]["resident_id"]
    assert lineage_domain.authorize_lineage_resident_principal(first["lineage_id"], resident_id) == resident_id
    with pytest.raises(PermissionError, match="不属于目标 Lineage"):
        lineage_domain.authorize_lineage_resident_principal(second["lineage_id"], resident_id)
    with pytest.raises(ValueError, match="不能独立增加或移除"):
        lineage_domain.apply_lineage_graph_patch(second["lineage_id"], {
            "patch": {"nodes": [existing["node"]]}, "expected_revision": second["revision"],
        }, "sample_admin")


def test_fork_appears_in_lineage_without_separate_graph_membership(future_creation_store: Path) -> None:
    """fork 出的 Resident 直接出现在来源 Lineage 图中，不能经图补丁单独增删"""
    created = lineage_domain.create_lineage({"title": "Fork family", "nodes": [], "edges": []}, "sample_admin")
    root_result = lineage_domain.create_lineage_resident(created["lineage_id"], {
        **_new_session_payload("Fork root"), "x": 300, "y": -250,
    }, "sample_admin")
    root = root_result["resident"]
    _append_conversation_turn(root['resident_id'], 0)
    generated = resident_domain.create_resident_full_context_fork(
        root["resident_id"], "sample_admin", defer_backend_start=True, fork_title="Generated Resident",
    )
    generated_id = generated["resident_id"]
    record = resident_lineage.find(generated_id)
    assert "owner_ids" not in record
    assert record["lineage_id"] == created["lineage_id"]
    assert "lineage_membership" not in record
    assert resident_runtime_domain._is_direct_family_resident(root, record)
    store = resident_catalog.get_catalog().state_store
    before = store.lineage_definition(created["lineage_id"])
    nodes = {node["id"]: node for node in before["nodes"]}
    assert set(nodes) == {root["resident_id"], generated_id}
    assert nodes[generated_id]["title"] == "Generated Resident"
    assert "role" not in nodes[generated_id]
    assert "status" not in nodes[generated_id]
    assert (nodes[root["resident_id"]]["x"], nodes[root["resident_id"]]["y"]) == (300, -250)
    assert (nodes[generated_id]["x"], nodes[generated_id]["y"]) != (300, -250)
    assert before.get("edges", []) == root_result["lineage"].get("edges", [])
    assert before["leader_resident_id"] == root_result["lineage"]["leader_resident_id"]
    viewed = lineage_domain.get_lineage(created["lineage_id"], "sample_admin")
    assert {node["id"] for node in viewed["nodes"]} == set(nodes)
    assert store.lineage_definition(created["lineage_id"]) == before
    assert lineage_domain.authorize_lineage_resident_principal(created["lineage_id"], generated_id) == generated_id
    with pytest.raises(PermissionError, match="当前 Leader"):
        lineage_domain.authorize_lineage_resident_principal(created["lineage_id"], generated_id, require_leader=True)
    with pytest.raises(ValueError, match="不能独立增加或移除"):
        lineage_domain.apply_lineage_graph_patch(created["lineage_id"], {"patch": {"nodes": [nodes[root["resident_id"]]]}}, "sample_admin")


def _trace_prompt(record, job):
    from management_portal.society_service.resident_execution_domain import _portable_context_execution
    prepared = _portable_context_execution(record, job)
    return prepared.get('fork_context_input', {}).get('portable_prompt', prepared['actual_sent_prompt'])
