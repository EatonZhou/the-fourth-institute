from __future__ import annotations

import copy
import json
from typing import Any
from urllib.parse import urlsplit


APPROVAL_METHODS = {
    "item/commandExecution/requestApproval": "command",
    "item/fileChange/requestApproval": "file_change",
    "item/permissions/requestApproval": "permissions",
}
MCP_METHOD = "mcpServer/elicitation/request"
INTERACTIVE_TOOL_NAMES = {"request_user_input", "request_approval", "mcp_elicitation"}


def normalize_interaction(method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method not in APPROVAL_METHODS and method != MCP_METHOD:
        raise ValueError("unsupported native interaction")
    request = copy.deepcopy(params)
    if not all(isinstance(request.get(key), str) and request[key] for key in ("threadId", "turnId", "itemId")):
        raise ValueError("native interaction requires an active thread, turn and item")
    request["isBlocking"] = request.get("isBlocking", True)
    if not isinstance(request["isBlocking"], bool):
        raise ValueError("invalid blocking flag")
    request["questions"] = []
    interaction = {"method": method, "kind": APPROVAL_METHODS.get(method, "mcp"), "request": copy.deepcopy(params)}
    if method in APPROVAL_METHODS:
        if method.endswith("permissions/requestApproval") and not isinstance(params.get("permissions"), dict):
            raise ValueError("permission approval requires requested permissions")
    else:
        if not isinstance(params.get("message"), str) or not params.get("serverName"):
            raise ValueError("MCP elicitation requires server and message")
        if params.get("mode") == "url":
            parsed = urlsplit(str(params.get("url") or ""))
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("MCP elicitation requires a safe HTTPS URL")
        elif params.get("mode") in {"form", "openaiForm", "openai/form"}:
            validate_form_schema(params.get("requestedSchema"))
        else:
            raise ValueError("unsupported MCP elicitation mode")
    request["interaction"] = interaction
    return request


def validate_form_schema(schema: object) -> None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("MCP form must have an object schema")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or len(properties) > 40:
        raise ValueError("MCP form has too many fields")
    for field in properties.values():
        if not isinstance(field, dict) or field.get("type") not in {"string", "number", "integer", "boolean", "array"}:
            raise ValueError("unsupported MCP field type")
        if field.get("writeOnly") or field.get("format") in {"password", "secret"}:
            raise ValueError("secret MCP input requires a protected input channel")
        if field.get("type") == "array" and (not isinstance(field.get("items"), dict)
                or not any(field["items"].get(key) for key in ("enum", "anyOf", "oneOf"))):
            raise ValueError("MCP array requires an enum item schema")
    def check(node: object) -> None:
        if isinstance(node, dict):
            if "$ref" in node or "$dynamicRef" in node:
                raise ValueError("MCP form schema references are not supported")
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    check(schema)
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("invalid MCP form schema") from exc


def interaction_response(value: dict[str, Any], interaction: dict[str, Any] | None = None) -> dict[str, Any]:
    action = value.get("action")
    if action not in {"accept", "decline", "cancel"}:
        raise ValueError("unsupported interaction action")
    content = value.get("content")
    if action != "accept" and content is not None:
        raise ValueError("declined or cancelled interaction cannot contain input")
    if len(json.dumps(content, ensure_ascii=False)) > 64000:
        raise ValueError("MCP input is too large")
    response = {"type": "user_input_response", "request_id": value["request_id"],
                "interaction_response": True, "action": action, "content": copy.deepcopy(content)}
    if interaction is not None:
        params = interaction["request"]
        if interaction["kind"] != "mcp":
            if content is not None:
                raise ValueError("approval cannot supply additional permissions")
            decisions = params.get("availableDecisions")
            if isinstance(decisions, list) and action not in decisions:
                raise ValueError("decision is not offered by this Provider request")
        elif action == "accept" and params["mode"] != "url":
            from jsonschema import Draft202012Validator, FormatChecker

            schema = params["requestedSchema"]
            validate_form_schema(schema)
            if not isinstance(content, dict) or set(content) - set(schema.get("properties", {})):
                raise ValueError("MCP answer contains unknown fields")
            error = next(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(content), None)
            if error:
                raise ValueError(f"MCP answer does not satisfy field {'/'.join(map(str, error.path)) or '(form)'}")
        elif content is not None:
            raise ValueError("URL elicitation cannot include form content")
    return response


def wire_response(response: dict[str, Any], interaction: dict[str, Any]) -> dict[str, Any]:
    action = response["action"]
    if interaction["kind"] == "permissions":
        if action == "cancel":
            return {"error": {"code": -32800, "message": "User cancelled permission request"}}
        permissions = interaction["request"]["permissions"] if action == "accept" else {}
        return {"result": {"permissions": copy.deepcopy(permissions), "scope": "turn"}}
    if interaction["kind"] == "mcp":
        return {"result": {"action": action, "content": response.get("content")}}
    return {"result": {"decision": action}}
