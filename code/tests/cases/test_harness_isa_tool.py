"""The HATG ISA as a Harness tool (decision_898b8911a2ff “调用入口”).

:module: isa.instructions
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from management_common.portal_database import portal_database
from management_portal.execution import harness_isa_tool
from management_portal.execution.harness import tool_call_scope
from management_portal.hatg import kernel as kernel_module
from management_portal.hatg.catalog import instruction_catalog
from management_portal.hatg.kernel import InstructionKernel, OwnerBinding
from management_portal.hatg.store import ExecutionStore


RESIDENT = "resident_aaaaaaaaaaaa"


@pytest.fixture
def kernels(monkeypatch):
    calls: list[tuple[str, str, dict[str, Any]]] = []
    catalog = instruction_catalog()

    def handler(name):
        def run(invocation):
            calls.append((name, invocation.caller, dict(invocation.arguments)))
            return {"call": len(calls)}
        return run

    execution_names = [name for name, item in catalog.items() if item.owner == "execution"]
    execution = InstructionKernel(
        OwnerBinding(owner="execution", handlers={name: handler(name) for name in execution_names}),
        store=ExecutionStore(portal_database(schema=f"isa_tool_{uuid.uuid4().hex[:8]}")),
        authorize=lambda *args: None,
    )
    stream_state = {"batches": [], "release": threading.Event()}

    def watch(invocation):
        calls.append(("WatchTriggerPath", invocation.caller, dict(invocation.arguments)))
        for batch in stream_state["batches"]:
            if batch == "wait":
                stream_state["release"].wait(5)
                continue
            yield batch

    lineage_names = [name for name, item in catalog.items() if "lineage" in item.owners]
    lineage = InstructionKernel(
        OwnerBinding(
            owner="lineage",
            handlers={name: handler(name) for name in lineage_names if not catalog[name].streaming},
            streams={name: watch for name in lineage_names if catalog[name].streaming},
        ),
        store=None,
        authorize=lambda *args: None,
    )
    monkeypatch.setattr(kernel_module, "_KERNELS", {"execution": execution, "lineage": lineage})
    yield SimpleNamespace(calls=calls, stream=stream_state)
    stream_state["release"].set()


SETTINGS = {
    "instruction": "UpdateProviderAccount",
    "arguments": {"account_id": "one", "max_concurrency": 1,
                  "dispatch_interval_min_seconds": 1, "dispatch_interval_max_seconds": 2},
}


def test_a_mutation_uses_the_tool_call_id_as_its_idempotency_key(kernels):
    """ISA 工具执行写指令时以 tool call ID 作幂等键，同一 ID 重试只回放结果"""
    first = harness_isa_tool.run_instruction(RESIDENT, dict(SETTINGS), call_id="call_1")
    retried = harness_isa_tool.run_instruction(RESIDENT, dict(SETTINGS), call_id="call_1")
    other = harness_isa_tool.run_instruction(RESIDENT, dict(SETTINGS), call_id="call_2")
    assert first["status"] == retried["status"] == other["status"] == "committed"
    assert retried["replayed"] is True and retried["instruction_id"] == first["instruction_id"]
    assert other["replayed"] is False
    assert [call[:2] for call in kernels.calls] == [("UpdateProviderAccount", RESIDENT)] * 2


def test_an_explicit_key_wins_over_the_tool_call_id(kernels):
    """显式传入的 idempotency_key 优先于 tool call ID"""
    first = harness_isa_tool.run_instruction(RESIDENT, {**SETTINGS, "idempotency_key": "mine"}, call_id="call_1")
    second = harness_isa_tool.run_instruction(RESIDENT, {**SETTINGS, "idempotency_key": "mine"}, call_id="call_2")
    assert second["replayed"] and second["instruction_id"] == first["instruction_id"]


def test_invalid_calls_become_failed_results(kernels):
    """未知指令或参数不合法的调用返回 failed 结果和错误码"""
    unknown = harness_isa_tool.run_instruction(RESIDENT, {"instruction": "Nope", "arguments": {}}, call_id="c")
    assert unknown["status"] == "failed" and unknown["error"]["code"] == "instruction.unknown"
    invalid = harness_isa_tool.run_instruction(RESIDENT, {"instruction": "GetUsage", "arguments": {}}, call_id="c")
    assert invalid["error"]["code"] == "instruction.invalid_arguments"


def test_streaming_returns_the_first_batch_and_its_cursor(kernels):
    """流式指令通过 ISA 工具返回第一批事件和 next_cursor"""
    kernels.stream["batches"] = [[{"cursor": "c1", "type": "invocation"}, {"cursor": "c2", "type": "done"}]]
    result = harness_isa_tool.run_instruction(RESIDENT, {
        "instruction": "WatchTriggerPath", "arguments": {"lineage_id": "lineage_aaaaaaaaaaaa", "trigger_path_id": "run_aaaaaaaaaaaa"},
        "wait_seconds": 5,
    }, call_id="c")
    assert result["status"] == "committed"
    assert result["instruction_id"].startswith("inst_ent_")
    assert [item["cursor"] for item in result["result"]["items"]] == ["c1", "c2"]
    assert result["result"]["next_cursor"] == "c2"


def test_streaming_without_news_returns_an_empty_batch_after_wait_seconds(kernels):
    """流式指令在 wait_seconds 内没有新事件时返回空批次并保留原游标"""
    kernels.stream["batches"] = ["wait"]
    result = harness_isa_tool.run_instruction(RESIDENT, {
        "instruction": "WatchTriggerPath",
        "arguments": {"lineage_id": "lineage_aaaaaaaaaaaa", "trigger_path_id": "run_aaaaaaaaaaaa", "after_cursor": 9},
        "wait_seconds": 0.2,
    }, call_id="c")
    assert result["result"] == {"items": [], "next_cursor": 9, "ended": False}


def test_the_tool_handler_reads_the_provider_call_id(kernels):
    """ISA 工具 handler 从当前 tool call 作用域读取 Provider 的调用 ID 作幂等键"""
    tool = harness_isa_tool.ResidentIsaTool().tool(RESIDENT)
    with tool_call_scope("provider-call-7"):
        first = json.loads(tool.handler(dict(SETTINGS)))
    with tool_call_scope("provider-call-7"):
        again = json.loads(tool.handler(dict(SETTINGS)))
    assert again["replayed"] and again["instruction_id"] == first["instruction_id"]
    assert "Instructions:" in tool.description and "UpdateProviderAccount" in tool.description


@dataclass(frozen=True)
class Invocation:
    request: Any
    harness_tools: tuple = field(default_factory=tuple)


@pytest.mark.parametrize("provider,metadata,attached", [
    ("codex", {"resident_id": RESIDENT}, True),
    ("claude", {"resident_id": RESIDENT}, True),
    ("kimi", {"resident_id": RESIDENT}, True),
    ("dsh", {"resident_id": RESIDENT}, True),
    ("codex", {}, False),
    ("codex", {"resident_id": "portal"}, False),
])
def test_the_tool_is_attached_only_to_resident_turns_on_tool_capable_providers(provider, metadata, attached):
    """hatg_instruction 工具只挂到支持工具的 Provider 上的 Resident turn"""
    request = SimpleNamespace(provider=provider, metadata=metadata, runtime_options={})
    prepared = harness_isa_tool.ResidentIsaTool().prepare_invocation(Invocation(request))
    names = [tool.name for tool in prepared.harness_tools]
    assert names == (["hatg_instruction"] if attached else [])
