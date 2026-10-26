"""The fact adapter: Portal objects become elements of a scheme's trees (decision_b820faf42218 §3.3).

A ``Fact`` is what the permission system needs to know about one Portal
object: its type, the objects it is related to (the Project of a Lineage,
the Lineage of a Resident, the owner User of a personal AMA …), who created
it and the relations rules read (a Family's steward, a chatroom's members, a
User's category).  Every fact is read from the object's own record; the
permission system keeps none of its own (decision_b820faf42218 §5.7).
``Instance`` keeps the instance saved with one permission scheme in step with
the facts:

- a new object is placed under its related object in the P and R trees, and
  the rows the scheme's rules give that object are written, in the
  transaction that creates it (the caller passes its connection);
- an object that moves changes position only; its rows stay as they are, so
  rows edited by hand keep their edits;
- a Family that changes steward, or a chatroom that changes members, loses
  the rows the old relation gave and gets the rows of the new one;
- a deleted object leaves the trees with every row naming it.

``generate`` writes the whole instance from the rules (a scheme is created,
or its rules change: hand edits are dropped); ``complete`` brings a saved
instance up to date with objects created, moved or deleted while it was not
maintained (a scheme is activated, or the permission system is turned on),
keeping every row it already has.  ``regenerate_keeping_edits`` generates
from new rules and writes the hand edits back (a copy takes a release's
changes to its built-in source).

A record names its creator in ``created_by`` (``author`` for a post) and,
when a Resident created it while working on a User's input, that User in
``created_for``; both are creators.

A personal AMA Lineage has its owner User and that User's personal space as
related objects, and no Project.  Where it goes is the scheme's: the default
scheme places Lineages under each personal space (R), so the owner's
personal-space permission covers the whole AMA; in P a scheme may place them
under the owner User (``under: user``), the default scheme does not.  Its
Residents hold every permission the owner holds under any scheme: the
engine judges them as the owner (``par.represents``).  Nothing here places a
personal AMA by itself (decision_b820faf42218 §5.5).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .authdiff import View, compare
from .categories import DEFAULT as DEFAULT_CATEGORY
from .engine.store import Content, Relation, Store
from .scheme.compiler import CompiledScheme
from .scheme.vocabulary import OWNERS, PERSONAL_SPACE

# Relations an object can change after it is created (its steward, its members): their rows are swapped on change.
CHANGING = ("steward", "members")


def personal_space(username: str) -> str:
    """The R element that holds one User's personal resources."""
    return f"{PERSONAL_SPACE}:{username}"


def principal_id(value: Any) -> str:
    """The User or Resident ``value`` names, or "" (import scripts and services are not creators)."""
    from management_common.principal import actor_principal

    text = _text(value)
    if not text:
        return ""
    try:
        principal = actor_principal(text)
    except ValueError:
        return ""
    return principal.id if principal.kind in {"user", "resident"} else ""


def live(record: Mapping[str, Any]) -> bool:
    """Whether the record describes an object that exists (not deleted or retired)."""
    attributes = record.get("attributes") if isinstance(record.get("attributes"), Mapping) else {}
    status = _text(record.get("status") or attributes.get("status") or attributes.get("lifecycle"))
    return status not in {"deleted", "retired"} and not _text(record.get("deleted_at") or attributes.get("deleted_at"))


@dataclass(frozen=True)
class Fact:
    """One Portal object as the permission system sees it.

    ``owners`` maps a related type to the related object's ID.  ``dependents``
    are objects placed with this one (a Resident's own Workspace, a User's
    personal space).  ``on_behalf_of`` is the User a Resident creator created
    the object for; it is a creator too.  ``category`` is a User's category.
    """

    kind: str
    id: str
    owners: Mapping[str, str] = field(default_factory=dict)
    active: bool = True
    creator: str = ""
    steward: str = ""
    members: tuple[str, ...] = ()
    dependents: tuple["Fact", ...] = ()
    on_behalf_of: str = ""
    category: str = DEFAULT_CATEGORY

    @property
    def creators(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item for item in (self.creator, self.on_behalf_of) if item))

    def relations(self) -> frozenset[Relation]:
        """``(relation, principal, target)`` the steward and members rules read: a steward manages its Family's Lineage."""
        found = {("members", member, self.id) for member in self.members}
        lineage = self.owners.get("lineage", "")
        if self.kind == "family" and self.steward and lineage:
            found.add(("steward", self.steward, lineage))
        return frozenset(found)


