"""The rules compiler of a permission scheme (decision_b820faf42218 §3.5).

A scheme's rules are data.  Each tree declares two kinds of nodes:

- **class nodes**, fixed sets such as "共享" or "管理（admin）";
- **each nodes** (``"each": <object type>``), a pattern: every Portal object
  of that type becomes an element of the tree, placed by the node's
  placement rule (decision_b820faf42218 §5.9): "under the object it has this
  relation with; without that relation, at this node".  ``"under": <type>``
  names the relation by the related object's type (a Lineage under its
  Project, a personal AMA Lineage under its owner User); the relations are
  fixed in code (``vocabulary.PLACEMENTS``).  Without the relation, an each
  node under a class node puts the object under that class node; a nested
  each node (one under another each node, whose type it must name in
  ``under``) puts it at ``"otherwise": <class node>``, else at the tree's
  fallback node (``default_parent``).  Object types no each node covers sit
  under the fallback node too.

One object type may have several each nodes in a tree, one per related
type.  An object takes the first, in declared order, whose relation it has;
an object having none of them takes the first.

A P class node may carry ``"user_category"``: the Users of that category
(Portal 管理员, 一般用户, 实习用户) sit under it, so its entries speak for
the whole category.

Rule entries are of two kinds (decision_b820faf42218 §5.8).  A **node rule**
``{p, a, r}`` names a class node of each tree and gives one instance entry.
A **relation rule** ``{relation, a, r}`` names a relation, an A class node
and an R each node: for every object that node places, the relation names
principals (its creator, the account itself, the Residents that belong to
it …) and each gets one entry ``(principal, a, object)``.  The relations are
fixed in code (``vocabulary.RELATIONS``); a scheme only chooses among them.
Generating the instance writes all of them as plain rows; nothing in the
instance remembers which rule a row came from.

What a call is judged on belongs to the instruction: its declaration lists
decision slots (``InstructionDeclaration.decides``), and each slot is one A
element, named after the instruction (one slot) or ``<instruction>:<argument
or fixed name>`` (several).  ``instructions`` only classifies them:
``{"<element>": {"a": <A class node>}}`` puts the element under that node,
so an entry on that node opens it, and a fixed slot, which names no object,
adds ``"r": <R class node>``, the node it asks about.  The compiler knows no
node by name; "viewer" or "admin" are titles a scheme gives its own A nodes.
An element the scheme does not classify sits under the A default node, and
an unclassified fixed slot asks about the R default node (the default scheme
puts them under "未归类" on "共享", so only administrators pass them); an
argument slot still judges the objects its argument names.

Compiling checks the structure only: that the declared nodes form trees (one
root, declared parents, no cycle), that every placement names a relation the
object type really has, one pattern per relation, that relation rules use
relations where they apply and that every classified element exists.  What
the rules mean — whether a permit lies inside a deny, whether
administrators keep their rights — is left to whoever writes them.  Errors
carry the JSON path of the offending part.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from management_common.format_tags import schema_matches
from management_portal.hatg.declarations import DecisionSlot, decision_elements

from ..engine.model import DENY, PERMIT, TREES, Node, Tree
from . import vocabulary

SCHEMA = "tfi.permission-scheme.v2"
_NODE_ID = re.compile(r"@[a-z][a-z0-9_]*")
_SCHEME_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
FIELDS = frozenset({"schema", "scheme_id", "title", "description", "trees", "entries", "instructions"})


class SchemeError(ValueError):
    """The rules are invalid; ``errors`` lists each problem with its JSON ``path``."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        super().__init__("；".join(f"{item['path']}: {item['message']}" for item in errors[:5]))


