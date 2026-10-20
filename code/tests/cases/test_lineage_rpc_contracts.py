from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import inspect

import pytest

from management_portal.society_service import contracts, server
from management_portal.society_service.result_contracts import (
    ResultContractError, validate_result,
)
from management_portal.society_service.lineage_program import empty_program
from management_portal.society_service.lineage_graph_state import empty_state


def test_operation_catalog_is_complete_and_binds_real_domain_signatures():
    """SocietyService 操作目录覆盖每个领域方法并绑定真实签名，未注册操作被拒绝"""
    server._validate_domain_handlers()
    for domain, operations in contracts.DOMAIN_OPERATIONS.items():
        assert set(operations) == contracts.DOMAIN_METHODS[domain]
        for operation in operations.values():
            assert operation.mode in {"read", "write", "dispatch"}
            assert operation.authority
            assert bool(operation.target) == (operation.scope != "owner")
    for name in ("get_batch", "get_invocation"):
        assert contracts.operation_contract("service_invocation", name).mode == "write"
    assert contracts.operation_contract("resident", "list_resident_forward_targets").mode == "read"
    with pytest.raises(ValueError, match="unsupported"):
        contracts.operation_contract("resident", "get_unregistered")


# Instruction handlers call their domain modules directly, so Domain RPC writes
# only serve other services' instruction handlers and internal work that has no
# instruction. A write that duplicates an ISA instruction would bypass its
# policy check; adding one here means first checking that no instruction does it.
DOMAIN_RPC_WRITES = {
    "resident": {"cancel_resident_service_input", "request_resident_summary_rebuild", "submit_routing_eval_correction", "set_resident_embeddings_enabled"},
    "workspace": {"save_workspace_attachment", "save_workspace_pasted_image"},
    "project": {"delete_project"},
    "lineage": {"create_lineage_resident", "ensure_personal_ama", "delete_personal_ama", "ensure_personal_assistant",
                "provision_project_principal", "retry_lineage_projections"},
    "service_invocation": {
        "prepare_batches", "submit_batch", "get_batch", "get_invocation", "cancel_invocation", "fail_invocation",
    },
}


def test_domain_rpc_writes_are_the_reviewed_set_without_instruction_duplicates():
    """Domain RPC 的写方法只限已审阅的集合，不重复 ISA 指令

    :design: decision_d918ebebc062
    """
    writes = {
        domain: {name for name, operation in operations.items() if operation.mode == "write"}
        for domain, operations in contracts.DOMAIN_OPERATIONS.items()
    }
    assert {domain: names for domain, names in writes.items() if names} == DOMAIN_RPC_WRITES


def test_scope_uses_bound_named_target_and_ignores_unrelated_ids(monkeypatch):
    """RPC 作用域按绑定的具名 resident_id 查找 Lineage，忽略位置相同的其他 ID"""
    lookup = Mock(return_value="lineage_target")
    monkeypatch.setattr(server, "_domain_module", lambda domain: SimpleNamespace(resident_aggregate_lineage_id=lookup))
    def handler(actor, resident_id):
        pass
    signature = inspect.signature(handler)
    operation = contracts.operation_contract("resident", "get_resident")
    for bound in (
        signature.bind("resident_misleading_actor", "resident_target"),
        signature.bind(resident_id="resident_target", actor="resident_misleading_actor"),
    ):
        assert server._operation_lineage_id(operation, bound) == "lineage_target"
    assert lookup.call_args.args == ("resident_target",)
    with pytest.raises(ValueError, match="unsupported"):
        contracts.operation_contract("hatg", "execute_portal_payload")


def test_rpc_implementation_is_explicit_and_never_falls_back(monkeypatch):
    """RPC 操作只从声明的实现模块取处理函数，找不到时直接报错不回退"""
    declared = contracts.operation_contract("resident", "get_resident")
    importer = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(server, "import_module", importer)
    with pytest.raises(AttributeError):
        server._domain_handler("resident", "get_resident")
    importer.assert_called_once_with(declared.implementation)


