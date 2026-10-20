"""Instruction declarations: the only description an instruction has.

A declaration lists the instruction name, category, owning service, a
one-sentence description, parameters, result fields, whether it changes
state, whether it streams, which parameter names the target object, and the
decision slots: which objects a call is judged on (decision_898b8911a2ff
"指令声明与目录").  Each slot becomes one A element; a permission scheme
only says which A class node each element sits under and which R node a
fixed slot asks about (decision_b820faf42218 §3.2).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import invalid


# The core categories; installed features add theirs (catalog.categories()).
CATEGORIES: dict[str, str] = {
    "resource": "资源",
    "user": "用户",
    "resident": "Resident",
    "conversation": "对话与上下文",
    "mailbox": "Mailbox",
    "resident_execution": "Resident 执行",
    "lineage_definition": "Lineage 定义",
    "lineage_state": "Lineage 共享状态",
    "lineage_run": "Lineage 运行",
    "script_run": "脚本运行",
    "function_block": "Function Block",
    "family": "Family",
    "family_internal": "Family 内部机制",
    "template": "模板",
    "organization": "组织与分工",
    "permission": "权限",
    "environment": "运行环境",
    "isa": "指令集自描述",
}

# Owning services.  ``entry`` means the receiving entry answers locally.
OWNERS: dict[str, str] = {
    "resource": "ResourceService",
    "web": "Portal Web",
    "lineage": "SocietyService",
    "execution": "ExecutionService",
    "entry": "入口本地",
}
# Owner marks embedded in instruction IDs so GetInstructionExecution can route.
OWNER_MARKS: dict[str, str] = {
    "resource": "res",
    "web": "web",
    "lineage": "lin",
    "execution": "exe",
    "entry": "ent",
}
PARAM_TYPES = frozenset({"string", "integer", "number", "boolean", "object", "array", "any"})


def _matches(value: Any, value_type: str) -> bool:
    if value_type == "any":
        return True
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "object":
        return isinstance(value, Mapping)
    if value_type == "array":
        return isinstance(value, list)
    return False


@dataclass(frozen=True, slots=True)
class Param:
    """One parameter or result field."""

    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    enum: tuple[Any, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None
    properties: Mapping[str, Mapping[str, Any]] | None = None
    items: Mapping[str, Any] | None = None
    allow_empty: bool = False

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise RuntimeError(f"未知参数类型 {self.type}（{self.name}）")

    def schema(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
        }
        if self.enum:
            payload["enum"] = list(self.enum)
        if self.minimum is not None:
            payload["minimum"] = self.minimum
        if self.maximum is not None:
            payload["maximum"] = self.maximum
        if self.max_length is not None:
            payload["max_length"] = self.max_length
        if self.properties is not None:
            payload["properties"] = copy.deepcopy(dict(self.properties))
        if self.items is not None:
            payload["items"] = copy.deepcopy(dict(self.items))
        return payload

    def check(self, value: Any) -> str:
        """Return an error description, or an empty string when valid."""

        if value is None:
            # JSON null is a value for an any-typed parameter (for example "unset" in a compare-and-set).
            return "" if not self.required or self.type == "any" else "缺少值"
        if not _matches(value, self.type):
            return f"应为 {self.type}"
        if self.enum and value not in self.enum:
            return "取值应为 " + " | ".join(str(item) for item in self.enum)
        if self.type in {"integer", "number"}:
            if self.minimum is not None and value < self.minimum:
                return f"不能小于 {self.minimum}"
            if self.maximum is not None and value > self.maximum:
                return f"不能大于 {self.maximum}"
        if self.max_length is not None and isinstance(value, (str, list)) and len(value) > self.max_length:
            return f"长度不能超过 {self.max_length}"
        return ""


@dataclass(frozen=True, slots=True)
class DecisionSlot:
    """One object a call is judged on; every permission scheme shares it (decision_b820faf42218 §3.2).

    An argument slot judges the objects ``argument`` (a parameter path) names,
    one request each; ``types`` limits them to these resource types.  An
    ``optional`` slot asks nothing when the argument is not given.  With
    ``via`` (``<record>.<field>``) the argument names saved records and the
    object judged is what each record names in that field; with ``place``
    (related type -> parameter paths) the argument is the type of an object
    about to be created and the object judged is the R node it will sit under.

    A fixed slot (``fixed``) names no object: the scheme says which R node it
    asks about.  Its name only tells the slots of one instruction apart.
    """

    argument: str = ""
    types: tuple[str, ...] = ()
    optional: bool = False
    via: str = ""
    place: tuple[tuple[str, tuple[str, ...]], ...] = ()
    fixed: str = ""

    def to_dict(self) -> dict[str, Any]:
        if not self.argument:
            return {"fixed": self.fixed}
        value: dict[str, Any] = {"argument": self.argument}
        if self.via:
            value["via"] = self.via
        if self.place:
            value["place"] = {related: list(paths) for related, paths in self.place}
        if self.types:
            value["types"] = list(self.types)
        if self.optional:
            value["optional"] = True
        return value


def On(argument: str, *types: str, optional: bool = False, via: str = "",
       place: Mapping[str, tuple[str, ...]] | None = None) -> DecisionSlot:
    """An argument slot: the objects ``argument`` names, of ``types`` when given."""

    return DecisionSlot(argument=argument, types=types, optional=optional, via=via,
                        place=tuple((related, tuple(paths)) for related, paths in (place or {}).items()))


def Fixed(name: str = "fixed") -> DecisionSlot:
    """A fixed slot: the scheme names the R node it asks about."""

    return DecisionSlot(fixed=name)


def decision_elements(name: str, decides: tuple[DecisionSlot, ...]) -> tuple[tuple[str, DecisionSlot], ...]:
    """``(A element, slot)`` per decision slot: one slot takes the instruction's name, several ``<name>:<argument or fixed name>``."""
    if len(decides) == 1:
        return ((name, decides[0]),)
    return tuple((f"{name}:{slot.argument or slot.fixed}", slot) for slot in decides)


