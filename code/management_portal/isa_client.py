"""Shared client for Portal Web code calling HATG ISA instructions.

Page backends and Portal features build a ``tfi.hatg-instruction.v2``
call and hand it to the router, which executes it at the owning service
(decision_898b8911a2ff).  The caller is the authenticated user, Resident or
Portal service the feature acts for.
"""
from __future__ import annotations

import time
from typing import Any

from .hatg import routing
from .hatg.contracts import InstructionContext, InstructionError

# A committed change whose projection is not yet confirmed completes by repeating
# the call with the same idempotency key (only the projection is redone).
PROJECTION_RETRY_DELAYS = (0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 10.0)


def call(
    actor: str,
    instruction: str,
    arguments: dict[str, Any] | None = None,
    *,
    key: str = "",
    version: Any = None,
    entry: str = "web",
    trigger_path_id: str = "",
) -> Any:
    """Execute one instruction and return its result, raising its error."""

    payload: dict[str, Any] = {"instruction": instruction, "arguments": dict(arguments or {})}
    if key:
        payload["idempotency_key"] = key
    if version is not None:
        payload["expected_version"] = version
    context = InstructionContext(caller=actor, entry=entry, trigger_path_id=trigger_path_id)
    for delay in (*PROJECTION_RETRY_DELAYS, None):
        try:
            return routing.execute_or_raise(payload, context)
        except InstructionError as error:
            if not key or error.code != "instruction.projection_pending" or delay is None:
                raise
            time.sleep(delay)
