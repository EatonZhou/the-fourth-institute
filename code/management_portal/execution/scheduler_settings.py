"""Operator settings of the scheduler: quota overrides, account quota samples and capacity."""
from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any

from management_common.database_time import instant

from .account_quota import normalize_quota_override, quota_state, validate_threshold
from .contracts import BINDING_RE, execution_timestamp
from .scheduler_capacity import SchedulerCapacity, capacity_dispatch_state
from .scheduler_changes import SchedulerChanges
from .scheduler_quota import SchedulerQuota
from .scheduler_rows import compact_json, json_object


class SchedulerSettings:
    """Reads and writes of the settings that steer admission."""

    def __init__(self, accounts: Any, *, changes: SchedulerChanges, capacity: SchedulerCapacity,
                 quota: SchedulerQuota) -> None:
        self.accounts = accounts
        self.changes = changes
        self.capacity = capacity
        self.quota = quota

    def binding_quota_states(self, bindings: Mapping[str, str]) -> dict[str, Any]:
        """Read dispatch protection for visible caller-resolved bindings, without work.

        This is not admission or an intake mutation. Use the same durable quota
        samples and acknowledged overrides as claim(), including idle bindings.
        """
        if not isinstance(bindings, Mapping) or len(bindings) > 500:
            raise ValueError("bindings must be a mapping of at most 500 entries")
        if any(not isinstance(binding, str) or not BINDING_RE.fullmatch(binding)
               or not isinstance(account, str) or not account for binding, account in bindings.items()):
            raise ValueError("valid binding IDs and account IDs required")
        if not bindings:
            return {}
        accounts = {account.account_id: account for account in self.accounts.accounts()}
        known = {binding: accounts[account] for binding, account in bindings.items() if account in accounts}
        keys = {a.capacity_key for a in known.values()} & self.quota.quota_capacity_keys if self.quota.quota_reserve_enabled else set()
        from .account_registrations import registrations
        disabled = {row['account_id'] for row in registrations() if not row['enabled']}
        now = time.time()
        with self.changes.connect() as connection:
            quotas, overrides = self.quota.quota_states(connection, keys, set(known), now=now)
        result = {}
        for binding, account_id in bindings.items():
            account = known.get(binding)
            quota = quotas.get(account.capacity_key, {}) if account else {}
            override = binding in overrides
            state, reason = 'open', ''
            if not account:
                state, reason = 'unknown', 'account_unavailable'
            elif account_id in disabled:
                state, reason = 'closed', 'account_disabled'
            elif quota.get('automatic_paused') and not override:
                state = 'closed'
                reason = ('quota_recovery_unconfirmed' if quota.get('status') == 'reserved' and not quota.get('fresh')
                          else 'quota_used_up' if quota.get('used_up')
                          else 'quota_reserved' if quota.get('status') == 'reserved' else 'quota_unknown')
            result[binding] = {'state': state, 'reason': reason, 'account_id': account_id,
                               'override_enabled': override, 'quota_status': quota.get('status', ''),
                               'used_up': bool(quota.get('used_up')), 'sample_fresh': bool(quota.get('fresh')),
                               'observed_at': quota.get('observed_at'), 'sampled_at': now}
        return result

    def configure_binding_quota_overrides(self, policies: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(policies, list):
            raise ValueError("quota policies must be a list")
        normalized = {}
        for policy in policies:
            policy = normalize_quota_override(policy)
            binding_id = policy["binding_id"]
            if binding_id in normalized:
                raise ValueError("quota policy requires a unique valid binding_id")
            normalized[binding_id] = policy
        updated = 0
        with self.changes.connect() as connection:
            for binding_id, policy in normalized.items():
                key = f"quota_override:{binding_id}"
                row = connection.execute('SELECT value FROM execution_store_meta WHERE key=%s FOR UPDATE', (key,)).fetchone()
                previous = json_object(row["value"]) if row else {}
                previous_revision = int(previous.get("revision") or 0)
                if previous_revision > policy["revision"]:
                    continue
                if previous_revision == policy["revision"]:
                    if previous != policy:
                        raise ValueError("quota policy revision conflict")
                    continue
                connection.execute(
                    'INSERT INTO execution_store_meta(key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (key, compact_json(policy)),
                )
                updated += 1
            if updated:
                self.quota.enforce_quota_reserve(connection)
            connection.commit()
        if updated:
            self.changes.notify("execution", "control")
        return {"updated": updated}

    def forget_binding_quota_overrides(self, binding_ids: list[str]) -> dict[str, Any]:
        """Drop the overrides of bindings whose Agent was deleted."""

        if not isinstance(binding_ids, list) or not all(
            isinstance(binding_id, str) and BINDING_RE.fullmatch(binding_id) for binding_id in binding_ids
        ):
            raise ValueError("quota override bindings must be valid binding_ids")
        with self.changes.connect() as connection:
            removed = connection.execute(
                'DELETE FROM execution_store_meta WHERE key = ANY(%s)',
                ([f"quota_override:{binding_id}" for binding_id in binding_ids],),
            ).rowcount
            connection.commit()
        return {"removed": removed}

    def update_account_quota(self, account_id: str, sample: dict[str, Any]) -> None:
        capacity_key = self.accounts.get(account_id).capacity_key
        with self.changes.connect() as connection:
            previous = self.quota.account_quota(connection, capacity_key)
            incoming = sample.get("status", "unknown")
            sample = quota_state(sample, now=time.time(), threshold=self.quota.close_threshold(connection, capacity_key, sample))
            # An unreadable sample never releases a hold, also when a zero
            # threshold would report it as available.
            if previous["status"] == "reserved" and "unknown" in (incoming, sample.get("status")):
                sample = previous
            connection.execute(
                'INSERT INTO execution_store_meta(key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (f"quota:{capacity_key}", compact_json(sample)),
            )
            if sample.get("status") == "reserved":
                self.quota.enforce_quota_reserve(connection, capacity_key)
            connection.commit()
        self.changes.notify("execution", "control")

    def configure_capacity(
        self,
        capacity_key: str,
        limit: int,
        dispatch_interval_min_seconds: float,
        dispatch_interval_max_seconds: float,
        *,
        actor: str,
        close_threshold: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_key = str(capacity_key or "").strip()
        if not clean_key:
            raise ValueError("capacity_key is required")
        if type(limit) is not int:
            raise ValueError("max_concurrency must be an integer")
        clean_limit = limit
        if clean_limit < 1 or clean_limit > 256:
            raise ValueError("max_concurrency must be between 1 and 256")
        interval_min = float(dispatch_interval_min_seconds)
        interval_max = float(dispatch_interval_max_seconds)
        if not math.isfinite(interval_min) or not math.isfinite(interval_max):
            raise ValueError("dispatch interval must be finite")
        if interval_min < 0 or interval_max > 3600:
            raise ValueError("dispatch interval must be between 0 and 3600 seconds")
        if interval_min > interval_max:
            raise ValueError("dispatch interval minimum cannot exceed maximum")
        clean_actor = str(actor or "").strip()
        if not clean_actor:
            raise ValueError("actor is required")
        updated_at = execution_timestamp()
        with self.changes.connect() as connection:
            threshold = self.quota.close_threshold(connection, clean_key) if clean_key in self.quota.quota_capacity_keys else None
            if close_threshold is not None:
                if threshold is None:
                    raise ValueError("This account does not provide a Close metric")
                previous_threshold = threshold
                threshold = validate_threshold(close_threshold, threshold)
                connection.execute(
                    'INSERT INTO execution_store_meta(key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (f"quota_threshold:{clean_key}", compact_json({**threshold, "updated_by": clean_actor, "updated_at": updated_at})),
                )
                state = self.quota.account_quota(connection, clean_key)
                connection.execute(
                    'INSERT INTO execution_store_meta(key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (f"quota:{clean_key}", compact_json(state)),
                )
                if any(threshold.get(key) != previous_threshold.get(key) for key in ("value", "unit")):
                    self.quota.exempt_running_from_threshold_change(connection, clean_key)
            connection.execute(
                '\n                INSERT INTO capacity_settings (\n                    capacity_key, limit_value,\n                    dispatch_interval_min_seconds,\n                    dispatch_interval_max_seconds,\n                    updated_by, updated_at\n                ) VALUES (%s, %s, %s, %s, %s, %s)\n                ON CONFLICT(capacity_key) DO UPDATE SET\n                    limit_value = excluded.limit_value,\n                    dispatch_interval_min_seconds =\n                        excluded.dispatch_interval_min_seconds,\n                    dispatch_interval_max_seconds =\n                        excluded.dispatch_interval_max_seconds,\n                    updated_by = excluded.updated_by,\n                    updated_at = excluded.updated_at\n                ',
                (
                    clean_key,
                    clean_limit,
                    interval_min,
                    interval_max,
                    clean_actor,
                    updated_at,
                ),
            )
            dispatch_state = capacity_dispatch_state(
                connection,
                clean_key,
            )
            previous_interval = dispatch_state["interval_seconds"]
            if (
                dispatch_state["last_dispatched_at"] > 0
                and not interval_min <= previous_interval <= interval_max
            ):
                interval_seconds = self.capacity.next_dispatch_interval_seconds(
                    connection, clean_key,
                )
                connection.execute(
                    '\n                    UPDATE capacity_dispatch_state\n                    SET next_dispatch_at = %s, interval_seconds = %s,\n                        updated_at = %s\n                    WHERE capacity_key = %s\n                    ',
                    (
                        instant(dispatch_state["last_dispatched_at"]
                        + interval_seconds),
                        interval_seconds,
                        updated_at,
                        clean_key,
                    ),
                )
        self.changes.notify("execution", "control")
        return {
            "capacity_key": clean_key,
            "close_threshold": threshold,
            "limit": clean_limit,
            "dispatch_interval_min_seconds": interval_min,
            "dispatch_interval_max_seconds": interval_max,
            "updated_by": clean_actor,
            "updated_at": updated_at,
        }