@dataclass(frozen=True)
class Slot:
    """One ``(p, element, r)`` request an instruction makes: a declared decision slot placed by the scheme.

    ``r`` is the resource an argument names, or a fixed R node (``resource``).
    With ``via`` (``<record>.<field>``) the argument names saved records and
    ``r`` is what each record names in that field (vocabulary.RECORDS).
    With ``place`` (related type -> argument paths) the argument names the
    type of an object about to be created and ``r`` is the R node it will sit
    under: its related object, else its pattern's fallback (``placement``).
    ``classified`` is false when the scheme leaves the element to its default nodes.
    """

    element: str
    a: str
    argument: str = ""
    types: tuple[str, ...] = ()
    optional: bool = False
    resource: str = ""
    via: str = ""
    place: tuple[tuple[str, tuple[str, ...]], ...] = ()
    classified: bool = True

    def to_dict(self) -> dict[str, Any]:
        if self.resource:
            return {"element": self.element, "a": self.a, "resource": self.resource}
        value: dict[str, Any] = {"element": self.element, "argument": self.argument, "a": self.a}
        if self.via:
            value["via"] = self.via
        if self.place:
            value["place"] = {related: list(paths) for related, paths in self.place}
        if self.types:
            value["types"] = list(self.types)
        if self.optional:
            value["optional"] = True
        return value


@dataclass(frozen=True)
class Pattern:
    """An each node: objects of ``types`` sit under their ``under`` related object, else at ``fallback``."""

    node: str
    types: tuple[str, ...]
    under: str
    fallback: str


@dataclass(frozen=True)
class Rule:
    """A rule entry: a node rule names class nodes ``p`` and ``r``; a relation rule names ``relation`` and an R each node ``r``."""

    rule_id: str
    effect: str
    p: str
    a: str
    r: str
    relation: str = ""


@dataclass(frozen=True)
class CompiledScheme:
    scheme_id: str
    title: str
    description: str
    # ``digest`` changes with any part of the rules; ``rules_digest`` only with what the instance is generated from.
    digest: str
    rules_digest: str
    trees: Mapping[str, Tree]
    # tree -> object type -> its patterns in declared order (one per owner type).
    patterns: Mapping[str, Mapping[str, tuple[Pattern, ...]]]
    # Node rules: one instance entry each.
    fixed: tuple[Rule, ...]
    # Relation rules, expanded per object.
    rules: tuple[Rule, ...]
    instructions: Mapping[str, tuple[Slot, ...]]
    source: Mapping[str, Any]
    # User category -> the P class node holding the Users of that category.
    user_categories: Mapping[str, str] = MappingProxyType({})

    def slots(self, instruction: str) -> tuple[Slot, ...]:
        return self.instructions.get(instruction, ())

    @property
    def unmapped_resource(self) -> str:
        """The R node an unclassified fixed slot, or a name no instruction declares, asks about: the R default node."""
        return self.trees["R"].default_parent

    def unmapped(self) -> list[str]:
        """The A elements this scheme does not classify."""
        return sorted(slot.element for slots in self.instructions.values() for slot in slots if not slot.classified)

    def describe(self, instruction: str) -> dict[str, Any]:
        """What GetInstructionSchema reports about an instruction's permission requirements."""
        slots = self.slots(instruction)
        if not slots:
            return {"required": True, "unmapped": True, "requirements": [
                {"a": self.trees["A"].default_parent, "resource": self.unmapped_resource}]}
        described: dict[str, Any] = {"required": True, "requirements": [slot.to_dict() for slot in slots]}
        if not all(slot.classified for slot in slots):
            described["unmapped"] = True
        return described

    def pattern_of(self, tree: str, object_type: str, owners: Mapping[str, str] | None = None) -> Pattern | None:
        """Where objects of ``object_type`` with related ``owners`` sit: the first pattern whose relation they have, else the first."""
        found = self.patterns[tree].get(object_type, ())
        return next((pattern for pattern in found if pattern.under and (owners or {}).get(pattern.under)),
                    found[0] if found else None)

    def placement(self, tree: str, object_type: str, owners: Mapping[str, str] | None = None) -> str:
        """The element a new object of ``object_type`` with ``owners`` will sit under in ``tree``.

        The same choice the fact adapter makes when it places the object
        (``Instance._parent_path``): its related object, else its pattern's
        fallback node, else the tree's default node for a type no each node covers.
        """
        pattern = self.pattern_of(tree, object_type, owners)
        if pattern is None:
            return self.trees[tree].default_parent
        related = (owners or {}).get(pattern.under, "") if pattern.under else ""
        return related or pattern.fallback

    def summary(self) -> dict[str, Any]:
        return {"scheme_id": self.scheme_id, "title": self.title, "description": self.description}