# -- reading facts from Portal records -------------------------------------------------


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _related(record: Mapping[str, Any], kind: str) -> list[str]:
    """IDs of ``kind`` a record names: ``<kind>_id``/``<kind>_ids`` fields, also under attributes, and relations."""
    attributes = record.get("attributes") if isinstance(record.get("attributes"), Mapping) else {}
    found: list[str] = []
    for source in (record, attributes):
        found.append(_text(source.get(f"{kind}_id")))
        values = source.get(f"{kind}_ids")
        if isinstance(values, list):
            found.extend(_text(item) for item in values)
        if kind == "project":
            found.append(_text(source.get("project")))
    for relation in record.get("relations") or []:
        if isinstance(relation, Mapping) and relation.get("kind") == kind:
            found.append(_text(relation.get("target_id")))
    return list(dict.fromkeys(item for item in found if item))


def fact_from_record(record: Mapping[str, Any], kind: str) -> Fact:
    """The fact a resource descriptor or index record states (the first related object it names wins)."""
    attributes = record.get("attributes") if isinstance(record.get("attributes"), Mapping) else {}
    resource_id = _text(record.get("resource_id") or record.get("id"))
    if not resource_id:
        raise ValueError("A permission fact needs the resource ID")
    active = live(record)
    creator = principal_id(attributes.get("created_by") or record.get("created_by") or record.get("author"))
    created_for = principal_id(attributes.get("created_for") or record.get("created_for")) if creator else ""
    owners = {}
    if kind == "lineage" and _text(attributes.get("lineage_membership")).lower() == "personal":
        # A personal AMA belongs to its owner User, not to a Project.
        if creator and not creator.startswith("resident_"):
            owners = {PERSONAL_SPACE: personal_space(creator), "user": creator}
    else:
        for owner in OWNERS.get(kind, ()):
            if kind == "lineage" and owner != "project":
                continue
            related = _related(record, owner)
            if related:
                owners[owner] = related[0]
    dependents: tuple[Fact, ...] = ()
    steward = members = None
    if kind == "resident":
        workspace = _text(attributes.get("owned_workspace_id"))
        if workspace:
            dependents = (Fact("workspace", workspace, {"resident": resource_id}, active),)
    if kind == "family":
        steward = principal_id(attributes.get("steward_resident_id"))
    if kind == "chatroom":
        members = tuple(sorted({principal_id(item.get("actor") if isinstance(item, Mapping) else item)
                                for item in record.get("members") or []} - {""}))
    return Fact(kind, resource_id, owners, active, creator, steward or "", members or (), dependents,
                on_behalf_of=created_for if created_for != creator else "")


def user_fact(username: str, *, active: bool = True, category: str = DEFAULT_CATEGORY) -> Fact:
    return Fact("user", username, {}, active, category=category or DEFAULT_CATEGORY,
                dependents=(Fact(PERSONAL_SPACE, personal_space(username), {"user": username}, active),))


# -- turning facts into an instance ----------------------------------------------------


class TooManyRemoved(RuntimeError):
    """An unattended 补齐 would take out more objects than it may; nothing was written."""

    def __init__(self, count: int, sample: Sequence[str]) -> None:
        super().__init__(f"补齐将移出 {count} 个对象，超过上限，未写入；例如：{', '.join(sample)}")
        self.count = count
        self.sample = list(sample)


class InstanceReplaced(RuntimeError):
    """The instance was generated from other rules while a 补齐 waited for it (an automatic merge); nothing was written."""


