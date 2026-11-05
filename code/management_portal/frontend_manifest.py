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

CORE_BUILTINS = {
    "development": {"title": "Portal 开发投影", "path": "/development", "description": "Portal 的架构、阶段进展与未来方向工作台"},
    "studio": {"title": "投影工坊", "path": "/workbenches?panel=studio", "description": "Workbenches 中由普通 Lineage 提供的 AI 页面生成模块"},
}


@cache
def builtins() -> dict[str, dict]:
    """Core page entries plus installed features, shared with the Workbench store."""
    found = dict(CORE_BUILTINS)
    for _feature, builtin in portal_features.contributions("workbench.builtin"):
        found[builtin["name"]] = {key: builtin[key] for key in ("title", "path", "description")}
    return found


@cache
def manifest() -> dict:
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
