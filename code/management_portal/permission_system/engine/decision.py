"""Deciding requests for one principal (thinking_3cbdd375ebf8 §4.3, §12.5).

``Grants`` holds every entry whose P node lies on the principal's path,
indexed by A node.  Each permit carries its holes: the deny entries that can
overlap it.  A decision walks the action's path and the resource's path — a
few hash lookups, independent of how many principals, actions or resources
exist — and checks only the holes of the permit it found.

Why one permit's holes suffice: every deny that covers ``(p, a, r)`` applies
to ``p`` and has its A node on the path of ``a``, like the permit's A node, so
the two A nodes are comparable and the deny is among that permit's holes.
Holes are not narrowed by R nodes, so a resource moving in the R tree never
makes them stale; the decision checks each hole against the current path.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from .model import DENY, PERMIT, Coverage, Decision, Entry


class _Permit:
    __slots__ = ("entry", "holes")

    def __init__(self, entry: Entry, holes: tuple[Entry, ...]) -> None:
        self.entry = entry
        self.holes = holes


class Grants:
    """The entries that apply to one principal, ready for decisions."""

    def __init__(self, entries: Iterable[Entry], a_path) -> None:
        """``a_path(node)`` is the root-to-node path of an A node."""
        entries = list(entries)
        self.entries = tuple(entries)
        denies = [entry for entry in entries if entry.effect == DENY]
        self._denies: dict[str, list[Entry]] = {}
        for entry in denies:
            self._denies.setdefault(entry.a, []).append(entry)
        self._permits: dict[str, list[_Permit]] = {}
        for entry in entries:
            if entry.effect != PERMIT:
                continue
            path = set(a_path(entry.a))
            holes = tuple(deny for deny in denies if deny.a in path or entry.a in a_path(deny.a))
            self._permits.setdefault(entry.a, []).append(_Permit(entry, holes))

    def decide(self, a_path: Sequence[str], r_path: Sequence[str], now: float) -> Decision:
        """Whether the principal may do the action at ``a_path`` on the resource at ``r_path``."""
        actions, resources = set(a_path), set(r_path)
        for a_node in a_path:
            for permit in self._permits.get(a_node, ()):
                entry = permit.entry
                if entry.r not in resources or not entry.live(now):
                    continue
                for deny in permit.holes:
                    if deny.a in actions and deny.r in resources and deny.live(now):
                        return Decision(False, deny=deny)
                return Decision(True, permit=entry)
        return Decision(False)

    def coverage(self, a_path: Sequence[str], now: float) -> Coverage:
        """The R nodes under which the action at ``a_path`` is permitted, minus its holes."""
        permit, deny = set(), set()
        for a_node in a_path:
            for item in self._permits.get(a_node, ()):
                if item.entry.live(now):
                    permit.add(item.entry.r)
            for entry in self._denies.get(a_node, ()):
                if entry.live(now):
                    deny.add(entry.r)
        return Coverage(frozenset(permit), frozenset(deny))
