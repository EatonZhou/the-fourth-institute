"""Backend-neutral account identity, observations and scheduling contract.

Adapters own native route aliases, transports and quota parsing. Account IDs are
opaque outside those adapters. Existing capacity keys remain adapter metadata,
so renaming a public account never resets saved limits or dispatch timestamps.
"""
from __future__ import annotations

from management_portal.ai_provider_registry import AIProviderRegistry

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Protocol

_BATCH = threading.local()
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionAccount:
    account_id: str
    provider: str
    route_id: str
    capacity_key: str
    max_concurrency: int = 8
    quota_policy: str = "none"
    aliases: tuple[str, ...] = ()
    billing_account_id: str = ""
    usage_aliases: tuple[tuple[str, str], ...] = ()
    # The registration kind (subscription_login, api_profile, service).
    kind: str = ""

    @property
    def quota_enabled(self) -> bool:
        return self.quota_policy == "reserve" or bool(self.billing_account_id)


class AccountAdapter(Protocol):
    provider: str

    def accounts(self) -> tuple[ExecutionAccount, ...]: ...
    def resolve(self, route_id: str, options: dict[str, Any], *, bound: bool = False) -> ExecutionAccount: ...
    def observe(self, account: ExecutionAccount, *, force: bool = False) -> dict[str, Any]: ...
    def catalog(self, account: ExecutionAccount) -> dict[str, Any]: ...


@dataclass(frozen=True)
class UsageIdentities:
    """One snapshot of Backend identities for catalog, ledger writes and reads."""

    backends: set[str]
    profiles: dict[str, tuple[str, str]]
    accounts: dict[tuple[str, str], tuple[str, str]]

    def normalize(self, provider: str, account_id: str = "") -> tuple[str, str]:
        exact = self.accounts.get((provider, account_id))
        if exact is not None:
            return exact
        return provider, account_id

    def source_providers(self, backend: str) -> list[str]:
        """Candidate SQL keys; exact account normalization follows the query."""
        return sorted({backend}
                      | {key[0] for key, value in self.accounts.items() if value[0] == backend})