class _RolledBack(Exception):
    """Ends a dry run's transaction, carrying its report out."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("dry run")
        self.report = report


class Instance:
    """Keeps the saved instance of one permission scheme in step with Portal facts."""

    def __init__(self, scheme: CompiledScheme, store: Store) -> None:
        self.scheme = scheme
        self.store = store.scoped(scheme.scheme_id)
        self._object_rules = tuple(rule for rule in scheme.rules if rule.relation not in CHANGING)
        self._relation_rules = {name: tuple(rule for rule in scheme.rules if rule.relation == name) for name in CHANGING}

    # -- the rows the rules give ------------------------------------------------------

    def fixed(self) -> list[Content]:
        """The rows of the rules that name class nodes only."""
        return [(rule.effect, rule.p, rule.a, rule.r) for rule in self.scheme.fixed]

    def _owner_user(self, fact: Fact) -> str:
        if fact.kind == PERSONAL_SPACE:
            return fact.owners.get("user", "")
        return ""

    def _principals_of(self, relation: str, fact: Fact, creators: Sequence[str]) -> Sequence[str]:
        """The principals ``relation`` names for one object (decision_b820faf42218 §5.8)."""
        if relation in ("self", "belongs"):
            # The account or Resident itself; for a Project or Lineage, its P element holding the Residents that belong to it.
            return [fact.id]
        if relation == "owner":
            return [self._owner_user(fact)]
        if relation == "creator":
            return creators
        return ()

    def derive(self, fact: Fact, creators: Sequence[str] | None = None) -> list[Content]:
        """The rows the relation rules give one object, without the rows of its changing relations.

        ``creators`` default to the fact's own.
        """
        r_pattern = self.scheme.pattern_of("R", fact.kind, fact.owners)
        if r_pattern is None:
            return []
        made_by = fact.creators if creators is None else tuple(creators)
        return [(rule.effect, principal, rule.a, fact.id) for rule in self._object_rules if rule.r == r_pattern.node
                for principal in self._principals_of(rule.relation, fact, made_by) if principal]

    def relation_rows(self, relations: Iterable[Relation]) -> list[Content]:
        """The rows the steward and members rules give for these relations."""
        return [(rule.effect, principal, rule.a, target)
                for relation, principal, target in sorted(relations) for rule in self._relation_rules.get(relation, ())]

    def generated(self, facts: Iterable[Fact], creators: Mapping[str, Sequence[str]]) -> set[Content]:
        """Every row the rules give for ``facts`` (flattened, active ones), computed without touching the database."""
        rows = set(self.fixed())
        for fact in facts:
            rows.update(self.derive(fact, creators.get(fact.id, ())))
            rows.update(self.relation_rows(fact.relations()))
        return rows

    # -- placement -------------------------------------------------------------------

    def _parent_path(self, connection, tree: str, fact: Fact) -> list[str] | None:
        """Where ``fact`` goes in ``tree``, or None when the scheme does not place its type there.

        The related object it goes under is in the tree already: an object
        commits with its place, so the object it belongs to came first
        (decision_b820faf42218 §5.7).  One that is missing sends the object to
        the place its pattern gives objects without that relation.
        """
        if tree == "P" and fact.kind == "user" and self.scheme.user_categories:
            return self._category_path(fact.category)
        pattern = self.scheme.pattern_of(tree, fact.kind, fact.owners)
        if pattern is None:
            return None
        related = fact.owners.get(pattern.under, "") if pattern.under else ""
        stored = self.store.paths(tree, [related], connection).get(related) if related else None
        return list(stored) if stored else list(self.scheme.trees[tree].path(pattern.fallback))

    def _category_path(self, category: str) -> list[str]:
        """The P path a User of ``category`` sits under: its category's node, else the tree's default parent."""
        p_tree = self.scheme.trees["P"]
        return list(p_tree.path(self.scheme.user_categories.get(category) or p_tree.default_parent))

    # -- incremental changes ----------------------------------------------------------

    def sync(self, facts: Iterable[Fact], *, connection=None) -> bool:
        """Bring the objects' places and rows in line with their facts; returns whether anything changed."""
        changed = False
        with self.store.transaction(connection) as connection:
            for fact in facts:
                changed = self._sync(connection, fact) or changed
        return changed

    def _sync(self, connection, fact: Fact) -> bool:
        if not fact.active:
            return self._retire(connection, fact)
        changed = False
        known = self.store.objects(connection, [fact.id], lock=True)
        for tree in ("R", "P"):
            parent_path = self._parent_path(connection, tree, fact)
            if parent_path is not None and self.store.place(connection, tree, fact.id, parent_path, kind=fact.kind):
                changed = True
        relations = fact.relations()
        if fact.id not in known:
            # A new object: the rules apply to it, and to nothing else.
            self.store.add_contents(connection, self.derive(fact, self._principals(connection, fact.creators))
                                    + self.relation_rows(relations))
            self.store.set_objects(connection, {fact.id: relations})
            changed = True
        else:
            if known[fact.id] != relations:
                before, after = set(self.relation_rows(known[fact.id])), set(self.relation_rows(relations))
                self.store.delete_contents(connection, before - after)
                self.store.add_contents(connection, after - before)
                self.store.set_objects(connection, {fact.id: relations})
                changed = True
        for dependent in fact.dependents:
            changed = self._sync(connection, dependent) or changed
        return changed

    def _principals(self, connection, creators: Sequence[str]) -> list[str]:
        """The creators that are principals of the instance (placed in its P tree): an import script gets no rows."""
        if not creators or not any(rule.relation == "creator" for rule in self._object_rules):
            return []
        placed = self.store.paths("P", creators, connection)
        return [creator for creator in creators if creator in placed]

    def _retire(self, connection, fact: Fact) -> bool:
        removed = []
        for tree in ("R", "P"):
            removed += self.store.remove(connection, tree, fact.id)
        forgotten = self.store.forget_objects(connection, [fact.id])
        if forgotten:
            # The rows its relations gave that name neither it nor its place: a steward's row on the Lineage.
            removed += self.store.delete_contents(connection, self.relation_rows(forgotten[fact.id]))
        changed = bool(removed or forgotten)
        for dependent in fact.dependents:
            changed = self._retire(connection, Fact(dependent.kind, dependent.id, dependent.owners, False)) or changed
        return changed

    def retire(self, kind: str, resource_ids: Iterable[str], *, connection=None) -> list[str]:
        retired = []
        with self.store.transaction(connection) as connection:
            for resource_id in resource_ids:
                if self._retire(connection, Fact(kind, resource_id, active=False)):
                    retired.append(resource_id)
        return retired

    # -- the whole instance -------------------------------------------------------------

    @staticmethod
    def creators(active: Mapping[str, Fact]) -> dict[str, list[str]]:
        """The creators each live object's record names, kept while they are live Users or Residents."""
        principals = {fact.id for fact in active.values() if fact.kind in ("user", "resident")}
        return {fact.id: [item for item in fact.creators if item in principals] for fact in active.values() if fact.creators}

    def targets(self, active: Mapping[str, Fact]) -> dict[tuple[str, str], tuple[str, str, tuple[str, ...]]]:
        """Where every live object belongs: ``(tree, id) -> (parent, kind, path)``."""
        found = {}
        for tree in ("R", "P"):
            paths: dict[str, list[str]] = {}
            if tree == "P" and self.scheme.user_categories:
                # Users sit under their category; objects placed under their owner User are placed from these.
                paths.update({fact.id: [*self._category_path(fact.category), fact.id]
                              for fact in active.values() if fact.kind == "user"})
            for fact in active.values():
                path = self._target_path(tree, fact, active, paths, ())
                if path is not None:
                    found[(tree, fact.id)] = (path[-2], fact.kind, tuple(path))
        return found

    def _target_path(self, tree: str, fact: Fact, active: Mapping[str, Fact], paths: dict[str, list[str]], seen: tuple[str, ...]) -> list[str] | None:
        if fact.id in paths:
            return paths[fact.id]
        pattern = self.scheme.pattern_of(tree, fact.kind, fact.owners)
        if pattern is None:
            return None
        parent = list(self.scheme.trees[tree].path(pattern.fallback))
        related_id = fact.owners.get(pattern.under, "") if pattern.under else ""
        if related_id and related_id not in seen:
            related = active.get(related_id)
            if related is not None and related.kind == pattern.under:
                parent = self._target_path(tree, related, active, paths, (*seen, fact.id)) or parent
        paths[fact.id] = [*parent, fact.id]
        return paths[fact.id]

    def _lock(self, connection) -> None:
        # Incremental changes wait until the whole instance is written; readers see the old instance until it commits.
        self.store.execute(connection, "LOCK TABLE {s}.scheme_nodes, {s}.scheme_entries, {s}.scheme_objects IN EXCLUSIVE MODE")

    def _revise_all(self, connection) -> None:
        """A new version number: every process drops its decision cache."""
        self.store.bump(connection)

    def generate(self, facts: Iterable[Fact], *, connection=None) -> dict[str, int]:
        """Write the whole instance from the rules and ``facts``; rows edited by hand are dropped, temporary credentials stay."""
        active = {fact.id: fact for fact in _flatten(facts) if fact.active}
        with self.store.transaction(connection) as connection:
            self._lock(connection)
            creators = self.creators(active)
            targets = self.targets(active)
            self.store.clear(connection, keep_temporary=True)
            self.store.insert_nodes(connection, [(tree, element, *place) for (tree, element), place in targets.items()])
            rows = self.generated(active.values(), creators)
            self.store.add_contents(connection, rows)
            self.store.set_objects(connection, {fact.id: fact.relations() for fact in active.values()})
            self._revise_all(connection)
            self.store.set_instance(connection, self.scheme.rules_digest, dict(self.scheme.source))
        return {"nodes": len(targets), "entries": len(rows)}

    def regenerate_keeping_edits(self, facts: Iterable[Fact], previous: CompiledScheme, *, max_removed: int | None = None,
                                 changes: bool = False, auth_limit: int | None = None, dry_run: bool = False,
                                 connection=None) -> dict[str, Any]:
        """Generate the instance from these rules, writing back what people did to the one ``previous`` gave (§5.11).

        Automatic merges and merges saved with ``source_digest`` both come here.
        ``changes`` adds the rows the instance gains and loses (``added``,
        ``removed``); ``auth_limit`` adds the authorization difference from
        the instance as it was to the new one (``auth_diff``, at most that many
        groups); ``dry_run`` does everything in a transaction it rolls back,
        so the report says what a merge would do.

        The instance is first brought up to date under ``previous``, the rules
        it was generated from, so every row those rules give the live objects
        is known; what differs from them is a person's edit.  After the new
        rules are applied:

        - a row they did not give (written by hand) and a disabled row come
          back with their ID, version and disabled flag when their P, A and R
          nodes are all in the new instance; the others are ``unrestored``;
        - a row they gave and a person deleted stays deleted; those the new
          rules give again are ``deleted_again``;
        - a temporary credential stays when its nodes are in the new
          instance; the others are revoked and ``unrestored``.
        """
        if dry_run:
            if connection is not None:
                raise ValueError("dry_run 在自己的事务中执行并回滚")
            try:
                with self.store.transaction() as own:
                    raise _RolledBack(self.regenerate_keeping_edits(facts, previous, max_removed=max_removed, changes=changes,
                                                                    auth_limit=auth_limit, connection=own))
            except _RolledBack as rolled:
                return rolled.report
        facts = list(facts)
        active = {fact.id: fact for fact in _flatten(facts) if fact.active}
        before = Instance(previous, self.store)
        with self.store.transaction(connection) as connection:
            self._lock(connection)
            present = self.store.contents(connection) if changes or auth_limit else {}
            was = View(previous, self.store.nodes(connection), present) if auth_limit else None
            before.complete(facts, max_removed=max_removed, connection=connection)
            baseline = before.generated(active.values(), self.creators(active))
            lasting = self.store.entries(connection, where="expires_at IS NULL")
            temporary = self.store.entries(connection, where="expires_at IS NOT NULL")
            edited = [entry for entry in lasting if entry.disabled or _content(entry) not in baseline]
            deleted = baseline - {_content(entry) for entry in lasting}
            report = self.generate(facts, connection=connection)
            stored = self.store.nodes(connection)

            def missing(entry) -> list[str]:
                return [tree for tree, node in (("P", entry.p), ("A", entry.a), ("R", entry.r))
                        if node not in self.scheme.trees[tree].nodes and (tree, node) not in stored]

            unrestored = [{**_listed(entry), "missing": missing(entry)} for entry in [*edited, *temporary] if missing(entry)]
            gone = {item["entry_id"] for item in unrestored}
            restored = [entry for entry in edited if entry.entry_id not in gone]
            # A generated row with the content of a restored one gives way to it.
            self.store.delete_contents(connection, [_content(entry) for entry in restored])
            self.store.add(connection, restored)
            redeleted = self.store.delete_contents(connection, deleted)
            revoked = [entry.entry_id for entry in temporary if entry.entry_id in gone]
            if revoked:
                self.store.delete(connection, where="entry_id = ANY(%s)", parameters=(revoked,))
            result = {**report, "restored_entries": len(restored), "revoked_credentials": len(revoked),
                      "unrestored": unrestored,
                      "deleted_again": [{"effect": entry.effect, "p": entry.p, "a": entry.a, "r": entry.r}
                                        for entry in sorted(redeleted, key=_content)]}
            if changes or auth_limit:
                after = self.store.contents(connection)
            if changes:
                result["added"] = [(*item, after[item]) for item in sorted(after.keys() - present.keys())]
                result["removed"] = [(*item, present[item]) for item in sorted(present.keys() - after.keys())]
            if auth_limit:
                result["auth_diff"] = compare(was, View(self.scheme, self.store.nodes(connection), after), limit=auth_limit)
        return result

    def complete(self, facts: Iterable[Fact], *, dry_run: bool = False, max_removed: int | None = None,
                 rules_digest: str | None = None, preview: bool = False, connection=None) -> dict[str, Any]:
        """补齐: bring the saved instance up to date with ``facts``, keeping every row it already has.

        Objects new to the instance are placed and get the rows the rules give
        them; objects that moved change position; objects that are gone leave
        with every row naming them; changed stewards and members swap their
        rows.  ``dry_run`` only counts; with ``preview`` it also gives the
        instance it would leave (``preview``, a ``View``).  ``max_removed`` guards an unattended
        run: when more objects would leave than that, nothing is written and
        ``TooManyRemoved`` says how many (the facts were probably read short).
        ``rules_digest`` is the instance's when the caller chose these rules:
        if the instance was generated from other rules by the time the tables
        are locked, nothing is written and ``InstanceReplaced`` is raised.
        """
        active = {fact.id: fact for fact in _flatten(facts) if fact.active}
        # Worked out from the facts alone, before the instance tables are locked.
        creators = self.creators(active)
        targets = self.targets(active)
        with self.store.transaction(connection) as connection:
            if not dry_run:
                self._lock(connection)
                saved = self.store.instance(connection)
                if rules_digest is not None and (saved or {}).get("rules_digest") != rules_digest:
                    raise InstanceReplaced(f"方案 {self.scheme.scheme_id} 的实例已按其他规则重新生成，本次补齐未写入")
            stored = self.store.nodes(connection)
            known = self.store.objects(connection)
            new_nodes = [(tree, element, *place) for (tree, element), place in targets.items() if (tree, element) not in stored]
            moved = [(tree, element, *place) for (tree, element), place in targets.items()
                     if (tree, element) in stored and stored[(tree, element)] != place]
            gone = [key for key in stored if key not in targets]
            leaving = sorted({element for _tree, element in gone})
            if max_removed is not None and len(leaving) > max_removed:
                raise TooManyRemoved(len(leaving), leaving[:20])
            adds: set[Content] = set()
            removals: set[Content] = set()
            objects: dict[str, frozenset[Relation]] = {}
            for fact in active.values():
                relations = fact.relations()
                if fact.id not in known:
                    adds.update(self.derive(fact, creators.get(fact.id, ())))
                    adds.update(self.relation_rows(relations))
                    objects[fact.id] = relations
                elif known[fact.id] != relations:
                    before, after = set(self.relation_rows(known[fact.id])), set(self.relation_rows(relations))
                    adds |= after - before
                    removals |= before - after
                    objects[fact.id] = relations
            departed = [object_id for object_id in known if object_id not in active]
            for object_id in departed:
                removals.update(self.relation_rows(known[object_id]))
            removals -= adds
            if dry_run:
                present = self.store.contents(connection)
                gone_ids = {"P": [key[1] for key in gone if key[0] == "P"], "R": [key[1] for key in gone if key[0] == "R"]}
                named = self.store.count(connection, where="p_node = ANY(%s) OR r_node = ANY(%s)",
                                         parameters=(gone_ids["P"], gone_ids["R"])) if gone else 0
                counted = {"added_nodes": len(new_nodes), "moved_nodes": len(moved), "removed_nodes": len(gone),
                           "added_entries": len(adds - present.keys()),
                           "removed_entries": named + len(removals & present.keys())}
                if preview:
                    # What the writes below would leave: the elements where the facts put them, rows naming departed ones gone.
                    leaving_p, leaving_r = set(gone_ids["P"]), set(gone_ids["R"])
                    rows = {content: disabled for content, disabled in present.items()
                            if content not in removals and content[1] not in leaving_p and content[3] not in leaving_r}
                    rows.update(dict.fromkeys(adds - rows.keys(), False))
                    counted["preview"] = View(self.scheme, targets, rows)
                return counted
            removed = self.store.delete_nodes(connection, gone)
            self.store.update_nodes(connection, moved)
            self.store.insert_nodes(connection, new_nodes)
            removed += len(self.store.delete_contents(connection, removals))
            added = self.store.add_contents(connection, adds)
            self.store.forget_objects(connection, departed)
            self.store.set_objects(connection, objects)
            self._revise_all(connection)
            if self.store.instance(connection) is None:
                self.store.set_instance(connection, self.scheme.rules_digest, dict(self.scheme.source))
        return {"added_nodes": len(new_nodes), "moved_nodes": len(moved), "removed_nodes": len(gone),
                "added_entries": added, "removed_entries": removed}


