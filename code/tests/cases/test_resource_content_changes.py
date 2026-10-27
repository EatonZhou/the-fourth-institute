""":importance: P2"""
from management_portal.execution import event_projection as execution_events
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import socketserver
import threading

import pytest

from management_common.execution_association import execution_scope
from management_common.resource_turn_artifacts import content_part
from management_common.provider_turn_artifacts import review_target
from management_portal.resources import content_changes
from management_portal.resources.content_changes import ContentChangeStore, capture_content_changes, record_text_change
from management_portal.resources.service import ResourceService
from management_portal.resources.errors import ResourceVersionConflict
from management_portal.resources.providers.knowledge import _update_knowledge
from management_portal.resources.server import ResourceServiceServer
from management_portal.resources.transport import ResourceServiceClient
from management_portal.society_service import resident_conversation_stream, resident_resource_changes, resident_execution_work
from management_portal.society_service import resident_execution_domain
from tests.cases.test_knowledge_versioning import KNOWLEDGE_ID, _markdown
from tests.cases.test_resident_execution_protocol import resident_record
from knowledge_board_lan import knowledge_backend


ACTOR = "resident_300dead2872a"


@pytest.fixture
def receipt_store(monkeypatch):
    store = ContentChangeStore()
    store.executions = {execution: {"request": {"agent_id": actor}, "status": "running"}
                        for execution, actor in (("execution-resource-test", ACTOR), ("execution-one", ACTOR),
                                                 ("execution-two", "resident_0123456789ab"))}
    execution_service = Mock()
    execution_service.status.side_effect = lambda execution_id, **kwargs: store.executions[execution_id]
    monkeypatch.setattr(content_changes, "get_execution_service", lambda: execution_service)
    return store


def test_real_knowledge_write_captures_versions_conflict_and_parallel_attribution(receipt_store, tmp_path, monkeypatch):
    """知识写入保留连续版本、冲突检查与并发执行归属"""
    primary = tmp_path / "item.md"
    primary.write_text(_markdown(), encoding="utf-8")
    record = {"address": KNOWLEDGE_ID, "kb_id": KNOWLEDGE_ID, "type": "thinking",
              "path": str(primary), "selected_path": str(primary), "selected_file_id": "file-primary"}
    monkeypatch.setattr(knowledge_backend, "resolve_item_address", lambda *a, **k: record)
    def update(actor, execution):
        with execution_scope(execution), capture_content_changes(actor=actor, resource_id=KNOWLEDGE_ID):
            return _update_knowledge(KNOWLEDGE_ID, {"text": _markdown() + execution}, actor=actor)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(update, ACTOR, "execution-one"), pool.submit(update, "resident_0123456789ab", "execution-two")]
        for future in futures:
            future.result()
    first = receipt_store.for_execution("execution-one", ACTOR)
    second = receipt_store.for_execution("execution-two", "resident_0123456789ab")
    changes = sorted(first + second, key=lambda c: c["after"]["version"]["number"])
    assert len(first) == len(second) == 1
    assert changes[0]["before"]["text"] == _markdown()
    assert changes[0]["after"] == changes[1]["before"]
    assert changes[-1]["after"]["text"] == primary.read_text(encoding="utf-8")
    assert changes[-1]["after"]["version"]["number"] == 5
    with execution_scope("execution-resource-test"), capture_content_changes(actor=ACTOR, resource_id=KNOWLEDGE_ID):
        with pytest.raises(ResourceVersionConflict):
            _update_knowledge(KNOWLEDGE_ID, {"text": "old", "expected_version": 3}, actor=ACTOR)
    assert not receipt_store.for_execution("execution-resource-test", ACTOR)
    assert not ResourceService().execution_content_changes("execution-one", actor="resident_0123456789ab")


