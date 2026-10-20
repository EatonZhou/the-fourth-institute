from __future__ import annotations

import os
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TypeVar
from urllib.parse import urlsplit, urlunsplit


T = TypeVar("T")
_AFTER_COMMIT: ContextVar[dict] = ContextVar("database_after_commit", default={})


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class _SharedPool:
    __slots__ = ("pool", "references")

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.references = 1


_POOL_REGISTRY_LOCK = threading.Lock()
_POOL_REGISTRY: dict[tuple[int, str, str, int, int, float, bool], _SharedPool] = {}
_READY_SCHEMAS: set[tuple[int, str, str]] = set()


def close_all_database_pools() -> None:
    """Close every PostgreSQL pool owned by the current process.

    This is primarily a deterministic teardown hook for isolated test runs.
    Service code should continue to close its individual database objects.
    """

    process_id = os.getpid()
    with _POOL_REGISTRY_LOCK:
        pools = {
            shared.pool
            for key, shared in list(_POOL_REGISTRY.items())
            if key[0] == process_id
        }
        for key in [key for key in _POOL_REGISTRY if key[0] == process_id]:
            del _POOL_REGISTRY[key]
    for pool in pools:
        pool.close()


class PostgresRow:
    """A psycopg row supporting positional and named field access.

    Portal stores use both ``row[0]`` and ``row["name"]``; a few also copy rows
    with ``tuple(row)`` or ``dict(row)``.  The database boundary provides that
    stable row contract.
    """

    __slots__ = ("_columns", "_index", "_values")

    def __init__(self, columns: tuple[str, ...], values: Sequence[object]) -> None:
        self._columns = columns
        self._index = {name: offset for offset, name in enumerate(columns)}
        self._values = tuple(values)

    def __getitem__(self, key: int | str | slice) -> Any:
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PostgresRow):
            return self._columns == other._columns and self._values == other._values
        if isinstance(other, (tuple, list)):
            return self._values == tuple(other)
        return NotImplemented

    def __repr__(self) -> str:
        return f"PostgresRow({dict(self)!r})"

    def keys(self) -> list[str]:
        return list(self._columns)


def _portal_row_factory(cursor: Any):
    if cursor.description is None:
        return lambda values: PostgresRow((), values)
    columns = tuple(column.name for column in cursor.description)

    def make_row(values: Sequence[object]) -> PostgresRow:
        return PostgresRow(columns, values)

    return make_row


def _display_dsn(dsn: str) -> str:
    parsed = urlsplit(dsn)
    if not parsed.scheme:
        return "postgresql"
    hostname = parsed.hostname or "local-socket"
    netloc = hostname
    if parsed.username:
        netloc = f"{parsed.username}@{netloc}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    # Connection query parameters can contain credentials (for example TLS
    # key paths or provider-specific tokens), so diagnostic names omit them.
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _postgres_modules():
    try:
        import psycopg
        from psycopg_pool import ConnectionPool
    except ImportError as exc:  # pragma: no cover - exercised by deployment checks
        raise RuntimeError(
            "PostgreSQL Lineage storage requires psycopg[binary,pool]"
        ) from exc
    return psycopg, _portal_row_factory, ConnectionPool


class PostgresConnection:
    backend = "postgresql"

    def __init__(self, connection: Any) -> None:
        self.raw = connection

    @property
    def in_transaction(self) -> bool:
        psycopg, _row_factory, _pool = _postgres_modules()
        return self.raw.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    def execute(
        self,
        statement: str,
        parameters: Sequence[object] | None = None,
        *,
        prepare: bool | None = None,
    ) -> Any:
        # prepare=False keeps a statement from being prepared, so PostgreSQL
        # plans it for its actual parameters every time.
        return self.raw.execute(
            statement,
            tuple(parameters or ()),
            prepare=prepare,
        )

    def executemany(
        self,
        statement: str,
        parameters: Sequence[Sequence[object]],
    ) -> None:
        # psycopg exposes executemany() on Cursor, not Connection.  Portal
        # stores use this only for batched mutations and do not consume a
        # result cursor.
        with self.raw.cursor() as cursor:
            cursor.executemany(
                statement,
                parameters,
            )

    def executescript(self, script: str) -> None:
        # PostgreSQL accepts a multi-statement simple query when no parameters
        # are present.
        self.raw.execute(script, prepare=False)

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        # Pool ownership stays with PostgresDatabase; a logical connection does
        # not own the shared process pool.
        return None