class _Errors:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, path: str, message: str) -> None:
        self.items.append({"path": path, "message": message})


def _tree(name: str, value: Any, errors: _Errors) -> tuple[Tree | None, dict[str, tuple[Pattern, ...]]]:
    where = f"trees.{name}"
    if not isinstance(value, Mapping) or set(value) - {"root", "default_parent", "nodes"}:
        errors.add(where, "需要 root、default_parent、nodes")
        return None, {}
    root, default_parent, raw = value.get("root"), value.get("default_parent"), value.get("nodes")
    if not isinstance(raw, list) or not raw:
        errors.add(f"{where}.nodes", "必须是非空数组")
        return None, {}
    declared: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, item in enumerate(raw):
        path = f"{where}.nodes[{index}]"
        if not isinstance(item, Mapping) or set(item) - {"id", "parent", "title", "each", "under", "otherwise", "user_category"} or not isinstance(item.get("id"), str):
            errors.add(path, "节点只含 id、parent、title、each、under、otherwise、user_category")
            continue
        if "user_category" in item and name != "P":
            errors.add(f"{path}.user_category", "只有 P 树的分类节点可以代表 User 分类")
        if not _NODE_ID.fullmatch(item["id"]):
            errors.add(f"{path}.id", "节点 ID 以 @ 开头，只含小写字母、数字和下划线")
        if item["id"] in declared:
            errors.add(f"{path}.id", f"节点重复：{item['id']}")
            continue
        declared[item["id"]] = (index, dict(item))
    if root not in declared or declared[root][1].get("parent") or declared[root][1].get("each"):
        errors.add(f"{where}.root", "root 必须是已声明、没有父节点的分类节点")
        return None, {}
    nodes: dict[str, Node] = {}
    broken: set[str] = set()

    def resolve(node_id: str, seen: tuple[str, ...]) -> Node | None:
        if node_id in nodes or node_id in broken:
            return nodes.get(node_id)
        index, item = declared[node_id]
        if node_id in seen:
            errors.add(f"{where}.nodes[{index}].parent", f"父子关系成环：{' → '.join((*seen, node_id))}")
            broken.add(node_id)
            return None
        parent = item.get("parent") or ""
        if node_id == root:
            path: tuple[str, ...] = (node_id,)
        elif not parent:
            errors.add(f"{where}.nodes[{index}].parent", "缺少 parent；每棵树只有一个根")
            broken.add(node_id)
            return None
        elif parent not in declared:
            errors.add(f"{where}.nodes[{index}].parent", f"parent {parent} 未声明")
            broken.add(node_id)
            return None
        else:
            above = resolve(parent, (*seen, node_id))
            if above is None:
                broken.add(node_id)
                return None
            path = above.path + (node_id,)
        node = Node(node_id, parent, str(item.get("title") or ""), path)
        nodes[node_id] = node
        return node

    for node_id in declared:
        resolve(node_id, ())
    if broken:
        return None, {}
    patterns: dict[str, tuple[Pattern, ...]] = {}
    each_of: dict[str, tuple[str, ...]] = {}
    for node_id, (index, item) in declared.items():
        each = item.get("each")
        if each is None:
            continue
        kinds = (each,) if isinstance(each, str) else tuple(each) if isinstance(each, list) else ()
        allowed = vocabulary.OBJECT_TYPES[name]
        if not kinds or any(kind not in allowed for kind in kinds):
            errors.add(f"{where}.nodes[{index}].each", f"对象类型必须取自：{'、'.join(sorted(allowed))}")
            continue
        each_of[node_id] = kinds
    for node_id, (index, item) in declared.items():
        if node_id not in each_of and nodes[node_id].parent in each_of:
            errors.add(f"{where}.nodes[{index}].parent", "分类节点不能挂在规则节点下")
        if node_id not in each_of and ("under" in item or "otherwise" in item):
            errors.add(f"{where}.nodes[{index}]", "under、otherwise 只用于规则节点")
    if default_parent not in nodes or default_parent in each_of:
        errors.add(f"{where}.default_parent", "必须是已声明的分类节点")
        return None, patterns

    for node_id, kinds in each_of.items():
        index, item = declared[node_id]
        parent = nodes[node_id].parent
        under = item.get("under", "")
        if not isinstance(under, str):
            errors.add(f"{where}.nodes[{index}].under", "必须是对象类型")
            continue
        if parent in each_of:
            # A nested each node draws its objects under the parent's objects: it says so.
            if len(each_of[parent]) != 1 or under != each_of[parent][0]:
                errors.add(f"{where}.nodes[{index}].under", "挂在规则节点下的规则节点，under 必须是父节点的对象类型（父节点只放一种对象）")
                continue
            otherwise = item.get("otherwise", default_parent)
            if not isinstance(otherwise, str) or otherwise not in nodes or otherwise in each_of:
                errors.add(f"{where}.nodes[{index}].otherwise", "必须是本树的分类节点")
                continue
        elif "otherwise" in item:
            errors.add(f"{where}.nodes[{index}].otherwise", "挂在分类节点下的规则节点，没有关系时就放在这个分类节点下")
            continue
        else:
            otherwise = parent
        for kind in kinds:
            if under and under not in vocabulary.PLACEMENTS.get(kind, {}):
                errors.add(f"{where}.nodes[{index}].under",
                           f"{kind} 没有与 {under} 的关系；可用：{'、'.join(vocabulary.PLACEMENTS.get(kind, {})) or '无'}")
        for kind in kinds:
            taken = next((pattern for pattern in patterns.get(kind, ()) if pattern.under == under), None)
            if taken is not None:
                errors.add(f"{where}.nodes[{index}].each",
                           f"{kind} 已由 {taken.node} 按{'关系 ' + under if under else '无关系'}放置；同类对象的多个位置须按不同关系区分")
                continue
            patterns[kind] = (*patterns.get(kind, ()), Pattern(node_id, kinds, under, otherwise))
    return Tree(name, root, {key: node for key, node in nodes.items() if key not in each_of}, default_parent), patterns


