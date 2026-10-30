"""Native account configuration and observations at the backend boundary."""
from __future__ import annotations

from management_portal.ai_provider_registry import AIProviderRegistry

import logging
import time
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .accounts import ExecutionAccount
from .provider_catalog import AGENT_PROVIDERS

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodexAccountRoute:
    account_id: str
    codex_home: Path
    socket_path: Path
    max_concurrency: int
    fallback_account_ids: tuple[str, ...] = ()
    registration_id: str = ""
    kind: str = "subscription_login"
    # The Model API an api_profile route authenticates with.
    api_id: str = ""


class CodexRoutes:
    """Codex route projection of registered identities; never reads YAML."""

    def __init__(self):
        self._load()

    def _load(self):
        from .account_registrations import default_route, registrations
        from .account_hosting import account_home, codex_socket_path
        rows = registrations("codex")
        self._accounts = {row["route_id"]: CodexAccountRoute(
            row["route_id"], account_home("codex", row["account_id"]), codex_socket_path(row["account_id"]),
            row["max_concurrency"], tuple(next((r["route_id"] for r in rows if r["account_id"] == value), value)
                                          for value in row["config"].get("fallback_account_ids", [])), row["account_id"],
            row["kind"], row["config"].get("api_id", "")) for row in rows}
        self._aliases = {row["account_id"]: row["route_id"] for row in rows}
        self._enabled = {row["route_id"] for row in rows if row["enabled"]}
        if sum(1 for row in rows if row["enabled"] and row["config"].get("is_default")) > 1:
            raise ValueError("Multiple default Codex accounts")
        # A site's only enabled Codex account is its default without being marked;
        # among several, the default must be chosen explicitly.
        self.default_account_id = default_route(rows)
        self._loaded_at = time.monotonic()

    def _refresh(self):
        if time.monotonic() - self._loaded_at >= 1:
            self._load()

    def get(self, account_id):
        self._refresh()
        key = str(account_id or "").strip().lower()
        try:
            return self._accounts[self._aliases.get(key, key)]
        except KeyError:
            raise ValueError(f"unknown Codex account route: {key}") from None

    def routes(self):
        self._refresh()
        return tuple(self._accounts.values())

    def default_for_new_session(self):
        self._refresh()
        if not self.default_account_id:
            raise ValueError("No enabled default Codex account")
        return self.get(self.default_account_id)

    def candidates(self, *, requested_account_id="", allow_fallback):
        primary = self.get(requested_account_id) if requested_account_id else self.default_for_new_session()
        ids = [primary.account_id, *(primary.fallback_account_ids if allow_fallback else ())]
        return tuple(self.get(key) for key in ids if key in self._enabled)

    def find_thread_rollout(self, account_id, backend_session_id, *, include_archived=True):
        thread = str(backend_session_id or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", thread):
            return None
        route = self.get(account_id)
        for name in ("sessions", "archived_sessions") if include_archived else ("sessions",):
            root = route.codex_home / name
            visited = set()
            for directory, children, filenames in os.walk(root, followlinks=True):
                try:
                    stat = os.stat(directory)
                except OSError:
                    children.clear()
                    continue
                identity = (stat.st_dev, stat.st_ino)
                if identity in visited:
                    children.clear()
                    continue
                visited.add(identity)
                for filename in sorted(filenames):
                    if filename.endswith(f"{thread}.jsonl"):
                        rollout = Path(directory) / filename
                        if rollout.is_file():
                            return rollout
        return None


class CodexAccounts:
    provider = "codex"

    def __init__(self, registry=None, *, account_registry=None, provider_registry=None):
        self.registry = registry or account_registry or CodexRoutes()

    def accounts(self):
        return tuple(self._account(route) for route in self.registry.routes())

    @staticmethod
    def _account(route):
        name = route.account_id.removeprefix("codex_")
        kind = getattr(route, "kind", "subscription_login")
        # An API key is billed by its Model API balance, not a subscription quota.
        api = kind == "api_profile"
        return ExecutionAccount(getattr(route, "registration_id", "") or f"codex_{name}", "codex", route.account_id,
                                f"codex:{name}", route.max_concurrency, "none" if api else "reserve", (name,),
                                billing_account_id=route.api_id if api else "", kind=kind)

    def resolve(self, route_id, options, *, bound=False):
        if bound and not route_id:
            raise ValueError("existing session requires an account route")
        route = self.registry.get(route_id) if route_id else self.registry.default_for_new_session()
        return self._account(route)

    @staticmethod
    def api_registration(route):
        """The api_profile registration behind a Codex route, ``None`` for a subscription."""
        from .account_registrations import registrations
        key = str(route or "").strip().lower()
        return next((row for row in registrations("codex") if row["kind"] == "api_profile"
                     and key in (row["route_id"], row["account_id"])), None) if key else None

    @classmethod
    def route_upstream(cls, route):
        # An API key reaches its own Model API, a service Portal has no probe for.
        return "model_api" if cls.api_registration(route) else ""

    @classmethod
    def route_takes_images(cls, route):
        # A Model API declares no image input for its models.
        return cls.api_registration(route) is None

    def canonical_route(self, route_id):
        if not str(route_id or "").strip():
            return ""
        try:
            return self.resolve(str(route_id).strip().lower(), {}).route_id
        except ValueError:
            return ""

    def resident_selection(self, source_route):
        from .account_registrations import registrations
        accounts = self.accounts()
        source = self.canonical_route(source_route)
        disabled = {row["account_id"] for row in registrations("codex") if not row["enabled"]}
        public_ids = {account.route_id: account.account_id for account in accounts}
        selectable = {route: account_id for route, account_id in public_ids.items() if account_id not in disabled}
        default = source if source in selectable else ""
        if not default:
            try:
                default = self.registry.default_for_new_session().account_id
            except ValueError:
                pass
        return {
            "enabled": True,
            "account_ids": list(selectable.values()),
            "source_account_id": public_ids.get(source, ""),
            "default_account_id": selectable.get(default, ""),
            "copy_context_when_changed": True,
        }

    def derive_resident_account(self, source_route, source_has_session, requested, inherited_settings):
        from . import resident_runtime_settings, codex_runtime_settings

        source = self.canonical_route(source_route)
        if source_has_session and not source:
            raise RuntimeError("source Codex session has no backend route")
        source = source or self.registry.default_for_new_session().account_id
        target = self.resolve(str(requested or "").strip().lower() or source, {}).route_id
        resolved = codex_runtime_settings.resolve_codex_runtime_configuration(
            inherited_settings, account_id=target,
        )
        settings = resident_runtime_settings.mark_portal_manual_authority(
            resolved,
            actor=str(inherited_settings.get("runtime_settings_authority_by") or ""),
            updated_at=str(inherited_settings.get("runtime_settings_authority_at") or ""),
        )
        return {
            "backend_route_id": target,
            "runtime_settings": settings,
        }

    def catalog(self, account):
        from management_portal.codex_app_server_client import client_for_socket
        from management_portal.codex_capabilities import runtime_catalog
        from .codex_runtime_settings import resolve_codex_runtime_catalog
        route = self.registry.get(account.route_id)
        if getattr(route, "kind", "") == "api_profile":
            # An API key's models are the ones its Model API declares; model/list
            # describes ChatGPT plans.
            from management_portal.ai_provider_registry import shared_ai_provider_registry
            from .codex_api_accounts import catalog
            return resolve_codex_runtime_catalog(catalog(shared_ai_provider_registry.get(route.api_id), account.account_id))
        return resolve_codex_runtime_catalog(runtime_catalog(client_for_socket(route.socket_path), account.account_id))

    def usage(self, account):
        from management_portal.codex_app_server_client import client_for_socket
        payload = client_for_socket(self.registry.get(account.route_id).socket_path).request("account/usage/read", {}, timeout=15.0)
        buckets = payload.get("dailyUsageBuckets")
        if not isinstance(buckets, list):
            raise ValueError("Account usage response is missing daily buckets")
        daily = []
        for bucket in buckets:
            if not isinstance(bucket, dict) or not bucket.get("startDate"):
                raise ValueError("Invalid account usage bucket")
            tokens = bucket.get("tokens")
            if type(tokens) is not int or tokens < 0:
                raise ValueError("Account usage bucket is missing a valid token count")
            daily.append({"date": str(bucket["startDate"]), "tokens": tokens})
        return {"status": "ok", "daily_buckets": daily}

    def observe(self, account, *, force=False):
        from management_portal.codex_app_server_client import client_for_socket
        from .codex_quota import credits, quota_sample
        route = self.registry.get(account.route_id)
        if getattr(route, "kind", "") == "api_profile":
            # Rate-limit windows belong to ChatGPT plans; an API key's spend is its balance.
            return {"status": "ok", "rate_limits": []}
        client = client_for_socket(route.socket_path)
        payload = client.request("account/rateLimits/read", {}, timeout=15.0)
        sample = quota_sample(payload, observed_at=time.time())
        credit_sample = credits(payload)
        if credit_sample is not None:
            credit_sample.update(observed_at=sample["observed_at"], stale=False)
        return {"status": "ok", "quota": sample, "credits": credit_sample,
                "rate_limits": [{"id": window["window"],
                                 "label": "Weekly" if window.get("window_duration_mins") == 10080 else "5 小时" if window.get("window_duration_mins") == 300 else window["window"],
                                 "remaining_percent": window["remaining_percent"],
                                 "window_duration_mins": window.get("window_duration_mins"),
                                 "resets_at": window.get("resets_at"),
                                 "observed_at": sample["observed_at"],
                                 "stale": False} for window in sample["windows"]]}


class ClaudeAccounts:
    provider = "claude"

    def __init__(self, registry=None, *, account_registry=None, provider_registry=None):
        from .account_registrations import registrations
        self.entries = {row["account_id"]: row for row in registrations("claude")}

    def accounts(self):
        return tuple(ExecutionAccount(row["account_id"], self.provider, row["route_id"], f"account:{row['account_id']}",
                                      row["max_concurrency"], "reserve" if row["kind"] == "subscription_login" else "none",
                                      billing_account_id=row["config"].get("api_id", ""), kind=row["kind"],
                                      # Historical native usage used the vendor
                                      # before subscriptions moved to registrations.
                                      usage_aliases=(("anthropic", row["account_id"]),)
                                      if row["kind"] == "subscription_login" else ()) for row in self.entries.values())

    def resolve(self, route_id, options, *, bound=False):
        profile = str(options.get("provider_profile") or route_id or AGENT_PROVIDERS[self.provider].default_route)
        if route_id and route_id != profile:
            raise ValueError("Account changes require a session migration")
        try:
            return next(account for account in self.accounts() if account.route_id == profile)
        except StopIteration:
            raise ValueError(f"unknown Execution account route: {profile}") from None

    def route_options(self, account, options):
        return {**options, "provider_profile": account.route_id}

    def catalog(self, account):
        from management_portal.claude_provider_catalog import catalog
        return {**catalog(account.route_id), "account_id": account.account_id}

    def observe(self, account, *, force=False):
        from management_portal.claude_code_client import shared_client
        if account.quota_policy == "none":
            return {"status": "ok", "rate_limits": []}
        snapshot = shared_client().account_usage(account.route_id, force_refresh=force)
        error_code = snapshot.get("quota_error_code", "")
        error = snapshot.get("quota_error", "")
        login_observation, login_observed_at = None, None
        if error_code == "provider_authentication_failed":
            login_observation = "login_expired"
            login_observed_at = snapshot.get("quota_error_observed_at")
        elif error:
            # /usage can return local token totals even when logged out. Missing
            # quota rows alone say nothing about auth; ask the native login owner.
            from .account_login import now, provider_logged_in
            started = now().isoformat()
            try:
                logged_in = provider_logged_in("claude", account.account_id)
            except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired):
                logged_in = None
            if logged_in is False:
                error_code, error = "provider_authentication_failed", "Claude 登录凭据失效，请重新登录"
            if logged_in is not None:
                login_observation = "logged_in" if logged_in else "login_expired"
                login_observed_at = started
        windows = snapshot.get("rate_limits") or []
        if not error and snapshot.get("quota_status") == "available":
            login_observation = "logged_in"
            login_observed_at = min((w["observed_at"] for w in windows if w.get("observed_at")), default=None)
        found = {window["id"] for window in windows}
        windows = [*windows, *({"id": key, "label": label, "remaining_percent": None, "stale": False} for key, label in (("five_hour", "5 小时"), ("seven_day", "每周")) if key not in found)]
        known = [window for window in windows if isinstance(window.get("remaining_percent"), (int, float)) and not window.get("stale")]
        remaining = min((window["remaining_percent"] for window in known), default=None)
        return {"status": snapshot.get("quota_status", "unknown"),
                "login_observation": login_observation, "login_observed_at": login_observed_at,
                "error_code": error_code,
                "error": error, "refreshing": bool(snapshot.get("quota_refreshing")),
                "rate_limits": windows,
                "quota": {"observed_at": min((w.get("observed_at_unix", 0) for w in known), default=0), "max_age_seconds": 300,
                          "valid_until": min((w.get("fresh_until", 0) for w in known), default=0),
                          "status": "available" if remaining is not None and len(known) == len(windows) else "unknown",
                          "complete": remaining is not None and len(known) == len(windows),
                          "remaining_percent": remaining,
                          "windows": [{"window": w["id"], "remaining_percent": w["remaining_percent"], "resets_at": w.get("resets_at")} for w in known]}}


