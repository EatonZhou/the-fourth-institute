"""Application Events retain their service identity without lending it out."""
import pytest

from management_common.principal import OperationIdentity
from management_portal.society_service import lineage_instruction_context as contexts
from management_portal.society_service import lineage_primitives as primitives
from management_portal.hatg.kernel import _portal_system_call
from management_portal.hatg.contracts import InstructionContext


def test_explicit_portal_event_keeps_service_context_and_trace():
    """portal 显式发起的事件保留服务调用者、lineage 入口和 trigger_path_id"""
    event = primitives.make_event("manual.triggered", payload={"trigger_path_id": "trigger_path_0123456789ab"},
                                 identity=OperationIdentity.for_actor("portal").to_dict())
    context = contexts.event_context(event, "portal")
    assert context.caller == "portal" and context.entry == "lineage"
    assert context.trigger_path_id == "trigger_path_0123456789ab"
    assert _portal_system_call(context)


def test_other_service_cannot_acquire_portal_system_entry():
    """其他服务发起的事件无法取得 Portal 系统入口"""
    event = primitives.make_event("manual.triggered",
                                 identity=OperationIdentity.for_actor("ai-socket-relay").to_dict())
    with pytest.raises(PermissionError):
        contexts.event_context(event, "ai-socket-relay")


def test_resident_event_forwarded_by_portal_acts_as_the_resident():
    """portal 转发的 Resident 事件以该 Resident 身份执行，不取得 Portal 系统入口"""
    resident = "resident_0123456789ab"
    event = primitives.make_event("manual.triggered", payload={"trigger_path_id": "trigger_path_0123456789ab"},
                                 identity=OperationIdentity.for_actor(resident, initiated_by="portal").to_dict())
    context = contexts.event_context(event, resident)
    assert context.caller == resident
    assert not _portal_system_call(context)


def test_event_context_rejects_service_identity_actor_mismatch():
    """事件身份为 portal 而执行者是 Resident 时拒绝建立事件上下文"""
    event = primitives.make_event("manual.triggered", identity=OperationIdentity.for_actor("portal").to_dict())
    with pytest.raises(PermissionError):
        contexts.event_context(event, "resident_0123456789ab")


@pytest.mark.parametrize("caller,entry,trace", [
    ("portal", "web", "trigger_path_0123456789ab"),
    ("portal", "lineage", ""),
    ("resident_0123456789ab", "lineage", "trigger_path_0123456789ab"),
    ("ai-socket-relay", "lineage", "trigger_path_0123456789ab"),
])
def test_only_portal_application_path_retains_service_authority(caller, entry, trace):
    """调用者不是 portal、入口不是 lineage 或缺少触发路径时不具备 Portal 系统权限"""
    assert not _portal_system_call(InstructionContext(caller=caller, entry=entry, trigger_path_id=trace))
