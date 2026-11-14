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

# Quota reads are rationed (lukai, 2026-11-13). A scheduled check runs hourly
# and reads an account only when its last good reading has expired: it is
# older than 8 hours, or one of its windows reset after it was taken. Apart
# from that, a Home load or refresh click asks for a read. Every real read of
# one account, whatever asked for it, is at least 10 minutes after the last.
SCHEDULED_CHECK_SECONDS = 3600
READING_EXPIRES_SECONDS = 8 * 3600
MIN_READ_INTERVAL_SECONDS = 600
from .activity_redaction import redact_sensitive_text
from .account_usage_store import AccountUsageStore
from .account_observation_store import AccountObservationStore
from .account_login import login_store, now as login_now
from .provider_failures import is_authentication_failure


logger = logging.getLogger(__name__)


def _subscription(account):
    kind = getattr(account, "kind", "")
    return kind == "subscription_login" if kind else (
        account.provider == "codex" or account.provider == "claude" and account.quota_policy == "reserve")


def _rationed_quota(sample):
    """The scheduler's quota reading, valid as long as reads are rationed.

    A reading stays usable for READING_EXPIRES_SECONDS and until the first of
    its windows resets; Close thresholds then work on it between real reads.
    """
    quota = dict(sample.get("quota") or {"status": "unknown"})
    if quota.get("status") == "unknown":
        return quota
    observed = quota.get("observed_at") or 0
    resets = [w.get("resets_at") for w in sample.get("rate_limits") or [] if isinstance(w, dict)]
    resets = [r for r in resets if isinstance(r, (int, float)) and not isinstance(r, bool) and r > observed]
    quota["max_age_seconds"] = READING_EXPIRES_SECONDS
    quota["valid_until"] = min(resets) if resets else None
    return quota


