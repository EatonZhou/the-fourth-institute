"""Scheme and entry instructions (decision_22227d0ad075), answered the same way with the permission system on or off.

A permission scheme is its rules plus the instance saved with it.  These
instructions read and change both:

- creating a scheme copies another scheme's rules and instance, or
  generates the instance of new rules from the current objects;
- changing the rules of a custom scheme generates its instance again (the
  edits made in the instance are dropped); ``dry_run`` reports what would
  be added, removed and lost, and changing only titles, descriptions or the
  instruction map keeps the instance.  Rules saved as a merge of the
  source's changes (``source_digest``) keep the edits, as an automatic
  merge does (§5.11), and are recorded as the copy's last merge.  A
  built-in scheme's rules are read-only; it is used as it is: activated,
  and its instance edited, like any other;
- activating a scheme brings its saved instance up to date with the
  objects created, moved or deleted since (补齐) and makes the permission
  system use it; with the permission system off it only records the
  choice, and turning the permission system on does the rest;
- both report, dry or saved, what principals gain and lose on the current
  objects (``auth_diff``, ``authdiff``): saved rules against the instance
  they replace, an activated scheme against the active one;
- entry instructions list, add, change, disable and delete rows of any
  scheme's instance (the active one when ``scheme_id`` is omitted).  A row
  is checked for nodes that exist and nothing else; what rows mean
  together is up to whoever writes them.

Who may call each instruction is the scheme's instruction map, judged at the
entry; changing or deleting a stored row is judged there on the row's saved
``r`` (``via: permission_entry.r``).  With the permission system off,
everyone signed in may (``may`` answers yes).
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from . import authdiff
from .engine.model import DENY, PERMIT, Entry
from .engine.store import Content, EntryConflict, Store, new_entry_id
from .scheme import vocabulary
from .scheme.compiler import SchemeError
from .scheme.repository import SchemeConflict, SchemeNotFound, Schemes

DIFF_LIMIT, DIFF_MAX = 50, 500
# The scheme editor's buttons; GetPermissionSystem reports whether the caller passes each.
EDITOR_INSTRUCTIONS = ("CreatePermissionScheme", "UpdatePermissionScheme", "DeletePermissionScheme", "ActivatePermissionScheme")
# Entry buttons are judged on each row's r: ListPermissionEntries answers them for the r asked and per row.
ENTRY_INSTRUCTIONS = ("CreatePermissionEntry", "UpdatePermissionEntry", "DeletePermissionEntry")
ENTRY_LIMIT, ENTRY_MAX = 100, 500
SCOPES = ("exact", "subtree", "ancestors")


def text(arguments: Mapping[str, Any], name: str, *, required: bool = False) -> str:
    value = arguments.get(name)
    if value is None or value == "":
        if required:
            raise ValueError(f"缺少 {name}")
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value.strip()


def _integer(arguments: Mapping[str, Any], name: str, default: int, *, minimum: int, maximum: int) -> int:
    value = arguments.get(name, default)
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{name} 为 {minimum} 至 {maximum} 的整数")
    return value


def _flag(arguments: Mapping[str, Any], name: str) -> bool | None:
    value = arguments.get(name)
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{name} 必须是布尔值")
    return value


@contextmanager
def instruction_errors() -> Iterator[None]:
    """Rule problems keep their JSON paths in the error details; a version conflict names the object and its current version."""
    from management_portal.hatg.contracts import InstructionError

    try:
        yield
    except SchemeError as exc:
        raise InstructionError("instruction.invalid_arguments", str(exc), details={"errors": exc.errors}) from exc
    except (SchemeConflict, EntryConflict) as exc:
        if exc.current_version is not None:
            raise InstructionError("instruction.version_conflict", str(exc), details={
                "object_id": exc.object_id, "expected_version": exc.expected_version,
                "current_version": exc.current_version}) from exc
        raise InstructionError("instruction.conflict", str(exc)) from exc


@dataclass
class Context:
    """What the instructions work with: the scheme repository on the permission store, and the mode's answers."""

    schemes: Schemes
    enabled: bool
    # Every live Portal object, read inside ResourceService; needed to generate or bring up to date an instance.
    collect_facts: Callable[[], list] | None
    # Whether ``actor`` passes ``instruction`` called with ``arguments``; always yes with the permission system off.
    may: Callable[[str, str, Mapping[str, Any]], bool]
    # Called after the active scheme or its instance changed, so this process loads it again.
    changed: Callable[[], None] = lambda: None
    # The call envelope's version token for the scheme or entry the call targets; None compares nothing.
    expected_version: Any = None

    @property
    def store(self) -> Store:
        return self.schemes.store

    def expected(self) -> int | None:
        token = self.expected_version
        if token is None:
            return None
        if isinstance(token, bool) or not str(token).strip().isdigit():
            raise ValueError("expected_version 必须是读取时得到的整数 version")
        return int(token)

    def facts(self) -> list:
        from .facts import flatten

        if self.collect_facts is None:
            raise RuntimeError("生成权限实例需要 ResourceService")
        return flatten(self.collect_facts())