def test_capture_requires_verified_execution_and_resets_between_requests(receipt_store):
    """内容变更捕获核实执行归属并在请求之间重置"""
    with execution_scope("execution-resource-test"):
        with capture_content_changes(actor="resident_0123456789ab", resource_id=KNOWLEDGE_ID) as capture:
            assert capture["unconfirmed_reason"] == "execution_actor_mismatch"
            record_text_change(KNOWLEDGE_ID, "item.md", "old", "foreign association")
        assert not receipt_store.for_execution("execution-resource-test", "resident_0123456789ab")
        receipt_store.executions["execution-resource-test"]["status"] = "completed"
        with capture_content_changes(actor=ACTOR, resource_id=KNOWLEDGE_ID) as capture:
            assert capture["unconfirmed_reason"] == "execution_not_running"
            record_text_change(KNOWLEDGE_ID, "item.md", "old", "expired association")
        assert not receipt_store.for_execution("execution-resource-test", ACTOR)
        receipt_store.executions["execution-resource-test"]["status"] = "running"
        with capture_content_changes(actor=ACTOR, resource_id=KNOWLEDGE_ID):
            record_text_change(KNOWLEDGE_ID, "item.md", "old", "new")
            record_text_change(KNOWLEDGE_ID, "item.md", "new", "new")
    record_text_change(KNOWLEDGE_ID, "item.md", "new", "not an execution")
    changes = receipt_store.for_execution("execution-resource-test", ACTOR)
    assert len(changes) == 1
    assert changes[0]["before"]["text"] == "old"
    assert changes[0]["after"]["text"] == "new"


def test_receipt_failure_does_not_claim_resource_write_failed(receipt_store, monkeypatch):
    """变更回执记录失败时单独累计错误"""
    monkeypatch.setattr(ContentChangeStore, "append", Mock(side_effect=OSError("disk full")))
    with execution_scope("execution-resource-test"), capture_content_changes(actor=ACTOR, resource_id=KNOWLEDGE_ID) as capture:
        record_text_change(KNOWLEDGE_ID, "item.md", "before", "committed")
    assert capture["errors"] == 1
    assert capture["recorded"] == 0


def test_resource_receipts_become_replayable_chat_artifact_and_review(receipt_store, resident_record, monkeypatch):
    """资源回执生成可重放会话产物并限制评审归属"""
    resident_id = resident_record["resident_id"]
    execution_id = "execution-resource-test"
    receipt_store.executions[execution_id]["request"]["agent_id"] = resident_id
    with execution_scope(execution_id), capture_content_changes(actor=resident_id, resource_id=KNOWLEDGE_ID):
        record_text_change(KNOWLEDGE_ID, "item.md", "old\n", "new\n", before_version={"number": 3}, after_version={"number": 4})
    repository = resident_conversation_stream.get_repository()
    service = Mock()
    service.execution_content_changes.side_effect = lambda execution, actor: receipt_store.for_execution(execution, actor)
    monkeypatch.setattr(resident_resource_changes, "get_resource_service", lambda: service)
    outputs = resident_resource_changes.display_outputs(repository, resident_id, execution_id)
    assert outputs == resident_resource_changes.display_outputs(repository, resident_id, execution_id)
    part = outputs[0]["content_parts"][0]
    assert "-old\n+new" in part["diff"]
    assert repository.execution_content_parts(resident_id, execution_id, [{**part, "diff": "forged"}]) == [part]
    message = {"message_id": outputs[0]["output_id"], "role": "agent", "created_at": outputs[0]["created_at"], "content_parts": [part]}
    repository.append_message(resident_id, message)
    repository.append_message(resident_id, message)
    # The message is a Segment of its own; its part names the artifact Segment.
    stored = repository.agent_data.chat_message(resident_id, message["message_id"])
    assert stored["content_parts"][0]["segment_id"] == part["segment_id"] != stored["segment_id"]
    source = repository.agent_data.operation_record(part["segment_id"])
    assert "-old\n+new" in review_target(source, resident_id)["instructions"]
    with pytest.raises(ValueError):
        review_target(source, "resident_ffffffffffff")
    with pytest.raises(ValueError):
        repository.execution_content_parts(resident_id, "another-execution", [part])


