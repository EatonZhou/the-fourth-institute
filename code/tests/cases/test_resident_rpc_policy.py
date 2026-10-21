"""Internal system entries still check the real User or Resident against the permission scheme.

:design: decision_b820faf42218
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.hatg.contracts import InstructionError
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401

RID = 'resident_aaaaaaaaaaaa'


@pytest.mark.parametrize('actor', ['bob', 'resident_bbbbbbbbbbbb'])
def test_system_entry_keeps_user_and_resident_policy_checks(permissions, actor):
    """内部 system 入口保持 User 与 Resident 的策略检查

    内部 system 入口保持 User 与 Resident 的策略检查，授权失败不提交目标状态。
    """
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.society_service.isa import binding
    permissions.place(Fact('resident', RID))
    mutation = Mock(return_value={})
    from dataclasses import replace
    from management_portal.hatg.store import ExecutionStore
    from management_common.portal_database import portal_database
    original = binding()
    owner = replace(original, handlers={**original.handlers, 'ClearContext': mutation},
                    record_scope=lambda inv: ('caller', inv.caller))
    kernel = InstructionKernel(owner, store=ExecutionStore(portal_database(schema='p1_rpc_boundary')))
    call = InstructionCall('ClearContext', {'resident_id': RID})
    denied = kernel.execute(call, InstructionContext(caller=actor, entry='system'))
    assert denied.error['code'] == 'instruction.forbidden'
    mutation.assert_not_called()
    permissions.grant(actor, RID, '@admin')
    assert kernel.execute(call, InstructionContext(caller=actor, entry='system')).status == 'committed'
    assert kernel.execute(call, InstructionContext(caller='portal', entry='system')).status == 'committed'


def test_system_user_cannot_read_another_callers_execution_record():
    """内部调用不能读取其他调用者的私有执行记录

    内部调用不能读取其他调用者的私有执行记录，授权失败不提交目标状态。
    """
    from management_portal.hatg.contracts import InstructionContext
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.society_service.isa import binding
    kernel = InstructionKernel(binding(), store=SimpleNamespace(get=lambda key: {
        'scope_kind': 'caller', 'caller': 'alice', 'instruction_id': key,
    }))
    with pytest.raises(InstructionError) as error:
        kernel.execution_record('inst_private', InstructionContext(caller='bob', entry='system'))
    assert error.value.code == 'instruction.not_found'
