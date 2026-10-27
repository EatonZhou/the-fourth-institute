from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import uuid

import pytest

from management_portal.execution.contracts import ExecutionRequest
from management_portal import agent_context_selection
from tests.cases.isa_test_support import reset_kernels, run
from management_portal.society_service import (
    invariant_locks,
    lineage_domain,
    resident_catalog,
    resident_conversation_stream,
    resident_delivery_work,
    resident_execution_work,
    resident_input_queue_domain,
    resident_protocol,
    resident_record_domain,
    state_store,
)
from management_portal.society_service import resident_lifecycle_domain
from management_portal.society_service import resident_workspace_domain
from management_portal.resources.providers import project
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners

LOCAL_POPEN = subprocess.Popen


@pytest.fixture
def team(owners, monkeypatch):
    reset_kernels()
    target = lineage_domain.create_lineage_resident(owners.lineage_id, {
        "title": "Residual target", "backend": "codex", "mode": "new_session",
    }, ACTOR)["resident"]
    resident_protocol.change_resident_state(
        target["resident_id"], ACTOR, state_domain="intake", transition="close",
    )
    target = resident_record_domain.find_resident(target["resident_id"])
    yield SimpleNamespace(owners=owners, source=owners.record, target=target, lineage_id=owners.lineage_id)
    reset_kernels()


def instruction(team, name, arguments, *, key=None, actor=ACTOR):
    return run(name, arguments, actor=actor, key=key or "residual-" + uuid.uuid4().hex)


def test_artifact_isa_reads_exact_response_and_enforces_resident_scope(team):
    """GetMessageArtifact 返回完整响应并按 Resident 隔离产物"""
    resident_id = team.source["resident_id"]
    message_id = "msg_" + uuid.uuid4().hex
    response = {"output": [{"text": "synthetic structured response"}], "usage": {"tokens": 7}}
    message = resident_input_queue_domain.build_message(
        "agent", "Visible answer", resident_id, message_id=message_id,
        provider_response=response,
    )
    resident_lifecycle_domain.append_conversation_message(resident_id, message)
    artifact_id = message_id + ":provider_response"
    arguments = {"resident_id": resident_id, "artifact_id": artifact_id}
    result = instruction(team, "GetMessageArtifact", arguments)
    assert result.status == "committed"
    artifact = result.result["artifact"]
    assert artifact == {"artifact_id": artifact_id, "media_type": "application/json", "value": response}
    artifact["value"]["usage"]["tokens"] = 99
    assert resident_protocol.get_resident_artifact(resident_id, ACTOR, artifact_id)["value"] == response
    stored = resident_protocol.get_resident_message(resident_id, ACTOR, message_id)
    assert any(part.get("artifact_id") == artifact_id for part in stored["content_parts"])
    for other_id, other_artifact in (
        (team.target["resident_id"], artifact_id),
        (resident_id, "missing:provider_response"),
    ):
        with pytest.raises(KeyError):
            resident_protocol.get_resident_artifact(other_id, ACTOR, other_artifact)
    assert resident_protocol.get_resident_artifact(
        resident_id,
        "storage-outsider",
        artifact_id,
    )["value"] == response


def test_reorder_isa_persists_input_order_before_execution_allocation(team):
    """ReorderMailbox 把消息移到队首时在分配执行前持久调整输入顺序"""
    resident_id = team.target["resident_id"]
    entries = []
    for index in range(4):
        message_id = "msg_" + uuid.uuid4().hex
        resident_lifecycle_domain.append_conversation_message(
            resident_id, resident_input_queue_domain.build_message("user", message_id, ACTOR, message_id=message_id),
        )
        entries.append({
             "user_message_id": message_id,
            "message": message_id, "actor": ACTOR})
    record = resident_record_domain.find_resident(resident_id)
    entries[0]["execution_id"] = "execution-" + uuid.uuid4().hex[:24]
    resident_execution_work.write(record, {**entries[0], "queue": entries[1:]})
    result = instruction(team, "ReorderMailbox", {
        "resident_id": resident_id, "message_id": entries[3]["user_message_id"],
        "before_message_id": entries[1]["user_message_id"],
    })
    assert result.status == "committed"
    durable = resident_execution_work.read(record)
    assert [item["user_message_id"] for item in durable["queue"]] == [
        entries[3]["user_message_id"], entries[1]["user_message_id"], entries[2]["user_message_id"],
    ]
    assert durable["user_message_id"] == entries[0]["user_message_id"]
    assert all(not item.get("execution_id") for item in durable["queue"])
    resident_protocol.reorder_resident_queue(
        resident_id,
        "storage-outsider",
        message_id=entries[1]["user_message_id"],
        before_message_id=entries[3]["user_message_id"],
    )
    after_other_user_update = deepcopy(resident_execution_work.read(record))
    assert [item["user_message_id"] for item in after_other_user_update["queue"]] == [
        entries[1]["user_message_id"], entries[3]["user_message_id"], entries[2]["user_message_id"],
    ]
    with pytest.raises(ValueError):
        resident_protocol.reorder_resident_queue(
            resident_id, ACTOR, message_id="missing-input", before_message_id=entries[1]["user_message_id"],
        )
    assert resident_execution_work.read(record) == after_other_user_update


