"""Resolve registered account operations through their Provider declarations."""

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from jsonschema import Draft202012Validator

from management_portal.hatg.contracts import InstructionError, invalid, not_found


@dataclass(frozen=True)
class AccountOperation:
    adapter: str
    kinds: tuple[str, ...] = ("subscription_login",)
    schema: dict[str, Any] | None = None
    references: bool = False
    authorization_kind: str = ""
    authorization_hint: str = ""

    def __call__(self, *args, **kwargs):
        module, _, name = self.adapter.rpartition(".")
        return getattr(import_module(module), name)(*args, **kwargs)

    def describe(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kinds": list(self.kinds)}
        if self.schema is not None:
            result["schema"] = self.schema
        if self.references:
            result["references"] = True
        if self.authorization_kind:
            result["authorization_kind"] = self.authorization_kind
        return result


def account_operation(account_id: str, capability: str):
    from .account_registrations import registration
    from .provider_catalog import AGENT_PROVIDERS

    row = registration(account_id)
    if row is None:
        raise not_found("执行账号不存在")
    provider = AGENT_PROVIDERS.get(row["provider"])
    operation = provider.account_operations.get(capability) if provider else None
    if operation is None or row["kind"] not in operation.kinds:
        raise unsupported(capability)
    return row, operation


def unsupported(capability: str) -> InstructionError:
    return InstructionError("instruction.invalid_arguments", "该账号不支持请求的 Provider 能力",
                            details={"reason": "provider.capability_unsupported", "capability": capability})


EMPTY_SCHEMA = {"type": "object", "additionalProperties": False}


def validate_fields(value, schema, *, argument: str):
    errors = Draft202012Validator(schema if schema is not None else EMPTY_SCHEMA).iter_errors(value)
    error = next(errors, None)
    if error is not None:
        # Validation reprs can contain a submitted value. Only name the field.
        path = ".".join(str(item) for item in error.absolute_path)
        raise invalid(f"{argument}{'.' + path if path else ''} 不符合 Provider 声明")
    return value
