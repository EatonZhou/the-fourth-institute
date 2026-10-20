"""Three-way merge of a copy's rules with the changes of its source (decision_b820faf42218 §5.11).

A copy records the source rules it was copied from or last merged (``was``).
When the source changes (``now``), each unit of the rules is compared on the
three sides: a unit the copy left as it was (``own`` equals ``was``) takes the
source's new version, including units the source added or deleted; a unit the
copy changed too, differently, keeps the copy's version and is a conflict.

The units are those the editor compares (``portal_permission_schemes.js``,
``mergeUnits``): each tree's settings (root, default_parent and the rest), each
tree node, each entry rule, each A element's classification
(``instruction|<element>``) and every other top-level
field such as the description.  The scheme ID and the title belong to the copy
and are never merged.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Callable, Mapping

OWN_FIELDS = ("scheme_id", "title", "trees", "entries", "instructions")
_ABSENT = object()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _same(left: Any, right: Any) -> bool:
    if left is _ABSENT or right is _ABSENT:
        return left is right
    return _canonical(left) == _canonical(right)


def entry_key(entry: Mapping[str, Any], position: int) -> str:
    return entry.get("id") or f"entry_{position}"


def units(document: Mapping[str, Any] | None) -> dict[str, Any]:
    found: dict[str, Any] = {}
    document = document or {}
    for key, value in document.items():
        if key not in OWN_FIELDS:
            found[f"field|{key}"] = value
    for name, tree in (document.get("trees") or {}).items():
        settings = {key: value for key, value in (tree or {}).items() if key != "nodes"}
        found[f"tree|{name}"] = settings
        for node in (tree or {}).get("nodes") or []:
            found[f"node|{name}|{node.get('id')}"] = node
    for position, entry in enumerate(document.get("entries") or []):
        found[f"entry|{entry_key(entry, position)}"] = entry
    for element, classified in (document.get("instructions") or {}).items():
        found[f"instruction|{element}"] = classified
    return found


def source_changes(recorded: Mapping[str, Any], current: Mapping[str, Any], mine: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The units the source changed since ``recorded`` that ``mine`` does not already match.

    Each change is ``{key, was, now, own, conflict}``; a side without the unit
    is None.  ``conflict`` says ``mine`` changed the unit too, differently.
    """
    was, now, own = units(recorded), units(current), units(mine)
    changes = []
    for key in dict.fromkeys([*now, *was]):
        before, after, kept = was.get(key, _ABSENT), now.get(key, _ABSENT), own.get(key, _ABSENT)
        if _same(before, after) or _same(kept, after):
            continue
        changes.append({"key": key, "was": _plain(before), "now": _plain(after), "own": _plain(kept),
                        "conflict": not _same(kept, before)})
    return changes


def _plain(value: Any) -> Any:
    return None if value is _ABSENT else copy.deepcopy(value)


def _put(items: list, key_of: Callable[[Any, int], str], key: str, value: Any, order: list) -> None:
    """The source's version of one item in a list; a new item goes after the nearest earlier item of the source's list."""
    at = next((index for index, item in enumerate(items) if key_of(item, index) == key), -1)
    if value is None:
        if at >= 0:
            del items[at]
        return
    if at >= 0:
        items[at] = value
        return
    position = next((index for index, item in enumerate(order) if key_of(item, index) == key), -1)
    for index in range(position - 1, -1, -1):
        earlier = key_of(order[index], index)
        before = next((place for place, item in enumerate(items) if key_of(item, place) == earlier), -1)
        if before >= 0:
            items.insert(before + 1, value)
            return
    items.insert(0, value)


def apply_change(document: dict[str, Any], change: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    """Make one unit of ``document`` the source's current version (``change["now"]``; None deletes it)."""
    kind, name, *rest = change["key"].split("|")
    now = copy.deepcopy(change["now"])
    if kind == "field":
        if now is None:
            document.pop(name, None)
        else:
            document[name] = now
    elif kind == "tree":
        trees = document.setdefault("trees", {})
        if now is None:
            trees.pop(name, None)
        else:
            trees[name] = {**now, "nodes": (trees.get(name) or {}).get("nodes") or []}
    elif kind == "node":
        tree = document.setdefault("trees", {}).setdefault(name, {"nodes": []})
        nodes = tree.setdefault("nodes", [])
        _put(nodes, lambda node, _index: node.get("id"), rest[0], now, ((current.get("trees") or {}).get(name) or {}).get("nodes") or [])
    elif kind == "entry":
        _put(document.setdefault("entries", []), entry_key, name, now, list(current.get("entries") or []))
    elif kind == "instruction":
        instructions = document.setdefault("instructions", {})
        if now is None:
            instructions.pop(name, None)
        else:
            instructions[name] = now


def merge(recorded: Mapping[str, Any], current: Mapping[str, Any], mine: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``mine`` with every source change it did not touch; returns the merged rules and the conflicts it kept its own version of."""
    merged = copy.deepcopy(dict(mine))
    conflicts = []
    for change in source_changes(recorded, current, mine):
        if change["conflict"]:
            conflicts.append(change)
        else:
            apply_change(merged, change, current)
    return merged, conflicts
