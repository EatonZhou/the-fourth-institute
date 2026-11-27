from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace as replace_provider
from copy import deepcopy
from typing import Any, Iterator


RegisterHandler = Callable[..., dict[str, Any]]
UnregisterHandler = Callable[..., dict[str, Any]]
UpdateHandler = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ResourceMutationProvider:
    register: RegisterHandler | None = None
    unregister: UnregisterHandler | None = None
    update: UpdateHandler | None = None
    content_updatable: bool = False
    service_managed_registration: bool = False
    # The domain serializes deletion and may synchronously publish its tombstone
    # back through another ResourceService request. Do not lock across that call.
    owner_managed_unregistration: bool = False
    # Explicit submission ports, installed by the owning optional provider.
    # They are not reachable through generic UpdateResource or action dispatch.
    update_workbench_page: Callable[..., dict[str, Any]] | None = None
    update_workbench_reverse_plan: Callable[..., dict[str, Any]] | None = None
    register_contract: Mapping[str, Any] | None = None
    update_contract: Mapping[str, Any] | None = None


def _copy_contracts(provider):
    return replace_provider(provider, register_contract=deepcopy(provider.register_contract),
                            update_contract=deepcopy(provider.update_contract))


class MutationProviderRegistry:
    """Thread-safe resource-type to mutation-provider registry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._providers: dict[str, ResourceMutationProvider] = {}

    def register(
        self,
        resource_types: str | Iterable[str],
        provider: ResourceMutationProvider,
        *,
        replace: bool = False,
    ) -> None:
        values = (
            [resource_types]
            if isinstance(resource_types, str)
            else list(resource_types)
        )
        clean_types = tuple(
            dict.fromkeys(str(value or "").strip() for value in values)
        )
        if not clean_types or any(not value for value in clean_types):
            raise ValueError("at least one resource type is required")
        handlers = (provider.register, provider.unregister, provider.update,
                    provider.update_workbench_page, provider.update_workbench_reverse_plan)
        if not any(handler is not None for handler in handlers):
            raise TypeError("resource mutation provider requires a handler")
        if any(
            handler is not None and not callable(handler)
            for handler in handlers
        ):
            raise TypeError("resource mutation provider handlers must be callable")
        from .mutation_contracts import prepare_mutation_contract, has_replay_contract

        contracts = {}
        for operation in ("register", "update"):
            contract = getattr(provider, operation + "_contract")
            if getattr(provider, operation) is None:
                if contract is not None:
                    raise ValueError(f"{', '.join(clean_types)}: {operation} contract requires a handler")
            else:
                if contract is None:
                    raise ValueError(f"{', '.join(clean_types)}: {operation} handler requires a mutation contract")
                contract = prepare_mutation_contract(contract)
                if has_replay_contract(contract) and (operation != "register" or not provider.service_managed_registration):
                    raise ValueError("legacy replay requires service-managed registration")
            contracts[operation + "_contract"] = contract
        provider = replace_provider(provider, **contracts)
        with self._lock:
            conflicts = [
                value for value in clean_types if value in self._providers
            ]
            if conflicts and not replace:
                raise ValueError(
                    "resource mutation provider already registered: "
                    + ", ".join(conflicts)
                )
            for resource_type in clean_types:
                self._providers[resource_type] = provider

    def provider_for(self, resource_type: str) -> ResourceMutationProvider:
        with self._lock:
            provider = self._providers.get(str(resource_type or "").strip())
        if provider is None:
            raise ValueError(
                f"no mutation provider registered for {resource_type}"
            )
        return _copy_contracts(provider)

    def registered_types(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._providers)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Roll back every registration made by a failed loader."""
        with self._lock:
            previous = dict(self._providers)
            try:
                yield
            except Exception:
                self._providers = previous
                raise


def mutation_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("resource registration payload must be an object")
    return dict(value)
