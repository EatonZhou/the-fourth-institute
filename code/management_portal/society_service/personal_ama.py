"""A personal AMA is its owner's: a rule of personal AMAs, kept by Society (decision_bd5217d08c09).

A personal AMA — its Lineage, its Residents and Families, the Workspaces in
its folder — reaches only its owner User, that User's AMA Residents and the
Portal itself; to everyone else it does not exist, so what the rule keeps
from someone is reported as not found.  The rule is not a permission: it
holds whether the permission system is on or off, reads no scheme and asks
nothing of the permission system.  With the permission system on, the scheme
judges what the rule lets through, as it judges any other object.

Everyone may deliver a message to an AMA's Resident (DeliverMessage); the
sender gets the receipt of its own message and nothing of the Conversation.

An AMA Resident stands for its owner: ``owner_of`` names that User, and the
permission system judges the Resident as the owner (decision_b820faf42218 §5.5).
"""
from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

_LOCK = threading.Lock()
_DESCRIPTORS: Any = None
# A Resident's owner is fixed when it is registered, so a registered Resident's answer is kept.
_OWNERS: dict[str, str] = {}
_KEPT = 20000


class NotFound(KeyError):
    """What the rule keeps from someone: it does not exist for them."""

    def __init__(self, resource_id: object) -> None:
        super().__init__(f"{str(resource_id or '').strip()} not found")


def owner_of(principal_id: object) -> str:
    """The User a personal AMA Resident belongs to (its registered ``personal_owner_id``), or ""."""
    global _DESCRIPTORS
    principal = str(principal_id or "").strip()
    if not principal.startswith("resident_"):
        return ""
    with _LOCK:
        if principal in _OWNERS:
            return _OWNERS[principal]
        if _DESCRIPTORS is None:
            from management_portal.resources.collaboration_registry import CollaborationDescriptorStore

            _DESCRIPTORS = CollaborationDescriptorStore()
    try:
        record = _DESCRIPTORS.get(principal)
    except (KeyError, ValueError):
        return ""
    owner = str((record.get("attributes") or {}).get("personal_owner_id") or "").strip()
    with _LOCK:
        if len(_OWNERS) >= _KEPT:
            _OWNERS.clear()
        _OWNERS[principal] = owner
    return owner


def forget() -> None:
    """Drop the owners kept (tests that reuse Resident IDs)."""
    with _LOCK:
        _OWNERS.clear()


def party(principal_id: object) -> str:
    """Who ``principal_id`` acts as: the owner of an AMA Resident, otherwise itself."""
    principal = str(principal_id or "").strip()
    return owner_of(principal) or principal


def same_party(first: object, second: object) -> bool:
    """Whether two principals act as the same User: one is the other's AMA Resident, or both are that User's."""
    one, other = party(first).casefold(), party(second).casefold()
    return bool(one) and one == other


def acts_for(actor: object, owner: object) -> bool:
    """Whether ``actor`` is the User ``owner``, one of that User's AMA Residents, or the Portal itself."""
    actor_id, owner_id = str(actor or "").strip(), str(owner or "").strip()
    if actor_id == "portal":
        return True
    return bool(owner_id) and same_party(actor_id, owner_id)


def reaches(actor: object, owner: object) -> bool:
    """Whether the rule lets ``actor`` reach something whose personal AMA owner is ``owner`` ("" for anything else)."""
    return not str(owner or "").strip() or acts_for(actor, owner)


def hidden(actor: object, owners: Mapping[str, object]) -> set[str]:
    """Of the resources ``owners`` (ID -> personal AMA owner), those the rule keeps from ``actor``."""
    reached: dict[str, bool] = {}
    kept = set()
    for resource_id, owner in owners.items():
        key = str(owner or "").strip()
        if key not in reached:
            reached[key] = reaches(actor, key)
        if not reached[key]:
            kept.add(resource_id)
    return kept