# The slots of an instruction judged on no particular object.
FIXED = (Fixed(),)
# The slots of an instruction judged on the Lineage, Resident or User its ``<type>_id`` names.
ON_LINEAGE = (On("lineage_id", "lineage"),)
ON_RESIDENT = (On("resident_id", "resident"),)
ON_USER = (On("user_id", "user"),)


def P(name: str, type: str = "string", description: str = "", **options: Any) -> Param:
    """Optional parameter."""

    return Param(name=name, type=type, description=description, **options)


def R(name: str, type: str = "string", description: str = "", **options: Any) -> Param:
    """Required parameter."""

    return Param(name=name, type=type, required=True, description=description, **options)


# Paging (decision_898b8911a2ff "声明格式"): a call reads one page of ``limit`` items; ``cursor``
# hands back the previous page's ``next_cursor`` unchanged; a missing or empty ``next_cursor``
# means no page follows.
def CURSOR(description: str = "上一页结果的 next_cursor，原样交回；不填时读第一页", *, required: bool = False) -> Param:
    return Param(name="cursor", required=required, description=description)


def NEXT_CURSOR(description: str = "还有下一页时返回，作为下一次调用的 cursor；不返回或为空表示已读完") -> Param:
    return Param(name="next_cursor", description=description)


def cursor_offset(value: Any) -> int:
    """The position a ``cursor`` stands for, for a list paged by position (``offset_cursor`` wrote it)."""
    text = str(value or "").strip()
    if not text:
        return 0
    if not text.isdigit():
        raise invalid("cursor 无效：请原样交回上一页的 next_cursor")
    return int(text)


def offset_cursor(offset: Any) -> str:
    """The ``next_cursor`` of a list paged by position: empty when no page follows (``offset`` is None)."""
    return "" if offset is None else str(int(offset))


