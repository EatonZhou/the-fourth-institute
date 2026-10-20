"""Lineage definition preconditions shared by declarations and their owner."""

DEFINITION_MUTATIONS = frozenset({
    "UpdateLineage", "DeleteLineage", "CreateComponent", "UpdateComponent", "DeleteComponent",
    "CreateWire", "UpdateWire", "DeleteWire", "ApplyLineageChanges",
    "CreateCandidateFork", "DeleteCandidateFork", "CreateWorkspaceHandoff", "UpdateWorkspaceHandoff",
    "DeleteWorkspaceHandoff", "CreateResident", "UpdateResident",
    "DeleteResident", "ForkResident", "PromoteSeed", "MoveResident", "MoveLineageObjects",
    "Functionalize", "Unfunctionalize", "UpdateFunctionBlock", "DeleteFunctionBlock",
    "ApplyTemplate", "Familize", "Unfamilize", "DeleteFamily", "UpdateFamily",
})


VERSION_DESCRIPTION = (
    " 可选 expected_version 使用 GetLineage.result.lineage.revision；仅在本次调用对各 Lineage 的首次定义提交短事务内比较，后续提交不重复比较；外部工作不在该事务内。"
    "不一致返回 instruction.conflict，error.details.current_version 为当前 revision。"
    "跨 Lineage 可传以 Lineage ID 为键的版本映射；未传时按提交时合并处理。"
    "PrepareWorkspaceHandoff 不使用 Lineage revision 前提；未写定义时不比较。"
)
