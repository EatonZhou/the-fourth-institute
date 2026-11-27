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


@dataclass(frozen=True)
class LoginProfile:
    """How a Provider's own login program is run and read.

    The login code names no Provider: everything that differs between
    Providers is declared here.  A ``*_hook`` field is ``module.function``.
    """

    # The environment variable naming the account's home for the login program.
    home_env: str = ""
    # Hosts a login URL printed by the program may name.
    hosts: tuple[str, ...] = ()
    # Arguments after the program for a login and for a status query.
    login_args: tuple[str, ...] = ()
    status_args: tuple[str, ...] = ()
    # The program prints a device code to type at the login URL.
    device_code: bool = False
    # The status query prints JSON with a ``loggedIn`` boolean; otherwise exit status 0 means logged in.
    status_json: bool = False
    # Look the program up on PATH when the site names none.
    path_lookup: bool = False
    # Windows npm package that a configured directory holds.
    npm_package: str = ""
    # ``() -> Path | None``: the program in the Provider's managed runtime.
    runtime_binary_hook: str = ""
    # ``(status: bool) -> argv`` for a Provider that logs in through its own wrapper.
    command_hook: str = ""
    # ``() -> dict``: environment added for the login process.
    environment_hook: str = ""
    # ``(account_id) -> bool`` replacing the program's status query.
    logged_in_hook: str = ""
    # ``(line) -> str | None``: the error a login output line reports, ``None`` when it reports none.
    error_hook: str = ""
    # ``(account_id, store) -> None``: runs after a login succeeded for an enabled account.
    after_login_hook: str = ""

    def hook(self, name: str):
        path = getattr(self, f"{name}_hook")
        module, _, function = path.rpartition(".")
        return getattr(import_module(module), function)


def login_profile(provider: str) -> LoginProfile | None:
    from .provider_catalog import AGENT_PROVIDERS

    definition = AGENT_PROVIDERS.get(provider)
    return definition.login if definition else None


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
