"""The knowledge tags of the Project registry are read and replaced through HATG ISA.

The tags (``tag_vocab``) live in the Project registry in PostgreSQL;
GetKnowledgeTags and UpdateKnowledgeTags are the only way to change them
(decision_6591e7793116).

:module: isa.instructions
:design: decision_6591e7793116
"""
from __future__ import annotations

import pytest

from management_portal import project_registry
from management_portal.hatg import catalog
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.hatg.kernel import Invocation
from management_portal.society_service.isa import organization
from tests.cases.registry_support import write_projects

TAGS = [
    {"id": "todo", "folder": "todo", "description": "待办"},
    {"id": "event", "folder": "event", "description": "事件", "param": "who, ddl"},
]
PROJECT = {"id": "Demo", "path": "/Projects/Demo", "status": "active"}


def invocation(name, expected_version=None, **arguments):
    payload = {"instruction": name, "arguments": arguments}
    if expected_version is not None:
        payload["expected_version"] = expected_version
    return Invocation(
        declaration=catalog.instruction_catalog()[name],
        call=InstructionCall.from_payload(payload),
        context=InstructionContext(caller="CASE-A", entry="web"),
        instruction_id="inst_lin_projecttags",
    )


@pytest.fixture
def registry():
    write_projects({"status_vocab": ["active"], "tag_vocab": TAGS, "projects": [PROJECT]})
    project_registry.reset_cache_for_tests()
    yield
    project_registry.reset_cache_for_tests()


def test_tags_are_read_with_a_content_version(registry):
    """GetKnowledgeTags 读取知识标签及其内容版本

    返回 Project 登记中的完整 tag_vocab 与按内容计算的 version。

    :design-point:
    """
    result = organization.get_knowledge_tags(invocation("GetKnowledgeTags"))
    assert result["tags"] == TAGS
    assert result["version"] == project_registry.tags_version(TAGS)


def test_a_whole_list_adds_renames_and_removes_tags_and_keeps_the_projects(registry):
    """UpdateKnowledgeTags 以完整列表新增、改名和删除标签

    以完整列表替换 tag_vocab，folder 缺省与 id 相同；Project 列表与 status_vocab 不变，version 随内容变化。

    :design-point:
    """
    version = organization.get_knowledge_tags(invocation("GetKnowledgeTags"))["version"]
    tags = [
        {"id": "task", "folder": "task", "description": "待办"},  # todo renamed
        {"id": "risk", "description": "风险"},  # added; folder follows the ID
    ]  # event removed
    result = organization.update_knowledge_tags(invocation("UpdateKnowledgeTags", version, tags=tags))
    assert result["tags"] == [
        {"id": "task", "folder": "task", "description": "待办"},
        {"id": "risk", "folder": "risk", "description": "风险"},
    ]
    assert result["version"] != version
    assert project_registry.tags() == result["tags"]
    assert project_registry.tag_folder_map() == {"task": "task", "risk": "risk"}
    assert project_registry.projects() == [PROJECT]
    assert project_registry.load()["status_vocab"] == ["active"]


def test_a_stale_version_changes_nothing(registry):
    """UpdateKnowledgeTags 遇到过期版本不修改

    expected_version 与当前标签内容版本不一致时返回 instruction.version_conflict，并给出当前版本，标签不变。

    :design-point:
    """
    with pytest.raises(InstructionError) as error:
        organization.update_knowledge_tags(invocation("UpdateKnowledgeTags", "0" * 16, tags=[]))
    assert error.value.code == "instruction.version_conflict"
    assert error.value.details["current_version"] == project_registry.tags_version(TAGS)
    assert project_registry.tags() == TAGS


@pytest.mark.parametrize(("tags", "message"), [
    ([{"id": "Todo", "description": "x"}], "小写字母"),
    ([{"id": "a", "description": "x"}, {"id": "a", "description": "y"}], "ID 重复"),
    ([{"id": "a", "folder": "x", "description": "x"}, {"id": "b", "folder": "x", "description": "y"}], "folder 重复"),
    ([{"id": "a", "folder": "../x", "description": "x"}], "一层目录名"),
    ([{"id": "a"}], "description"),
    ([{"id": "a", "description": "x", "color": "red"}], "color"),
    ("todo", "数组"),
])
def test_invalid_tags_are_rejected_without_a_change(registry, tags, message):
    """UpdateKnowledgeTags 拒绝无效标签

    ID 格式、重复 ID 或 folder、多层 folder、缺少 description、未知字段或非数组输入都返回 instruction.invalid_arguments，标签不变。

    :design-point:
    """
    with pytest.raises(InstructionError) as error:
        organization.update_knowledge_tags(invocation("UpdateKnowledgeTags", tags=tags))
    assert error.value.code == "instruction.invalid_arguments"
    assert message in str(error.value)
    assert project_registry.tags() == TAGS


def test_tag_instructions_are_declared_and_wired():
    """知识标签指令已在指令目录登记并接入

    UpdateKnowledgeTags 与 GetKnowledgeTags 都在 ISA 指令目录中登记（前者为修改指令），两条指令都在 SocietyService 的组织指令中实现。

    :design: decision_b820faf42218
    :design-point:
    """
    declarations = catalog.instruction_catalog()
    assert {"UpdateKnowledgeTags", "GetKnowledgeTags"} <= set(declarations)
    assert declarations["UpdateKnowledgeTags"].mutates and not declarations["GetKnowledgeTags"].mutates
    assert organization.HANDLERS["GetKnowledgeTags"] is organization.get_knowledge_tags
    assert organization.HANDLERS["UpdateKnowledgeTags"] is organization.update_knowledge_tags