def test_absorb_persists_reference_message_without_history_copy(team, monkeypatch):
    """AbsorbContext 持久保存引用消息且不复制来源历史"""
    monkeypatch.setattr(agent_context_selection, "HOST_AGENTS_ROOT", team.owners.root / "agents")
    monkeypatch.setattr(agent_context_selection, "HOST_SKILLS_ROOT", team.owners.root / "skills")
    monkeypatch.setattr(agent_context_selection, "THIRD_PARTY_SKILLS_ROOT", team.owners.root / "skills" / "third-party")
    source_id, target_id = team.source["resident_id"], team.target["resident_id"]
    resident_lifecycle_domain.append_conversation_message(source_id, resident_input_queue_domain.build_message(
        "user", "PRIVATE_HISTORY_MUST_NOT_BE_EMBEDDED", ACTOR, message_id="msg_" + uuid.uuid4().hex,
    ))
    arguments = {"source_resident_id": source_id, "target_resident_id": target_id}
    key = "residual-absorb-canonical"
    result = instruction(team, "AbsorbContext", arguments, key=key)
    assert result.status == "committed", result.to_payload()
    edge = next(edge for edge in lineage_domain._read_lineage(team.lineage_id, actor=ACTOR)["edges"] if edge["type"] == "absorption")
    assert edge["absorption"]["status"] == "queued", edge["absorption"]
    message_id = edge["absorption"]["target_message_id"]
    message = resident_protocol.get_resident_message(target_id, ACTOR, message_id)
    encoded = json.dumps(message, ensure_ascii=False)
    assert source_id in encoded
    assert "PRIVATE_HISTORY_MUST_NOT_BE_EMBEDDED" not in encoded
    repeated = instruction(team, "AbsorbContext", arguments, key=key)
    assert repeated.status == "committed"
    absorption = next(edge for edge in lineage_domain._read_lineage(team.lineage_id, actor=ACTOR)["edges"] if edge["type"] == "absorption")["absorption"]
    assert absorption["run_count"] == 1
    assert absorption["target_message_id"] == message_id
    assert instruction(
        team,
        "AbsorbContext",
        arguments,
        actor="storage-outsider",
    ).status == "committed"
    assert resident_protocol.get_resident_state(target_id, ACTOR)["state"]["intake_state"] == "closed"


@pytest.fixture
def input_team(team, monkeypatch):
    monkeypatch.setattr(agent_context_selection, "HOST_AGENTS_ROOT", team.owners.root / "agents")
    monkeypatch.setattr(agent_context_selection, "HOST_SKILLS_ROOT", team.owners.root / "skills")
    monkeypatch.setattr(agent_context_selection, "THIRD_PARTY_SKILLS_ROOT", team.owners.root / "skills" / "third-party")
    return team


def queued_input_payload(team, *, activation="enqueue_and_trigger"):
    return {
        "resident_id": team.target["resident_id"],
        "activation_policy": activation,
        "message": {
            "message_id": "msg_" + uuid.uuid4().hex,
            "role": "user",
            "content_parts": [{"type": "text", "text": "Durable queued input"}],
        },
    }


