"""Quota protection: account quota samples, Close thresholds and the reserve they enforce."""
from __future__ import annotations

import time
from typing import Any

from .account_quota import close_threshold as account_close_threshold, quota_state
from .contracts import execution_timestamp
from .scheduler_capacity import ActiveRequests, SchedulerCapacity
from .scheduler_changes import SchedulerChanges
from .scheduler_rows import compact_json, json_object
from .account_session_credits import credits_available, override_allows


def _threshold_exempt_ids(connection, capacity_key: str) -> set[str]:
    row = connection.execute(
        'SELECT value FROM execution_store_meta WHERE key=%s',
        (f"quota_threshold_exempt:{capacity_key}",),
    ).fetchone()
    return {str(item) for item in (json_object(row["value"]).get("execution_ids") or [])} if row else set()


class SchedulerQuota:
    """Account quota states and the quota reserve policy."""

    def __init__(self, accounts: Any, *, changes: SchedulerChanges, capacity: SchedulerCapacity,
                 active_requests: ActiveRequests, quota_reserve_enabled: bool) -> None:
        self.accounts = accounts
        self.changes = changes
        self.capacity = capacity
        self.active_requests = active_requests
        self.quota_reserve_enabled = quota_reserve_enabled

    @property
    def quota_capacity_keys(self):
        return {account.capacity_key for account in self.accounts.accounts() if account.quota_enabled}

    def account_quota(self, connection, capacity_key: str) -> dict[str, Any]:
        states, _ = self.quota_states(connection, {capacity_key}, set(), now=time.time())
        return states[capacity_key]

    def quota_states(self, connection, capacity_keys, binding_ids, *, now):
        """Read quota samples, thresholds and overrides once for a scheduling pass."""
        keys = [prefix + key for key in capacity_keys for prefix in ("quota:", "quota_threshold:")]
        keys.extend("quota_override:" + binding for binding in binding_ids if binding)
        metadata = {row["key"]: json_object(row["value"]) for row in connection.execute(
            "SELECT key,value FROM execution_store_meta WHERE key=ANY(%s)", (keys,),
        ).fetchall()} if keys else {}
        accounts = {account.capacity_key: account for account in self.accounts.accounts()}
        states = {}
        for key in capacity_keys:
            sample = metadata.get("quota:" + key, {})
            threshold = account_close_threshold(accounts[key], sample, metadata.get("quota_threshold:" + key, {}))
            states[key] = quota_state(sample, now=now, threshold=threshold)
        overrides = {binding: metadata["quota_override:" + binding] for binding in binding_ids
                     if metadata.get("quota_override:" + binding, {}).get("enabled") is True}
        scoped = {binding: policy["credits_scope"] for binding, policy in overrides.items()
                  if "credits_scope" in policy}
        if scoped:
            from .agent_data_store import AgentDataStore
            refs = [binding for binding, scope in scoped.items() if "provider_session_ref" in scope]
            sessions = AgentDataStore().provider_binding_routes(refs) if refs else {}
            by_id = {account.account_id: account for account in accounts.values()}
            for binding, scope in scoped.items():
                account = by_id.get(scope["account_id"])
                sample = states.get(account.capacity_key, {}) if account else {}
                if (not credits_available(sample, now=now) or
                        ("provider_session_ref" in scope and
                         sessions.get(binding, {}).get("provider_session_ref") != scope["provider_session_ref"])):
                    overrides.pop(binding)
        return states, overrides

    def close_threshold(self, connection, capacity_key, sample=None):
        account = next(a for a in self.accounts.accounts() if a.capacity_key == capacity_key)
        if sample is None:
            row = connection.execute('SELECT value FROM execution_store_meta WHERE key=%s', (f"quota:{capacity_key}",)).fetchone()
            sample = json_object(row["value"]) if row else {}
        row = connection.execute('SELECT value FROM execution_store_meta WHERE key=%s', (f"quota_threshold:{capacity_key}",)).fetchone()
        return account_close_threshold(account, sample, json_object(row["value"]) if row else {})

    def quota_paused(self, connection, request: dict[str, Any]) -> bool:
        if not self.quota_reserve_enabled:
            return False
        capacity_key = self.capacity.capacity_key(request)
        if capacity_key not in self.quota_capacity_keys:
            return False
        binding_id = str((request.get("session") or {}).get("binding_id") or "")
        quotas, overrides = self.quota_states(connection, {capacity_key}, {binding_id}, now=time.time())
        account = self.accounts.for_request(request)
        return (quotas[capacity_key]["automatic_paused"] and not override_allows(
            overrides, binding_id, account.account_id if account else "", request.get("execution_id", "")))

    def exempt_running_from_threshold_change(self, connection, capacity_key: str) -> None:
        """Work already claimed or running when a Close threshold changes runs to completion.

        The new threshold applies to later dispatches; quota protection skips the
        executions recorded here (decision_d40f59f5508f).
        """
        running = {
            execution_id
            for execution_id, request in self.active_requests.scheduling_requests(connection, uncancelled=True)
            if self.capacity.capacity_key(request) == capacity_key
        }
        kept = _threshold_exempt_ids(connection, capacity_key) & {
            execution_id for execution_id, _request in self.active_requests.scheduling_requests(connection)
        }
        connection.execute(
            'INSERT INTO execution_store_meta(key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (f"quota_threshold_exempt:{capacity_key}", compact_json({"execution_ids": sorted(running | kept)})),
        )

    def enforce_quota_reserve(self, connection, capacity_key: str = "") -> None:
        if not self.quota_reserve_enabled:
            return
        active = self.active_requests.scheduling_requests(connection, uncancelled=True)
        requests = [request for _, request in active]
        keys = {self.capacity.capacity_key(request) for request in requests} & self.quota_capacity_keys
        bindings = {str((request.get("session") or {}).get("binding_id") or "") for request in requests}
        quotas, overrides = self.quota_states(connection, keys, bindings, now=time.time())
        exempt = {key: _threshold_exempt_ids(connection, key) for key in keys}
        account_ids = {account.capacity_key: account.account_id for account in self.accounts.accounts()}
        for execution_id, request in active:
            key = self.capacity.capacity_key(request)
            if key not in quotas or (capacity_key and key != capacity_key):
                continue
            binding = str((request.get("session") or {}).get("binding_id") or "")
            quota = quotas[key]
            # Work kept through a threshold change still stops once the quota is
            # used up; only a binding that a person opened keeps running.
            if ((execution_id in exempt[key] and not quota.get("used_up"))
                    or override_allows(overrides, binding, account_ids[key], execution_id)
                    or not quota["automatic_paused"] or quota["status"] != "reserved"):
                continue
            connection.execute(
                "UPDATE executions SET cancel_requested='1', updated_at=%s WHERE execution_id=%s",
                (execution_timestamp(), execution_id),
            )
            self.changes.event(connection, execution_id, "cancellation_requested", {
                "reason": "quota_reserve", "account_id": account_ids[key],
                "close_threshold": quota["close_threshold"], "reserve_percent": quota["reserve_percent"],
            })