class ExecutionAccounts:
    def __init__(self, adapters: tuple[AccountAdapter, ...], *, provider_registry=None, dynamic=False) -> None:
        from management_portal.ai_provider_registry import shared_ai_provider_registry

        self.provider_registry = provider_registry or shared_ai_provider_registry
        self.dynamic = dynamic
        self._refreshed_at = time.monotonic()
        self.adapters = {adapter.provider: adapter for adapter in adapters}
        entries = tuple(account for adapter in adapters for account in adapter.accounts())
        self._accounts = {account.account_id: account for account in entries}
        if len(self._accounts) != len(entries):
            raise ValueError("Execution account IDs must be globally unique")

    def refresh(self):
        if self.dynamic and time.monotonic() - self._refreshed_at >= 1:
            current = default_execution_accounts(provider_registry=self.provider_registry, tolerate_unavailable=True)
            self.adapters, self._accounts = current.adapters, current._accounts
            self._refreshed_at = time.monotonic()

    def accounts(self) -> tuple[ExecutionAccount, ...]:
        self.refresh()
        return tuple(self._accounts.values())

    def get(self, account_id: str) -> ExecutionAccount:
        self.refresh()
        account_id = next((a.account_id for a in self._accounts.values() if account_id in a.aliases), account_id)
        try:
            return self._accounts[account_id]
        except KeyError:
            raise ValueError(f"unknown Execution account: {account_id}") from None

    def resolve(self, provider: str, route_id: str = "", options: dict[str, Any] | None = None, *, bound: bool = False) -> ExecutionAccount | None:
        self.refresh()
        adapter = self.adapters.get(provider)
        return adapter.resolve(route_id, options or {}, bound=bound) if adapter else self._accounts.get(provider)

    def canonical_route(self, provider: str, route_id: object) -> str:
        adapter = self.adapters.get(provider)
        if not adapter or not str(route_id or "").strip():
            return ""
        normalize = getattr(adapter, "canonical_route", None)
        if callable(normalize):
            return str(normalize(route_id) or "")
        try:
            return str(adapter.resolve(str(route_id), {}, bound=True).route_id)
        except ValueError:
            return ""

    def resident_selection(self, provider: str, source_route: str = "") -> dict[str, Any]:
        adapter = self.adapters.get(provider)
        select = getattr(adapter, "resident_selection", None)
        if callable(select):
            return select(source_route)
        return {"enabled": False, "account_ids": [], "source_account_id": "", "default_account_id": ""}

    def derive_resident_account(self, definition, source_route: str, source_has_session: bool,
                                requested: str, inherited_settings: dict[str, Any]) -> dict[str, Any]:
        adapter = self.adapters[definition.provider]
        derive = getattr(adapter, "derive_resident_account", None)
        if callable(derive):
            return derive(source_route, source_has_session, requested, inherited_settings)
        account = adapter.resolve(requested or source_route, {}, bound=False)
        if account is None:
            raise ValueError(f"{definition.label} Fork requires an execution account")
        route = account.route_id
        return {
            "backend_route_id": route,
            "provider_profile": route,
            "runtime_settings": definition.runtime_adapter().reconcile(definition, route, inherited_settings),
        }

    def configuration(self, provider: str, route_id: str = "", options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Public identity for native routes and registered completion profiles."""
        account = self.resolve(provider, route_id, options)
        if account is not None:
            from .account_registrations import registration
            row = registration(account.account_id)
            return {"account_id": account.account_id, "provider": account.provider,
                    "backend_route_id": account.route_id, "capacity_key": account.capacity_key,
                    "quota_protection_enabled": account.quota_enabled,
                    "enabled": row["enabled"] if row else True,
                    "account_status": "disabled" if row and not row["enabled"] else "enabled"}
        return {}

    def for_request(self, request: dict[str, Any]) -> ExecutionAccount | None:
        self.refresh()
        if request.get("service_type") == "chat_completion":
            return self._accounts.get(str(request.get("provider") or ""))
        if request.get("service_type") != "agent_session":
            return None
        return self.resolve(str(request.get("provider") or ""),
                            str((request.get("session") or {}).get("backend_route_id") or ""),
                            request.get("runtime_options") or {})

    @staticmethod
    def usage_backends() -> set[str]:
        from .provider_catalog import AGENT_PROVIDERS
        return set(AGENT_PROVIDERS)

    def usage_identities(self, entries=None) -> UsageIdentities:
        from management_portal.ai_provider_registry import shared_ai_provider_registry
        backends = self.usage_backends()
        profiles, accounts = {}, {}
        for entry in shared_ai_provider_registry.entries() if entries is None else entries:
            provider, account_id = entry["provider"], entry["id"]
            identity = (provider, account_id)
            profiles[account_id] = identity
            accounts[(entry["provider"], account_id)] = identity
        for account in self.accounts():
            # Adapters own historical ledger aliases independently of
            # today's HTTP API catalog. Real API identities take priority.
            for source in account.usage_aliases:
                accounts.setdefault(source, (account.provider, account.account_id))
        return UsageIdentities(backends, profiles, accounts)

    def usage_catalog(self) -> list[dict[str, str]]:
        from management_portal.ai_provider_registry import shared_ai_provider_registry
        entries = shared_ai_provider_registry.entries()
        identities = self.usage_identities(entries)
        catalog = {identities.profiles.get(a.provider, identities.normalize(a.provider, a.account_id))
                   for a in self.accounts()}
        catalog.update(identities.profiles[entry["id"]] for entry in entries if AIProviderRegistry.is_enabled(entry))
        return [{"provider": provider, "account_id": account_id} for provider, account_id in sorted(catalog)]

    def usage_identity(self, request: dict[str, Any]) -> tuple[str, str]:
        """Resolve the actual execution route, never a Resident's current binding."""
        provider = str(request.get("provider") or "unknown").strip().lower()
        if request.get("service_type") == "chat_completion":
            from management_portal.ai_provider_registry import shared_ai_provider_registry
            try:
                entry = shared_ai_provider_registry.get(provider)
            except (KeyError, ValueError):
                return provider, provider
            return self.usage_identities([entry]).normalize(str(entry.get("provider") or provider), str(entry.get("id") or provider))
        try:
            account = self.for_request(request)
        except (KeyError, ValueError):
            account = None
        provider, account_id = (account.provider, account.account_id) if account else (provider, "")
        return provider, account_id

    def reroute(self, request: dict[str, Any], route_id: str) -> dict[str, Any]:
        """Apply a selected route and its adapter-owned runtime settings together."""
        provider = str(request.get("provider") or "")
        account = self.resolve(provider, route_id)
        if account is None:
            raise ValueError("unknown account route")
        options = dict(request.get("runtime_options") or {})
        update_options = getattr(self.adapters[provider], "route_options", None)
        if update_options is not None:
            options = update_options(account, options)
        return {**request, "session": {**(request.get("session") or {}), "backend_route_id": account.route_id},
                "runtime_options": options}

    def observe(self, account: ExecutionAccount, *, force: bool = False) -> dict[str, Any]:
        return self.adapters[account.provider].observe(account, force=force)

    def runtime_catalog(self, account_id: str = "", *, backend: str = "", include_backends: bool = False) -> dict[str, Any]:
        from .provider_catalog import AGENT_PROVIDERS
        if include_backends:
            from .account_registrations import registrations
            disabled = {row["account_id"] for row in registrations() if not row["enabled"]}
            rows = []
            for definition in AGENT_PROVIDERS.values():
                accounts = [a for a in self.accounts() if a.provider == definition.provider and a.account_id not in disabled]
                row = {"backend": definition.provider, "provider": definition.provider, "label": definition.label,
                       "seeded_compaction": definition.seeded_compaction,
                       "runtime_settings": list(definition.runtime_settings), "operations": sorted(definition.operations),
                       "runtime_capabilities": definition.runtime_capabilities(),
                       "account_selection": {"enabled": callable(getattr(self.adapters.get(definition.provider), "resident_selection", None))},
                       "routes": []}
                for account in accounts:
                    route = {"account_id": account.account_id, "backend_route_id": account.route_id, "label": account.account_id}
                    # Configured model capabilities are local adapter data. Live
                    # catalogs are read only when that account is selected.
                    if definition.local_catalog:
                        try:
                            route.update(self.runtime_catalog(account.account_id))
                        except Exception:  # noqa: BLE001 - one broken route must not hide the others
                            LOGGER.warning("Runtime catalog unavailable for %s", account.account_id, exc_info=True)
                            continue
                    row["routes"].append(route)
                default = self.resident_selection(definition.provider).get("default_account_id") or definition.default_route
                row["default_route"] = next((a.route_id for a in accounts if default in (a.account_id, a.route_id)),
                                             accounts[0].route_id if accounts else "")
                rows.append(row)
            api_entries = [entry for entry in self.provider_registry.entries()
                           if AIProviderRegistry.supports_model_api(entry)
                           and entry["id"] in self._accounts and entry["id"] not in disabled]
            if api_entries:
                from .resident_configuration import api_capabilities
                capabilities = api_capabilities()
                rows.append({"backend": "provider_api", "provider": "provider_api", "label": "Model API",
                             "runtime_settings": capabilities["settings"], "operations": [],
                             "runtime_capabilities": capabilities, "account_selection": {"enabled": False},
                             "routes": [{**self._api_runtime_catalog(entry, include_routes=False), "label": entry["id"]} for entry in api_entries]})
            return {"schema": "tfi.agent-backends.v1", "backends": rows}
        if backend == "provider_api":
            return self._api_runtime_catalog(self.provider_registry.get(account_id))
        definition = AGENT_PROVIDERS.get(backend)
        account = self.get(account_id) if account_id else self.resolve(definition.provider if definition else "codex")
        if account is None:
            raise ValueError("Backend has no runtime account")
        if account.provider not in AGENT_PROVIDERS:
            return self._api_runtime_catalog(self.provider_registry.get(account.account_id))
        definition = AGENT_PROVIDERS[account.provider]
        result = self.adapters[account.provider].catalog(account)
        capabilities = definition.runtime_capabilities()
        return {**result, "schema": "tfi.agent-runtime-catalog.v1", "backend": definition.provider,
                "provider": definition.provider, "account_id": account.account_id, "backend_route_id": account.route_id,
                "runtime_settings": capabilities["settings"], "operations": capabilities["operations"],
                "runtime_capabilities": capabilities}

    def _api_runtime_catalog(self, entry: dict[str, Any], *, include_routes: bool = True) -> dict[str, Any]:
        from .account_adapters import ApiProfileAccounts
        from .resident_configuration import api_capabilities
        from .model_api_protocols import protocol_settings

        if not AIProviderRegistry.supports_model_api(entry):
            raise ValueError("backend_route_id must reference an enabled Model API")
        # A native agent and direct API calls can use the same billing account.
        # The selected backend owns model capabilities; the account keeps its
        # single scheduler identity and capacity settings.
        account = self.get(entry["id"])
        result = ApiProfileAccounts(entry).catalog(account)
        capabilities = api_capabilities()
        routes = {"api_routes": [self._api_runtime_catalog(item, include_routes=False)
                  for item in self.provider_registry.entries() if AIProviderRegistry.supports_model_api(item)
                  and item["id"] in self._accounts]} if include_routes else {}
        return {**result, **routes, "schema": "tfi.agent-runtime-catalog.v1", "backend": "provider_api",
                "provider": "provider_api", "account_id": account.account_id, "backend_route_id": entry["id"],
                "api_protocol": entry["kind"], "api_settings": protocol_settings(entry),
                "runtime_settings": capabilities["settings"], "operations": [], "runtime_capabilities": capabilities}


def default_execution_accounts(*, account_registry=None, provider_registry=None, tolerate_unavailable=False) -> ExecutionAccounts:
    from . import account_adapters
    from .provider_catalog import AGENT_PROVIDERS
    from management_portal.ai_provider_registry import shared_ai_provider_registry
    adapters = tuple(
        getattr(account_adapters, provider.account_adapter)(account_registry=account_registry, provider_registry=provider_registry)
        if provider.account_adapter else account_adapters.ConfiguredAccounts(provider, tolerate_unavailable=tolerate_unavailable)
        for provider in AGENT_PROVIDERS.values()
    )
    existing = {account.account_id for adapter in adapters for account in adapter.accounts()}
    from .account_registrations import registrations
    rows = registrations("provider_api")
    registry = provider_registry or shared_ai_provider_registry
    entries = {entry["id"]: entry for entry in registry.entries()}
    profiles = tuple(account_adapters.ApiProfileAccounts(entries.get(row["config"]["api_id"], {"id": row["config"]["api_id"]}), row)
                     for row in rows if row["account_id"] not in existing)
    return ExecutionAccounts((*adapters, *profiles), provider_registry=provider_registry,
                             dynamic=account_registry is None and provider_registry is None)


@contextmanager
def shared_account_configuration() -> Iterator[None]:
    """Build the account adapters once for a batch read of many Residents.

    Building them reads every Provider's account files and the Model API
    registry; a list of fifty Residents rebuilt them fifty times. Configuration
    read inside one batch comes from one build, so nothing that changes it may
    run inside the scope.
    """
    if getattr(_BATCH, "active", False):
        yield
        return
    _BATCH.active, _BATCH.accounts = True, None
    try:
        yield
    finally:
        _BATCH.active, _BATCH.accounts = False, None


def resolve_account_configuration(provider: str, backend_route_id: str = "", *, runtime_options=None) -> dict[str, Any]:
    """Read adapter configuration without submitting or waiting for execution."""
    if not getattr(_BATCH, "active", False):
        return default_execution_accounts(tolerate_unavailable=True).configuration(provider, backend_route_id, runtime_options)
    if _BATCH.accounts is None:
        _BATCH.accounts = default_execution_accounts(tolerate_unavailable=True)
    return _BATCH.accounts.configuration(provider, backend_route_id, runtime_options)


def runtime_configuration_catalog() -> dict[str, Any]:
    return default_execution_accounts(tolerate_unavailable=True).runtime_catalog(include_backends=True)
