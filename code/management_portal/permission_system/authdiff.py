"""授权差报告: what an instance change grants and takes away (thinking_3cbdd375ebf8 §14.4, decision_b820faf42218 §5.12).

Replacing an instance — rules saved again, a merge of the source's changes,
another scheme activated — changes the authorization set from Auth to Auth′.
The report gives the two differences on the current objects: ΔAuth⁺ (what
principals gain) and ΔAuth⁻ (what they lose), summed up into boxes
``(P node, A levels, R node)`` instead of single triples.

How it is computed without deciding every triple.  A principal's decisions
depend only on which nodes named by some row lie on its path, so principals
with the same such nodes before and after form one class; resources the
same.  Each pair of classes is decided once per side as a bit set over the
A class nodes (the levels instructions ask for: @viewer, @contributor,
@admin …); a class whose rows and a class whose path are unchanged on both
sides need no decision.  Pairs that differ are merged into groups with the
same change, and each group's principals and resources are named by the
node holding them in the new trees, with the nodes it leaves out.  A
personal AMA's Resident is judged as its owner (``Engine.represents``), so
its class reads its owner's P path as well as its own.

An instruction element is an element of the A tree (§14), so a scheme that
classifies it under another A node — or, for a fixed slot, asks about
another R node — moves it, and Auth changes with it.  Those elements are
compared one by one: where the old node and where the new node sit, each on
its own side, decided over the same principal and resource classes; their
groups are per element, ``(P node, element, R node)``.  The A class nodes
above stand only for elements that stayed where they were.

The report is bounded: the groups listed (``limit``, instance groups and
element groups ranked together) and the time spent (``budget`` seconds;
past it the report says ``complete: false`` and its counts are lower
bounds).  It only reports; nothing is refused.  Which intents changed
(§14.5, rows following a class instead of elements) is not reported.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from .engine.model import DENY
from .scheme.compiler import CompiledScheme
from .scheme.vocabulary import RECORDS, REGISTERABLE_TYPES

BUDGET = 2.0
EXCEPT_LIMIT = 10
PRINCIPAL_KINDS = frozenset({"user", "resident"})
NEUTRAL, TIGHTENING, LOOSENING, MIXED = "neutral", "tightening", "loosening", "mixed"

Content = tuple[str, str, str, str]
Place = tuple[str, str, tuple[str, ...]]


@dataclass(frozen=True)
class View:
    """One instance as the report reads it: the rules' trees, the elements placed and the lasting rows (content -> disabled)."""

    scheme: CompiledScheme
    nodes: Mapping[tuple[str, str], Place]
    entries: Mapping[Content, bool]

    @classmethod
    def stored(cls, scheme: CompiledScheme, store, connection=None) -> "View":
        with store.read(connection) as connection:
            return cls(scheme, store.nodes(connection), store.contents(connection))

    @classmethod
    def generated(cls, scheme: CompiledScheme, store, facts: Iterable[Any]) -> "View":
        """The instance ``scheme`` would generate from ``facts`` (live, flattened), computed in memory."""
        from .facts import Instance

        instance = Instance(scheme, store)
        active = {fact.id: fact for fact in facts if fact.active}
        rows = instance.generated(active.values(), instance.creators(active))
        return cls(scheme, instance.targets(active), dict.fromkeys(rows, False))

    def path(self, tree: str, element: str) -> tuple[str, ...]:
        """Where ``element`` sits: its stored place, else under the tree's default node (as decisions place it)."""
        place = self.nodes.get((tree, element))
        return tuple(place[2]) if place is not None else self.scheme.trees[tree].element_path(element)

    def rows(self) -> list[Content]:
        return [content for content, disabled in self.entries.items() if not disabled]


def remapped(before: CompiledScheme, after: CompiledScheme, limit: int) -> dict[str, Any]:
    """Instructions whose requirements differ: they change what an instruction asks for, not the instance."""
    items = []
    for name in sorted(set(before.instructions) | set(after.instructions)):
        old = [slot.to_dict() for slot in before.slots(name)]
        new = [slot.to_dict() for slot in after.slots(name)]
        if old != new:
            items.append({"instruction": name, "before": old, "after": new})
    return {"total": len(items), "items": items[:limit]}