class ApiProfileAccounts:
    """One registered completion profile using the common scheduler policy."""

    def __init__(self, entry, registration=None):
        self.provider = entry["id"]
        self.entry = entry
        if registration is None:
            from .account_registrations import registration as read_registration
            registration = read_registration(entry["id"])
        self.registration = registration

    def accounts(self):
        if not self.registration:
            return ()
        return (ExecutionAccount(self.registration["account_id"], self.provider, "", f"account:{self.registration['account_id']}",
                                 self.registration["max_concurrency"],
                                 billing_account_id=self.provider),)

    def resolve(self, route_id, options, *, bound=False):
        if route_id:
            raise ValueError("API completion accounts do not have native session routes")
        return self.accounts()[0]

    def catalog(self, account):
        # The advanced model list is optional; the registered default is still
        # a declared model and must be selectable in Resident Settings.
        models = AIProviderRegistry.harness_models(self.entry)
        default = str(self.entry.get("default_model") or "")
        return {"models": models, "default_model": default, "efforts_by_model": {},
                "default_efforts_by_model": {}, "service_tiers_by_model": {}}

    def observe(self, account, *, force=False):
        return {"status": "ok", "rate_limits": []}


class KimiAccounts:
    provider = "kimi"

    def __init__(self, **kwargs):
        from .account_registrations import registrations
        self.entries = registrations("kimi")

    def accounts(self):
        from management_portal.kimi_code_server import provider_profile_id
        profile = provider_profile_id()
        return tuple(ExecutionAccount(row["account_id"], self.provider, row["route_id"], f"account:{row['account_id']}",
                                      row["max_concurrency"], billing_account_id=(row["account_id"] if row["kind"] == "subscription_login"
                                          else row["config"].get("api_id", profile)),
                                      kind=row["kind"]) for row in self.entries)

    def resolve(self, route_id, options, *, bound=False):
        route_id = route_id or str(options.get("provider_profile") or "")
        for account in self.accounts():
            if route_id in {account.account_id, account.route_id}:
                return account
        raise ValueError("Unknown Kimi account route")

    def route_options(self, account, options):
        return {**options, "provider_profile": account.route_id}

    def catalog(self, account):
        from management_portal.kimi_provider_catalog import catalog
        return catalog(next(row for row in self.entries if row["account_id"] == account.account_id))

    def observe(self, account, *, force=False):
        if account.kind != "subscription_login":
            return {"status": "ok", "rate_limits": []}
        from management_portal.kimi_account_runtime import client_for_route
        from .account_login import now
        observed_at = now().isoformat()
        # Monitoring must neither launch a server nor infer authentication from
        # process availability. The native managed provider owns this fact.
        auth = client_for_route(account.route_id, for_control=True)._request("GET", "auth")
        status = (auth.get("managed_provider") or {}).get("status")
        state = ("logged_in" if status == "authenticated" else
                 "login_expired" if status in {"expired", "revoked"} else "logged_out")
        return {"status": "ok" if state == "logged_in" else "unknown", "rate_limits": [],
                "login_observation": state, "login_observed_at": observed_at}


