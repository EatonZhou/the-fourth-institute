"""The objects of the PAR permission model (thinking_3cbdd375ebf8).

A permission instance is three trees and one table.  Each tree partitions one
universe — Principals (P), Actions (A), Resources (R) — into nested classes:
the root holds everything, a child is a proper subset of its parent and
siblings never overlap, so every element has exactly one position.  An entry
``(P node, A node, R node)`` permits (or, as an exception, denies) every
combination of the elements under its three nodes.  A request is permitted
when some permit entry covers it and no deny entry does.

This module knows nothing about the Portal: nodes and elements are strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

TREES = ("P", "A", "R")
PERMIT, DENY = "permit", "deny"


@dataclass(frozen=True)
class Node:
    """A node of a tree: a class of elements, or an element the scheme names."""

    id: str
    parent: str
    title: str = ""
    path: tuple[str, ...] = ()


@dataclass(frozen=True)
class Tree:
    """The fixed part of one tree.

    ``nodes`` are the nodes a scheme declares; elements placed while the
    Portal runs are stored with the instance.  An element that has no stored
    place sits under ``default_parent``.
    """

    name: str
    root: str
    nodes: Mapping[str, Node]
    default_parent: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", MappingProxyType(dict(self.nodes)))

    def path(self, node_id: str) -> tuple[str, ...]:
        """Root-to-node path of a declared node."""
        return self.nodes[node_id].path

    def element_path(self, element: str, parent_path: tuple[str, ...] | None = None) -> tuple[str, ...]:
        """Root-to-element path: a declared node's own path, else under its stored or default parent."""
        node = self.nodes.get(element)
        if node is not None:
            return node.path
        return (parent_path if parent_path is not None else self.path(self.default_parent)) + (element,)


@dataclass(frozen=True)
class Entry:
    """One row of the permission table: ``(p, a, r)`` and its effect.

    Rows do not say where they came from; a row generated from the scheme's
    rules and one added by hand are alike.  A ``disabled`` row stays in the
    table and decides nothing.  ``version`` counts its edits.  A temporary
    credential is a permit row lent by ``issued_by`` until ``expires_at``;
    one that travels with a message names it (``message_id``) and ends when
    the recipient's input of that message ends.
    """

    entry_id: str
    effect: str
    p: str
    a: str
    r: str
    disabled: bool = False
    version: int = 1
    issued_by: str = ""
    issued_at: float = 0.0
    expires_at: float | None = None
    message_id: str = ""

    def __post_init__(self) -> None:
        if self.effect not in (PERMIT, DENY):
            raise ValueError(f"unknown entry effect: {self.effect}")
        if not (self.entry_id and self.p and self.a and self.r):
            raise ValueError("an entry names its id and three nodes")

    @property
    def temporary(self) -> bool:
        return self.expires_at is not None

    @property
    def content(self) -> tuple[str, str, str, str]:
        return self.effect, self.p, self.a, self.r

    def live(self, now: float) -> bool:
        return self.expires_at is None or self.expires_at > now

    def to_dict(self) -> dict:
        value = {"entry_id": self.entry_id, "effect": self.effect, "p": self.p, "a": self.a, "r": self.r,
                 "disabled": self.disabled, "version": self.version}
        if self.temporary:
            value.update(issued_by=self.issued_by, issued_at=self.issued_at, expires_at=self.expires_at)
            if self.message_id:
                value["message_id"] = self.message_id
        return value


@dataclass(frozen=True)
class Decision:
    """The answer for one ``(p, a, r)``: the permit entry that covers it, or why not."""

    allowed: bool
    permit: Entry | None = None
    deny: Entry | None = None

    @property
    def reason(self) -> str:
        if self.allowed:
            return "permitted"
        return "denied" if self.deny is not None else "no_permit"


@dataclass(frozen=True)
class Coverage:
    """What one principal may do with one action, as R nodes.

    The resources it covers are the subtrees under ``permit`` minus the
    subtrees under ``deny``; a list query filters stored resource paths with
    exactly this condition instead of deciding resource by resource.
    """

    permit: frozenset[str] = field(default_factory=frozenset)
    deny: frozenset[str] = field(default_factory=frozenset)

    def covers(self, path: tuple[str, ...]) -> bool:
        return not self.permit.isdisjoint(path) and self.deny.isdisjoint(path)