def kept(before: CompiledScheme, after: CompiledScheme, store, *, limit: int, connection=None,
         budget: float = BUDGET, represents: Callable[[list[str]], Mapping[str, str]] | None = None) -> dict[str, Any]:
    """The report of a change that keeps the instance ``store`` holds: only the elements it moves change Auth."""
    if not _moves(before, after):
        return _report(NEUTRAL, True, {"triples": 0, "principals": 0, "resources": 0},
                       {"triples": 0, "principals": 0, "resources": 0}, 0, [], remapped(before, after, limit))
    with store.read(connection) as connection:
        nodes, entries = store.nodes(connection), store.contents(connection)
    return compare(View(before, nodes, entries), View(after, nodes, entries), limit=limit, budget=budget,
                   represents=represents)


def _moves(before: CompiledScheme, after: CompiledScheme) -> list[tuple[str, Any, Any]]:
    """The A elements ``after`` places elsewhere: ``(instruction, old slot, new slot)``, by element."""
    old = {slot.element: slot for slots in before.instructions.values() for slot in slots}
    found = []
    for name, slots in after.instructions.items():
        for slot in slots:
            was = old.get(slot.element)
            if was is not None and (was.a, was.resource) != (slot.a, slot.resource):
                found.append((name, was, slot))
    return sorted(found, key=lambda item: item[2].element)


def _report(kind, complete, gained, lost, total, items, remaps) -> dict[str, Any]:
    return {"kind": kind, "complete": complete, "gained": gained, "lost": lost,
            "groups": {"total": total, "items": items}, "remapped": remaps}


def _kind(gained: bool, lost: bool) -> str:
    return MIXED if gained and lost else LOOSENING if gained else TIGHTENING if lost else NEUTRAL


class _Side:
    """One instance prepared for class decisions: rows by P node, each as R node -> (permit bits, deny bits)."""

    def __init__(self, view: View, points: list[str]) -> None:
        self.view = view
        a_tree = view.scheme.trees["A"]
        paths = [set(a_tree.element_path(point)) for point in points]
        below: dict[str, int] = {}

        def bits(a_node: str) -> int:
            if a_node not in below:
                below[a_node] = sum(1 << index for index, path in enumerate(paths) if a_node in path)
            return below[a_node]

        self.by_p: dict[str, list[tuple[str, bool, int]]] = {}
        for effect, p, a, r in view.rows():
            self.by_p.setdefault(p, []).append((r, effect == DENY, bits(a)))
        self.p_named = frozenset(self.by_p)
        self.r_named = frozenset(r for rows in self.by_p.values() for r, _deny, _bits in rows)
        self.tops = self._tops(a_tree, points)

    @staticmethod
    def _tops(a_tree, points: list[str]) -> list[tuple[int, int]]:
        """For each point, the bits of the points above it in this A tree."""
        index = {point: position for position, point in enumerate(points)}
        found = []
        for point in points:
            path = a_tree.element_path(point)[:-1]
            found.append(sum(1 << index[node] for node in path if node in index))
        return list(enumerate(found))

    def grants(self, p_nodes: tuple[str, ...]) -> dict[str, tuple[int, int]]:
        found: dict[str, list[int]] = {}
        for node in p_nodes:
            for r, deny, bits in self.by_p.get(node, ()):
                pair = found.setdefault(r, [0, 0])
                pair[deny] |= bits
        return {r: (pair[0], pair[1]) for r, pair in found.items()}

    def maximal(self, mask: int, points: list[str]) -> list[str]:
        return [points[position] for position, above in self.tops if mask >> position & 1 and not mask & above]


def _allowed(grants: Mapping[str, tuple[int, int]], r_nodes: tuple[str, ...]) -> int:
    permit = deny = 0
    for node in r_nodes:
        pair = grants.get(node)
        if pair is not None:
            permit |= pair[0]
            deny |= pair[1]
    return permit & ~deny


def _classes(elements: list[str], tree: str, old: _Side, new: _Side, named_old, named_new,
             owners: Mapping[str, str] | None = None) -> list[tuple[tuple, tuple, list[str]]]:
    found: dict[tuple[tuple, tuple], list[str]] = {}
    for element in elements:
        key = (tuple(node for node in _judged(old.view, tree, element, owners) if node in named_old),
               tuple(node for node in _judged(new.view, tree, element, owners) if node in named_new))
        found.setdefault(key, []).append(element)
    return [(before, after, members) for (before, after), members in found.items()]


