"""Admission: whether queued work may be claimed now, and in which fair order."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any

from management_common.database_time import epoch_seconds, instant

from .contracts import execution_timestamp
from .scheduler_capacity import (
    ActiveRequests,
    SchedulerCapacity,
    capacity_settings,
    record_dispatch_row,
)
from .scheduler_quota import SchedulerQuota
from .scheduler_rows import json_object


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


def record_actor_dispatch(
    connection: Any,
    *,
    actor: str,
    priority: int,
    now_epoch: float,
) -> None:
    record_dispatch_row(
        connection, "execution_actor_dispatch_state", {"priority": priority, "actor": actor},
        {"last_dispatched_at": instant(now_epoch), "updated_at": execution_timestamp()},
    )


class Admission:
    """Admission inputs, reasons and the fair order of queued work."""

    def __init__(self, accounts: Any, *, capacity: SchedulerCapacity, quota: SchedulerQuota,
                 active_requests: ActiveRequests) -> None:
        self.accounts = accounts
        self.capacity = capacity
        self.quota = quota
        self.active_requests = active_requests

    def admission_snapshot(self, connection, requests, *, now=None):
        """Shared inputs for claim, a detailed queue view and batched states."""
        now = time.time() if now is None else now
        active_counts = defaultdict(int)
        active_bindings = set()
        for _, request in self.active_requests.scheduling_requests(connection):
            active_counts[self.capacity.capacity_key(request)] += 1
            binding = str((request.get("session") or {}).get("binding_id") or "")
            if binding:
                active_bindings.add(binding)
        capacities = {self.capacity.capacity_key(request) for request in requests}
        bindings = {str((request.get("session") or {}).get("binding_id") or "") for request in requests}
        settings = capacity_settings(connection)
        defaults = self.capacity.default_capacity_limits
        limits = {key: int(settings[key]["limit_value"]) if key in settings else defaults.get(key, self.capacity.max_concurrency) for key in capacities}
        dispatch = {row["capacity_key"]: {name: float(row[name]) if name == "interval_seconds" else epoch_seconds(row[name]) for name in ("last_dispatched_at", "next_dispatch_at", "interval_seconds")} for row in connection.execute(
            "SELECT * FROM capacity_dispatch_state WHERE capacity_key=ANY(%s)", (list(capacities),),
        ).fetchall()}
        quota_keys = capacities & self.quota.quota_capacity_keys if self.quota.quota_reserve_enabled else set()
        quotas, overrides = self.quota.quota_states(connection, quota_keys, bindings, now=now)
        from .account_registrations import registrations
        disabled = {row["account_id"] for row in registrations() if not row["enabled"]}
        return {"now": now, "active_counts": active_counts, "active_bindings": active_bindings,
                "limits": limits, "dispatch": dispatch, "quotas": quotas, "overrides": overrides,
                "disabled_accounts": disabled}

    def admission_reason(self, request, available_at, interrupt_only, admission):
        account = self.accounts.for_request(request)
        if not interrupt_only and account and account.account_id in admission.get("disabled_accounts", set()):
            return "account_disabled"
        capacity = self.capacity.capacity_key(request)
        binding = str((request.get("session") or {}).get("binding_id") or "")
        paused = admission["quotas"].get(capacity, {}).get("automatic_paused", False)
        return _queue_reason(
            paused=not interrupt_only and paused and binding not in admission["overrides"],
            deferred=float(available_at) > admission["now"],
            capacity=interrupt_only or admission["limits"][capacity] > admission["active_counts"][capacity],
            dispatch_wait=not interrupt_only and admission["dispatch"].get(capacity, {}).get("next_dispatch_at", 0) > admission["now"],
            binding_busy=binding in admission["active_bindings"],
        )

    @staticmethod
    def fair_queue_order(
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

    def queued_execution_state(
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
        capacity_key = self.capacity.capacity_key(request)
        admission = self.admission_snapshot(connection, [request])
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
            candidate_request = json_object(candidate["request_json"])
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
        for candidate in self.fair_queue_order(
            connection,
            eligible_candidates,
        ):
            candidate_request = json_object(candidate["request_json"])
            if self.capacity.capacity_key(candidate_request) != capacity_key:
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
        reason = self.admission_reason(
            request, epoch_seconds(row["available_at"]),
            isinstance(json_object(row["checkpoint_json"]).get("abandoned"), dict), admission,
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
