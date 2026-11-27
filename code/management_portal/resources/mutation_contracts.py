"""Provider-owned JSON schemas, shared by discovery and actual mutations."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
import re
from typing import Any, Literal
from urllib.parse import unquote

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import SchemaError

from .errors import ResourceOperationError


MutationValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine_many({
        "integer": lambda _checker, value: type(value) is int,
        "number": lambda _checker, value: type(value) in (int, float)
        and (type(value) is int or math.isfinite(value)),
    }),
)
_MODE = "x-resource-mode"
_MAPS = ("properties", "patternProperties", "$defs", "dependentSchemas")
_SINGLE = ("additionalProperties", "unevaluatedProperties", "propertyNames", "items",
           "contains", "unevaluatedItems", "not", "if", "then", "else")
_ARRAYS = ("allOf", "anyOf", "oneOf", "prefixItems")
_EXPECTED = ("type", "enum", "const", "minimum", "maximum", "exclusiveMinimum",
             "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern",
             "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties")


def _json_value(value):
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(type(k) is str and _json_value(v) for k, v in value.items())
    return type(value) is list and all(_json_value(v) for v in value)


def _json_copy(value):
    if isinstance(value, Mapping):
        return {key: _json_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_copy(item) for item in value]
    return value


def _nodes(schema, path=()):
    if not isinstance(schema, dict):
        return
    yield path, schema
    for key in _MAPS:
        for name, child in schema.get(key, {}).items():
            yield from _nodes(child, (*path, key, name))
    for key in _SINGLE:
        if key in schema:
            yield from _nodes(schema[key], (*path, key))
    for key in _ARRAYS:
        for index, child in enumerate(schema.get(key, [])):
            yield from _nodes(child, (*path, key, index))


def _validation_schema(schema):
    # jsonschema's direct False descent loses the instance path. The
    # equivalent assertion keeps that path; discovery retains the original.
    if schema is False:
        return {"not": {}}
    if not isinstance(schema, dict):
        return schema
    result = dict(schema)
    for key in _MAPS:
        if key in result:
            result[key] = {name: _validation_schema(child) for name, child in result[key].items()}
    for key in _SINGLE:
        if key in result:
            # These keywords already produce a normal keyword error for
            # False, including the information used to name extra fields.
            if not (key in ('additionalProperties', 'unevaluatedProperties') and result[key] is False):
                result[key] = _validation_schema(result[key])
    for key in _ARRAYS:
        if key in result:
            result[key] = [_validation_schema(child) for child in result[key]]
    return result


def _reference(root, ref):
    if not isinstance(ref, str) or not (ref == "#" or ref.startswith("#/")):
        raise ValueError("mutation contracts require local JSON Pointer $ref")
    value = root
    try:
        for token in unquote(ref[2:]).split("/") if ref != "#" else ():
            token = token.replace("~1", "/").replace("~0", "~")
            value = value[int(token)] if isinstance(value, list) else value[token]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("mutation contract has an unresolved local $ref") from exc
    if not isinstance(value, (dict, bool)):
        raise ValueError("mutation contract $ref must refer to a schema")
    return value


def _chain(root, schema):
    seen = set()
    while isinstance(schema, dict):
        if id(schema) in seen:
            raise ValueError("mutation contract contains a cyclic direct $ref")
        seen.add(id(schema))
        yield schema
        if "$ref" not in schema:
            break
        schema = _reference(root, schema["$ref"])


def _object_schema(root, schema, seen=()):
    if not isinstance(schema, dict) or id(schema) in seen:
        return False
    if schema.get("type") == "object":
        return True
    seen = (*seen, id(schema))
    if "$ref" in schema and _object_schema(root, _reference(root, schema["$ref"]), seen):
        return True
    if any(_object_schema(root, s, seen) for s in schema.get("allOf", [])):
        return True
    return any(schema.get(key) and all(_object_schema(root, s, seen) for s in schema[key])
               for key in ("oneOf", "anyOf"))


def prepare_mutation_contract(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a declaration before installing any of the provider's types."""
    if not isinstance(schema, Mapping) or not _json_value(schema):
        raise TypeError("mutation contract must be a JSON Schema object")
    schema = _json_copy(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("invalid mutation JSON Schema: " + _pointer(exc.path)) from exc
    nodes = list(_nodes(schema))
    for _path, node in nodes:
        if "$schema" in node and node["$schema"] != "https://json-schema.org/draft/2020-12/schema":
            raise ValueError("mutation contracts require Draft 2020-12")
        if any(key in node for key in ("$id", "$dynamicRef", "$dynamicAnchor", "$anchor", "format")):
            raise ValueError("mutation contracts use local pointers and explicit constraints, not URI/format assertions")
        if "$ref" in node:
            _reference(schema, node["$ref"])
            list(_chain(schema, node))
    if not _object_schema(schema, schema):
        raise ValueError("mutation contract must describe an object payload")
    if not any(node.get("properties") or node.get("required") or
               node.get("additionalProperties") is False or "const" in node or "enum" in node
               for _path, node in nodes):
        raise ValueError("mutation contract must declare its payload fields")
    # An effect-mode annotation is legal only at the root or a top-level
    # exclusive branch, including local references used by those branches.
    allowed = {id(node) for node in _chain(schema, schema)}
    for root_node in _chain(schema, schema):
        for branch in root_node.get("oneOf", []):
            allowed.update(id(node) for node in _chain(schema, branch))
    for _path, node in nodes:
        if _MODE in node and (node[_MODE] != "legacy-replay-only" or id(node) not in allowed):
            raise ValueError("legacy-replay-only is allowed only on the root or a top-level oneOf branch")
    return schema


def has_replay_contract(contract):
    return any(_MODE in node for _path, node in _nodes(contract))


class ResourceMutationValidationError(ResourceOperationError):
    commit_state = "not_committed"

    def __init__(self, resource_type, operation, errors):
        super().__init__("invalid_argument", errors[0]["message"], 400, details={
            "resource_type": resource_type, "operation": operation, "errors": errors,
            "domain_committed": False,
        })


def _pointer(path):
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in path)