def test_domain_ports_delegate_to_bound_hosts():
    """Resident 领域端口未绑定时报错，绑定后把调用原样转给宿主"""
    from management_portal.society_service.domain_ports import _ResidentDomainPort, _ResidentLineageHostPort

    cases = (
        (_ResidentDomainPort, "resolve_resident_identity", ("id", "sample_admin")),
        (_ResidentLineageHostPort, "lineage_access_projection", ("id",)),
    )
    for port_type, operation, args in cases:
        port = port_type()
        with pytest.raises(RuntimeError, match="not configured"):
            getattr(port, operation)
        with pytest.raises(ValueError, match="required"):
            port.bind(None)
        target = Mock(return_value={"ok": True})
        port.bind(SimpleNamespace(**{operation: target}))
        assert getattr(port, operation)(*args) == {"ok": True}
        target.assert_called_once_with(*args)


def test_project_domain_port_exposes_only_project_selection_validation():
    """Project 领域端口只暴露已有 Project 选择校验，不转发删除等其他方法"""
    from management_portal.society_service.domain_ports import _ProjectDomainPort

    port = _ProjectDomainPort()
    with pytest.raises(RuntimeError, match="not configured"):
        port.validate_existing_project_selection({"project_id": "project_1"})
    with pytest.raises(ValueError, match="required"):
        port.bind(None)

    target = Mock(return_value={"ok": True})
    unrelated = Mock()
    port.bind(SimpleNamespace(
        validate_existing_project_selection=target,
        delete_empty_project=unrelated,
    ))
    payload = {"project_id": "project_1"}
    assert port.validate_existing_project_selection(payload) == {"ok": True}
    target.assert_called_once_with(payload)
    with pytest.raises(AttributeError):
        port.delete_empty_project("project_1")
    unrelated.assert_not_called()


def test_structured_errors_survive_rpc_and_map_to_the_isa_vocabulary():
    """结构化错误经 RPC 往返不变并映射到 ISA 错误码，格式非法时报 contract.invalid_error"""
    from management_common.operation_errors import OperationError, failure_payload
    from management_common.projection_receipts import CommittedProjectionError
    from management_portal.hatg.contracts import ERROR_STATUS, InstructionError, normalize_error
    from management_portal.society_service.errors import LineageConflictError, SocietyServiceBusy
    from management_portal.society_service.transport import SocietyServiceClient

    for original, code in (
        (LineageConflictError("deleted by another instruction"), "instruction.conflict"),
        (CommittedProjectionError([{"committed_revision": 3, "projection": "resource"}]), "instruction.projection_pending"),
        (SocietyServiceBusy("response timed out"), "instruction.owner_unavailable"),
        (OperationError("denied", code="authorization.denied", http_status=403, commit_state="not_committed"),
         "instruction.forbidden"),
        (InstructionError("instruction.projection_pending", "recover projection", http_status=503,
                          details={"domain_committed": True, "retryable": True,
                                   "retry": "same_instruction_same_idempotency_key"}),
         "instruction.projection_pending"),
    ):
        encoded = server._error_payload(original)
        assert "type" not in encoded
        with pytest.raises(OperationError) as remote:
            SocietyServiceClient._raise_remote_error(encoded)
        assert failure_payload(remote.value) == encoded
        normalized = normalize_error(remote.value)
        assert normalized.code == code and normalized.http_status == ERROR_STATUS[code]
        assert normalized.commit_state == encoded["commit_state"]
        if code == "instruction.projection_pending":
            assert encoded["commit_state"] == "committed"
        if isinstance(original, InstructionError):
            assert encoded["retryable"] is True
    assert failure_payload(TypeError("implementation defect"))["http_status"] == 500
    for invalid in ({"type": "ValueError", "message": "old wire"},
                    {**encoded, "commit_state": []},
                    {**encoded, "code": "contract.invalid_result", "details": {}},
                    {**encoded, "code": "projection.pending", "details": {}},
                    {**encoded, "code": "projection.pending", "details": {"projection_failures": [1]}}):
        with pytest.raises(OperationError) as caught:
            SocietyServiceClient._raise_remote_error(invalid)
        assert caught.value.code == "contract.invalid_error"


