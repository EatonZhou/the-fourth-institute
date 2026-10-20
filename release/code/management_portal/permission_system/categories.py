"""User categories: every User is a Portal 管理员, a 一般用户 or a 实习用户 (decision_600ae27b0538 §2).

A category is a fact about the account, kept in the account record
(``portal_identity.user_accounts.category``, decision_b820faf42218 §5.7) and
valid whether the permission system is on or off.  With it on, the scheme
places each User under the P node that names the User's category, so the
entries on that node speak for the whole category.  With it off, categories
decide no instruction.  In either mode only a Portal 管理员 sets or clears
Portal 管理员 (``administrators.py``); moves between the other two follow
UpdateUser's own check.  A new Portal's first account is its first Portal
管理员 (``UserEnrollment.create_account``).

Nothing here imports the PAR implementation.
"""
from __future__ import annotations

from typing import Any

CATEGORIES = {"portal_admin": "Portal 管理员", "member": "一般用户", "intern": "实习用户"}
DEFAULT = "member"


def category_name(value: Any) -> str:
    text = str(value or "").strip()
    if text not in CATEGORIES:
        raise ValueError(f"User 分类只能是：{'、'.join(CATEGORIES)}")
    return text


_ACCOUNTS: Any = None


def accounts() -> Any:
    """The account store whose records hold the categories (its own handle on the account tables)."""
    global _ACCOUNTS
    if _ACCOUNTS is None:
        from management_portal.portal_access_control import PortalAccessStore

        _ACCOUNTS = PortalAccessStore()
    return _ACCOUNTS


class Categories:
    """Reads of the categories the account records hold."""

    def of(self, username: str) -> str:
        return accounts().user(username).get("category") or DEFAULT

    def all(self) -> dict[str, str]:
        """The category of every live User that is not a 一般用户."""
        return {username: user["category"] for username, user in accounts().snapshot()["users"].items()
                if user["status"] != "deleted" and user["category"] != DEFAULT}


_CATEGORIES = Categories()


def categories() -> Categories:
    return _CATEGORIES
