"""Intake decides once what an input's activation policy asks of it.

``resident_intake_domain.ACTIVATION_POLICIES`` gives every policy the message
contract accepts exactly one role: a turn of its own, joining the active turn,
or an info that waits for the next request.

:module: resident.conversation
:importance: P2
"""
from __future__ import annotations

from management_portal.society_service import resident_intake_domain, resident_message_contract


def test_every_contract_activation_policy_has_one_intake_role():
    """消息协议接受的每种激活策略在接收中恰有一种处理方式"""
    policies = resident_intake_domain.ACTIVATION_POLICIES
    assert set(policies) == set(resident_message_contract.ACTIVATION_POLICIES)
    for name, policy in policies.items():
        assert policy.name == name
        roles = (policy.starts_turn, policy.joins_active_turn, policy.waits_as_info)
        assert roles.count(True) == 1, name
    assert policies["enqueue_and_trigger"].starts_turn
    assert policies["inject_active_turn"].joins_active_turn
    assert policies["attach_to_next"].waits_as_info
