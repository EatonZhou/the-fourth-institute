"""Telepus commands: Telegram intake, shared reply refresh and Watchboard commands.

``python -m management_portal.telepus.cli <command> [options]``. The Watchboard
script (``<Telepus Lineage>/script/src/main.py``) calls ``main`` with its own
status file; the services start through ``router_service`` and
``refresh_service``.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .router_core import RouterCore
from .telegram_adapter import TelegramAdapter
from .telegram_client import TelegramClient


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_status(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def write_status(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def router_store_schema() -> str:
    """Telepus state lives in this PostgreSQL schema (site setting database_schema, default telepus)."""
    from . import postgres

    return postgres.schema_name()


def router_core() -> RouterCore:
    return RouterCore(router_store_schema())


def telegram_adapter(client: TelegramClient | None = None) -> TelegramAdapter:
    return TelegramAdapter(router_core(), client=client, background_attachment_uploads=True)


def option_value(argv: list[str], name: str, default: str = "") -> str:
    flag = f"--{name.replace('_', '-')}"
    for idx, item in enumerate(argv):
        if item == flag and idx + 1 < len(argv):
            return argv[idx + 1]
    return default


def option_bool(argv: list[str], name: str) -> bool:
    return f"--{name.replace('_', '-')}" in argv


def option_int(argv: list[str], name: str, default: int = 0) -> int:
    raw = option_value(argv, name, "")
    return int(raw) if raw else default


def run_router_dispatch(argv: list[str]) -> dict:
    text = option_value(argv, "text")
    actor = option_value(argv, "actor")
    if not text:
        raise ValueError("--text required")
    if not actor:
        raise ValueError("--actor required")
    return router_core().dispatch_message(
        chat_id=option_value(argv, "chat_id", "dev-chat"),
        telegram_user_id=option_value(argv, "user_id", "dev-user"),
        text=text,
        portal_actor=actor,
        chat_type=option_value(argv, "chat_type", "private"),
    )


def run_telegram_handle_update_file(argv: list[str]) -> dict:
    path = option_value(argv, "path")
    if not path:
        raise ValueError("--path required")
    return telegram_adapter().handle_update_file(Path(path), dry_run=not option_bool(argv, "send"))


def run_telegram_poll_once(argv: list[str]) -> dict:
    return telegram_adapter(TelegramClient()).poll_once(
        limit=option_int(argv, "limit", 20), timeout=option_int(argv, "timeout", 0), dry_run=False)


def run_telegram_refresh_routes(argv: list[str]) -> dict:
    dry_run = not option_bool(argv, "send")
    return telegram_adapter(None if dry_run else TelegramClient()).refresh_routes(
        limit=option_int(argv, "limit", 100), dry_run=dry_run)


def register_bot_commands(client: TelegramClient) -> None:
    """Show the commands in Telegram's "/" menu; a failure leaves the menu as it was."""
    from .router_core import BOT_COMMANDS

    try:
        client.set_my_commands([{"command": name, "description": text} for name, text in BOT_COMMANDS])
    except Exception as exc:  # noqa: BLE001 - /help still works without the menu
        print(json.dumps({"schema": "telepus.telegram.commands_menu.v1", "status": "failed",
                          "error": f"{type(exc).__name__}: {str(exc)[:300]}"}, ensure_ascii=False), flush=True)


