"""The single door between Portal's business code and its permission system (decision_b820faf42218).

Business code states the facts it owns — who calls, which resources, the
records of objects it creates, moves or deletes — and asks this module.  The
switch in 执行设置 says whether there is a permission system:

- on (``par``), the PAR engine answers on the instance of the active
  permission scheme (its rules plus the instance saved with it);
- off (``open``), there is none: every signed-in User and Resident may do
  everything, and only a Portal 管理员 changes the administrator list;
  nothing else is asked, no scheme and no table.  Permission instructions still work —
  schemes and their rows stay editable, checks and temporary credentials
  answer under the active scheme — and nothing asks whether their caller may.

The PAR implementation (engine, scheme, facts, requests, temporary
credentials, management) is loaded the first time a question needs it, so a
Portal running with the switch off loads it only for permission
instructions.

Business code never asks for a level or a role.  It asks whether the
instruction that protects what it is about to do would pass: "may manage this
Lineage" is ``permitted(actor, "UpdateLineage", "lineage_id", [lineage])``, a
list filter asks the list's read instruction, a button asks the instruction
it calls.

A personal AMA's Resident is judged as its owner User, in both modes and
under every scheme (decision_b820faf42218 §5.5).  That a personal AMA is
known only to its owner is a rule of personal AMAs, kept by Society
(``personal_ama``), not asked here.  In both modes and
under every scheme, a Portal 管理员 may turn the switch and activate a scheme,
and only a Portal 管理员 changes the administrator list
(``administrators.py``, §3.1).  Every User has a
category — Portal 管理员, 一般用户 or 实习用户 (``categories.py``) — that
schemes place Users by.  Every other fact a scheme reads (who created an
object, its Project, its steward …) is in the object's own record, so
nothing is kept here while the switch is off (decision_b820faf42218 §5.7).
Authentication, identity integrity, safety rules and workflow roles are not
permissions; their owners keep them.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any

from . import administrators

PAR = "par"
OPEN = "open"

# Every process re-reads the saved choice this often, so a change in 执行设置
# reaches all Portal services within seconds and without a restart.
MODE_REFRESH_SECONDS = 3.0

PROMPT_RULE_OFF = ("权限系统已关闭：已登录的 User 与 Resident 可以查看、使用、修改和删除所有对象。"
                   "以自己的 Resident 身份调用。")

LOGGER = logging.getLogger(__name__)
_LOCK = threading.Lock()
_IMPLEMENTATION: Any = None
_SETTINGS: Any = None
_MODE: tuple[str, float] | None = None


def configured_mode() -> str:
    """The mode saved in 执行设置; until it can be read the permission system stays on."""
    global _SETTINGS, _MODE
    now = time.monotonic()
    cached = _MODE
    if cached is not None and now < cached[1]:
        return cached[0]
    with _LOCK:
        cached = _MODE
        if cached is not None and now < cached[1]:
            return cached[0]
        try:
            if _SETTINGS is None:
                from management_portal.execution.service_settings import ExecutionSettings

                _SETTINGS = ExecutionSettings(initialize=False)
            value = PAR if _SETTINGS.permission_system_enabled() else OPEN
        except Exception:
            value = cached[0] if cached is not None else PAR
            LOGGER.warning("permission system mode unreadable; keeping %s", value, exc_info=True)
        _MODE = (value, now + MODE_REFRESH_SECONDS)
        return value


def forget_mode() -> None:
    """Read the saved mode again at the next question (after this process changed it)."""
    global _MODE
    _MODE = None


def _off() -> bool:
    return configured_mode() == OPEN


def active() -> Any:
    """The PAR implementation of this process, loaded on first use."""
    global _IMPLEMENTATION
    implementation = _IMPLEMENTATION
    if implementation is None:
        with _LOCK:
            if _IMPLEMENTATION is None:
                from .par import PARPermissions

                _IMPLEMENTATION = PARPermissions()
            implementation = _IMPLEMENTATION
    return implementation


def mode() -> str:
    return configured_mode()


def forget_implementation() -> None:
    """Load the PAR implementation afresh at the next question (tests, process store resets)."""
    global _IMPLEMENTATION
    _IMPLEMENTATION = None


def _principal(actor: Any):
    from management_common.principal import actor_principal

    try:
        return actor_principal(str(actor or "").strip())
    except ValueError:
        return None


def _signed_in(actor: Any) -> bool:
    """A User or Resident, or the Portal itself: with the switch off, everyone who may call at all."""
    principal = _principal(actor)
    return principal is not None and (principal.kind in {"user", "resident"}
                                      or (principal.kind == "service" and principal.id == "portal"))


# -- entry checks ---------------------------------------------------------------


def authorize_instruction(declaration: Any, arguments: Mapping[str, Any], context: Any, *, instruction_id: str = "") -> None:
    """Raise InstructionError when the caller may not run this HATG call."""
    if _off() or administrators.passes(context.caller, declaration.name, arguments):
        return None
    return active().authorize_instruction(declaration, arguments, context, instruction_id=instruction_id)


def authorize_reads(declaration: Any, arguments: list[Mapping[str, Any]], context: Any) -> list[Any]:
    """One refusal (or ``None``) per argument set of a read instruction, in order."""
    if _off():
        return [None] * len(arguments)
    return active().authorize_reads(declaration, arguments, context)


def readable_ids(instruction: str, parameter: str, identifiers: Iterable[str], *, actor: str, entry: str = "web") -> set[str]:
    """The identifiers whose exact read ``instruction`` would allow ``actor``."""
    if _off():
        return {str(value) for value in identifiers if value}
    return active().readable_ids(instruction, parameter, identifiers, actor=actor, entry=entry)


def instruction_requirement(name: str) -> dict[str, Any]:
    """What a call of ``name`` needs under the active scheme, as GetInstructionSchema shows it.

    ``portal_admin`` states the Portal 管理员 guarantee (§3.1) for the instructions it covers, in both modes.
    """
    found = {"required": False, "requirements": []} if _off() else dict(active().instruction_requirement(name))
    guarantee = administrators.requirement(name)
    return {**found, "portal_admin": guarantee} if guarantee else found


# -- questions from business code ------------------------------------------------


def allows(actor: str, instruction: str, arguments: Mapping[str, Any] | None = None) -> bool:
    """Whether ``actor`` would pass the permission check of ``instruction`` called with ``arguments``.

    For a button or a setting guarded by one instruction, such as whether to
    show the account management page (``CreateUser``).
    """
    if _off():
        return _signed_in(actor)
    if administrators.passes(actor, instruction, arguments):
        return True
    return active().allows(actor, instruction, dict(arguments or {}))


def allows_each(actor: str, instructions: Iterable[str], arguments: Mapping[str, Any] | None = None) -> dict[str, bool]:
    """``{instruction: allows(...)}`` for the buttons of one object, all called with the same ``arguments``."""
    return {name: allows(actor, name, arguments) for name in dict.fromkeys(instructions)}


def permitted(
    actor: str, instruction: str, parameter: str, resource_ids: Iterable[str], *, batch: int = 500,
) -> Iterator[tuple[str, bool]]:
    """``(resource_id, allowed)`` in order: whether ``instruction`` would pass with ``parameter`` naming each resource.

    The requirements the scheme maps to ``parameter`` are decided (their
    resource types are not checked), together with its requirements on fixed
    R nodes; an instruction the scheme does not map asks its unmapped
    request, and requirements on other parameters pass.  Callers that need
    only the first answer stop iterating early.
    """
    if _off():
        signed_in = _signed_in(actor)
        for resource_id in resource_ids:
            yield resource_id, signed_in
        return
    yield from active().permitted(actor, instruction, parameter, list(resource_ids), batch=batch)


def permitted_ids(actor: str, instruction: str, parameter: str, resource_ids: Iterable[str]) -> set[str]:
    """The resources among ``resource_ids`` on which ``permitted`` answers yes."""
    return {resource_id for resource_id, allowed in permitted(actor, instruction, parameter, resource_ids) if allowed}


def owners_only() -> bool:
    """Whether an object kept for the Users who made it (a Graph Draft, an upload) stays theirs alone.

    Retracting one's own message and personal to-dos are the actor's own
    records and keep their owner whatever this answers.
    """
    return not _off()


def users_permitted(instruction: str, parameter: str, resource_id: str) -> list[str] | None:
    """Active Users whose own lasting entries let ``instruction`` pass on ``resource_id``; ``None`` with the switch off.

    With no permission system there are no entries to name anyone; the
    caller decides whom to tell instead.
    """
    if _off():
        return None
    return active().users_permitted(instruction, parameter, resource_id)


def user_categories() -> dict[str, str]:
    """The category of every User that is not a 一般用户 (``member``), in either mode."""
    from .categories import categories

    return categories().all()


def user_category(username: str) -> str:
    """``portal_admin``, ``member`` or ``intern``."""
    from .categories import categories

    return categories().of(str(username or "").strip())


def set_user_category(username: str, category: str, *, actor: str) -> bool:
    """Write a User's category into the account record (UpdateUser changes.category, the first administrator).

    Returns whether it changed.  With the permission system on, the User
    moves to its category's P node in the account's transaction; turning it
    on later moves the User there.
    """
    from .categories import accounts, category_name

    username, category = str(username or "").strip(), category_name(category)

    def place(connection: Any) -> None:
        if not _off():
            active().sync_user(username, category=category, connection=connection)
    return accounts().set_category(username, category, actor=actor, before_commit=place)


def creation_fields(actor: str) -> dict[str, str]:
    """The creator fields a store writes into a new object's record: ``created_by`` and, when it applies, ``created_for``.

    An object created without an actor (an import) gets neither.
    """
    creator = str(actor or "").strip()
    behalf = on_behalf_of(creator) if creator else ""
    return {**({"created_by": creator} if creator else {}), **({"created_for": behalf} if behalf else {})}


# -- keeping the instance current -------------------------------------------------


def sync_resource(record: Mapping[str, Any], *, resource_type: str, connection: Any = None) -> bool:
    """An object was created or changed (or marked deleted); returns whether its permission facts changed.

    ``connection`` is the caller's open transaction on the Portal database,
    in which the object's own record is written: the object and its place in
    the trees commit together (decision_b820faf42218 §5.7).  Every fact comes
    from ``record``: its creator is ``created_by`` (and ``created_for``, see
    ``on_behalf_of``).  With the switch off there is no instance to keep.
    """
    if _off():
        return False
    return active().sync_resource(record, resource_type=resource_type, connection=connection)


def on_behalf_of(actor: str) -> str:
    """The User whose input the running execution of Resident ``actor`` works on, or "".

    A store creating an object for ``actor`` records this User as the
    object's ``created_for``: a Resident creating while working on a User's
    input creates for that User too (decision_600ae27b0538 §4).  The
    execution is the one the current instruction call belongs to; it counts
    only while it is that Resident's running execution.
    """
    from management_common.execution_association import current_execution_id
    from management_common.principal import actor_principal

    execution_id = current_execution_id()
    try:
        principal = actor_principal(str(actor or "").strip())
    except ValueError:
        return ""
    if principal.kind != "resident" or not execution_id:
        return ""
    from management_portal.society_service.resident_work_origin import execution_initiator

    try:
        return execution_initiator(principal.id, execution_id)
    except Exception:  # noqa: BLE001 - a creation never fails over whom it was for
        LOGGER.warning("could not read the initiator of %s for %s", execution_id, principal.id, exc_info=True)
        return ""


def retire_resources(resource_ids: Iterable[str], *, connection: Any = None) -> list[str]:
    """Deleted objects leave the trees with every entry that names them (turning the switch on catches up)."""
    if _off():
        return []
    return active().retire_resources(list(resource_ids), connection=connection)


def sync_user(username: str, *, deleted: bool = False, connection: Any = None) -> None:
    """A User account was created or deleted, in ``connection``: the account's transaction (decision_b820faf42218 §5.7)."""
    if _off():
        return None
    return active().sync_user(username, deleted=deleted, connection=connection)