_CURRENT_CONNECTIONS: ContextVar[
    dict[tuple[str, str], PostgresConnection] | None
] = ContextVar("postgres_current_connections", default=None)


class _SchemaConnection(PostgresConnection):
    """Schema-bound statements on a connection shared by an atomic transaction."""

    def __init__(self, raw: Any, schema: str) -> None:
        super().__init__(raw)
        self.schema = schema

    def _activate(self) -> None:
        self.raw.execute("SELECT set_config('search_path', %s, true)", (self.schema,))

    def execute(self, statement, parameters=None, *, prepare=None):
        self._activate()
        return super().execute(statement, parameters, prepare=prepare)

    def executemany(self, statement, parameters):
        self._activate()
        return super().executemany(statement, parameters)

    def executescript(self, script):
        self._activate()
        return super().executescript(script)


@contextmanager
def atomic_transaction(*databases: PostgresDatabase) -> Iterator[None]:
    """Commit domain stores in the same database as one physical transaction.

    Owners retain their schemas and normal store methods. Initialization must
    precede this scope; unrelated existing transactions cannot be enlisted.
    """
    if not databases or len({db.dsn for db in databases}) != 1:
        raise ValueError("Atomic transactions require domains in one database")
    active = dict(_CURRENT_CONNECTIONS.get() or {})
    keys = [(db.dsn, db.schema) for db in databases]
    if any(key in active for key in keys):
        raise RuntimeError("Start the atomic transaction before domain operations")
    for db in databases:
        db._connection_pool()
    with databases[0]._connection_pool().connection() as raw:
        with raw.transaction():
            active.update({key: _SchemaConnection(raw, key[1]) for key in keys})
            token = _CURRENT_CONNECTIONS.set(active)
            try:
                yield
            finally:
                _CURRENT_CONNECTIONS.reset(token)