def _judged(view: View, tree: str, element: str, owners: Mapping[str, str] | None) -> tuple[str, ...]:
    """The path decisions read: a personal AMA's Resident adds its owner's P path, as the engine does (``Engine.represents``)."""
    path = view.path(tree, element)
    owner = (owners or {}).get(element, "") if tree == "P" else ""
    if owner and owner not in path:
        path = tuple(dict.fromkeys((*path, *view.path("P", owner))))
    return path


def _owners(principals: list[str]) -> Mapping[str, str]:
    """The owners decisions judge personal AMA Residents as (decision_b820faf42218 §5.5)."""
    from management_portal.society_service.personal_ama import owners_of

    return owners_of(principals)


def compare(before: View, after: View, *, limit: int, budget: float = BUDGET,
            represents: Callable[[list[str]], Mapping[str, str]] | None = None) -> dict[str, Any]:
    """The authorization difference from ``before`` to ``after`` on the objects ``after`` places.

    ``represents`` names the owner each personal AMA Resident is judged as
    (``{principal: owner}`` for a list of principals); a Resident's class
    reads its owner's path too, so moving an AMA in the P tree alone changes
    nothing here, as in decisions.
    """
    deadline = time.monotonic() + budget
    moves = _moves(before.scheme, after.scheme)
    moving = {slot.element for _name, _was, slot in moves}
    landing = _landing(before.scheme, after.scheme)
    # The A class nodes, and the instruction elements a row names itself; moved elements are compared on their own.
    points = list(dict.fromkeys([*(node for node in [*after.scheme.trees["A"].nodes, *before.scheme.trees["A"].nodes]
                                   if node.startswith("@")),
                                 *(a for _e, _p, a, _r in [*before.rows(), *after.rows()] if a not in moving)]))
    old, new = _Side(before, points), _Side(after, points)
    kinds: dict[tuple[str, str], str] = {}
    principals, resources = [], []
    for (tree, element), (_parent, kind, _path) in after.nodes.items():
        if tree == "P" and kind in PRINCIPAL_KINDS:
            principals.append(element)
        elif tree == "R":
            resources.append(element)
        kinds[(tree, element)] = kind
    p_classes = _classes(principals, "P", old, new, old.p_named, new.p_named, (represents or _owners)(principals))
    r_classes = _classes(resources, "R", old, new, old.r_named, new.r_named)
    moved = [index for index, (was, now, _members) in enumerate(r_classes) if was != now]

    cells: dict[tuple[int, int, int], list[int]] = {}
    complete = True
    checked = 0
    for p_index, (was, now, _members) in enumerate(p_classes):
        if time.monotonic() > deadline:
            complete = False
            break
        old_grants, new_grants = old.grants(was), new.grants(now)
        # Unchanged rows decide alike wherever the resource's named nodes stayed the same.
        for r_index in (range(len(r_classes)) if old_grants != new_grants else moved):
            r_was, r_now, _resources = r_classes[r_index]
            a, b = _allowed(old_grants, r_was), _allowed(new_grants, r_now)
            if a != b:
                cells.setdefault((r_index, a, b), []).append(p_index)
            checked += 1
            if checked % 4096 == 0 and time.monotonic() > deadline:
                complete = False
                break

    moved_cells: dict[tuple[int, bool, int], list[int]] = {}
    landed: list[tuple[tuple, tuple, list[str]]] = []
    if moves and complete:
        complete = _moved_cells(moves, before, after, p_classes, r_classes, kinds, landing, deadline, moved_cells,
                                landed)

    groups: dict[tuple[tuple[int, ...], int, int], list[int]] = {}
    for (r_index, a, b), p_indexes in cells.items():
        groups.setdefault((tuple(p_indexes), a, b), []).append(r_index)
    gained = {"triples": 0, "principals": set(), "resources": set()}
    lost = {"triples": 0, "principals": set(), "resources": set()}
    names = _Names(after, kinds, principals, resources)

    def p_members(p_indexes):
        return [member for index in p_indexes for member in p_classes[index][2]]

    def counted(total, size, p_indexes, r_members):
        total["triples"] += size
        total["principals"].update(p_indexes)
        total["resources"].update(r_members)

    # (size, order, item maker): instance groups and element groups ranked together.
    summed: list[tuple[int, tuple, Any]] = []
    for (p_indexes, a, b), r_indexes in groups.items():
        p_count = sum(len(p_classes[index][2]) for index in p_indexes)
        r_members = [member for index in r_indexes for member in r_classes[index][2]]
        plus, minus = (b & ~a).bit_count(), (a & ~b).bit_count()
        for total, count in ((gained, plus), (lost, minus)):
            if count:
                counted(total, p_count * len(r_members) * count, p_indexes, r_members)

        def item(p_indexes=p_indexes, r_members=r_members, a=a, b=b, p_count=p_count):
            old_tops, new_tops = old.maximal(a, points), new.maximal(b, points)
            return {
                "kind": _kind(bool(b & ~a), bool(a & ~b)),
                "p": names.describe("P", p_members(p_indexes)),
                "r": names.describe("R", r_members),
                "before": [node for node in old_tops if node not in new_tops],
                "after": [node for node in new_tops if node not in old_tops],
                "gained": [point for index, point in enumerate(points) if (b & ~a) >> index & 1],
                "lost": [point for index, point in enumerate(points) if (a & ~b) >> index & 1],
                "principals": p_count, "resources": len(r_members),
            }
        summed.append((p_count * len(r_members) * (plus + minus), (0, p_indexes, tuple(r_indexes)), item))

    # A class node a place slot lands at is a group of its own (its own r class index), named by that node.
    moved_groups: dict[tuple[int, bool, tuple[int, ...], int], list[int]] = {}
    for (position, gaining, r_index), p_indexes in moved_cells.items():
        own = r_index if r_index >= len(r_classes) else -1
        moved_groups.setdefault((position, gaining, tuple(p_indexes), own), []).append(r_index)
    for (position, gaining, p_indexes, own), r_indexes in moved_groups.items():
        name, was, slot = moves[position]
        p_count = sum(len(p_classes[index][2]) for index in p_indexes)
        both_fixed = bool(slot.resource and was.resource)
        lands = landed[own - len(r_classes)][2][0] if own >= 0 else ""
        r_members = [] if both_fixed else [lands] if lands else [
            member for index in r_indexes
            for member in _typed(r_classes[index][2], was if slot.resource else slot, kinds, landing)]
        r_count = len(r_members) or 1
        # Class nodes, like a fixed slot's R node, are not among the report's resources.
        counted(gained if gaining else lost, p_count * r_count, p_indexes, [] if lands else r_members)

        def item(name=name, was=was, slot=slot, gaining=gaining, p_indexes=p_indexes, r_members=r_members,
                 p_count=p_count, r_count=r_count, both_fixed=both_fixed, lands=lands):
            found = {
                "kind": LOOSENING if gaining else TIGHTENING, "element": slot.element, "instruction": name,
                "p": names.describe("P", p_members(p_indexes)),
                "r": names.node("R", slot.resource if both_fixed else lands) if both_fixed or lands
                else names.describe("R", r_members),
                "a": {"before": was.a, "after": slot.a},
                "before": [] if gaining else [slot.element], "after": [slot.element] if gaining else [],
                "gained": [slot.element] if gaining else [], "lost": [] if gaining else [slot.element],
                "principals": p_count, "resources": r_count,
            }
            if slot.resource or was.resource:
                # "" on a side where the slot judges the object its argument names.
                found["resource"] = {"before": was.resource, "after": slot.resource}
            return found
        summed.append((p_count * r_count, (1, slot.element, gaining, p_indexes, lands), item))
    summed.sort(key=lambda entry: (-entry[0], entry[1]))

    def totals(found):
        return {"triples": found["triples"],
                "principals": sum(len(p_classes[index][2]) for index in found["principals"]),
                "resources": len(found["resources"])}

    items = [make() for _size, _order, make in summed[:limit]]
    return _report(_kind(bool(gained["triples"]), bool(lost["triples"])), complete, totals(gained), totals(lost),
                   len(summed), items, remapped(before.scheme, after.scheme, limit))


