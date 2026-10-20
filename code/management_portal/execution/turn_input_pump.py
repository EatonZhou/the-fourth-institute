"""Input that reaches a Provider turn while the turn runs.

Two sources queue such input for a running turn: Send now (the Execution's
input controls, delivered by the Worker) and the reminders of the Harness loop
governor (``provider_event_processor``).  Codex takes it through its app
server's ``turn/steer``.  The other Adapters run their turn as one blocking
call, so a ``TurnInputPump`` polls both sources beside that call and hands
each item to the Adapter's ``deliver`` function, which passes it to the
Provider's own mechanism for input between the steps of a running turn.

An item the Provider did not take is recorded as failed where it came from:
Send now then runs the message as the next turn, and the governor counts the
reminder as not applied.
"""
from __future__ import annotations

from management_common.format_tags import schema_matches

import threading
from typing import Any, Callable

from .contracts import (
    EXECUTION_INPUT_CONTROL_SCHEMA,
    EXECUTION_TURN_SETTINGS_CONTROL_SCHEMA,
)
from .control_messages import report_harness_control

POLL_SECONDS = 0.25
STOP_WAIT_SECONDS = 20.0

# deliver(turn_id, input_items, control_id) -> (taken, error)
Deliver = Callable[[str, list[dict[str, Any]], str], tuple[bool, str]]


def text_input(items: list[dict[str, Any]]) -> str:
    """The text of input items, or "" when an item is not text."""

    texts: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "text":
            return ""
        text = str(item.get("text") or "")
        if text:
            texts.append(text)
    return "\n\n".join(texts)


class TurnInputPump:
    """Polls queued turn input while a Provider turn runs and delivers it."""

    def __init__(
        self,
        *,
        context: Any,
        event_processor: Any,
        deliver: Deliver,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._context = context
        self._event_processor = event_processor
        self._deliver = deliver
        self._poll_seconds = max(0.01, float(poll_seconds))
        self._lock = threading.Lock()
        self._active_turn = ""
        # Turn ids a control may name: Portal binds a control to the turn id
        # it last saw, which is the Provider's turn or the Execution's own.
        self._turn_ids: set[str] = set()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        has_external = bool(
            getattr(self._context, "has_turn_control_provider", lambda: False)()
        )
        return has_external or self._event_processor is not None

    def turn_started(self, turn_id: str, *aliases: str) -> None:
        """The Provider runs ``turn_id`` now; input goes to it from here on."""

        turn_id = str(turn_id or "").strip()
        with self._lock:
            self._active_turn = turn_id
            self._turn_ids.update(
                value for value in (turn_id, *(str(a or "").strip() for a in aliases)) if value
            )

    def turn_ended(self) -> None:
        """The Provider's turn is over; queued input waits for the next turn."""

        with self._lock:
            self._active_turn = ""

    def __enter__(self) -> "TurnInputPump":
        if self.enabled:
            self._thread = threading.Thread(
                target=self._run, name="turn-input-pump", daemon=True,
            )
            self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.turn_ended()
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(STOP_WAIT_SECONDS)

    def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                delivered_any = self.pump_once()
            except Exception:  # noqa: BLE001 - the pump must never fail the turn
                delivered_any = False
            if not delivered_any:
                self._stopped.wait(self._poll_seconds)

    def pump_once(self) -> bool:
        """Deliver at most one queued item; whether there was one."""

        with self._lock:
            turn_id = self._active_turn
            accepted = set(self._turn_ids)
        if not turn_id:
            return False
        external = True
        control = self._context.next_turn_control()
        if control is None and self._event_processor is not None:
            external = False
            control = self._event_processor.next_control()
        if not isinstance(control, dict):
            return False
        taken, error = self._attempt(control, turn_id, accepted)
        self._record(control, external, taken, error)
        return True

    def _attempt(
        self,
        control: dict[str, Any],
        turn_id: str,
        accepted: set[str],
    ) -> tuple[bool, str]:
        if schema_matches(control.get("schema"), "tfi.execution-turn-settings-control.v1"):
            return False, "turn_settings_unsupported"
        action = str(control.get("action") or "steer").strip()
        if action != "steer":
            return False, "turn_control_unsupported"
        expected = str(control.get("expected_turn_id") or "").strip()
        if expected and expected not in accepted:
            return False, "active provider turn changed"
        items = control.get("input")
        if not isinstance(items, list) or not items:
            return False, "turn_control_input_missing"
        if any(isinstance(item, dict) and item.get("type") == "user_input_response" for item in items):
            return False, "user_input_response_unsupported"
        with self._lock:
            if self._active_turn != turn_id:
                return False, "active provider turn changed"
        control_id = str(control.get("control_id") or "")
        try:
            taken, error = self._deliver(turn_id, [dict(item) for item in items], control_id)
        except Exception as exc:  # noqa: BLE001 - an undelivered item runs later
            return False, type(exc).__name__
        return bool(taken), "" if taken else str(error or "not_taken")

    def _record(self, control: dict[str, Any], external: bool, taken: bool, error: str) -> None:
        try:
            if (
                external
                or schema_matches(control.get("schema"), "tfi.execution-input-control.v1")
                or schema_matches(control.get("schema"), "tfi.execution-turn-settings-control.v1")
            ):
                self._context.record_turn_control_result(control, taken, error)
            elif self._event_processor is not None:
                self._event_processor.record_control_result(control, taken, error)
                if taken:
                    report_harness_control(self._context, control)
        except Exception:  # noqa: BLE001 - a lost receipt falls back to the next turn
            pass


class _NoTurnInput:
    """The pump of a Provider that takes no input during a turn: does nothing."""

    enabled = False

    def turn_started(self, turn_id: str, *aliases: str) -> None:
        pass

    def turn_ended(self) -> None:
        pass

    def __enter__(self) -> "_NoTurnInput":
        return self

    def __exit__(self, *_exc: object) -> None:
        pass


NO_TURN_INPUT = _NoTurnInput()
