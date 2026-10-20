"""The HATG ISA as a Harness tool (decision_898b8911a2ff “调用入口”).

A working Resident calls any named instruction through one tool.  The
execution layer knows which Resident is running and supplies it as the
caller; the call goes straight to the owning service without an HTTP
loopback.  A mutation without an idempotency key uses the model's tool-call
ID, so a retried tool call runs once.  A streaming instruction returns its
first batch of new items, or nothing after ``wait_seconds`` (a parameter of
this tool, not of the instruction), together with the cursor to resume from.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any, Callable

from .harness import HarnessTool, attach_harness_tools, current_tool_call_id, stable_json_result


ISA_TOOL_NAME = "hatg_instruction"
DEFAULT_WAIT_SECONDS = 30.0
MAX_WAIT_SECONDS = 300.0


def _catalog_text() -> str:
    from management_portal.hatg.catalog import categories, instruction_catalog

    groups: dict[str, list[str]] = {}
    for name, declaration in sorted(instruction_catalog().items()):
        groups.setdefault(declaration.category, []).append(name)
    titles = categories()
    return "\n".join(
        f"- {titles.get(category, category)}: {', '.join(names)}"
        for category, names in groups.items()
    )


def _description() -> str:
    return (
        "Call one Portal HATG ISA instruction as the current Resident. "
        "Pass the instruction name and its arguments; use GetInstructionSchema "
        "{instruction} to read an instruction's parameters before first use, and "
        "ListInstructions to browse. Optional idempotency_key makes a mutation run "
        "once (defaults to this tool call's ID); optional expected_version compares "
        "the target's version token before changing it. Streaming instructions "
        "(Watch*) return the first batch of new items or an empty batch after "
        "wait_seconds, plus next_cursor to pass as arguments.after_cursor.\n"
        "Instructions:\n" + _catalog_text()
    )


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "instruction": {"type": "string", "minLength": 1, "maxLength": 80},
        "arguments": {"type": "object"},
        "idempotency_key": {"type": "string", "maxLength": 240},
        "expected_version": {"type": ["integer", "string", "object"]},
        "wait_seconds": {"type": "number", "minimum": 0, "maximum": MAX_WAIT_SECONDS},
    },
    "required": ["instruction"],
    "additionalProperties": False,
}


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _first_batch(
    subscribe: Callable[[], Any],
    wait_seconds: float,
) -> tuple[list[dict[str, Any]], bool]:
    """Wait for the first batch of a stream; (items, ended)."""

    results: queue.Queue = queue.Queue(maxsize=1)
    stop = threading.Event()

    def pump() -> None:
        stream = None
        try:
            stream = subscribe()
            for batch in stream:
                if stop.is_set():
                    return
                if batch:
                    results.put(("batch", list(batch)))
                    return
            results.put(("end", []))
        except Exception as exc:  # noqa: BLE001 - reported to the caller below
            results.put(("error", exc))
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass

    threading.Thread(target=pump, name="hatg-harness-stream", daemon=True).start()
    try:
        kind, value = results.get(timeout=max(0.0, wait_seconds))
    except queue.Empty:
        stop.set()
        return [], False
    if kind == "error":
        raise value
    return value, kind == "end"


def run_instruction(caller: str, arguments: dict[str, Any], *, call_id: str = "", execution_id: str = "") -> dict[str, Any]:
    """Execute one tool call as the Resident and return the v2 result payload."""

    from management_portal.hatg import routing
    from management_portal.hatg.contracts import (
        RESULT_SCHEMA,
        InstructionCall,
        InstructionContext,
        failed_result,
        normalize_error,
    )
    from management_portal.hatg.kernel import new_instruction_id

    context = InstructionContext(caller=caller, entry="harness", execution_id=execution_id)
    try:
        call = InstructionCall.from_payload({
            key: arguments[key]
            for key in ("instruction", "arguments", "idempotency_key", "expected_version")
            if key in arguments
        })
        declaration = routing.declaration_for(call.instruction)
    except Exception as exc:  # noqa: BLE001
        return failed_result(new_instruction_id("entry"), str(arguments.get("instruction") or ""),
                             normalize_error(exc)).to_payload()
    if declaration.mutates and not call.idempotency_key and call_id:
        call = InstructionCall(
            instruction=call.instruction,
            arguments=call.arguments,
            idempotency_key=f"tool:{call_id}"[:240],
            expected_version=call.expected_version,
        )
    if not declaration.streaming:
        return routing.execute(call, context).to_payload()
    wait = float(arguments.get("wait_seconds", DEFAULT_WAIT_SECONDS) or 0)
    wait = min(MAX_WAIT_SECONDS, max(0.0, wait))

    def subscribe():
        yield from routing.stream(call, context)

    try:
        items, ended = _first_batch(subscribe, wait)
    except Exception as exc:  # noqa: BLE001
        return failed_result(new_instruction_id("entry"), call.instruction, normalize_error(exc)).to_payload()
    cursor = next(
        (item.get("cursor") for item in reversed(items) if isinstance(item, dict) and item.get("cursor") is not None),
        call.arguments.get("after_cursor"),
    )
    return {
        "schema": RESULT_SCHEMA,
        "instruction_id": new_instruction_id("entry"),
        "instruction": call.instruction,
        "status": "committed",
        "replayed": False,
        "result": {"items": items, "next_cursor": cursor, "ended": ended},
    }


def _metadata(invocation: Any) -> dict[str, Any]:
    metadata = getattr(getattr(invocation, "request", None), "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _only_reads(arguments: dict[str, Any]) -> bool:
    """Whether the instruction a call names neither changes state nor streams."""

    from management_portal.hatg import routing

    try:
        declaration = routing.declaration_for(str(arguments.get("instruction") or ""))
    except Exception:  # noqa: BLE001 - an unknown instruction is not a read
        return False
    return not declaration.mutates and not declaration.streaming


def _resident_caller(invocation: Any) -> str:
    resident_id = str(_metadata(invocation).get("resident_id") or "").strip().lower()
    return resident_id if resident_id.startswith("resident_") else ""


class ResidentIsaTool:
    """Attach the ISA tool to every Resident turn whose Adapter offers Portal tools."""


    def tool(self, caller: str, execution_id: str = "") -> HarnessTool:
        def handler(arguments: dict[str, Any]) -> str:
            return _dumps(run_instruction(caller, dict(arguments or {}), call_id=current_tool_call_id(),
                                          execution_id=execution_id))

        return HarnessTool(
            name=ISA_TOOL_NAME,
            description=_description(),
            input_schema=dict(INPUT_SCHEMA),
            handler=handler,
            read_only=_only_reads,
            # Every call gets its own instruction ID.
            stable_result=stable_json_result("instruction_id", "replayed"),
        )

    def prepare_invocation(self, invocation: Any) -> Any:
        request = getattr(invocation, "request", None)
        caller = _resident_caller(invocation)
        if not caller:
            return invocation
        execution_id = str(getattr(request, "execution_id", "") or "")
        prepared, _added, _reason = attach_harness_tools(invocation, (self.tool(caller, execution_id),))
        return prepared
