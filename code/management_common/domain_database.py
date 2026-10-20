"""Connection and DDL lifecycle for domain-owned relational tables."""
from __future__ import annotations

import threading
from contextlib import contextmanager

from .portal_database import portal_database


class DomainDatabase:
    def __init__(self, schema: str, owner: str, ddl: str):
        self.database = portal_database(schema=schema)
        self.owner = owner
        self.ddl = ddl
        self._ready = False
        self._lock = threading.Lock()

    def ensure(self):
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with self.database.transaction() as connection:
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':' || %s, 0))", (self.owner + ':ddl',))
                connection.executescript(self.ddl)
            self._ready = True

    @contextmanager
    def transaction(self, key: str | None = None):
        self.ensure()
        with self.database.transaction() as connection:
            if key is not None:
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':' || %s, 0))", (self.owner + ':' + key,))
            yield connection

    @contextmanager
    def read(self):
        self.ensure()
        with self.database.read_current() as connection:
            yield connection