@dataclass(frozen=True, slots=True)
class InstructionDeclaration:
    name: str
    category: str
    owner: str
    description: str
    parameters: tuple[Param, ...] = ()
    result: tuple[Param, ...] = ()
    mutates: bool = False
    streaming: bool = False
    target: str = ""
    # Alternative required parameter groups: exactly one group must be present.
    one_of: tuple[tuple[str, ...], ...] = ()
    # Filter parameters of which at least one must be present; each given one is decided on its own.
    any_of: tuple[str, ...] = ()
    # (argument, owner) pairs: a call carrying that argument runs at that service instead of
    # ``owner``. One instruction can then cover state held by two services (ClearMailbox).
    argument_owners: tuple[tuple[str, str], ...] = ()
    # The objects a call is judged on; one A element each (decision_b820faf42218 §3.2).
    decides: tuple[DecisionSlot, ...] = ()

    @property
    def elements(self) -> tuple[tuple[str, DecisionSlot], ...]:
        return decision_elements(self.name, self.decides)

    def __post_init__(self) -> None:
        # The category is checked when the catalog is built: a feature's
        # declarations may use a category the feature itself adds.
        if self.owner not in OWNERS:
            raise RuntimeError(f"{self.name} 使用未知拥有服务 {self.owner}")
        names = [param.name for param in self.parameters]
        if len(names) != len(set(names)):
            raise RuntimeError(f"{self.name} 声明了重复参数")
        if self.streaming and self.mutates:
            raise RuntimeError(f"{self.name} 流式指令必须是只读指令")
        elements = [element for element, _slot in self.elements]
        if len(elements) != len(set(elements)):
            raise RuntimeError(f"{self.name} 的判定槽重名：每个槽的参数或固定名必须不同")
        target_root = self.target.split(".")[0]
        if target_root and target_root not in names:
            raise RuntimeError(f"{self.name} 的目标参数 {self.target} 未声明")
        for group in self.one_of:
            for name in group:
                if name not in names:
                    raise RuntimeError(f"{self.name} 的参数组引用了未声明参数 {name}")
        for name in self.any_of:
            if name not in names:
                raise RuntimeError(f"{self.name} 的参数组引用了未声明参数 {name}")
        for name, owner in self.argument_owners:
            if name not in names:
                raise RuntimeError(f"{self.name} 按未声明参数 {name} 分流")
            if owner not in OWNERS or owner == self.owner:
                raise RuntimeError(f"{self.name} 按参数分流到无效服务 {owner}")
        if self.argument_owners and self.streaming:
            raise RuntimeError(f"{self.name} 流式指令不按参数分流")
        elements = [element for element, _slot in self.elements]
        if len(elements) != len(set(elements)):
            raise RuntimeError(f"{self.name} 的判定槽重名")
        for slot in self.decides:
            if bool(slot.argument) == bool(slot.fixed):
                raise RuntimeError(f"{self.name} 的判定槽要么指向参数，要么是固定槽")
            if slot.argument and slot.argument.split(".")[0] not in names:
                raise RuntimeError(f"{self.name} 的判定槽引用了未声明参数 {slot.argument}")
            if (slot.via or slot.place) and (slot.types or (slot.via and slot.place)):
                raise RuntimeError(f"{self.name} 的判定槽 {slot.argument}：via、place、types 互斥")
            for _related, paths in slot.place:
                for path in paths:
                    if path.split(".")[0] not in names:
                        raise RuntimeError(f"{self.name} 的落点参数 {path} 未声明")

    @property
    def parameter_map(self) -> dict[str, Param]:
        return {param.name: param for param in self.parameters}

    @property
    def owners(self) -> tuple[str, ...]:
        """Every service that runs some calls of this instruction."""
        return (self.owner, *(owner for _name, owner in self.argument_owners))

    def owner_for(self, arguments: Mapping[str, Any]) -> str:
        """The service that runs a call with these arguments."""
        for name, owner in self.argument_owners:
            if arguments.get(name) not in (None, ""):
                return owner
        return self.owner

    def validate_arguments(self, arguments: Mapping[str, Any]) -> None:
        declared = self.parameter_map
        unknown = sorted(set(arguments) - set(declared))
        if unknown:
            raise invalid(
                f"{self.name} 不接受参数：" + "、".join(unknown), unknown_arguments=unknown
            )
        missing = [
            name
            for name, param in declared.items()
            if param.required
            and (
                (arguments.get(name) is None and not (param.type == "any" and name in arguments))
                or (param.type == "string" and arguments.get(name) == "" and not param.allow_empty)
            )
        ]
        if missing:
            raise invalid(f"{self.name} 缺少参数：" + "、".join(missing), missing_arguments=missing)
        problems = []
        for name, value in arguments.items():
            reason = declared[name].check(value)
            if reason:
                problems.append({"argument": name, "reason": reason})
        if problems:
            raise invalid(
                f"{self.name} 参数无效："
                + "；".join(f"{item['argument']} {item['reason']}" for item in problems),
                invalid_arguments=problems,
            )
        if self.one_of:
            present = [
                group
                for group in self.one_of
                if all(arguments.get(name) is not False and arguments.get(name) not in (None, "", [], {}) for name in group)
            ]
            if len(present) != 1:
                rendered = " 或 ".join("+".join(group) for group in self.one_of)
                raise invalid(
                    f"{self.name} 需要且只能提供一组参数：{rendered}",
                    alternatives=[list(group) for group in self.one_of],
                )
        if self.any_of and all(arguments.get(name) in (None, "", [], {}) for name in self.any_of):
            raise invalid(
                f"{self.name} 至少需要一个参数：" + "、".join(self.any_of),
                alternatives=[[name] for name in self.any_of],
            )

    def summary(self) -> dict[str, Any]:
        from .catalog import categories

        return {
            "instruction": self.name,
            "category": self.category,
            "category_title": categories()[self.category],
            "owner": self.owner,
            "owner_title": OWNERS[self.owner],
            "description": self.description,
            "mutates": self.mutates,
            "streaming": self.streaming,
        }

    def schema(self) -> dict[str, Any]:
        payload = self.summary()
        payload["parameters"] = [param.schema() for param in self.parameters]
        payload["result"] = [param.schema() for param in self.result]
        payload["target"] = self.target
        if self.one_of:
            payload["one_of"] = [list(group) for group in self.one_of]
        if self.any_of:
            payload["any_of"] = list(self.any_of)
        if self.argument_owners:
            payload["argument_owners"] = [{"argument": name, "owner": owner} for name, owner in self.argument_owners]
        return payload


