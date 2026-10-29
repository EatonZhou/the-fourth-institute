"""Public domain result shapes used by Domain RPC.

Only stable exchanged fields are constrained; domain-owned presentation fields
remain extensible. Identity syntax is owned by identity_contract, not this layer.
"""
from __future__ import annotations

from management_common.operation_errors import OperationError

from copy import deepcopy
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from .lineage_definition_contract import PROGRAM_SCHEMA
from .lineage_program import PROGRAM_WIRE_KINDS

STRING = {"type": "string", "minLength": 1}
COUNT = {"type": "integer", "minimum": 0}


def _object(properties: dict, *required: str) -> dict:
    return {"type": "object", "properties": properties, "required": list(required)}


NODE = _object({"id": STRING, "title": {"type": "string"},
                "backend": {"type": "string"}}, "id")
# A graph reference must not acquire a second Resident identity.
NODE["not"] = {"anyOf": [{"required": ["resident_id"]}, {"required": ["node_id"]}]}
EDGE = _object({"id": STRING, "source": STRING, "target": STRING,
                "type": STRING}, "id", "source", "target")
COMPONENT = _object({"id": STRING, "kind": STRING, "config": {"type": "object"},
                     "title": {"type": "string"}}, "id", "kind", "config")
WIRE = _object({"id": STRING, "kind": {"enum": sorted(PROGRAM_WIRE_KINDS)}, "source": STRING, "target": STRING,
                "source_port": STRING, "target_port": STRING},
               "id", "kind", "source", "target", "source_port")
WIRE["allOf"] = [{
    "if": {"properties": {"kind": {"const": "route"}}},
    "then": {"required": ["target_port"]},
    "else": {"not": {"required": ["target_port"]}},
}]
PROGRAM = _object({
    "schema": {"const": PROGRAM_SCHEMA},
    "components": {"type": "array", "items": COMPONENT},
    "wires": {"type": "array", "items": WIRE},
}, "schema", "components", "wires")
FUNCTION_BLOCK = _object({
    "id": STRING, "title": {"type": "string"}, "template_id": {"type": ["string", "null"]},
    "elements": {"type": "array", "items": _object({"id": STRING,
        "kind": {"enum": ["resident", "component", "wire", "function_block"]},
        "key": {"type": ["string", "null"]}}, "id", "kind")},
}, "id", "elements")
CONTEXT_PRESSURE = {"oneOf": [
    {"type": "object", "maxProperties": 0},  # explicitly unavailable
    _object({
        "context_tokens": COUNT, "model_context_window": COUNT,
        "used_percent": {"type": ["number", "null"], "minimum": 0},
        "heat_level": {"enum": ["normal", "warm", "hot"]},
        "updated_at": {"type": "string"}, "source": {"type": "string"},
    }, "context_tokens", "model_context_window", "used_percent", "heat_level", "updated_at", "source"),
]}
LINEAGE_GRAPH = _object({
    "lineage_id": STRING,
    "revision": COUNT,
    "nodes": {"type": "array", "items": NODE},
    "edges": {"type": "array", "items": EDGE},
    "program": PROGRAM,
    "function_blocks": {"type": "array", "items": FUNCTION_BLOCK},
}, "lineage_id", "revision", "nodes", "edges", "program")

RESULT_SCHEMAS = {
    "resident": _object({
        "resident_id": STRING, "lineage_id": STRING, "title": {"type": "string"},
        "project_id": {"type": "string"}, "provider": {"type": "string"}, "account": {"type": "string"},
        "workspaces": {"type": "array", "items": _object({"workspace_id": STRING, "path": STRING, "is_owned": {"type": "boolean"}, "purpose": {"type": "string"}}, "workspace_id", "path", "is_owned", "purpose")},
    }, "resident_id", "lineage_id", "title", "project_id", "provider", "account", "workspaces"),
    "message_counts": _object({
        "lineage_id": STRING,
        "sessions": {"type": "array", "items": _object({
            "resident_id": STRING, "user_message_count": COUNT,
            "available": {"type": "boolean"}, "context_pressure": CONTEXT_PRESSURE,
        }, "resident_id", "user_message_count", "available")},
    }, "lineage_id", "sessions"),
    "lineage_graph": LINEAGE_GRAPH,

}


class ResultContractError(OperationError):
    code = "contract.invalid_result"
    http_status = 500
    retryable = False

    def __init__(self, contract: str, path: str, reason: str) -> None:
        super().__init__(f"{contract} result violates contract at {path}: {reason}",
                         details={"contract": contract, "path": path, "reason": reason, "retryable": False})


def result_schema(name: str) -> dict:
    return deepcopy(RESULT_SCHEMAS[name])


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    schema = result_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_result(name: str, value: Any) -> None:
    error = next(_validator(name).iter_errors(value), None)
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        # Never include the returned payload (which may contain private text).
        raise ResultContractError(name, path, f"invalid {error.validator}")
    if name == "message_counts":
        ids = [row["resident_id"] for row in value["sessions"]]
        if len(set(ids)) != len(ids):
            raise ResultContractError(name, "sessions", "duplicate resident_id")
    if name == "lineage_graph":
        graph = value
        ids = [node["id"] for node in graph["nodes"]]
        ids.extend(component["id"] for component in graph["program"]["components"])
        if len(set(ids)) != len(ids):
            raise ResultContractError(name, "nodes/program.components", "duplicate object identity")