@pytest.mark.parametrize("closed", [True, False])
def test_queued_input_ack_requires_readable_immutable_message_and_state(input_team, monkeypatch, closed):
    """排队输入接收后消息可读且内容不可变并可查询状态"""
    resident_id = input_team.target["resident_id"]
    if not closed:
        resident_protocol.change_resident_state(resident_id, ACTOR, state_domain="intake", transition="open")
    repository = resident_conversation_stream.get_repository()
    for index in range(2):
        payload = queued_input_payload(input_team)
        message_id = payload["message"]["message_id"]
        receipt = resident_protocol.deliver_resident_input(payload, ACTOR)
        stored = resident_protocol.get_resident_message(resident_id, ACTOR, message_id)
        assert receipt["accepted"] and receipt["message_id"] == message_id
        assert "message" not in receipt and "input_state" not in receipt
        resident_delivery_work.drain(resident_id)
        stored = resident_protocol.get_resident_message(resident_id, ACTOR, message_id)
        assert stored["content_parts"] == payload["message"]["content_parts"]
        started = not closed and index == 0
        assert ("activated_at" in stored) is started
        assert any(
            message["message_id"] == message_id
            for message in repository.read_messages(resident_id)
        ) is started
        assert resident_protocol.get_resident_input_state(resident_id, ACTOR, message_id)["state"] == (
            "running" if started else "queued"
        )
        record = resident_record_domain.find_resident(resident_id)
        job = resident_execution_work.read(record)
        assert len(resident_execution_work.normalized_queue(job)) == index
        events = repository.events_after(resident_id, 0)
        resident_protocol.deliver_resident_input(payload, ACTOR)
        assert resident_execution_work.read(record) == job
        assert repository.events_after(resident_id, 0) == events
        altered = deepcopy(payload)
        altered["message"]["content_parts"][0]["text"] = "Changed content"
        with pytest.raises(ValueError):
            resident_protocol.deliver_resident_input(altered, ACTOR)
        assert resident_protocol.get_resident_message(
            resident_id,
            "storage-outsider",
            message_id,
        ) == stored
        assert resident_protocol.get_resident_message(resident_id, ACTOR, message_id) == stored


@pytest.mark.parametrize("existing_queue", [False, True])
def test_queued_input_commit_failure_retries_durable_job_without_duplicate(input_team, monkeypatch, existing_queue):
    """排队输入提交失败时整体回滚且重试不生成重复项"""
    resident_id = input_team.target["resident_id"]
    if existing_queue:
        resident_protocol.deliver_resident_input(queued_input_payload(input_team), ACTOR)
    payload = queued_input_payload(input_team)
    message_id = payload["message"]["message_id"]
    repository = resident_conversation_stream.get_repository()
    original_accept = repository.accept_user_message
    record = resident_record_domain.find_resident(resident_id)
    before_job = resident_execution_work.read(record)
    before_events = repository.events_after(resident_id, 0)

    def unavailable_accept(*args, **kwargs):
        # Fail after the immutable input is written, inside the admission transaction.
        original_accept(*args, **kwargs)
        raise OSError("isolated Conversation commit failure")

    monkeypatch.setattr(repository, "accept_user_message", unavailable_accept)
    with pytest.raises(OSError, match="isolated Conversation commit failure"):
        resident_protocol.deliver_resident_input(payload, ACTOR)
    with pytest.raises(KeyError):
        resident_protocol.get_resident_message(resident_id, ACTOR, message_id)
    assert resident_execution_work.read(record) == before_job
    assert repository.events_after(resident_id, 0) == before_events
    monkeypatch.setattr(repository, "accept_user_message", original_accept)
    receipt = resident_protocol.deliver_resident_input(payload, ACTOR)
    stored = resident_protocol.get_resident_message(resident_id, ACTOR, message_id)
    assert receipt["accepted"] and receipt["message_id"] == message_id
    assert stored["content_parts"] == payload["message"]["content_parts"]
    assert resident_protocol.get_resident_input_state(resident_id, ACTOR, message_id)["state"] == "queued"
    job = resident_execution_work.read(record)
    candidates = [job, *resident_execution_work.normalized_queue(job)]
    assert len(candidates) == 1 + int(existing_queue)
    assert sum(item["user_message_id"] == message_id for item in candidates) == 1
    events = repository.events_after(resident_id, 0)
    resident_protocol.deliver_resident_input(payload, ACTOR)
    assert resident_execution_work.read(record) == job
    assert resident_protocol.get_resident_message(resident_id, ACTOR, message_id) == stored
    assert repository.events_after(resident_id, 0) == events


@pytest.mark.parametrize("closed", [True, False])
def test_bootstrap_admission_starts_unless_intake_is_closed(input_team, closed):
    """首次会话输入接收后仅在接收状态开放时启动"""
    resident_id = input_team.target["resident_id"]
    if not closed:
        resident_protocol.change_resident_state(resident_id, ACTOR, state_domain="intake", transition="open")
    record = resident_record_domain.find_resident(resident_id)
    record["session_state"] = "queued"
    resident_record_domain.save_resident_record(record)
    assert resident_record_domain.find_resident(resident_id)["session_state"] != "pending_create"
    payload = queued_input_payload(input_team)
    receipt = resident_protocol.deliver_resident_input(payload, ACTOR)
    job = resident_execution_work.read(record)
    assert job["bootstrap_session"] is True
    assert job["activation_policy"] == payload["activation_policy"]
    assert resident_execution_work.is_execution_held(job) is closed
    message_id = payload["message"]["message_id"]
    assert receipt["accepted"] and receipt["message_id"] == message_id
    assert resident_protocol.get_resident_message(resident_id, ACTOR, message_id)["content_parts"] == payload["message"]["content_parts"]
    resident_delivery_work.drain(resident_id)
    assert resident_protocol.get_resident_input_state(resident_id, ACTOR, message_id)["state"] == (
        "queued" if closed else "running"
    )


