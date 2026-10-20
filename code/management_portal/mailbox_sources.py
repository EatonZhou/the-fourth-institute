"""The items features add to Users' Mailboxes beside Resident conversations (decision_15f7df737ed5).

A feature adds its items through the ``mailbox.source`` hook
(``portal_features``) with a ``MailboxSource``: the key prefix of its items,
one User's items, and whether opening one leads to a replyable Chat (the
conversation section) or it is a notification.  The read state of most items
lives in ``conversation_read_state``.  A feature that keeps its members' read
positions itself, as a Chatroom does, also reads and unreads its items and
says whether an item is in a User's Mailbox; an entry it lists carries its
own ``unread`` and ``read_revision``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from management_portal import portal_features


@dataclass(frozen=True)
class MailboxSource:
    prefix: str
    entries: Callable[[str], list[dict[str, Any]]]
    conversation: bool = False
    # (actor, item_id, unread, *, expected_revision=None) -> {"unread", "read_revision"};
    # raises conversation_read_state.VersionConflict when expected_revision is out of date.
    set_unread: Callable[..., dict[str, Any]] | None = None
    # (item_id, actor) -> whether the item is in the actor's Mailbox.
    contains: Callable[[str, str], bool] | None = None


def sources() -> list[MailboxSource]:
    return [source for _feature, source in portal_features.contributions("mailbox.source")]


def entries(actor: str) -> list[dict[str, Any]]:
    """Every source's items of one User.

    An unavailable source raises instead of looking emptied: a failed read
    keeps the browser's last snapshot and every pin.
    """
    return [entry for source in sources() for entry in source.entries(actor)]


def source_of(key: str) -> MailboxSource | None:
    return next((source for source in sources() if key.startswith(source.prefix)), None)


def conversation_prefixes() -> tuple[str, ...]:
    return tuple(source.prefix for source in sources() if source.conversation)


def own_read_state_prefixes() -> tuple[str, ...]:
    """Key prefixes of the items whose feature keeps their read state."""
    return tuple(source.prefix for source in sources() if source.set_unread is not None)
