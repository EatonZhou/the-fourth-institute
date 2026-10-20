from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from management_portal.resources.portal import portal_route_script
from management_portal.test_framework import api


PORTAL_ROOT = Path(__file__).resolve().parents[2]
_SANDBOX_TARGETS = {
    "/test-demo/components": PORTAL_ROOT / "management_portal" / "test_demo_components.html",
}
_STYLESHEET_RE = re.compile(
    r"<link\b(?P<attrs>[^>]*?\brel=[\"']stylesheet[\"'][^>]*?)\bhref=[\"'](?P<src>/static/[^\"']+)[\"'](?P<tail>[^>]*)/?>",
    flags=re.IGNORECASE,
)
_SCRIPT_RE = re.compile(
    r"<script\b(?P<attrs>[^>]*?)\bsrc=[\"'](?P<src>/static/[^\"']+)[\"'](?P<tail>[^>]*)>\s*</script>",
    flags=re.IGNORECASE,
)


def demo_descriptor(case_id: str) -> dict[str, Any]:
    """Project a registered Live definition without owning Test Case semantics."""
    case = api.service().catalog.get(case_id)
    if not case.live:
        raise ValueError("测试用例没有已登记的 Live 呈现")
    raw = json.dumps(case.live, ensure_ascii=False, sort_keys=True)
    return {
        "schema": "tfi.portal-live-tour.v1",
        "case_id": case.id,
        "title": case.title,
        "revision": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24],
        "demo": dict(case.live),
    }


def _static_asset(raw: str) -> Path | None:
    source = str(raw or "").partition("?")[0]
    if not source.startswith("/static/") or "\\" in source:
        return None
    static_root = (PORTAL_ROOT / "management_portal" / "static").resolve()
    path = (static_root / source.removeprefix("/static/")).resolve()
    if static_root not in path.parents or not path.is_file():
        return None
    return path


def _inline_static_assets(html: str) -> str:
    def stylesheet(match: re.Match[str]) -> str:
        path = _static_asset(match.group("src"))
        if path is None:
            return ""
        content = path.read_text(encoding="utf-8")
        safe = re.sub(r"</style", r"<\\/style", content, flags=re.IGNORECASE)
        return f'<style data-portal-demo-src="{match.group("src")}">{safe}</style>'

    def script(match: re.Match[str]) -> str:
        if match.group("src").partition("?")[0] == "/static/portal_resource_routes.js":
            content = portal_route_script().decode("utf-8")
        else:
            path = _static_asset(match.group("src"))
            if path is None:
                return ""
            content = path.read_text(encoding="utf-8")
        safe = re.sub(r"</script", r"<\\/script", content, flags=re.IGNORECASE)
        return f'<script data-portal-demo-src="{match.group("src")}">{safe}</script>'

    return _SCRIPT_RE.sub(script, _STYLESHEET_RE.sub(stylesheet, html))


def demo_frame_html(case_id: str, session: str) -> bytes:
    """Build an opaque-origin frame for one registered sandbox Live."""
    case = api.service().catalog.get(case_id)
    live = case.live or {}
    if live.get("mode") != "sandbox":
        raise ValueError("测试用例没有沙盒 Live")
    target = str(live.get("target") or "").strip()
    target_path = target.partition("?")[0].rstrip("/") or "/"
    source = _SANDBOX_TARGETS.get(target_path)
    if source is None:
        raise ValueError("沙盒 Live 的目标没有登记")
    clean_session = str(session or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{16,100}", clean_session):
        raise ValueError("沙盒 Live 需要有效的 session")

    html = source.read_text(encoding="utf-8")
    # The synchronous shared state owner must run once, before the guard and consumers.
    html = re.sub(r'<script\s+src=["\']/static/portal_state\.js["\']\s*></script>', '', html)
    bootstrap = {
        "caseId": case.id,
        "session": clean_session,
        "target": target,
    }
    bootstrap_json = json.dumps(bootstrap, ensure_ascii=False).replace("<", "\\u003c")
    guard = f"""
    <base href={json.dumps(target_path)} />
    <meta http-equiv="Content-Security-Policy" content="default-src 'self' data: blob:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'none'; form-action 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'" />
    <script>window.__PORTAL_DEMO_BOOTSTRAP__ = Object.freeze({bootstrap_json});</script>
    <script src="/static/portal_state.js"></script>
    <script src="/static/portal_demo_frame.js"></script>
    """
    html = html.replace("<head>", f"<head>{guard}", 1) if "<head>" in html else guard + html
    return _inline_static_assets(html).encode("utf-8")
