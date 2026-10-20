"""The general PAR engine: runs the instance of any permission scheme.

It knows trees, entries and stored element positions, nothing about the
Portal.  A decision costs one database statement — the instance version
number, the principal's position and the requested resources' paths — and
then hash lookups in the principal's cached ``Grants``.  The cache holds the
rows that apply to each principal; when the version number differs from the
one the cache was built at, the whole cache is dropped and rebuilt on
demand.  Resource paths are never cached, so a resource that moves needs no
new version.  Every entry is a stored row, the rows the rules generated and
the rows added by hand alike.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence

from .decision import Grants
from .model import DENY, PERMIT, Coverage, Decision, Entry, Node, Tree
from .store import Snapshot, Store

__all__ = ["Coverage", "DENY", "Decision", "Engine", "Entry", "Grants", "Node", "PERMIT", "Snapshot", "Store", "Tree"]


class Engine:
    def __init__(self, trees: Mapping[str, Tree], store: Store, *,
                 clock: Callable[[], float] = time.time, cache_size: int = 4096) -> None:
        self.trees = dict(trees)
        self.store = store
        self.clock = clock
        self._cache: OrderedDict[str, tuple[tuple[str, ...], Grants]] = OrderedDict()
        self._cache_size = cache_size
        self._version: int | None = None
        self._lock = threading.Lock()

    # -- positions -------------------------------------------------------------------

    def a_path(self, action: str) -> tuple[str, ...]:
        return self.trees["A"].element_path(action)

    def path(self, tree: str, element: str, stored: Sequence[str] | None = None) -> tuple[str, ...]:
        """An element's root-to-element path: its stored path, else its declared or default place."""
        if stored:
            return tuple(stored)
        return self.trees[tree].element_path(element)

    # -- grants ----------------------------------------------------------------------

    def _grants(self, principal: str, path: tuple[str, ...], version: int) -> Grants:
        with self._lock:
            if version != self._version:
                # Something a cache could hold changed somewhere: start again.
                self._cache.clear()
                self._version = version
            cached = self._cache.get(principal)
            if cached is not None and cached[0] == path:
                self._cache.move_to_end(principal)
                return cached[1]
        grants = Grants(self.store.entries_for(list(path)), self.a_path)
        with self._lock:
            if version == self._version:
                self._cache[principal] = (path, grants)
                self._cache.move_to_end(principal)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        return grants

    def snapshot(self, principal: str, resources: Iterable[str] = ()) -> Snapshot:
        """What a decision reads, from this engine's scheme."""
        declared = self.trees["R"].nodes
        return self.store.snapshot(principal, [item for item in dict.fromkeys(resources) if item not in declared])

    def grants(self, principal: str, resources: Iterable[str] = (), *,
               snapshot: Snapshot | None = None) -> tuple[tuple[str, ...], Grants, dict[str, tuple[str, ...]]]:
        """The principal's path and Grants, plus the current paths of ``resources``, from one read.

        ``snapshot`` is that read when the caller already made it.
        """
        resources = list(dict.fromkeys(resources))
        if snapshot is None:
            snapshot = self.snapshot(principal, resources)
        path = self.path("P", principal, snapshot.principal)
        grants = self._grants(principal, path, snapshot.version)
        return path, grants, {item: self.path("R", item, snapshot.resources.get(item)) for item in resources}

    def forget(self) -> None:
        with self._lock:
            self._cache.clear()
            self._version = None

    # -- questions -------------------------------------------------------------------

    def decide(self, principal: str, requests: Sequence[tuple[str, str]], *, snapshot: Snapshot | None = None) -> list[Decision]:
        """One decision per ``(action, resource)`` for the principal."""
        if not requests:
            return []
        _path, grants, paths = self.grants(principal, [resource for _action, resource in requests], snapshot=snapshot)
        now = self.clock()
        return [grants.decide(self.a_path(action), paths[resource], now) for action, resource in requests]

    def coverage(self, principal: str, action: str) -> Coverage:
        _path, grants, _paths = self.grants(principal)
        return grants.coverage(self.a_path(action), self.clock())

    def covered(self, principal: str, action: str, *, kind: str = "", ids: Sequence[str] | None = None) -> list[str]:
        """Reverse query: the stored resources the principal may do ``action`` on, filtered in the database."""
        coverage = self.coverage(principal, action)
        return self.store.covered(coverage.permit, coverage.deny, kind=kind, ids=ids)