class ConfiguredAccounts:
    """Default account adapter for a backend with a declarative route catalog."""
    def __init__(self, definition, *, tolerate_unavailable=False):
        self.definition = definition
        self.provider = definition.provider
        from .account_registrations import registrations
        try:
            self._routes = registrations(self.provider)
        except Exception:
            if not tolerate_unavailable:
                raise
            self._routes = []

    def accounts(self):
        return tuple(ExecutionAccount(row["account_id"], self.provider, row["route_id"],
                                      f"account:{row['account_id']}", row["max_concurrency"],
                                      billing_account_id=row["config"].get("api_id", ""), kind=row["kind"]) for row in self._routes)

    def resolve(self, route_id, options, *, bound=False):
        route_id = route_id or str(options.get("provider_profile") or self.definition.default_route)
        for account in self.accounts():
            if route_id in {account.route_id, account.account_id}:
                return account
        raise ValueError(f"unknown Execution account route: {route_id}")

    def route_options(self, account, options):
        return {**options, "provider_profile": account.route_id}

    def catalog(self, account):
        return {**self.definition.catalog(account.route_id), "account_id": account.account_id}

    def observe(self, account, *, force=False):
        return {"status": "ok", "rate_limits": []}


class AntigravityAccounts(ConfiguredAccounts):
    """Native authentication and the read-only ACP quota command."""

    def __init__(self, **kwargs):
        super().__init__(AGENT_PROVIDERS["antigravity"])

    def observe(self, account, *, force=False):
        from management_portal.antigravity_accounts import observe_account
        return observe_account(account.account_id)
