"""Resident-selected MCP tools backed by authenticated ResourceService documents.

The only supported connection schema is tfi.mcp-connection.v1. Required fields:
schema, protocol_version=2025-06-18, transport. timeout_seconds defaults to 30
(0.05..120), for one entire discovery or invocation including negotiation.

transport is one of:
  {type: stdio, command: absolute executable, args: [str],
   env_allowlist: [environment variable name], env: {name: {ref: resource}}}
  {type: streamable_http, url: HTTPS or loopback HTTP endpoint,
   headers: {header-name: {ref: credential resource}}}
Only those fields are accepted. Credential content is raw UTF-8, never logged.
No shell, inherited environment outside the allowlist, HTTP redirects, proxy
inheritance, URL credentials/query, implicit prompts/resources, remote JSON
Schema references, sampling, elicitation, or old HTTP+SSE transport.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import ipaddress
import os
import re
from typing import Callable
from urllib.parse import quote, urlsplit

import jsonschema

from management_portal.resources import get_resource_service
from .harness import HarnessTool, current_tool_call_id
from .resident_mcp_transport import MCPError, PROTOCOL, connect, decode, encode

_MAX_DOCUMENT = 128 * 1024
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEADER = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_RESERVED_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "accept",
                     "content-type", "mcp-session-id", "mcp-protocol-version", "origin"}


def _text(value, max_length=4096):
    return isinstance(value, str) and bool(value) and len(value) <= max_length and not any(ord(c) < 32 for c in value)


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required) <= set(value):
        raise MCPError("invalid_connection")


def _ref(value):
    _keys(value, {"ref"}, {"ref"})
    if not _text(value["ref"], 512):
        raise MCPError("invalid_connection")


def _read(ref: str, caller: str) -> bytes:
    try:
        content, _, _ = get_resource_service().read_content(ref, actor=caller, max_bytes=_MAX_DOCUMENT)
        content = bytes(content)
        if len(content) > _MAX_DOCUMENT:
            raise MCPError("resource_too_large")
        return content
    except MCPError:
        raise
    except PermissionError:
        raise MCPError("access_denied") from None
    except (FileNotFoundError, KeyError):
        raise MCPError("resource_missing", status="missing") from None
    except Exception:
        raise MCPError("resource_unavailable", status="unknown") from None


def _document(ref, caller):
    doc = decode(_read(ref, caller))
    _keys(doc, {"schema", "protocol_version", "timeout_seconds", "transport"},
          {"schema", "protocol_version", "transport"})
    if doc["schema"] != "tfi.mcp-connection.v1":
        raise MCPError("invalid_connection")
    if doc["protocol_version"] != PROTOCOL:
        raise MCPError("unsupported_protocol")
    timeout = doc.setdefault("timeout_seconds", 30)
    if type(timeout) not in (float, int) or not 0.05 <= timeout <= 120:
        raise MCPError("invalid_connection")
    transport = doc["transport"]
    if not isinstance(transport, dict):
        raise MCPError("invalid_connection")
    if transport.get("type") == "stdio":
        _keys(transport, {"type", "command", "args", "env_allowlist", "env"}, {"type", "command"})
        command = transport["command"]
        if not _text(command) or not os.path.isabs(command):
            raise MCPError("invalid_connection")
        args = transport.get("args", [])
        allowlist = transport.get("env_allowlist", [])
        if not isinstance(args, list) or len(args) > 128 or any(not isinstance(a, str) or "\0" in a for a in args):
            raise MCPError("invalid_connection")
        if not isinstance(allowlist, list) or any(not isinstance(k, str) or not _NAME.fullmatch(k) for k in allowlist):
            raise MCPError("invalid_connection")
        refs = transport.get("env", {})
        if not isinstance(refs, dict) or any(not _NAME.fullmatch(k) for k in refs):
            raise MCPError("invalid_connection")
    elif transport.get("type") == "streamable_http":
        _keys(transport, {"type", "url", "headers"}, {"type", "url"})
        if not _text(transport["url"]):
            raise MCPError("invalid_connection")
        try:
            url = urlsplit(transport["url"])
            loopback = url.hostname == "localhost"
            if not loopback:
                try:
                    loopback = ipaddress.ip_address(url.hostname or "").is_loopback
                except ValueError:
                    pass
            if not url.hostname or url.username or url.password or url.query or url.fragment or (
                    url.scheme != "https" and not (url.scheme == "http" and loopback)):
                raise ValueError()
            _ = url.port
        except ValueError:
            raise MCPError("invalid_connection") from None
        refs = transport.get("headers", {})
        if (not isinstance(refs, dict) or any(not _HEADER.fullmatch(k) or k.lower() in _RESERVED_HEADERS for k in refs)
                or len({k.lower() for k in refs}) != len(refs)):
            raise MCPError("invalid_connection")
    else:
        raise MCPError("unsupported_transport")
    for value in refs.values():
        _ref(value)
    return doc


def _credentials(doc, caller):
    transport = doc["transport"]
    env = {name: os.environ[name] for name in transport.get("env_allowlist", []) if name in os.environ}
    headers, secrets = {}, []
    for name, reference in transport.get("env", transport.get("headers", {})).items():
        try:
            value = _read(reference["ref"], caller).decode("utf-8")
        except UnicodeError:
            raise MCPError("invalid_credential") from None
        if not value or "\0" in value or (transport["type"] == "streamable_http" and any(ord(c) < 32 or ord(c) > 126 for c in value)):
            raise MCPError("invalid_credential")
        (env if transport["type"] == "stdio" else headers)[name] = value
        secrets.append(value)
    return env, headers, secrets


def _no_secret(value, secrets):
    rendered = encode(value)
    if any(encode(secret)[1:-1] in rendered for secret in secrets):
        raise MCPError("credential_exposure")


def _schema(schema):
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise MCPError("invalid_tool_schema")
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (not isinstance(item, str) or not item.startswith("#")):
                    raise MCPError("unsupported_schema_ref")
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(schema)
    try:
        validator = (jsonschema.validators.validator_for(schema, default=None)
                     if "$schema" in schema else jsonschema.Draft202012Validator)
        if validator is None:
            raise MCPError("unsupported_schema_dialect")
        validator.check_schema(schema)
        return validator(schema)
    except jsonschema.exceptions.SchemaError:
        raise MCPError("invalid_tool_schema") from None


async def _directory(connection):
    directory, cursors, params = {}, set(), {}
    for _ in range(32):
        result = await connection.request("tools/list", params)
        items = result.get("tools")
        if not isinstance(items, list) or len(items) + len(directory) > 1024:
            raise MCPError("invalid_directory")
        for item in items:
            if not isinstance(item, dict) or not _text(item.get("name"), 128):
                raise MCPError("invalid_tool")
            name = item["name"]
            if name in directory:
                raise MCPError("duplicate_tool")
            directory[name] = item
        cursor = result.get("nextCursor")
        if cursor is None:
            return directory
        if not _text(cursor) or cursor in cursors:
            raise MCPError("invalid_cursor")
        cursors.add(cursor)
        params = {"cursor": cursor}
    raise MCPError("directory_limit")


def _run(factory):
    # Harness handlers are synchronous, including when a provider calls them on
    # its asyncio loop. Each operation owns its loop and never shares a client.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="resident-mcp") as pool:
        return pool.submit(lambda: asyncio.run(factory())).result()


def _digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def _expected_dependency(dependencies, ref, names):
    expected = dependencies.get(ref)
    if expected is None:
        raise MCPError("recovery_dependency_missing")
    if (not isinstance(expected, dict) or set(expected) != {"connection_digest", "tools"}
            or not isinstance(expected["tools"], dict)):
        raise MCPError("invalid_recovery_dependencies")
    digests = [expected["connection_digest"], *expected["tools"].values()]
    if (any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in digests)
            or any(not _text(name, 128) for name in expected["tools"])):
        raise MCPError("invalid_recovery_dependencies")
    if not set(names) <= set(expected["tools"]):
        raise MCPError("recovery_dependency_missing")
    return expected


def materialize_mcp_tools(selections, *, caller: str, execution_id: str,
                          is_cancelled: Callable[[], bool] | None = None,
                          expected_dependencies: dict[str, dict] | None = None):
    """Discover selected schemas, returning tools and non-secret observations.

    Optional discovery failures become observations. Required failures raise
    before the provider sees an invocation. The handler captures caller and
    execution; caller-looking keys in tool arguments have no authority.
    An omitted or empty tools list selects nothing, including for required refs.
    On recovery, expected_dependencies freezes connection documents and entire
    tools/list items. Any selected recovery failure is fatal, even if optional.
    Digests use canonical JSON; connection defaults are applied before hashing.
    Credential contents are never included, only their references.
    """
    if not isinstance(caller, str) or not re.fullmatch(r"resident_[a-z0-9_]+", caller) or not execution_id:
        raise MCPError("invalid_execution_identity")
    if expected_dependencies is not None and not isinstance(expected_dependencies, dict):
        raise MCPError("invalid_recovery_dependencies")
    expected_dependencies = copy.deepcopy(expected_dependencies)
    tools, observations, seen = [], [], set()
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) - {"ref", "enabled", "required", "tools"}:
            raise MCPError("invalid_selection")
        ref = selection.get("ref")
        enabled, required = selection.get("enabled", True), selection.get("required", False)
        names = selection.get("tools", [])
        if (not _text(ref, 512) or ref in seen or type(enabled) is not bool or type(required) is not bool
                or (required and not enabled) or not isinstance(names, list)
                or any(not _text(name, 128) for name in names) or len(names) != len(set(names))):
            raise MCPError("invalid_selection")
        seen.add(ref)
        base = {"ref": ref, "source": "mcp", "required": required, "delivery": "not_sent"}
        if not enabled or names == []:
            reason = "disabled" if not enabled else "unselected"
            observations.append({**base, "status": reason, "observation": "unknown", "reason": reason})
            continue
        try:
            if is_cancelled and is_cancelled():
                raise InterruptedError("MCP execution cancelled")
            expected = (_expected_dependency(expected_dependencies, ref, names)
                        if expected_dependencies is not None else None)
            doc = _document(ref, caller)
            connection_digest = _digest(doc)
            if expected is not None and connection_digest != expected["connection_digest"]:
                raise MCPError("recovery_connection_changed")
            env, headers, secrets = _credentials(doc, caller)
            async def discover():
                async with connect(doc, env=env, headers=headers, is_cancelled=is_cancelled) as connection:
                    return await _directory(connection)
            directory = _run(discover)
            selected = sorted(names)
            staged_tools, staged_observations = [], []
            for name in selected:
                if expected is not None and (name not in directory
                        or _digest(directory[name]) != expected["tools"][name]):
                    raise MCPError("recovery_tool_changed")
                if name not in directory:
                    if required:
                        raise MCPError("tool_missing", status="missing")
                    staged_observations.append({**base, "local_name": name, "status": "missing",
                        "observation": "missing", "reason": "tool_missing"})
                    continue
                item = directory[name]
                _schema(item.get("inputSchema"))
                if item.get("outputSchema") is not None:
                    _schema(item["outputSchema"])
                if not isinstance(item.get("description", ""), str):
                    raise MCPError("invalid_tool")
                _no_secret(item, secrets)
                alias = "mcp_" + hashlib.sha256(encode([ref, name]).encode()).hexdigest()[:60]
                handler = _handler(ref, doc, item, caller, execution_id, is_cancelled)
                staged_tools.append(HarnessTool(alias, item.get("description", ""),
                    copy.deepcopy(item["inputSchema"]), handler,
                    capability_ref=f"mcp:{ref}#{quote(name, safe='')}",
                    connection_ref=ref, local_name=name))
                staged_observations.append({**base, "local_name": name, "name": alias,
                    "status": "discovered", "observation": "found", "reason": "tools_list_received",
                    "schema_hash": _digest(item["inputSchema"]),
                    "connection_digest": connection_digest, "tool_digest": _digest(item)})
            tools.extend(staged_tools)
            observations.extend(staged_observations)
        except MCPError as exc:
            if expected_dependencies is not None:
                raise
            if required:
                raise MCPError("required_mcp: " + exc.reason, status=exc.status) from None
            observations.append({**base, "status": exc.status, "reason": exc.reason,
                "observation": "missing" if exc.status == "missing" else "unknown"})
    return tuple(tools), observations


def _handler(ref, document, item, caller, execution_id, is_cancelled):
    snapshot, tool_snapshot = encode(document), encode(item)
    local_name, input_schema = item["name"], copy.deepcopy(item["inputSchema"])
    validator = _schema(input_schema)

    def handler(arguments):
        if is_cancelled and is_cancelled():
            raise InterruptedError("MCP execution cancelled")
        # No tool argument can change caller, execution_id, ref or local_name.
        current = _document(ref, caller)
        if encode(current) != snapshot:
            raise MCPError("connection_changed")
        if not isinstance(arguments, dict):
            raise MCPError("invalid_arguments")
        try:
            validator.validate(arguments)
        except Exception:
            raise MCPError("invalid_arguments") from None
        env, headers, secrets = _credentials(current, caller)
        provenance = {"tfi/resident_id": caller, "tfi/execution_id": execution_id,
                      "tfi/tool_call_id": current_tool_call_id()}

        async def invoke():
            async with connect(current, env=env, headers=headers, is_cancelled=is_cancelled) as connection:
                directory = await _directory(connection)
                if local_name not in directory or encode(directory[local_name]) != tool_snapshot:
                    raise MCPError("tool_changed")
                result = await connection.request("tools/call", {"name": local_name,
                    "arguments": arguments, "_meta": provenance})
                if not isinstance(result.get("content"), list) or type(result.get("isError", False)) is not bool:
                    raise MCPError("invalid_tool_result")
                _no_secret(result, secrets)
                return encode(result)
        return _run(invoke)
    return handler