class AccountMonitor:
    def __init__(self, accounts, scheduler, *, usage_store=None, login_state_store=None,
                 observation_store=None):
        self.accounts, self.scheduler = accounts, scheduler
        from .provider_balance_adapters import BillingObservations
        self.billing = BillingObservations()
        self._samples = {}
        self._usage = {}
        self._usage_store = usage_store if usage_store is not None else AccountUsageStore()
        self._observation_store = observation_store if observation_store is not None else AccountObservationStore()
        self._login_store = login_state_store if login_state_store is not None else login_store()
        for account in accounts.accounts():
            if self._reads_usage(account):
                self._usage[account.account_id] = self._stored_usage(account)
        self._last_refresh_request = 0.0
        self._refresh_refused = False
        self._refreshing = False
        self._inflight = {}
        self._last_read_attempt = {}
        self._last_good_read = {}
        for account in accounts.accounts():
            state = self._observation_store.state(account.provider, account.account_id)
            if not isinstance(state, dict):
                continue
            if state.get('attempted_at') is not None:
                self._last_read_attempt[account.account_id] = state['attempted_at']
            if state.get('success_at') is not None:
                self._last_good_read[account.account_id] = state['success_at']
                self._samples[account.account_id] = state.get('sample') or {}
        self._next_scheduled_check = 0.0
        self._lock = threading.RLock()
        self.stopped, self.wakeup = threading.Event(), threading.Event()
        from .account_observation import ObservationLifetime
        self.observations = ObservationLifetime()
        self.thread = threading.Thread(target=self._run, name="execution-account-status", daemon=True)

    def _stored_usage(self, account):
        try:
            return self._usage_store.read(account.provider, account.account_id)
        except Exception:
            logger.warning("Could not load saved account usage for %s", account.account_id)
            return {}

    def refresh_account(self, account, *, force=False, passive=False):
        # Single admission point, including direct/manual calls. Failures count
        # as attempts; passive observations neither consume nor obey this gate.
        if not passive:
            with self._lock:
                now = time.time()
                if not self._observation_store.claim(account.provider, account.account_id,
                        now=now, interval=MIN_READ_INTERVAL_SECONDS):
                    state = self._observation_store.state(account.provider, account.account_id)
                    self._refresh_refused = True
                    self._last_refresh_request = state.get('attempted_at') or now
                    return
                self._last_read_attempt[account.account_id] = now
        # Every refresh the monitor already performs is appended to the history log;
        # recording never triggers an extra Provider read.
        started_at, error_code = time.time(), ''
        try:
            with self.observations.scope():
                self._refresh_account(account, force=force, passive=passive)
        except InterruptedError:
            if not self.stopped.is_set():
                error_code = 'InterruptedError'
                raise
        except Exception as error:
            error_code = type(error).__name__
            raise
        finally:
            if not self.stopped.is_set():
                with self._lock:
                    sample = deepcopy(self._samples.get(account.account_id) or {})
                if error_code:
                    sample.update(status='unavailable', error_code=error_code)
                try:
                    self._observation_store.append(account.provider, account.account_id, sample,
                        started_at=started_at, finished_at=time.time(), forced=force)
                except Exception as error:
                    # Persistence failure must not turn a good quota read into a bad one.
                    logger.warning('Account refresh history write failed for %s (%s)',
                                   account.account_id, type(error).__name__)

    def _refresh_account(self, account, *, force=False, passive=False):
        observed_at = login_now()
        try:
            sample = (self.accounts.observe(account, force=force, passive=True) if passive
                      else self.accounts.observe(account, force=force))
            if self.stopped.is_set():
                return  # Cancellation is not a fresh quota or login observation.
            sample["checked_at"] = time.time()
            if not passive and sample.get("status") in {"ok", "available"}:
                self._observation_store.save_success(account.provider, account.account_id,
                                                    sample, now=sample['checked_at'])
                with self._lock:
                    self._last_good_read[account.account_id] = sample["checked_at"]
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
                if self.stopped.is_set():
                    return
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
                credit = sample.get("credits") or {}
                self.scheduler.update_account_quota(account.account_id, {
                    **_rationed_quota(sample),
                    "credits": {**credit, "observed_at": sample["checked_at"],
                                "authenticated": sample.get("status") == "ok"
                                and self._login_store.login_state(account.account_id) == "logged_in"},
                })
            with self._lock:
                catalog = (self._samples.get(account.account_id) or {}).get("selection_catalog") or {}
            if not passive:
                try:
                    native = self.accounts.runtime_catalog(account.account_id)
                    catalog = {"fresh": True, "checked_at": time.time(),
                               "models": [m if isinstance(m, str) else m["id"] for m in native.get("models", []) if isinstance(m, str) or m.get("enabled", True)],
                               "efforts_by_model": native.get("efforts_by_model", {}), "default_model": native.get("default_model", "")}
                except Exception as error:
                    # Catalog exceptions may contain credentials or URLs. Keep
                    # the failure observable without retaining their raw text.
                    logger.warning("Account catalog read failed for %s (%s): %s",
                                   account.account_id, account.provider, type(error).__name__)
                    catalog = {"fresh": False, "checked_at": time.time(), "models": []}
            sample["selection_catalog"] = catalog
            with self._lock:
                self._samples[account.account_id] = sample
        except Exception as error:
            if self.stopped.is_set():
                return
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
            if account.quota_enabled and self.scheduler.quota_reserve_enabled:
                self.scheduler.update_account_quota(account.account_id, {"status": "unknown", "credits": {}})
        if not self.stopped.is_set() and not passive:
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
        if self.stopped.is_set():
            return
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
            reading = read(account)
            if self.stopped.is_set():
                return
            sample = self._usage_store.save(account.provider, account.account_id, reading, observed_at=now)
            sample["checked_at"] = now
        except Exception as exc:  # noqa: BLE001 - one account's failure keeps its last sample
            if self.stopped.is_set():
                return
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
            self._refresh_refused = False
            self.wakeup.set()

    def _reading_expired(self, account, now):
        with self._lock:
            good = self._last_good_read.get(account.account_id)
            sample = self._samples.get(account.account_id) or {}
        if good is None or now - good >= READING_EXPIRES_SECONDS:
            return True
        for window in sample.get("rate_limits") or []:
            reset = window.get("resets_at") if isinstance(window, dict) else None
            if isinstance(reset, (int, float)) and not isinstance(reset, bool) and good < reset <= now:
                return True
        return False

    def _real_read_due(self, account, now, *, requested, scheduled):
        """Whether to query the Provider now; see the rationing rules above."""
        return requested or (scheduled and self._reading_expired(account, now))

    def _passive_read(self, account):
        observes = getattr(self.accounts, "observes_passively", None)
        return callable(observes) and observes(account) is True

    def _run(self):
        # A slow account must not hold the next refresh of every other account.
        # Bound resource use and keep at most one pending observation per identity.
        try:
            with ThreadPoolExecutor(max_workers=32, thread_name_prefix="execution-accounts") as pool:
                while not self.stopped.is_set():
                    with self._lock:
                        force = self.wakeup.is_set()
                        self.wakeup.clear()
                        self._refreshing = True
                    try:
                        for account_id, future in list(self._inflight.items()):
                            if future.done():
                                with self._lock:
                                    del self._inflight[account_id]
                                try:
                                    future.result()
                                except Exception as error:
                                    if not self.stopped.is_set():
                                        logger.warning("Account observation failed for %s (%s); retrying next poll",
                                                       account_id, type(error).__name__)
                        now = time.time()
                        scheduled = now >= self._next_scheduled_check
                        for account in self.accounts.accounts():
                            if self.stopped.is_set():
                                break
                            real = self._real_read_due(account, now, requested=force, scheduled=scheduled)
                            # Between real reads, a Provider whose turns already
                            # report quota is read from that cache only.
                            passive = self._passive_read(account)
                            if not real and not passive:
                                continue
                            with self._lock:
                                if account.account_id not in self._inflight:
                                    self._inflight[account.account_id] = pool.submit(
                                        self.refresh_account, account,
                                        force=force and real, passive=not real)
                        # A check that could not list the accounts runs again on the next poll.
                        if scheduled:
                            self._next_scheduled_check = now + SCHEDULED_CHECK_SECONDS
                    except Exception as error:
                        if not self.stopped.is_set():
                            # Directory/database outages are not empty account catalogs.
                            # Keep observations and retry; never log connection strings.
                            logger.warning("Account monitor poll failed (%s); retrying next poll", type(error).__name__)
                    finally:
                        with self._lock:
                            self._refreshing = False
                    if not self.stopped.is_set():
                        self.wakeup.wait(POLL_SECONDS)
        finally:
            with self._lock:
                self._inflight.clear()
                self._refreshing = False

    def snapshot(self, *, account_facts=None):
        from .account_migration_types import migration_type
        from .provider_catalog import AGENT_PROVIDERS
        health = self.scheduler.health()
        partitions = health.get("capacity_partitions") or {}
        with self._lock:
            samples = deepcopy(self._samples)
            refreshing = self._refreshing or self.wakeup.is_set() or any(not f.done() for f in self._inflight.values())
            retry_after = max(0, MIN_READ_INTERVAL_SECONDS - (time.time() - self._last_refresh_request)) if self._refresh_refused else 0
        from .account_registrations import registrations
        rows = registrations()
        entries = self.accounts.accounts()
        profiles = self._observation_store.profiles([a.account_id for a in entries])
        if not isinstance(profiles, dict):
            profiles = {}
        enabled = {r["account_id"]: r["enabled"] for r in rows}
        # Missing rows stay distinguishable from a recorded logged_out state:
        # the public registration view still owns legacy credential discovery.
        login_states = self._login_store.login_states({
            *(a.account_id for a in entries if _subscription(a)),
            *(r["account_id"] for r in rows if r["kind"] == "subscription_login"),
        })
        if account_facts is not None:
            account_facts.update(registrations=rows, login_states=login_states)
        items = []
        for account in entries:
            settings = partitions.get(account.capacity_key) or {}
            sample = samples.get(account.account_id) or {"status": "loading", "refreshing": True, "rate_limits": []}
            sample = {**sample, 'subscription': deepcopy(profiles.get(account.account_id) or {})}
            # Raw observations feed the scheduler. Its configured quota_reserve
            # is the single public interpretation of the account's threshold.
            sample.pop("quota", None)
            quota = settings.get("quota_reserve") or {}
            # A reading stays current until a read of it fails (refresh_account
            # then marks it stale); its age alone never expires it.
            items.append({**sample, "account_id": account.account_id, "provider": account.provider, "backend_route_id": account.route_id, "billing_account_id": account.billing_account_id,
                          "enabled": enabled.get(account.account_id, True),
                          "active_count": settings.get("active_count", 0), "queued_count": settings.get("queued_count", 0),
                          "login_state": login_states.get(account.account_id, "logged_out") if _subscription(account) else "not_applicable",
                          "max_concurrency": settings.get("limit", account.max_concurrency),
                          "allow_credits": settings.get("allow_credits", False),
                          "credits_supported": settings.get("credits_supported", False),
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
        try:
            self.observations.close()
        finally:
            if self.thread.is_alive():
                self.thread.join()  # IO owners cancel and reap; do not abandon pool workers.
