from __future__ import annotations
from management_common.site_compatibility import compatibility_value

from management_common.format_tags import schema_matches

from .contracts import TOKEN_USAGE_KEYS, execution_timestamp

import json
from dataclasses import replace
import logging
import math
import random
import re
import secrets
import threading
import time
from collections.abc import Mapping
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from management_common.principal import OperationIdentity, actor_principal

from management_common.database_time import instant, iso_time, epoch_seconds, decode_times
from management_common.json_columns import json_text, json_value
from management_common.portal_database import portal_database

from .contracts import (
    BINDING_RE,
    EXECUTION_TURN_SETTINGS_CONTROL_SCHEMA,
    ExecutionInputControl,
    ExecutionTurnSettingsControl,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSnapshot,
    TERMINAL_STATUSES,
)
from .transport import CONTROL_REQUEST_TIMEOUT_SECONDS, ExecutionServiceClient
from .provider_catalog import AGENT_PROVIDERS
from .account_quota import close_threshold as account_close_threshold, validate_threshold, quota_state, normalize_quota_override


SCHEDULER_SCHEMA = "tfi.execution-scheduler.v10"
EXECUTION_STORE_SCHEMA_VERSION = "19"
ACTIVE_STATUSES = ("claimed", "running")
INPUT_CONTROL_ACCEPTING_STATUSES = ("queued", *ACTIVE_STATUSES)
SHARED_CAPACITY_KEY = "shared-ai"
SCHEDULING_MEMO_LIMIT = 4096


def _scheduling_memo_key(row: Any) -> tuple[str, str, int]:
    return (str(row["row_version"]), (iso_time(row["updated_at"]) or ""), int(row["request_size"] or 0))


def _scheduling_request(request: dict[str, Any]) -> dict[str, Any]:
    """The request fields capacity, quota and binding checks read.

    request_json also carries the whole prompt and context, often hundreds of
    KB; scheduling needs only the route and binding.
    """

    session = request.get("session") if isinstance(request.get("session"), dict) else {}
    slim: dict[str, Any] = {
        key: request[key]
        for key in ("service_type", "operation", "provider")
        if key in request
    }
    if isinstance(request.get("runtime_options"), dict):
        slim["runtime_options"] = dict(request["runtime_options"])
    slim["session"] = {
        key: session[key]
        for key in ("backend_route_id", "binding_id")
        if key in session
    }
    return slim
SESSION_CONTROL_CAPACITY_PREFIX = "session-control:"
TRANSIENT_EVENTS_PER_EXECUTION = 256
TOKEN_USAGE_SCHEMA = "tfi.execution-token-usage.v1"
# A terminal result nobody acknowledged within a week has no caller left waiting for it.
UNACKNOWLEDGED_TERMINAL_RETENTION_SECONDS = 7 * 24 * 60 * 60
RESIDENT_ID_RE = re.compile(r"^resident_[0-9a-f]{12}$")
DEFAULT_WORKER_HEARTBEAT_TIMEOUT_SECONDS = 180.0
SHARED_SCHEDULER_POLL_SECONDS = 5.0
SCHEDULER_CHANGE_CHANNEL = compatibility_value("execution_scheduler_change_channel", "tfi_execution_scheduler_changed")
# pg_trigger.tgtype of a FOR EACH ROW AFTER INSERT OR UPDATE OR DELETE trigger.
SCHEDULER_TRIGGER_TYPE = 1 | 4 | 8 | 16




