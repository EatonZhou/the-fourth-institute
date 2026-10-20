"""Durable native account daily totals, independent of presentation caches."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import threading

from management_common.portal_database import portal_database
from management_common.database_time import instant, epoch_seconds


class AccountUsageStore:
    def __init__(self):
        self.database = portal_database(schema="execution_account_usage")
        self._ready = False
        self._lock = threading.Lock()

    @contextmanager
    def _connection(self):
        if not self._ready:
            with self._lock:
                if not self._ready:
                    with self.database.connect() as connection:
                        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(), 0))")
                        connection.execute("""CREATE TABLE IF NOT EXISTS accounts (
                            provider TEXT NOT NULL, account_id TEXT NOT NULL,
                            observed_at TIMESTAMPTZ NOT NULL,
                            PRIMARY KEY (provider, account_id))""")
                        connection.execute("""CREATE TABLE IF NOT EXISTS daily_usage (
                            provider TEXT NOT NULL, account_id TEXT NOT NULL,
                            day DATE NOT NULL, tokens BIGINT NOT NULL CHECK (tokens >= 0),
                            observed_at TIMESTAMPTZ NOT NULL,
                            PRIMARY KEY (provider, account_id, day))""")
                    self._ready = True
        with self.database.connect() as connection:
            yield connection

    @staticmethod
    def _read(connection, provider, account_id):
        account = connection.execute(
            "SELECT observed_at FROM accounts WHERE provider=%s AND account_id=%s",
            (provider, account_id),
        ).fetchone()
        if account is None:
            return {}
        rows = connection.execute(
            "SELECT day, tokens FROM daily_usage WHERE provider=%s AND account_id=%s ORDER BY day",
            (provider, account_id),
        ).fetchall()
        return {"status": "stale", "observed_at": epoch_seconds(account["observed_at"]),
                "daily_buckets": [{"date": str(row["day"]), "tokens": row["tokens"]} for row in rows]}

    def read(self, provider, account_id):
        with self._connection() as connection:
            return self._read(connection, provider, account_id)

    def save(self, provider, account_id, sample, *, observed_at):
        observed_at = instant(observed_at)
        buckets = sample.get("daily_buckets")
        if sample.get("status") != "ok" or not isinstance(buckets, list):
            raise ValueError("Account usage is incomplete")
        daily = {}
        for bucket in buckets:
            day = date.fromisoformat(bucket["date"]).isoformat()
            tokens = bucket.get("tokens")
            if type(tokens) is not int or tokens < 0 or day in daily:
                raise ValueError("Invalid account usage bucket")
            daily[day] = tokens
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO accounts(provider,account_id,observed_at) VALUES(%s,%s,%s) "
                "ON CONFLICT(provider,account_id) DO UPDATE SET observed_at="
                "GREATEST(accounts.observed_at,EXCLUDED.observed_at)",
                (provider, account_id, observed_at),
            )
            if not daily and connection.execute(
                "SELECT 1 FROM daily_usage WHERE provider=%s AND account_id=%s LIMIT 1",
                (provider, account_id),
            ).fetchone():
                raise ValueError("Account usage unexpectedly omitted all historical days")
            # Missing days are not zero. Only explicitly returned dates are updated;
            # an older response from another service cannot overwrite a newer one.
            connection.executemany(
                "INSERT INTO daily_usage(provider,account_id,day,tokens,observed_at) VALUES(%s,%s,%s,%s,%s) "
                "ON CONFLICT(provider,account_id,day) DO UPDATE SET "
                "tokens=EXCLUDED.tokens,observed_at=EXCLUDED.observed_at "
                "WHERE daily_usage.observed_at <= EXCLUDED.observed_at",
                [(provider, account_id, day, tokens, observed_at) for day, tokens in sorted(daily.items())],
            )
            return {**self._read(connection, provider, account_id), "status": "ok"}
