"""Token usage: normalized Provider usage and the hourly ledger per account and Resident."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from management_common.database_time import decode_times, instant, iso_time

from .contracts import TOKEN_USAGE_KEYS, execution_timestamp
from .scheduler_changes import SchedulerChanges
from .scheduler_rows import ACTIVE_STATUSES, assert_owner, compact_json, json_object


TOKEN_USAGE_SCHEMA = "tfi.execution-token-usage.v1"
RESIDENT_ID_RE = re.compile(r"^resident_[0-9a-f]{12}$")


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


def merge_normalized_usage(
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


def remaining_normalized_usage(
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


class TokenUsage:
    """The hourly token usage ledger and the reads it serves."""

    def __init__(self, accounts: Any, *, changes: SchedulerChanges) -> None:
        self.accounts = accounts
        self.changes = changes

    def record_token_usage(
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
        with self.changes.connect() as connection:
            row = assert_owner(
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
                    compact_json(normalized),
                ),
            ).rowcount
            if not inserted:
                connection.commit()
                return {"recorded": False, "duplicate": True}
            self.record_token_usage(
                connection,
                request=json_object(row["request_json"]),
                usage=normalized,
                occurred_at=observed_at,
            )
            accounted = merge_normalized_usage(
                json_object(row["usage_accounted_json"]),
                normalized,
            )
            connection.execute(
                '\n                UPDATE executions\n                SET usage_accounted_json = %s, updated_at = %s\n                WHERE execution_id = %s\n                ',
                # The hour may be an earlier one; the execution changed now.
                (compact_json(accounted), execution_timestamp(), execution_id),
            )
            connection.commit()
        return {"recorded": True, "duplicate": False}

    def usage_watermark(self, key: str) -> str:
        """How far a source of requests outside Executions was counted ('' before the first time)."""
        with self.changes.connect() as connection:
            row = connection.execute(
                'SELECT value FROM execution_store_meta WHERE key = %s', (key,),
            ).fetchone()
        return str(row["value"]) if row else ""

    def latest_runtime_root(self, binding_ids: list[str]) -> str:
        """The runtime directory the latest kept request of these bindings named ('' when none)."""
        with self.changes.connect() as connection:
            row = connection.execute(
                "SELECT request_json::jsonb #>> '{runtime_options,runtime_root}' AS runtime_root FROM executions "
                "WHERE request_json::jsonb #>> '{session,binding_id}' = ANY(%s) ORDER BY created_at DESC LIMIT 1",
                (list(binding_ids),),
            ).fetchone()
        return str(row["runtime_root"] or "") if row else ""

    def earliest_unfinished_execution(self, resident_id: str) -> str:
        """When the oldest unfinished Execution of a Resident was accepted ('' when none)."""
        with self.changes.connect() as connection:
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
        with self.changes.connect() as connection:
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
                self.record_token_usage(connection, request=request, usage=usage, occurred_at=occurred_at)
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
        with self.changes.connect() as connection:
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