def declared_decisions() -> dict[str, tuple[DecisionSlot, ...]]:
    """instruction -> its declared decision slots, for every instruction this Portal installs."""
    from management_portal.hatg.catalog import instruction_catalog

    return {name: declaration.decides for name, declaration in instruction_catalog().items()}


# What a slot judges is declared with the instruction; a scheme only classifies its element.
_DECLARED_FIELDS = frozenset({"argument", "types", "optional", "resource", "via", "place"})


def _renamed(element: str, declared: Mapping[str, tuple[DecisionSlot, ...]]) -> str:
    """Where an element went when its instruction gained or lost slots (one slot takes the instruction's name)."""
    name = element.split(":", 1)[0]
    now = [found for found, _slot in decision_elements(name, declared[name])] if name in declared else []
    if not now:
        return ""
    return f"；{name} 的元素现在是 {'、'.join(now)}，原来的分类需要重新给出"


def _instructions(value: Any, a_nodes, r_nodes, declared: Mapping[str, tuple[DecisionSlot, ...]],
                  defaults: tuple[str, str], errors: _Errors, *, before: bool = False) -> dict[str, tuple[Slot, ...]]:
    """Every declared instruction's slots, each under the A class node the scheme classifies its element at.

    ``before`` compiles rules a release has left (a built-in scheme's basis):
    an element they give an R node although its slot is an argument slot now
    was a fixed slot then, and stays one, so the comparison sees what it asked.
    """
    if not isinstance(value, Mapping):
        errors.add("instructions", "必须是 A 元素到分类（a，固定槽另有 r）的对象")
        value = {}
    elements = {element: (name, slot) for name, decides in declared.items()
                for element, slot in decision_elements(name, decides)}
    classified: dict[str, tuple[str, str]] = {}
    was_fixed: set[str] = set()
    for element, item in value.items():
        where = f"instructions.{element}"
        if element not in elements:
            errors.add(where, "A 元素不存在：指令声明中没有这个判定槽" + _renamed(element, declared))
            continue
        if not isinstance(item, Mapping) or (set(item) & _DECLARED_FIELDS):
            errors.add(where, "判定槽由指令声明给出；方案只写 a（A 树分类节点），固定槽另写 r（R 树分类节点）")
            continue
        if set(item) - {"a", "r"}:
            errors.add(where, "分类只含 a 与 r")
            continue
        if item.get("a") not in a_nodes:
            errors.add(f"{where}.a", "必须是 A 树分类节点")
            continue
        fixed = not elements[element][1].argument
        if "r" in item and not fixed and before:
            was_fixed.add(element)
        elif "r" in item and not fixed:
            errors.add(f"{where}.r", "参数槽判定参数指向的对象，不写 r；r 只用于固定槽")
            continue
        if "r" in item and item["r"] not in r_nodes:
            errors.add(f"{where}.r", "必须是 R 树分类节点")
            continue
        classified[element] = (item["a"], item.get("r", defaults[1]))
    result: dict[str, tuple[Slot, ...]] = {}
    for name, decides in declared.items():
        slots = []
        for element, slot in decision_elements(name, decides):
            a, r = classified.get(element, defaults)
            if element in was_fixed:
                slots.append(Slot(element, a, resource=r, classified=True))
                continue
            slots.append(Slot(element, a, slot.argument, tuple(slot.types), slot.optional,
                              resource="" if slot.argument else r, via=slot.via, place=tuple(slot.place),
                              classified=element in classified))
        result[name] = tuple(slots)
    return result


