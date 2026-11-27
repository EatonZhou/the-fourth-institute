"""Contracts shared by independently implemented template kinds."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def template_id(key):
    """The stable identity of a builtin template."""
    return (
        "template_"
        + hashlib.sha256(f"builtin-template:{key}".encode()).hexdigest()[:12]
    )


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


def result(summary: str = "", **extra) -> dict:
    return {
        "function_block_ids": [],
        "created": {"resident_ids": [], "component_ids": [], "wire_ids": []},
        "delivered": [],
        "trigger_path_ids": [],
        "summary": summary,
        **extra,
    }