@pytest.mark.parametrize("mode", ["direct", "copy"])
def test_handoff_isa_prepares_durable_binding_and_idempotent_delivery(team, mode, monkeypatch):
    """PrepareWorkspaceHandoff 持久绑定目标且重放不发送消息"""
    def local_git_only(arguments, *args, **kwargs):
        assert arguments[0] == "git"
        assert arguments[1] == "-C"
        assert Path(arguments[2]).resolve().is_relative_to(team.owners.root)
        assert arguments[3] in {"rev-parse", "status", "branch", "rev-list", "log"}
        return LOCAL_POPEN(arguments, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", local_git_only)
    source = Path(team.source["default_cwd"])
    (source / "handoff.txt").write_text("original bytes")
    (source / ".env").write_text("private fixture only")
    before_owned = team.target["owned_workspace"]
    created = lineage_domain.create_handoff(team.lineage_id, {
        "source_resident_id": team.source["resident_id"], "target_resident_id": team.target["resident_id"],
        "items": [{"source": {"path": str(source)}, "transfer": {"mode": mode}}],
        "execute_now": False,
        "expected_revision": lineage_domain._read_lineage(team.lineage_id, actor=ACTOR)["revision"],
    }, ACTOR)
    handoff_id = created["handoff"]["id"]
    prepared = instruction(team, "PrepareWorkspaceHandoff", {"lineage_id": team.lineage_id, "handoff_id": handoff_id})
    assert prepared.status == "committed", prepared.to_payload()
    handoff = prepared.result
    assert handoff["status"] == "ready"
    output = Path(handoff["items"][0]["result"]["target_path"])
    assert (output / "handoff.txt").read_text() == "original bytes"
    record = resident_record_domain.find_resident(team.target["resident_id"])
    binding = next(binding for binding in record["workspace_bindings"] if Path(binding["path"]).resolve() == output.resolve())
    assert record["owned_workspace"] == before_owned
    assert record["default_cwd"] == team.target["default_cwd"]
    if mode == "direct":
        assert output.resolve() == source.resolve()
    else:
        assert output.resolve() != source.resolve()
        assert not (output / ".env").exists()
        (output / "handoff.txt").write_text("target change")
        assert (source / "handoff.txt").read_text() == "original bytes"
    replay = instruction(team, "PrepareWorkspaceHandoff", {"lineage_id": team.lineage_id, "handoff_id": handoff_id})
    assert replay.result["items"] == handoff["items"]
    # Preparing a handoff never sends the target a message.
    with state_store.get_lineage_data_store().database.read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM conversation_input_states").fetchone()[0] == 0


def test_handoff_isa_prepares_a_new_handoff_named_by_its_resident_pair(team, monkeypatch):
    """按 Resident 对准备交接时新建交接并共享来源的当前工作区

    PrepareWorkspaceHandoff 只给来源和目标 Resident 时，先建立这对端点的交接；没有交接项时直接共享来源当前工作目录所在的 Workspace，准备成功为 ready，再次准备复用同一条交接。

    :importance: P1
    :aspects: functional, behavior
    :design: decision_4637ca4a69e2, decision_7ef392b5f60d
    """
    def local_git_only(arguments, *args, **kwargs):
        assert arguments[:2] == ["git", "-C"]
        return LOCAL_POPEN(arguments, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", local_git_only)
    pair = {"lineage_id": team.lineage_id, "source_resident_id": team.source["resident_id"], "target_resident_id": team.target["resident_id"]}
    prepared = instruction(team, "PrepareWorkspaceHandoff", pair)
    assert prepared.status == "committed", prepared.to_payload().get("error")
    assert prepared.result["handoff_id"].startswith("handoff_") and prepared.result["status"] == "ready"
    # Without items the handoff shares the Workspace the source works in.
    [item] = prepared.result["items"]
    assert Path(item["result"]["target_path"]).resolve() == Path(team.source["default_cwd"]).resolve()
    # The pair now names that handoff; preparing it again reuses it.
    again = instruction(team, "PrepareWorkspaceHandoff", pair)
    assert again.result["handoff_id"] == prepared.result["handoff_id"]


def test_removed_approval_instruction_is_rejected_without_mutation(team):
    """已移除的 ApproveWorkspaceHandoff 指令拒绝执行且无变更"""
    before = lineage_domain._read_lineage(team.lineage_id, actor=ACTOR)
    result = instruction(team, "ApproveWorkspaceHandoff", {"handoff_id": "handoff_missing"})
    assert result.status == "failed" and result.error["code"] == "instruction.unknown"
    assert lineage_domain._read_lineage(team.lineage_id, actor=ACTOR) == before




def test_project_move_updates_canonical_member_paths_and_keeps_single_root(team):
    """迁移项目更新权威成员路径并保留唯一目录根"""
    next_project = "ResidualTargetProject"
    project.register_project({"id": next_project, "description": "Move fixture", "project_type": "other", "initial_admin_ids": [ACTOR]}, actor=ACTOR, access_store=team.owners.access)
    adapter = team.owners.index.adapter_layer.adapters["project"]
    records, source_state = adapter.scan()
    team.owners.index_store.publish_snapshot("project", records, source_state)
    root = lineage_domain._lineage_dir(team.lineage_id)
    original = root.resolve()
    (original / "preserve.txt").write_text("user content")
    current = lineage_domain._read_lineage(team.lineage_id, actor=ACTOR)
    lineage_domain.update_lineage_metadata(team.lineage_id, {
        "project_id": next_project, "expected_revision": current["revision"],
    }, ACTOR)
    destination = team.owners.root / "Projects" / next_project / "knowledge_base" / "agents" / team.lineage_id
    assert root.resolve() == destination
    assert original == root and root.is_symlink() or not original.exists()
    assert (destination / "preserve.txt").read_text() == "user content"
    for resident_id in (team.source["resident_id"], team.target["resident_id"]):
        record = resident_record_domain.find_resident(resident_id)
        assert record["lineage_id"] == team.lineage_id
        assert record["project_id"] == next_project
        assert Path(record["default_cwd"]).resolve().is_relative_to(destination)


def test_missing_folder_rebuild_uses_canonical_members_and_preserves_user_files(team):
    """目录丢失时按权威成员重建结构且保留已有用户文件"""
    lineage = lineage_domain._read_lineage(team.lineage_id, actor=ACTOR)
    canonical = lineage_domain._lineage_dir(team.lineage_id)
    actual = canonical.resolve()
    (actual / "user.txt").write_text("cannot reconstruct this file")
    backup = team.owners.root / "saved-original"
    actual.rename(backup)
    lineage_domain._ensure_lineage_folder_files(lineage)
    for record in resident_catalog.get_catalog().for_lineage(team.lineage_id):
        resident_workspace_domain.ensure_resident_workspace(record)
    assert (canonical / "README.md").is_file()
    assert (canonical / "runtime").is_dir()
    assert not (canonical / "user.txt").exists()
    assert (backup / "user.txt").read_text() == "cannot reconstruct this file"
    for resident_id in (team.source["resident_id"], team.target["resident_id"]):
        folder = canonical / "residents" / resident_id
        assert (folder / "resident.md").is_file()
        assert (folder / "workspace").is_dir()
    (canonical / "LINEAGE.md").write_text("human notes")
    lineage_domain._ensure_lineage_folder_files(lineage)
    assert (canonical / "LINEAGE.md").read_text() == "human notes"


def test_failed_handoff_preparation_is_reported_as_a_failed_status(team, monkeypatch):
    """交接准备失败时返回并持久保存 failed 状态"""
    from management_portal.society_service import lineage_handoff_preparation

    source = Path(team.source["default_cwd"])
    created = lineage_domain.create_handoff(team.lineage_id, {
        "source_resident_id": team.source["resident_id"], "target_resident_id": team.target["resident_id"],
        "items": [{"source": {"path": str(source)}, "transfer": {"mode": "copy"}}],
        "execute_now": False,
        "expected_revision": lineage_domain._read_lineage(team.lineage_id, actor=ACTOR)["revision"],
    }, ACTOR)

    def unavailable(*_args, **_kwargs):
        raise OSError("fixture copy target unavailable")

    monkeypatch.setattr(lineage_handoff_preparation, "prepare_items", unavailable)
    prepared = instruction(team, "PrepareWorkspaceHandoff", {"lineage_id": team.lineage_id, "handoff_id": created["handoff"]["id"]})
    assert prepared.status == "committed", prepared.to_payload()
    assert prepared.result["status"] == "failed"
    got = instruction(team, "GetWorkspaceHandoff", {"handoff_id": created["handoff"]["id"]})
    assert got.result["status"] == "failed" and "fixture copy target unavailable" in got.result["error"]