def _flatten(facts: Iterable[Fact]) -> Iterable[Fact]:
    for fact in facts:
        yield fact
        yield from _flatten(fact.dependents)


def flatten(facts: Iterable[Fact]) -> list[Fact]:
    """The facts with their dependents, live ones only."""
    return [fact for fact in _flatten(facts) if fact.active]


def _content(entry) -> Content:
    return (entry.effect, entry.p, entry.a, entry.r)


def _listed(entry) -> dict[str, Any]:
    listed = {"entry_id": entry.entry_id, "effect": entry.effect, "p": entry.p, "a": entry.a, "r": entry.r,
              "disabled": entry.disabled}
    if entry.expires_at is not None:
        listed.update(issued_by=entry.issued_by, expires_at=entry.expires_at, message_id=entry.message_id)
    return listed


# -- every current Portal object, for a rebuild -------------------------------------------


def collect_portal_facts(service) -> list[Fact]:
    """The facts of every live object, read inside ResourceService (``service``).

    Users come from the account store, Lineages, Residents and Families from
    the collaboration descriptors, Projects from the project registry, the
    objects features keep outside the index (Chatrooms, Notice Board posts)
    from the features (``permission.records`` hook), and everything the
    resource index holds (knowledge, papers, skills, Workbenches …) from the
    index snapshot.
    """
    from management_portal import portal_features, project_registry
    from management_portal.society_service.state_store import get_lineage_data_store
    from management_portal.portal_access_control import get_portal_access_store

    from .categories import categories

    facts: dict[str, Fact] = {}
    stored_categories = categories().all()
    for username, user in sorted((get_portal_access_store().snapshot().get("users") or {}).items()):
        facts[username] = user_fact(username, active=_text((user or {}).get("status")) != "deleted",
                                    category=stored_categories.get(username, DEFAULT_CATEGORY))
    for project in project_registry.load().get("projects") or []:
        project_id = _text(project.get("id")) if isinstance(project, Mapping) else ""
        if project_id:
            facts[project_id] = fact_from_record({**project, "resource_id": project_id}, "project")
    with get_lineage_data_store().database.read() as connection:
        owned = {row["resident_id"]: row["owned_workspace_id"] for row in connection.execute(
            "SELECT resident_id, owned_workspace_id FROM residents WHERE deleted_at IS NULL AND owned_workspace_id IS NOT NULL").fetchall()}
    for kind in ("lineage", "resident", "family"):
        for record in service._collaboration.list(kind):
            fact = fact_from_record(record, kind)
            if not fact.active:
                continue
            if kind == "resident" and not fact.dependents and owned.get(fact.id):
                fact = replace(fact, dependents=(Fact("workspace", owned[fact.id], {"resident": fact.id}),))
            facts[fact.id] = fact
    for _feature, records in portal_features.contributions("permission.records"):
        for record in records():
            fact = fact_from_record(record, _text(record["resource_type"]))
            facts[fact.id] = fact
    # A Resident's own Workspace is placed with that Resident; the index record does not name its owner.
    placed = set(facts) | {dependent.id for fact in _flatten(facts.values()) for dependent in fact.dependents}
    for record in service._index().authorization_snapshot().get("records") or []:
        resource_id, kind = _text(record.get("resource_id")), _text(record.get("resource_type"))
        if resource_id and kind and resource_id not in placed and kind not in {"user", "project", "lineage", "resident", "family"}:
            fact = fact_from_record(record, kind)
            if fact.active:
                facts[resource_id] = fact
    return list(facts.values())
