"""Whether the service a Provider's route sends its model requests to serves this host now.

Each route declares the service its model requests go to
(``AgentProvider.upstream_for``); what Portal knows about reaching each
service is here.  Anthropic serves only some locations: this host reaches it
through the VPS egress, and it refuses a request from an unserved location
with 403 "Request not allowed" before it looks at credentials.  A probe
without credentials is never refused for its permissions, so any 403 from
such a service is the location.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Upstream:
    """A service that answers the model requests of some routes."""

    # A public endpoint that answers without credentials or model work.
    probe_url: str
    # It serves only some locations; this host reaches it through the VPS
    # egress, which can fail open to a network it refuses.
    location_restricted: bool = False


PROBE_TIMEOUT_SECONDS = 10.0
UPSTREAMS = {
    "anthropic": Upstream("https://api.anthropic.com/v1/models", location_restricted=True),
    "openai": Upstream("https://chatgpt.com/backend-api/models"),
    "moonshot": Upstream(""),
    "deepseek": Upstream("https://api.deepseek.com/models"),
}


def route_upstream(provider: str, route: str = "") -> str:
    """The service a Provider's route sends its model requests to ("" for an unknown Provider)."""

    from .provider_catalog import AGENT_PROVIDERS

    definition = AGENT_PROVIDERS.get(str(provider or ""))
    return definition.upstream_for(route) if definition else ""


def location_restricted(upstream: str) -> bool:
    target = UPSTREAMS.get(upstream)
    return bool(target and target.location_restricted)


def _answer(upstream: str, timeout: float) -> int | None:
    """The HTTP status the service gives a probe, ``None`` when it cannot be reached."""

    target = UPSTREAMS.get(upstream)
    if target is None:
        return None
    probe_url = target.probe_url
    if upstream == "moonshot":
        from management_portal.kimi_code_server import provider_profile

        profile = provider_profile()
        probe_url = str(profile.get("base_url") or "").rstrip("/") + "/models"
    request = urllib.request.Request(probe_url, headers={"User-Agent": "tfi-egress-probe/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except (OSError, ValueError):
        return None


def _state(upstream: str, status: int | None) -> str:
    if status is None:
        return "unreachable"
    return "blocked" if status == 403 and location_restricted(upstream) else "allowed"


def egress_state(upstream: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> str:
    """``allowed``, ``blocked`` (refused for the location) or ``unreachable``: whether the service answers this host."""

    return _state(upstream, _answer(upstream, timeout))


def provider_network_state(provider: str, route: str = "", timeout: float = 2.0) -> str:
    """Whether a turn on this route can get through now: the service also has to work (no 5xx, no 429).

    A service Portal has no probe for is ``allowed``: waiting on it would never end.
    """

    upstream = route_upstream(provider, route)
    if upstream not in UPSTREAMS:
        return "allowed"
    status = _answer(upstream, timeout)
    if status is not None and (status >= 500 or status == 429):
        return "unreachable"
    return _state(upstream, status)