def test_consumed_nested_contracts_reject_invalid_values_without_rewriting():
    """结果契约拒绝嵌套字段非法的 resident、消息计数和 Lineage 图，且不改写输入"""
    invalid_graph = graph()
    invalid_graph["program"]["components"] = [{"id": "effect_example", "kind": "effect", "config": []}]
    cases = [
        ("resident", {"resident_id": "resident_example", "lineage_id": "lineage_example", "title": "X", "metadata": {}}),
        ("message_counts", {"lineage_id": "lineage_example", "sessions": [
            {"resident_id": "resident_example", "available": True, "user_message_count": 2,
             "context_pressure": {"used_percent": "50"}},
        ]}),
        ("lineage_graph", invalid_graph),
    ]
    for name, payload in cases:
        original = deepcopy(payload)
        with pytest.raises(ResultContractError):
            validate_result(name, payload)
        assert payload == original


def graph():
    return {"lineage_id": "lineage_example", "revision": 0,
            "nodes": [{"id": "resident_parent"}, {"id": "resident_child"}],
            "edges": [{"id": "edge_branch", "source": "resident_parent", "target": "resident_child"}],
            "program": empty_program()}


@pytest.mark.parametrize("kind", ["route", "watch", "target"])
def test_graph_contract_distinguishes_route_inputs_from_object_bindings(kind):
    """Lineage 图契约要求 route 连线带 target_port，watch 和 target 绑定不能带"""
    value = graph()
    wire = {"id": "wire_example", "kind": kind, "source": "component_source",
            "target": "resident_child", "source_port": "output" if kind == "route" else kind}
    if kind == "route":
        wire["target_port"] = "invoke"
    value["program"]["wires"] = [wire]
    validate_result("lineage_graph", value)
    if kind == "route":
        wire.pop("target_port")
    else:
        wire["target_port"] = "invoke"
    with pytest.raises(ResultContractError):
        validate_result("lineage_graph", value)


@pytest.mark.parametrize("name,payload", [
    ("resident", {"resident_id": "resident_example", "lineage_id": "lineage_example", "title": "Example",
                  "project_id": "test", "provider": "codex", "account": "one",
                  "workspaces": [{"workspace_id": "workspace_123456789abc", "path": "/share/Projects/test", "is_owned": False, "purpose": ""}]}),
    ("message_counts", {"lineage_id": "lineage_example", "sessions": [
        {"resident_id": "resident_empty", "user_message_count": 0, "available": True},
        {"resident_id": "resident_unavailable", "user_message_count": 0, "available": False},
    ]}),
    ("lineage_graph", graph()),
])
def test_public_results_are_checked_without_rewriting(name, payload):
    """合法的 resident、消息计数和 Lineage 图结果通过校验且不被改写，空结果被拒绝"""
    original = deepcopy(payload)
    validate_result(name, payload)
    assert payload == original
    with pytest.raises(ResultContractError):
        validate_result(name, {})


def test_statistics_wrong_id_field_is_rejected():
    """消息计数结果用 id 代替 resident_id 时被结果契约拒绝"""
    with pytest.raises(ResultContractError):
        validate_result("message_counts", {"lineage_id": "lineage_example", "sessions": [
            {"id": "resident_wrong_field", "user_message_count": 3, "available": True},
        ]})


def test_resident_result_accepts_workspaces_without_optional_purpose():
    """Resident 结果中的 Workspace 可省略 purpose，workspace_id 为空时被拒绝"""
    resident = {
        "resident_id": "resident_123456789abc", "lineage_id": "lineage_123456789abc",
        "title": "Workspace contract", "project_id": "test", "provider": "codex", "account": "test",
        "workspaces": [
            {"workspace_id": "workspace_123456789abc", "path": "/test/owned", "is_owned": True, "purpose": ""},
            {"workspace_id": "workspace_23456789abcd", "path": "/test/shared", "is_owned": False, "purpose": ""},
        ],
    }
    validate_result("resident", resident)
    resident["workspaces"][1]["purpose"] = "Shared sources"
    validate_result("resident", resident)
    resident["workspaces"][1]["workspace_id"] = ""
    with pytest.raises(ResultContractError):
        validate_result("resident", resident)


