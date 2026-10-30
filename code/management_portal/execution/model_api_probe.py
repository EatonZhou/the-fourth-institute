"""Ask a Model API endpoint which wire protocols it serves to agent harnesses.

Each protocol gets one short, non-streaming request with the API's key and
headers, at the address its harness would use. Nothing is stored, and the key
never reaches the result or a log.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable

from management_portal.ai_provider_registry import AIProviderRegistry

PROMPT = "Reply with OK."
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT_SECONDS = 30
MESSAGE_LIMIT = 300


def _chat(decoded: dict[str, Any]) -> bool:
    return isinstance(decoded.get("choices"), list)


def _responses(decoded: dict[str, Any]) -> bool:
    return decoded.get("object") == "response" or isinstance(decoded.get("output"), list)


def _messages(decoded: dict[str, Any]) -> bool:
    return decoded.get("type") == "message" or (decoded.get("role") == "assistant"
                                                and isinstance(decoded.get("content"), list))


def _post(url: str, headers: dict[str, str], payload: dict[str, Any]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read(1 << 20)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(1 << 14)


def _message(body: bytes, secret: str) -> str:
    text = body.decode("utf-8", errors="replace")
    try:
        decoded = json.loads(text)
        error = decoded.get("error") if isinstance(decoded, dict) else None
        text = str((error.get("message") if isinstance(error, dict) else error) or decoded.get("message") or text)
    except (ValueError, AttributeError):
        pass
    if secret:
        text = text.replace(secret, "***")
    return re.sub(r"\s+", " ", text).strip()[:MESSAGE_LIMIT]


def _outcome(status: int, body: bytes, shape: Callable[[dict[str, Any]], bool], secret: str) -> dict[str, Any]:
    if 200 <= status < 300:
        try:
            decoded = json.loads(body)
        except ValueError:
            decoded = None
        if isinstance(decoded, dict) and shape(decoded):
            return {"status": "supported", "http_status": status}
        # Some gateways answer unknown paths with their web page.
        return {"status": "unsupported", "http_status": status, "message": "返回内容不是该协议的格式"}
    state = ("unsupported" if status in (404, 405, 501) else "auth_failed" if status in (401, 403)
             else "rejected" if status in (400, 409, 422) else "error")
    return {"status": state, "http_status": status, "message": _message(body, secret)}


def _attempt(url: str, headers: dict[str, str], payload: dict[str, Any],
             shape: Callable[[dict[str, Any]], bool], secret: str) -> dict[str, Any]:
    try:
        status, body = _post(url, headers, payload)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"status": "unreachable", "message": _message(str(reason).encode(), secret)}
    return _outcome(status, body, shape, secret)


def probe(entry: dict[str, Any], secret: str, *, anthropic_base_url: str = "") -> dict[str, dict[str, Any]]:
    """The outcome of one request per harness protocol, keyed like ``harness_protocols``."""
    model = str(entry.get("default_model") or "")
    base = AIProviderRegistry.base_url(entry)
    headers = AIProviderRegistry.headers(str(entry.get("provider") or "").lower(), secret, entry)
    if not secret:
        headers.pop("Authorization")
    results = {
        "openai_chat": _attempt(f"{base}/chat/completions", headers, {
            "model": model, "messages": [{"role": "user", "content": PROMPT}], "max_tokens": 16, "stream": False},
            _chat, secret),
        "openai_responses": _attempt(f"{base}/responses", headers, {
            "model": model, "input": PROMPT, "max_output_tokens": 16, "store": False}, _responses, secret),
    }
    # Use the harness address, including the legacy DeepSeek /anthropic route.
    # Undeclared protocols can still be probed at their conventional address.
    anthropic_base = str(anthropic_base_url).strip().rstrip("/")
    if not anthropic_base:
        if "anthropic_messages" in AIProviderRegistry.harness_protocols(entry):
            anthropic_base, _ = AIProviderRegistry.harness_endpoint(entry, "anthropic_messages")
        else:
            anthropic_base = base.removesuffix("/v1")
    anthropic = anthropic_base + "/v1/messages"
    payload = {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": PROMPT}]}
    attempts = {}
    for auth in ("bearer", "x-api-key"):
        auth_headers = {**headers, "anthropic-version": ANTHROPIC_VERSION}
        if auth == "x-api-key":
            auth_headers.pop("Authorization", None)
            auth_headers["x-api-key"] = secret
        attempts[auth] = _attempt(anthropic, auth_headers, payload, _messages, secret)
        if attempts[auth]["status"] != "auth_failed" or not secret:
            break
    auth, outcome = next(((name, result) for name, result in attempts.items() if result["status"] != "auth_failed"),
                         ("bearer", attempts["bearer"]))
    results["anthropic_messages"] = {**outcome, "auth": auth} if outcome["status"] == "supported" else outcome
    return results