def _landing(before: CompiledScheme, after: CompiledScheme) -> Callable[[tuple], tuple[frozenset[str], frozenset]]:
    """Where a place slot can put a new object: ``place -> (related types, landings)``.

    The new object has a registerable type, and each scheme places it as the
    request does (``CompiledScheme.placement``) for every choice of related
    objects the call may give: under the related object when its type's
    placement follows that relation, else at a class node.  A related type
    counts when some registerable type sits under it on either side; the
    related objects are resources of the instance.  ``landings`` are the
    ``(old class node, new class node)`` pairs where a new object lands
    otherwise, one resource each.  A type a release moves between a related
    object and a class node counts only on the related side.
    """
    found: dict[tuple, tuple[frozenset[str], frozenset]] = {}

    def landing(place: tuple) -> tuple[frozenset[str], frozenset]:
        if place not in found:
            names = [name for name, _paths in place]
            related, landings = set(), set()
            for size in range(len(names) + 1):
                for given in combinations(names, size):
                    owners = {name: _GIVEN + name for name in given}
                    for kind in REGISTERABLE_TYPES:
                        sides = tuple(scheme.placement("R", kind, owners) for scheme in (before, after))
                        under = {node[len(_GIVEN):] for node in sides if node.startswith(_GIVEN)}
                        related |= under
                        if not under:
                            landings.add(sides)
            found[place] = (frozenset(related), frozenset(landings))
        return found[place]

    return landing