def unmapped(document: Any) -> list[str]:
    """The A elements ``document`` does not classify: they sit under the A default node (default scheme: administrators only)."""
    mapped = document.get("instructions") if isinstance(document, Mapping) else None
    return sorted(set(_elements()) - set(mapped if isinstance(mapped, Mapping) else ()))


def _elements() -> dict[str, tuple[str, Any]]:
    """A element -> (instruction, decision slot), for every installed instruction."""
    from management_portal.hatg.declarations import decision_elements

    from .scheme import repository

    return {element: (name, slot) for name, decides in repository.declared_decisions().items()
            for element, slot in decision_elements(name, decides)}


# -- schemes ----------------------------------------------------------------------------


def system(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    from . import OPEN, PAR

    scheme_id = context.schemes.active()
    found = context.schemes.get(scheme_id)
    compiled = context.schemes.compiled(scheme_id)
    saved = context.store.scoped(scheme_id).instance()
    # One answer per button: each asks the instruction it calls.
    allows = {name: context.may(actor, name, {"scheme_id": scheme_id}) for name in EDITOR_INSTRUCTIONS}
    allows.update(dict.fromkeys(ENTRY_INSTRUCTIONS, True))
    return {"enabled": context.enabled, "mode": PAR if context.enabled else OPEN,
            "scheme": {**compiled.summary(), "version": found["version"], "builtin": found["builtin"]},
            "instance": saved or {},
            "needs_regenerate": saved is None or saved["rules_digest"] != compiled.rules_digest,
            "allows": allows}


def list_schemes(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    active = context.schemes.active()
    sources = context.schemes.sources()
    return {"schemes": [{**item, "active": item["scheme_id"] == active,
                         "source_scheme_id": sources.get(item["scheme_id"], {}).get("scheme_id", ""),
                         "outdated": sources.get(item["scheme_id"], {}).get("outdated", False)}
                        for item in context.schemes.list()]}


def get_scheme(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    active = context.schemes.active()
    scheme_id = text(arguments, "scheme_id") or active
    found = context.schemes.get(scheme_id)
    result = {"scheme_id": scheme_id, "rules": found["rules"], "builtin": found["builtin"], "version": found["version"],
              "active": scheme_id == active, "unmapped": unmapped(found["rules"])}
    for name in ("created_by", "updated_by", "updated_at"):
        if name in found:
            result[name] = found[name]
    source = context.schemes.source(scheme_id)
    if source is not None:
        result["source"] = source
    elif found["builtin"] and (merged := context.schemes.last_merge(scheme_id)) is not None:
        # The last release that changed the rules, applied to the instance keeping its edits (§5.11).
        result["last_merge"] = merged
    if arguments.get("vocabulary"):
        result["vocabulary"] = {**vocabulary.describe(), "instructions": _instruction_vocabulary()}
    return result


def _checked(context: Context, document: Any) -> dict[str, Any]:
    try:
        context.schemes.compile(document)
    except SchemeError as exc:
        return {"valid": False, "errors": exc.errors, "unmapped": unmapped(document)}
    return {"valid": True, "errors": [], "unmapped": unmapped(document)}


def create_scheme(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """A new scheme: a copy of another scheme's rules and instance, or new rules with an instance generated from the objects."""
    from .facts import Instance

    scheme_id = text(arguments, "scheme_id", required=True)
    source = text(arguments, "source_scheme_id")
    rules = arguments.get("rules")
    if bool(source) == (rules is not None):
        raise ValueError("需要 source_scheme_id 或 rules 之一")
    if source:
        document = dict(context.schemes.get(source)["rules"])
        document["scheme_id"] = scheme_id
        document["title"] = text(arguments, "title") or f"{document.get('title') or source} 副本"
    elif isinstance(rules, Mapping):
        document = dict(rules)
        document["scheme_id"] = scheme_id
    else:
        raise ValueError("rules 必须是方案规则文档")
    for name in ("title", "description"):
        if text(arguments, name):
            document[name] = text(arguments, name)
    if arguments.get("dry_run"):
        return _checked(context, document)
    facts = None if source else context.facts()
    store = context.store
    with store.transaction() as connection:
        compiled = context.schemes.create(document, actor=actor, connection=connection)
        target = store.scoped(scheme_id)
        if source:
            # The copy remembers its source's rules, so a change to them can be merged later (§5.11).
            context.schemes.record_source(scheme_id, source, connection=connection)
            origin = store.scoped(source)
            saved = origin.instance(connection)
            if saved is not None:
                origin.copy_to(connection, scheme_id)
                target.set_instance(connection, saved["rules_digest"], origin.basis(connection))
                report = {"nodes": len(target.nodes(connection)), "entries": target.count(connection)}
            else:
                facts = context.facts()
        if facts is not None:
            report = Instance(compiled, store).generate(facts, connection=connection)
    return {"scheme_id": scheme_id, "version": 1, **report}


def _content(item: Content, disabled: bool = False) -> dict[str, Any]:
    effect, p, a, r = item
    return {"effect": effect, "p": p, "a": a, "r": r, "disabled": disabled}


def _listed(items: Sequence[Any], limit: int) -> dict[str, Any]:
    return {"total": len(items), "items": list(items[:limit])}


def regeneration_diff(context: Context, scheme_id: str, rules: Any, facts: list, *, limit: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """What generating the instance of ``scheme_id`` from ``rules`` would add, remove and lose of the edits made in it,
    and what principals would gain and lose (§5.12)."""
    from .facts import Instance

    store = context.store
    old = context.schemes.compiled(scheme_id)
    new = context.schemes.compile(rules)
    active = {fact.id: fact for fact in facts}
    instance = Instance(new, store)
    creators = instance.creators(active)
    with store.read() as connection:
        present = instance.store.contents(connection)
        nodes = instance.store.nodes(connection)
    target = instance.generated(active.values(), creators)
    auth = authdiff.compare(authdiff.View(old, nodes, present),
                            authdiff.View(new, instance.targets(active), dict.fromkeys(target, False)), limit=limit)
    baseline = Instance(old, store).generated(active.values(), creators)
    added = sorted(target - present.keys())
    removed = sorted(present.keys() - target)
    lost = ([{"kind": "added", "entry": _content(item, present[item])} for item in sorted(present.keys() - baseline - target)]
            + [{"kind": "deleted", "entry": _content(item)} for item in sorted((baseline - present.keys()) & target)]
            + [{"kind": "disabled", "entry": _content(item, True)} for item, disabled in sorted(present.items()) if disabled])
    return {"added": _listed([_content(item) for item in added], limit),
            "removed": _listed([_content(item, present[item]) for item in removed], limit),
            "lost_edits": _listed(lost, limit)}, auth


def update_scheme(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Replace a custom scheme's rules; when what the instance is generated from changes, generate it again."""
    from .facts import Instance

    scheme_id = text(arguments, "scheme_id", required=True)
    rules = arguments.get("rules")
    limit = _integer(arguments, "diff_limit", DIFF_LIMIT, minimum=1, maximum=DIFF_MAX)
    if context.schemes.builtin(scheme_id):
        raise SchemeConflict("内置方案不可修改；请复制为新方案")
    context.schemes.get(scheme_id)  # a scheme that does not exist is not found, whatever the rules say
    # A merge of the source's changes moves the source record to the source rules it was made against (§5.11).
    merged = text(arguments, "source_digest")
    if merged:
        source = context.schemes.source(scheme_id)
        if source is None:
            raise ValueError(f"方案 {scheme_id} 没有记录来源方案，不能带 source_digest")
        if source["current_digest"] != merged:
            raise SchemeConflict(f"来源方案 {source['scheme_id']} 的规则摘要现在是 {source['current_digest'] or '（已删除）'}，"
                                 f"不是 {merged}；请重新读取来源后再合并")
    if not isinstance(rules, Mapping) or rules.get("scheme_id") != scheme_id:
        error = SchemeError([{"path": "scheme_id", "message": "方案 ID 不能修改"}])
        if arguments.get("dry_run"):
            return {"valid": False, "errors": error.errors, "unmapped": unmapped(rules)}
        raise error
    rules = dict(rules)
    checked = _checked(context, rules)
    if arguments.get("dry_run") and not checked["valid"]:
        return checked
    new = context.schemes.compile(rules)
    saved = context.store.scoped(scheme_id).instance()
    regenerate = saved is None or saved["rules_digest"] != new.rules_digest
    if arguments.get("dry_run") and _flag(arguments, "diff") is False:
        # Checking while editing: compiling is enough; the diff reads every object.
        return {**checked, "regenerates": regenerate}
    empty = {"total": 0, "items": []}
    store = context.store
    facts = context.facts() if regenerate else []
    # A merge writes back what people did to the instance, the same as an automatic merge (§5.11);
    # rules a person edits and saves generate the instance again (§5.3).
    keeping = bool(merged) and saved is not None and regenerate
    kept: dict[str, Any] = {"unrestored": [], "deleted_again": []}
    previous = context.schemes.compiled(scheme_id)
    if keeping:
        if arguments.get("dry_run"):
            report = Instance(new, store).regenerate_keeping_edits(facts, previous, changes=True, auth_limit=limit, dry_run=True)
            return {**checked, "diff": _merge_diff(report, limit), "auth_diff": report["auth_diff"], "regenerates": True,
                    **{name: report[name] for name in kept}}
    else:
        diff, auth = (regeneration_diff(context, scheme_id, rules, facts, limit=limit) if regenerate
                      else ({"added": empty, "removed": empty, "lost_edits": empty}, authdiff.kept(previous, new, context.store.scoped(scheme_id), limit=limit)))
        if arguments.get("dry_run"):
            return {**checked, "diff": diff, "auth_diff": auth, "regenerates": regenerate}
    with store.transaction() as connection:
        version = context.schemes.update(scheme_id, rules, expected_version=context.expected(), actor=actor,
                                         connection=connection)
        if merged and context.schemes.record_source(scheme_id, source["scheme_id"], connection=connection) != merged:
            raise SchemeConflict(f"来源方案 {source['scheme_id']} 的规则刚刚改变；请重新读取来源后再合并")
        if keeping:
            report = Instance(new, store).regenerate_keeping_edits(facts, previous, changes=True, auth_limit=limit,
                                                                   connection=connection)
            diff = _merge_diff(report, limit)
            kept = {name: report.pop(name) for name in kept}
            auth = report.pop("auth_diff")
            regenerated = {name: value for name, value in report.items() if name not in {"added", "removed"}}
        else:
            regenerated = Instance(new, store).generate(facts, connection=connection) if regenerate else None
        if merged:
            context.schemes.record_merge(scheme_id, source["scheme_id"], from_digest=source["digest"], to_digest=merged,
                                         conflicts=[], merged_by=actor, auth_diff=auth, regenerated=regenerate,
                                         connection=connection, **kept)
    context.changed()
    result = {"scheme_id": scheme_id, "version": version, "diff": diff, "auth_diff": auth, "regenerated": regenerated}
    if merged:
        result.update(source_digest=merged, **kept)
    return result


def _merge_diff(report: Mapping[str, Any], limit: int) -> dict[str, Any]:
    """A merge's changes to the instance in the shape of ``regeneration_diff``; the only lost edits are the rows not written back."""
    return {"added": _listed([_content(row[:4], row[4]) for row in report["added"]], limit),
            "removed": _listed([_content(row[:4], row[4]) for row in report["removed"]], limit),
            "lost_edits": _listed([{"kind": "unrestored", "entry": {name: item[name] for name in ("effect", "p", "a", "r", "disabled")},
                                    "missing": item["missing"]} for item in report["unrestored"]], limit)}


def delete_scheme(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    scheme_id = text(arguments, "scheme_id", required=True)
    context.schemes.delete(scheme_id, expected_version=context.expected())
    return {"deleted": scheme_id}


def follow_release(schemes: Schemes, store: Store, scheme_id: str, facts: list, *, max_removed: int | None = None,
                   connection=None) -> dict[str, Any] | None:
    """Give a built-in scheme's instance the rules a release changed, keeping the edits made in it (§5.11).

    The same write-back as a merge into a copy
    (``Instance.regenerate_keeping_edits``), against the rules the instance
    was generated from: rows added, disabled or deleted in the instance and
    temporary credentials stay; those it cannot write back and the rows it
    leaves deleted are recorded with the authorization difference as the
    scheme's last merge (GetPermissionScheme ``last_merge``).  An instance
    with no rules kept to compare against (never generated, or saved before
    they were kept) is generated.  Runs in ``connection``'s transaction when
    given, so activating a scheme or turning the system on writes back in
    its own.  A release that changed only what the instance is not generated
    from (instruction classifications, titles) leaves the instance as it is
    (``instance`` None): the basis moves on to the new rules, and moved
    elements are recorded as the last merge with what they grant and take
    away (§5.12).  Returns the report in the shape of a merge, or None when
    the instance is on the rules already (another process applied them meanwhile).
    """
    from .facts import Instance
    from .scheme.repository import source_digest

    rules = schemes.compiled(scheme_id, connection)
    to_digest = source_digest(rules.source)
    scoped = store.scoped(scheme_id)
    with store.transaction(connection) as connection:
        # One release at a time: a second process waits here and finds the instance on the new rules.
        store.execute(connection, "SELECT pg_advisory_xact_lock(hashtext('permission.follow_release'), hashtext(%s))",
                      (scheme_id,))
        saved = scoped.instance(connection)
        if saved is not None and saved["rules_digest"] == rules.rules_digest:
            basis = scoped.basis(connection)
            if basis is None or source_digest(basis) == to_digest:
                return None
            previous = schemes.compile(basis, saved=True, before=True)
            report = {"from_digest": source_digest(basis), "instance": None, "unrestored": [], "deleted_again": [],
                      "auth_diff": authdiff.kept(previous, rules, scoped, limit=DIFF_LIMIT, connection=connection)}
            scoped.set_basis(connection, dict(rules.source), replace=True)
            if report["auth_diff"]["remapped"]["total"]:
                schemes.record_merge(scheme_id, scheme_id, from_digest=report["from_digest"], to_digest=to_digest,
                                     conflicts=[], unrestored=[], deleted_again=[], merged_by="portal",
                                     auth_diff=report["auth_diff"], regenerated=False, connection=connection)
            return {"source_scheme_id": scheme_id, "to_digest": to_digest, "version": 0, "conflicts": [], **report}
        basis = scoped.basis(connection) if saved is not None else None
        if basis is None:
            instance = Instance(rules, store).generate(facts, connection=connection)
            report = {"from_digest": "", "instance": instance, "unrestored": [], "deleted_again": [], "auth_diff": None}
        else:
            previous = schemes.compile(basis, saved=True, before=True)
            instance = Instance(rules, store).regenerate_keeping_edits(
                facts, previous, max_removed=max_removed, auth_limit=DIFF_LIMIT, connection=connection)
            report = {"from_digest": source_digest(basis), "instance": instance, "unrestored": instance.pop("unrestored"),
                      "deleted_again": instance.pop("deleted_again"), "auth_diff": instance.pop("auth_diff")}
            schemes.record_merge(scheme_id, scheme_id, from_digest=report["from_digest"], to_digest=to_digest,
                                 conflicts=[], unrestored=report["unrestored"], deleted_again=report["deleted_again"],
                                 merged_by="portal", auth_diff=report["auth_diff"], regenerated=True, connection=connection)
    return {"source_scheme_id": scheme_id, "to_digest": to_digest, "version": 0, "conflicts": [], **report}


def prepare(context: Context, scheme_id: str, facts: list, *, dry_run: bool, view: bool = False,
            connection=None) -> dict[str, Any]:
    """Bring the saved instance of ``scheme_id`` up to date (补齐); one never generated, or generated from other rules, is generated.

    A built-in scheme whose instance a release left behind its rules takes
    the new ones keeping the edits made in it, as at startup
    (``follow_release``): activating it or turning the system on before the
    startup merge ran, or after it failed, loses nothing.  ``view`` adds the
    instance it leaves (or, dry, would leave) as ``view``, an ``authdiff.View``.
    """
    from .facts import Instance, _RolledBack

    outer = connection is not None
    compiled = context.schemes.compiled(scheme_id, connection)
    instance = Instance(compiled, context.store)
    with instance.store.transaction(connection) as connection:
        saved = instance.store.instance(connection)
        if saved is not None and saved["rules_digest"] == compiled.rules_digest:
            return _completed(instance, facts, dry_run=dry_run, view=view, connection=connection)
        before = {"removed_nodes": len(instance.store.nodes(connection)),
                  "removed_entries": instance.store.count(connection, where="expires_at IS NULL")}
        keeping = saved is not None and context.schemes.builtin(scheme_id) and instance.store.basis(connection) is not None
        if keeping and not dry_run:
            return _written_back(context, scheme_id, facts, before, dry_run=False, view=view, connection=connection)
        if not keeping and dry_run:
            generated_view = authdiff.View.generated(compiled, context.store, facts)
            generated = {"nodes": len(generated_view.nodes), "entries": len(generated_view.entries)}
        elif not keeping:
            generated = instance.generate(facts, connection=connection)
            generated_view = authdiff.View.stored(compiled, instance.store, connection) if view else None
    if keeping:
        # A dry run writes back in a transaction of its own and rolls it back, so the report says what activating would do.
        if outer:
            raise ValueError("dry_run 在自己的事务中执行并回滚")
        try:
            with context.store.transaction() as own:
                raise _RolledBack(_written_back(context, scheme_id, facts, before, dry_run=True, view=view, connection=own))
        except _RolledBack as rolled:
            return rolled.report
    report = {"regenerate": True, "added_nodes": generated["nodes"], "moved_nodes": 0, "added_entries": generated["entries"], **before}
    if view:
        report["view"] = generated_view
    return report


def _completed(instance, facts: list, *, dry_run: bool, view: bool, connection) -> dict[str, Any]:
    """``prepare``'s report for an instance already on its scheme's rules: brought up to date (补齐)."""
    report = {"regenerate": False, **instance.complete(facts, dry_run=dry_run, preview=dry_run and view,
                                                      connection=connection)}
    if view:
        report["view"] = report.pop("preview") if dry_run else authdiff.View.stored(instance.scheme, instance.store, connection)
    return report


def _written_back(context: Context, scheme_id: str, facts: list, before: Mapping[str, int], *, dry_run: bool, view: bool,
                  connection) -> dict[str, Any]:
    """``prepare``'s report for a built-in instance that took a release's rules keeping its edits (``follow_release``)."""
    from .facts import Instance

    followed = follow_release(context.schemes, context.store, scheme_id, facts, connection=connection)
    if followed is None or followed["instance"] is None:
        # Another process followed the release after ``prepare`` read the instance: it is on the rules now.
        return _completed(Instance(context.schemes.compiled(scheme_id, connection), context.store), facts,
                          dry_run=dry_run, view=view, connection=connection)
    instance = followed["instance"]
    report = {"regenerate": True, "added_nodes": instance["nodes"], "moved_nodes": 0, "added_entries": instance["entries"],
              **before, "restored_entries": instance.get("restored_entries", 0),
              "unrestored": followed["unrestored"], "deleted_again": followed["deleted_again"]}
    if view:
        report["view"] = authdiff.View.stored(context.schemes.compiled(scheme_id, connection),
                                              context.store.scoped(scheme_id), connection)
    return report


def activate_scheme(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Make the permission system use a scheme, built-in or custom (§5.11); on, its instance is brought up to date first,
    in the same transaction."""
    scheme_id = text(arguments, "scheme_id", required=True)
    compiled = context.schemes.compiled(scheme_id)
    dry_run = bool(arguments.get("dry_run"))
    limit = _integer(arguments, "diff_limit", DIFF_LIMIT, minimum=1, maximum=DIFF_MAX)
    facts = context.facts()
    # The instance the permission system uses now, against the one it will use (§5.12).
    current = context.schemes.active()
    was = authdiff.View.stored(context.schemes.compiled(current), context.store.scoped(current))
    result = {"scheme_id": scheme_id, "title": compiled.title, "unmapped": compiled.unmapped()}
    if dry_run or not context.enabled:
        # With the permission system off this is what turning it on will do.
        result.update(prepare(context, scheme_id, facts, dry_run=True, view=True))
        if not dry_run:
            context.schemes.activate(scheme_id, actor=actor)
    else:
        with context.store.transaction() as connection:
            result.update(prepare(context, scheme_id, facts, dry_run=False, view=True, connection=connection))
            context.schemes.activate(scheme_id, actor=actor, connection=connection)
        context.changed()
    result["auth_diff"] = authdiff.compare(was, result.pop("view"), limit=limit)
    return {**result, "activated": not dry_run}


def _instruction_vocabulary() -> list[dict[str, Any]]:
    from management_portal.hatg.catalog import instruction_catalog

    instructions = []
    for name, declaration in sorted(instruction_catalog().items()):
        instructions.append({
            "name": name, "description": declaration.description, "category": declaration.category,
            "mutates": declaration.mutates,
            "parameters": [{"name": parameter.name, "type": parameter.type, "description": parameter.description}
                           for parameter in declaration.parameters],
            # The A elements the scheme classifies: what each slot judges is fixed by the declaration.
            "elements": [{"element": element, **slot.to_dict()} for element, slot in declaration.elements],
        })
    return instructions


# -- nodes ------------------------------------------------------------------------------------


def _scheme(context: Context, arguments: Mapping[str, Any]):
    """The scheme an instruction names (the active one when omitted), compiled, and the Store of its instance."""
    scheme_id = text(arguments, "scheme_id") or context.schemes.active()
    return context.schemes.compiled(scheme_id), context.store.scoped(scheme_id)


def _tree(arguments: Mapping[str, Any]) -> str:
    tree = text(arguments, "tree", required=True)
    if tree not in {"P", "A", "R"}:
        raise ValueError("tree 只能是 P、A、R")
    return tree


def _path(scheme, store: Store, tree: str, node: str, connection=None) -> list[str]:
    stored = store.paths(tree, [node], connection).get(node) if tree != "A" else None
    return list(stored or scheme.trees[tree].element_path(node))


def node(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    tree = _tree(arguments)
    node_id = text(arguments, "node_id", required=True)
    limit = _integer(arguments, "limit", 100, minimum=1, maximum=500)
    scheme, store = _scheme(context, arguments)
    declared = scheme.trees[tree].nodes
    path = _path(scheme, store, tree, node_id)
    found = declared.get(node_id)
    view = {"id": node_id, "kind": "class" if node_id.startswith("@") else "element",
            "title": found.title if found is not None else "", "parent": path[-2] if len(path) > 1 else "", "path": path}
    children = [{"id": child.id, "kind": "class" if child.id.startswith("@") else "element", "title": child.title}
                for child in declared.values() if child.parent == node_id]
    cursor = text(arguments, "cursor")
    next_cursor = ""
    if tree != "A":
        rows = store.children(tree, node_id, after=cursor, limit=limit + 1)
        children += [{"id": row["id"], "kind": "element", "object_type": row["kind"]} for row in rows[:limit]]
        next_cursor = rows[limit - 1]["id"] if len(rows) > limit else ""
    return {"scheme_id": scheme.scheme_id, "tree": tree, "node": view, "children": children, "next_cursor": next_cursor}


def search_nodes(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Class nodes and elements whose ID or title contains ``query``: declared nodes first, then stored elements."""
    tree = _tree(arguments)
    query = text(arguments, "query", required=True)
    limit = _integer(arguments, "limit", 20, minimum=1, maximum=100)
    scheme, store = _scheme(context, arguments)
    needle = query.casefold()
    found: list[dict[str, Any]] = []
    for item in scheme.trees[tree].nodes.values():
        if needle in item.id.casefold() or needle in item.title.casefold():
            found.append({"id": item.id, "kind": "class" if item.id.startswith("@") else "element",
                          **({"title": item.title} if item.title else {}), "path": list(item.path)})
    if tree != "A":
        # The A tree holds every element; the other trees keep their objects in the instance.
        found += [{"id": row["id"], "kind": "element", "object_type": row["kind"], "path": row["path"]}
                  for row in store.search(tree, query, limit=limit)]
    return {"scheme_id": scheme.scheme_id, "tree": tree, "nodes": found[:limit]}


# -- entries ----------------------------------------------------------------------------------


def _known(scheme, store: Store, tree: str, node: str, connection) -> bool:
    """Whether ``node`` is a node of ``tree``: declared, stored, or an element the trees can hold."""
    if node in scheme.trees[tree].nodes:
        return True
    if tree == "A" or node.startswith("@"):
        return False
    if store.paths(tree, [node], connection):
        return True
    if tree == "P":
        from management_common.principal import actor_principal

        try:
            return actor_principal(node).kind in {"user", "resident"}
        except ValueError:
            return False
    from .requests import resource_type

    return bool(resource_type(node))


def _check_nodes(scheme, store: Store, connection, p: str, a: str, r: str) -> None:
    for tree, item in (("P", p), ("A", a), ("R", r)):
        if not _known(scheme, store, tree, item, connection):
            raise ValueError(f"{item} 不是 {tree} 树的节点")


def _effect(arguments: Mapping[str, Any], default: str | None = PERMIT) -> str | None:
    effect = text(arguments, "effect") or default
    if effect is not None and effect not in (PERMIT, DENY):
        raise ValueError("effect 只能是 permit 或 deny")
    return effect


def _scope_nodes(scheme, store: Store, tree: str, node: str, scope: str, connection) -> list[str] | None:
    """The nodes an entry's ``tree`` side may name to match ``node`` in ``scope``; None matches everything."""
    if scope == "exact":
        return [node]
    if scope == "ancestors":
        return _path(scheme, store, tree, node, connection)
    if node == scheme.trees[tree].root:
        return None
    nodes = [item.id for item in scheme.trees[tree].nodes.values() if node in item.path]
    if tree != "A":
        nodes += store.under(tree, node, connection)
    return list(dict.fromkeys([node, *nodes]))


def list_entries(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    from management_portal.hatg.declarations import cursor_offset, offset_cursor

    scheme, store = _scheme(context, arguments)
    offset = cursor_offset(arguments.get("cursor"))
    limit = _integer(arguments, "limit", ENTRY_LIMIT, minimum=1, maximum=ENTRY_MAX)
    if not (text(arguments, "p") or text(arguments, "r")):
        raise ValueError("p 和 r 至少给一个；列出整个实例用 r=@portal、r_scope=subtree")
    clauses: list[str] = []
    parameters: list[Any] = []
    with store.read() as connection:
        for tree, column in (("P", "p_node"), ("A", "a_node"), ("R", "r_node")):
            value = text(arguments, tree.lower())
            scope = text(arguments, f"{tree.lower()}_scope") or "exact"
            if scope not in SCOPES:
                raise ValueError(f"{tree.lower()}_scope 只能是 {'、'.join(SCOPES)}")
            if not value:
                continue
            nodes = _scope_nodes(scheme, store, tree, value, scope, connection)
            if nodes is not None:
                clauses.append(f"{column} = ANY(%s)")
                parameters.append(nodes)
        effect = _effect(arguments, None)
        if effect:
            clauses.append("effect = %s")
            parameters.append(effect)
        for name, clause in (("disabled", "disabled"), ("temporary", "expires_at IS NOT NULL")):
            flag = _flag(arguments, name)
            if flag is not None:
                clauses.append(clause if flag else f"NOT ({clause})")
        where = " AND ".join(clauses) or "TRUE"
        total = store.count(connection, where=where, parameters=parameters)
        entries = store.entries(connection, where=where, parameters=parameters, limit=limit, offset=offset,
                                order="p_node, a_node, r_node, entry_id")
    # Writing, changing and deleting entries on a resource all ask CreatePermissionEntry on it.
    writes = {}

    def may_write(r: str) -> bool:
        if r not in writes:
            writes[r] = context.may(actor, "CreatePermissionEntry", {"scheme_id": scheme.scheme_id, "r": r})
        return writes[r]

    listed = []
    for entry in entries:
        allowed = may_write(entry.r)
        listed.append({**entry.to_dict(), "allows": {"UpdatePermissionEntry": allowed, "DeletePermissionEntry": allowed}})
    result = {"scheme_id": scheme.scheme_id, "total": total, "entries": listed}
    if offset + len(entries) < total:
        result["next_cursor"] = offset_cursor(offset + len(entries))
    if r := text(arguments, "r"):
        # The grant and delete buttons beside a listed resource.
        result["allows"] = dict.fromkeys(("CreatePermissionEntry", "DeletePermissionEntry"), may_write(r))
    return result


def create_entry(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    scheme, store = _scheme(context, arguments)
    p, a, r = (text(arguments, name, required=True) for name in ("p", "a", "r"))
    disabled = _flag(arguments, "disabled") or False
    entry = Entry(new_entry_id(), _effect(arguments), p, a, r, disabled=disabled)
    with store.transaction() as connection:
        if store.instance(connection) is None:
            # Generating the instance later would drop the row: the scheme is activated (or its rules saved) first.
            raise EntryConflict(f"方案 {scheme.scheme_id} 还没有生成实例")
        _check_nodes(scheme, store, connection, p, a, r)
        if not store.add(connection, [entry]):
            existing = store.entries(connection, where="expires_at IS NULL AND effect = %s AND p_node = %s AND a_node = %s AND r_node = %s",
                                     parameters=entry.content)
            raise EntryConflict(f"已有相同内容的表项 {existing[0].entry_id if existing else ''}".strip())
    return {"scheme_id": scheme.scheme_id, "entry": entry.to_dict()}


def update_entry(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    scheme, store = _scheme(context, arguments)
    entry_id = text(arguments, "entry_id", required=True)
    expected = context.expected()
    with store.transaction() as connection:
        found = store.entries(connection, where="entry_id = %s", parameters=(entry_id,))
        if not found:
            raise KeyError(entry_id)
        current = found[0]
        p, a, r = (text(arguments, name) or getattr(current, name) for name in ("p", "a", "r"))
        disabled = _flag(arguments, "disabled")
        _check_nodes(scheme, store, connection, p, a, r)
        updated = store.update(connection, entry_id, expected_version=expected, effect=_effect(arguments, current.effect),
                               p=p, a=a, r=r, disabled=current.disabled if disabled is None else disabled)
    return {"scheme_id": scheme.scheme_id, "entry": updated.to_dict()}


def delete_entries(context: Context, actor: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    scheme, store = _scheme(context, arguments)
    ids = arguments.get("entry_ids")
    if isinstance(arguments.get("entry_id"), str):
        ids = [arguments["entry_id"]]
    if not isinstance(ids, list) or not 1 <= len(ids) <= 1000 or any(not isinstance(item, str) for item in ids):
        raise ValueError("需要 entry_id 或 1 至 1000 个 entry_ids")
    with store.transaction() as connection:
        found = {entry.entry_id: entry for entry in store.entries(connection, where="entry_id = ANY(%s)", parameters=(ids,))}
        for entry_id in ids:
            entry = found.get(entry_id)
            if entry is None:
                raise KeyError(entry_id)
            if entry.temporary:
                raise EntryConflict(f"{entry_id} 是临时凭证，用 RevokeTemporaryCredential 撤销")
        store.delete(connection, where="entry_id = ANY(%s)", parameters=(ids,))
    return {"scheme_id": scheme.scheme_id, "deleted": list(dict.fromkeys(ids))}


ACTIONS: dict[str, Callable[[Context, str, Mapping[str, Any]], dict[str, Any]]] = {
    "GetPermissionSystem": system,
    "ListPermissionSchemes": list_schemes,
    "GetPermissionScheme": get_scheme,
    "CreatePermissionScheme": create_scheme,
    "UpdatePermissionScheme": update_scheme,
    "DeletePermissionScheme": delete_scheme,
    "ActivatePermissionScheme": activate_scheme,
    "GetPermissionNode": node,
    "SearchPermissionNodes": search_nodes,
    "ListPermissionEntries": list_entries,
    "CreatePermissionEntry": create_entry,
    "UpdatePermissionEntry": update_entry,
    "DeletePermissionEntry": delete_entries,
}


def manage(context: Context, instruction: str, arguments: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
    with instruction_errors():
        return ACTIONS[instruction](context, actor, arguments)


__all__ = ["ACTIONS", "Context", "SchemeNotFound", "instruction_errors", "manage", "prepare", "text"]
