from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Iterable


ROUTE_NOT_FOUND = object()
RouteHandler = Callable[[Any, Any, str], Any] | Callable[[Any, Any, str, dict[str, str]], Any]


@dataclass(frozen=True, slots=True)
class RouteDefinition:
    method: str
    path: str
    handler: RouteHandler
    group: str
    phase: str = "protected"
    match: str = "exact"
    page: bool = False

    def __post_init__(self) -> None:
        if self.page and (self.method != "GET" or self.match != "exact" or not self.path.startswith("/")):
            raise ValueError("Navigation pages must be explicit GET document routes")
        if self.method != self.method.upper():
            raise ValueError("HTTP route methods must be uppercase")
        if not self.path.startswith("/") and not (self.path == "" and self.match == "exact"):
            raise ValueError("HTTP route paths must be absolute")
        if self.phase not in {"public", "dual-auth", "protected"}:
            raise ValueError(f"unknown HTTP route phase: {self.phase}")
        if self.match not in {"exact", "prefix", "prefix_fallback", "template", "fallback"}:
            raise ValueError(f"unknown HTTP route match mode: {self.match}")
        if self.match == "template" and not any(
            re.fullmatch(r"\{[a-z_]+\}", segment)
            for segment in self.path.split("/")
        ):
            raise ValueError("HTTP route templates need a named path segment")

    @staticmethod
    def _segments_overlap(left: str, right: str) -> bool:
        left_parts = left.split("/")
        right_parts = right.split("/")
        return len(left_parts) == len(right_parts) and all(
            a == b or re.fullmatch(r"\{[a-z_]+\}", a) or re.fullmatch(r"\{[a-z_]+\}", b)
            for a, b in zip(left_parts, right_parts)
        )

    def matches(self, method: str, path: str, phase: str) -> bool:
        if self.method != method or self.phase != phase:
            return False
        if self.match == "exact":
            return path == self.path
        if self.match in {"prefix", "prefix_fallback"}:
            return path.startswith(self.path)
        if self.match == "template":
            return self.template_params(path) is not None
        if self.match == "fallback":
            return True
        raise AssertionError("validated route match mode became invalid")

    def template_params(self, path: str) -> dict[str, str] | None:
        if self.match != "template":
            return None
        parts = path.split("/")
        template = self.path.split("/")
        if len(parts) != len(template):
            return None
        params: dict[str, str] = {}
        for segment, value in zip(template, parts):
            placeholder = re.fullmatch(r"\{([a-z_]+)\}", segment)
            if placeholder:
                if not value:
                    return None
                params[placeholder.group(1)] = value
            elif segment != value:
                return None
        return params

    def overlaps(self, other: "RouteDefinition") -> bool:
        if self.method != other.method or self.phase != other.phase:
            return False
        if self.match == "fallback" or other.match == "fallback":
            return self.match == other.match
        if {self.match, other.match} == {"exact", "prefix_fallback"}:
            return False
        if self.match == other.match == "exact":
            return self.path == other.path
        if self.match == "template" or other.match == "template":
            if self.match in {"prefix", "prefix_fallback"} or other.match in {"prefix", "prefix_fallback"}:
                prefix = self if self.match in {"prefix", "prefix_fallback"} else other
                template = other if self.match in {"prefix", "prefix_fallback"} else self
                static_start = template.path.split("{", 1)[0]
                return static_start.startswith(prefix.path) or prefix.path.startswith(static_start)
            return self._segments_overlap(self.path, other.path)
        if self.match in {"prefix", "prefix_fallback"} and other.match in {"prefix", "prefix_fallback"}:
            return self.path.startswith(other.path) or other.path.startswith(self.path)
        exact = self if self.match == "exact" else other
        prefix = other if self.match == "exact" else self
        return exact.path.startswith(prefix.path)


class RouteDispatcher:
    def __init__(self, routes: Iterable[RouteDefinition]) -> None:
        self._routes = tuple(routes)
        accepted: list[RouteDefinition] = []
        fallbacks: set[tuple[str, str]] = set()
        for route in self._routes:
            scope = (route.method, route.phase)
            if scope in fallbacks:
                raise RuntimeError(f"HTTP route {route.method} {route.path} follows a fallback")
            for previous in accepted:
                if (previous.method, previous.phase) == scope and previous.match == "prefix_fallback" and route.match == "exact" and route.path.startswith(previous.path):
                    raise RuntimeError(f"HTTP route {route.method} {route.path} follows a prefix fallback")
                if not route.overlaps(previous):
                    continue
                raise RuntimeError(
                    f"HTTP route {route.method} {route.path} is owned by both "
                    f"{previous.group} and {route.group}"
                )
            accepted.append(route)
            if route.match == "fallback":
                fallbacks.add(scope)

    def dispatch(
        self,
        method: str,
        path: str,
        request: Any,
        parsed: Any,
        actor: str,
        *,
        phase: str,
    ) -> Any:
        for route in self._routes:
            if route.method != method or route.phase != phase:
                continue
            if route.match == "template":
                params = route.template_params(path)
                if params is None:
                    continue
                result = route.handler(request, parsed, actor, params)
            elif route.matches(method, path, phase):
                result = route.handler(request, parsed, actor)
            else:
                continue
            if result is not ROUTE_NOT_FOUND:
                return result
        return ROUTE_NOT_FOUND

    def definitions(self) -> tuple[RouteDefinition, ...]:
        return self._routes

    def page_paths(self) -> tuple[str, ...]:
        """Only explicitly registered documents, never APIs or prefix handlers."""
        return tuple(route.path for route in self._routes if route.page)
