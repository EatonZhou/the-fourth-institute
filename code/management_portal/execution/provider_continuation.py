"""Carrying a turn on after its Provider failed it or lost it.

A turn the Provider failed after taking its input is continued in the same
session as the Harness recovery policy allows
(``provider_error_continuation``); a turn whose route could not reach its
service from this host first waits for the Provider egress.  A turn that is
no longer running is continued with ``INTERRUPTED_TURN_CONTINUATION``.  The
input Portal sends to recover a turn is recorded as it goes out
(``report_recovery_input``).
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .backend_contracts import BackendContext, BackendInvocation, _positive_int
from .contracts import ExecutionControlSignal, ExecutionRecoveryDeferred
from .harness import ProviderErrorRecoveryPolicy
from .provider_egress import egress_state, location_restricted, route_upstream
from .provider_failures import is_egress_block_failure, is_network_failure, is_usage_limit_failure


# A Provider error with one of these in its text will fail again the same way.
_UNCONTINUABLE_ERROR_MARKERS = (
    "authenticat", "unauthorized", "api key", "api_key", "forbidden", "permission denied",
    "safety", "content policy", "policy violation", "insufficient", "balance", "billing", "credit",
    "context length", "context window", "prompt is too long", "maximum context", "too many tokens",
)
PROVIDER_CONTINUATION_WAIT_SECONDS = 540.0
# A turn that could not reach Anthropic from this host (refused for the
# location, or the VPS tunnel down while the host holds its default traffic
# closed) waits for the egress to come back: how long, how often it looks,
# and how many such continuations one Execution sends.  The outages seen on
# 10-07 lasted 30 to 150 seconds.
EGRESS_BLOCK_WAIT_SECONDS = 1800.0
EGRESS_PROBE_INTERVAL_SECONDS = 10.0
EGRESS_BLOCK_CONTINUE_ATTEMPTS = 5

# What continues a turn that is no longer running (its Provider process
# restarted, or a Worker left it): the Provider gets no input twice.
INTERRUPTED_TURN_CONTINUATION = "continue"


def report_recovery_input(
    context: BackendContext,
    *,
    provider: str,
    reason: str,
    prompt: str,
    input_delivery: str,
    turn_id: str,
    image_count: int = 0,
) -> None:
    """Record the input Portal hands a Provider to recover a turn, as it goes out.

    ``reason``: ``worker_handover`` (a handover interrupted a Codex turn),
    ``turn_interrupted`` (the Provider's process no longer ran the turn),
    ``provider_error`` or ``provider_transition``.  ``input_delivery``:
    ``continued`` (the Provider had the turn's input) or ``first_delivery``
    (it never had it; ``prompt`` is the turn's own input).  A Worker that
    attaches to a running turn sends nothing and records nothing here.
    """

    context.emit("recovery_input_sent", {
        "provider": provider, "reason": reason, "prompt": prompt,
        "input_delivery": input_delivery, "turn_id": turn_id,
        **({"image_count": image_count} if image_count else {}),
    })


def continuation_prompt_of(invocation: BackendInvocation) -> str:
    """The prompt of a continuation after a Provider error, per the Harness policy."""

    policy = getattr(invocation, "provider_error_recovery", None)
    return str(getattr(policy, "continuation_prompt", "") or INTERRUPTED_TURN_CONTINUATION)


def provider_error_continuation(
    error: BaseException,
    *,
    invocation: BackendInvocation,
    context: BackendContext,
    provider_id: str,
    turn_started: bool,
) -> tuple[str, int]:
    """The prompt and number of the continuation Portal sends now, or re-raise.

    The Harness attaches the recovery policy (``provider_error_continue``).
    An Adapter that declares ``error_continuation`` asks here after its turn
    failed.  The turn is continued in the same Provider session when the
    Provider had already taken the turn's input, the failure is one a second
    request can get past, Portal is not cancelling the Execution, and this
    Execution has an attempt left; the used attempts are kept with the
    recovery checkpoint, so another Worker does not start counting again.

    A turn whose route reaches a location-restricted service
    (``provider_egress``: Anthropic, through the VPS egress) and could not
    reach it from this host is not the Provider failing: refused for the
    location (``blocked``), or unable to connect while the probe cannot reach
    the service either (``unreachable``; the VPS tunnel is down).  The
    continuation waits until the service serves this host again and has its
    own attempts (``EGRESS_BLOCK_CONTINUE_ATTEMPTS``).  A connection failure
    while the probe gets through is an ordinary Provider error, and so is any
    failure on a route whose service serves every location.
    ``provider_error_continue_count`` counts every continuation, which names
    the continuation turn; ``egress_block_continue_count`` the ones after
    the egress was unavailable.
    """

    policy = getattr(invocation, "provider_error_recovery", None)
    current = getattr(context, "current_provider_recovery_state", None)
    state = current() if callable(current) else None
    if not isinstance(state, dict):
        # This attempt has not continued yet: the counts it started with.
        checkpoint = getattr(invocation, "recovery_checkpoint", None)
        state = checkpoint.get("provider_recovery_state") if isinstance(checkpoint, dict) else None
    state = dict(state) if isinstance(state, dict) else {}
    used = _positive_int(state.get("provider_error_continue_count"))
    egress_used = _positive_int(state.get("egress_block_continue_count"))
    text = str(error).casefold()
    session = getattr(invocation, "session", None)
    upstream = route_upstream(provider_id, str(getattr(session, "backend_route_id", "") or ""))
    restricted = location_restricted(upstream)
    egress = "blocked" if restricted and is_egress_block_failure(text) else ""
    if (
        not egress
        and restricted
        and policy is not None
        and turn_started
        and is_network_failure(text)
        and not context.is_cancelled()
        and egress_state(upstream) != "allowed"
    ):
        egress = "unreachable"
    egress_block = bool(egress)
    if (
        policy is None
        or (
            egress_used >= EGRESS_BLOCK_CONTINUE_ATTEMPTS
            if egress_block
            else used - egress_used >= policy.max_continue_attempts
        )
        or not turn_started
        or isinstance(error, (InterruptedError, ExecutionControlSignal, ValueError, TypeError))
        or context.is_cancelled()
        or is_usage_limit_failure(text)
        or (not egress_block and any(marker in text for marker in _UNCONTINUABLE_ERROR_MARKERS))
    ):
        raise error
    if egress_block:
        context.emit("provider_egress_unavailable", {
            "provider": provider_id, "upstream": upstream, "state": egress, "error": str(error)[:300],
        })
        if not wait_for_provider_egress(context, upstream):
            raise error
    elif not wait_before_continuation(policy, context):
        raise error
    attempt = used + 1
    context.set_provider_recovery_state({
        **state, "provider_error_continue_count": attempt,
        **({"egress_block_continue_count": egress_used + 1} if egress_block else {}),
    })
    context.emit("agent_harness_provider_error_continue", {
        "attempt": attempt,
        "max_attempts": EGRESS_BLOCK_CONTINUE_ATTEMPTS if egress_block else policy.max_continue_attempts,
        "provider": provider_id,
        "reason": f"egress_{egress}" if egress_block else "provider_error",
        "error": str(error)[:300],
    })
    # The continuation starts because the Provider failed.  A Worker handover
    # whose turn kept running started no turn, and its pending reason must
    # not name this start.
    context.set_turn_start_reason("provider_recovery", replace_pending=True)
    return policy.continuation_prompt, attempt


def wait_before_continuation(policy: ProviderErrorRecoveryPolicy, context: BackendContext) -> bool:
    """Wait the policy's pause before the continuation; False when the Execution is cancelled meanwhile."""

    remaining = max(0.0, float(policy.continue_delay_seconds))
    while remaining > 0:
        if context.is_cancelled():
            return False
        step = min(0.5, remaining)
        time.sleep(step)
        remaining -= step
    return not context.is_cancelled()


def wait_for_provider_egress(
    context: BackendContext,
    upstream: str,
    *,
    probe: Callable[[], str] | None = None,
) -> bool:
    """Wait until the service ``upstream`` serves this host's requests again.

    False when the Execution is cancelled or the egress does not come back
    within ``EGRESS_BLOCK_WAIT_SECONDS``.  A Worker handing the Execution
    over stops waiting; the next Worker continues the turn.
    """

    probe = probe or (lambda: egress_state(upstream))
    deadline = time.monotonic() + EGRESS_BLOCK_WAIT_SECONDS
    while True:
        if context.is_cancelled():
            return False
        if context.is_handover():
            raise ExecutionRecoveryDeferred(
                "Worker handover: the turn waits for the Provider egress on the next Worker",
                retry_after_seconds=EGRESS_PROBE_INTERVAL_SECONDS,
            )
        if probe() == "allowed":
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        pause = min(EGRESS_PROBE_INTERVAL_SECONDS, remaining)
        while pause > 0:
            if context.is_cancelled() or context.is_handover():
                break
            step = min(0.5, pause)
            time.sleep(step)
            pause -= step


def run_provider_continuation(
    run: Callable[[], Any],
    *,
    context: BackendContext,
    unavailable: tuple[type[BaseException], ...],
) -> Any:
    """Send the continuation, waiting for a Provider process that is restarting."""

    deadline = time.monotonic() + PROVIDER_CONTINUATION_WAIT_SECONDS
    while True:
        try:
            return run()
        except unavailable:
            if context.is_cancelled() or time.monotonic() >= deadline:
                raise
            time.sleep(5)
