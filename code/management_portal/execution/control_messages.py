from __future__ import annotations


AGENT_HARNESS_RUNTIME_CHECKPOINT_TAG = "agent-harness-runtime-checkpoint"


# A reminder of the Harness loop governor that reached a running turn.
HARNESS_CONTROL_EVENT = "harness_control_delivered"


def report_harness_control(context: object, control: dict) -> None:
    """Record a Harness reminder the Provider took into its running turn, as it went in.

    The Provider received it like any input, so it belongs to the trace and
    to the session's context.
    """

    items = control.get("input") if isinstance(control.get("input"), list) else []
    text = "\n\n".join(
        str(item.get("text") or "") for item in items
        if isinstance(item, dict) and item.get("type") == "text" and str(item.get("text") or "").strip()
    )
    emit = getattr(context, "emit", None)
    if not text or not callable(emit):
        return
    metadata = control.get("metadata") if isinstance(control.get("metadata"), dict) else {}
    emit(HARNESS_CONTROL_EVENT, {
        "control_id": str(control.get("control_id") or ""),
        "reason": str(control.get("reason") or ""),
        "control_kind": str(metadata.get("kind") or ""),
        "text": text,
    })
