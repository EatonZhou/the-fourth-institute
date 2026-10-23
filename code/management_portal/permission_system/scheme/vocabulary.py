"""The Portal words a scheme's rules may use: object types, relations and User categories.

The rules are data, but their placements and relation rules name Portal
relations; the fact adapter (``facts.py``) reads each of them from the
objects' own records.  The relations are fixed here; a scheme only chooses
among them, and a new relation needs code here and in ``facts.py``
(decision_b820faf42218 §5.8).  The scheme editor offers exactly these
choices.
"""
from __future__ import annotations

from management_common.resource_identity import get_resource_type_registry

from ..categories import CATEGORIES as USER_CATEGORIES

# Types addressed by their own names rather than ``<type>_<hex>``.
NAMED_TYPES = ("user", "project")
# One node per User under "个人", holding that User's personal resources.
PERSONAL_SPACE = "personal_space"

RESOURCE_TYPES = frozenset(get_resource_type_registry().types) | frozenset(NAMED_TYPES)
# The types a new object may have: a place slot's argument names one of them (RegisterResource).
REGISTERABLE_TYPES = frozenset(kind for kind, declaration in get_resource_type_registry().declarations.items()
                               if declaration.get("registerable"))

OBJECT_TYPES = {
    # Principals are Users and Residents; Projects and Lineages group Residents.
    "P": frozenset({"user", "resident", "project", "lineage"}),
    "A": frozenset(),
    "R": RESOURCE_TYPES | {PERSONAL_SPACE},
}

_PROJECT_OWNED = ("article", "concept", "dataset", "decision", "document", "experiment", "idea", "information",
                  "insight", "material", "meeting", "model", "paper", "patent", "plan", "question", "repo", "review",
                  "risk", "search", "skill", "test", "thinking", "todo")

# object type -> related type -> the relation (``belongs`` 所属 or ``owner`` 所有者) a placement
# ``"under": <related type>`` follows: the object sits under that related object.
PLACEMENTS: dict[str, dict[str, str]] = {
    **{kind: {"project": "belongs"} for kind in _PROJECT_OWNED},
    # A personal AMA Lineage has an owner User (and that User's personal space) instead of a Project.
    "lineage": {"project": "belongs", PERSONAL_SPACE: "owner", "user": "owner"},
    "resident": {"lineage": "belongs"},
    "family": {"lineage": "belongs"},
    # A Workbench names the Projects of the Lineages it binds; the first one places it.
    "workbench": {"project": "belongs", "lineage": "belongs"},
    "workspace": {"resident": "belongs"},
    PERSONAL_SPACE: {"user": "owner"},
}

# object type -> the related types a Portal record of it names.
OWNERS: dict[str, tuple[str, ...]] = {kind: tuple(related) for kind, related in PLACEMENTS.items()}
# The related types a ``place`` slot may read from a creating call's arguments.
PLACE_RELATED = frozenset(related for placements in PLACEMENTS.values() for related in placements)

# relation -> its title and the object types a relation rule may expand it over ("any": every type).
# Each names principals of one object: 本人 the account or Resident itself; 所有者 the User a personal
# space belongs to; 所属 the object's own P element, holding the Residents that belong to it; 创建者 who
# created it; steward the steward of a Family in the Lineage; 成员 the members of a chatroom.
RELATIONS: dict[str, dict] = {
    "self": {"title": "本人（账号或 Resident 自身）", "types": ("user", "resident")},
    "owner": {"title": "所有者（个人空间属于的 User）", "types": (PERSONAL_SPACE,)},
    "belongs": {"title": "所属（属于该 Project 或 Lineage 的 Resident）", "types": ("project", "lineage")},
    "creator": {"title": "创建者", "types": "any"},
    "steward": {"title": "该 Lineage 中 Family 的 steward", "types": ("lineage",)},
    "members": {"title": "聊天室成员", "types": ("chatroom",)},
}

PLACEMENT_TITLES = {"belongs": "所属", "owner": "所有者"}

# Records an argument may name in place of the object judged (decision_898b8911a2ff "判定对象可以来自已保存的记录"):
# ``"via": "<record>.<field>"`` judges the object the saved record names in that field.  Each needs a reader
# from the code that keeps the record (``PARPermissions._readers``).  A field that may be empty
# (``may_be_unowned``) asks nothing for such a record, so its slot is optional beside a fixed slot.
RECORDS: dict[str, dict] = {
    "permission_entry": {"title": "权限表项", "fields": {"r": {"title": "表项的资源（已保存的 r）"}}},
    "instruction_execution": {"title": "指令执行回执", "fields": {
        "lineage": {"title": "回执所属的 Lineage（调用者作用域的回执没有）", "may_be_unowned": True,
                    "types": ("lineage",)}}},
}
# A field's ``types`` are the R object types it can name; without them it can name any R node.  The
# authorization diff counts a moved via slot over these.


def describe() -> dict:
    """The choices a scheme editor offers."""
    return {
        "object_types": {tree: sorted(kinds) for tree, kinds in OBJECT_TYPES.items() if kinds},
        "placements": {kind: [{"under": related, "relation": relation, "title": PLACEMENT_TITLES[relation]}
                              for related, relation in placements.items()]
                       for kind, placements in sorted(PLACEMENTS.items())},
        "relations": [{"relation": name, "title": value["title"],
                       "types": value["types"] if isinstance(value["types"], str) else list(value["types"])}
                      for name, value in RELATIONS.items()],
        "resource_types": sorted(RESOURCE_TYPES),
        "place_related": sorted(PLACE_RELATED),
        "records": [{"via": f"{record}.{field}", "record": record, "record_title": value["title"],
                     "title": about["title"], "may_be_unowned": about.get("may_be_unowned", False)}
                    for record, value in RECORDS.items() for field, about in value["fields"].items()],
        "user_categories": [{"category": name, "title": title} for name, title in USER_CATEGORIES.items()],
    }