def test_instruction_declarations_publish_their_results():
    """GetResident、GetLineage、ForkResident 和共享状态指令的声明都带结果契约"""
    from management_portal.hatg.catalog import instruction_catalog

    for name in ("GetResident", "GetLineage", "GetSharedState", "ForkResident",
                 "UpdateSharedState", "DeleteSharedState"):
        assert instruction_catalog()[name].result


def test_rpc_publishes_only_domain_commits_not_ids_in_payload_or_result(monkeypatch):
    """RPC 只发布领域实际提交的变更，不按参数或结果里出现的 ID 发布"""
    from management_portal.society_service.changes import resident_changed

    def write(payload, actor):
        resident_changed("resident_committed", scope="identity")
        return {"resident_id": "resident_unrelated", "lineage_id": "lineage_unrelated"}

    monkeypatch.setattr(server, "_domain_handler", lambda *args: write)
    monkeypatch.setattr(server, "_domain_module", lambda *args: SimpleNamespace())
    finished = Mock()
    endpoint = SimpleNamespace(read_pool=SimpleNamespace(workers=0), _finish_changes=finished)
    server.SocietyServiceServer._domain_call(endpoint, {
        "domain": "resident", "name": "request_resident_summary_rebuild",
        "args": [{"source_resident_id": "resident_source", "lineage_id": "lineage_source"}, "actor"],
        "kwargs": {},
    })
    assert [(domain, resource, scope) for domain, resource, scope, _ in finished.call_args.args[0]] == [
        ("resident", "resident_committed", "identity"),
    ]


def test_result_contract_errors_preserve_shape_across_rpc_transport():
    """结果契约错误经 RPC 传输后保留错误详情并返回 HTTP 500"""
    from management_portal.society_service.transport import SocietyServiceClient

    error = ResultContractError("message_counts", "sessions.0", "missing resident_id")
    with pytest.raises(ResultContractError) as caught:
        SocietyServiceClient._raise_remote_error(server._error_payload(error))
    assert caught.value.details == error.details
    assert caught.value.http_status == 500


def test_graph_state_owner_publishes_after_commit_not_for_noops_or_rollback(monkeypatch):
    """共享状态写入只在提交后发布变更，无变化的写入和回滚都不发布"""
    from contextlib import contextmanager
    from management_portal.society_service import lineage_graph_state
    from management_portal.society_service.changes import collect_changes

    lineage_id = "lineage_123456789abc"
    store = lineage_graph_state.get_lineage_data_store()
    store.put_lineage_definition(lineage_id, {
        "lineage_id": lineage_id, "title": "State contract", "revision": 0,
        "nodes": [], "edges": [], "program": {"components": [], "wires": []},
    }, {})
    with collect_changes() as changes:
        result = lineage_graph_state.update_cell(lineage_id, "counter", 1)
    assert [item[:3] for item in changes] == [("lineage", lineage_id, "runtime")]

    with collect_changes() as changes:
        lineage_graph_state.update_cell(lineage_id, "counter", 1)
        result = lineage_graph_state.update_cell(lineage_id, "counter", 2, expected_value=0)
        lineage_graph_state.delete_cell(lineage_id, "counter", 0)
    assert result["matched"] is False
    assert changes == []

    transaction = store.lineage_shared_state.transaction

    @contextmanager
    def failed_commit(*args):
        with transaction(*args) as state:
            yield state
            raise RuntimeError("commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(store.lineage_shared_state, "transaction", failed_commit)
        with collect_changes() as changes, pytest.raises(RuntimeError, match="commit failed"):
            lineage_graph_state.update_cell(lineage_id, "counter", 2)
        assert changes == []
    assert lineage_graph_state.read_cell(lineage_id, "counter")["value"] == 1
    with collect_changes() as changes:
        result = lineage_graph_state.delete_cell(lineage_id, "counter")
    assert len(changes) == 1
