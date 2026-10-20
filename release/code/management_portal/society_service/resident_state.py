from __future__ import annotations

INTAKE_DOMAIN = "intake"
INTAKE_FIELD = "intake_state"
INTAKE_OPEN = "open"
INTAKE_CLOSED = "closed"
INTAKE_VALUES = frozenset({INTAKE_OPEN, INTAKE_CLOSED})
INTAKE_TRANSITIONS = frozenset({"open", "close"})
INTAKE_DEFAULT = INTAKE_OPEN
INTAKE_CLOSED_QUEUE_REASON = "intake_closed"


def normalize_intake_state(value: object) -> str:
    """Return the canonical persisted intake value."""

    normalized = str(value or "").strip().lower()
    return normalized if normalized in INTAKE_VALUES else INTAKE_DEFAULT


def intake_state_for_transition(transition: object) -> str:
    """Map a state-changing action to its canonical persisted value."""

    action = str(transition or "").strip().lower()
    if action not in INTAKE_TRANSITIONS:
        raise ValueError("intake transition 必须是 open 或 close")
    return INTAKE_OPEN if action == "open" else INTAKE_CLOSED