def _type(value):
    if value is None:
        return "null"
    return {bool: "boolean", int: "integer", float: "number", str: "string",
            dict: "object", list: "array"}.get(type(value), type(value).__name__)


def _errors(error, prefix):
    if error.context:
        for child in error.context:
            yield from _errors(child, prefix)
        return
    path = list(error.absolute_path)
    paths = [path]
    if error.validator == "required" and isinstance(error.instance, dict):
        paths = [path + [key] for key in error.validator_value if key not in error.instance]
    elif error.validator == "additionalProperties" and isinstance(error.instance, dict):
        properties = error.schema.get("properties", {})
        patterns = error.schema.get("patternProperties", {})
        paths = [path + [key] for key in error.instance
                 if key not in properties and not any(re.search(p, key) for p in patterns)] or paths
    schema = error.schema if isinstance(error.schema, dict) else {}
    keyword = error.validator or 'false_schema'
    expected = {k: deepcopy(v) for k, v in schema.items() if k in _EXPECTED}
    if keyword not in expected:
        expected[keyword] = deepcopy(error.validator_value)
    for field_path in paths:
        field_expected = expected
        actual_type = _type(error.instance)
        if error.validator == 'required':
            field_expected = {k: deepcopy(v) for k, v in schema.get('properties', {}).get(field_path[-1], {}).items()
                              if k in _EXPECTED}
            field_expected['required'] = True
            actual_type = 'missing'
        elif error.validator == 'additionalProperties' and len(field_path) > len(path):
            field_expected = {'additionalProperties': False}
            actual_type = _type(error.instance[field_path[-1]])
        yield {"path": _pointer(field_path), "schema_path": _pointer(error.absolute_schema_path),
               "keyword": keyword, "expected": field_expected,
               "actual_type": actual_type,
               "message": f"{prefix}{_pointer(field_path)}: expected {field_expected}"}


def validate_mutation_payload(contract, payload, *, resource_type, operation) -> Literal['write', 'legacy-replay-only']:
    if operation not in {"register", "update"}:
        raise ValueError("unsupported mutation contract operation")
    if contract is None:
        raise ValueError("mutation provider has no contract")
    if operation == "update" and has_replay_contract(contract):
        raise ValueError("update contract cannot declare legacy replay")
    prefix = "fields" if operation == "register" else "changes"
    if not isinstance(payload, Mapping) or not _json_value(payload):
        raise ResourceMutationValidationError(resource_type, operation, [{
            "path": "", "schema_path": "", "keyword": "type", "expected": {"type": "object"},
            "actual_type": _type(payload), "message": f"{prefix}: expected a finite JSON object",
        }])
    validator = MutationValidator(_validation_schema(contract))
    errors = [item for error in validator.iter_errors(dict(payload))
              for item in _errors(error, prefix)]
    if errors:
        errors.sort(key=lambda item: (item['path'], item['schema_path'], item['keyword']))
        raise ResourceMutationValidationError(resource_type, operation, errors)
    for node in _chain(contract, contract):
        if node.get(_MODE) == 'legacy-replay-only':
            return 'legacy-replay-only'
        for branch in node.get('oneOf', []):
            if validator.evolve(schema=_validation_schema(branch)).is_valid(dict(payload)):
                if any(part.get(_MODE) == 'legacy-replay-only' for part in _chain(contract, branch)):
                    return 'legacy-replay-only'
    return 'write'
