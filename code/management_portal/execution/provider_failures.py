from __future__ import annotations

from collections.abc import Mapping
import re


_USAGE_LIMIT_CODES = frozenset({
    "insufficient_quota",
    "provider_usage_limit",
    "usage_limit",
    "usage_limit_exceeded",
})
_USAGE_LIMIT_MARKERS = (
    "you've hit your usage limit",
    "you’ve hit your usage limit",
    "you've hit your weekly limit",
    "you’ve hit your weekly limit",
    "you've hit your limit",
    "usage limit",
    "purchase more credits",
    "insufficient_quota",
    "quota exhausted",
    # A prepaid account out of money: Kimi ("credit balance is running low ...
    # please recharge"), Anthropic ("credit balance is too low"), DeepSeek
    # ("Insufficient Balance").  Kimi sends these as HTTP 429.
    "credit balance",
    "insufficient balance",
    "please recharge",
)
# Anthropic refuses a request from a location it does not serve with HTTP 403
# "Request not allowed"; Claude Code reports it as "Failed to authenticate.
# API Error: 403 Request not allowed".  This host reaches Anthropic through
# its VPS egress; before 10-07 the egress health check failed over to the
# physical network, and every request in that window got this answer.
_EGRESS_BLOCK_MARKERS = ("403 request not allowed",)
# What Claude Code reports when it could not reach the API at all.  Since
# 10-07 the host holds default traffic closed while the VPS tunnel is down, so
# an outage of the tunnel ends a turn with one of these.
_NETWORK_FAILURE_MARKERS = (
    "unable to connect to api",
    "connection error",
    "request timed out",
    "connection refused",
    "connection reset",
    "temporary failure in name resolution",
    "network is unreachable",
    "socket hang up",
    "fetch failed",
    "econnreset",
    "econnrefused",
    "etimedout",
    "enetunreach",
    "ehostunreach",
    "eai_again",
    "enotfound",
)


def _failure_chain(error: object):
    current = error
    seen: set[int] = set()
    for _depth in range(4):
        if not isinstance(current, Mapping) or id(current) in seen:
            return
        seen.add(id(current))
        yield current
        nested = current.get("error")
        if not isinstance(nested, Mapping):
            return
        current = nested


def failure_code(error: object) -> str:
    for item in _failure_chain(error):
        code = str(item.get("code") or item.get("error_code") or "").strip().casefold()
        if code:
            return code
    return ""


def failure_message(error: object, fallback: str = "") -> str:
    if isinstance(error, Mapping):
        for item in _failure_chain(error):
            for key in ("message", "error", "reason"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return str(fallback or "").strip()
    return str(error or fallback or "").strip()


def is_usage_limit_failure(error: object, *, code: str = "") -> bool:
    normalized_code = str(code or failure_code(error)).strip().casefold()
    if normalized_code in _USAGE_LIMIT_CODES:
        return True
    message = failure_message(error).casefold()
    return any(marker in message for marker in _USAGE_LIMIT_MARKERS)


def is_egress_block_failure(error: object) -> bool:
    """Whether the Provider refused the request for the location it came from."""

    message = failure_message(error).casefold()
    return any(marker in message for marker in _EGRESS_BLOCK_MARKERS)


def is_network_failure(error: object) -> bool:
    """Whether the Provider client could not reach its API at all."""

    message = failure_message(error).casefold()
    return any(marker in message for marker in _NETWORK_FAILURE_MARKERS)


def is_authentication_failure(error: object, *, provider: str = "") -> bool:
    """Recognize explicit credential failures, not network or regional denial."""
    if getattr(error, "error_code", "") == "provider_authentication_failed" or failure_code(error) == "provider_authentication_failed":
        return True
    if is_egress_block_failure(error) or is_network_failure(error):
        return False
    message = failure_message(error).casefold()
    markers = ("provider_authentication_failed", "authentication_error", "unauthorized",
               "access token could not be refreshed", "authentication token is expired",
               "please run /login", "please log in", "not logged in", "login expired",
               "authentication failed", "oauth session expired")
    return any(marker in message for marker in markers) or provider == "claude" and bool(re.search(r"\b401\b", message))