# Stands for the related object a call names, so a placement under it is told from a class node.
_GIVEN = "\0"


def _typed(members: list[str], slot, kinds: Mapping[tuple[str, str], str], landing) -> list[str]:
    """The resources an argument slot can name: those of its declared types.

    A place slot judges where the new object will sit: here its related objects (``_landing``).  A via slot judges
    what the record's field names: objects of the field's types, or any node when it declares none.  The class
    nodes a slot can name are decided apart (``_moved_cells``).
    """
    if slot.place:
        related, _landings = landing(slot.place)
        return [member for member in members if kinds.get(("R", member)) in related]
    types = _named_types(slot)
    return [member for member in members if kinds.get(("R", member)) in types] if types else members


def _named_types(slot) -> tuple[str, ...]:
    """The R object types an argument slot names: declared, or its via field's; none means any R node."""
    if slot.via:
        record, field = slot.via.split(".", 1)
        return tuple(RECORDS.get(record, {}).get("fields", {}).get(field, {}).get("types") or ())
    return tuple(slot.types)


def _moved_cells(moves, before: View, after: View, p_classes, r_classes, kinds, landing, deadline: float,
                 cells: dict[tuple[int, bool, int], list[int]], landed: list) -> bool:
    """Decide each moved element at its old place on the old side and its new place on the new side.

    Fills ``cells`` with ``(move, gained, r class or -1 for a fixed slot) -> P classes`` and says whether it finished.
    The class nodes moved place slots land at follow ``r_classes`` as classes of their own, appended to ``landed``.
    """
    elements = [slot.element for _name, _was, slot in moves]
    old, new = _Side(before, elements), _Side(after, elements)

    def path(view, side, node):
        # A class node one side's tree lacks can't be named there: no rows reach it.
        if node.startswith("@") and node not in view.scheme.trees["R"].nodes:
            return ()
        return tuple(found for found in view.scheme.trees["R"].element_path(node) if found in side.r_named)

    # A slot fixed on one side and an argument slot on the other (a release changed its kind)
    # is decided per resource class, with the fixed side at its R node.
    fixed, arguments, old_fixed, new_fixed = [], 0, [], []
    for position, (_name, was, slot) in enumerate(moves):
        if slot.resource and was.resource:
            fixed.append((position, path(before, old, was.resource), path(after, new, slot.resource)))
            continue
        arguments |= 1 << position
        if was.resource:
            old_fixed.append((position, path(before, old, was.resource)))
        if slot.resource:
            new_fixed.append((position, path(after, new, slot.resource)))
    old_mask = sum(1 << position for position, _path in old_fixed)
    new_mask = sum(1 << position for position, _path in new_fixed)

    def at_fixed(grants, places):
        return sum((_allowed(grants, nodes) >> position & 1) << position for position, nodes in places)

    # Which moved argument elements can name some resource of each class.
    reach = [sum(1 << position for position, (_name, was, slot) in enumerate(moves)
                 if arguments >> position & 1 and _typed(members, was if slot.resource else slot, kinds, landing))
             for _was, _now, members in r_classes]
    # Class nodes are resources too: one per (old node, new node), for the argument sides that can name them.
    every = {(node, node) for view in (before, after) for node in view.scheme.trees["R"].nodes}

    def class_targets(side):
        if side.resource:
            return set()
        if side.place:
            return landing(side.place)[1]
        return set() if _named_types(side) else every

    lands: dict[tuple[str, str], int] = {}
    for position, (_name, was, slot) in enumerate(moves):
        if arguments >> position & 1:
            for pair in class_targets(was) | class_targets(slot):
                lands[pair] = lands.get(pair, 0) | 1 << position
    for (was_node, now_node), bits in lands.items():
        landed.append((path(before, old, was_node), path(after, new, now_node), [now_node]))
        reach.append(bits)
    classes = [*r_classes, *landed]
    checked = 0
    for p_index, (was, now, _members) in enumerate(p_classes):
        if time.monotonic() > deadline:
            return False
        old_grants, new_grants = old.grants(was), new.grants(now)
        for position, r_was, r_now in fixed:
            a, b = _allowed(old_grants, r_was) >> position & 1, _allowed(new_grants, r_now) >> position & 1
            if a != b:
                cells.setdefault((position, bool(b), -1), []).append(p_index)
        if not arguments:
            continue
        old_at, new_at = at_fixed(old_grants, old_fixed), at_fixed(new_grants, new_fixed)
        for r_index, (r_was, r_now, _resources) in enumerate(classes):
            a = _allowed(old_grants, r_was) & ~old_mask | old_at
            b = _allowed(new_grants, r_now) & ~new_mask | new_at
            changed = (a ^ b) & reach[r_index]
            while changed:
                position = (changed & -changed).bit_length() - 1
                changed &= changed - 1
                cells.setdefault((position, bool(b >> position & 1), r_index), []).append(p_index)
            checked += 1
            if checked % 4096 == 0 and time.monotonic() > deadline:
                return False
    return True


