"""The PAR permission system running a copy of the default scheme on the test database.

``permissions`` turns the permission system on for one case with an empty
instance; a case places the objects it needs (``place``), makes Users Portal
管理员 and adds entries by hand, then exercises Portal code that asks the
permission port.
"""
from __future__ import annotations

import pytest

from management_portal import permission_system
from management_portal.permission_system.engine.model import DENY, PERMIT, Entry
from management_portal.permission_system.engine.store import new_entry_id
from management_portal.permission_system.facts import Fact, user_fact
from management_portal.permission_system.par import PARPermissions


class Permissions:
    def __init__(self, implementation: PARPermissions) -> None:
        self.implementation = implementation

    @property
    def scheme(self):
        return self.implementation.current()[0]

    @property
    def instance(self):
        return self.implementation.current()[2]

    @property
    def store(self):
        """The Store of the active scheme's instance."""
        return self.implementation.current()[1].store

    def place(self, *facts: Fact) -> None:
        self.instance.sync(facts)

    def users(self, *usernames: str, category: str = "member") -> None:
        """Users of ``category``: a 一般用户 (member) manages every Project, a 实习用户 (intern) only reads them."""
        for username in usernames:
            self.account(username)
            if category != "member":
                permission_system.set_user_category(username, category, actor="test-actor")
        self.place(*(user_fact(username, category=category) for username in usernames))

    @staticmethod
    def account(username: str) -> None:
        """An active account: the User's category lives in its account record."""
        from management_portal.permission_system.categories import accounts

        access = accounts()
        if not access.is_configured(username):
            access.upsert_user(username, actor="test-actor")

    def administrator(self, principal: str, granted: bool = True) -> None:
        """A User joins (or leaves) the Portal 管理员 category; a Resident, which has none, gets the same rights by hand."""
        if principal.startswith("resident_"):
            store = self.store
            with store.transaction() as connection:
                if granted:
                    store.add(connection, [Entry(new_entry_id(), PERMIT, principal, "@actions", "@shared")])
                else:
                    store.delete(connection, where="p_node = %s AND a_node = '@actions' AND r_node = '@shared'",
                                 parameters=(principal,))
            return
        self.account(principal)
        permission_system.set_user_category(principal, "portal_admin" if granted else "member", actor="test-actor")

    def grant(self, principal: str, resource: str, a: str = "@contributor", *, effect: str = PERMIT) -> Entry:
        """An entry added by hand naming A node ``a`` of the default scheme (``@viewer``, ``@contributor``, ``@admin``, …)."""
        entry = Entry(new_entry_id(), effect, principal, a, resource)
        store = self.store
        with store.transaction() as connection:
            if not store.add(connection, [entry]):
                entry = store.entries(connection, where="expires_at IS NULL AND effect = %s AND p_node = %s AND a_node = %s"
                                      " AND r_node = %s", parameters=entry.content)[0]
        return entry

    def deny(self, principal: str, resource: str, a: str = "@viewer") -> Entry:
        return self.grant(principal, resource, a, effect=DENY)

    def holds(self, principal: str, a: str, resource: str) -> bool:
        """Whether the engine permits A node ``a`` on ``resource`` to ``principal``."""
        _scheme, (decision,) = self.implementation._decide(principal, [(a, resource)])
        return decision.allowed


def portal_admin(username: str) -> None:
    """``username`` gets an active account in the Portal 管理员 category (the category lives in the account record)."""
    Permissions.account(username)
    permission_system.set_user_category(username, "portal_admin", actor="portal")


ACTIVE_COPY = "default_copy"


def enable(monkeypatch) -> Permissions:
    """Turn the permission system on with a copy of the default scheme on an empty instance (inside one isolated case).

    Built-in schemes are read-only, so the active scheme is a copy, as on the Portal (§5.11).
    """
    implementation = PARPermissions()
    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.PAR)
    monkeypatch.setattr(permission_system, "_IMPLEMENTATION", implementation)
    implementation.regenerate([], "default")
    implementation.manage("CreatePermissionScheme", {"scheme_id": ACTIVE_COPY, "source_scheme_id": "default"},
                          actor="portal", enforce=False)
    implementation.schemes.activate(ACTIVE_COPY, actor="portal")
    implementation.forget()
    return Permissions(implementation)


@pytest.fixture
def permissions(monkeypatch):
    """The permission system on, running the default scheme on an empty instance."""
    yield enable(monkeypatch)
    permission_system.forget_implementation()


__all__ = ["ACTIVE_COPY", "DENY", "Fact", "PERMIT", "Permissions", "permissions", "user_fact"]
