from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import unquote

from management_common.resource_identity import ParsedResourceId, ResourceTypeRegistry


ResolveHandler = Callable[..., dict[str, Any]]
ContentHandler = Callable[..., tuple[bytes, str, dict[str, Any]]]


@dataclass(frozen=True)
class ResourceAddress:
    value: str
    identity: ParsedResourceId
    suffix: tuple[str, ...]

    @property
    def root_id(self) -> str:
        return self.identity.resource_id

    @property
    def resource_type(self) -> str:
        return self.identity.resource_type

class AddressParser:
    def __init__(self, identity: ResourceTypeRegistry) -> None:
        self.identity = identity

    def parse(self, address: str) -> ResourceAddress:
        clean_address = unquote(str(address or "").strip())
        if (
            not clean_address
            or "\x00" in clean_address
            or "\n" in clean_address
            or "\r" in clean_address
        ):
            raise ValueError("invalid resource address")
        clean_address = clean_address.strip().strip("/")
        root_id, separator, raw_suffix = clean_address.partition("/")
        identity = self.identity.parse(root_id)
        suffix = tuple(raw_suffix.split("/")) if separator else ()
        if any(not part for part in suffix):
            raise ValueError("resource address contains an empty segment")
        return ResourceAddress(clean_address, identity, suffix)


@dataclass(frozen=True)
class ResourceResolver:
    resolve: ResolveHandler
    read_content: ContentHandler | None = None
    read_content_range: ContentHandler | None = None


class ResolverRegistry:
    """Thread-safe type-to-resolver registry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._resolvers: dict[str, ResourceResolver] = {}

    def register(
        self,
        resource_types: str | Iterable[str],
        resolver: ResourceResolver,
        *,
        replace: bool = False,
    ) -> None:
        values = [resource_types] if isinstance(resource_types, str) else list(resource_types)
        clean_types = tuple(dict.fromkeys(str(value or "").strip() for value in values))
        if not clean_types or any(not value for value in clean_types):
            raise ValueError("at least one resource type is required")
        with self._lock:
            conflicts = [value for value in clean_types if value in self._resolvers]
            if conflicts and not replace:
                raise ValueError(
                    f"resource resolver already registered: {', '.join(conflicts)}"
                )
            for resource_type in clean_types:
                self._resolvers[resource_type] = resolver

    def resolver_for(self, resource_type: str) -> ResourceResolver:
        with self._lock:
            resolver = self._resolvers.get(resource_type)
        if resolver is None:
            raise ValueError(f"no address resolver registered for {resource_type}")
        return resolver

    def registered_types(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._resolvers)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Roll back every registration made by a failed loader."""
        with self._lock:
            previous = dict(self._resolvers)
            try:
                yield
            except Exception:
                self._resolvers = previous
                raise
