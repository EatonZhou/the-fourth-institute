"""Duplicate-read removal for Portal's own tools.

Found in the Harness experiment: hash_aware_read_dedup only knew generic read
tool names (read_file, cat, ...), none of which is a Portal tool, and every
ISA result carries its own instruction ID, so two identical reads never
matched.  On Claude Code, Kimi Code and DSH the Portal tools are the only
results the feature can touch, so it could not act at all.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from management_portal.execution.harness import (
    AgentLoopToolBatch,
    AgentLoopToolResult,
    HarnessTool,
    stable_json_result,
)
from management_portal.execution.harness_isa_tool import ResidentIsaTool
from management_portal.execution.harness_read_dedup import HashAwareReadDedupFeature

PLACEHOLDER = "[AgentHarness removed a duplicate"


def _isa_result(instruction: str, instruction_id: str, payload: object) -> str:
    return json.dumps({
        "schema": "tfi.hatg-instruction-result.v2", "instruction_id": instruction_id,
        "instruction": instruction, "status": "committed", "replayed": False, "result": payload,
    })


def _batch(*items: tuple[str, str, dict, str]) -> AgentLoopToolBatch:
    return AgentLoopToolBatch(items=tuple(
        AgentLoopToolResult(
            call_id=call_id, tool_name=name, arguments=arguments,
            result={"success": True, "contentItems": [{"type": "inputText", "text": text}]}, source="harness",
        )
        for call_id, name, arguments, text in items
    ))


def _texts(batch: AgentLoopToolBatch) -> list[str]:
    return [item.result["contentItems"][0]["text"] for item in batch.items]


def _run(batch: AgentLoopToolBatch, tools: tuple[HarnessTool, ...]):
    return HashAwareReadDedupFeature().after_tool_batch(
        batch=batch, provider=None, invocation=SimpleNamespace(harness_tools=tools),
        context=None, config={"min_output_tokens": 32}, state={},
    )


def _isa_tool() -> HarnessTool:
    return ResidentIsaTool().tool("resident_0123456789ab")


BIG = {"instructions": [f"Instruction{n}" for n in range(200)]}


def test_a_repeated_read_only_instruction_is_answered_once() -> None:
    """重复的只读指令只回答一次

    同一批中重复调用同一只读指令且结果相同时，后续结果替换为指向首次调用的占位说明。
    """
    arguments = {"instruction": "ListInstructions", "arguments": {}}
    outcome = _run(_batch(
        ("call-1", "hatg_instruction", arguments, _isa_result("ListInstructions", "inst_1", BIG)),
        ("call-2", "hatg_instruction", arguments, _isa_result("ListInstructions", "inst_2", BIG)),
    ), (_isa_tool(),))

    first, second = _texts(outcome.batch)
    assert outcome.applied is True
    assert '"instruction_id": "inst_1"' in first
    assert second.startswith(PLACEHOLDER) and "call_id:call-1" in second


def test_a_changed_result_or_an_instruction_that_writes_is_kept() -> None:
    """结果变化或会写入的指令不去重

    相同调用的结果有变化，或指令会修改状态时，工具结果原样保留。
    """
    reads = {"instruction": "ListInstructions", "arguments": {}}
    changed = _run(_batch(
        ("call-1", "hatg_instruction", reads, _isa_result("ListInstructions", "inst_1", BIG)),
        ("call-2", "hatg_instruction", reads, _isa_result("ListInstructions", "inst_2", {**BIG, "extra": 1})),
    ), (_isa_tool(),))
    assert changed.applied is False

    writes = {"instruction": "DeliverMessage", "arguments": {"resident_id": "resident_0123456789ab"}}
    same_write = _run(_batch(
        ("call-1", "hatg_instruction", writes, _isa_result("DeliverMessage", "inst_1", BIG)),
        ("call-2", "hatg_instruction", writes, _isa_result("DeliverMessage", "inst_2", BIG)),
    ), (_isa_tool(),))
    assert same_write.applied is False


def test_resource_reads_match_whether_or_not_the_cache_answered() -> None:
    """资源读取无论是否命中缓存都能匹配

    资源读取结果来自缓存或实际读取时，去重比较的结果一致。
    """
    tool = HarnessTool(
        name="read_portal_resource", description="", input_schema={"type": "object"}, handler=lambda _a: "",
        read_only=True, stable_result=stable_json_result("cache_hit"),
    )
    body = {"address": "decision_x", "content": "text " * 200}
    arguments = {"address": "decision_x"}
    outcome = _run(_batch(
        ("call-1", "read_portal_resource", arguments, json.dumps({**body, "cache_hit": False})),
        ("call-2", "read_portal_resource", arguments, json.dumps({**body, "cache_hit": True})),
    ), (tool,))
    assert _texts(outcome.batch)[1].startswith(PLACEHOLDER)


def test_a_tool_that_does_not_say_it_reads_is_left_alone() -> None:
    """未声明只读的工具不参与去重

    没有声明只读的工具调用结果不做去重处理。
    """
    tool = HarnessTool(name="jev_decide", description="", input_schema={"type": "object"}, handler=lambda _a: "")
    text = json.dumps({"answers": ["x" * 400]})
    outcome = _run(_batch(("call-1", "jev_decide", {"q": 1}, text), ("call-2", "jev_decide", {"q": 1}, text)), (tool,))
    assert outcome.applied is False