class _Names:
    """Names a set of elements by the node holding them in the new trees, the object types it keeps and the nodes it leaves out."""

    def __init__(self, view: View, kinds: Mapping[tuple[str, str], str], principals: list[str], resources: list[str]) -> None:
        self.view = view
        self.kinds = kinds
        self.universe = {"P": principals, "R": resources}
        self._under: dict[str, dict[str, list[str]]] = {}

    def under(self, tree: str) -> dict[str, list[str]]:
        if tree not in self._under:
            found: dict[str, list[str]] = {}
            for element in self.universe[tree]:
                for node in self.view.path(tree, element):
                    found.setdefault(node, []).append(element)
            self._under[tree] = found
        return self._under[tree]

    def node(self, tree: str, node: str) -> dict[str, Any]:
        found: dict[str, Any] = {"node": node}
        declared = self.view.scheme.trees[tree].nodes.get(node)
        if declared is not None and declared.title:
            found["title"] = declared.title
        return found

    def describe(self, tree: str, members: list[str]) -> dict[str, Any]:
        paths = [self.view.path(tree, member) for member in members]
        common = paths[0]
        for path in paths[1:]:
            size = 0
            while size < min(len(common), len(path)) and common[size] == path[size]:
                size += 1
            common = common[:size]
        node = common[-1] if common else self.view.scheme.trees[tree].root
        found: dict[str, Any] = {"node": node}
        title = self.view.scheme.trees[tree].nodes.get(node)
        if title is not None and title.title:
            found["title"] = title.title
        inside = self.under(tree).get(node, [])
        if len(inside) == len(members):
            return found
        types = sorted({self.kinds.get((tree, member), "") for member in members})
        same_type = [element for element in inside if self.kinds.get((tree, element), "") in types]
        if len(same_type) < len(inside):
            found["kinds"] = types
        chosen = set(members)
        holes = [element for element in same_type if element not in chosen]
        if not holes:
            return found
        # The highest nodes below ``node`` holding only left-out elements of these types.
        depth = len(common)
        counts: dict[str, int] = {}
        for element in same_type:
            for item in self.view.path(tree, element)[depth:]:
                counts[item] = counts.get(item, 0) + 1
        missing: dict[str, int] = {}
        for element in holes:
            for item in self.view.path(tree, element)[depth:]:
                missing[item] = missing.get(item, 0) + 1
        left_out = []
        for element in holes:
            top = next(item for item in self.view.path(tree, element)[depth:] if missing[item] == counts[item])
            left_out.append(top)
        left_out = sorted(set(left_out))
        found["except"] = left_out[:EXCEPT_LIMIT]
        found["except_total"] = len(left_out)
        return found


__all__ = ["BUDGET", "LOOSENING", "MIXED", "NEUTRAL", "TIGHTENING", "View", "compare", "kept", "remapped"]
