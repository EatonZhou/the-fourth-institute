"""Public, account-scoped observations from Claude's SDK rate-limit events.

The native CLI owns authentication and /usage queries; snapshots remain disposable.
An absent percentage is unknown, even when the provider reports ``allowed``.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from management_portal.claude_provider_catalog import login_environment
from management_portal.claude_runtime import native_cli_path


WINDOWS = {
    "five_hour": "5 小时",
    "seven_day": "每周",
    "seven_day_opus": "Opus 每周",
    "seven_day_sonnet": "Sonnet 每周",
    "seven_day_overage_included": "额外每周额度",
    "overage": "额外用量",
}
MAX_AGE_SECONDS = 300
# The CLI's /usage alone takes 7-12 s and 12-20 s end to end on a loaded host;
# the read runs in the background, so the bound only catches a hung process.
NATIVE_USAGE_TIMEOUT_SECONDS = 60


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (OverflowError, ValueError):
        return None


class ClaudeAccountUsage:
    def __init__(self) -> None:
        self._accounts: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._refreshes: dict[tuple[str, str], asyncio.Task] = {}
        self._attempts: dict[tuple[str, str], float] = {}
        self._errors: dict[tuple[str, str], str] = {}

    def request_refresh(self, profile: dict[str, Any], *, force: bool = False) -> None:
        key = (profile["id"], str(profile["config_dir"]))
        task = self._refreshes.get(key)
        interval = 60 if force or key in self._errors else 180
        if (task and not task.done()) or time.monotonic() - self._attempts.get(key, -float("inf")) < interval:
            return
        self._attempts[key] = time.monotonic()
        self._refreshes[key] = asyncio.create_task(self._refresh(profile, key))

    async def _refresh(self, profile: dict[str, Any], key: tuple[str, str]) -> None:
        try:
            windows = await read_native_usage(profile)
            # A complete CLI result replaces removed model-specific windows too.
            self._accounts[key] = {}
            self.observe(*key, {"unifiedWindows": windows}, source="claude_cli_usage")
            self._errors.pop(key, None)
        except TimeoutError:
            self._errors[key] = "Claude 用量查询超时，请稍后刷新"
        except Exception as exc:
            self._errors[key] = str(exc) if isinstance(exc, ValueError) else "Claude 用量查询暂不可用"

    def observe(self, route: str, config_dir: str, info: dict[str, Any], *, now: float | None = None, source: str = "claude_sdk_events") -> None:
        if not isinstance(info, dict):
            return
        sampled_at = time.time() if now is None else now
        candidates = dict(info.get("unifiedWindows") or {}) if isinstance(info.get("unifiedWindows"), dict) else {}
        kind = info.get("rateLimitType")
        if isinstance(kind, str) and kind in WINDOWS:
            # The named event includes status even when no percentage is exposed.
            previous = candidates.get(kind)
            candidates[kind] = {**(previous if isinstance(previous, dict) else {}), **{
                key: info[key] for key in ("status", "utilization", "resetsAt") if key in info
            }}
        account = self._accounts.setdefault((route, config_dir), {})
        for window_id, raw in candidates.items():
            if window_id not in WINDOWS or not isinstance(raw, dict):
                continue
            previous = account.get(window_id, {})
            event_reset = _number(raw.get("resetsAt"))
            prior_reset = _number(previous.get("resets_at"))
            # /usage formats resets to minutes; SDK events contain epoch seconds.
            same_window = event_reset is None or (prior_reset is not None and abs(event_reset - prior_reset) <= 60)
            if source == "claude_sdk_events" and "utilization" not in raw and previous.get("source") == "claude_cli_usage" and same_window:
                # A partial turn event cannot erase or freshen an actual /usage sample.
                if raw.get("status") == "rejected":
                    previous["fresh_until"] = min(previous["fresh_until"], sampled_at)
                continue
            utilization = _number(raw.get("utilization"))
            reset = _number(raw.get("resetsAt"))
            reset = reset if reset is not None and 0 < reset < 253402300800 else None
            used = round(utilization * 100, 2) if utilization is not None and 0 <= utilization <= 1 else None
            account[window_id] = {
                "id": window_id, "label": WINDOWS[window_id], "source": source,
                "status": raw.get("status") if isinstance(raw.get("status"), str) and raw["status"] in {"allowed", "allowed_warning", "rejected"} else "unknown",
                "used_percent": used,
                "remaining_percent": round(100 - used, 2) if used is not None else None,
                "resets_at": int(reset) if reset is not None and reset > 0 else None,
                "observed_at": datetime.fromtimestamp(sampled_at, timezone.utc).isoformat(),
                "observed_at_unix": sampled_at,
                "fresh_until": min(sampled_at + MAX_AGE_SECONDS, reset) if reset is not None and reset > 0 else sampled_at + MAX_AGE_SECONDS,
            }

    def snapshot(self, route: str, config_dir: str) -> dict[str, Any]:
        key = (route, config_dir)
        account = self._accounts.get(key, {})
        # A reading stays current until a /usage read fails; ``fresh_until``
        # only bounds how long the scheduler's quota protection trusts it.
        failed = key in self._errors
        windows = [{**account[window_id], "stale": failed} for window_id in WINDOWS if window_id in account]
        usable = any(not w["stale"] and w["used_percent"] is not None for w in windows)
        task = self._refreshes.get(key)
        return {"quota_refreshing": bool(task and not task.done()), "quota_error": self._errors.get(key, ""),
                "quota_status": "available" if usable else "stale" if windows and all(w["stale"] for w in windows) else "unavailable",
                "rate_limits": windows, "quota_source": "claude_cli_usage" if any(w.get("source") == "claude_cli_usage" for w in windows) else "claude_sdk_events"}


_USAGE_LABELS = {
    "Current session": "five_hour",
    "Current week (all models)": "seven_day",
    "Current week (Sonnet only)": "seven_day_sonnet",
    "Current week (Opus only)": "seven_day_opus",
}


def parse_native_usage(text: str, *, now: float | None = None) -> dict[str, Any]:
    """Parse only the pinned CLI's subscription rows, never local token totals."""
    current = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
    windows = {}
    for line in text.splitlines():
        match = re.fullmatch(r"(Current session|Current week \(all models\)|Current week \(Sonnet only\)|Current week \(Opus only\)): ([0-9]+(?:\.[0-9]+)?)% used(?: · resets (.*))?", line.strip())
        if not match:
            continue
        used = float(match[2])
        if not 0 <= used <= 100:
            continue
        reset = None
        reset_text = match[3] or ""
        # TZ=UTC makes the CLI output deterministic; never guess a different zone.
        if reset_text.endswith(" (UTC)"):
            for fmt in ("%b %d, %I:%M%p", "%b %d, %I%p"):
                try:
                    date = datetime.strptime(reset_text[:-6], fmt).replace(tzinfo=timezone.utc)
                    candidates = [date.replace(year=current.year + delta).timestamp() for delta in (-1, 0, 1)]
                    reset = next((t for t in candidates if current.timestamp() - 120 <= t <= current.timestamp() + 8 * 86400), None)
                    break
                except ValueError:
                    continue
        windows[_USAGE_LABELS[match[1]]] = {"utilization": used / 100, "resetsAt": reset,
                                          "status": "rejected" if used == 100 else "allowed"}
    if not {"five_hour", "seven_day"}.issubset(windows):
        raise ValueError("Claude 未返回完整的订阅用量，请稍后刷新")
    return windows


async def read_native_usage(profile: dict[str, Any]) -> dict[str, Any]:
    command = native_cli_path()
    process = await asyncio.create_subprocess_exec(
        command, "--print", "/usage", "--output-format", "json", "--tools", "",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "",
        "--settings", '{"disableAllHooks":true}', "--no-session-persistence", "--max-turns", "1",
        cwd=str(profile["config_dir"]),
        env={**os.environ, **login_environment(profile), "TZ": "UTC", "LC_ALL": "C", "DISABLE_AUTOUPDATER": "1",
             "PORTAL_DATABASE_URL": ""},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=NATIVE_USAGE_TIMEOUT_SECONDS)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.communicate()
        raise
    try:
        result = json.loads(stdout)
    except (ValueError, TypeError):
        raise ValueError("Claude 用量查询返回格式异常") from None
    if process.returncode or not isinstance(result, dict) or result.get("is_error") or not isinstance(result.get("result"), str):
        raise ValueError("Claude 用量查询失败，请稍后刷新")
    # /usage is a local CLI command; model output is never accepted as quota data.
    if result.get("total_cost_usd", 0) != 0:
        raise ValueError("Claude 用量查询未返回本地命令结果")
    return parse_native_usage(result["result"])