def test_resource_receipts_project_only_at_terminal_without_polling(receipt_store, resident_record, monkeypatch):
    """资源回执仅在执行终结时读取并投影为回复"""
    resident_id = resident_record["resident_id"]
    execution_id, job_id = "execution-resource-test", "job-different-from-execution"
    receipt_store.executions[execution_id]["request"]["agent_id"] = resident_id
    with execution_scope(execution_id), capture_content_changes(actor=resident_id, resource_id=KNOWLEDGE_ID):
        record_text_change(KNOWLEDGE_ID, "item.md", "before", "after")
    service = Mock()
    service.execution_content_changes.side_effect = lambda execution, actor: receipt_store.for_execution(execution, actor)
    monkeypatch.setattr(resident_resource_changes, "get_resource_service", lambda: service)
    monkeypatch.setattr(execution_events, "execution_projection_from_execution", lambda *a, **kw: {"outputs": []})
    resident_execution_work.write(resident_record, { "execution_id": execution_id, "actor": "CASE-A", "user_message_id": "msg-source", "submitted_at": "2026-10-14T00:00:00Z"})
    resident_execution_domain._project_resident_execution_snapshot(resident_id,  execution_id, {"status": "running"})
    service.execution_content_changes.assert_not_called()
    resident_execution_domain._project_resident_execution_snapshot(resident_id,  execution_id, {"status": "completed"})
    service.execution_content_changes.assert_not_called()
    execution_service = Mock()
    execution_service.wait.return_value = {"status": "completed"}
    resident_execution_domain.wait_for_resident_execution(
        execution_service, resident_id=resident_id,
        execution_id=execution_id, actor="CASE-A", user_message_id="msg-source",
    )
    service.execution_content_changes.assert_called_once_with(execution_id, actor=resident_id)
    repository = resident_conversation_stream.get_repository()
    part = resident_resource_changes.display_outputs(repository, resident_id, execution_id)[0]
    message = repository.message(resident_id, part["output_id"])
    assert message["reply_to_message_id"] == "msg-source"
    assert message["content_parts"][0]["type"] == "resource_changes"
    resident_execution_domain._project_resident_execution_snapshot(resident_id,  execution_id, {"status": "completed"})
    service.execution_content_changes.assert_called_once()


def test_display_budget_does_not_truncate_stored_snapshots(receipt_store):
    """展示差异截断时仍完整保存内容快照"""
    with execution_scope("execution-resource-test"), capture_content_changes(actor=ACTOR, resource_id=KNOWLEDGE_ID):
        record_text_change(KNOWLEDGE_ID, "item.md", "old", "x" * 300000)
    changes = receipt_store.for_execution("execution-resource-test", ACTOR)
    part = content_part(changes, execution_id="execution-resource-test", actor=ACTOR)
    assert len(changes[0]["after"]["text"]) == 300000
    assert len(part["diff"]) == 262144 and part["diff_truncated"]
    with pytest.raises(ValueError, match="truncated"):
        review_target({"created_by_agent_id": ACTOR, "content": part}, ACTOR)


