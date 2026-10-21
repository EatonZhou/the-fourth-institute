"""The HATG ISA catalog matches its category specifications instruction by instruction.

Each category decision lists its instructions in one table: name, whether it
changes state ("是", "否", "否（流式）" or a per-action rule) and a one-line
purpose.  The code catalog must hold exactly those instructions, in the
same categories, with the same state-changing and streaming flags.

:module: isa.instructions
"""

from __future__ import annotations

import re
import os
from pathlib import Path

import pytest

from management_portal.hatg.catalog import categories, instruction_catalog
from management_portal.hatg.declarations import OWNER_MARKS


DECISIONS = Path(os.environ.get(
    "TFI_SPEC_DECISIONS",
    str(Path(__file__).resolve().parents[3] / "instance/example_instance/seed/data/share_root/Projects/AI员工/knowledge_base/decision"),
))
CATEGORY_DECISIONS = {
    "resource": "ad3e21094f14",
    "user": "6eac174d85bf",
    "resident": "d19b9909a674",
    "conversation": "2fe6fd5367fb",
    "mailbox": "c54e68a82d7a",
    "resident_execution": "2a3ff353165c",
    "lineage_definition": "4637ca4a69e2",
    "lineage_state": "71ba0494b93e",
    "lineage_run": "68215b3a825b",
    "script_run": "8b510cbd9c58",
    "function_block": "5b5653c3eaa5",
    "family": "44cf587c1721",
    "family_internal": "b77fde81e7a0",
    "template": "b04cac4188bb",
    "discussion": "3e063aac15b3",
    "pulse": "cc96fce57ab6",
    "organization": "6591e7793116",
    "permission": "22227d0ad075",
    "environment": "4527448ad07f",
    "isa": "aa4fdbac8364",
}
ROW = re.compile(r"^\| `([A-Z][A-Za-z]+)` \| ([^|]+) \|", re.M)


def _spec_rows() -> dict[str, tuple[str, str]]:
    """name -> (category, state column) from the category decisions."""

    if not DECISIONS.is_dir():
        pytest.skip("HATG ISA category decisions are not available on this host")
    rows: dict[str, tuple[str, str]] = {}
    for category, decision_id in CATEGORY_DECISIONS.items():
        matches = sorted(DECISIONS.glob(f"*decision_{decision_id}.md"))
        assert matches, f"missing decision document for {category}"
        for name, state in ROW.findall(matches[0].read_text(encoding="utf-8").split("## 参数名契约", 1)[0]):
            assert name not in rows, f"{name} is listed twice"
            rows[name] = (category, state.strip())
    return rows


def test_catalog_holds_exactly_the_specified_instructions():
    """ISA 代码目录与各类别规范表逐条一致（指令集合完全相同）

    :design: decision_898b8911a2ff, decision_aa4fdbac8364
    """
    spec = _spec_rows()
    catalog = instruction_catalog()
    assert sorted(catalog) == sorted(spec)


def test_each_instruction_keeps_its_specified_category():
    """每条 ISA 指令的类别与类别规范表一致"""
    spec = _spec_rows()
    mismatched = {
        name: (declaration.category, spec[name][0])
        for name, declaration in instruction_catalog().items()
        if declaration.category != spec[name][0]
    }
    assert mismatched == {}
    assert set(categories()) == set(CATEGORY_DECISIONS)


def test_state_changing_and_streaming_flags_follow_the_specification():
    """ISA 指令的状态变更和流式标记与类别规范表一致"""
    spec = _spec_rows()
    wrong = {}
    for name, declaration in instruction_catalog().items():
        state = spec[name][1]
        streaming = "流式" in state
        if declaration.streaming != streaming:
            wrong[name] = ("streaming", declaration.streaming, state)
        if state.startswith("是") and not declaration.mutates:
            wrong[name] = ("mutates", declaration.mutates, state)
        if state.startswith("否") and declaration.mutates:
            wrong[name] = ("mutates", declaration.mutates, state)
    assert wrong == {}


@pytest.mark.parametrize("category", sorted(CATEGORY_DECISIONS))
def test_documented_argument_names_match_instruction_schema(category):
    """每个类别规范都有参数名契约表，表中列出的参数与指令 schema 声明一致

    :module: lineage.hatg
    :design: decision_898b8911a2ff"""
    if not DECISIONS.is_dir():
        pytest.skip("HATG ISA category decisions are not available on this host")
    paths = sorted(DECISIONS.glob(f"*decision_{CATEGORY_DECISIONS[category]}.md"))
    assert paths, category
    text = paths[0].read_text(encoding="utf-8")
    assert "## 参数名契约" in text, category
    parameter_table = text.split("## 参数名契约", 1)[1]
    rows = ROW.findall(parameter_table)
    names = [name for name, _ in rows]
    assert len(names) == len(set(names)), category
    documented = {name: re.findall(r"`([a-z][a-z_0-9]*)`", parameters)
                  for name, parameters in rows}
    declared = {name: [p.name for p in declaration.parameters]
                for name, declaration in instruction_catalog().items()
                if declaration.category == category}
    assert documented == declared