def run_telegram_daemon(argv: list[str]) -> int:
    """Receive Telegram updates until stopped; a pass that received nothing is not logged."""
    max_iterations = option_int(argv, "max_iterations", 0)
    poll_limit = option_int(argv, "poll_limit", 20)
    poll_timeout = option_int(argv, "poll_timeout", 10)
    sleep_seconds = option_int(argv, "sleep_seconds", 1)
    client = TelegramClient()
    adapter = telegram_adapter(client)
    register_bot_commands(client)
    index = 0
    while True:
        index += 1
        started_at = now_iso()
        try:
            result = adapter.poll_once(limit=poll_limit, timeout=poll_timeout, dry_run=False)
            payload = {"schema": "telepus.telegram.daemon_iteration.v2", "index": index, "started_at": started_at,
                       "finished_at": now_iso(), "status": "success", "poll_updates": result.get("updates", 0)}
        except Exception as exc:
            payload = {"schema": "telepus.telegram.daemon_iteration.v2", "index": index, "started_at": started_at,
                       "finished_at": now_iso(), "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
        if payload["status"] != "success" or payload.get("poll_updates"):
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        if max_iterations and index >= max_iterations:
            return 0
        time.sleep(max(0, sleep_seconds))


def run_telegram_refresh_daemon(argv: list[str]) -> int:
    """Push replies until stopped; only passes that sent, failed or changed their error count are logged."""
    max_iterations = option_int(argv, "max_iterations", 0)
    refresh_limit = option_int(argv, "refresh_limit", 100)
    sleep_seconds = option_int(argv, "sleep_seconds", 3)
    adapter = telegram_adapter(TelegramClient())
    from .weixin_adapter import WeixinAdapter
    from concurrent.futures import ThreadPoolExecutor
    weixin = WeixinAdapter()
    from .refresh_service import NotificationScheduler
    notifications = NotificationScheduler(weixin)
    weixin_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='telepus-weixin-refresh')
    pending = {}
    last_errors = 0
    cleanup_at = 0.0
    index = 0
    try:
        notifications.start()
        while True:
            pending = {actor: task for actor, task in pending.items() if not task.done()}
            index += 1
            started_at = now_iso()
            if time.monotonic() >= cleanup_at:
                cleanup_at = time.monotonic() + 60
                try:
                    adapter.router.store.prune_expired_callback_tokens()
                except Exception as exc:
                    weixin.notifications.diagnostic('', exc)
            try:
                result = adapter.refresh_routes(limit=refresh_limit, dry_run=False)
                for actor in weixin.store.active_actors():
                    if actor not in pending or pending[actor].done():
                        pending[actor] = weixin_pool.submit(weixin.refresh, actor)
                payload = {"schema": "telepus.telegram.portal_refresh_iteration.v2", "started_at": started_at,
                           "finished_at": now_iso(), "status": "success", "chats": result.get("chats", 0),
                           "sent": sum(1 for item in result.get("handled") or [] if item.get("status") == "refreshed"),
                           "errors": result.get("errors", 0)}
            except Exception as exc:
                payload = {"schema": "telepus.telegram.portal_refresh_iteration.v2", "started_at": started_at,
                           "finished_at": now_iso(), "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
            errors = int(payload.get("errors") or 0)
            if payload["status"] != "success" or payload.get("sent") or errors != last_errors:
                print(json.dumps(payload, ensure_ascii=False), flush=True)
            last_errors = errors
            if max_iterations and index >= max_iterations:
                return 0
            time.sleep(max(1, sleep_seconds))
    finally:
        notifications.close()
        weixin_pool.shutdown(wait=False, cancel_futures=True)
        pending.clear()


def success(command: str, payload: dict) -> int:
    print(json.dumps({"schema": "dirtybase.resident_watchboard_run.v1", "command": command, "status": "success",
                      "summary": payload}, ensure_ascii=False))
    return 0


def failure(command: str, error: str) -> int:
    print(json.dumps({"schema": "dirtybase.resident_watchboard_run.v1", "command": command, "status": "failed",
                      "errors": [error]}, ensure_ascii=False))
    return 2


COMMANDS = {
    "router-dispatch": run_router_dispatch,
    "telegram-get-me": lambda argv: TelegramClient().get_me(),
    "telegram-webhook-info": lambda argv: TelegramClient().get_webhook_info(),
    "telegram-poll-once": run_telegram_poll_once,
    "telegram-handle-update-file": run_telegram_handle_update_file,
    "telegram-refresh-routes": run_telegram_refresh_routes,
}


def main(argv: list[str], *, status_path: Path | None = None) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    rest = argv[2:]
    if command == "telegram-daemon":
        return run_telegram_daemon(rest)
    if command == "telegram-portal-refresh-daemon":
        return run_telegram_refresh_daemon(rest)
    status = read_status(status_path)
    if command == "status":
        status.setdefault("schema", "dirtybase.resident_watchboard_status.v1")
        status.setdefault("state", "idle")
        status["router_store_schema"] = router_store_schema()
        status["updated_at"] = now_iso()
        write_status(status_path, status)
        return success(command, status)
    if command in {"run-once", "router-init", "router-status"}:
        try:
            core = router_core()
            payload = core.init() if command == "router-init" else core.status()
        except Exception as exc:
            return failure(command, str(exc))
        if command != "router-status":
            status.update({"schema": "dirtybase.resident_watchboard_status.v1", "state": "idle",
                           "router": payload, "updated_at": now_iso()})
            if command == "run-once":
                status["last_run_at"] = now_iso()
            write_status(status_path, status)
        return success(command, status if command == "run-once" else payload)
    handler = COMMANDS.get(command)
    if handler is None:
        return failure(command, "unknown command")
    try:
        return success(command, handler(rest))
    except Exception as exc:
        return failure(command, str(exc))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
