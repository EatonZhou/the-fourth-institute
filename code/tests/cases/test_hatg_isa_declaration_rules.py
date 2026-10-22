"""Catalog tests for the declaration rules of decision_898b8911a2ff ("新增与修改指令").

Each rule is checked against the whole catalog: the decision slots every
instruction declares (``InstructionDeclaration.decides``) and its parameters.
Owner-derived permission arguments are listed explicitly and checked through
the kernel's authorization path as well as the catalog.

:module: isa.instructions
:design: decision_898b8911a2ff, decision_600ae27b0538
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from management_portal.hatg.catalog import instruction_catalog


DECISIONS = Path(os.environ.get(
    "TFI_SPEC_DECISIONS",
    str(Path(__file__).resolve().parents[3] / "instance/example_instance/seed/data/share_root/Projects/AI员工/knowledge_base/decision"),
))

# One object has one parameter name in every instruction.
OBJECT_NAMES = {"username": "user_id", "account": "account_id", "project": "project_id",
                "type": "resource_type", "role": "backend_role"}

# decision_8b510cbd9c58: the owner resolves this before asking permission.
# The parametrized kernel test below guards both derivation and rejection.
OWNER_AUTHORIZATION_ARGUMENTS = {"RunScript": {"lineage_id"}}


def _argument_slots():
    for name, declaration in instruction_catalog().items():
        for slot in declaration.decides:
            if slot.argument:
                yield name, slot


def test_every_instruction_declares_what_it_is_judged_on():
    """每条指令都声明判定槽，每个判定槽是一个 A 元素

    只有一个槽时 A 元素就是指令名，有多个槽时是“指令:参数”或“指令:固定槽名”；元素名在整个目录中不重复。
    """
    catalog = instruction_catalog()
    assert [name for name, declaration in catalog.items() if not declaration.decides] == []
    elements = [element for declaration in catalog.values() for element in declaration.elements]
    assert len(elements) == len(set(elements))
    assert all(declaration.elements[0][0] == name for name, declaration in catalog.items() if len(declaration.decides) == 1)


def test_slot_arguments_are_declared():
    """判定槽引用的每个参数都在指令声明中存在

    槽写的是点分路径时，根参数必须已声明；根参数声明了 properties 时，下一层字段也必须在其中。按落点判定的槽在 place 中写的参数路径同样检查。
    """
    catalog = instruction_catalog()
    missing = []
    for name, slot in _argument_slots():
        for argument in (slot.argument, *(path for _related, paths in slot.place for path in paths)):
            root, *rest = argument.split(".")
            parameter = catalog[name].parameter_map.get(root)
            if parameter is None:
                missing.append((name, argument))
            elif rest and parameter.properties is not None and rest[0] not in parameter.properties:
                missing.append((name, argument))
    assert missing == []


def test_slot_types_and_places_are_registered():
    """判定槽写的资源类型和落点关系都已登记

    types 中的类型在资源类型注册表中（或是 user、project 这类命名对象）；place 的关系对象类型是某条放置规则的关系对象。
    """
    from management_portal.permission_system.scheme import vocabulary

    wrong = [(name, slot.argument) for name, slot in _argument_slots()
             if set(slot.types) - vocabulary.RESOURCE_TYPES
             or {related for related, _paths in slot.place} - vocabulary.PLACE_RELATED]
    assert wrong == []


# The owner fills in the argument the caller may omit before the call is judged (OwnerBinding.authorization_arguments):
# RunScript's Lineage, the one exception decision_898b8911a2ff names to "目标对象用统一 ID 指定，且必填".
DERIVED_BY_OWNER = {"RunScript"}


def _undecided() -> set[str]:
    """Instructions with a legal call that makes no permission request."""
    undecided = set()
    for name, declaration in instruction_catalog().items():
        if name in DERIVED_BY_OWNER:
            continue
        slots = declaration.decides
        if any(not slot.argument for slot in slots):
            continue  # A fixed slot is always asked.
        required = {parameter.name for parameter in declaration.parameters if parameter.required}
        required |= OWNER_AUTHORIZATION_ARGUMENTS.get(name, set())
        if any(slot.argument.split(".", 1)[0] in required and not slot.optional for slot in slots):
            continue
        mapped = {slot.argument.split(".", 1)[0] for slot in slots}
        if declaration.one_of and all(mapped & set(group) for group in declaration.one_of):
            continue
        if declaration.any_of and set(declaration.any_of) <= mapped:
            continue
        undecided.add(name)
    return undecided


def test_every_legal_call_makes_a_permission_request():
    """每一种合法的参数组合至少生成一个判定请求

    判定槽的参数必填、有固定槽、每个“恰好给一个”的参数组都含有判定槽的参数，或“至少给一个”的每个参数都有判定槽。
    Owner 从正式调用上下文补齐的参数还须经过下面的真实鉴权路径检查；不能仅因参数可省略就认作免检。
    """
    assert _undecided() == set()


@pytest.mark.parametrize("caller,explicit,activation,expected", [
    ("resident_aaaaaaaaaaaa", None, None, "lineage_aaaaaaaaaaaa"),
    ("resident_aaaaaaaaaaaa", "lineage_bbbbbbbbbbbb", None, "lineage_bbbbbbbbbbbb"),
    ("script-user", "lineage_bbbbbbbbbbbb", None, "lineage_bbbbbbbbbbbb"),
    ("script-user", None, "lineage_cccccccccccc", "lineage_cccccccccccc"),
    ("script-user", None, None, "需要有效的所属 lineage_id"),
    ("script-user", "lineage_bbbbbbbbbbbb", "lineage_cccccccccccc", "与当前激活不符"),
])
def test_owner_derived_permission_argument_makes_request_or_rejects(monkeypatch, caller, explicit, activation, expected):
    """RunScript 省略所属参数仍对真实 Lineage 生成判定请求，无法确定或冲突时拒绝。

    :design: decision_8b510cbd9c58
    """
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.permission_system.requests import requests
    from management_portal.permission_system.scheme.compiler import compile_scheme
    from management_portal.permission_system.scheme.repository import builtin_documents
    from management_portal.society_service import isa
    from management_portal.society_service.isa import common
    from management_portal.society_service.script_runs import service

    monkeypatch.setattr(common, "lineage_of_resident", lambda resident: {
        "resident_aaaaaaaaaaaa": "lineage_aaaaaaaaaaaa",
    }[resident])
    scheme = compile_scheme(builtin_documents()["default"])
    asked = []

    def authorize(declaration, arguments, context):
        assert context.caller == caller
        asked.extend((request.action, request.resource) for request in requests(scheme, declaration.name, arguments))
        raise PermissionError("permission request inspected")  # Never launch the script.

    kernel = InstructionKernel(isa.binding(), store=None, authorize=authorize)
    arguments = {"path": "/never-launched.py"}
    if explicit:
        arguments["lineage_id"] = explicit
    token = service.ACTIVATION.set({"lineage_id": activation} if activation else {})
    try:
        result = kernel.execute(InstructionCall("RunScript", arguments), InstructionContext(caller=caller, entry="lineage"))
    finally:
        service.ACTIVATION.reset(token)
    if expected.startswith("lineage_"):
        assert asked == [("RunScript", expected)]
        assert "permission request inspected" in result.error["message"]
    else:
        assert asked == []
        assert expected in result.error["message"]


def _records() -> dict[str, dict]:
    from management_portal.permission_system.scheme import vocabulary
    return {item["via"]: item for item in vocabulary.describe()["records"]}


def test_via_slots_name_registered_records_without_types():
    """按记录判定的槽只写已登记的记录字段，不写 types

    via 的值必须是词汇 records 中登记过的“记录.字段”；判定对象由记录给出，槽不再写 types。
    """
    records = _records()
    wrong = [(name, slot.argument) for name, slot in _argument_slots()
             if slot.via and (slot.via not in records or slot.types)]
    assert wrong == []


def test_via_slots_on_records_that_may_be_unowned_have_a_fixed_slot_beside_them():
    """记录可能不属于任何对象时，按它判定的指令另有固定槽

    词汇 records 中标了 may_be_unowned 的字段可能为空，这时 via 槽不生成判定请求（可选与否只管参数没给的情况）；
    没有其他请求的调用按未归类判定，只有管理员能通过。固定槽让这样的调用仍按方案判定。
    """
    unowned = {via for via, item in _records().items() if item["may_be_unowned"]}
    catalog = instruction_catalog()
    wrong = [name for name, slot in _argument_slots() if slot.via in unowned
             and not any(other.fixed for other in catalog[name].decides)]
    assert wrong == []


def test_each_object_has_one_parameter_name():
    """同一对象在所有指令中使用同一个参数名，版本前提只写在调用信封中

    User 写 user_id，执行账号写 account_id，Project 写 project_id，资源类型写 resource_type，Backend 角色写 backend_role；业务参数不重复调用信封的 expected_version。
    """
    catalog = instruction_catalog()
    aliases = sorted((name, parameter.name) for name, declaration in catalog.items()
                     for parameter in declaration.parameters if parameter.name in OBJECT_NAMES)
    assert aliases == []
    repeated = {name for name, declaration in catalog.items() if "expected_version" in declaration.parameter_map}
    assert repeated == set()


# Changes with no single object to name: a work queue, a new terminal session,
# a Pulse item addressed by period, key and item, a Portal instance, and the
# Portal-wide settings, Telepus service enablement and knowledge tag list.
WITHOUT_TARGET = {"ClaimResidentSummaryJobs", "OpenTerminal", "PublishPulseItem", "RestartPortal",
                  "SetTelepusEnabled", "UpdateExecutionSettings", "UpdateKnowledgeTags"}


def test_changes_to_an_existing_object_declare_it_as_target():
    """修改已有对象的指令把目标对象参数声明为 target

    创建与登记指令给出新对象的位置，“恰好给一个”的指令每种对象一个参数；其余修改指令的 target 指向它作用的对象，调用信封的标量 expected_version 按它比较。
    """
    catalog = instruction_catalog()
    missing = {name for name, declaration in catalog.items()
               if declaration.mutates and not declaration.target and not declaration.one_of
               and not name.startswith(("Create", "Register"))}
    assert missing == WITHOUT_TARGET


def test_watch_instructions_resume_after_the_returned_cursor():
    """Watch 指令都用 after_cursor 续读，每项结果带 cursor

    Harness 工具与命令行客户端把上一项的 cursor 作为 arguments.after_cursor 传回，指令不另起续读参数名。
    """
    catalog = instruction_catalog()
    wrong = {name for name, declaration in catalog.items() if declaration.streaming
             and ("after_cursor" not in declaration.parameter_map or "cursor" not in {item.name for item in declaration.result})}
    assert wrong == set()


# Reads that address positions inside one object, so a caller may start anywhere (the end of a
# trace, a byte in a log, one offset shared by a Trigger Path's four lists): offset/next_offset.
READ_BY_POSITION = {"GetSubagentRun", "GetTriggerPath", "ReadScriptRunOutput"}
OTHER_PAGING_NAMES = {"offset", "page_token", "before", "next_offset", "next_page_token", "next_before"}


def test_paged_lists_use_cursor_limit_and_next_cursor():
    """分页读取一组条目的指令都用 cursor 和 limit，结果用 next_cursor，不用 offset、page_token 或 before

    cursor 是上一页 next_cursor 的原样交回。按位置读取一个对象内部序列的三条指令保留 offset。

    :design: decision_898b8911a2ff
    """
    catalog = instruction_catalog()
    wrong = {}
    for name, declaration in catalog.items():
        parameters = set(declaration.parameter_map)
        results = {item.name for item in declaration.result}
        other = (parameters | results) & OTHER_PAGING_NAMES
        if name in READ_BY_POSITION:
            other -= {"offset", "next_offset"}
        if other:
            wrong[name] = sorted(other)
        elif "cursor" in parameters and not {"limit", "next_cursor"} <= parameters | results:
            wrong[name] = ["cursor without limit and next_cursor"]
    assert wrong == {}
    assert all("offset" in catalog[name].parameter_map for name in READ_BY_POSITION)


def test_every_parameter_and_result_field_says_what_it_is():
    """每个参数和结果字段都有说明

    GetInstructionSchema 把说明交给调用者，调用者据此填写参数、读懂结果，不必读处理代码。

    :design: decision_898b8911a2ff
    """
    missing = sorted(f"{name}.{field.name}" for name, declaration in instruction_catalog().items()
                     for field in (*declaration.parameters, *declaration.result) if not field.description.strip())
    assert missing == []


def test_at_least_one_group_rejects_a_call_without_any_of_its_parameters():
    """“至少给一个”的参数组：一个都不给时在入口拒绝，给出任意一个或全部都接受

    :design: decision_898b8911a2ff, decision_22227d0ad075
    """
    from management_portal.hatg.contracts import InstructionError

    declaration = instruction_catalog()["ListPermissionEntries"]
    assert declaration.schema()["any_of"] == ["p", "r"]
    with pytest.raises(InstructionError) as raised:
        declaration.validate_arguments({"a": "@viewer"})
    assert raised.value.code == "instruction.invalid_arguments"
    for arguments in ({"p": "alice"}, {"r": "@portal"}, {"p": "alice", "r": "@portal"}):
        declaration.validate_arguments(arguments)


def _listed_verbs() -> set[str]:
    paths = sorted(DECISIONS.glob("*decision_898b8911a2ff.md"))
    if not paths:
        pytest.skip("HATG ISA decisions are not available on this host")
    section = paths[0].read_text(encoding="utf-8").split("## 命名规则", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"`([A-Z][a-z]+)`", section))


def test_instruction_verbs_follow_the_naming_rules():
    """指令名的动词属于命名规则的动词表，动词表不列没有指令使用的动词"""
    used = {re.match(r"[A-Z][a-z]+", name).group(0) for name in instruction_catalog()}
    listed = _listed_verbs()
    assert used - listed == set()
    assert listed - used == set()