def test_documented_default_scheme_requirements_match_the_default_scheme():
    """每个类别规范的指令表都有「默认方案要求」列，覆盖本类全部指令，并与默认方案逐条一致，漂移就失败

    每行写的等级（@viewer、@admin 等，免检写作“免检”）与固定槽的 R 节点（“共享”等，按标题）必须等于
    default.json 给这条指令各 A 元素的分类。默认方案的分类以 decision_600ae27b0538 和 default.json 为准，
    类别规范引用它时由这条测试守住（decision_898b8911a2ff“有契约”）。

    :design: decision_898b8911a2ff, decision_600ae27b0538
    """
    from management_portal.permission_system.scheme.compiler import compile_scheme
    from management_portal.permission_system.scheme.repository import builtin_documents

    if not DECISIONS.is_dir():
        pytest.skip("HATG ISA category decisions are not available on this host")
    scheme = compile_scheme(builtin_documents()["default"])
    titles = {node_id: node.title for node_id, node in scheme.trees["R"].nodes.items()}
    declared: dict[str, set[str]] = {}
    for name, declaration in instruction_catalog().items():
        declared.setdefault(declaration.category, set()).add(name)
    uncovered, drifted = {}, {}
    for category, decision_id in CATEGORY_DECISIONS.items():
        text = sorted(DECISIONS.glob(f"*decision_{decision_id}.md"))[0].read_text(encoding="utf-8")
        checked = set()
        for table in re.findall(r"^(\| 指令 \|[^\n]*\|[ \t]*\n(?:\|[^\n]*\|[ \t]*\n?)+)", text, re.M):
            header = [cell.strip() for cell in table.split("\n", 1)[0].strip().strip("|").split("|")]
            if "默认方案要求" not in header:
                continue
            column = header.index("默认方案要求")
            for line in table.splitlines()[2:]:
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                name = re.fullmatch(r"`([A-Z][A-Za-z]+)`", cells[0])
                if not name:
                    continue
                written = cells[column] if column < len(cells) else ""
                documented = (set(re.findall(r"@[a-z_]+", written)) | ({"@free"} if "免检" in written else set()),
                              set(re.findall(r"“([^”]+)”", written)))
                slots = scheme.slots(name.group(1))
                expected = ({slot.a for slot in slots},
                            {titles[slot.resource] for slot in slots if slot.resource and slot.a != "@free"})
                checked.add(name.group(1))
                if documented != expected:
                    drifted[name.group(1)] = {"written": written, "default": expected}
        if checked != declared.get(category, set()):
            uncovered[category] = sorted(declared.get(category, set()) ^ checked)
    assert uncovered == {}
    assert drifted == {}


def test_declarations_are_complete_and_self_consistent():
    """ISA 指令声明字段齐全，参数不重名，流式指令只读且以 Watch 开头"""
    owners = {}
    for name, declaration in instruction_catalog().items():
        assert declaration.name == name
        assert declaration.owner in OWNER_MARKS, name
        assert declaration.description.strip(), name
        parameter_names = [parameter.name for parameter in declaration.parameters]
        assert len(parameter_names) == len(set(parameter_names)), name
        if declaration.target:
            assert declaration.target in parameter_names, (name, declaration.target)
        if declaration.streaming:
            assert not declaration.mutates and name.startswith("Watch"), name
        owners[declaration.owner] = owners.get(declaration.owner, 0) + 1
    assert set(owners) == set(OWNER_MARKS)


def test_every_owner_kernel_builds_with_a_handler_for_each_of_its_instructions():
    """每个 owner 的指令内核都能为其所有指令找到实现并成功构建

    A kernel refuses to start when an instruction it owns has no implementation."""

    from management_portal.hatg import kernel as kernel_module, routing

    kernel_module.reset_kernels_for_tests()
    try:
        for owner in ("lineage", "web", "resource", "execution"):
            assert routing.kernel_for(owner, required=True).owner == owner
    finally:
        kernel_module.reset_kernels_for_tests()


def test_the_society_service_rpc_contract_matches_its_domain_modules():
    """SocietyService 的 RPC 契约与各 domain 模块的处理函数一致

    SocietyService validates this at startup and refuses to run otherwise."""

    from management_portal.society_service import server

    server._validate_domain_handlers()