class PostgresDatabase:
    """Process-safe PostgreSQL transaction boundary for one data-domain schema."""

    backend = "postgresql"

    def __init__(
        self,
        dsn: str,
        *,
        schema: str,
        min_size: int | None = None,
        max_size: int | None = None,
    ) -> None:
        self.dsn = str(dsn).strip()
        if not self.dsn:
            raise ValueError("PostgreSQL DSN is required")
        clean_schema = str(schema or "").strip()
        if not _IDENTIFIER.fullmatch(clean_schema):
            raise ValueError(f"Invalid PostgreSQL schema: {schema!r}")
        self.schema = clean_schema
        self.identity = f"{self.dsn}#{self.schema}"
        self.display_name = f"{_display_dsn(self.dsn)}#{self.schema}"
        read_worker = str(os.getenv("PORTAL_LINEAGE_READ_WORKER") or "") == "1"
        # Read workers share one bounded pool across domains. Schema belongs
        # to a transaction/statement, not to a retained physical connection.
        self._shared_schemas = read_worker
        configured_max = int(
            os.getenv("PORTAL_POSTGRES_POOL_SIZE")
            or (2 if read_worker else 16)
        )
        selected_min = 0 if min_size is None else min_size
        selected_max = configured_max if max_size is None else max_size
        # Projection processes execute one read at a time. Apply their limit
        # after the domain factory's explicit size, which otherwise bypasses it.
        if read_worker:
            selected_min = 0
            selected_max = 2
        self._min_size = max(0, int(selected_min))
        self._max_size = max(1, self._min_size, int(selected_max))
        self._max_idle = float(
            os.getenv("PORTAL_POSTGRES_POOL_MAX_IDLE_SECONDS") or ("30" if read_worker else "1")
        )
        if self._max_idle <= 0:
            raise ValueError("PostgreSQL pool max idle seconds must be positive")
        instance = re.sub(
            r"[^a-zA-Z0-9_.-]+",
            "-",
            str(os.getenv("PORTAL_INSTANCE") or "portal"),
        ).strip("-") or "portal"
        self._application_name = (
            f"tfi:{instance}:{'read' if read_worker else self.schema}:{os.getpid()}"
        )[:63]
        self._pool_lock = threading.Lock()
        self._pool: Any = None
        self._pool_pid = 0
        self._pool_key: tuple[int, str, str, int, int, float, bool] | None = None

    def _current_connection(self) -> PostgresConnection | None:
        current = _CURRENT_CONNECTIONS.get()
        return None if current is None else current.get((self.dsn, self.schema))

    def _configure(self, connection: Any) -> None:
        psycopg, row_factory, _pool = _postgres_modules()
        connection.row_factory = row_factory
        connection.execute("SET timezone = 'UTC'")
        connection.execute("SET lock_timeout = '10s'")
        connection.execute("SET statement_timeout = '120s'")
        connection.execute("SET jit = off")
        connection.execute(
            psycopg.sql.SQL("SET search_path TO {}").format(
                psycopg.sql.Identifier("public" if self._shared_schemas else self.schema)
            )
        )
        connection.commit()

    def _ensure_schema(self) -> None:
        psycopg, _row_factory, _pool = _postgres_modules()
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            try:
                connection.execute(
                    psycopg.sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                        psycopg.sql.Identifier(self.schema)
                    )
                )
            except (
                psycopg.errors.DuplicateSchema,
                psycopg.errors.UniqueViolation,
            ):
                # Concurrent first-use CREATE SCHEMA statements can race in
                # PostgreSQL's namespace catalog even with IF NOT EXISTS.
                exists = connection.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname=%s",
                    (self.schema,),
                ).fetchone()
                if exists is None:
                    raise

    def _connection_pool(self) -> Any:
        process_id = os.getpid()
        with self._pool_lock:
            if self._pool is not None and self._pool_pid == process_id:
                if not bool(getattr(self._pool, "closed", False)):
                    return self._pool
                self._pool = None
                self._pool_pid = 0
                self._pool_key = None
            # A fork inherits object references but not a usable pool worker
            # runtime.  Leave the parent's copied registry entry untouched and
            # acquire a pool owned by the child PID.
            if self._pool is not None:
                self._pool = None
                self._pool_pid = 0
                self._pool_key = None
            key = (
                process_id,
                self.dsn,
                "" if self._shared_schemas else self.schema,
                self._min_size,
                self._max_size,
                self._max_idle,
                self._shared_schemas,
            )
            with _POOL_REGISTRY_LOCK:
                schema_key = (process_id, self.dsn, self.schema)
                if schema_key not in _READY_SCHEMAS:
                    self._ensure_schema()
                    _READY_SCHEMAS.add(schema_key)
                shared = _POOL_REGISTRY.get(key)
                if shared is None:
                    _psycopg, row_factory, ConnectionPool = _postgres_modules()
                    pool = ConnectionPool(
                        conninfo=self.dsn,
                        min_size=self._min_size,
                        max_size=self._max_size,
                        max_idle=self._max_idle,
                        kwargs={
                            "row_factory": row_factory,
                            "application_name": self._application_name,
                        },
                        configure=self._configure,
                        open=True,
                        name=f"portal-{self.schema}-{process_id}",
                    )
                    try:
                        pool.wait(timeout=15.0)
                    except BaseException:
                        # A failed acquisition must not leave an unregistered
                        # pool's workers and connections alive.
                        pool.close()
                        raise
                    shared = _SharedPool(pool)
                    _POOL_REGISTRY[key] = shared
                else:
                    shared.references += 1
            self._pool = shared.pool
            self._pool_pid = process_id
            self._pool_key = key
            return self._pool

    @contextmanager
    def _connection(self) -> Iterator[PostgresConnection]:
        current = self._current_connection()
        if current is not None:
            yield current
            return
        pool = self._connection_pool()
        with pool.connection() as raw:
            connection = _SchemaConnection(raw, self.schema) if self._shared_schemas else PostgresConnection(raw)
            active = dict(_CURRENT_CONNECTIONS.get() or {})
            active[(self.dsn, self.schema)] = connection
            token = _CURRENT_CONNECTIONS.set(active)
            try:
                yield connection
            finally:
                _CURRENT_CONNECTIONS.reset(token)

    @contextmanager
    def read_current(self) -> Iterator[PostgresConnection]:
        with self._connection() as connection:
            yield connection

    @contextmanager
    def connect(self) -> Iterator[PostgresConnection]:
        """Open a transactional connection for stores that own commit boundaries."""

        with self._connection() as connection:
            try:
                yield connection
                if connection.in_transaction:
                    connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    @contextmanager
    def read(self) -> Iterator[PostgresConnection]:
        with self._connection() as connection:
            yield connection

    @contextmanager
    def read_transaction(self) -> Iterator[PostgresConnection]:
        current = self._current_connection()
        if current is not None and current.in_transaction:
            yield current
            return
        with self._connection() as connection:
            with connection.raw.transaction():
                yield connection

    @contextmanager
    def snapshot(self) -> Iterator[PostgresConnection]:
        """A read-only transaction whose statements all see one committed snapshot."""

        current = self._current_connection()
        if current is not None and current.in_transaction:
            yield current
            return
        with self._connection() as connection:
            with connection.raw.transaction():
                connection.raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                yield connection

    @contextmanager
    def transaction(self) -> Iterator[PostgresConnection]:
        current = self._current_connection()
        if current is not None and current.in_transaction:
            yield current
            return
        callbacks: list[Callable[[], Any]] = []
        key = (self.dsn, self.schema)
        token = _AFTER_COMMIT.set({**_AFTER_COMMIT.get(), key: callbacks})
        try:
            with self._connection() as connection:
                with connection.raw.transaction():
                    yield connection
        finally:
            _AFTER_COMMIT.reset(token)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logging.getLogger(__name__).exception("Post-commit notification failed")

    def after_commit(self, callback: Callable[[], Any]) -> None:
        callbacks = _AFTER_COMMIT.get().get((self.dsn, self.schema))
        if callbacks is None:
            if self._current_connection() is not None and self._current_connection().in_transaction:
                raise RuntimeError("Post-commit effects require an owned write transaction")
            callback()
        else:
            callbacks.append(callback)

    def write(self, operation: Callable[[PostgresConnection], T]) -> T:
        psycopg, _dict_row, _pool = _postgres_modules()
        deadline = time.monotonic() + 10.0
        delay = 0.005
        while True:
            try:
                with self.transaction() as connection:
                    return operation(connection)
            except (
                psycopg.errors.DeadlockDetected,
                psycopg.errors.LockNotAvailable,
                psycopg.errors.SerializationFailure,
            ):
                # The owner of an existing transaction must retry the whole
                # operation, not a fragment on its now-aborted connection.
                if self._current_connection() is not None:
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(0.1, delay * 2)

    @contextmanager
    def advisory_lock(self, key: int, *, blocking: bool = True, shared: bool = False) -> Iterator[bool]:
        psycopg, _row_factory, _pool = _postgres_modules()
        with psycopg.connect(self.dsn, autocommit=True, connect_timeout=10) as connection:
            connection.execute("SET lock_timeout = '30s'")
            suffix = '_shared' if shared else ''
            if blocking:
                connection.execute(f"SELECT pg_advisory_lock{suffix}(%s)", (key,))
                yield True
            else:
                yield bool(connection.execute(f"SELECT pg_try_advisory_lock{suffix}(%s)", (key,)).fetchone()[0])

    def close(self) -> None:
        with self._pool_lock:
            pool = self._pool
            key = self._pool_key
            pool_pid = self._pool_pid
            self._pool = None
            self._pool_pid = 0
            self._pool_key = None
            if pool is None or key is None or pool_pid != os.getpid():
                return
            close_pool = False
            with _POOL_REGISTRY_LOCK:
                shared = _POOL_REGISTRY.get(key)
                if shared is None or shared.pool is not pool:
                    return
                shared.references -= 1
                if shared.references <= 0:
                    del _POOL_REGISTRY[key]
                    close_pool = True
            if close_pool:
                pool.close()