def rename_element(connection: Any, old_id: str, new_id: str, *, apply: bool) -> int:
    """A Project's ID changes inside the rename's transaction: rewrite the tree paths that name it.

    The rename rewrites plain text columns itself; paths are arrays.  Returns
    how many stored paths name the old ID.  The paths of every scheme's saved
    instance are rewritten, with the permission system on or off, so the
    renamed Project keeps its place and its rows.
    """
    from .inline import rename_paths

    return rename_paths(connection, old_id, new_id, apply=apply)


def end_task_credentials(connection: Any, resident_id: str, message_id: str) -> int:
    """A Resident's input ended (completed, failed, cancelled): the temporary credentials lent with that message end too.

    ``connection`` is the transaction recording the input's end; with the
    permission system on or off.
    """
    from .inline import end_with_input

    return end_with_input(connection, str(resident_id or "").strip(), str(message_id or "").strip())


def maintain() -> None:
    """Periodic cleanup: expired temporary credentials (with the switch on; turning it on resumes it)."""
    if _off():
        return None
    return active().maintain()


# -- permission instructions ------------------------------------------------------


def manage(instruction: str, arguments: Mapping[str, Any], *, actor: str, collect_facts: Callable[[], list] | None = None,
           expected_version: Any = None) -> dict[str, Any]:
    """Answer a permission instruction (schemes, nodes, entries, checks, temporary credentials).

    ``expected_version`` is the call envelope's version token for the scheme or entry the call targets.
    """
    if _off() and not _signed_in(actor):
        from management_portal.hatg.contracts import InstructionError

        raise InstructionError("instruction.forbidden", "需要已登录的 User 或 Resident")
    return active().manage(instruction, dict(arguments), actor=actor, collect_facts=collect_facts, enforce=not _off(),
                           expected_version=expected_version)


def prompt_rule() -> str:
    """One sentence telling a Resident how its calls are checked."""
    if _off():
        return PROMPT_RULE_OFF
    return active().prompt_rule()
