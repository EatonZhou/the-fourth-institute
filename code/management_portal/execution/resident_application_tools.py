"""Application tool transport for Workers: signed RPC, with no business imports."""
from __future__ import annotations

import json
import secrets

from .harness import HarnessTool, current_tool_call_id, stable_json_result


def rpc(method, params, *, caller):
    from management_common.rpc_auth import request_auth
    from management_common.service_socket import connect_service
    from management_portal.hatg.socket_rpc import web_service_socket, _decode
    path = web_service_socket()
    request_id = secrets.token_hex(12)
    payload = {"id": request_id, "method": method, "params": params,
               "auth": request_auth(method, params, socket_path=path, actor=caller)}
    connection, _ = connect_service(path, timeout=2700)
    with connection:
        connection.sendall(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
        with connection.makefile("rb") as stream:
            return _decode("web", stream.readline(), request_id)["result"]


def materialize(selections, *, caller, execution_id):
    try:
        catalog = rpc("application.tools.list", {}, caller=caller)
    except Exception:
        if any(item.get("required") for item in selections):
            raise
        return [], [{"ref": item["ref"], "observation": "unknown",
                     "reason": "application_catalog_unavailable"} for item in selections], {}
    declarations = {item["ref"]: item for item in catalog["tools"]}
    attached, observations, dependencies = [], [], {}
    for selection in selections:
        ref = selection["ref"]
        declaration = declarations.get(ref)
        if declaration is None:
            if selection.get("required"):
                raise ValueError(f"Required application tool missing: {ref}")
            observations.append({"ref": ref, "observation": "missing"})
            continue
        dependencies[ref] = declaration
        def handler(arguments, ref=ref):
            if not isinstance(arguments, dict) or set(arguments) - {"arguments", "idempotency_key", "expected_version"}:
                raise ValueError("Expected arguments and optional application call controls")
            call = dict(arguments)
            call.update(schema="tfi.application-tool-call.v1", tool=ref)
            if not call.get("idempotency_key") and current_tool_call_id():
                call["idempotency_key"] = "tool:" + current_tool_call_id()
            result = rpc("application.tools.execute", {
                "call": call,
                "execution_id": execution_id}, caller=caller)
            return json.dumps(result, ensure_ascii=False)
        schema = {"type": "object", "properties": {
            "arguments": declaration["input_schema"],
            "idempotency_key": {"type": "string", "maxLength": 240,
                                "description": "Optional business retry key; defaults to this tool call ID"},
            "expected_version": {"type": ["integer", "string", "object"],
                                 "description": "Optional expected target version"}},
            "required": ["arguments"], "additionalProperties": False}
        attached.append(HarnessTool(name=declaration["name"], description=declaration["description"],
            input_schema=schema, handler=handler, capability_ref=ref,
            read_only=not declaration["mutates"], stable_result=stable_json_result("call_id", "replayed")))
    return attached, observations, dependencies
