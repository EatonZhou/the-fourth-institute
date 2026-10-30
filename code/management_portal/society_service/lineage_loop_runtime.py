"""Loop component runtime (decision_4adcc1ff2f2c “Packet、算子与循环”).

A loop counts the rounds of a back-and-forth.  Each arrival on ``invoke``
adds one round and leaves through ``continue``; exceeding ``max_rounds`` or an
arrival on ``done`` leaves through ``stop``.  With ``completion=complete`` a
stopped loop stays stopped until ``ResetComponentRuntime``; with ``rearm`` the
next ``invoke`` starts counting again.  Rounds, generation and the paused flag
live in the Program's generic operator runtime.
"""
from __future__ import annotations

from typing import Any, Callable

from . import lineage_primitives, lineage_program_runtime, lineage_program_vocabulary

Dispatch = Callable[..., dict[str, Any]]


def _state(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "round": max(0, int(raw.get("round") or 0)),
        "stopped": bool(raw.get("stopped")),
        "paused": bool(raw.get("paused")),
        "generation": max(0, int(raw.get("generation") or 0)),
    }


def view(lineage_id: str, loop_id: str, loop: dict[str, Any]) -> dict[str, Any]:
    state = _state(lineage_program_runtime.read_operator_state(lineage_id, loop_id))
    return {
        "component_id": loop_id,
        "kind": "loop",
        "round": state["round"],
        "max_rounds": int(loop.get("max_rounds") or 1),
        "completion": str(loop.get("completion") or "complete"),
        "state": "paused" if state["paused"] else "stopped" if state["stopped"] else "active",
        "paused": state["paused"],
        "generation": state["generation"],
    }


def set_paused(lineage_id: str, loop_id: str, paused: bool) -> None:
    with lineage_program_runtime.operator_state_transaction(lineage_id, loop_id) as raw:
        if paused:
            raw["paused"] = True
        else:
            raw.pop("paused", None)


def reset(lineage_id: str, loop_id: str) -> int:
    with lineage_program_runtime.operator_state_transaction(lineage_id, loop_id) as raw:
        state = _state(raw)
        generation = state["generation"] + 1
        raw.clear()
        raw["generation"] = generation
        if state["paused"]:
            raw["paused"] = True  # reset only clears the round count
    return generation


def _advance(lineage_id: str, loop_id: str, loop: dict[str, Any], port: str) -> tuple[str, dict[str, Any]]:
    """Commit one arrival and return the output port it leaves through ('' for none)."""

    maximum = max(1, int(loop.get("max_rounds") or 1))
    rearm = str(loop.get("completion") or "complete") == "rearm"
    with lineage_program_runtime.operator_state_transaction(lineage_id, loop_id) as raw:
        state = _state(raw)
        # A paused loop starts no new round; the round in progress may still finish.
        if state["paused"] and port != "done":
            return "", {**state, "status": "paused"}
        if port == "done":
            if state["stopped"]:
                return "", {**state, "status": "stopped"}
            raw["stopped"] = True
            return "stop", {**_state(raw), "status": "done"}
        if state["stopped"]:
            if not rearm:
                return "", {**state, "status": "stopped"}
            raw["stopped"] = False
            raw["round"] = 0
            state = _state(raw)
        if state["round"] >= maximum:
            raw["stopped"] = True
            return "stop", {**_state(raw), "status": "max_rounds"}
        raw["round"] = state["round"] + 1
        return "continue", {**_state(raw), "status": "continue"}


def receive(
    lineage_id: str,
    plan: dict[str, Any],
    loop_id: str,
    port: str,
    event: lineage_primitives.LineageEvent,
    actor: str,
    *,
    dispatch_packets: Dispatch,
    packet: dict[str, Any],
) -> dict[str, Any]:
    loop = (plan.get("loops") or {}).get(loop_id)
    if not isinstance(loop, dict):
        raise ValueError("循环组件已不存在")
    output, state = _advance(lineage_id, loop_id, loop, port if port in lineage_program_vocabulary.LOOP_INPUT_PORTS else "invoke")
    result: dict[str, Any] = {"loop_id": loop_id, "input_port": port, **state}
    if not output:
        return result
    payload = dict(event.payload)
    payload.update({
        "loop_id": loop_id,
        "source_port": output,
        "round": state["round"],
        "max_rounds": int(loop.get("max_rounds") or 1),
        "source_event_id": event.id,
        "generation": state["generation"],
    })
    outgoing = lineage_primitives.make_event(
        event.type,
        subject_resident_id=event.subject_resident_id,
        payload=payload,
        correlation=f"{loop_id}:{output}:{state['generation']}:{state['round']}:{event.id}",
        identity=dict(event.identity),
    )
    result["output_port"] = output
    result["dispatch"] = dispatch_packets(loop_id, output, outgoing, packet)
    return result
