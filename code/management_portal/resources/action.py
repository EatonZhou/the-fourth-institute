from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from typing import Any, Iterator


ActionHandler = Callable[..., dict[str, Any]]
_ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)*$")


class ActionProviderRegistry:
    """Thread-safe resource-type and action-name dispatch registry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: dict[tuple[str, str], ActionHandler] = {}

    def register(
        self,
        resource_types: str | Iterable[str],
        actions: Mapping[str, ActionHandler],
        *,
        replace: bool = False,
    ) -> None:
        values = (
            [resource_types]
            if isinstance(resource_types, str)
            else list(resource_types)
        )
        clean_types = tuple(
            dict.fromkeys(str(value or "").strip() for value in values)
        )
        if not clean_types or any(not value for value in clean_types):
            raise ValueError("at least one resource type is required")
        if not isinstance(actions, Mapping) or not actions:
            raise ValueError("at least one resource action is required")
        clean_actions: dict[str, ActionHandler] = {}
        for action, handler in actions.items():
            clean_action = str(action or "").strip()
            if clean_action != action or not _ACTION_NAME_RE.fullmatch(clean_action):
                raise ValueError(f"invalid resource action: {clean_action}")
            if not callable(handler):
                raise TypeError("resource action handlers must be callable")
            clean_actions[clean_action] = handler

        with self._lock:
            conflicts = [
                f"{resource_type}:{action}"
                for resource_type in clean_types
                for action in clean_actions
                if (resource_type, action) in self._handlers
            ]
            if conflicts and not replace:
                raise ValueError(
                    "resource action already registered: " + ", ".join(conflicts)
                )
            for resource_type in clean_types:
                for action, handler in clean_actions.items():
                    self._handlers[(resource_type, action)] = handler

    def handler_for(self, resource_type: str, action: str) -> ActionHandler:
        clean_type = str(resource_type or "").strip()
        clean_action = str(action or "").strip()
        with self._lock:
            handler = self._handlers.get((clean_type, clean_action))
        if handler is None:
            raise ValueError(
                f"{clean_type} resources do not support action {clean_action}"
            )
        return handler

    def actions_for(self, resource_type: str) -> frozenset[str]:
        clean_type = str(resource_type or "").strip()
        with self._lock:
            return frozenset(
                action
                for (registered_type, action) in self._handlers
                if registered_type == clean_type
            )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Roll back every registration made by a failed loader."""
        with self._lock:
            previous = dict(self._handlers)
            try:
                yield
            except Exception:
                self._handlers = previous
                raise


def action_payload(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("resource action payload must be an object")
    return dict(value)
