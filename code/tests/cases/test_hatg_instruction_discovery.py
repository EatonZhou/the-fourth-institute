"""按任务发现 ISA 能力，保留对象分类和调用权限边界。

:module: isa.instructions
:design: decision_aa4fdbac8364, decision_898b8911a2ff
"""
import pytest

from management_portal.hatg import routing
from management_portal.hatg.catalog import instruction_catalog
from management_portal.hatg.contracts import InstructionContext
from management_portal.hatg.declarations import DISCOVERY_GROUPS
from management_portal.execution.harness_isa_tool import _description

CONTEXT = InstructionContext(caller="resident_aaaaaaaaaaaa", entry="harness")


def listing(**arguments):
    result = routing.execute({"instruction": "ListInstructions", "arguments": arguments}, CONTEXT)
    assert result.status == "committed"
    return result.result


def names(result):
    return {item["instruction"] for item in result["instructions"]}


def test_default_discovery_has_general_capabilities_and_complete_group_index():
    """默认列日常能力，索引让应用、内部维护、运维能力仍可被发现"""
    result = listing()
    assert {"ListInstructions", "DeliverMessage", "ReadResource", "ApplyLineageChanges"} <= names(result)
    assert not {"ExportPaperBibtex", "CheckFamilyProgress", "RestartPortal", "SubmitResidentSummary"} & names(result)
    assert {item["group"] for item in result["groups"]} == set(DISCOVERY_GROUPS)
    assert sum(item["count"] for item in result["groups"]) == len(instruction_catalog())
    assert result["guide"] == "/guide/agent.md"


def test_explicit_groups_partition_the_catalog_without_changing_categories():
    """按组展开恰好覆盖当前安装目录，组与对象类别各自保留"""
    groups = [names(listing(group=group)) for group in DISCOVERY_GROUPS]
    assert sum(map(len, groups)) == len(set.union(*groups))
    assert set.union(*groups) == names(listing(group="all")) == set(instruction_catalog())
    for group in DISCOVERY_GROUPS:
        result = listing(group=group)
        assert all(item["discovery_group"] == group for item in result["instructions"])
    assert instruction_catalog()["CheckFamilyProgress"].category == "family_internal"


def test_category_alone_crosses_groups_and_combined_filter_intersects():
    """现有类别查询仍完整，组加类别只返回交集"""
    assert "GetResearchMap" in names(listing(category="research"))
    assert names(listing(category="research", group="general")) == set()
    assert names(listing(category="resident", group="internal")) == {
        "RequestResidentSummaryRebuild", "GetResidentSummaryQueue", "ClaimResidentSummaryJobs",
        "SubmitResidentSummary", "ReleaseResidentSummaryJob",
    }


def test_unknown_group_is_rejected():
    """未知组返回参数错误，不悄悄退化为完整目录"""
    result = routing.execute({"instruction": "ListInstructions", "arguments": {"group": "admin"}}, CONTEXT)
    assert result.status == "failed"
    assert result.error["code"] == "instruction.invalid_arguments"


@pytest.mark.parametrize("name", ["EnsurePaperTranslationBinding", "TranslatePaperText"])
def test_retired_translation_has_no_declaration_handler_or_callable_alias(name):
    """已取消实验完全退出目录与handler，旧调用返回未知指令"""
    from management_portal.paper_web_isa import HANDLERS
    assert name not in instruction_catalog() and name not in HANDLERS
    for instruction, arguments in [(name, {}), ("GetInstructionSchema", {"instruction": name})]:
        result = routing.execute({"instruction": instruction, "arguments": arguments}, CONTEXT)
        assert result.status == "failed" and result.error["code"] == "instruction.unknown"


def test_tool_description_supports_discovery_without_catalog_expansion():
    """工具引导按需发现；应用安装和指令增加不扩写工具说明"""
    text = _description()
    assert "ListInstructions" in text and "group=all" in text
    assert "internal" in text and "operations" in text
    assert len(text) < 1400
    assert "ExportPaperBibtex" not in text and "UpdateProviderAccount" not in text
