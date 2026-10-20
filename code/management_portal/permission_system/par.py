"""The permission system when it is on: the PAR engine running the active scheme's instance.

``PARPermissions`` answers the port's questions.  It keeps the compiled
rules of the active scheme, an engine on that scheme's instance and the fact
adapter that maintains the instance.  Each decision reads, in its one
statement, which scheme is active and at which rules version; when another
process activated another scheme or replaced the active one's rules, the
decision loads that scheme and goes on with the same read.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any

from . import _principal, authdiff
from .engine import Engine, Store
from .engine.store import DEFAULT_SCHEME, Snapshot
from .facts import Fact, Instance, fact_from_record, user_fact
from .requests import RecordNotFound, Request, Unresolved, requests
from .scheme.compiler import CompiledScheme
from .scheme.repository import SchemeNotFound, Schemes, source_digest

PROMPT_RULE = ("权限按当前权限方案判定，只看你自己的权限，不借用消息发起者的权限；"
               "需要别人的权限时，请持有者给你签发临时凭证：随消息签发的凭证在你处理完那条消息时失效，"
               "按时长签发的到期失效，两种最长 7 天；用完可以用 RevokeTemporaryCredential 交还。"
               "你也可以把自己持有的权限随 DeliverMessage 的 temporary_credentials 交给收件 Resident。以自己的 Resident 身份调用。")

LOGGER = logging.getLogger(__name__)


def _is_portal(actor: Any) -> bool:
    principal = _principal(actor)
    return principal is not None and principal.kind == "service" and principal.id == "portal"


class PARPermissions:
    def __init__(self, store: Store | None = None) -> None:
        self.store = store or Store()
        self.schemes = Schemes(self.store)
        self._lock = threading.Lock()
        self._loaded: tuple[tuple[str, int], CompiledScheme, Engine, Instance] | None = None

    # -- the active scheme -------------------------------------------------------------

    def _load(self, active: tuple[str, int] | None = None) -> tuple[CompiledScheme, Engine, Instance]:
        """The active scheme, its engine and instance; ``active`` is ``(scheme_id, rules version)`` as a decision read it."""
        with self._lock:
            if self._loaded is not None and (active is None or self._loaded[0] == active):
                return self._loaded[1], self._loaded[2], self._loaded[3]
            if active is None:
                scheme_id = self.schemes.active()
                active = (scheme_id, self.schemes.version(scheme_id))
            try:
                scheme = self.schemes.compiled(active[0])
            except SchemeNotFound:
                scheme = self.schemes.compiled(DEFAULT_SCHEME)
            # Rules a release changed keep working on the saved instance until it is generated again.
            store = self.store.scoped(scheme.scheme_id)
            engine = Engine(scheme.trees, store)
            self._loaded = (active, scheme, engine, Instance(scheme, store))
            return scheme, engine, self._loaded[3]

    def current(self) -> tuple[CompiledScheme, Engine, Instance]:
        """The active scheme as it is now (one read of which scheme is active)."""
        scheme_id = self.schemes.active()
        return self._load((scheme_id, self.schemes.version(scheme_id)))

    def forget(self) -> None:
        with self._lock:
            self._loaded = None

    def _decide(self, principal: str, items: list[tuple[str, str]]):
        """Decisions under the active scheme for fixed ``(action, resource)`` items."""
        scheme, _items, decisions = self._decide_built(principal, lambda _scheme: items)
        return scheme, decisions

    def _decide_built(self, principal: str, build: Callable[[CompiledScheme], list[tuple[str, str]]]):
        """Decisions for the items ``build`` makes from the active scheme's instruction map.

        One read gives the version number, the active scheme and the paths.
        When it shows another scheme or rules version than this process holds,
        that scheme is loaded and the items are made again from its map; only
        then are the paths read a second time, from its instance.
        """
        held = self._load()[0]
        items = build(held)
        if not items:
            return held, items, []
        snapshot: Snapshot = self.store.snapshot(principal, list(dict.fromkeys(resource for _action, resource in items)),
                                                 active=True)
        scheme, engine, _instance = self._load((snapshot.scheme_id, snapshot.scheme_version))
        if scheme is not held or scheme.scheme_id != snapshot.scheme_id:
            items = build(scheme)
            snapshot = engine.snapshot(principal, [resource for _action, resource in items])
        return scheme, items, engine.decide(principal, items, snapshot=snapshot)

    # -- entry checks -------------------------------------------------------------------

    def _requests(self, scheme: CompiledScheme, name: str, arguments: Mapping[str, Any]) -> list[Request]:
        return requests(scheme, name, arguments, readers=self._readers())

    def _readers(self) -> dict[str, Any]:
        """Who reads each record a ``via`` slot names (vocabulary.RECORDS): the code that keeps it."""
        from management_portal.hatg.kernel import receipt_scopes

        return {"permission_entry": self._entries, "instruction_execution": receipt_scopes}

    def _entries(self, entry_ids: list[str], arguments: Mapping[str, Any]) -> dict[str, dict[str, str]]:
        """Saved entries of the scheme the call names (the active one when omitted), for ``via: permission_entry.r``."""
        scheme_id = str(arguments.get("scheme_id") or "").strip() or self.schemes.active()
        store = self.store.scoped(scheme_id)
        with store.transaction() as connection:
            found = store.entries(connection, where="entry_id = ANY(%s)", parameters=(entry_ids,))
        return {entry.entry_id: {"r": entry.r} for entry in found}

    def authorize_instruction(self, declaration, arguments, context, *, instruction_id=""):
        error = self._check(declaration, [arguments], context)[0]
        if error is not None:
            raise error

    def authorize_reads(self, declaration, arguments, context):
        if declaration.mutates:
            raise ValueError("authorize_reads only accepts read instructions")
        return self._check(declaration, arguments, context)

    def _check(self, declaration, arguments, context, *, log: bool = True) -> list[Any]:
        """One refusal (or None) per argument set; all requests are decided in one engine call."""
        from management_portal.hatg.contracts import InstructionError

        if not arguments:
            return []
        if _is_portal(context.caller):
            return [None] * len(arguments)
        errors: list[Any] = [None] * len(arguments)
        per_call: list[list[Request]] = []
        flat: list[Request] = []

        def build(scheme: CompiledScheme) -> list[tuple[str, str]]:
            errors[:] = [None] * len(arguments)
            per_call.clear()
            for index, args in enumerate(arguments):
                try:
                    per_call.append(self._requests(scheme, declaration.name, args))
                except Unresolved as exc:
                    errors[index] = InstructionError("instruction.invalid_arguments", str(exc))
                    per_call.append([])
                except RecordNotFound as exc:
                    errors[index] = InstructionError("instruction.not_found", str(exc))
                    per_call.append([])
            flat[:] = list(dict.fromkeys(request for items in per_call for request in items))
            return [(request.action, request.resource) for request in flat]

        scheme, _items, decisions = self._decide_built(context.caller, build)
        if not flat:
            return errors
        by_request = dict(zip(flat, decisions))
        for index, items in enumerate(per_call):
            for request in items:
                decision = by_request[request]
                if decision.allowed:
                    continue
                errors[index] = self._denied(declaration, request, decision, scheme)
                # One line per refusal, so a refused instruction can be traced to its rule.
                if log:
                    LOGGER.warning("permission refused: caller=%s instruction=%s target=%s a=%s reason=%s",
                                   context.caller, declaration.name, request.resource,
                                   request.slot.a if request.slot is not None else "", decision.reason)
                break
        return errors

    @staticmethod
    def _denied(declaration, request: Request, decision, scheme: CompiledScheme):
        """A denied read looks like a missing object (decision_898b8911a2ff "返回格式")."""
        from management_portal.hatg.contracts import InstructionError

        a = request.slot.a if request.slot is not None else ""
        node = scheme.trees["A"].nodes.get(a)
        needed = (node.title or a) if node is not None else a
        message = (f"无权对 {request.resource} 执行 {declaration.name}" + (f"：需要 {needed}" if needed else "")
                   + ("（命中 deny 表项）" if decision.reason == "denied" else ""))
        details = {"target_id": request.resource, "action": request.action, "reason": decision.reason}
        if a:
            details["a"] = a
        if decision.deny is not None:
            details["deny_entry_id"] = decision.deny.entry_id
        code = "instruction.forbidden" if declaration.mutates else "instruction.not_found"
        return InstructionError(code, message, details=details)

    def readable_ids(self, instruction, parameter, identifiers, *, actor, entry="web"):
        from management_portal.hatg.catalog import declaration
        from management_portal.hatg.contracts import InstructionContext
        from management_portal.portal_access_control import is_active_user_or_resident

        ids = list(dict.fromkeys(str(value) for value in identifiers if value))
        if not ids or not is_active_user_or_resident(actor):
            return set()
        # A filter, not a refused call: what it leaves out is not logged.
        errors = self._check(declaration(instruction), [{parameter: value} for value in ids],
                             InstructionContext(caller=actor, entry=entry), log=False)
        return {value for value, error in zip(ids, errors) if error is None}

    def instruction_requirement(self, name):
        return self._load()[0].describe(name)

    # -- questions ------------------------------------------------------------------

    def allows(self, actor, instruction: str, arguments: Mapping[str, Any]) -> bool:
        if _is_portal(actor):
            return True
        principal = _principal(actor)
        if principal is None or principal.kind not in {"user", "resident"}:
            return False
        return self.passes(principal.id, instruction, arguments)

    def _elements(self, scheme: CompiledScheme, instruction: str, parameter: str) -> list[str] | None:
        """The A elements deciding ``parameter`` of ``instruction``; ``None`` when nothing does."""
        slots = scheme.slots(instruction)
        if not slots:
            return [instruction]
        # A via slot's argument names records, a place slot's a type: neither is the resource asked about here.
        return [slot.element for slot in slots if slot.argument == parameter and not slot.via and not slot.place] or None

    def permitted(self, actor, instruction: str, parameter: str, resource_ids, *, batch) -> Iterator[tuple[str, bool]]:
        principal = _principal(actor)
        if _is_portal(actor) or principal is None or principal.kind not in {"user", "resident"}:
            allowed = _is_portal(actor)
            for resource_id in resource_ids:
                yield resource_id, allowed
            return
        resource_ids = list(resource_ids)
        for start in range(0, len(resource_ids), batch):
            chunk = resource_ids[start:start + batch]
            shape: dict[str, int] = {}

            def build(scheme: CompiledScheme) -> list[tuple[str, str]]:
                slots = scheme.slots(instruction)
                # Requests that are the same for every resource: fixed R nodes, or the unmapped request.
                fixed = ([(slot.element, slot.resource) for slot in slots if slot.resource] if slots
                         else [(instruction, scheme.unmapped_resource)])
                elements = [slot.element for slot in slots if slot.argument == parameter and not slot.via and not slot.place]
                shape.update(fixed=len(fixed), elements=len(elements))
                return fixed + [(element, resource_id) for resource_id in chunk for element in elements]

            _scheme, _items, decisions = self._decide_built(principal.id, build)
            fixed_allowed = all(decision.allowed for decision in decisions[:shape["fixed"]])
            answers = decisions[shape["fixed"]:]
            width = shape["elements"]
            for offset, resource_id in enumerate(chunk):
                yield resource_id, fixed_allowed and all(
                    decision.allowed for decision in answers[offset * width:(offset + 1) * width])

    def users_permitted(self, instruction: str, parameter: str, resource_id: str) -> list[str]:
        """Active Users whose own enabled permits reach every element deciding ``parameter`` on the resource or an ancestor.

        A permit on a User category's node speaks for each User of that category;
        permits on the nodes above (every User, every principal) name nobody in particular.
        """
        from management_portal.portal_access_control import get_portal_access_store

        from .categories import DEFAULT, categories

        scheme, engine, _instance = self._load()
        store = engine.store
        elements = self._elements(scheme, instruction, parameter) or []
        if not scheme.slots(instruction):
            resource_id = scheme.unmapped_resource
        path = engine.path("R", resource_id, store.paths("R", [resource_id]).get(resource_id))
        access = get_portal_access_store()
        category_nodes = set(scheme.user_categories.values())
        stored = categories().all() if category_nodes else {}

        def members(node: str) -> set[str]:
            names = {username for username, category in stored.items() if scheme.user_categories.get(category) == node}
            if scheme.user_categories.get(DEFAULT) == node:
                # 一般用户 are the Users without a stored category.
                names |= {username for username in access.snapshot()["users"] if username not in stored}
            return names

        found: set[str] | None = None
        for element in elements:
            a_path = engine.a_path(element)
            with store.read() as connection:
                rows = store.execute(
                    connection, "SELECT DISTINCT p_node FROM {s}.scheme_entries WHERE scheme_id = %s AND effect = 'permit'"
                    " AND NOT disabled AND r_node = ANY(%s) AND a_node = ANY(%s) AND (expires_at IS NULL OR expires_at > now())",
                    (store.scheme_id, list(path), list(a_path))).fetchall()
            holders = {row["p_node"] for row in rows}
            for node in holders & category_nodes:
                holders |= members(node)
            found = holders if found is None else found & holders
        users = []
        for node in sorted(found or ()):
            principal = _principal(node)
            if principal is not None and principal.kind == "user" and access.is_active(principal.id):
                users.append(principal.id)
        return users

    # -- the instance -------------------------------------------------------------------

    # The instance writers read which scheme is active each time: activating another one while the
    # permission system is off reaches no decision here, and objects must follow into its instance.

    def sync_resource(self, record: Mapping[str, Any], *, resource_type: str, connection=None) -> bool:
        return self.current()[2].sync([fact_from_record(record, resource_type)], connection=connection)

    def retire_resources(self, resource_ids: Iterable[str], *, connection=None) -> list[str]:
        from .requests import resource_type

        instance = self.current()[2]
        retired = []
        for resource_id in resource_ids:
            retired += instance.retire(resource_type(resource_id), [resource_id], connection=connection)
        return retired

    def sync_user(self, username: str, *, deleted: bool = False, category: str = "", connection=None) -> None:
        """Place (or take out) a User; ``category`` defaults to the one its account record holds."""
        from .categories import categories

        self.current()[2].sync([user_fact(username, active=not deleted, category=category or categories().of(username))],
                             connection=connection)

    def _context(self, collect_facts=None, *, enforce: bool = True, expected_version: Any = None):
        from .schemes import Context

        return Context(self.schemes, enabled=enforce, collect_facts=collect_facts,
                       may=lambda actor, instruction, arguments: not enforce or self.passes(actor, instruction, arguments),
                       changed=self.forget, expected_version=expected_version)

    def prepare_active(self, facts: Iterable[Fact]) -> dict[str, Any]:
        """Turning the permission system on: bring the active scheme's instance up to date (补齐)."""
        from .facts import flatten
        from .schemes import prepare

        scheme_id = self.schemes.active()
        report = prepare(self._context(), scheme_id, flatten(facts), dry_run=False)
        self.forget()
        return {"scheme_id": scheme_id, **report}

    def complete_active(self, facts: Iterable[Fact], *, max_removed: int | None = None) -> dict[str, Any] | None:
        """补齐 the active scheme's saved instance from ``facts``, keeping every row (§5.7); None when it has none.

        Repairs what a sync after commit missed.  It never generates: an
        instance generated from other rules stays as it is.
        """
        scheme_id = self.schemes.active()
        # The instance's rules before its scheme's: an automatic merge in between is caught under the lock.
        saved = self.store.scoped(scheme_id).instance()
        if saved is None:
            return None
        instance = Instance(self.schemes.compiled(scheme_id), self.store)
        report = instance.complete(facts, max_removed=max_removed, rules_digest=saved["rules_digest"])
        self.forget()
        return {"scheme_id": scheme_id, **report}

    def follow_sources(self, collect_facts: Callable[[], list], *, max_removed: int | None = None) -> dict[str, Any]:
        """Merge into every copy of a built-in scheme the changes a release made to its source (§5.11).

        Three-way per unit of the rules (``scheme.merge``): what the copy left
        as it was takes the source's new version; what both changed keeps the
        copy's and is a conflict.  The source record moves to the source's
        rules.  The instance stays when only instruction mappings or
        descriptions changed; otherwise it is generated again, keeping the
        rows people wrote (``Instance.regenerate_keeping_edits``).  Conflicts
        and rows not written back are kept for the editor
        (GetPermissionScheme ``source.last_merge``).  One scheme failing does
        not stop the others; it is reported with ``error`` and nothing of it is
        written.  Returns ``{scheme_id: report}`` for the copies merged.
        """
        reports: dict[str, Any] = {}
        for scheme_id, source in sorted(self.schemes.sources().items()):
            if not source["outdated"] or not self.schemes.builtin(source["scheme_id"]):
                continue
            try:
                report = self._follow(scheme_id, collect_facts, max_removed=max_removed)
            except Exception as exc:  # noqa: BLE001 - reported per scheme, the others still merge
                LOGGER.exception("permission scheme %s: merging its source failed", scheme_id)
                report = {"error": f"{exc.__class__.__name__}: {exc}"}
            if report is not None:
                reports[scheme_id] = report
        if reports:
            self.forget()
        return reports

    def _follow(self, scheme_id: str, collect_facts: Callable[[], list], *, max_removed: int | None) -> dict[str, Any] | None:
        from .schemes import DIFF_LIMIT
        from .scheme.merge import merge

        with self.store.transaction() as connection:
            record = self.schemes.locked_source(scheme_id, connection)
            if record is None or not self.schemes.builtin(record["scheme_id"]):
                return None
            current = self.schemes.get(record["scheme_id"], connection)["rules"]
            to_digest = source_digest(current)
            if record["digest"] == to_digest:
                return None  # another process merged it meanwhile
            found = self.schemes.get(scheme_id, connection)
            merged, conflicts = merge(record["rules"], current, found["rules"])
            previous = self.schemes.compiled(scheme_id, connection)
            rules = self.schemes.compile(merged)
            version = found["version"]
            if merged != found["rules"]:
                version = self.schemes.update(scheme_id, merged, expected_version=version, actor="portal", connection=connection)
            self.schemes.record_source(scheme_id, record["scheme_id"], connection=connection)
            saved = self.store.scoped(scheme_id).instance(connection)
            instance = None
            if saved is not None and saved["rules_digest"] != rules.rules_digest:
                instance = Instance(rules, self.store).regenerate_keeping_edits(
                    collect_facts(), previous, max_removed=max_removed, auth_limit=DIFF_LIMIT, connection=connection)
            unrestored = instance.pop("unrestored") if instance else []
            deleted_again = instance.pop("deleted_again") if instance else []
            # What the merge granted and took away (§5.12); the editor's last-merge panel shows it.
            auth_diff = instance.pop("auth_diff") if instance else authdiff.kept(
                previous, rules, self.store.scoped(scheme_id), limit=DIFF_LIMIT, connection=connection)
            self.schemes.record_merge(scheme_id, record["scheme_id"], from_digest=record["digest"], to_digest=to_digest,
                                      conflicts=conflicts, unrestored=unrestored, deleted_again=deleted_again,
                                      merged_by="portal", auth_diff=auth_diff, regenerated=instance is not None,
                                      connection=connection)
        return {"source_scheme_id": record["scheme_id"], "from_digest": record["digest"], "to_digest": to_digest,
                "version": version, "conflicts": conflicts, "instance": instance, "unrestored": unrestored,
                "deleted_again": deleted_again, "auth_diff": auth_diff}

    def stale_builtin(self) -> list[str]:
        """Built-in schemes whose saved instance a release left behind their rules (§5.11).

        A built-in scheme's rules follow the code: when a release changes
        them, its own instance takes the new rules (``follow_release``) and no
        other scheme is touched.  One never generated waits until it is
        activated or copied, unless it is active already.  An instance saved
        before the whole rules were kept with it, and still on the current
        ones, keeps them now as the basis of the next release.
        """
        active = self.schemes.active()
        stale = []
        for scheme_id in self.schemes.builtin_ids():
            store = self.store.scoped(scheme_id)
            saved = store.instance()
            if saved is None and scheme_id != active:
                continue
            rules = self.schemes.compiled(scheme_id)
            if saved is None or saved["rules_digest"] != rules.rules_digest:
                stale.append(scheme_id)
                continue
            basis = store.basis()
            if basis is None:
                with store.transaction() as connection:
                    store.set_basis(connection, dict(rules.source))
            elif source_digest(basis) != source_digest(rules.source):
                # The release changed what the instance is not generated from (instruction classifications, titles).
                stale.append(scheme_id)
        return stale

    def follow_release(self, scheme_id: str, facts: Iterable[Fact], *, max_removed: int | None = None) -> dict[str, Any] | None:
        """Give a built-in scheme's instance the rules a release changed, keeping the edits made in it (§5.11).

        ``schemes.follow_release``; activating the scheme or turning the
        system on before this ran writes back the same way (``schemes.prepare``).
        Returns the report in the shape of a merge, or None when another
        process applied the rules meanwhile.
        """
        from .schemes import follow_release

        report = follow_release(self.schemes, self.store, scheme_id, list(facts), max_removed=max_removed)
        self.forget()
        return report

    def regenerate(self, facts: Iterable[Fact], scheme_id: str | None = None) -> dict[str, Any]:
        """Generate a scheme's instance (the active one when omitted) again from its rules and ``facts``."""
        scheme_id = scheme_id or self.schemes.active()
        report = Instance(self.schemes.compiled(scheme_id), self.store).generate(facts)
        self.forget()
        return {"scheme_id": scheme_id, **report}

    def maintain(self) -> None:
        from . import temporary

        temporary.expire(self.store)

    def passes(self, actor: str, instruction: str, arguments: Mapping[str, Any] | None = None) -> bool:
        """Whether ``actor`` passes ``instruction`` called with ``arguments`` under the active scheme."""
        from .administrators import passes as administrator_passes

        if _is_portal(actor) or administrator_passes(actor, instruction, arguments):
            return True

        def build(scheme: CompiledScheme) -> list[tuple[str, str]]:
            return [(request.action, request.resource) for request in self._requests(scheme, instruction, arguments or {})]

        try:
            return all(decision.allowed for decision in self._decide_built(actor, build)[2])
        except (Unresolved, RecordNotFound):
            return False

    def manage(self, instruction: str, arguments: Mapping[str, Any], *, actor: str, collect_facts=None,
               enforce: bool = True, expected_version: Any = None) -> dict[str, Any]:
        """Answer a permission instruction; rule problems keep their JSON paths in the error details.

        ``enforce`` is off while the permission system is off: the instruction
        does what it says, and nothing asks whether the caller may.
        """
        from . import schemes
        from .management import ACTIONS, Management

        if instruction in schemes.ACTIONS:
            context = self._context(collect_facts, enforce=enforce, expected_version=expected_version)
            return schemes.manage(context, instruction, arguments, actor=actor)
        with schemes.instruction_errors():
            return getattr(Management(self), ACTIONS[instruction])(actor, arguments)

    def prompt_rule(self) -> str:
        return PROMPT_RULE