def _json(value: object) -> str:
    return json_text(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _turn_kept_running(checkpoint: dict[str, Any]) -> bool:
    """Whether an unfinished Provider turn may outlive its Worker."""
    from .provider_catalog import AGENT_PROVIDERS
    provider = AGENT_PROVIDERS.get(str(checkpoint.get("provider") or ""))
    return bool(provider and provider.handover_keeps_turn and checkpoint.get("turn_id")
                and not checkpoint.get("provider_completed")
                and not isinstance(checkpoint.get("abandoned"), dict))


def _object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _nonnegative_int(value: object) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _first_usage_value(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = _nonnegative_int(payload.get(key))
        if value:
            return value
    return 0


def normalize_token_usage(value: object) -> dict[str, int]:
    """Normalize one provider turn without retaining provider-specific payloads."""

    payload = value if isinstance(value, dict) else {}
    data = payload.get("last") if isinstance(payload.get("last"), dict) else payload
    input_details = next(
        (
            data.get(key)
            for key in ("input_tokens_details", "prompt_tokens_details")
            if isinstance(data.get(key), dict)
        ),
        {},
    )
    output_details = next(
        (
            data.get(key)
            for key in ("output_tokens_details", "completion_tokens_details")
            if isinstance(data.get(key), dict)
        ),
        {},
    )
    usage = {
        "input_tokens": _first_usage_value(
            data,
            "input_tokens",
            "inputTokens",
            "prompt_tokens",
            "promptTokens",
        ),
        "cached_input_tokens": _first_usage_value(
            data,
            "cached_input_tokens",
            "cachedInputTokens",
            "prompt_cache_hit_tokens",
        )
        or _first_usage_value(input_details, "cached_tokens", "cachedTokens"),
        "output_tokens": _first_usage_value(
            data,
            "output_tokens",
            "outputTokens",
            "completion_tokens",
            "completionTokens",
        ),
        "reasoning_output_tokens": _first_usage_value(
            data,
            "reasoning_output_tokens",
            "reasoningOutputTokens",
        )
        or _first_usage_value(
            output_details,
            "reasoning_tokens",
            "reasoningTokens",
            # Anthropic reports the thinking tokens inside the output count.
            "thinking_tokens",
        ),
        "total_tokens": _first_usage_value(
            data,
            "total_tokens",
            "totalTokens",
        ),
    }
    # Anthropic reports uncached input, cache writes and cache reads separately.
    # The canonical input count includes all three; only reads are cache hits.
    # Native fields are discarded below, keeping normalization idempotent.
    cache_read = _first_usage_value(data, "cache_read_input_tokens")
    cache_write = _first_usage_value(data, "cache_creation_input_tokens")
    usage["input_tokens"] += cache_read + cache_write
    if cache_read:
        usage["cached_input_tokens"] = cache_read
    if not usage["total_tokens"]:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return {key: usage[key] for key in TOKEN_USAGE_KEYS if usage[key] > 0}


def _usage_bucket_hour(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("token usage timestamp is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (
        parsed.astimezone(timezone.utc)
        .replace(minute=0, second=0, microsecond=0)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _usage_resident_id(request: dict[str, Any]) -> str:
    """The Resident that started the call (``metadata.resident_id``); others are counted together."""
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    resident_id = str(metadata.get("resident_id") or "").strip().lower()
    return resident_id if RESIDENT_ID_RE.fullmatch(resident_id) else "others"


class _SchedulerTransaction:
    """A scheduler connection whose events reach this process only once committed.

    Readers and waiters do not hold the scheduler lock while a transaction
    runs, so an event shown before its commit could appear next to the old row.
    """

    def __init__(self, connection: Any, publish: Callable[[list[dict[str, Any]]], None]) -> None:
        self._connection = connection
        self._publish = publish
        self.pending_events: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def commit(self) -> None:
        self._connection.commit()
        events, self.pending_events = self.pending_events, []
        self._publish(events)

    def rollback(self) -> None:
        self._connection.rollback()
        self.pending_events = []


class Scheduler:
    """Persistent control-plane state and scheduling policy."""

    @property
    def quota_capacity_keys(self):
        return {account.capacity_key for account in self.accounts.accounts() if account.quota_enabled}

    @property
    def _default_capacity_limits(self):
        return {**{account.capacity_key: account.max_concurrency for account in self.accounts.accounts()},
                **self._capacity_limit_overrides}

    def __init__(
        self,
        *,
        max_concurrency: int = 8,
        capacity_limits: dict[str, int] | None = None,
        default_lease_seconds: int = 270,
        worker_heartbeat_timeout_seconds: float = (
            DEFAULT_WORKER_HEARTBEAT_TIMEOUT_SECONDS
        ),
        dispatch_interval_min_seconds: float = 2.0,
        dispatch_interval_max_seconds: float = 6.0,
        quota_reserve_enabled: bool = False,
        accounts=None,
    ) -> None:
        from .accounts import default_execution_accounts
        self.accounts = accounts or default_execution_accounts()
        self.quota_reserve_enabled = quota_reserve_enabled
        self.database = portal_database(schema="execution_runtime")
        self.max_concurrency = max(1, int(max_concurrency))
        self._capacity_limit_overrides = {
            str(key or "").strip(): max(1, int(limit))
            for key, limit in (capacity_limits or {}).items()
            if str(key or "").strip()
        }
        self.default_lease_seconds = max(15, int(default_lease_seconds))
        self.worker_heartbeat_timeout_seconds = max(
            1.0,
            float(worker_heartbeat_timeout_seconds),
        )
        self.dispatch_interval_min_seconds = max(
            0.0,
            float(dispatch_interval_min_seconds),
        )
        self.dispatch_interval_max_seconds = max(
            self.dispatch_interval_min_seconds,
            float(dispatch_interval_max_seconds),
        )
        # Guards only this process's memory (event cursor, transient events,
        # wake-ups). Transactions never hold it: row updates keep each
        # execution consistent, and capacity is admitted without a lock
        # because a briefly exceeded limit is acceptable.
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._claim_changed = threading.Condition(self._lock)
        self._worker_control_changed = threading.Condition(self._lock)
        self._change_generations = {"execution": 0, "claim": 0, "control": 0}
        self._listener_guard = threading.Lock()
        self._scheduling_memo_lock = threading.Lock()
        self._scheduling_memo: dict[str, tuple[tuple[str, str, int], dict[str, Any]]] = {}
        self._listener_ready = threading.Event()
        self._listener_thread: threading.Thread | None = None
        self._listener_waiters = 0
        self._listener_idle_since = 0.0
        # Event cursors survive service replacement.  Epoch microseconds stay
        # within JSON's exact integer range while advancing across processes.
        self._event_sequence = time.time_ns() // 1_000
        self._transient_event_items: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=TRANSIENT_EVENTS_PER_EXECUTION)
        )
        self._last_maintenance_result = {
            "expired_worker_registrations": 0,
            "recovered_expired_leases": 0,
        }
        self._initialize()

    def _capacity_key(self, request: dict[str, Any]) -> str:
        if request.get("service_type") == "agent_session" and request.get("operation") == "release":
            return f"{SESSION_CONTROL_CAPACITY_PREFIX}{request.get('provider', '')}"
        account = self.accounts.for_request(request)
        return account.capacity_key if account else SHARED_CAPACITY_KEY

    @staticmethod
    def _supports_active_turn_input(request: dict[str, Any]) -> bool:
        from .provider_catalog import AGENT_PROVIDERS
        adapter = AGENT_PROVIDERS.get(str(request.get("provider") or ""))
        return bool(adapter and adapter.active_turn_input)

    def _capacity_settings(self, connection) -> dict[str, dict[str, Any]]:
        return {str(row["capacity_key"]): dict(row) for row in connection.execute(
            "SELECT * FROM capacity_settings"
        ).fetchall()}

    def _capacity_limit(self, connection, capacity_key: str) -> int:
        settings = self._capacity_settings(connection).get(capacity_key)
        return int(settings["limit_value"]) if settings else self._default_capacity_limits.get(capacity_key, self.max_concurrency)
    def _account_quota(self, connection, capacity_key: str) -> dict[str, Any]:
        states, _ = self._quota_states(connection, {capacity_key}, set(), now=time.time())
        return states[capacity_key]

    def _quota_states(self, connection, capacity_keys, binding_ids, *, now):
        """Read quota samples, thresholds and overrides once for a scheduling pass."""
        keys = [prefix + key for key in capacity_keys for prefix in ("quota:", "quota_threshold:")]
        keys.extend("quota_override:" + binding for binding in binding_ids if binding)
        metadata = {row["key"]: _object(row["value"]) for row in connection.execute(
            "SELECT key,value FROM execution_store_meta WHERE key=ANY(%s)", (keys,),
        ).fetchall()} if keys else {}
        accounts = {account.capacity_key: account for account in self.accounts.accounts()}
        states = {}
        for key in capacity_keys:
            sample = metadata.get("quota:" + key, {})
            threshold = account_close_threshold(accounts[key], sample, metadata.get("quota_threshold:" + key, {}))
            states[key] = quota_state(sample, now=now, threshold=threshold)
        overrides = {binding for binding in binding_ids if metadata.get("quota_override:" + binding, {}).get("enabled") is True}
        return states, overrides

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
        keys = {a.capacity_key for a in known.values()} & self.quota_capacity_keys if self.quota_reserve_enabled else set()
        from .account_registrations import registrations
        disabled = {row['account_id'] for row in registrations() if not row['enabled']}
        now = time.time()
        with self._connect() as connection:
            quotas, overrides = self._quota_states(connection, keys, set(known), now=now)
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

    def _admission_snapshot(self, connection, requests, *, now=None):
        """Shared inputs for claim, a detailed queue view and batched states."""
        now = time.time() if now is None else now
        active_counts = defaultdict(int)
        active_bindings = set()
        for _, request in self._active_scheduling_requests(connection):
            active_counts[self._capacity_key(request)] += 1
            binding = str((request.get("session") or {}).get("binding_id") or "")
            if binding:
                active_bindings.add(binding)
        capacities = {self._capacity_key(request) for request in requests}
        bindings = {str((request.get("session") or {}).get("binding_id") or "") for request in requests}
        settings = self._capacity_settings(connection)
        defaults = self._default_capacity_limits
        limits = {key: int(settings[key]["limit_value"]) if key in settings else defaults.get(key, self.max_concurrency) for key in capacities}
        dispatch = {row["capacity_key"]: {name: float(row[name]) if name == "interval_seconds" else epoch_seconds(row[name]) for name in ("last_dispatched_at", "next_dispatch_at", "interval_seconds")} for row in connection.execute(
            "SELECT * FROM capacity_dispatch_state WHERE capacity_key=ANY(%s)", (list(capacities),),
        ).fetchall()}
        quota_keys = capacities & self.quota_capacity_keys if self.quota_reserve_enabled else set()
        quotas, overrides = self._quota_states(connection, quota_keys, bindings, now=now)
        from .account_registrations import registrations
        disabled = {row["account_id"] for row in registrations() if not row["enabled"]}
        return {"now": now, "active_counts": active_counts, "active_bindings": active_bindings,
                "limits": limits, "dispatch": dispatch, "quotas": quotas, "overrides": overrides,
                "disabled_accounts": disabled}

    def _admission_reason(self, request, available_at, interrupt_only, admission):
        account = self.accounts.for_request(request)
        if not interrupt_only and account and account.account_id in admission.get("disabled_accounts", set()):
            return "account_disabled"
        capacity = self._capacity_key(request)
        binding = str((request.get("session") or {}).get("binding_id") or "")
        paused = admission["quotas"].get(capacity, {}).get("automatic_paused", False)
        return self._queue_reason(
            paused=not interrupt_only and paused and binding not in admission["overrides"],
            deferred=float(available_at) > admission["now"],
            capacity=interrupt_only or admission["limits"][capacity] > admission["active_counts"][capacity],
            dispatch_wait=not interrupt_only and admission["dispatch"].get(capacity, {}).get("next_dispatch_at", 0) > admission["now"],
            binding_busy=binding in admission["active_bindings"],
        )

    def _close_threshold(self, connection, capacity_key, sample=None):
        account = next(a for a in self.accounts.accounts() if a.capacity_key == capacity_key)
        if sample is None:
            row = connection.execute('SELECT value FROM execution_store_meta WHERE key=%s', (f"quota:{capacity_key}",)).fetchone()
            sample = _object(row["value"]) if row else {}
        row = connection.execute('SELECT value FROM execution_store_meta WHERE key=%s', (f"quota_threshold:{capacity_key}",)).fetchone()
        return account_close_threshold(account, sample, _object(row["value"]) if row else {})

    def _quota_paused(self, connection, request: dict[str, Any]) -> bool:
        if not self.quota_reserve_enabled:
            return False
        capacity_key = self._capacity_key(request)
        if capacity_key not in self.quota_capacity_keys:
            return False
        binding_id = str((request.get("session") or {}).get("binding_id") or "")
        if binding_id:
            row = connection.execute(
                'SELECT value FROM execution_store_meta WHERE key = %s',
                (f"quota_override:{binding_id}",),
            ).fetchone()
            if row and _object(row["value"]).get("enabled") is True:
                return False
        return self._account_quota(connection, capacity_key)["automatic_paused"]

    def _active_scheduling_requests(
        self,
        connection,
        *,
        exclude: str = "",
        uncancelled: bool = False,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Scheduling fields of every claimed or running execution.

        Claims and queue positions read these on every poll.  Parsing each
        active request_json there cost about a CPU core in both ExecutionService
        and PostgreSQL even while idle.  The fields are kept per execution and
        re-read only when (row version, updated_at, request size) changes: every
        UPDATE gives the row a new xmin, even within the millisecond updated_at
        resolves, and pg_column_size reads the stored size without
        decompressing the value.
        """

        sql = (
            "SELECT execution_id, xmin::text AS row_version, updated_at, "
            "pg_column_size(request_json) AS request_size "
            "FROM executions WHERE status IN ('claimed', 'running')"
        )
        params: list[Any] = []
        if uncancelled:
            sql += " AND cancel_requested = '0'"
        if exclude:
            sql += " AND execution_id != %s"
            params.append(exclude)
        keys = {
            str(row["execution_id"]): _scheduling_memo_key(row)
            for row in connection.execute(sql, tuple(params)).fetchall()
        }
        with self._scheduling_memo_lock:
            known = {execution_id: self._scheduling_memo.get(execution_id) for execution_id in keys}
        requests = {
            execution_id: entry[1]
            for execution_id, entry in known.items()
            if entry is not None and entry[0] == keys[execution_id]
        }
        missing = [execution_id for execution_id in keys if execution_id not in requests]
        fresh: dict[str, tuple[tuple[str, int], dict[str, Any]]] = {}
        if missing:
            for row in connection.execute(
                "SELECT execution_id, xmin::text AS row_version, updated_at, "
                "pg_column_size(request_json) AS request_size, request_json "
                "FROM executions WHERE execution_id = ANY(%s)",
                (missing,),
            ).fetchall():
                execution_id = str(row["execution_id"])
                request = _scheduling_request(_object(row["request_json"]))
                fresh[execution_id] = (_scheduling_memo_key(row), request)
                requests[execution_id] = request
        with self._scheduling_memo_lock:
            self._scheduling_memo.update(fresh)
            if not exclude and not uncancelled:
                for execution_id in [key for key in self._scheduling_memo if key not in keys]:
                    del self._scheduling_memo[execution_id]
            elif len(self._scheduling_memo) > SCHEDULING_MEMO_LIMIT:
                self._scheduling_memo.clear()
        return [(execution_id, requests[execution_id]) for execution_id in keys if execution_id in requests]

    def _threshold_exempt_ids(self, connection, capacity_key: str) -> set[str]:
        row = connection.execute(
            'SELECT value FROM execution_store_meta WHERE key=%s',
            (f"quota_threshold_exempt:{capacity_key}",),
        ).fetchone()
        return {str(item) for item in (_object(row["value"]).get("execution_ids") or [])} if row else set()

    def _exempt_running_from_threshold_change(self, connection, capacity_key: str) -> None:
        """Work already claimed or running when a Close threshold changes runs to completion.

        The new threshold applies to later dispatches; quota protection skips the
        executions recorded here (decision_d40f59f5508f).
        """
        running = {
            execution_id
            for execution_id, request in self._active_scheduling_requests(connection, uncancelled=True)
            if self._capacity_key(request) == capacity_key
        }
        kept = self._threshold_exempt_ids(connection, capacity_key) & {
            execution_id for execution_id, _request in self._active_scheduling_requests(connection)
        }
        connection.execute(
            'INSERT INTO execution_store_meta(key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (f"quota_threshold_exempt:{capacity_key}", _json({"execution_ids": sorted(running | kept)})),
        )

    def _enforce_quota_reserve(self, connection, capacity_key: str = "") -> None:
        if not self.quota_reserve_enabled:
            return
        active = self._active_scheduling_requests(connection, uncancelled=True)
        requests = [request for _, request in active]
        keys = {self._capacity_key(request) for request in requests} & self.quota_capacity_keys
        bindings = {str((request.get("session") or {}).get("binding_id") or "") for request in requests}
        quotas, overrides = self._quota_states(connection, keys, bindings, now=time.time())
        exempt = {key: self._threshold_exempt_ids(connection, key) for key in keys}
        account_ids = {account.capacity_key: account.account_id for account in self.accounts.accounts()}
        for execution_id, request in active:
            key = self._capacity_key(request)
            if key not in quotas or (capacity_key and key != capacity_key):
                continue
            binding = str((request.get("session") or {}).get("binding_id") or "")
            quota = quotas[key]
            # Work kept through a threshold change still stops once the quota is
            # used up; only a binding that a person opened keeps running.
            if (execution_id in exempt[key] and not quota.get("used_up")) or binding in overrides or not quota["automatic_paused"] or quota["status"] != "reserved":
                continue
            connection.execute(
                "UPDATE executions SET cancel_requested='1', updated_at=%s WHERE execution_id=%s",
                (execution_timestamp(), execution_id),
            )
            self._event(connection, execution_id, "cancellation_requested", {
                "reason": "quota_reserve", "account_id": account_ids[key],
                "close_threshold": quota["close_threshold"], "reserve_percent": quota["reserve_percent"],
            })

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
        with self._connect() as connection:
            for binding_id, policy in normalized.items():
                key = f"quota_override:{binding_id}"
                row = connection.execute('SELECT value FROM execution_store_meta WHERE key=%s FOR UPDATE', (key,)).fetchone()
                previous = _object(row["value"]) if row else {}
                previous_revision = int(previous.get("revision") or 0)
                if previous_revision > policy["revision"]:
                    continue
                if previous_revision == policy["revision"]:
                    if previous != policy:
                        raise ValueError("quota policy revision conflict")
                    continue
                connection.execute(
                    'INSERT INTO execution_store_meta(key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (key, _json(policy)),
                )
                updated += 1
            if updated:
                self._enforce_quota_reserve(connection)
            connection.commit()
        if updated:
            self._notify("execution", "control")
        return {"updated": updated}

    def forget_binding_quota_overrides(self, binding_ids: list[str]) -> dict[str, Any]:
        """Drop the overrides of bindings whose Agent was deleted."""

        if not isinstance(binding_ids, list) or not all(
            isinstance(binding_id, str) and BINDING_RE.fullmatch(binding_id) for binding_id in binding_ids
        ):
            raise ValueError("quota override bindings must be valid binding_ids")
        with self._connect() as connection:
            removed = connection.execute(
                'DELETE FROM execution_store_meta WHERE key = ANY(%s)',
                ([f"quota_override:{binding_id}" for binding_id in binding_ids],),
            ).rowcount
            connection.commit()
        return {"removed": removed}

    def update_account_quota(self, account_id: str, sample: dict[str, Any]) -> None:
        capacity_key = self.accounts.get(account_id).capacity_key
        with self._connect() as connection:
            previous = self._account_quota(connection, capacity_key)
            incoming = sample.get("status", "unknown")
            sample = quota_state(sample, now=time.time(), threshold=self._close_threshold(connection, capacity_key, sample))
            # An unreadable sample never releases a hold, also when a zero
            # threshold would report it as available.
            if previous["status"] == "reserved" and "unknown" in (incoming, sample.get("status")):
                sample = previous
            connection.execute(
                'INSERT INTO execution_store_meta(key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (f"quota:{capacity_key}", _json(sample)),
            )
            if sample.get("status") == "reserved":
                self._enforce_quota_reserve(connection, capacity_key)
            connection.commit()
        self._notify("execution", "control")

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
        with self._connect() as connection:
            threshold = self._close_threshold(connection, clean_key) if clean_key in self.quota_capacity_keys else None
            if close_threshold is not None:
                if threshold is None:
                    raise ValueError("This account does not provide a Close metric")
                previous_threshold = threshold
                threshold = validate_threshold(close_threshold, threshold)
                connection.execute(
                    'INSERT INTO execution_store_meta(key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (f"quota_threshold:{clean_key}", _json({**threshold, "updated_by": clean_actor, "updated_at": updated_at})),
                )
                state = self._account_quota(connection, clean_key)
                connection.execute(
                    'INSERT INTO execution_store_meta(key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (f"quota:{clean_key}", _json(state)),
                )
                if any(threshold.get(key) != previous_threshold.get(key) for key in ("value", "unit")):
                    self._exempt_running_from_threshold_change(connection, clean_key)
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
            dispatch_state = self._capacity_dispatch_state(
                connection,
                clean_key,
            )
            previous_interval = dispatch_state["interval_seconds"]
            if (
                dispatch_state["last_dispatched_at"] > 0
                and not interval_min <= previous_interval <= interval_max
            ):
                interval_seconds = self._next_dispatch_interval_seconds(
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
        self._notify("execution", "control")
        return {
            "capacity_key": clean_key,
            "close_threshold": threshold,
            "limit": clean_limit,
            "dispatch_interval_min_seconds": interval_min,
            "dispatch_interval_max_seconds": interval_max,
            "updated_by": clean_actor,
            "updated_at": updated_at,
        }

    def _dispatch_interval_range(self, connection, capacity_key: str) -> tuple[float, float]:
        if capacity_key.startswith(SESSION_CONTROL_CAPACITY_PREFIX):
            return (0.0, 0.0)
        settings = self._capacity_settings(connection).get(capacity_key)
        if settings and settings["dispatch_interval_min_seconds"] is not None and settings["dispatch_interval_max_seconds"] is not None:
            return (float(settings["dispatch_interval_min_seconds"]), float(settings["dispatch_interval_max_seconds"]))
        return (self.dispatch_interval_min_seconds, self.dispatch_interval_max_seconds)
    def _next_dispatch_interval_seconds(self, connection, capacity_key: str) -> float:
        lower, upper = self._dispatch_interval_range(connection, capacity_key)
        if upper <= 0:
            return 0.0
        if upper == lower:
            return lower
        return random.uniform(lower, upper)

    @staticmethod
    def _capacity_dispatch_state(
        connection: Any,
        capacity_key: str,
    ) -> dict[str, float]:
        row = connection.execute(
            '\n            SELECT last_dispatched_at, next_dispatch_at, interval_seconds\n            FROM capacity_dispatch_state\n            WHERE capacity_key = %s\n            ',
            (capacity_key,),
        ).fetchone()
        if row is None:
            return {
                "last_dispatched_at": 0.0,
                "next_dispatch_at": 0.0,
                "interval_seconds": 0.0,
            }
        return {
            "last_dispatched_at": (epoch_seconds(row["last_dispatched_at"]) or 0.0),
            "next_dispatch_at": (epoch_seconds(row["next_dispatch_at"]) or 0.0),
            "interval_seconds": float(row["interval_seconds"]),
        }

    def _reserve_capacity_dispatch(
        self,
        connection: Any,
        capacity_key: str,
        *,
        now_epoch: float,
    ) -> dict[str, float]:
        interval_seconds = self._next_dispatch_interval_seconds(connection, capacity_key)
        next_dispatch_at = now_epoch + interval_seconds
        self._record_dispatch_row(
            connection, "capacity_dispatch_state", {"capacity_key": capacity_key}, {
                "last_dispatched_at": instant(now_epoch),
                "next_dispatch_at": instant(next_dispatch_at),
                "interval_seconds": interval_seconds,
                "updated_at": execution_timestamp(),
            },
        )
        return {
            "last_dispatched_at": now_epoch,
            "next_dispatch_at": next_dispatch_at,
            "interval_seconds": interval_seconds,
        }

    @staticmethod
    def _fair_queue_order(
        connection: Any,
        rows: list[Any],
    ) -> list[Any]:
        """Round-robin queued work across actors while preserving priority/FIFO."""
        if not rows:
            return []
        last_dispatch = {
            (int(row["priority"]), str(row["actor"])): (epoch_seconds(row["last_dispatched_at"]) or 0.0)
            for row in connection.execute(
                """
                SELECT priority, actor, last_dispatched_at
                FROM execution_actor_dispatch_state
                """
            ).fetchall()
        }
        pending: dict[int, dict[str, deque[Any]]] = defaultdict(
            lambda: defaultdict(deque)
        )
        for row in rows:
            pending[int(row["priority"])][str(row["actor"])].append(row)

        ordered: list[Any] = []
        virtual_dispatch = max(last_dispatch.values(), default=0.0)
        for priority in sorted(pending, reverse=True):
            actor_queues = pending[priority]
            while actor_queues:
                actor = min(
                    actor_queues,
                    key=lambda candidate: (
                        (priority, candidate) in last_dispatch,
                        last_dispatch.get((priority, candidate), 0.0),
                        str(actor_queues[candidate][0]["created_at"]),
                        candidate,
                    ),
                )
                ordered.append(actor_queues[actor].popleft())
                virtual_dispatch += 1.0
                last_dispatch[(priority, actor)] = virtual_dispatch
                if not actor_queues[actor]:
                    del actor_queues[actor]
        return ordered

    @classmethod
    def _record_actor_dispatch(
        cls,
        connection: Any,
        *,
        actor: str,
        priority: int,
        now_epoch: float,
    ) -> None:
        cls._record_dispatch_row(
            connection, "execution_actor_dispatch_state", {"priority": priority, "actor": actor},
            {"last_dispatched_at": instant(now_epoch), "updated_at": execution_timestamp()},
        )

    @staticmethod
    def _record_dispatch_row(
        connection: Any, table: str, keys: dict[str, Any], values: dict[str, Any],
    ) -> None:
        """Record a dispatch for pacing or fairness, unless a concurrent claim is.

        Waiting for that claim's commit would line claims up again; either
        record serves, as pacing and fairness may be briefly exceeded.
        """
        where = " AND ".join(f"{name} = %s" for name in keys)
        key_values = tuple(keys.values())
        if connection.execute(
            f"SELECT 1 FROM {table} WHERE {where} FOR UPDATE SKIP LOCKED", key_values,
        ).fetchone() is not None:
            assignments = ", ".join(f"{name} = %s" for name in values)
            connection.execute(
                f"UPDATE {table} SET {assignments} WHERE {where}",
                (*values.values(), *key_values),
            )
        elif connection.execute(
            f"SELECT 1 FROM {table} WHERE {where}", key_values,
        ).fetchone() is None:
            columns = [*keys, *values]
            connection.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))}) ON CONFLICT DO NOTHING",
                (*key_values, *values.values()),
            )

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        with self.database.connect() as connection:
            transaction = _SchedulerTransaction(connection, self._publish_events)
            yield transaction
        # connect() committed what was still open.
        self._publish_events(transaction.pending_events)

    def _signal(self, kind: str) -> threading.Condition:
        return {
            "execution": self._condition,
            "claim": self._claim_changed,
            "control": self._worker_control_changed,
        }[kind]

    def _notify(self, *kinds: str) -> None:
        with self._lock:
            for kind in kinds:
                self._change_generations[kind] += 1
                self._signal(kind).notify_all()

    def _change_generation(self, kind: str) -> int:
        with self._lock:
            return self._change_generations[kind]

    def _wait_for_change_signal(self, kind: str, seen: int, timeout: float) -> None:
        """Sleep until a change after ``seen``; the caller read the database unlocked."""
        condition = self._signal(kind)
        with condition:
            if self._change_generations[kind] == seen:
                condition.wait(timeout)

    def _publish_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        with self._lock:
            for event in events:
                self._transient_event_items[event["execution_id"]].append(event)
        self._notify("execution")

    def _transient_events(self, execution_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._transient_event_items.get(execution_id, ()))

    @contextmanager
    def _change_notifications(self) -> Iterator[None]:
        with self._listener_guard:
            self._listener_waiters += 1
            self._listener_idle_since = 0.0
            thread = self._listener_thread
            if thread is None or not thread.is_alive():
                self._listener_ready = threading.Event()
                thread = threading.Thread(
                    target=self._listen_for_changes,
                    name="execution-scheduler-listener",
                    daemon=True,
                )
                self._listener_thread = thread
                thread.start()
            ready = self._listener_ready
        ready.wait(timeout=1.0)
        try:
            yield
        finally:
            with self._listener_guard:
                self._listener_waiters -= 1
                if self._listener_waiters == 0:
                    self._listener_idle_since = time.monotonic()

    def _listen_for_changes(self) -> None:
        try:
            import psycopg
            from psycopg import sql

            with psycopg.connect(
                self.database.dsn,
                autocommit=True,
                application_name="tfi:execution-listener",
            ) as connection:
                connection.execute(
                    sql.SQL("LISTEN {}").format(
                        sql.Identifier(SCHEDULER_CHANGE_CHANNEL)
                    )
                )
                self._listener_ready.set()
                while True:
                    for notification in connection.notifies(
                        timeout=3.0,
                        stop_after=1,
                    ):
                        schema, separator, payload = str(notification.payload or "").partition(":")
                        if not separator or schema != self.database.schema:
                            continue
                        categories = {
                            item.strip()
                            for item in payload.split(",")
                            if item.strip()
                        }
                        self._notify(*(
                            kind for kind in ("execution", "claim", "control")
                            if kind in categories
                        ))
                    with self._listener_guard:
                        if (
                            self._listener_waiters == 0
                            and self._listener_idle_since
                            and time.monotonic() - self._listener_idle_since >= 1.0
                        ):
                            return
        except Exception:
            logging.getLogger(__name__).exception(
                "Execution Scheduler PostgreSQL notification listener failed"
            )
            self._listener_ready.set()
        finally:
            with self._listener_guard:
                if self._listener_thread is threading.current_thread():
                    self._listener_thread = None

    @staticmethod
    def _transaction_lock(connection: Any, scope: str) -> None:
        """Serialize schema initialization across starting services."""
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':' || %s, 0))",
            (scope,),
        )

    @staticmethod
    def _try_binding_claim(connection: Any, binding_id: str) -> bool:
        """Reserve a session binding for this claim without waiting.

        One binding runs one execution at a time. A claimer that finds the
        binding reserved by a concurrent claim skips that candidate.
        """
        row = connection.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema() || ':binding:' || %s, 0)) AS reserved",
            (binding_id,),
        ).fetchone()
        return bool(row["reserved"])

    def _initialize(self) -> None:
        with self._connect() as connection:
            self._transaction_lock(connection, "schema-init")
            table_columns = {
                "execution_store_meta": {"key", "value"},
                "executions": {
                    "execution_id", "capability", "actor",
                    "priority", "correlation_id", "request_json", "status",
                    "owner_worker_id", "claim_token", "lease_expires_at",
                    "available_at",
                    "cancel_requested", "attempts", "checkpoint_json",
                    "result_json", "error", "usage_accounted",
                    "usage_accounted_json",
                    "acknowledged_at", "created_at", "updated_at",
                },
                "execution_workers": {
                    "worker_id", "build_id", "state",
                    "capabilities_json", "pid", "heartbeat_at", "updated_at",
                },
                "capacity_dispatch_state": {
                    "capacity_key", "last_dispatched_at", "next_dispatch_at",
                    "interval_seconds", "updated_at",
                },
                "capacity_settings": {
                    "capacity_key", "limit_value",
                    "dispatch_interval_min_seconds",
                    "dispatch_interval_max_seconds",
                    "updated_by", "updated_at",
                },
                "execution_actor_dispatch_state": {
                    "priority", "actor", "last_dispatched_at", "updated_at",
                },
                "execution_account_usage_hourly": {
                    "bucket_hour", "provider", "account_id", "resident_id",
                    "input_tokens", "cached_input_tokens", "output_tokens",
                    "reasoning_output_tokens", "total_tokens", "updated_at",
                },
                "execution_token_usage_events": {
                    "execution_id", "event_id", "occurred_at", "usage_json",
                },
                "execution_input_controls": {
                    "control_id", "execution_id", "actor", "input_json",
                    "expected_turn_id", "status", "error", "created_at",
                    "updated_at",
                },
            }
            expected_indexes = {
                "executions_schedule_idx",
                "executions_actor_idx",
                "executions_terminal_retention_idx",
                "execution_input_controls_pending_idx",
            }
            existing_tables = {
                str(row["table_name"])
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
                )
            }
            # ExecutionService settings are owned by their module, outside the
            # scheduler's exact table/column contract.
            existing_tables.discard("service_settings")
            if existing_tables:
                if "execution_store_meta" not in existing_tables:
                    raise RuntimeError(
                        "execution store schema does not match current contract"
                    )
                actual_columns = {
                    table: {
                        str(row["column_name"])
                        for row in connection.execute(
                            'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s',
                            (table,),
                        ).fetchall()
                    }
                    for table in table_columns
                    if table in existing_tables
                }
                actual_indexes = {
                    str(row["indexname"])
                    for row in connection.execute(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname=current_schema()"
                    )
                    if not str(row["indexname"]).endswith("_pkey")
                    and not str(row["indexname"]).endswith("_key")
                }
                if (
                    existing_tables != set(table_columns)
                    or actual_columns != table_columns
                    or actual_indexes != expected_indexes
                ):
                    raise RuntimeError(
                        "execution store schema does not match current contract"
                    )
                version = connection.execute(
                    "SELECT value FROM execution_store_meta "
                    "WHERE key = 'schema_version'"
                ).fetchone()
                if (
                    version is None
                    or str(version["value"]) != EXECUTION_STORE_SCHEMA_VERSION
                ):
                    raise RuntimeError(
                        "execution store schema does not match current contract"
                    )
            else:
                # Older schemas keep cancel_requested and usage_accounted as BIGINT
                # until their conversion: SQL writes and compares '1'/'0', which
                # both BIGINT and BOOLEAN accept.
                connection.executescript(
                    """
                CREATE TABLE IF NOT EXISTS execution_store_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    capability TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    priority BIGINT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    request_json JSONB NOT NULL,
                    status TEXT NOT NULL,
                    owner_worker_id TEXT NOT NULL DEFAULT '',
                    claim_token TEXT NOT NULL DEFAULT '',
                    lease_expires_at TIMESTAMPTZ,
                    available_at TIMESTAMPTZ NOT NULL DEFAULT 'epoch',
                    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
                    attempts BIGINT NOT NULL DEFAULT 0,
                    checkpoint_json JSONB NOT NULL DEFAULT '{}',
                    result_json JSONB NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    usage_accounted BOOLEAN NOT NULL DEFAULT FALSE,
                    usage_accounted_json JSONB NOT NULL DEFAULT '{}',
                    acknowledged_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                CREATE INDEX IF NOT EXISTS executions_schedule_idx
                    ON executions(status, priority DESC, created_at ASC);
                CREATE INDEX IF NOT EXISTS executions_actor_idx
                    ON executions(actor, created_at DESC);
                CREATE INDEX IF NOT EXISTS executions_terminal_retention_idx
                    ON executions(status, updated_at);
                CREATE TABLE IF NOT EXISTS execution_workers (
                    worker_id TEXT PRIMARY KEY,
                    build_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    capabilities_json JSONB NOT NULL,
                    pid BIGINT NOT NULL DEFAULT 0,
                    heartbeat_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capacity_dispatch_state (
                    capacity_key TEXT PRIMARY KEY,
                    last_dispatched_at TIMESTAMPTZ NOT NULL DEFAULT 'epoch',
                    next_dispatch_at TIMESTAMPTZ NOT NULL DEFAULT 'epoch',
                    interval_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capacity_settings (
                    capacity_key TEXT PRIMARY KEY,
                    limit_value BIGINT NOT NULL,
                    dispatch_interval_min_seconds DOUBLE PRECISION,
                    dispatch_interval_max_seconds DOUBLE PRECISION,
                    updated_by TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_actor_dispatch_state (
                    priority BIGINT NOT NULL,
                    actor TEXT NOT NULL,
                    last_dispatched_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (priority, actor)
                );
                CREATE TABLE IF NOT EXISTS execution_account_usage_hourly (
                    bucket_hour TIMESTAMPTZ NOT NULL, provider TEXT NOT NULL,
                    account_id TEXT NOT NULL, resident_id TEXT NOT NULL,
                    input_tokens BIGINT NOT NULL DEFAULT 0,
                    cached_input_tokens BIGINT NOT NULL DEFAULT 0,
                    output_tokens BIGINT NOT NULL DEFAULT 0,
                    reasoning_output_tokens BIGINT NOT NULL DEFAULT 0,
                    total_tokens BIGINT NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (bucket_hour, provider, account_id, resident_id)
                );
                CREATE TABLE IF NOT EXISTS execution_token_usage_events (
                    execution_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    occurred_at TIMESTAMPTZ NOT NULL,
                    usage_json JSONB NOT NULL,
                    PRIMARY KEY (execution_id, event_id),
                    FOREIGN KEY (execution_id)
                        REFERENCES executions(execution_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS execution_input_controls (
                    control_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    input_json JSONB NOT NULL,
                    expected_turn_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    FOREIGN KEY (execution_id)
                        REFERENCES executions(execution_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS execution_input_controls_pending_idx
                    ON execution_input_controls(
                        execution_id, status, created_at
                    );
                    """
                )
                connection.execute(
                    "\n                    INSERT INTO execution_store_meta (key, value)\n                    VALUES ('schema_version', %s)\n                    ",
                    (EXECUTION_STORE_SCHEMA_VERSION,),
                )
            connection.executescript(
                f"""
                CREATE OR REPLACE FUNCTION notify_scheduler_change()
                RETURNS trigger LANGUAGE plpgsql AS $$
                DECLARE
                    categories TEXT := '';
                BEGIN
                    IF TG_TABLE_NAME = 'executions' THEN
                        categories := 'execution';
                        IF TG_OP <> 'UPDATE' OR NOT (
                            NEW.status IS NOT DISTINCT FROM OLD.status
                            AND NEW.cancel_requested IS NOT DISTINCT FROM OLD.cancel_requested
                            AND NEW.available_at IS NOT DISTINCT FROM OLD.available_at
                            AND NEW.priority IS NOT DISTINCT FROM OLD.priority
                            AND NEW.capability IS NOT DISTINCT FROM OLD.capability
                            AND NEW.request_json IS NOT DISTINCT FROM OLD.request_json
                        ) THEN
                            categories := categories || ',claim';
                        END IF;
                        IF TG_OP <> 'UPDATE' OR NOT (
                            NEW.status IS NOT DISTINCT FROM OLD.status
                            AND NEW.cancel_requested IS NOT DISTINCT FROM OLD.cancel_requested
                            AND NEW.owner_worker_id IS NOT DISTINCT FROM OLD.owner_worker_id
                            AND NEW.claim_token IS NOT DISTINCT FROM OLD.claim_token
                        ) THEN
                            categories := categories || ',control';
                        END IF;
                    ELSIF TG_TABLE_NAME = 'execution_workers' THEN
                        IF TG_OP <> 'UPDATE' OR NEW.state IS DISTINCT FROM OLD.state THEN
                            categories := 'claim,control';
                        END IF;
                    ELSIF TG_TABLE_NAME = 'execution_input_controls' THEN
                        categories := 'control';
                    ELSE
                        categories := 'claim';
                    END IF;
                    IF categories <> '' THEN
                        PERFORM pg_notify('{SCHEDULER_CHANGE_CHANNEL}', current_schema() || ':' || categories);
                    END IF;
                    RETURN NULL;
                END;
                $$;
                """
            )
            for table in (
                "executions",
                "execution_workers",
                "capacity_dispatch_state",
                "execution_input_controls",
                "execution_store_meta",
            ):
                trigger = f"{table}_scheduler_change"
                # Startup used to drop and recreate each trigger.  Dropping
                # takes an ACCESS EXCLUSIVE lock, which waits for every
                # reader, including a running pg_dump backup, and queues
                # every later reader behind it; the service then failed on
                # its lock timeout and restarted in a loop until the backup
                # ended.  A trigger that already matches is left alone, and
                # CREATE OR REPLACE needs only a lock that readers share.
                if connection.execute(
                    """
                    SELECT 1 FROM pg_trigger
                    WHERE tgrelid = to_regclass(%s) AND tgname = %s
                      AND NOT tgisinternal AND tgenabled = 'O'
                      AND tgfoid = to_regproc('notify_scheduler_change')
                      AND tgtype = %s AND tgqual IS NULL
                      AND cardinality(tgattr::int2[]) = 0
                    """,
                    (table, trigger, SCHEDULER_TRIGGER_TYPE),
                ).fetchone() is None:
                    connection.execute(
                        f"CREATE OR REPLACE TRIGGER {trigger} "
                        f"AFTER INSERT OR UPDATE OR DELETE ON {table} "
                        "FOR EACH ROW EXECUTE FUNCTION notify_scheduler_change()"
                    )
    def _event(
        self,
        connection: Any,
        execution_id: str,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._event_sequence = max(
                self._event_sequence + 1,
                time.time_ns() // 1_000,
            )
            sequence = self._event_sequence
        event = {
            "execution_id": execution_id,
            "sequence": sequence,
            "type": str(event_type or ""),
            "created_at": execution_timestamp(),
            "details": dict(details or {}),
        }
        from .execution_lifecycle import scheduler_transition
        scheduler_transition(connection, execution_id, event_type, event["created_at"])
        if event_type == "started":
            connection.execute(
                "INSERT INTO execution_store_meta(key,value) VALUES (%s,%s) ON CONFLICT(key) DO NOTHING",
                (f"started:{execution_id}", _json(event)),
            )
        pending = getattr(connection, "pending_events", None)
        if pending is None:
            self._publish_events([event])
        else:
            pending.append(event)
        return dict(event)

    def _record_token_usage(
        self,
        connection: Any,
        *,
        request: dict[str, Any],
        usage: object,
        occurred_at: str,
    ) -> bool:
        normalized = normalize_token_usage(usage)
        if not normalized:
            return False
        provider, account_id = self.accounts.usage_identity(request)
        if not provider or len(provider) > 120:
            provider = "unknown"
        bucket_hour = _usage_bucket_hour(occurred_at)
        resident_id = _usage_resident_id(request)
        updated_at = execution_timestamp()
        connection.execute(
            '\n            INSERT INTO execution_account_usage_hourly (\n                bucket_hour, provider, account_id, resident_id, input_tokens,\n                cached_input_tokens, output_tokens,\n                reasoning_output_tokens, total_tokens, updated_at\n            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)\n            ON CONFLICT(bucket_hour, provider, account_id, resident_id) DO UPDATE SET\n                input_tokens = (\n                    execution_account_usage_hourly.input_tokens\n                    + excluded.input_tokens\n                ),\n                cached_input_tokens = (\n                    execution_account_usage_hourly.cached_input_tokens\n                    + excluded.cached_input_tokens\n                ),\n                output_tokens = (\n                    execution_account_usage_hourly.output_tokens\n                    + excluded.output_tokens\n                ),\n                reasoning_output_tokens = (\n                    execution_account_usage_hourly.reasoning_output_tokens\n                    + excluded.reasoning_output_tokens\n                ),\n                total_tokens = (\n                    execution_account_usage_hourly.total_tokens\n                    + excluded.total_tokens\n                ),\n                updated_at = excluded.updated_at\n            ',
            (
                instant(bucket_hour),
                provider,
                account_id,
                resident_id,
                normalized.get("input_tokens", 0),
                normalized.get("cached_input_tokens", 0),
                normalized.get("output_tokens", 0),
                normalized.get("reasoning_output_tokens", 0),
                normalized.get("total_tokens", 0),
                updated_at,
            ),
        )
        return True

    @staticmethod
    def _merge_normalized_usage(
        left: object,
        right: object,
    ) -> dict[str, int]:
        left_usage = normalize_token_usage(left)
        right_usage = normalize_token_usage(right)
        return {
            key: left_usage.get(key, 0) + right_usage.get(key, 0)
            for key in TOKEN_USAGE_KEYS
            if left_usage.get(key, 0) + right_usage.get(key, 0) > 0
        }

    @staticmethod
    def _remaining_normalized_usage(
        total: object,
        accounted: object,
    ) -> dict[str, int]:
        total_usage = normalize_token_usage(total)
        accounted_usage = normalize_token_usage(accounted)
        return {
            key: max(
                0,
                total_usage.get(key, 0) - accounted_usage.get(key, 0),
            )
            for key in TOKEN_USAGE_KEYS
            if total_usage.get(key, 0) > accounted_usage.get(key, 0)
        }

    def record_token_usage_delta(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        event_id: str,
        usage: object,
        occurred_at: str = "",
    ) -> dict[str, Any]:
        clean_event_id = str(event_id or "").strip()
        if not clean_event_id or len(clean_event_id) > 256:
            raise ValueError("token usage event id is invalid")
        normalized = normalize_token_usage(usage)
        if not normalized:
            return {"recorded": False, "duplicate": False}
        observed_at = str(occurred_at or execution_timestamp())
        with self._connect() as connection:
            row = self._assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                connection.rollback()
                raise RuntimeError(
                    f"cannot record token usage in state {row['status']}"
                )
            inserted = connection.execute(
                '\n                INSERT INTO execution_token_usage_events (\n                    execution_id, event_id, occurred_at, usage_json\n                ) VALUES (%s, %s, %s, %s)\n                ON CONFLICT DO NOTHING\n                ',
                (
                    execution_id,
                    clean_event_id,
                    observed_at,
                    _json(normalized),
                ),
            ).rowcount
            if not inserted:
                connection.commit()
                return {"recorded": False, "duplicate": True}
            self._record_token_usage(
                connection,
                request=_object(row["request_json"]),
                usage=normalized,
                occurred_at=observed_at,
            )
            accounted = self._merge_normalized_usage(
                _object(row["usage_accounted_json"]),
                normalized,
            )
            connection.execute(
                '\n                UPDATE executions\n                SET usage_accounted_json = %s, updated_at = %s\n                WHERE execution_id = %s\n                ',
                # The hour may be an earlier one; the execution changed now.
                (_json(accounted), execution_timestamp(), execution_id),
            )
            connection.commit()
        return {"recorded": True, "duplicate": False}

    def usage_watermark(self, key: str) -> str:
        """How far a source of requests outside Executions was counted ('' before the first time)."""
        with self._connect() as connection:
            row = connection.execute(
                'SELECT value FROM execution_store_meta WHERE key = %s', (key,),
            ).fetchone()
        return str(row["value"]) if row else ""

    def latest_runtime_root(self, binding_ids: list[str]) -> str:
        """The runtime directory the latest kept request of these bindings named ('' when none)."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_json::jsonb #>> '{runtime_options,runtime_root}' AS runtime_root FROM executions "
                "WHERE request_json::jsonb #>> '{session,binding_id}' = ANY(%s) ORDER BY created_at DESC LIMIT 1",
                (list(binding_ids),),
            ).fetchone()
        return str(row["runtime_root"] or "") if row else ""

    def earliest_unfinished_execution(self, resident_id: str) -> str:
        """When the oldest unfinished Execution of a Resident was accepted ('' when none)."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MIN(created_at) AS created_at FROM executions "
                "WHERE status NOT IN ('completed', 'failed', 'cancelled') "
                "AND request_json::jsonb #>> '{metadata,resident_id}' = %s",
                (resident_id,),
            ).fetchone()
        return (iso_time(row["created_at"]) or "") if row else ""

    def record_unattributed_token_usage(
        self,
        key: str,
        *,
        expected: str,
        watermark: str,
        request: dict[str, Any],
        requests: list[tuple[str, object, str]],
    ) -> int:
        """Count model requests that belong to no Execution in the hourly ledger.

        ``requests`` are ``(event id, usage, occurred at)`` read from a source
        up to ``watermark``.  They count only while ``key`` still holds
        ``expected``, so each is counted once; one an Execution already
        reported is skipped.  No Execution's own usage changes.
        """
        with self._connect() as connection:
            # Moving the watermark first is the compare-and-set: a concurrent
            # counter of the same range waits on this row, then finds it moved.
            advanced = connection.execute(
                'UPDATE execution_store_meta SET value = %s WHERE key = %s AND value = %s',
                (watermark, key, expected),
            ).rowcount
            if not advanced and expected == "":
                advanced = connection.execute(
                    'INSERT INTO execution_store_meta(key, value) VALUES (%s, %s) ON CONFLICT(key) DO NOTHING',
                    (key, watermark),
                ).rowcount
            if not advanced:
                connection.rollback()
                return 0
            reported = {
                str(row["event_id"])
                for row in connection.execute(
                    'SELECT event_id FROM execution_token_usage_events WHERE event_id = ANY(%s)',
                    ([event_id for event_id, _usage, _at in requests],),
                ).fetchall()
            } if requests else set()
            counted = sum(
                self._record_token_usage(connection, request=request, usage=usage, occurred_at=occurred_at)
                for event_id, usage, occurred_at in requests
                if event_id not in reported
            )
            connection.commit()
        return counted

    def token_usage(
        self,
        *,
        start_hour: str,
        end_hour: str,
        provider: str = "codex",
        include_accounts: bool = False,
    ) -> dict[str, Any]:
        """Read one provider, or all providers with provider='*'."""
        start = _usage_bucket_hour(start_hour)
        end = _usage_bucket_hour(end_hour)
        if start >= end:
            raise ValueError("token usage range must be increasing")
        start_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_at = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if (end_at - start_at).days > 62:
            raise ValueError("token usage range cannot exceed 62 days")
        clean_provider = str(provider or "").strip().lower()
        if not clean_provider or len(clean_provider) > 120:
            raise ValueError("token usage provider is invalid")
        identities = self.accounts.usage_identities()
        clean_provider, requested_account = identities.normalize(clean_provider)
        provider_filter = "" if clean_provider == "*" else " AND provider = ANY(%s)"
        provider_ids = identities.source_providers(clean_provider)
        parameters = (start, end) if clean_provider == "*" else (start, end, provider_ids)
        # Keep account identity until Backend normalization, including aggregate reads.
        dimensions = "bucket_hour, provider, resident_id, account_id"
        totals = ", ".join(f"SUM({key}) AS {key}" for key in TOKEN_USAGE_KEYS)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT {dimensions}, {totals}, MAX(updated_at) AS updated_at
                  FROM execution_account_usage_hourly
                  WHERE bucket_hour >= %s AND bucket_hour < %s{provider_filter}
                  GROUP BY {dimensions} ORDER BY {dimensions}""",
                parameters,
            ).fetchall()
        normalized_rows = {}
        for row in rows:
            item = decode_times(row, ("bucket_hour", "updated_at"))
            item["provider"], item["account_id"] = identities.normalize(item["provider"], item["account_id"])
            if clean_provider != "*" and item["provider"] != clean_provider:
                continue
            if requested_account and item["account_id"] != requested_account:
                continue
            dimensions = ("bucket_hour", "provider", "resident_id") + (("account_id",) if include_accounts else ())
            identity = tuple(item[key] for key in dimensions)
            if identity in normalized_rows:
                previous = normalized_rows[identity]
                for key in TOKEN_USAGE_KEYS:
                    previous[key] += item[key]
                previous["updated_at"] = max(previous["updated_at"], item["updated_at"])
            else:
                normalized_rows[identity] = item
        rows = [normalized_rows[key] for key in sorted(normalized_rows)]
        return {
            "schema": TOKEN_USAGE_SCHEMA,
            "provider": clean_provider,
            "start_hour": start,
            "end_hour": end,
            "rows": [
                {
                    "bucket_hour": row["bucket_hour"],
                    **({"provider": str(row["provider"])} if clean_provider == "*" else {}),
                    "resident_id": str(row["resident_id"]),
                    **({"account_id": str(row["account_id"])} if include_accounts else {}),
                    "usage": {
                        key: int(row[key])
                        for key in TOKEN_USAGE_KEYS
                        if int(row[key]) > 0
                    },
                }
                for row in rows
            ],
            "updated_at": max(
                (row["updated_at"] for row in rows),
                default="",
            ),
        }

    def submit(self, request: ExecutionRequest, *, prepare=None) -> dict[str, Any]:
        request.validate()
        from .agent_data_store import get_agent_data_store
        from .execution_lifecycle import check_new_submission
        from .submission_identity import SubmissionIdentity, submission_digest
        get_agent_data_store()
        identity = SubmissionIdentity.from_request(request)
        original_digest = identity.digest
        now = execution_timestamp()
        digest_key = f"request_digest:{request.execution_id}"
        with self._connect() as connection:
            existing = connection.execute(
                'SELECT request_json FROM executions WHERE execution_id = %s',
                (request.execution_id,),
            ).fetchone()
            if existing is None and not connection.execute(
                # The digest row reserves the id: a concurrent submit of the
                # same id waits here and then answers as a duplicate.
                'INSERT INTO execution_store_meta(key,value) VALUES (%s,%s) ON CONFLICT(key) DO NOTHING',
                (digest_key, original_digest),
            ).rowcount:
                existing = connection.execute(
                    'SELECT request_json FROM executions WHERE execution_id = %s',
                    (request.execution_id,),
                ).fetchone()
                if existing is None:
                    # A digest left without its execution.
                    connection.execute(
                        'UPDATE execution_store_meta SET value=%s WHERE key=%s',
                        (original_digest, digest_key),
                    )
            if existing is not None:
                saved = connection.execute("SELECT value FROM execution_store_meta WHERE key=%s", (digest_key,)).fetchone()
                stored_digest = str(saved["value"]) if saved else submission_digest(_object(existing["request_json"]))
                if not identity.matches(stored_digest):
                    connection.rollback()
                    raise ValueError(
                        "execution id already exists with a different request"
                    )
                connection.commit()
                return self.snapshot(request.execution_id)
            check_new_submission(connection, request.execution_id, identity)
            if prepare is not None:
                request = prepare(request)
            capacity_key = self._capacity_key(request.to_payload())
            nonterminal = connection.execute(
                """
                SELECT 1
                FROM executions
                WHERE status IN ('queued', 'claimed', 'running')
                LIMIT 1
                """
            ).fetchone()
            if nonterminal is None:
                connection.execute("DELETE FROM execution_actor_dispatch_state")
            connection.execute(
                "\n                INSERT INTO executions (\n                    execution_id, capability, actor, priority,\n                    correlation_id, request_json, status, created_at, updated_at\n                ) VALUES (%s, %s, %s, %s, %s, %s, 'queued', %s, %s)\n                ",
                (
                    request.execution_id,
                    request.capability,
                    request.actor,
                    request.priority,
                    request.correlation_id,
                    _json(request.to_payload()),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                request.execution_id,
                "queued",
                {
                    "service_type": request.service_type,
                    "provider": request.provider,
                    "operation": request.operation,
                    "capability": request.capability,
                    "priority": request.priority,
                    "capacity_key": capacity_key,
                },
            )
            connection.commit()
        # Workers wait on this condition between claims.  Persisting a new
        # request without waking them makes every idle submission pay the
        # full long-poll timeout before it can be claimed.
        self._notify("claim")
        return self.snapshot(request.execution_id)

    def withdraw_unstarted(
        self, execution_id: str, *, actor: str, binding_id: str,
        operation_id: str, replacement_execution_id: str, performed_by: str,
    ) -> dict[str, Any]:
        """Retire an unclaimed request for its owner's durable input handoff.

        This does not cancel the business input. A claim, a previous attempt,
        a checkpoint or a Stop wins over the handoff. The terminal receipt is
        retained so a lost response can be read back without another action.
        """
        if not all((binding_id, operation_id, replacement_execution_id, performed_by)):
            raise ValueError("unstarted handoff identity is incomplete")
        if replacement_execution_id == execution_id:
            raise ValueError("handoff needs a distinct prepared execution")
        receipt = dict(execution_id=execution_id, binding_id=binding_id,
                       operation_id=operation_id, replacement_execution_id=replacement_execution_id)
        from . import execution_lifecycle
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM executions WHERE execution_id=%s FOR UPDATE', (execution_id,),
            ).fetchone()
            if row is not None and row['actor'] != actor:
                raise PermissionError("execution belongs to another actor")
            previous = execution_lifecycle.unstarted_handoff(connection, execution_id, actor=actor)
            if previous:
                if previous != receipt:
                    raise ValueError("execution already handed off by another operation")
                return {"withdrawn": True, **receipt}
            if row is None:
                raise KeyError(execution_id)
            request = _object(row['request_json'])
            session = request.get('session') or {}
            if session.get('mode') == 'stateless' and not session.get('binding_id'):
                # Timer/isolated Resident inputs deliberately have no session.
                # Their immutable request still names the Resident and input;
                # use that ownership for the same never-claimed input handoff.
                resident_id = request.get('agent_id') or ''
                metadata = request.get('metadata') or {}
                input_id = (request.get('runtime_options') or {}).get('client_user_message_id')
                if (not resident_id.startswith('resident_')
                        or metadata.get('business_type') != 'resident'
                        or metadata.get('resident_id') != resident_id or not input_id):
                    return {"withdrawn": False, "execution_id": execution_id}
                expected_binding = 'resident:' + resident_id
            elif session.get('mode') == 'persistent' and session.get('binding_id'):
                expected_binding = session['binding_id']
            else:
                return {"withdrawn": False, "execution_id": execution_id}
            if expected_binding != binding_id:
                raise ValueError("execution binding changed")
            if (row['status'] != 'queued' or row['attempts'] or row['owner_worker_id']
                    or row['cancel_requested'] or _object(row['checkpoint_json'])
                    or request.get('service_type') != 'agent_session' or request.get('operation') != 'turn'):
                return {"withdrawn": False, "execution_id": execution_id}
            self._finalize_released_cancellation(
                connection, row, error="unstarted execution handed off: " + operation_id,
                details={"actor": actor_principal(performed_by).id, "request_actor": actor,
                         "unstarted_handoff": receipt},
            )
            result = _object(connection.execute(
                'SELECT result_json FROM executions WHERE execution_id=%s', (execution_id,),
            ).fetchone()['result_json'])
            connection.execute('UPDATE executions SET result_json=%s WHERE execution_id=%s',
                               (_json({**result, "unstarted_handoff": receipt}), execution_id))
            execution_lifecycle.record_unstarted_handoff(
                connection, agent_id=request['agent_id'], receipt=receipt,
                performed_by=performed_by, at=execution_timestamp(),
            )
            connection.commit()
        self._notify("control")
        return {"withdrawn": True, **receipt}

    def set_priority(
        self,
        execution_id: str,
        *,
        actor: str,
        priority: int,
        performed_by: str = "",
    ) -> dict[str, Any]:
        performer = actor_principal(performed_by or actor).id
        next_priority = int(priority)
        if not -1_000_000 <= next_priority <= 1_000_000:
            raise ValueError("execution priority is outside the supported range")
        with self._connect() as connection:
            row = connection.execute(
                '\n                SELECT actor, status, request_json\n                FROM executions\n                WHERE execution_id = %s FOR UPDATE\n                ',
                (execution_id,),
            ).fetchone()
            if row is None or str(row["actor"]) != actor:
                connection.rollback()
                raise KeyError(execution_id)
            if str(row["status"]) != "queued":
                connection.rollback()
                raise ValueError("only queued executions can change priority")
            request = _object(row["request_json"])
            request["priority"] = next_priority
            connection.execute(
                '\n                UPDATE executions\n                SET priority = %s, request_json = %s, updated_at = %s\n                WHERE execution_id = %s\n                ',
                (next_priority, _json(request), execution_timestamp(), execution_id),
            )
            self._event(
                connection,
                execution_id,
                "priority_changed",
                {"actor": performer, "request_actor": actor, "priority": next_priority, "identity": OperationIdentity.for_actor(performer).to_dict()},
            )
            connection.commit()
        return self.snapshot(execution_id)

    def reassign_queued_route(
        self,
        execution_id: str,
        *,
        actor: str,
        backend_route_id: str,
        performed_by: str = "",
    ) -> dict[str, Any]:
        """Move a queued execution; the caller guarantees its binding is unused."""

        performer = actor_principal(performed_by or actor).id
        route_id = str(backend_route_id or "").strip()
        if not route_id:
            raise ValueError("backend route id is required")
        with self._connect() as connection:
            row = connection.execute(
                'SELECT actor, status, request_json FROM executions WHERE execution_id = %s FOR UPDATE',
                (execution_id,),
            ).fetchone()
            if row is None or str(row["actor"]) != actor:
                connection.rollback()
                raise KeyError(execution_id)
            if str(row["status"]) != "queued":
                connection.rollback()
                raise ValueError("only queued executions can change account")
            request = _object(row["request_json"])
            session = request.get("session") if isinstance(request.get("session"), dict) else {}
            if (
                str(request.get("service_type") or "") != "agent_session"
            ):
                connection.rollback()
                raise ValueError("only queued agent executions can change account")
            previous_route_id = str(session.get("backend_route_id") or "")
            request = self.accounts.reroute(request, route_id)
            normalized = ExecutionRequest.from_payload(request).to_payload()
            connection.execute(
                'UPDATE executions SET request_json = %s, updated_at = %s WHERE execution_id = %s',
                (_json(normalized), execution_timestamp(), execution_id),
            )
            self._event(connection, execution_id, "route_changed", {
                "actor": performer,
                "request_actor": actor,
                "source_backend_route_id": previous_route_id,
                "backend_route_id": route_id,
                "identity": OperationIdentity.for_actor(performer).to_dict(),
            })
            connection.commit()
        return self.snapshot(execution_id)

    def _prune_stale_workers(
        self,
        connection: Any,
        *,
        now_epoch: float | None = None,
    ) -> int:
        cutoff = (
            float(now_epoch if now_epoch is not None else time.time())
            - self.worker_heartbeat_timeout_seconds
        )
        cursor = connection.execute(
            "\n            DELETE FROM execution_workers\n            WHERE heartbeat_at < %s\n              AND worker_id NOT IN (\n                  SELECT owner_worker_id\n                  FROM executions\n                  WHERE status IN ('claimed', 'running')\n              )\n            ",
            (instant(cutoff),),
        )
        return max(0, int(cursor.rowcount or 0))

    def register_worker(
        self,
        *,
        worker_id: str,
        build_id: str,
        capabilities: list[str],
        pid: int = 0,
        state: str = "active",
    ) -> dict[str, Any]:
        clean_capabilities = sorted(
            {
                str(item or "").strip()
                for item in capabilities
                if str(item or "").strip()
            }
        )
        if (
            not worker_id
            or not build_id
            or not clean_capabilities
        ):
            raise ValueError("worker registration is incomplete")
        if state not in {"standby", "active", "draining", "offline"}:
            raise ValueError("invalid worker state")
        now = execution_timestamp()
        with self._connect() as connection:
            # A restarted Worker can take its predecessor's expired leases at once.
            self._recover_expired(connection)
            self._prune_stale_workers(connection)
            existing = connection.execute(
                'SELECT build_id, state FROM execution_workers WHERE worker_id = %s FOR UPDATE',
                (worker_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["build_id"]) != build_id
                ):
                    connection.rollback()
                    raise ValueError("worker identity cannot be rebound")
                if (
                    str(existing["state"]) in {"draining", "offline"}
                    and state in {"standby", "active"}
                ):
                    connection.rollback()
                    raise RuntimeError(
                        "retired worker cannot reactivate by registration"
                    )
            connection.execute(
                '\n                INSERT INTO execution_workers (\n                    worker_id, build_id, state, capabilities_json,\n                    pid, heartbeat_at, updated_at\n                ) VALUES (%s, %s, %s, %s, %s, %s, %s)\n                ON CONFLICT(worker_id) DO UPDATE SET\n                    build_id=excluded.build_id,\n                    state=excluded.state,\n                    capabilities_json=excluded.capabilities_json,\n                    pid=excluded.pid,\n                    heartbeat_at=excluded.heartbeat_at,\n                    updated_at=excluded.updated_at\n                ',
                (
                    worker_id,
                    build_id,
                    state,
                    _json(clean_capabilities),
                    int(pid or 0),
                    instant(time.time()),
                    now,
                ),
            )
            connection.commit()
        self._notify("execution", "claim")
        return {
            "worker_id": worker_id,
            "build_id": build_id,
            "state": state,
            "capabilities": clean_capabilities,
        }

    def activate_worker(
        self,
        worker_id: str,
        *,
        previous_worker_id: str = "",
    ) -> dict[str, Any]:
        target_id = str(worker_id or "").strip()
        previous_id = str(previous_worker_id or "").strip()
        if not target_id:
            raise ValueError("worker_id is required")
        if previous_id == target_id:
            raise ValueError("worker cannot replace itself")
        now = execution_timestamp()
        with self._connect() as connection:
            # A generation switch retires every other build; locking the few
            # Worker rows (in one order) keeps two switches from both winning.
            connection.execute(
                'SELECT worker_id FROM execution_workers ORDER BY worker_id FOR UPDATE'
            ).fetchall()
            target = connection.execute(
                'SELECT state, build_id FROM execution_workers WHERE worker_id = %s',
                (target_id,),
            ).fetchone()
            if target is None:
                connection.rollback()
                raise KeyError(target_id)
            if str(target["state"]) not in {"standby", "active"}:
                connection.rollback()
                raise RuntimeError(
                    f"cannot activate worker in state {target['state']}"
                )
            previous_state = ""
            if previous_id:
                previous = connection.execute(
                    'SELECT state FROM execution_workers WHERE worker_id = %s',
                    (previous_id,),
                ).fetchone()
                if previous is not None and str(previous["state"]) not in {
                    "active",
                    "draining",
                    "offline",
                }:
                    connection.rollback()
                    raise RuntimeError(
                        f"cannot retire worker in state {previous['state']}"
                    )
                if previous is not None and str(previous["state"]) == "offline":
                    # It already left (a Worker another generation retired
                    # exits by itself); there is nothing left to retire.
                    previous_state = "offline"
                elif previous is not None:
                    previous_state = "draining"
                    connection.execute(
                        "\n                        UPDATE execution_workers\n                        SET state = 'draining', heartbeat_at = %s, updated_at = %s\n                        WHERE worker_id = %s\n                        ",
                        (instant(time.time()), now, previous_id),
                    )
            # Workers of the target's generation keep claiming side by side
            # (the Dev and Release pools run the same build); every other
            # generation stops claiming and hands its executions over, so a
            # publish that reloads both pools moves each execution only once.
            connection.execute(
                "\n                UPDATE execution_workers\n                SET state = 'draining', heartbeat_at = %s, updated_at = %s\n                WHERE state = 'active' AND worker_id != %s AND build_id != %s\n                ",
                (instant(time.time()), now, target_id, str(target["build_id"])),
            )
            connection.execute(
                "\n                UPDATE execution_workers\n                SET state = 'active', heartbeat_at = %s, updated_at = %s\n                WHERE worker_id = %s\n                ",
                    (instant(time.time()), now, target_id),
                )
            connection.commit()
        self._notify("execution", "control")
        return {
            "worker_id": target_id,
            "state": "active",
            "previous_worker_id": previous_id,
            "previous_state": previous_state,
        }

    def set_worker_state(self, worker_id: str, state: str) -> None:
        if state not in {"draining", "offline"}:
            raise ValueError(
                "worker state can only retire through this endpoint"
            )
        with self._connect() as connection:
            cursor = connection.execute(
                '\n                UPDATE execution_workers\n                SET state = %s, heartbeat_at = %s, updated_at = %s\n                WHERE worker_id = %s\n                ',
                (state, instant(time.time()), execution_timestamp(), worker_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(worker_id)
            connection.commit()
        self._notify("execution", "control")

    @staticmethod
    def _handover_checkpoint(
        checkpoint: dict[str, Any], worker_id: str,
    ) -> dict[str, Any]:
        payload = dict(checkpoint)
        payload["turn_start_reason"] = "backend_service_update"
        if payload.get("turn_id") and not payload.get("provider_completed"):
            payload["worker_handover"] = {
                "worker_id": worker_id,
                "turn_id": str(payload["turn_id"]),
                "backend_session_id": str(payload.get("backend_session_id") or ""),
            }
            provider = AGENT_PROVIDERS.get(str(payload.get("provider") or ""))
            if provider is not None and provider.handover_keeps_turn and payload.get("operation") == "turn":
                # The turn goes on in the Provider's daemon; the next Worker
                # attaches to it instead of asking it again.
                payload["worker_handover"]["turn_kept_running"] = True
        return payload

    def prepare_handover(
        self, execution_id: str, *, worker_id: str, claim_token: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id, claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                raise PermissionError("execution claim is no longer active")
            if row["cancel_requested"]:
                return {"ready": False, "cancel_requested": True}
            checkpoint = _object(row["checkpoint_json"])
            if not checkpoint.get("resumable"):
                return {"ready": False, "cancel_requested": False}
            payload = self._handover_checkpoint(checkpoint, worker_id)
            connection.execute(
                'UPDATE executions SET checkpoint_json = %s, updated_at = %s WHERE execution_id = %s',
                (_json(payload), execution_timestamp(), execution_id),
            )
            if payload != checkpoint:
                self._event(connection, execution_id, "handover_prepared", {
                    "worker_id": worker_id,
                    "turn_id": str(payload.get("turn_id") or ""),
                })
            connection.commit()
        return {
            "ready": True,
            "cancel_requested": False,
            "interrupt_required": bool(
                payload.get("turn_id") and not payload.get("provider_completed")
                and not _turn_kept_running(payload)
            ),
        }

    def handover_worker(self, worker_id: str) -> dict[str, Any]:
        clean_worker_id = str(worker_id or "").strip()
        if not clean_worker_id:
            raise ValueError("worker_id is required")
        requeued: list[str] = []
        blocked: list[str] = []
        with self._connect() as connection:
            worker = connection.execute(
                'SELECT state FROM execution_workers WHERE worker_id = %s FOR UPDATE',
                (clean_worker_id,),
            ).fetchone()
            if worker is None:
                connection.rollback()
                return {
                    "worker_id": clean_worker_id,
                    "requeued_execution_ids": [],
                    "blocked_execution_ids": [],
                }
            if str(worker["state"]) not in {"draining", "offline"}:
                connection.rollback()
                raise RuntimeError("only a retiring worker can hand over claims")
            rows = connection.execute(
                "\n                SELECT execution_id, status, checkpoint_json, cancel_requested,\n                       owner_worker_id, request_json\n                FROM executions\n                WHERE owner_worker_id = %s\n                  AND status IN ('claimed', 'running') FOR UPDATE\n                ",
                (clean_worker_id,),
            ).fetchall()
            for row in rows:
                execution_id = str(row["execution_id"])
                checkpoint = _object(row["checkpoint_json"])
                if (
                    str(row["status"]) != "claimed"
                    and not bool(checkpoint.get("resumable"))
                ):
                    blocked.append(execution_id)
                    continue
                if bool(row["cancel_requested"]):
                    # The retired Worker's Provider turn may still run.
                    if self._requeue_abandoned_provider_work(
                        connection, row, cancelled_error="execution cancelled during worker handover",
                    ):
                        requeued.append(execution_id)
                        continue
                    self._finalize_released_cancellation(
                        connection, row,
                        error="execution cancelled during worker handover",
                        details={
                            "reason": "worker handover after cancellation",
                            "previous_worker_id": clean_worker_id,
                        },
                    )
                    continue
                if checkpoint.get("resumable"):
                    checkpoint = self._handover_checkpoint(checkpoint, clean_worker_id)
                connection.execute(
                    "\n                    UPDATE executions\n                    SET status = 'queued', owner_worker_id = '',\n                        claim_token = '', lease_expires_at = NULL, updated_at = %s,\n                        checkpoint_json = %s\n                    WHERE execution_id = %s AND owner_worker_id = %s\n                    ",
                    (
                        execution_timestamp(), _json(checkpoint),
                        execution_id, clean_worker_id,
                    ),
                )
                self._event(
                    connection,
                    execution_id,
                    "requeued",
                    {
                        "reason": "worker drain handover",
                        "previous_worker_id": clean_worker_id,
                        "checkpoint": checkpoint,
                    },
                )
                requeued.append(execution_id)
            connection.execute(
                "\n                UPDATE execution_workers\n                SET state = 'offline', heartbeat_at = %s, updated_at = %s\n                WHERE worker_id = %s\n                ",
                (instant(time.time()), execution_timestamp(), clean_worker_id),
            )
            connection.commit()
        self._notify("execution", "control")
        return {
            "worker_id": clean_worker_id,
            "requeued_execution_ids": requeued,
            "blocked_execution_ids": blocked,
        }

    def requeue_claim(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        reason: str,
        retry_after_seconds: float = 0,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if str(row["status"]) not in ACTIVE_STATUSES:
                connection.rollback()
                raise PermissionError("execution claim is no longer active")
            # The owner is releasing its claim. Cancellation must win in this
            # transaction: a cancelled queued execution cannot be claimed again.
            if bool(row["cancel_requested"]):
                if _turn_kept_running(_object(row["checkpoint_json"])) and self._requeue_abandoned_provider_work(
                    connection, row, cancelled_error="execution cancelled during worker handover",
                ):
                    # The handover left the turn running: a Worker stops it first.
                    connection.commit()
                    self._notify("execution", "control")
                    return self.snapshot(execution_id)
                self._finalize_released_cancellation(
                    connection, row,
                    error="execution cancelled before recovery requeue",
                    details={
                        "reason": "recovery requeue after cancellation",
                        "previous_worker_id": worker_id,
                    },
                )
                connection.commit()
                self._notify("execution", "control")
                return self.snapshot(execution_id)
            checkpoint = _object(row["checkpoint_json"])
            if not bool(checkpoint.get("resumable")):
                connection.rollback()
                raise RuntimeError(
                    "execution has no resumable checkpoint"
                )
            connection.execute(
                "\n                UPDATE executions\n                SET status = 'queued', owner_worker_id = '', claim_token = '',\n                    lease_expires_at = NULL, available_at = %s, updated_at = %s\n                WHERE execution_id = %s AND owner_worker_id = %s\n                  AND claim_token = %s\n                ",
                (
                    instant(time.time() + max(
                        0.0,
                        min(3600.0, float(retry_after_seconds or 0)),
                    )),
                    execution_timestamp(),
                    execution_id,
                    worker_id,
                    claim_token,
                ),
            )
            self._event(
                connection,
                execution_id,
                "requeued",
                {
                    "reason": str(reason or "provider recovery deferred"),
                    "previous_worker_id": worker_id,
                    "checkpoint": checkpoint,
                    "retry_after_seconds": max(
                        0.0,
                        min(3600.0, float(retry_after_seconds or 0)),
                    ),
                },
            )
            connection.commit()
        self._notify("execution", "control")
        return self.snapshot(execution_id)

    def unregister_worker(self, worker_id: str) -> bool:
        clean_worker_id = str(worker_id or "").strip()
        if not clean_worker_id:
            raise ValueError("worker_id is required")
        with self._connect() as connection:
            worker = connection.execute(
                'SELECT state FROM execution_workers WHERE worker_id = %s FOR UPDATE',
                (clean_worker_id,),
            ).fetchone()
            if worker is None:
                connection.rollback()
                return False
            if str(worker["state"]) != "offline":
                connection.rollback()
                raise RuntimeError(
                    f"cannot unregister worker in state {worker['state']}"
                )
            active = connection.execute(
                "\n                SELECT COUNT(*) AS count\n                FROM executions\n                WHERE owner_worker_id = %s\n                  AND status IN ('claimed', 'running')\n                ",
                (clean_worker_id,),
            ).fetchone()
            if int(active["count"] if active else 0):
                connection.rollback()
                raise RuntimeError(
                    "cannot unregister worker with active executions"
                )
            connection.execute(
                'DELETE FROM execution_workers WHERE worker_id = %s',
                (clean_worker_id,),
            )
            connection.commit()
        self._notify("execution")
        return True

    @staticmethod
    def _with_accounted_usage(row: Any, result: dict[str, Any]) -> dict[str, Any]:
        """Add what the execution counted for its Provider requests: the turn's usage."""
        accounted = _object(row["usage_accounted_json"])
        return {**result, "accounted_usage": dict(accounted)} if accounted else result

    def _finalize_released_cancellation(
        self,
        connection: Any,
        row: Any,
        *,
        error: str,
        details: dict[str, Any],
    ) -> None:
        """Commit cancellation only when no live claim must finish it first.

        Callers establish that boundary (owner release, expired lease, or
        unowned queued work) inside the same write transaction.
        """
        execution_id = str(row["execution_id"])
        checkpoint = _object(row["checkpoint_json"])
        result = self._with_accounted_usage(row, ExecutionResult(
            execution_id=execution_id,
            status="cancelled",
            error=error,
            backend_session_id=str(checkpoint.get("backend_session_id") or ""),
        ).to_payload())
        now = execution_timestamp()
        connection.execute(
            "\n            UPDATE executions\n            SET status = 'cancelled', cancel_requested = '1',\n                result_json = %s, error = %s, owner_worker_id = '',\n                claim_token = '', lease_expires_at = NULL, updated_at = %s\n            WHERE execution_id = %s\n            ",
            (_json(result), error, now, execution_id),
        )
        connection.execute(
            "\n            UPDATE execution_input_controls\n            SET status = 'failed',\n                error = 'provider_turn_finished_before_delivery', updated_at = %s\n            WHERE execution_id = %s AND status = 'pending'\n            ",
            (now, execution_id),
        )
        self._event(connection, execution_id, "cancelled", details)

    def _requeue_abandoned_provider_work(
        self, connection: Any, row: Any, *,
        cancelled_error: str = "execution cancellation finalized after worker lease expired",
    ) -> bool:
        """A lost Worker's Provider work is interrupted before its execution ends.

        The Provider runs a turn outside the Worker (Codex app server, Claude
        Code daemon, DSH process, Kimi server), so it keeps running when its
        Worker dies.  A turn or compaction that cannot be resumed (cancelled,
        or its turn not yet confirmed by the Provider) would otherwise end
        while its Provider work goes on, and the Resident would look idle.
        It returns to the queue marked ``abandoned``: the next Worker only
        interrupts that work and then ends the execution as failed (or
        cancelled).  The execution stays active until then.
        """

        checkpoint = _object(row["checkpoint_json"])
        status = str(row["status"])
        cancel_requested = bool(row["cancel_requested"])
        abandoned = checkpoint.get("abandoned")
        if isinstance(abandoned, dict):
            if status == "running":
                # The Worker sent to interrupt was lost too: end it as decided.
                details = {"reason": "worker lost while interrupting abandoned provider work",
                           "previous_worker_id": str(row["owner_worker_id"] or "")}
                if abandoned.get("status") == "cancelled":
                    self._finalize_released_cancellation(
                        connection, row, error=str(abandoned.get("error") or "cancelled"), details=details,
                    )
                    return True
                return False
            # Waiting to be interrupted: a cancellation does not end it early.
            if status != "queued" or not cancel_requested:
                return False
            abandoned = {**abandoned, "status": "cancelled"}
        else:
            if status != "running" and not _turn_kept_running(checkpoint):
                return False
            if not cancel_requested and checkpoint.get("resumable"):
                return False
            # Only a Provider turn or compaction can still be running for this
            # execution; the Worker may have died before it learned any id.
            request = _object(row["request_json"])
            if (
                str(request.get("service_type") or "") != "agent_session"
                or str(request.get("operation") or "") not in {"turn", "compact"}
            ):
                return False
            abandoned = {
                "status": "cancelled" if cancel_requested else "failed",
                "error": (
                    cancelled_error if cancel_requested else
                    "execution worker lease expired after backend execution started"
                ),
                "previous_worker_id": str(row["owner_worker_id"] or ""),
            }
        execution_id = str(row["execution_id"])
        connection.execute(
            "\n            UPDATE executions\n            SET status = 'queued', cancel_requested = '0', checkpoint_json = %s,\n                owner_worker_id = '', claim_token = '', lease_expires_at = NULL,\n                updated_at = %s\n            WHERE execution_id = %s\n            ",
            (_json({**checkpoint, "resumable": False, "abandoned": abandoned}), execution_timestamp(), execution_id),
        )
        self._event(
            connection,
            execution_id,
            "requeued",
            {
                "reason": "worker lost; interrupt its provider work before ending",
                "previous_worker_id": str(row["owner_worker_id"] or ""),
                "abandoned": abandoned,
            },
        )
        return True

    def _recover_expired(self, connection: Any) -> int:
        # A row another transaction holds is being renewed or recovered
        # elsewhere; the next maintenance pass looks at it again.
        rows = connection.execute(
            "\n            SELECT execution_id, owner_worker_id, status, cancel_requested,\n                   checkpoint_json, request_json, usage_accounted_json\n            FROM executions\n            WHERE (status IN ('claimed', 'running')\n              AND lease_expires_at IS NOT NULL\n              AND lease_expires_at < %s) FOR UPDATE SKIP LOCKED\n            ",
            (instant(time.time()),),
        ).fetchall()
        for row in rows:
            execution_id = str(row["execution_id"])
            if self._requeue_abandoned_provider_work(connection, row):
                continue
            if bool(row["cancel_requested"]):
                self._finalize_released_cancellation(
                    connection, row,
                    error=(
                        "execution cancellation finalized after worker lease expired"
                    ),
                    details={
                        "reason": (
                            "worker lease expired after cancellation"
                        ),
                        "previous_worker_id": row["owner_worker_id"],
                    },
                )
                continue
            checkpoint = _object(row["checkpoint_json"])
            resumable = bool(checkpoint.get("resumable"))
            if str(row["status"]) == "running" and not resumable:
                error = (
                    "execution worker lease expired after backend execution "
                    "started"
                )
                result = self._with_accounted_usage(row, ExecutionResult(
                    execution_id=execution_id,
                    status="failed",
                    error=error,
                ).to_payload())
                connection.execute(
                    "\n                    UPDATE executions\n                    SET status = 'failed', result_json = %s, error = %s,\n                        owner_worker_id = '', claim_token = '',\n                        lease_expires_at = NULL, updated_at = %s\n                    WHERE execution_id = %s\n                    ",
                    (_json(result), error, execution_timestamp(), execution_id),
                )
                self._event(
                    connection,
                    execution_id,
                    "failed",
                    {
                        "reason": "worker lease expired after start",
                        "previous_worker_id": row["owner_worker_id"],
                    },
                )
                continue
            connection.execute(
                "\n                UPDATE executions\n                SET status = 'queued', owner_worker_id = '', claim_token = '',\n                    lease_expires_at = NULL, updated_at = %s\n                WHERE execution_id = %s\n                ",
                (execution_timestamp(), execution_id),
            )
            self._event(
                connection,
                execution_id,
                "requeued",
                {
                    "reason": (
                        "worker lease expired with resumable provider turn"
                        if checkpoint
                        else "worker lease expired"
                    ),
                    "previous_worker_id": row["owner_worker_id"],
                    "checkpoint": checkpoint,
                },
            )
        return len(rows)

    def _claim_candidate(
        self, connection: Any, row: Any, request: dict[str, Any], *,
        worker_id: str, token: str, lease_expires_at: Any, now: str,
    ) -> bool:
        """Take one queued execution unless a concurrent claim took it or its binding."""
        binding = str((request.get("session") or {}).get("binding_id") or "")
        if binding:
            if not self._try_binding_claim(connection, binding):
                return False
            # A claim of this binding that committed after the admission read
            # is visible now that the binding is reserved.
            if any(
                str((active.get("session") or {}).get("binding_id") or "") == binding
                for _execution_id, active in self._active_scheduling_requests(connection)
            ):
                return False
        # A row another claim is taking is skipped instead of waited for.
        return connection.execute(
            "\n            UPDATE executions\n            SET status = 'claimed', owner_worker_id = %s, claim_token = %s,\n                lease_expires_at = %s, available_at = 'epoch'::timestamptz,\n                attempts = attempts + 1, updated_at = %s\n            WHERE execution_id = (\n                SELECT execution_id FROM executions\n                WHERE execution_id = %s AND status = 'queued' AND cancel_requested = '0'\n                FOR UPDATE SKIP LOCKED\n            )\n            ",
            (worker_id, token, lease_expires_at, now, str(row["execution_id"])),
        ).rowcount == 1

    def claim(
        self,
        *,
        worker_id: str,
        capabilities: list[str],
        lease_seconds: int | None = None,
    ) -> dict[str, Any]:
        supported = {
            str(item or "").strip()
            for item in capabilities
            if str(item or "").strip()
        }
        if not supported:
            raise ValueError("worker must support at least one capability")
        duration = max(15, int(lease_seconds or self.default_lease_seconds))
        # Claims run side by side. Expired leases and stale Workers are
        # recovered by maintain(); capacity, quota and dispatch pacing are
        # read without a lock, so concurrent claims may briefly exceed them.
        # Only the execution row (claimed once) and its session binding (one
        # execution at a time) are decided atomically.
        with self._connect() as connection:
            now_epoch = time.time()
            worker = connection.execute(
                '\n                SELECT state\n                FROM execution_workers\n                WHERE worker_id = %s\n                ',
                (worker_id,),
            ).fetchone()
            if worker is None:
                connection.commit()
                return {"claimed": False, "reason": "worker_not_registered"}
            if worker["state"] != "active":
                connection.commit()
                # A standby Worker waits for activation; a draining or offline
                # one was retired and leaves.
                return {
                    "claimed": False,
                    "reason": "worker_not_active",
                    "worker_state": str(worker["state"]),
                }
            connection.execute(
                '\n                UPDATE execution_workers\n                SET heartbeat_at = %s, updated_at = %s\n                WHERE worker_id = %s\n                ',
                (instant(now_epoch), execution_timestamp(), worker_id),
            )
            candidates = connection.execute(
                "\n                SELECT execution_id, capability, actor, priority,\n                       request_json, created_at, checkpoint_json\n                FROM executions\n                WHERE status = 'queued'\n                  AND cancel_requested = '0'\n                  AND available_at <= %s\n                ORDER BY priority DESC, created_at ASC\n                ",
                (instant(now_epoch),),
            ).fetchall()
            deferred_blocked = connection.execute(
                "\n                SELECT 1\n                FROM executions\n                WHERE status = 'queued'\n                  AND cancel_requested = '0'\n                  AND available_at > %s\n                LIMIT 1\n                ",
                (instant(now_epoch),),
            ).fetchone() is not None
            eligible_candidates: list[Any] = []
            # A lost Worker's Provider work waiting to be interrupted makes no
            # model call: it passes quota, capacity and dispatch pacing.
            interrupt_candidates: list[Any] = []
            requests = {row["execution_id"]: _object(row["request_json"]) for row in candidates}
            admission = self._admission_snapshot(connection, list(requests.values()), now=now_epoch)
            blocked = set()
            for row in candidates:
                if row["capability"] not in supported:
                    continue
                interrupt_only = isinstance(_object(row["checkpoint_json"]).get("abandoned"), dict)
                reason = self._admission_reason(requests[row["execution_id"]], 0, interrupt_only, admission)
                if reason != "ready":
                    blocked.add(reason)
                    continue
                (interrupt_candidates if interrupt_only else eligible_candidates).append(row)
            quota_blocked = "quota_reserve" in blocked
            capacity_blocked = "capacity" in blocked
            dispatch_interval_blocked = "dispatch_interval" in blocked
            fair_candidates = self._fair_queue_order(
                connection,
                eligible_candidates,
            )
            token = secrets.token_hex(24)
            now = execution_timestamp()
            candidate = None
            for option in [*interrupt_candidates, *fair_candidates]:
                if self._claim_candidate(
                    connection, option, requests[option["execution_id"]],
                    worker_id=worker_id, token=token,
                    lease_expires_at=instant(now_epoch + duration), now=now,
                ):
                    candidate = option
                    break
            if candidate is None:
                connection.commit()
                return {
                    "claimed": False,
                    "reason": (
                        "quota_reserve" if quota_blocked else
                        "capacity"
                        if capacity_blocked
                        else (
                            "dispatch_interval"
                            if dispatch_interval_blocked
                            else (
                                "deferred"
                                if deferred_blocked
                                else "empty"
                            )
                        )
                    ),
                }
            execution_id = str(candidate["execution_id"])
            capacity_key = self._capacity_key(requests[execution_id])
            interrupt_only = any(candidate is option for option in interrupt_candidates)
            dispatch_state = (
                {"interval_seconds": 0, "next_dispatch_at": 0} if interrupt_only
                else self._reserve_capacity_dispatch(
                    connection,
                    capacity_key,
                    now_epoch=now_epoch,
                )
            )
            if not interrupt_only:
                self._record_actor_dispatch(
                    connection,
                    actor=str(candidate["actor"]),
                    priority=int(candidate["priority"]),
                    now_epoch=now_epoch,
                )
            self._event(
                connection,
                execution_id,
                "claimed",
                {
                    "worker_id": worker_id,
                    "lease_seconds": duration,
                    "capacity_key": capacity_key,
                    "dispatch_interval_seconds": dispatch_state[
                        "interval_seconds"
                    ],
                    "next_dispatch_at": dispatch_state[
                        "next_dispatch_at"
                    ],
                },
            )
            row = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s',
                (execution_id,),
            ).fetchone()
            connection.commit()
        return {
            "claimed": True,
            "claim_token": token,
            "execution": self._row_payload(row),
        }

    def _next_claim_wake_seconds(self, maximum: float) -> float:
        now = time.time()
        # Dev and Release use separate ExecutionService processes backed by the
        # same Scheduler PostgreSQL.  Condition notifications only wake claimers in
        # the submitting process, so a bounded database poll is required for a
        # worker waiting through the peer service endpoint.
        candidates = [
            max(
                0.05,
                min(float(maximum), SHARED_SCHEDULER_POLL_SECONDS),
            )
        ]
        with self._connect() as connection:
            dispatch = connection.execute(
                '\n                SELECT MIN(next_dispatch_at) AS value\n                FROM capacity_dispatch_state\n                WHERE next_dispatch_at > %s\n                ',
                (instant(now),),
            ).fetchone()
            lease = connection.execute(
                "\n                SELECT MIN(lease_expires_at) AS value\n                FROM executions\n                WHERE status IN ('claimed', 'running')\n                  AND lease_expires_at > %s\n                ",
                (instant(now),),
            ).fetchone()
            deferred = connection.execute(
                "\n                SELECT MIN(available_at) AS value\n                FROM executions\n                WHERE status = 'queued' AND available_at > %s\n                ",
                (instant(now),),
            ).fetchone()
        for row in (dispatch, lease, deferred):
            value = (epoch_seconds(row["value"]) or 0) if row is not None else 0
            if value > now:
                candidates.append(max(0.05, value - now))
        return min(candidates)

    def claim_wait(
        self,
        *,
        worker_id: str,
        capabilities: list[str],
        lease_seconds: int | None = None,
        timeout_seconds: float = 15.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        with self._change_notifications():
            while True:
                seen = self._change_generation("claim")
                result = self.claim(
                    worker_id=worker_id,
                    capabilities=capabilities,
                    lease_seconds=lease_seconds,
                )
                if result.get("claimed") or result.get("reason") in {
                    "worker_not_active",
                    "worker_not_registered",
                }:
                    return result
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return result
                self._wait_for_change_signal(
                    "claim", seen, self._next_claim_wake_seconds(remaining),
                )

    @staticmethod
    def _assert_owner(
        row: Any | None,
        *,
        worker_id: str,
        claim_token: str,
    ) -> Any:
        if row is None:
            raise KeyError("execution not found")
        if (
            row["owner_worker_id"] != worker_id
            or row["claim_token"] != claim_token
        ):
            raise PermissionError("execution claim ownership changed")
        return row

    def start(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] == "claimed":
                connection.execute(
                    "\n                    UPDATE executions\n                    SET status = 'running', updated_at = %s\n                    WHERE execution_id = %s\n                    ",
                    (execution_timestamp(), execution_id),
                )
                self._event(
                    connection,
                    execution_id,
                    "started",
                    {"worker_id": worker_id},
                )
            elif row["status"] != "running":
                connection.rollback()
                raise RuntimeError(
                    f"cannot start execution in state {row['status']}"
                )
            connection.commit()
        return self.snapshot(execution_id)

    def heartbeat(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        lease_seconds: int | None = None,
    ) -> dict[str, Any]:
        duration = max(15, int(lease_seconds or self.default_lease_seconds))
        with self._connect() as connection:
            row = self._assert_owner(
                connection.execute(
                    # Only ownership and state: request_json alone is often hundreds of KB,
                    # and this runs for every streamed event / heartbeat.
                    'SELECT execution_id, actor, status, owner_worker_id, claim_token, cancel_requested FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                connection.rollback()
                raise RuntimeError(
                    f"cannot heartbeat execution in state {row['status']}"
                )
            now = execution_timestamp()
            connection.execute(
                '\n                UPDATE executions\n                SET lease_expires_at = %s\n                WHERE execution_id = %s\n                ',
                (instant(time.time() + duration), execution_id),
            )
            connection.execute(
                '\n                UPDATE execution_workers\n                SET heartbeat_at = %s, updated_at = %s\n                WHERE worker_id = %s\n                ',
                (instant(time.time()), now, worker_id),
            )
            connection.commit()
        return {"cancel_requested": bool(row["cancel_requested"])}

    @staticmethod
    def _input_control_payload(row: Any) -> dict[str, Any]:
        payload = _object(row["input_json"])
        return {
            **payload,
            "status": str(row["status"]),
            "error": str(row["error"]),
            "created_at": (iso_time(row["created_at"]) or ""),
            "updated_at": (iso_time(row["updated_at"]) or ""),
        }

    def submit_input_control(self, control: ExecutionInputControl) -> dict[str, Any]:
        return self._submit_turn_control(control)

    def submit_turn_settings_control(self, control: ExecutionTurnSettingsControl) -> dict[str, Any]:
        return self._submit_turn_control(control)

    def _submit_turn_control(
        self,
        control: ExecutionInputControl | ExecutionTurnSettingsControl,
    ) -> dict[str, Any]:
        control.validate()
        is_input = isinstance(control, ExecutionInputControl)
        description = "input" if is_input else "turn settings"
        with self._connect() as connection:
            # The execution row orders this control against its turn's changes.
            execution = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                (control.execution_id,),
            ).fetchone()
            existing_control = connection.execute(
                'SELECT * FROM execution_input_controls WHERE control_id = %s',
                (control.control_id,),
            ).fetchone()
            if existing_control is not None:
                stored = type(control).from_payload(
                    _object(existing_control["input_json"])
                )
                same_request = (
                    stored.execution_id == control.execution_id
                    and stored.actor == control.actor
                    and (stored.input == control.input if is_input else stored.settings == control.settings)
                    and (not is_input or not stored.source_segment_id or not control.source_segment_id
                         or stored.source_segment_id == control.source_segment_id)
                    and (
                        not control.expected_turn_id
                        or stored.expected_turn_id == control.expected_turn_id
                    )
                )
                connection.rollback()
                if not same_request:
                    raise ValueError(f"execution {description} control id was reused")
                return self._input_control_payload(existing_control)
            if execution is None:
                connection.rollback()
                raise KeyError(control.execution_id)
            if str(execution["status"]) not in INPUT_CONTROL_ACCEPTING_STATUSES:
                connection.rollback()
                raise ValueError("execution has no active turn")
            request = _object(execution["request_json"])
            if (
                str(request.get("service_type") or "") != "agent_session"
                or not self._supports_active_turn_input(request)
                or str(request.get("operation") or "") != "turn"
            ):
                connection.rollback()
                raise ValueError(
                    f"active execution does not support {description} updates"
                )
            checkpoint = _object(execution["checkpoint_json"])
            turn_id = str(checkpoint.get("turn_id") or "").strip()
            if control.expected_turn_id and control.expected_turn_id != turn_id:
                connection.rollback()
                raise ValueError("active provider turn changed")
            bound_control = replace(control, expected_turn_id=turn_id).validate()
            now = execution_timestamp()
            connection.execute(
                "\n                INSERT INTO execution_input_controls (\n                    control_id, execution_id, actor, input_json,\n                    expected_turn_id, status, error, created_at, updated_at\n                ) VALUES (%s, %s, %s, %s, %s, 'pending', '', %s, %s)\n                ",
                (
                    bound_control.control_id,
                    bound_control.execution_id,
                    bound_control.actor,
                    _json(bound_control.to_payload()),
                    bound_control.expected_turn_id,
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                control.execution_id,
                "input_control_requested" if is_input else "turn_settings_control_requested",
                {
                    "control_id": control.control_id,
                    "actor": control.actor,
                    "turn_id": turn_id,
                    **({} if is_input else {"fields": sorted(control.settings)}),
                },
            )
            connection.commit()
            self._notify("control")
            row = connection.execute(
                'SELECT * FROM execution_input_controls WHERE control_id = %s',
                (control.control_id,),
            ).fetchone()
        return self._input_control_payload(row)

    def input_control(self, control_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM execution_input_controls WHERE control_id=%s', (control_id,),
            ).fetchone()
            if row is None:
                return None
            return self._input_control_payload(row)


    def complete_input_control(
        self,
        control_id: str,
        *,
        execution_id: str,
        worker_id: str,
        claim_token: str,
        delivered: bool,
        error: str = "",
    ) -> dict[str, Any]:
        target_control_id = str(control_id or "").strip()
        with self._connect() as connection:
            self._assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            row = connection.execute(
                'SELECT * FROM execution_input_controls WHERE control_id = %s AND execution_id = %s',
                (target_control_id, execution_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(target_control_id)
            if str(row["status"]) != "pending":
                connection.commit()
                return self._input_control_payload(row)
            status = "delivered" if delivered else "failed"
            normalized_error = "" if delivered else str(error or "delivery_failed")
            updated_at = execution_timestamp()
            connection.execute(
                '\n                UPDATE execution_input_controls\n                SET status = %s, error = %s, updated_at = %s\n                WHERE control_id = %s\n                ',
                (status, normalized_error, updated_at, target_control_id),
            )
            # Receipts are shared across Scheduler instances; their transient
            # events are not. Wake peer snapshot observers via the same durable
            # execution change marker used by other runtime transitions.
            connection.execute(
                'UPDATE executions SET updated_at = %s WHERE execution_id = %s',
                (updated_at, execution_id),
            )
            payload = _object(row["input_json"])
            is_settings = (
                schema_matches(payload.get("schema"), "tfi.execution-turn-settings-control.v1")
            )
            self._event(
                connection,
                execution_id,
                (
                    "turn_settings_control_delivered"
                    if delivered
                    else "turn_settings_control_failed"
                ) if is_settings else (
                    "input_control_delivered"
                    if delivered
                    else "input_control_failed"
                ),
                {
                    "control_id": target_control_id,
                    "error": normalized_error,
                },
            )
            connection.commit()
            self._notify("control")
            row = connection.execute(
                'SELECT * FROM execution_input_controls WHERE control_id = %s',
                (target_control_id,),
            ).fetchone()
        return self._input_control_payload(row)


    def wait_for_worker_control(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Block until worker control changes or its heartbeat is due."""
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        with self._change_notifications():
            while True:
                seen = self._change_generation("control")
                with self._connect() as connection:
                    row = self._assert_owner(
                        connection.execute(
                            '\n                            SELECT executions.*, execution_workers.state\n                                   AS owner_worker_state\n                            FROM executions\n                            LEFT JOIN execution_workers\n                              ON execution_workers.worker_id =\n                                 executions.owner_worker_id\n                            WHERE executions.execution_id = %s\n                            ',
                            (execution_id,),
                        ).fetchone(),
                        worker_id=worker_id,
                        claim_token=claim_token,
                    )
                worker_state = str(row["owner_worker_state"] or "")
                handover_requested = worker_state not in {
                    "active",
                }
                payload = {
                    "cancel_requested": bool(row["cancel_requested"]),
                    "status": str(row["status"]),
                    "handover_requested": handover_requested,
                }
                controls: list[Any] = []
                if not handover_requested:
                    with self._connect() as connection:
                        controls = connection.execute(
                            "\n                            SELECT * FROM execution_input_controls\n                            WHERE execution_id = %s AND status = 'pending'\n                            ORDER BY created_at ASC, control_id ASC\n                            ",
                            (execution_id,),
                        ).fetchall()
                turn_controls = [
                    self._input_control_payload(control)
                    for control in controls
                ] if not handover_requested else []
                if turn_controls:
                    payload["turn_controls"] = turn_controls
                if (
                    payload["cancel_requested"]
                    or handover_requested
                    or turn_controls
                    or payload["status"] not in ACTIVE_STATUSES
                ):
                    return payload
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return payload
                # Peer Dev/Release commits cannot signal this local Condition.
                # Bound cancel, handover and input delivery latency independently
                # of the Worker's much longer heartbeat interval.
                self._wait_for_change_signal(
                    "control", seen, min(remaining, SHARED_SCHEDULER_POLL_SECONDS),
                )

    def append_event(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(event_type or "").strip():
            raise ValueError("execution event type is required")
        with self._connect() as connection:
            row = self._assert_owner(
                connection.execute(
                    # Only ownership and state: request_json alone is often hundreds of KB,
                    # and this runs for every streamed event / heartbeat.
                    'SELECT execution_id, actor, status, owner_worker_id, claim_token, cancel_requested FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                connection.rollback()
                raise RuntimeError(
                    f"cannot append event in state {row['status']}"
                )
            event = self._event(
                connection,
                execution_id,
                event_type,
                details,
            )
            connection.commit()
            return {
                **event,
                "actor": str(row["actor"]),
                "status": str(row["status"]),
            }

    def checkpoint(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(checkpoint or {})
        if not payload:
            raise ValueError("execution checkpoint is required")
        with self._connect() as connection:
            row = self._assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                connection.rollback()
                raise RuntimeError(
                    f"cannot checkpoint execution in state {row['status']}"
                )
            previous_handover = _object(row["checkpoint_json"]).get("worker_handover")
            if (
                isinstance(previous_handover, dict)
                and previous_handover.get("worker_id") == worker_id
            ):
                payload = self._handover_checkpoint(payload, worker_id)
            elif (
                isinstance(previous_handover, dict)
                and previous_handover.get("turn_id") == payload.get("turn_id")
                and not payload.get("provider_completed")
            ):
                payload["worker_handover"] = dict(previous_handover)
                payload["turn_start_reason"] = "backend_service_update"
            connection.execute(
                '\n                UPDATE executions\n                SET checkpoint_json = %s, updated_at = %s\n                WHERE execution_id = %s\n                ',
                (_json(payload), execution_timestamp(), execution_id),
            )
            self._event(
                connection,
                execution_id,
                "checkpointed",
                {
                    "provider": str(payload.get("provider") or ""),
                    "backend_session_id": str(
                        payload.get("backend_session_id") or ""
                    ),
                    "turn_id": str(payload.get("turn_id") or ""),
                    "resumable": bool(payload.get("resumable")),
                },
            )
            connection.commit()
        return payload

    def reassign_capacity(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        backend_route_id: str,
    ) -> dict[str, Any]:
        route_id = str(backend_route_id or "").strip()
        if not route_id:
            raise ValueError("backend route id is required")
        with self._connect() as connection:
            row = self._assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                connection.rollback()
                raise RuntimeError(
                    f"cannot reassign capacity in state {row['status']}"
                )
            request_payload = _object(row["request_json"])
            if (
                str(request_payload.get("service_type") or "")
                != "agent_session"
            ):
                connection.rollback()
                raise ValueError(
                    "backend route capacity requires an agent session"
                )
            current_key = self._capacity_key(request_payload)
            request_payload = self.accounts.reroute(request_payload, route_id)
            normalized = ExecutionRequest.from_payload(
                request_payload
            ).to_payload()
            next_key = self._capacity_key(normalized)
            if self._quota_paused(connection, normalized):
                connection.rollback()
                return {"accepted": False, "capacity_key": current_key,
                        "changed": False, "reason": "quota_reserve"}
            if next_key == current_key:
                connection.commit()
                return {
                    "accepted": True,
                    "capacity_key": current_key,
                    "changed": False,
                }
            destination_count = 0
            for _active_id, active_request in self._active_scheduling_requests(
                connection, exclude=execution_id,
            ):
                if self._capacity_key(active_request) == next_key:
                    destination_count += 1
            if destination_count >= self._capacity_limit(connection, next_key):
                connection.rollback()
                return {"accepted": False, "capacity_key": current_key,
                        "changed": False, "reason": "capacity_full"}
            connection.execute(
                '\n                UPDATE executions\n                SET request_json = %s, updated_at = %s\n                WHERE execution_id = %s\n                ',
                (_json(normalized), execution_timestamp(), execution_id),
            )
            self._event(
                connection,
                execution_id,
                "capacity_reassigned",
                {
                    "from_capacity_key": current_key,
                    "to_capacity_key": next_key,
                },
            )
            connection.commit()
        return {
            "accepted": True,
            "capacity_key": next_key,
            "changed": True,
        }

    def finish(
        self,
        result: ExecutionResult,
        *,
        worker_id: str,
        claim_token: str,
    ) -> dict[str, Any]:
        result.to_payload()
        with self._connect() as connection:
            row = self._assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (result.execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                connection.rollback()
                raise RuntimeError(
                    f"cannot finish execution in state {row['status']}"
                )
            status = "cancelled" if bool(row["cancel_requested"]) else result.status
            payload = {
                **result.to_payload(),
                "status": status,
            }
            checkpoint = _object(row["checkpoint_json"])
            if isinstance(checkpoint.get("abandoned"), dict) and not payload.get("backend_session_id"):
                # Ended for a lost Worker: name its session, as ending it in place did.
                payload["backend_session_id"] = str(checkpoint.get("backend_session_id") or "")
            now = execution_timestamp()
            accounted_usage = _object(row["usage_accounted_json"])
            if not bool(row["usage_accounted"]):
                remaining_usage = self._remaining_normalized_usage(
                    result.usage,
                    accounted_usage,
                )
                self._record_token_usage(
                    connection,
                    request=_object(row["request_json"]),
                    usage=remaining_usage,
                    occurred_at=now,
                )
                accounted_usage = self._merge_normalized_usage(
                    accounted_usage,
                    remaining_usage,
                )
            # Every Provider request this execution counted, across Workers and
            # recoveries: the turn's usage, whatever the Provider's own report
            # covers (Codex reports its last request and its thread total).
            if accounted_usage:
                payload["accounted_usage"] = dict(accounted_usage)
            connection.execute(
                "\n                UPDATE executions\n                SET status = %s, result_json = %s, error = %s,\n                    owner_worker_id = '', claim_token = '',\n                    lease_expires_at = NULL, usage_accounted = '1',\n                    usage_accounted_json = %s,\n                    updated_at = %s\n                WHERE execution_id = %s\n                ",
                (
                    status,
                    _json(payload),
                    str(payload.get("error") or ""),
                    _json(accounted_usage),
                    now,
                    result.execution_id,
                ),
            )
            connection.execute(
                "\n                UPDATE execution_input_controls\n                SET status = 'failed',\n                    error = 'provider_turn_finished_before_delivery',\n                    updated_at = %s\n                WHERE execution_id = %s AND status = 'pending'\n                ",
                (now, result.execution_id),
            )
            self._event(
                connection,
                result.execution_id,
                status,
                {
                    "worker_id": worker_id,
                    "backend_session_id": result.backend_session_id,
                    "error": result.error,
                    "error_code": result.error_code,
                },
            )
            connection.commit()
        self._notify("execution", "control")
        return self.snapshot(result.execution_id)

    def cancel(
        self,
        execution_id: str,
        *,
        actor: str,
        reason: str = "",
        performed_by: str = "",
        queued_only: bool = False,
    ) -> dict[str, Any]:
        performer = actor_principal(performed_by or actor).id
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                (execution_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(execution_id)
            if row["actor"] != actor:
                connection.rollback()
                raise PermissionError("execution belongs to another actor")
            status = str(row["status"])
            if queued_only and status != "queued":
                connection.commit()
                return self.snapshot(execution_id)
            if status in TERMINAL_STATUSES:
                connection.commit()
                return self.snapshot(execution_id)
            details = {"actor": performer, "request_actor": actor, "reason": str(reason or ""), "identity": OperationIdentity.for_actor(performer).to_dict()}
            checkpoint = _object(row["checkpoint_json"])
            if status == "queued" and isinstance(checkpoint.get("abandoned"), dict):
                # A lost Worker's Provider work waits to be interrupted; the
                # execution still ends only after that, now as cancelled.
                connection.execute(
                    'UPDATE executions SET checkpoint_json = %s, updated_at = %s WHERE execution_id = %s',
                    (_json({**checkpoint, "abandoned": {**checkpoint["abandoned"], "status": "cancelled"}}),
                     execution_timestamp(), execution_id),
                )
                self._event(connection, execution_id, "cancellation_requested", details)
            elif status == "queued" and _turn_kept_running(checkpoint):
                # A Worker handover left this turn running in the Provider; a
                # Worker interrupts it before the execution ends as cancelled.
                abandoned = {
                    "status": "cancelled",
                    "error": str(reason or "execution cancelled during worker handover"),
                    "previous_worker_id": str((checkpoint.get("worker_handover") or {}).get("worker_id") or ""),
                }
                connection.execute(
                    'UPDATE executions SET checkpoint_json = %s, updated_at = %s WHERE execution_id = %s',
                    (_json({**checkpoint, "resumable": False, "abandoned": abandoned}), execution_timestamp(), execution_id),
                )
                self._event(connection, execution_id, "cancellation_requested", details)
            elif status == "queued":
                self._finalize_released_cancellation(
                    connection, row,
                    error=str(reason or "cancelled before execution"),
                    details=details,
                )
            else:
                connection.execute(
                    "\n                    UPDATE executions\n                    SET cancel_requested = '1', updated_at = %s\n                    WHERE execution_id = %s\n                    ",
                    (execution_timestamp(), execution_id),
                )
                self._event(
                    connection,
                    execution_id,
                    "cancellation_requested",
                    details,
                )
            connection.commit()
        self._notify("control")
        return self.snapshot(execution_id)

    def _events(self, connection: Any, execution_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        cursor = max(0, int(after_sequence))
        items = {int(item["sequence"]): dict(item) for item in self._transient_events(execution_id)}
        row = connection.execute("SELECT value FROM execution_store_meta WHERE key=%s", (f"started:{execution_id}",)).fetchone()
        if row:
            event = _object(row["value"])
            items[int(event["sequence"])] = event
        return [items[key] for key in sorted(items) if key > cursor]
    @staticmethod
    def _row_payload(
        row: Any,
        *,
        include_result: bool = True,
    ) -> dict[str, Any]:
        return {
            "execution_id": str(row["execution_id"]),
            "capability": str(row["capability"]),
            "actor": str(row["actor"]),
            "priority": int(row["priority"]),
            "correlation_id": str(row["correlation_id"]),
            "request": _object(row["request_json"]),
            "status": str(row["status"]),
            "owner_worker_id": str(row["owner_worker_id"]),
            "lease_expires_at": (epoch_seconds(row["lease_expires_at"]) or 0.0),
            "available_at": (epoch_seconds(row["available_at"]) or 0.0),
            # A cancelled turn whose lost Worker's Provider work still waits to
            # be interrupted is stopping too.
            "cancel_requested": bool(row["cancel_requested"]) or (
                (_object(row["checkpoint_json"]).get("abandoned") or {}).get("status") == "cancelled"
            ),
            "attempts": int(row["attempts"]),
            "checkpoint": _object(row["checkpoint_json"]),
            "result": _object(row["result_json"]) if include_result else {},
            "error": str(row["error"]),
            "acknowledged_at": (iso_time(row["acknowledged_at"]) or ""),
            "created_at": (iso_time(row["created_at"]) or ""),
            "updated_at": (iso_time(row["updated_at"]) or ""),
        }

    def _queued_execution_state(
        self,
        connection: Any,
        row: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if str(row["status"]) != "queued":
            return {}
        request = (
            payload.get("request")
            if isinstance(payload.get("request"), dict)
            else {}
        )
        capacity_key = self._capacity_key(request)
        admission = self._admission_snapshot(connection, [request])
        capacity_limit = admission["limits"][capacity_key]
        active_capacity_count = admission["active_counts"][capacity_key]
        active_binding_ids = admission["active_bindings"]

        queue_position = 0
        now_epoch = admission["now"]
        queued_candidates = connection.execute(
            "\n            SELECT execution_id, actor, priority, request_json, created_at\n            FROM executions\n            WHERE status = 'queued'\n              AND cancel_requested = '0'\n              AND available_at <= %s\n            ORDER BY priority DESC, created_at ASC\n            ",
            (instant(now_epoch),),
        ).fetchall()
        eligible_candidates: list[Any] = []
        for candidate in queued_candidates:
            candidate_request = _object(candidate["request_json"])
            candidate_session = (
                candidate_request.get("session")
                if isinstance(candidate_request.get("session"), dict)
                else {}
            )
            candidate_binding_id = str(
                candidate_session.get("binding_id") or ""
            )
            if (
                candidate_binding_id
                and candidate_binding_id in active_binding_ids
            ):
                continue
            eligible_candidates.append(candidate)
        for candidate in self._fair_queue_order(
            connection,
            eligible_candidates,
        ):
            candidate_request = _object(candidate["request_json"])
            if self._capacity_key(candidate_request) != capacity_key:
                continue
            queue_position += 1
            if str(candidate["execution_id"]) == str(row["execution_id"]):
                break

        dispatch_state = admission["dispatch"].get(capacity_key, {
            "last_dispatched_at": 0.0, "next_dispatch_at": 0.0, "interval_seconds": 0.0,
        })
        dispatch_interval_remaining_seconds = max(
            0.0,
            dispatch_state["next_dispatch_at"] - now_epoch,
        )
        retry_after_remaining_seconds = max(
            0.0,
            (epoch_seconds(row["available_at"]) or 0.0) - now_epoch,
        )
        reason = self._admission_reason(
            request, epoch_seconds(row["available_at"]),
            isinstance(_object(row["checkpoint_json"]).get("abandoned"), dict), admission,
        )

        return {
            "queue_reason": reason,
            "queue_position": queue_position,
            "capacity_key": capacity_key,
            "capacity_limit": capacity_limit,
            "active_capacity_count": active_capacity_count,
            "last_dispatched_at": dispatch_state["last_dispatched_at"],
            "next_dispatch_at": dispatch_state["next_dispatch_at"],
            "dispatch_interval_seconds": dispatch_state[
                "interval_seconds"
            ],
            "dispatch_interval_remaining_seconds": (
                dispatch_interval_remaining_seconds
            ),
            "retry_after_remaining_seconds": (
                retry_after_remaining_seconds
            ),
        }

    def snapshot(
        self,
        execution_id: str,
        *,
        after_sequence: int = 0,
        include_events: bool = True,
        include_result: bool = True,
    ) -> ExecutionSnapshot:
        # PostgreSQL rows and transient in-memory events form one projection.
        # Events are read after the row and published only after their commit,
        # so a snapshot may show an event its row does not yet show, never the
        # reverse: a reader that stops at a terminal row has every event.
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s',
                (execution_id,),
            ).fetchone()
            if row is None:
                raise KeyError(execution_id)
            payload = self._row_payload(
                row,
                include_result=include_result,
            )
            queue_state = self._queued_execution_state(
                connection,
                row,
                payload,
            )
            events = (
                self._events(
                    connection,
                    execution_id,
                    after_sequence=after_sequence,
                )
                if include_events
                else []
            )
        scheduler_state = {
            key: payload[key]
            for key in (
                "capability",
                "priority",
                "correlation_id",
                "status",
                "owner_worker_id",
                "lease_expires_at",
                "cancel_requested",
                "attempts",
                "acknowledged_at",
                "created_at",
                "updated_at",
            )
        }
        scheduler_state.update(queue_state)
        return {
            "execution_id": execution_id,
            "actor": payload["actor"],
            "status": payload["status"],
            "request": payload["request"],
            "events": events,
            "result": payload["result"],
            "error": payload["error"],
            "scheduler": scheduler_state,
        }

    def states(self, executions: Mapping[str, str]) -> dict[str, Any]:
        """Batch control state; never fetch prompts, results or queue ranking."""
        if not executions:
            return {}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT execution_id,actor,status,cancel_requested,available_at,"
                "NULLIF(checkpoint_json::text,'')::jsonb #>> '{abandoned,status}' AS abandoned_status,"
                "CASE WHEN status='queued' THEN jsonb_build_object("
                "'service_type',request_json::jsonb->'service_type',"
                "'operation',request_json::jsonb->'operation',"
                "'provider',request_json::jsonb->'provider',"
                "'runtime_options',request_json::jsonb->'runtime_options',"
                "'session',request_json::jsonb->'session') END AS scheduling_request "
                "FROM executions WHERE execution_id=ANY(%s)", (list(executions),),
            ).fetchall()
            rows = [row for row in rows if row['actor'] == executions[row['execution_id']]]
            queued = [row for row in rows if row['status'] == 'queued']
            reasons = {}
            if queued:
                requests = {row["execution_id"]: dict(row["scheduling_request"]) for row in queued}
                admission = self._admission_snapshot(connection, list(requests.values()))
                for row in queued:
                    reasons[row["execution_id"]] = self._admission_reason(
                        requests[row["execution_id"]], epoch_seconds(row["available_at"]), bool(row["abandoned_status"]), admission,
                    )
        return {row['execution_id']: {
            'execution_id': row['execution_id'], 'status': row['status'],
            'scheduler': {
                'cancel_requested': bool(row['cancel_requested']) or row['abandoned_status'] == 'cancelled',
                **({'queue_reason': reasons[row['execution_id']]} if row['execution_id'] in reasons else {}),
            },
        } for row in rows}

    @staticmethod
    def _queue_reason(*, paused, deferred, capacity, dispatch_wait, binding_busy):
        if paused:
            return 'quota_reserve'
        if deferred:
            return 'deferred'
        if not capacity:
            return 'capacity'
        if dispatch_wait:
            return 'dispatch_interval'
        if binding_busy:
            return 'session_binding'
        return 'ready'

    def actor_of(self, execution_id: str) -> str:
        """The submitting actor, without reading the whole row."""
        with self._connect() as connection:
            row = connection.execute(
                'SELECT actor FROM executions WHERE execution_id = %s',
                (execution_id,),
            ).fetchone()
        if row is None:
            raise KeyError(execution_id)
        return str(row["actor"])

    def wait_for_change(
        self,
        execution_id: str,
        *,
        after_updated_at: str = "",
        after_sequence: int = 0,
        include_events: bool = True,
        include_result: bool = True,
        timeout_seconds: float = 30.0,
        quiet_reply: bool = False,
    ) -> dict[str, Any]:
        """Return the snapshot once it changes or the wait ends.

        With ``quiet_reply`` a wait that ends without a change answers
        ``unchanged`` instead of the full snapshot: a running turn's waiter
        polls every 0.25 s to check for Stop, and the full row carries the
        whole request.
        """
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))

        def full_snapshot() -> dict[str, Any]:
            return self.snapshot(
                execution_id,
                after_sequence=after_sequence,
                include_events=include_events,
                include_result=include_result,
            )

        with self._change_notifications():
            while True:
                if not after_updated_at:
                    return full_snapshot()
                seen = self._change_generation("execution")
                # Every append_event wakes every waiter in this process.  Decide
                # from status, updated_at and the in-memory events first; the full
                # row carries the whole request (often hundreds of KB).
                with self._connect() as connection:
                    row = connection.execute(
                        'SELECT status, updated_at FROM executions WHERE execution_id = %s',
                        (execution_id,),
                    ).fetchone()
                if row is None:
                    raise KeyError(execution_id)
                has_events = include_events and any(
                    int(item.get("sequence") or 0) > max(0, int(after_sequence))
                    for item in self._transient_events(execution_id)
                )
                remaining = deadline - time.monotonic()
                if (
                    str(row["status"] or "") in TERMINAL_STATUSES
                    or (iso_time(row["updated_at"]) or "") != after_updated_at
                    or has_events
                ):
                    return full_snapshot()
                if remaining <= 0:
                    if quiet_reply:
                        return {
                            "execution_id": execution_id,
                            "status": str(row["status"] or ""),
                            "unchanged": True,
                        }
                    return full_snapshot()
                # Dev/Release and rolling generations share PostgreSQL, not this
                # process's Condition. Observe peer commits within the same
                # bounded interval as admission, including quiet cancellations.
                self._wait_for_change_signal(
                    "execution", seen, min(remaining, SHARED_SCHEDULER_POLL_SECONDS),
                )

    def acknowledge(self, execution_id: str, *, actor: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT actor, status, acknowledged_at FROM executions WHERE execution_id = %s FOR UPDATE',
                (execution_id,),
            ).fetchone()
            if row is None or str(row["actor"]) != str(actor or ""):
                connection.rollback()
                raise KeyError(execution_id)
            if str(row["status"]) not in TERMINAL_STATUSES:
                connection.rollback()
                raise RuntimeError("only terminal executions can be acknowledged")
            acknowledged_at = (iso_time(row["acknowledged_at"]) or "") or execution_timestamp()
            connection.execute(
                "\n                UPDATE executions\n                SET request_json = '{}', checkpoint_json = '{}',\n                    acknowledged_at = %s, updated_at = %s\n                WHERE execution_id = %s\n                ",
                (acknowledged_at, acknowledged_at, execution_id),
            )
            self._event(
                connection,
                execution_id,
                "acknowledged",
                {},
            )
            connection.commit()
        return self.snapshot(execution_id, include_result=False)

    def prune_terminal(
        self,
        *,
        older_than_epoch: float,
        unacknowledged_older_than_epoch: float | None = None,
    ) -> int:
        """Delete terminal work items: acknowledged ones past ``older_than_epoch``,
        unacknowledged ones past a week. The trajectory lives in AgentData."""

        def stamp(epoch: float) -> str:
            return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z")

        threshold = stamp(older_than_epoch)
        unacknowledged_threshold = stamp(
            time.time() - UNACKNOWLEDGED_TERMINAL_RETENTION_SECONDS
            if unacknowledged_older_than_epoch is None
            else unacknowledged_older_than_epoch
        )
        with self._connect() as connection:
            # Dev and Release prune side by side; each deletes the rows it locked.
            rows = connection.execute(
                "\n                SELECT execution_id FROM executions\n                WHERE status IN ('completed', 'failed', 'cancelled')\n                  AND ((acknowledged_at IS NOT NULL AND updated_at < %s) OR updated_at < %s)\n                FOR UPDATE SKIP LOCKED\n                ",
                (threshold, unacknowledged_threshold),
            ).fetchall()
            ids = [str(row["execution_id"]) for row in rows]
            if ids:
                connection.executemany(
                    'DELETE FROM executions WHERE execution_id = %s',
                    [(execution_id,) for execution_id in ids],
                )
                connection.executemany(
                    'DELETE FROM execution_store_meta WHERE key IN (%s, %s)',
                    [(f"started:{execution_id}", f"request_digest:{execution_id}") for execution_id in ids],
                )
            connection.commit()
        with self._lock:
            for execution_id in ids:
                self._transient_event_items.pop(execution_id, None)
        if ids:
            self._notify("execution")
        return len(ids)


    def maintain(self) -> dict[str, int]:
        """Recover expired leases and remove stale worker registrations.

        Claims do not recover leases themselves; ExecutionService runs this
        every few seconds, well inside the shortest lease.
        """
        with self._connect() as connection:
            recovered = self._recover_expired(connection)
            pruned_workers = self._prune_stale_workers(connection)
            connection.commit()
        result = {
            "expired_worker_registrations": pruned_workers,
            "recovered_expired_leases": recovered,
        }
        with self._lock:
            self._last_maintenance_result = result
        if recovered or pruned_workers:
            self._notify("execution", "claim", "control")
        return dict(result)

    def health(self) -> dict[str, Any]:
        with self._lock:
            maintenance = dict(self._last_maintenance_result)
        with self._connect() as connection:
            counts = {
                str(row["status"]): int(row["count"])
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM executions
                    GROUP BY status
                    """
                ).fetchall()
            }
            unacknowledged_terminal = connection.execute(
                """
                SELECT COUNT(*) AS count, MIN(updated_at) AS oldest_updated_at
                FROM executions
                WHERE status IN ('completed', 'failed', 'cancelled')
                  AND acknowledged_at IS NULL
                """
            ).fetchone()
            workers = [
                {
                    "worker_id": str(row["worker_id"]),
                    "build_id": str(row["build_id"]),
                    "state": str(row["state"]),
                    "capabilities": json_value(row["capabilities_json"]),
                    "pid": int(row["pid"]),
                    "heartbeat_at": (epoch_seconds(row["heartbeat_at"]) or 0.0),
                }
                for row in connection.execute(
                    """
                    SELECT worker_id, build_id, state,
                           capabilities_json, pid, heartbeat_at
                    FROM execution_workers
                    ORDER BY worker_id
                    """
                ).fetchall()
            ]
            settings = self._capacity_settings(connection)
            capacity_counts = {
                key: {"active_count": 0, "queued_count": 0}
                for key in {
                    SHARED_CAPACITY_KEY,
                    *self._default_capacity_limits,
                    *settings,
                }
            }
            for row in connection.execute(
                """
                SELECT status, request_json
                FROM executions
                WHERE status IN ('queued', 'claimed', 'running')
                """
            ).fetchall():
                capacity_key = self._capacity_key(
                    _object(row["request_json"])
                )
                partition = capacity_counts.setdefault(
                    capacity_key,
                    {"active_count": 0, "queued_count": 0},
                )
                if str(row["status"]) in ACTIVE_STATUSES:
                    partition["active_count"] += 1
                else:
                    partition["queued_count"] += 1
            dispatch_partitions = {
                str(row["capacity_key"]): {
                    "last_dispatched_at": (epoch_seconds(row["last_dispatched_at"]) or 0.0),
                    "next_dispatch_at": (epoch_seconds(row["next_dispatch_at"]) or 0.0),
                    "interval_seconds": float(row["interval_seconds"]),
                    "remaining_seconds": max(
                        0.0,
                        (epoch_seconds(row["next_dispatch_at"]) or 0.0) - time.time(),
                    ),
                }
                for row in connection.execute(
                    """
                    SELECT capacity_key, last_dispatched_at,
                           next_dispatch_at, interval_seconds
                    FROM capacity_dispatch_state
                    ORDER BY capacity_key
                    """
                ).fetchall()
            }
            account_quotas = {
                key: self._account_quota(connection, key)
                for key in capacity_counts if key in self.quota_capacity_keys
            } if self.quota_reserve_enabled else {}
            thresholds = {key: account_quotas[key]["close_threshold"] if key in account_quotas else self._close_threshold(connection, key)
                          for key in capacity_counts if key in self.quota_capacity_keys}
            limits = {key: self._capacity_limit(connection, key) for key in capacity_counts}
            intervals = {key: self._dispatch_interval_range(connection, key) for key in capacity_counts}
        capacity_partitions = {
            key: {
                "quota_reserve": account_quotas.get(key, {}),
                "close_threshold": thresholds.get(key),
                "limit": limits[key],
                "dispatch_interval_min_seconds": (
                    intervals[key][0]
                ),
                "dispatch_interval_max_seconds": (
                    intervals[key][1]
                ),
                **values,
            }
            for key, values in sorted(capacity_counts.items())
        }
        return {
            "ok": True,
            "schema": SCHEDULER_SCHEMA,
            "database": self.database.display_name,
            "default_partition_limit": self.max_concurrency,
            "capacity_partitions": capacity_partitions,
            "dispatch_interval": {
                "min_seconds": self.dispatch_interval_min_seconds,
                "max_seconds": self.dispatch_interval_max_seconds,
                "partitions": dispatch_partitions,
            },
            "total_capacity": sum(
                partition["limit"]
                for partition in capacity_partitions.values()
            ),
            "active_count": sum(counts.get(status, 0) for status in ACTIVE_STATUSES),
            "status_counts": counts,
            "unacknowledged_terminal_count": int(
                unacknowledged_terminal["count"] if unacknowledged_terminal else 0
            ),
            "oldest_unacknowledged_terminal_at": iso_time(
                unacknowledged_terminal["oldest_updated_at"] if unacknowledged_terminal else None
            ) or "",
            "workers": workers,
            "expired_worker_registrations": maintenance[
                "expired_worker_registrations"
            ],
            "recovered_expired_leases": maintenance[
                "recovered_expired_leases"
            ],
        }


class SchedulerClient:
    """Worker-side Scheduler protocol client."""

    def __init__(
        self,
        *,
        worker_id: str,
        build_id: str,
        capabilities: list[str],
        pid: int = 0,
        service_client: ExecutionServiceClient | None = None,
    ) -> None:
        self.worker_id = str(worker_id or "").strip()
        self.build_id = str(build_id or "").strip()
        self.capabilities = sorted(
            {
                str(item or "").strip()
                for item in capabilities
                if str(item or "").strip()
            }
        )
        self.pid = int(pid or 0)
        # A Worker learns of an absent service at once: it fences its claims
        # by its lease deadline and retries on its own schedule.
        self.service = service_client or ExecutionServiceClient(restart_wait=0)

    def register(self, state: str = "active") -> dict[str, Any]:
        result = self.service.request(
            "scheduler.worker.register",
            {
                "worker_id": self.worker_id,
                "build_id": self.build_id,
                "capabilities": self.capabilities,
                "pid": self.pid,
                "state": state,
            },
        )
        return result if isinstance(result, dict) else {}

    def requeue(
        self,
        execution_id: str,
        claim_token: str,
        reason: str,
        *,
        retry_after_seconds: float = 0,
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.requeue",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "reason": str(reason or "provider recovery deferred"),
                "retry_after_seconds": max(
                    0.0,
                    float(retry_after_seconds or 0),
                ),
            },
        )
        return result if isinstance(result, dict) else {}

    def prepare_handover(
        self, execution_id: str, claim_token: str,
    ) -> dict[str, Any]:
        result = self.service.request("scheduler.handover.prepare", {
            "execution_id": execution_id,
            "worker_id": self.worker_id,
            "claim_token": claim_token,
        })
        return result if isinstance(result, dict) else {}

    def set_state(self, state: str) -> None:
        self.service.request(
            "scheduler.worker.state",
            {"worker_id": self.worker_id, "state": state},
        )

    def handover(self) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.worker.handover",
            {"worker_id": self.worker_id},
        )
        return result if isinstance(result, dict) else {}

    def unregister(self) -> bool:
        result = self.service.request(
            "scheduler.worker.unregister",
            {"worker_id": self.worker_id},
        )
        return bool(
            isinstance(result, dict) and result.get("removed")
        )

    def claim(
        self,
        lease_seconds: int = 270,
        *,
        wait_seconds: float = 15.0,
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.claim.wait",
            {
                "worker_id": self.worker_id,
                "capabilities": self.capabilities,
                "lease_seconds": lease_seconds,
                "timeout_seconds": max(0.1, float(wait_seconds)),
            },
            timeout=max(6.0, float(wait_seconds) + 6.0),
        )
        return result if isinstance(result, dict) else {}

    def start(self, execution_id: str, claim_token: str) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.start",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
            },
        )
        return result if isinstance(result, dict) else {}

    def heartbeat(
        self,
        execution_id: str,
        claim_token: str,
        lease_seconds: int = 270,
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.heartbeat",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "lease_seconds": lease_seconds,
            },
            timeout=CONTROL_REQUEST_TIMEOUT_SECONDS,
        )
        return result if isinstance(result, dict) else {}

    def wait_for_control(
        self,
        execution_id: str,
        claim_token: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.control.wait",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "timeout_seconds": max(0.1, float(timeout_seconds)),
            },
            timeout=max(6.0, float(timeout_seconds) + 6.0),
        )
        return result if isinstance(result, dict) else {}

    def complete_input_control(
        self,
        execution_id: str,
        claim_token: str,
        control_id: str,
        *,
        delivered: bool,
        error: str = "",
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.input.complete",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "control_id": control_id,
                "delivered": bool(delivered),
                "error": str(error or ""),
            },
        )
        return result if isinstance(result, dict) else {}

    def emit(
        self,
        execution_id: str,
        claim_token: str,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.service.request(
            "scheduler.event",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "event_type": event_type,
                "details": details or {},
            },
        )

    def checkpoint(
        self,
        execution_id: str,
        claim_token: str,
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.checkpoint",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "checkpoint": dict(checkpoint),
            },
        )
        return result if isinstance(result, dict) else {}

    def reassign_capacity(
        self,
        execution_id: str,
        claim_token: str,
        backend_route_id: str,
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.capacity.reassign",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "backend_route_id": backend_route_id,
            },
        )
        return result if isinstance(result, dict) else {}

    def finish(
        self,
        result: ExecutionResult,
        claim_token: str,
    ) -> dict[str, Any]:
        payload = self.service.request(
            "scheduler.finish",
            {
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "result": result.to_payload(),
            },
        )
        return payload if isinstance(payload, dict) else {}
