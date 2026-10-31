"""Collect adapter observations; publish one account contract to all clients."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import json
import logging
import sys
import threading
import time

from .account_quota import POLL_SECONDS
from .activity_redaction import redact_sensitive_text
from .account_usage_store import AccountUsageStore
from .account_login import login_store, now as login_now
from .provider_failures import is_authentication_failure


logger = logging.getLogger(__name__)


def _subscription(account):
    kind = getattr(account, "kind", "")
    return kind == "subscription_login" if kind else (
        account.provider == "codex" or account.provider == "claude" and account.quota_policy == "reserve")


class AccountMonitor:
    def __init__(self, accounts, scheduler, *, usage_store=None, login_state_store=None):
        self.accounts, self.scheduler = accounts, scheduler
        from .provider_balance_adapters import BillingObservations
        self.billing = BillingObservations()
        self._samples = {}
        self._usage = {}
        self._usage_store = usage_store if usage_store is not None else AccountUsageStore()
        self._login_store = login_state_store if login_state_store is not None else login_store()
        for account in accounts.accounts():
            if self._reads_usage(account):
                self._usage[account.account_id] = self._stored_usage(account)
        self._last_refresh_request = 0.0
        self._refresh_refused = False
        self._refreshing = False
        self._lock = threading.RLock()
        self.stopped, self.wakeup = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, name="execution-account-status", daemon=True)

    def _stored_usage(self, account):
        try:
            return self._usage_store.read(account.provider, account.account_id)
        except Exception:
            logger.warning("Could not load saved account usage for %s", account.account_id)
            return {}

    def refresh_account(self, account, *, force=False):
        observed_at = login_now()
        try:
            sample = self.accounts.observe(account, force=force)
            sample["checked_at"] = time.time()
            if _subscription(account):
                if "login_observation" in sample:
                    state = sample["login_observation"]
                    try:
                        source_time = datetime.fromisoformat(sample.get("login_observed_at") or "")
                    except (TypeError, ValueError):
                        source_time = None
                    if state in {"logged_in", "login_expired", "logged_out"} and source_time is not None and source_time.tzinfo is not None:
                        self._login_store.observe(account.account_id, state, observed_at=source_time)
                elif is_authentication_failure(sample.get("error_code") or sample.get("error", ""), provider=account.provider):
                    self._login_store.observe(account.account_id, "login_expired", observed_at=observed_at)
                elif sample.get("status") in {"ok", "available"}:
                    self._login_store.observe(account.account_id, "logged_in", observed_at=observed_at)
            if sample.get("status") == "unavailable":
                with self._lock:
                    sample = self._failed_sample(account, sample)
            if account.billing_account_id and account.quota_policy != "reserve":
                from .account_quota import finite_number
                balance = self.billing.observe(account.billing_account_id)
                sample["balance"] = balance
                try:
                    observed = datetime.fromisoformat(balance.get("updated_at", "").replace("Z", "+00:00")).timestamp()
                except (TypeError, ValueError):
                    observed = 0
                known = balance.get("status") == "ok" and balance.get("metric_kind") == "balance" and not balance.get("stale") and bool(balance.get("currency")) and finite_number(balance.get("amount"))
                sample["quota"] = {"status": "available" if known else "unknown", "complete": known,
                                   "remaining_balance": balance.get("amount") if known else None,
                                   "currency": balance.get("currency", ""), "observed_at": observed, "max_age_seconds": 120}
            if account.quota_enabled and self.scheduler.quota_reserve_enabled:
                self.scheduler.update_account_quota(account.account_id, sample.get("quota") or {"status": "unknown"})
            with self._lock:
                self._samples[account.account_id] = sample
        except Exception as error:
            if is_authentication_failure(error, provider=account.provider):
                error_code, message = "provider_authentication_failed", "登录凭据失效，请重新登录"
                if _subscription(account):
                    self._login_store.observe(account.account_id, "login_expired", observed_at=observed_at)
            elif isinstance(error, TimeoutError):
                error_code, message = "account_read_timeout", "额度读取超时，请稍后重试"
            else:
                error_code, message = "account_read_unavailable", "账号用量暂不可读取"
            with self._lock:
                self._samples[account.account_id] = self._failed_sample(account, {
                    "status": "unavailable", "error_code": error_code, "error": message, "refreshing": False})
        self.refresh_usage(account)

    def _failed_sample(self, account, sample):
        previous = deepcopy(self._samples.get(account.account_id) or {})
        for window in previous.get("rate_limits") or []:
            window["stale"] = True
        if isinstance(previous.get("credits"), dict):
            previous["credits"]["stale"] = True
        return {**previous, **sample}

    def _reads_usage(self, account):
        # Daily usage buckets describe a subscription; an API key's spend is its balance.
        return callable(getattr(self.accounts.adapters[account.provider], "usage", None)) and _subscription(account)

    def refresh_usage(self, account):
        if not self._reads_usage(account):
            return
        read = self.accounts.adapters[account.provider].usage
        with self._lock:
            previous = deepcopy(self._usage.get(account.account_id) or {})
        now = time.time()
        retry_after = 1800 if previous.get("status") == "ok" else 60
        if now - previous.get("checked_at", 0) < retry_after:
            return
        try:
            sample = self._usage_store.save(account.provider, account.account_id, read(account), observed_at=now)
            sample["checked_at"] = now
        except Exception as exc:  # noqa: BLE001 - one account's failure keeps its last sample
            stored = self._stored_usage(account)
            if stored.get("observed_at", 0) > previous.get("observed_at", 0):
                previous = stored
            # Keep why the read failed: a retained stale sample said only "stale",
            # and the Home usage warning could not name the account or the cause.
            error = {"code": type(exc).__name__, "at": now}
            if (previous.get("error") or {}).get("code") != error["code"]:
                print("execution account usage read failed: " + json.dumps({
                    "account_id": account.account_id, "provider": account.provider, "error": error["code"],
                    "message": redact_sensitive_text(str(exc))[:200],
                }, ensure_ascii=False), file=sys.stderr, flush=True)
            sample = {**previous, "checked_at": now, "error": error,
                      "status": "stale" if "daily_buckets" in previous else "unavailable"}
        with self._lock:
            self._usage[account.account_id] = sample

    def usage_snapshot(self):
        with self._lock:
            samples = deepcopy(self._usage)
        items = []
        identities = self.accounts.usage_identities()
        for account in self.accounts.accounts():
            if not self._reads_usage(account):
                continue
            sample = samples.get(account.account_id) or {"status": "loading"}
            backend, account_id = identities.normalize(account.provider, account.account_id)
            items.append({"account_id": account_id, "provider": backend, "usage": sample})
        return {"schema": "tfi.execution-account-usage.v1", "accounts": items, "catalog": self.accounts.usage_catalog()}

    def request_refresh(self):
        with self._lock:
            now = time.monotonic()
            # Only a refused request reports a wait; an accepted one clears it.
            self._refresh_refused = now - self._last_refresh_request < 60
            if not self._refresh_refused:
                self._last_refresh_request = now
                self.wakeup.set()

    def _run(self):
        with ThreadPoolExecutor(max_workers=max(1, len(self.accounts.accounts())), thread_name_prefix="execution-accounts") as pool:
            while not self.stopped.is_set():
                with self._lock:
                    force = self.wakeup.is_set()
                    self.wakeup.clear()
                    self._refreshing = True
                try:
                    list(pool.map(lambda account: self.refresh_account(account, force=force), self.accounts.accounts()))
                finally:
                    with self._lock:
                        self._refreshing = False
                self.wakeup.wait(POLL_SECONDS)

    def snapshot(self):
        from .account_migration_types import migration_type
        from .provider_catalog import AGENT_PROVIDERS
        health = self.scheduler.health()
        partitions = health.get("capacity_partitions") or {}
        with self._lock:
            samples = deepcopy(self._samples)
            refreshing = self._refreshing or self.wakeup.is_set()
            retry_after = max(0, 60 - (time.monotonic() - self._last_refresh_request)) if self._refresh_refused else 0
        items = []
        for account in self.accounts.accounts():
            settings = partitions.get(account.capacity_key) or {}
            sample = samples.get(account.account_id) or {"status": "loading", "refreshing": True, "rate_limits": []}
            # Raw observations feed the scheduler. Its configured quota_reserve
            # is the single public interpretation of the account's threshold.
            sample.pop("quota", None)
            quota = settings.get("quota_reserve") or {}
            # A reading stays current until a read of it fails (refresh_account
            # then marks it stale); its age alone never expires it.
            items.append({**sample, "account_id": account.account_id, "provider": account.provider, "backend_route_id": account.route_id, "billing_account_id": account.billing_account_id,
                          "login_state": self._login_store.login_state(account.account_id) if _subscription(account) else "not_applicable",
                          "max_concurrency": settings.get("limit", account.max_concurrency),
                          "dispatch_interval_min_seconds": settings.get("dispatch_interval_min_seconds", health.get("dispatch_interval", {}).get("min_seconds", 2)),
                          "dispatch_interval_max_seconds": settings.get("dispatch_interval_max_seconds", health.get("dispatch_interval", {}).get("max_seconds", 6)),
                          "quota_reserve": quota, "close_threshold": settings.get("close_threshold"), "capabilities": {"scheduler_settings": True, "resident_details": True}})
            items[-1]["kind"] = account.kind
            if account.provider in AGENT_PROVIDERS:
                items[-1]["migration_type"] = migration_type(items[-1])
        return {"schema": "tfi.execution-accounts.v1", "accounts": items,
                "exchange_rates": self.billing.exchange_rates_snapshot(),
                "refreshing": refreshing or any(item.get("refreshing") for item in items),
                "refresh_retry_after_seconds": int(retry_after + 0.999)}

    def start(self):
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.wakeup.set()
        if self.thread.is_alive():
            self.thread.join(timeout=21)
