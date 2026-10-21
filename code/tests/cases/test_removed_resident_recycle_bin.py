"""Removed lifecycle commands have no declarations, handlers or aliases.

:design: decision_d19b9909a674
"""
import pytest

from management_portal.hatg.catalog import declaration, instruction_catalog
from management_portal.hatg.contracts import InstructionError
from management_portal.society_service.isa.residents import HANDLERS


@pytest.mark.parametrize("name", ["TrashResident", "RestoreResident", "EmptyTrash"])
def test_recycle_bin_instruction_is_removed(name):
    """已移除的回收站指令没有声明与处理函数，调用时报未知指令

    :design-point:"""
    assert name not in instruction_catalog()
    assert name not in HANDLERS
    with pytest.raises(InstructionError) as unknown:
        declaration(name)
    assert unknown.value.code == "instruction.unknown"
