"""The statement tree of an Effect script; ``lineage_effect_script`` parses and runs it."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Ref:
    path: tuple[str, ...]
    # ``${path:offset:length}`` keeps that part of the value's text.
    span: tuple[int, int | None] | None = None


@dataclass(frozen=True)
class JsonValue:
    """JSON text with ``${…}`` placeholders outside strings."""

    segments: tuple[str | Ref, ...]
    line: int


@dataclass(frozen=True)
class Word:
    """One shell word: literal text, variable references, or an instruction call."""

    parts: tuple[str | Ref, ...]
    quoted: bool
    line: int
    json: JsonValue | None = None
    call: "Call | None" = None
    offset: int = 0

    @property
    def text(self) -> str | None:
        """The literal text when the word has no expansion."""
        if self.json is not None or self.call is not None:
            return None
        if all(isinstance(part, str) for part in self.parts):
            return "".join(self.parts)  # type: ignore[arg-type]
        return None


@dataclass(frozen=True)
class Argument:
    name: str
    value: Word | JsonValue | "Call"
    line: int


@dataclass(frozen=True)
class Call:
    offset: int
    instruction: str
    arguments: tuple[Argument, ...]
    line: int


@dataclass(frozen=True)
class Assign:
    name: str
    value: Word | JsonValue | Call
    line: int


@dataclass(frozen=True)
class Output:
    arguments: tuple[Argument, ...]
    line: int


@dataclass(frozen=True)
class ExitStatement:
    line: int


@dataclass(frozen=True)
class Fail:
    reason: Word | None
    line: int


@dataclass(frozen=True)
class Test:
    operator: str
    operands: tuple[Word, ...]
    negate: bool
    line: int


@dataclass(frozen=True)
class CallCondition:
    target: Call | Assign
    line: int


@dataclass(frozen=True)
class Not:
    condition: Any
    line: int


@dataclass(frozen=True)
class AndOr:
    operator: str
    left: Any
    right: Any
    line: int


@dataclass(frozen=True)
class If:
    branches: tuple[tuple[Any, tuple[Any, ...]], ...]
    otherwise: tuple[Any, ...]
    line: int


@dataclass(frozen=True)
class For:
    name: str
    items: tuple[Word, ...]
    body: tuple[Any, ...]
    line: int


@dataclass(frozen=True)
class Script:
    statements: tuple[Any, ...]
    calls: tuple[Call, ...]  # in source order

    def index_of(self, call: Call) -> int:
        """1-based position of a call in source order."""
        return self.calls.index(call) + 1