@dataclass(frozen=True, slots=True)
class ResourceActionDeclaration:
    """A resource type's own action, run through ``RunResourceAction``."""

    resource_type: str
    action: str
    description: str
    parameters: tuple[Param, ...] = ()
    result: tuple[Param, ...] = ()
    mutates: bool = True

    def validate_arguments(self, arguments: Mapping[str, Any]) -> None:
        InstructionDeclaration(
            name="RunResourceAction",
            category="resource",
            owner="resource",
            description=self.description,
            parameters=self.parameters,
        ).validate_arguments(arguments)

    def schema(self) -> dict[str, Any]:
        return {
            "resource_type": self.resource_type,
            "action": self.action,
            "description": self.description,
            "parameters": [param.schema() for param in self.parameters],
            "result": [param.schema() for param in self.result],
            "mutates": self.mutates,
        }


def target_value(declaration: InstructionDeclaration, arguments: Mapping[str, Any]) -> str:
    """The ID of the object a call targets, anchored on the root of an address
    such as ``workspace_x/report.md``; "" when the call names none."""

    if not declaration.target:
        return ""
    value: Any = arguments
    for part in declaration.target.split("."):
        if not isinstance(value, Mapping):
            return ""
        value = value.get(part)
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip().split("/", 1)[0]


PLACEMENT = (
    P("near_resident_id", description="在该 Resident 附近寻找空位；只影响画布位置"),
    P("placement", "object", "节点尺寸信息（node_sizes、child_size），单位为画布坐标"),
    P("x", "number", "首选横坐标，被占用时就近避让"),
    P("y", "number", "首选纵坐标，被占用时就近避让"),
)


def merge(*groups: tuple[InstructionDeclaration, ...]) -> dict[str, InstructionDeclaration]:
    catalog: dict[str, InstructionDeclaration] = {}
    for group in groups:
        for declaration in group:
            if declaration.name in catalog:
                raise RuntimeError(f"指令 {declaration.name} 被声明了两次")
            catalog[declaration.name] = declaration
    return catalog
