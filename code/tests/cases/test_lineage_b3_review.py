"""Version preconditions stay in short commits; delivery profiles follow inputs."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from management_portal.society_service import lineage_domain as graphs
from management_portal.society_service import lineage_program_runtime as runtime
from management_portal.society_service import resident_conversation_stream, resident_execution_domain
from management_portal.society_service.state_store import get_lineage_data_store
from management_portal.society_service.isa import common
from tests.cases.isa_test_support import ACTOR
from tests.cases.test_principal_hierarchy import family
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


def _inv(lineage_id, revision):
    return SimpleNamespace(arguments={"lineage_id": lineage_id}, expected_version=revision,
                           expected_version_for=lambda lid: revision)


def test_versioned_multistep_external_stage_allows_other_lineage_writes(future_creation_store):
    """带版本前置条件的多步指令在外部阶段不锁 Lineage 定义，其他连接可以写入"""
    lineage_id = family("Unlocked external stage")["lineage_id"]
    store = get_lineage_data_store()
    revision = graphs.get_lineage(lineage_id, ACTOR)["revision"]
    def external_stage():
        # A different connection can acquire the real definition row immediately.
        def probe(connection):
            connection.execute('SELECT 1 FROM lineages WHERE lineage_id=%s FOR NO KEY UPDATE NOWAIT', (lineage_id,))
        with store.database.transaction() as connection:
            probe(connection)
        # Also prove that ISA does not retain in-process Lineage authorities.
        graphs.update_lineage_metadata(lineage_id, {"description": "external progress"}, ACTOR)

    def multistep(_):
        graphs.update_lineage_metadata(lineage_id, {"title": "first commit"}, ACTOR)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(external_stage).result(timeout=5)
        graphs.update_lineage_metadata(lineage_id, {"title": "second commit"}, ACTOR)
    common.guard_definition_handler("ForkResident", multistep)(_inv(lineage_id, revision))
    assert graphs.get_lineage(lineage_id, ACTOR)["description"] == "external progress"


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_ordinary_event_profile_removed_after_terminal_notification(future_creation_store, monkeypatch, status):
    """普通事件的投递配置在输入完成、失败或取消的通知后删除"""
    root = family("Ordinary event")
    lid, rid = root["lineage_id"], root["resident_id"]
    mid = "msg_terminal_profile"
    runtime.record_delivery_profile(lid, mid, {"isolated": True})
    resident_conversation_stream.get_repository().set_input_state(rid, mid, status)
    monkeypatch.setattr(resident_execution_domain.resident_terminal_port, "report", lambda *a, **k: None)
    resident_execution_domain._notify_lineage_message_terminal(rid, ACTOR, mid, terminal_status=status, lineage_id=lid)
    assert runtime.delivery_profile(lid, mid) == {}


def test_new_profile_sweeps_only_terminal_inputs(future_creation_store):
    """记录新投递配置时只清理已结束输入的配置，运行中、排队和未知状态的保留"""
    root = family("Delivery profile sweep")
    lid, rid = root["lineage_id"], root["resident_id"]
    repo = resident_conversation_stream.get_repository()
    for status in ("completed", "failed", "cancelled", "running", "queued", "missing"):
        mid = "msg_profile_" + status
        runtime.record_delivery_profile(lid, mid, {"isolated": True})
        if status != "missing":
            repo.set_input_state(rid, mid, status)
    runtime.record_delivery_profile(lid, "msg_profile_new", {"isolated": True})
    profiles = runtime.read_runtime(lid)["delivery_profiles"]
    assert set(profiles) == {"msg_profile_running", "msg_profile_queued", "msg_profile_missing", "msg_profile_new"}


def test_aborted_commit_does_not_consume_version_precondition(future_creation_store):
    """带版本前置条件的指令中被回滚的写入不占用前置条件，遇并发修改仍报冲突"""
    from management_portal.hatg.contracts import InstructionError
    lineage_id = family("Aborted version check")["lineage_id"]
    store = get_lineage_data_store()
    revision = graphs.get_lineage(lineage_id, ACTOR)["revision"]
    def handler(_):
        def abort():
            graphs.update_lineage_metadata(lineage_id, {"title": "rolled back"}, ACTOR)
            raise RuntimeError("abort")
        with pytest.raises(RuntimeError, match="abort"):
            store.transaction(abort)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(graphs.update_lineage_metadata, lineage_id, {"title": "concurrent"}, ACTOR).result(timeout=5)
        graphs.update_lineage_metadata(lineage_id, {"title": "must reject"}, ACTOR)
    with pytest.raises(InstructionError) as error:
        common.guard_definition_handler("ForkResident", handler)(_inv(lineage_id, revision))
    assert error.value.code == "instruction.conflict"
    assert graphs.get_lineage(lineage_id, ACTOR)["title"] == "concurrent"


@pytest.mark.parametrize("instruction", ["PrepareWorkspaceHandoff", "RunScript"])
def test_runtime_operations_do_not_claim_definition_preconditions(instruction):
    """PrepareWorkspaceHandoff 和 RunScript 不算定义修改指令，不占用定义版本前置条件"""
    from management_portal.hatg.lineage_versions import DEFINITION_MUTATIONS
    assert instruction not in DEFINITION_MUTATIONS
