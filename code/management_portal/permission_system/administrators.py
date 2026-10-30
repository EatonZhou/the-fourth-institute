"""Portal 管理员保底: the one judgement made outside the permission scheme (decision_b820faf42218 §3.1).

Whether the permission system is on or off and whichever scheme is active,
the port answers these before the scheme, and no scheme can take them back:

- a Portal 管理员 may always turn the permission system on or off
  (UpdateExecutionSettings with only ``permission_system_enabled``), list
  the schemes (ListPermissionSchemes, a read) and activate one
  (ActivatePermissionScheme), so switching schemes never needs the switch
  off; for anyone else the scheme decides, and with the system off everyone
  signed in passes;
- only a Portal 管理员 changes the administrator list: makes another User one
  or takes the category away, disables, enables or deletes an administrator's
  account, never its own.  Moving a User between 一般用户 and 实习用户 stays
  with UpdateUser's own check.  Every administrator is a User; a Resident
  never is.

A personal AMA's Resident acts as its owner (§5.5): an administrator's AMA
passes as the administrator does, and it never takes its owner off the list.
The list is the account records' ``category`` (``portal_admin``), the same
field the scheme places Users by.

Nothing here imports the PAR implementation.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

ADMINISTRATOR = "portal_admin"
SWITCH = "permission_system_enabled"

REQUIREMENTS = {
    "UpdateExecutionSettings": "Portal 管理员只改 permission_system_enabled 时永远通过，不问方案；同一次调用里还有其他设置项时整次按方案判定",
    "ListPermissionSchemes": "Portal 管理员永远可以读取方案列表，不问方案，以便不关开关也能切换方案",
    "ActivatePermissionScheme": "Portal 管理员永远通过，不问方案",
    "UpdateUser": "设为或取消 Portal 管理员、停用或启用管理员账号只有 Portal 管理员能做，不能对自己做，不问方案；"
                  "changes 只含 category、status 且改的是管理员名单时，Portal 管理员不问方案",
    "DeleteUser": "删除 Portal 管理员只有其他 Portal 管理员能做；Portal 管理员删除管理员时不问方案",
}


def _user(actor: Any) -> str:
    """The User ``actor`` acts as: itself, or the owner of a personal AMA's Resident (§5.5)."""
    from management_common.principal import actor_principal

    try:
        principal = actor_principal(str(actor or "").strip())
    except ValueError:
        return ""
    if principal.kind == "resident":
        from management_portal.permission_system.personal_ama import owner_of

        return owner_of(principal.id)
    return principal.id if principal.kind == "user" else ""


def _account(username: str) -> Mapping[str, Any]:
    from .categories import accounts

    return accounts().user(username) if username else {}


def is_administrator(actor: Any) -> bool:
    """An active User account in the Portal 管理员 category, or a personal AMA's Resident of one."""
    account = _account(_user(actor))
    return bool(account.get("configured")) and account.get("status") == "active" and account.get("category") == ADMINISTRATOR


def _changes_list(instruction: str, arguments: Mapping[str, Any]) -> bool:
    """Whether a UpdateUser or DeleteUser call changes the administrator list and nothing else."""
    target = str(arguments.get("user_id") or "").strip()
    account = _account(target)
    current = account.get("category") == ADMINISTRATOR
    if instruction == "DeleteUser":
        return current
    changes = arguments.get("changes")
    if not isinstance(changes, Mapping) or not changes or set(changes) - {"category", "status"}:
        return False
    return (("category" in changes and (changes["category"] == ADMINISTRATOR) != current)
            or ("status" in changes and current and changes["status"] != account.get("status")))


def passes(actor: Any, instruction: str, arguments: Mapping[str, Any] | None = None) -> bool:
    """Whether the call passes without asking the scheme: an administrator doing what the scheme cannot take back."""
    arguments = arguments or {}
    if instruction not in REQUIREMENTS or not is_administrator(actor):
        return False
    if instruction == "UpdateExecutionSettings":
        return {name for name, value in arguments.items() if value is not None} == {SWITCH}
    if instruction in {"UpdateUser", "DeleteUser"}:
        return _changes_list(instruction, arguments)
    return True


def requirement(instruction: str) -> str:
    """What GetInstructionSchema says about the pass for ``instruction`` (empty when none)."""
    return REQUIREMENTS.get(instruction, "")


def check_change(actor: Any, username: str, *, category: str | None = None, status: str | None = None,
                 delete: bool = False) -> bool:
    """Refuse a change to the administrator list that ``actor`` may not make, in either mode.

    Returns whether the change takes an administrator off the list.  Raises
    PermissionError for a caller who is not an administrator and ValueError
    for one acting on itself.  A change that leaves the list as it is passes
    here: a status equal to the account's present one changes nothing.
    Callers check and write inside ``list_change``, which also keeps one
    active administrator.
    """
    account = _account(username)
    current = account.get("category") == ADMINISTRATOR
    granting = category is not None and category == ADMINISTRATOR and not current
    clearing = current and (delete or (category is not None and category != ADMINISTRATOR)
                            or (status is not None and status != account.get("status")))
    if not granting and not clearing:
        return False
    if not is_administrator(actor):
        raise PermissionError("只有 Portal 管理员能修改管理员名单")
    if clearing and _user(actor).casefold() == username.casefold():
        raise ValueError("不能取消、停用或删除自己的 Portal 管理员身份")
    return clearing


@contextmanager
def list_change(actor: Any, username: str, **change: Any) -> Iterator[None]:
    """Check a change to the administrator list and make it in one account transaction.

    The transaction holds the identity lock, so two administrators taking
    each other off the list at once are judged one after the other, and the
    second sees the first's write.  The writes in the block join the
    transaction; when a change taking an administrator off would leave no
    active administrator, all of it rolls back with ValueError.
    """
    from .categories import accounts

    store = accounts()
    with store._writing():
        clearing = check_change(actor, username, **change)
        yield
        if not clearing:
            return
        users = store._read_uncached()["users"].values()
        if not any(user.get("category") == ADMINISTRATOR and user.get("status") == "active" for user in users):
            raise ValueError("至少保留一位启用中的 Portal 管理员")
