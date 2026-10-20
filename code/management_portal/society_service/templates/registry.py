"""Contracts shared by independently implemented template kinds."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class TemplateError(ValueError):
    pass


@dataclass
class ApplyContext:
    lineage_id: str
    template: dict
    parameters: dict
    target: dict
    actor: str
    title: str = ""
    resident_settings: dict = field(default_factory=dict)
    # Found-element key -> the element IDs the applier allows it to bind.
    match_choices: dict = field(default_factory=dict)


class TemplateKind(Protocol):
    key: str
    title: str
    targets: tuple[str, ...]

    def normalize(self, template: dict) -> dict: ...
    def describe(self, template: dict) -> dict: ...
    def editor(self) -> dict: ...
    def apply(self, ctx: ApplyContext) -> dict: ...


_KINDS: dict[str, TemplateKind] = {}
_loaded = False


def register(kind: TemplateKind) -> None:
    if kind.key in _KINDS and _KINDS[kind.key] is not kind:
        raise TemplateError(f"模板模块重复注册：{kind.key}")
    _KINDS[kind.key] = kind


def _load() -> None:
    global _loaded
    if _loaded:
        return
    from . import explore, familize, prompt, script, structure

    for module in (prompt, structure, script, explore, familize):
        register(module.KIND)
    _loaded = True


def get(key: str) -> TemplateKind:
    _load()
    try:
        return _KINDS[key]
    except KeyError:
        raise TemplateError(f"未知模板模块：{key}") from None


def kinds() -> list[dict]:
    _load()
    return [
        {
            "key": kind.key,
            "title": kind.title,
            "targets": list(kind.targets),
            "editor": kind.editor(),
        }
        for kind in _KINDS.values()
    ]


def result(summary: str = "", **extra) -> dict:
    return {
        "function_block_ids": [],
        "created": {"resident_ids": [], "component_ids": [], "wire_ids": []},
        "delivered": [],
        "trigger_path_ids": [],
        "summary": summary,
        **extra,
    }
