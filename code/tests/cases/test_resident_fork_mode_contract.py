"""Fork and switching share two lazy memory transfers."""
from unittest import mock

import pytest

from management_portal.hatg import catalog
from management_portal.hatg.contracts import InstructionError
from management_portal.society_service import lineage_branch_transitions
from management_portal.society_service.templates.registry import TemplateError
from management_portal.society_service.templates import kinds


@pytest.mark.parametrize("mode", [None, "native", "local", "full", "visible"])
def test_public_fork_protocol_accepts_two_transfers_and_rejects_old_modes(mode):
    """ForkResident 接受缺省、native、local；拒绝 full 和 visible

    :design: decision_5bf5bfbd52cb, decision_d19b9909a674
    :design-point:
    """
    args = {"lineage_id": "lineage_aaaaaaaaaaaa", "source_resident_id": "resident_aaaaaaaaaaaa"}
    if mode is not None:
        args["fork_mode"] = mode
    if mode in {"full", "visible"}:
        with pytest.raises(InstructionError) as failed:
            catalog.declaration("ForkResident").validate_arguments(args)
        assert failed.value.code == "instruction.invalid_arguments"
    else:
        catalog.declaration("ForkResident").validate_arguments(args)


@pytest.mark.parametrize("mode", [None, "native", "local", "full", "visible", "unknown"])
def test_direct_branch_calls_validate_mode_before_provider_creation(mode):
    """直接创建分支时先校验分支模式，无效模式在创建 Resident 前被拒绝

    :design: decision_5bf5bfbd52cb, decision_d19b9909a674
    :design-point:
    """
    payload = {"source_resident_id": "resident_aaaaaaaaaaaa", "edge_id": "edge_aaaaaaaaaaaa"}
    if mode is not None:
        payload["mode"] = mode
    authority = mock.Mock()
    if mode in {"full", "visible", "unknown"}:
        with pytest.raises(ValueError, match="分支模式"):
            lineage_branch_transitions.branch_request(payload)
        with pytest.raises(ValueError, match="分支模式"):
            lineage_branch_transitions.create_derived_resident(
                payload["source_resident_id"], payload, "CASE-A", "lineage_aaaaaaaaaaaa",
                resident_authority=authority,
            )
        assert authority.mock_calls == []
    else:
        assert lineage_branch_transitions.branch_request(payload)[-1] == (mode or "")
        lineage_branch_transitions.create_derived_resident(
            payload["source_resident_id"], payload, "CASE-A", "lineage_aaaaaaaaaaaa",
            resident_authority=authority,
        )
        authority.create_resident_full_context_fork.assert_called_once()


@pytest.mark.parametrize("mode", [None, "native", "local", "full", "visible"])
def test_structure_templates_use_the_two_memory_transfers(mode):
    """结构模板不预先覆盖能力选择，支持两种方法"""
    role = {"key": "child", "from": "fork", "fork_of": "source"}
    if mode is not None:
        role["fork_mode"] = mode
    template = {
        "kind": "structure", "category": "structure", "title": "Fork", "targets": ["resident"], "parameters": [],
        "body": {"roles": [{"key": "source", "from": "target"}, role], "components": [], "wires": []},
    }
    if mode in {"full", "visible"}:
        with pytest.raises(TemplateError, match="Fork 模式"):
            kinds.normalize(template)
    else:
        assert kinds.normalize(template)["body"]["roles"][-1].get("fork_mode", "") == (mode or "")



@pytest.mark.parametrize("provider", [None, "claude", "kimi", "provider_api"])
def test_isa_fork_default_defers_choice_to_the_lineage_owner(monkeypatch, provider):
    """ForkResident 默认由交接能力选择方法，并带上指定的 Provider 与账号

    :design: decision_5bf5bfbd52cb, decision_d19b9909a674
    :design-point:
    """
    from types import SimpleNamespace
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import Invocation
    from management_portal.society_service.isa import residents

    payloads = []
    joins = []
    def fork_reply(_lineage, payload, _actor, *, include_lineage=True):
        assert include_lineage is False
        payloads.append(payload)
        return {"created_resident": {"id": "resident_bbbbbbbbbbbb"}}
    monkeypatch.setattr(residents, "_family_role", lambda _: "")
    monkeypatch.setattr(residents, "_family_to_join", lambda *_: "fbi_cccccccccccc")
    monkeypatch.setattr(residents, "_join_family", lambda *args: joins.append(args) or {})
    monkeypatch.setattr(residents, "lineage_of_resident", lambda _: "lineage_aaaaaaaaaaaa")
    monkeypatch.setattr(residents, "placement", lambda *_: {})
    monkeypatch.setattr(residents, "lineage_branches", lambda: SimpleNamespace(
        fork_lineage_resident=fork_reply,
    ))
    inv = Invocation(
        declaration=catalog.declaration("ForkResident"),
        call=InstructionCall.from_payload({
            "instruction": "ForkResident",
            "arguments": {"lineage_id": "lineage_aaaaaaaaaaaa", "source_resident_id": "resident_aaaaaaaaaaaa",
                          **({"provider": provider, "account_id": "selected-account"} if provider else {})},
        }),
        context=InstructionContext(caller="CASE-A", entry="web"),
        instruction_id="inst_lin_full_mode_default",
    )
    residents.fork_resident(inv)
    assert joins == [("lineage_aaaaaaaaaaaa", "resident_bbbbbbbbbbbb", "fbi_cccccccccccc", inv)]
    assert payloads[0]["fork_mode"] == ""
    if provider:
        assert payloads[0]["provider"] == provider
        assert payloads[0]["backend_route_id"] == "selected-account"


@pytest.mark.parametrize("mode", ["native", "local"])
def test_every_fork_mode_forwards_provider_and_account(mode):
    """每种 fork 模式都把指定的 Provider 和 backend_route_id 传给创建"""
    authority = mock.Mock()
    lineage_branch_transitions.create_derived_resident(
        "resident_aaaaaaaaaaaa", {"mode": mode, "provider": "claude", "backend_route_id": "chosen-route"},
        "CASE-A", "test", resident_authority=authority,
    )
    call, = authority.mock_calls
    assert call.kwargs["provider"] == "claude"
    assert call.kwargs["backend_route_id"] == "chosen-route"


def test_restart_provider_native_strategy_keeps_its_distinct_protocol():
    """RestartSession 的 context_strategy 接受 native，拒绝 full

    :design: decision_5bf5bfbd52cb, decision_d19b9909a674
    :design-point:
    """
    args = {"resident_id": "resident_aaaaaaaaaaaa", "context_strategy": "native"}
    catalog.declaration("RestartSession").validate_arguments(args)
    args["context_strategy"] = "full"
    with pytest.raises(InstructionError) as failed:
        catalog.declaration("RestartSession").validate_arguments(args)
    assert failed.value.code == "instruction.invalid_arguments"
