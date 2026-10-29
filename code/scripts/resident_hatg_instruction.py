#!/usr/bin/env python3
"""Command-line client of the Portal HATG ISA for Residents (decision_898b8911a2ff).

Submits ``tfi.hatg-instruction.v2`` calls to the one instruction address
``/api/instructions/execute`` with the bearer credential Portal issued to the
Resident.  Each command carries one idempotency key; the client's own retries
reuse it, so a retried mutation runs once.  Streaming instructions print one
JSON item per line and resume from the last cursor after a dropped connection.

    resident_hatg_instruction.py list [--category CATEGORY]
    resident_hatg_instruction.py schema INSTRUCTION
    resident_hatg_instruction.py call INSTRUCTION [--arguments-json JSON | --arguments-file PATH]
                                      [--idempotency-key KEY] [--expected-version JSON]
                                      [--request-timeout SECONDS] [--max-attempts COUNT]
    resident_hatg_instruction.py watch INSTRUCTION [--arguments-json JSON | --arguments-file PATH]
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_common.execution_context_file import execution_context  # noqa: E402
from management_common.portal_agent_http import urlopen  # noqa: E402
from management_common import portal_paths  # noqa: E402
from management_common.instance_config import installed_root, read_installation  # noqa: E402
from management_common.portal_paths import path as portal_path  # noqa: E402
from management_common.product_environment import read_product_env  # noqa: E402


SCHEMA = "tfi.hatg-instruction.v2"
RESIDENT_ID_RE = re.compile(r"^resident_[0-9a-f]{12}$")


def _portal_url() -> str:
    # A Resident shell need not inherit the service's PORTAL_URL. Resolve the
    # same installed instance as its credential paths, not a historical port.
    explicit = read_product_env("PORTAL_URL")
    if explicit is not None:
        return explicit.rstrip("/")
    root = installed_root(portal_paths.CONFIG_PATH)
    if root is not None:
        target = read_installation(root)["target"]
        return f"http://127.0.0.1:{target['ports']['web']}"
    return "http://127.0.0.1:18086"


DEFAULT_PORTAL_URL = _portal_url()
TOKEN_ROOT = Path(os.getenv("PORTAL_RESIDENT_HATG_TOKEN_ROOT", str(portal_path("secrets_dir") / "resident_hatg")))
RETRY_SECONDS = 90.0
TRANSIENT_CODES = frozenset({"instruction.in_progress", "instruction.owner_unavailable", "instruction.projection_pending"})


class InstructionFailed(RuntimeError):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        self.code = str(error.get("code") or "")
        super().__init__(str(error.get("message") or payload.get("error") or "指令执行失败"))


def _resident_id(requested: str) -> str:
    injected = str(read_product_env("PORTAL_RESIDENT_ID") or "").strip()
    if injected and requested and injected != requested:
        raise RuntimeError("--resident-id 与 Portal 注入的 Resident 身份 不一致")
    resident_id = injected or requested
    if not RESIDENT_ID_RE.fullmatch(resident_id):
        raise RuntimeError("需要 Resident 身份：请在 Portal 运行环境中使用，或传入 --resident-id")
    return resident_id


def _token(resident_id: str) -> str:
    try:
        token = (TOKEN_ROOT / f"{resident_id}.token").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("当前 Resident 尚未获得 Portal 凭证") from exc
    if not token:
        raise RuntimeError("当前 Resident 的 Portal 凭证为空")
    return token


def _request(resident_id: str, body: dict[str, Any], *, accept: str) -> urllib.request.Request:
    headers = {
        "Authorization": f"Bearer {_token(resident_id)}",
        "Content-Type": "application/json",
        "Accept": accept,
    }
    # The execution this call runs in; resource content it changes is shown
    # as that execution's output.
    environment_execution_id = read_product_env("PORTAL_EXECUTION_ID", "")
    execution_id = str(execution_context().get("execution_id") or environment_execution_id).strip()
    if execution_id:
        headers['X-Portal-Execution-ID'] = execution_id
    return urllib.request.Request(
        DEFAULT_PORTAL_URL + "/api/instructions/execute",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers=headers,
    )


def _read_error(exc: urllib.error.HTTPError) -> dict[str, Any]:
    raw = exc.read().decode("utf-8", errors="replace")
    exc.close()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"status": "failed", "error": {"code": "", "message": raw or str(exc)}}
    if not isinstance(payload, dict):
        payload = {"status": "failed", "error": {"code": "", "message": str(payload)}}
    payload.setdefault("http_status", exc.code)
    return payload


def execute(resident_id: str, body: dict[str, Any], *, request_timeout: float = 360,
            max_attempts: int | None = None) -> dict[str, Any]:
    """Retry transient failures with one key, unless the caller limits attempts.

    request_timeout bounds socket inactivity, not total elapsed time. A caller
    needing a wall-clock deadline must also bound and reap this CLI process.
    A timeout/disconnect does not prove a server-side mutation was cancelled.
    """

    if not math.isfinite(request_timeout) or request_timeout <= 0:
        raise ValueError("request-timeout 必须是有限正数")
    if max_attempts is not None and (type(max_attempts) is not int or max_attempts < 1):
        raise ValueError("max-attempts 必须是正整数")

    deadline = time.monotonic() + RETRY_SECONDS
    delay = 1.0
    attempts = 0
    while True:
        attempts += 1
        try:
            with urlopen(_request(resident_id, body, accept="application/json"), timeout=request_timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = _read_error(exc)
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            transient = str(error.get("code") or "") in TRANSIENT_CODES or (
                not error.get("code") and exc.code in {429, 502, 503, 504}
            )
            if (not transient or (max_attempts is not None and attempts >= max_attempts)
                    or time.monotonic() + delay >= deadline):
                raise InstructionFailed(payload) from None
        except (OSError, http.client.HTTPException) as exc:
            if ((max_attempts is not None and attempts >= max_attempts)
                    or time.monotonic() + delay >= deadline):
                cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                raise InstructionFailed({"status": "failed", "error": {
                    "code": "instruction.timeout" if isinstance(cause, TimeoutError) else "instruction.owner_unavailable",
                    "message": f"Portal 请求未完成：{exc}", "attempts": attempts,
                    "commit_state": "unknown"}}) from exc
        else:
            if payload.get("status") != "committed":
                raise InstructionFailed(payload)
            return payload
        time.sleep(delay)
        delay = min(delay * 2, 8.0)


def _events(response: Any) -> Iterator[tuple[str, Any]]:
    name, data = "message", []
    for raw in response:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data:
                try:
                    yield name, json.loads("\n".join(data))
                except json.JSONDecodeError:
                    pass
            name, data = "message", []
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def watch(resident_id: str, body: dict[str, Any]) -> int:
    """Print each streamed item as one JSON line until the stream ends."""

    arguments = dict(body.get("arguments") or {})
    delay = 1.0
    while True:
        try:
            with urlopen(_request(resident_id, {**body, "arguments": arguments}, accept="text/event-stream"),
                         timeout=900) as response:
                delay = 1.0
                for name, value in _events(response):
                    if name == "batch":
                        for item in (value or {}).get("items") or []:
                            print(json.dumps(item, ensure_ascii=False), flush=True)
                            if isinstance(item, dict) and item.get("cursor") is not None:
                                arguments["after_cursor"] = item["cursor"]
                    elif name == "error":
                        raise InstructionFailed({"status": "failed", "error": value})
                    elif name == "end":
                        return 0
        except urllib.error.HTTPError as exc:
            raise InstructionFailed(_read_error(exc)) from None
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(delay)
        delay = min(delay * 2, 8.0)


def _arguments(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "arguments_file", None):
        value = json.loads(Path(args.arguments_file).read_text(encoding="utf-8"))
    elif getattr(args, "arguments_json", None):
        value = json.loads(args.arguments_json)
    else:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("arguments 必须是 JSON 对象")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call the Portal HATG ISA as the current Resident.")
    parser.add_argument("--resident-id", default="")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="ListInstructions")
    listing.add_argument("--category", default="")
    schema = commands.add_parser("schema", help="GetInstructionSchema")
    schema.add_argument("instruction")
    for name in ("call", "watch"):
        command = commands.add_parser(name)
        command.add_argument("instruction")
        group = command.add_mutually_exclusive_group()
        group.add_argument("--arguments-json")
        group.add_argument("--arguments-file")
        if name == "call":
            command.add_argument("--request-timeout", type=float, default=360,
                                 help="连接/读取的 socket 空闲超时秒数；总期限仍由调用方控制")
            command.add_argument("--max-attempts", type=int, default=None,
                                 help="最多请求次数；1 禁止自动重试；超时不代表服务端取消")
            command.add_argument("--idempotency-key", default="")
            command.add_argument("--expected-version", default="",
                                 help="版本令牌；多个对象时传 JSON 映射，例如 '{\"lineage_x\": 12}'")
    args = parser.parse_args(argv)
    try:
        resident_id = _resident_id(args.resident_id)
        if args.command == "list":
            body = {"schema": SCHEMA, "instruction": "ListInstructions",
                    "arguments": {"category": args.category} if args.category else {}}
        elif args.command == "schema":
            body = {"schema": SCHEMA, "instruction": "GetInstructionSchema",
                    "arguments": {"instruction": args.instruction}}
        else:
            body = {"schema": SCHEMA, "instruction": args.instruction, "arguments": _arguments(args)}
        if args.command == "watch":
            return watch(resident_id, body)
        # One idempotency key per command (decision_898b8911a2ff “调用入口”).
        body["idempotency_key"] = str(getattr(args, "idempotency_key", "") or "") or f"cli:{uuid.uuid4().hex}"
        version = str(getattr(args, "expected_version", "") or "").strip()
        if version:
            try:
                body["expected_version"] = json.loads(version)
            except json.JSONDecodeError:
                body["expected_version"] = version
        result = execute(resident_id, body,
                         request_timeout=getattr(args, "request_timeout", 360),
                         max_attempts=getattr(args, "max_attempts", None))
    except InstructionFailed as exc:
        print(json.dumps(exc.payload, ensure_ascii=False, indent=2))
        return 1
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error": {"code": "", "message": str(exc)}}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