def test_signed_resource_update_propagates_execution_and_returns_own_receipts(receipt_store, tmp_path, monkeypatch):
    """签名资源更新传递执行标识且仅返回本执行者回执"""
    monkeypatch.setenv("PORTAL_RESOURCE_SERVICE_PROCESS", "1")
    service = ResourceService()
    monkeypatch.setattr(service, "_existing_identity", lambda resource_id, *a, **k: service.validate_id(resource_id))
    monkeypatch.setattr(service, "_require_mutation_actor", Mock())
    monkeypatch.setattr(service, "_notify_mutation", Mock(return_value={}))
    def update(resource_id, payload, **context):
        record_text_change(resource_id, "item.md", "before", payload["text"])
        return {"resource_id": resource_id, "text": payload["text"]}
    service.register_mutation_provider("thinking", update=update)
    server = ResourceServiceServer(tmp_path / "resource.sock", service=service, publisher=Mock())
    thread = threading.Thread(target=socketserver.BaseServer.serve_forever, args=(server,), kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        client = ResourceServiceClient(server.socket_path, timeout=2)
        with execution_scope("execution-resource-test"):
            result = client.update(KNOWLEDGE_ID, {"text": "after"}, actor=ACTOR)
        assert result["text"] == "after"
        changes = client.execution_content_changes("execution-resource-test", actor=ACTOR)
        assert changes[0]["after"]["text"] == "after"
        assert not client.execution_content_changes("execution-resource-test", actor="resident_ffffffffffff")
        assert not client.execution_content_changes("another-execution", actor=ACTOR)
        with execution_scope("execution-resource-test"):
            result = client.update(KNOWLEDGE_ID, {"text": "independent write"}, actor="resident_ffffffffffff")
        assert result["text"] == "independent write"
        assert result["change_recording"] == {"status": "unconfirmed", "reason": "execution_actor_mismatch"}
        assert not client.execution_content_changes("execution-resource-test", actor="resident_ffffffffffff")
        assert len(client.execution_content_changes("execution-resource-test", actor=ACTOR)) == 1
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_resident_cli_and_isa_kernel_carry_the_execution(receipt_store, monkeypatch):
    """Resident CLI 与 ISA 内核传递执行标识并清理请求上下文

    Resident 命令行客户端把当前执行标识放进请求头；ISA 内核在一次指令内让资源写入看到该执行标识，下一次没有执行标识的指令看不到它，指令结束后上下文清空。
    """
    from scripts import resident_hatg_instruction as cli
    from management_common.execution_association import current_execution_id
    from management_portal.hatg import routing
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.resources import isa as resource_isa

    monkeypatch.setenv("PORTAL_EXECUTION_ID", "execution-resource-test")
    monkeypatch.setattr(cli, "_token", lambda _: "synthetic-test-only")
    request = cli._request(ACTOR, {"instruction": "UpdateResource", "arguments": {}}, accept="application/json")
    header = dict(request.header_items())
    assert header["X-portal-execution-id"] == "execution-resource-test"
    assert not any(key.lower() == "x-archive-execution-id" for key in header)

    observed = []
    service = Mock()
    service.update.side_effect = lambda *a, **kw: observed.append(current_execution_id()) or {"version": "2"}
    monkeypatch.setattr(resource_isa, "_service", lambda: service)
    call = InstructionCall.from_payload({"instruction": "UpdateResource", "arguments": {
        "resource_id": KNOWLEDGE_ID, "changes": {"text": "after"}}, "idempotency_key": "first"})
    routing.execute(call, InstructionContext(caller=ACTOR, entry="cli", execution_id="execution-resource-test")).raise_for_error()
    second = InstructionCall.from_payload({"instruction": "UpdateResource", "arguments": {
        "resource_id": KNOWLEDGE_ID, "changes": {"text": "next request"}}, "idempotency_key": "second"})
    routing.execute(second, InstructionContext(caller=ACTOR, entry="cli")).raise_for_error()
    assert observed == ["execution-resource-test", ""]
    assert current_execution_id() == ""


def test_skill_and_experiment_writers_capture_exact_content(receipt_store, tmp_path, monkeypatch):
    """Skill 与实验写入捕获准确的前后内容"""
    from skill_board_lan import skill_backend
    from management_portal.resources.providers import experiment

    skill_id = "skill_0123456789ab"
    path = tmp_path / "SKILL.md"
    path.write_text("old skill", encoding="utf-8")
    monkeypatch.setattr(skill_backend, "_resolve_skill_address", lambda *a, **kw: (skill_id, path))
    monkeypatch.setattr(skill_backend, "_path_is_allowed", lambda *a, **kw: True)
    with execution_scope("execution-resource-test"), capture_content_changes(actor=ACTOR, resource_id=skill_id):
        status, _ = skill_backend.save_address_text(skill_id, "new skill", actor=ACTOR)
    assert status == 200
    experiment_id = "experiment_0123456789ab"
    document = tmp_path / "knowledge_base" / "experiment" / "sample" / "README.md"
    document.parent.mkdir(parents=True)
    document.write_text("# Old experiment\n", encoding="utf-8")
    monkeypatch.setattr(experiment, "_by_resource_id", lambda _: ("AI员工", document, {"kb_id": experiment_id}, "# Old experiment\n"))
    monkeypatch.setattr(experiment, "_project_root", lambda _: tmp_path)
    with execution_scope("execution-resource-test"), capture_content_changes(actor=ACTOR, resource_id=experiment_id):
        experiment.update_experiment(experiment_id, {"body": "New result"}, actor=ACTOR)
    changes = receipt_store.for_execution("execution-resource-test", ACTOR)
    assert [c["resource_id"] for c in changes] == [skill_id, experiment_id]
    assert changes[0]["before"]["text"] == "old skill"
    assert changes[0]["after"]["text"] == path.read_text(encoding="utf-8")
    assert changes[1]["before"]["text"] == "# Old experiment\n"
    assert changes[1]["after"]["text"] == document.read_text(encoding="utf-8")


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