def _entries(value: Any, trees: Mapping[str, Tree], patterns, errors: _Errors) -> tuple[tuple[Rule, ...], tuple[Rule, ...]]:
    if not isinstance(value, list):
        errors.add("entries", "必须是数组")
        return (), ()
    fixed: list[Rule] = []
    rules: list[Rule] = []
    seen: set[str] = set()
    r_patterns = {pattern.node: pattern for found in patterns["R"].values() for pattern in found}
    p_types = set(patterns["P"])
    for index, item in enumerate(value):
        where = f"entries[{index}]"
        if not isinstance(item, Mapping) or set(item) - {"id", "effect", "p", "relation", "a", "r", "title"} \
                or ("p" in item) == ("relation" in item):
            errors.add(where, "节点规则含 id、effect、p、a、r、title；关系规则把 p 换成 relation")
            continue
        rule_id = item.get("id") or f"entry_{index}"
        if not isinstance(rule_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", rule_id) or rule_id in seen:
            errors.add(f"{where}.id", "id 由小写字母、数字、下划线组成且不重复")
            continue
        seen.add(rule_id)
        effect = item.get("effect", PERMIT)
        if effect not in (PERMIT, DENY):
            errors.add(f"{where}.effect", "只能是 permit 或 deny")
            continue
        # Node IDs and relations are strings; anything else names nothing.
        a, r, p, relation = (value if isinstance(value, str) else "" for value in
                             (item.get("a"), item.get("r"), item.get("p"), item.get("relation")))
        if a not in trees["A"].nodes:
            errors.add(f"{where}.a", "必须是 A 树分类节点")
            continue
        if "p" in item:
            if p not in trees["P"].nodes:
                errors.add(f"{where}.p", "节点规则的 p 必须是 P 树分类节点；按对象展开的规则用 relation")
                continue
            if r not in trees["R"].nodes:
                errors.add(f"{where}.r", "节点规则的 r 必须是 R 树分类节点；按对象展开的规则用 relation")
                continue
            fixed.append(Rule(rule_id, effect, p, a, r))
            continue
        if relation not in vocabulary.RELATIONS:
            errors.add(f"{where}.relation", f"关系必须取自：{'、'.join(vocabulary.RELATIONS)}")
            continue
        pattern = r_patterns.get(r)
        if pattern is None:
            errors.add(f"{where}.r", "关系规则的 r 必须是 R 树规则节点（一类对象）")
            continue
        applies = vocabulary.RELATIONS[relation]["types"]
        if applies != "any" and any(kind not in applies for kind in pattern.types):
            errors.add(f"{where}.relation", f"{relation} 关系只用于这些对象：{'、'.join(applies)}")
            continue
        if relation == "belongs" and any(kind not in p_types for kind in pattern.types):
            errors.add(f"{where}.relation", "所属关系的主体是该对象在 P 树中的位置；方案须在 P 树放置这类对象")
            continue
        rules.append(Rule(rule_id, effect, "", a, r, relation))
    return tuple(fixed), tuple(rules)


def rules_digest(source: Mapping[str, Any]) -> str:
    """The digest of what an instance is generated from: the trees and the rule entries, without titles."""
    def untitled(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: untitled(item) for key, item in value.items() if key != "title"}
        if isinstance(value, list):
            return [untitled(item) for item in value]
        return value

    material = {"trees": untitled(source.get("trees")), "entries": untitled(source.get("entries") or [])}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def compile_scheme(source: Mapping[str, Any], *,
                   instructions: Mapping[str, tuple[DecisionSlot, ...]] | None = None,
                   before: bool = False) -> CompiledScheme:
    """Check a scheme's rules and build the engine's input; raises SchemeError listing every problem.

    ``instructions`` maps each instruction to its declared decision slots
    (default: the installed catalog); every classified element must be one of theirs.
    ``before``: the rules a release left, compiled only to compare with (``_instructions``).
    """
    errors = _Errors()
    if not isinstance(source, Mapping) or not schema_matches(source.get("schema"), expected="tfi.permission-scheme.v2"):
        raise SchemeError([{"path": "schema", "message": f"必须是 {SCHEMA}"}])
    for name in sorted(set(source) - FIELDS):
        errors.add(name, "未知字段")
    scheme_id = source.get("scheme_id")
    if not isinstance(scheme_id, str) or not _SCHEME_ID.fullmatch(scheme_id):
        errors.add("scheme_id", "以小写字母开头，只含小写字母、数字和下划线")
    trees_value = source.get("trees")
    if not isinstance(trees_value, Mapping) or set(trees_value) != set(TREES):
        raise SchemeError([*errors.items, {"path": "trees", "message": "需要 P、A、R 三棵树"}])
    trees: dict[str, Tree] = {}
    patterns: dict[str, dict[str, tuple[Pattern, ...]]] = {}
    for name in TREES:
        tree, found = _tree(name, trees_value[name], errors)
        if tree is not None:
            trees[name] = tree
        patterns[name] = found
    if len(trees) != 3:
        raise SchemeError(errors.items)
    if patterns["A"]:
        errors.add("trees.A", "A 树只有分类节点；指令通过 instructions 挂到分类节点下")
    user_categories = _user_categories(trees_value["P"].get("nodes") or [], trees["P"], errors)
    mapped = _instructions(source.get("instructions") or {}, trees["A"].nodes, trees["R"].nodes,
                           declared_decisions() if instructions is None else instructions,
                           (trees["A"].default_parent, trees["R"].default_parent), errors, before=before)
    a_nodes = dict(trees["A"].nodes)
    for slots in mapped.values():
        for slot in slots:
            a_nodes[slot.element] = Node(slot.element, slot.a, "", a_nodes[slot.a].path + (slot.element,))
    trees["A"] = Tree("A", trees["A"].root, a_nodes, trees["A"].default_parent)
    fixed, rules = _entries(source.get("entries") or [], trees, patterns, errors)
    if errors.items:
        raise SchemeError(errors.items)
    digest = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return CompiledScheme(
        scheme_id=scheme_id, title=str(source.get("title") or scheme_id),
        description=str(source.get("description") or ""), digest=digest, rules_digest=rules_digest(source),
        trees=MappingProxyType(trees),
        patterns=MappingProxyType({name: MappingProxyType(found) for name, found in patterns.items()}),
        fixed=fixed, rules=rules, instructions=MappingProxyType(mapped), source=MappingProxyType(dict(source)),
        user_categories=MappingProxyType(user_categories),
    )


def _user_categories(raw: list, tree: Tree, errors: _Errors) -> dict[str, str]:
    """category -> the P class node its Users sit under; each category names at most one node."""
    found: dict[str, str] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping) or "user_category" not in item:
            continue
        path, category = f"trees.P.nodes[{index}].user_category", item["user_category"]
        if item.get("id") not in tree.nodes:
            errors.add(path, "只有分类节点可以代表 User 分类")
        elif category not in vocabulary.USER_CATEGORIES:
            errors.add(path, f"User 分类必须取自：{'、'.join(vocabulary.USER_CATEGORIES)}")
        elif category in found:
            errors.add(path, f"{category} 已由 {found[category]} 代表；每个 User 分类只有一个节点")
        else:
            found[category] = item["id"]
    return found
