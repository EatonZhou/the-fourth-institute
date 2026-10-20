"""What the installed features add to the shared parts of Portal pages.

Every Portal page with the site navigation carries the manifest as
``window.__PORTAL_FRONTEND__``, set before the page's scripts run:

``navigation``
    The features' site navigation entries (``web.navigation``).
``workbenches``
    The built-in workbenches by name, with ``title`` and ``path``;
    ``initialize`` marks the features' business workbenches
    (``workbench.builtin``), whose pages initialize their entry when opened.
"""
from __future__ import annotations

import json
import re
from functools import cache

from management_portal import portal_features

_NAVIGATION_SCRIPT = b"/static/portal_nav.js"


@cache
def manifest() -> dict:
    from management_portal.workbench_projections.store import CORE_BUILTINS, builtins
    from management_common.distribution_features import included_features

    return {
        "distribution_features": included_features(),
        "navigation": [dict(entry) for _feature, entries in portal_features.contributions("web.navigation")
                       for entry in entries],
        "workbenches": {name: {"title": item["title"], "path": item["path"], "initialize": name not in CORE_BUILTINS}
                        for name, item in builtins().items()},
    }


@cache
def _script() -> bytes:
    payload = json.dumps(manifest(), ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    return b"<script>window.__PORTAL_FRONTEND__=Object.freeze(" + payload.encode("ascii") + b");</script>"


def inject(data: bytes) -> bytes:
    """Add the manifest to the head of a Portal page that loads the site navigation."""
    if _NAVIGATION_SCRIPT not in data or b"window.__PORTAL_FRONTEND__=" in data:
        return data
    head = re.search(rb"<head\b[^>]*>", data, re.IGNORECASE)
    if head is None:
        return data
    return data[:head.end()] + _script() + data[head.end():]
