from __future__ import annotations

import json
import os
import copy
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_RESOURCE_TYPES_PATH = Path(__file__).resolve().parents[1] / "config" / "resource-types"


@dataclass(frozen=True)
class ParsedResourceId:
    resource_id: str
    resource_type: str
    code: str


class ResourceTypeRegistry:
    """Canonical resource type and ID rules loaded from repository declarations."""

    # Parsing many IDs in one request must not repeatedly stat the directory.
    RECHECK_SECONDS = 1.0

    def __init__(self, directory: str | Path | None = None) -> None:
        self.path = Path(directory or os.environ.get("PORTAL_RESOURCE_TYPES_DIR") or DEFAULT_RESOURCE_TYPES_PATH).expanduser()
        self._lock = threading.RLock()
        self._loaded_signature: tuple | None = None
        self._metadata: dict[str, object] = {}
        self._declarations: dict[str, dict[str, object]] = {}
        self._checked_at = 0.0
        self._types: dict[str, dict[str, object]] = {}
        self._exceptions: dict[str, dict[str, object]] = {}
        self._id_pattern = re.compile(
            r"^(?P<type>[a-z][a-z0-9]*(?:_[a-z0-9]+)*)_(?P<code>[0-9a-f]{12})$"
        )

    def _signature(self) -> tuple:
        try:
            stat = self.path.stat()
            files = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(self.path.glob("*.json")))
        except OSError as exc:
            raise RuntimeError("TFI Portal resource type registry is unavailable") from exc
        return stat.st_mtime_ns, len(files), files

    def _load(self) -> None:
        if (
            self._loaded_signature is not None
            and time.monotonic() - self._checked_at < self.RECHECK_SECONDS
        ):
            return
        signature = self._signature()
        with self._lock:
            if self._loaded_signature == signature:
                self._checked_at = time.monotonic()
                return
            try:
                payload = json.loads((self.path / "_registry.json").read_text(encoding="utf-8"))
                declarations = {}
                for path in sorted(self.path.glob("*.json")):
                    if path.name == "_registry.json":
                        continue
                    definition = json.loads(path.read_text(encoding="utf-8"))
                    self._validate_declaration(path.stem, definition)
                    declarations[path.stem] = definition
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("TFI Portal resource type registry is unavailable") from exc
            exceptions = payload.get("id_exceptions") if isinstance(payload, dict) else None
            types = {key: value for key, value in declarations.items() if key not in (exceptions or {})}
            if not isinstance(types, dict) or not types:
                raise RuntimeError("TFI Portal resource type registry has no types")
            if not isinstance(exceptions, dict):
                raise RuntimeError("TFI Portal resource type registry has invalid ID exceptions")
            type_pattern = str(payload.get("type_pattern") or "")
            code_pattern = str(payload.get("code_pattern") or "")
            try:
                id_pattern = re.compile(
                    rf"^(?P<type>{type_pattern})_(?P<code>{code_pattern})$"
                )
            except re.error as exc:
                raise RuntimeError("TFI Portal resource type registry has invalid patterns") from exc
            self._types = {str(key): dict(value) for key, value in types.items() if isinstance(value, dict)}
            self._declarations = declarations
            self._metadata = dict(payload)
            self._exceptions = {
                str(key): dict(value)
                for key, value in exceptions.items()
                if isinstance(value, dict)
            }
            self._id_pattern = id_pattern
            self._loaded_signature = signature
            self._checked_at = time.monotonic()

    @staticmethod
    def _validate_declaration(name: str, definition: object) -> None:
        required = {"type", "category", "title", "description", "provider", "catalog_section",
                    "catalog_order", "registerable", "scope", "unit", "enabled_by_default"}
        if not isinstance(definition, dict) or not required <= definition.keys() or definition["type"] != name:
            raise RuntimeError(f"invalid resource type declaration: {name}")
        title = definition["title"]
        if not isinstance(title, dict) or not all(isinstance(title.get(lang), str) and title[lang] for lang in ("zh", "en")):
            raise RuntimeError(f"invalid resource type title: {name}")
        if definition["scope"] not in {"shared", "project", "lineage"} or not definition["unit"]:
            raise RuntimeError(f"invalid resource type scope/unit: {name}")
        if any(type(definition[field]) is not bool for field in ("registerable", "enabled_by_default")):
            raise RuntimeError(f"invalid resource type flags: {name}")
        if type(definition["catalog_order"]) is not int:
            raise RuntimeError(f"invalid resource type catalog order: {name}")
        provider = definition["provider"]
        if not isinstance(provider, str) or (provider and not re.fullmatch(r"management_portal\.resources\.providers\.[a-z_]+:[A-Za-z_][A-Za-z_0-9]*", provider)):
            raise RuntimeError(f"invalid resource type provider: {name}")

    @property
    def declarations(self) -> dict[str, dict[str, object]]:
        self._load()
        with self._lock:
            return copy.deepcopy(self._declarations)

    @property
    def metadata(self) -> dict[str, object]:
        self._load()
        with self._lock:
            return copy.deepcopy(self._metadata)

    def declaration(self, resource_type: str) -> dict[str, object]:
        self._load()
        with self._lock:
            if resource_type not in self._declarations:
                raise ValueError(f"unregistered resource type: {resource_type}")
            return copy.deepcopy(self._declarations[resource_type])

    @property
    def types(self) -> frozenset[str]:
        self._load()
        with self._lock:
            return frozenset(self._types)

    @property
    def id_exception_types(self) -> frozenset[str]:
        self._load()
        with self._lock:
            return frozenset(self._exceptions)

    def is_registered(self, resource_type: str) -> bool:
        clean_type = str(resource_type or "").strip()
        self._load()
        with self._lock:
            return clean_type in self._types or clean_type in self._exceptions

    def parse(self, resource_id: str) -> ParsedResourceId:
        clean_id = str(resource_id or "").strip()
        self._load()
        with self._lock:
            match = self._id_pattern.fullmatch(clean_id)
            if not match:
                # Existing Notice Board IDs predate unified IDs. New IDs keep
                # the twelve-digit contract; the original post identity stays.
                for kind, definition in self._types.items():
                    legacy = definition.get("legacy_code_pattern")
                    if legacy:
                        match = re.fullmatch(rf"(?P<type>{re.escape(kind)})_(?P<code>{legacy})", clean_id)
                        if match:
                            break
            if not match:
                raise ValueError(f"invalid resource id: {clean_id!r}")
            resource_type = match.group("type")
            if resource_type not in self._types:
                raise ValueError(f"unregistered resource type: {resource_type}")
            return ParsedResourceId(
                resource_id=clean_id,
                resource_type=resource_type,
                code=match.group("code"),
            )

    def validate(self, resource_id: str, resource_type: str | None = None) -> ParsedResourceId:
        clean_id = str(resource_id or "").strip()
        clean_type = str(resource_type or "").strip()
        if not clean_type:
            return self.parse(clean_id)
        self._load()
        with self._lock:
            if clean_type == "project":
                if not clean_id or "/" in clean_id or "\x00" in clean_id:
                    raise ValueError(f"invalid project id: {clean_id!r}")
                return ParsedResourceId(clean_id, clean_type, "")
            if clean_type == "user":
                if (
                    not clean_id
                    or "/" in clean_id
                    or "\x00" in clean_id
                    or any(character.isspace() for character in clean_id)
                ):
                    raise ValueError(f"invalid user id: {clean_id!r}")
                return ParsedResourceId(clean_id, clean_type, "")
            if clean_type not in self._types:
                raise ValueError(f"unregistered resource type: {clean_type}")
            try:
                parsed = self.parse(clean_id)
            except ValueError:
                parsed = None
            if parsed is None or parsed.resource_type != clean_type:
                raise ValueError(
                    f"resource id {clean_id!r} does not match type {clean_type!r}"
                )
            return parsed

    def new_id(
        self,
        resource_type: str,
        existing_ids: Iterable[str] = (),
    ) -> str:
        clean_type = str(resource_type or "").strip()
        self._load()
        with self._lock:
            if clean_type in self._exceptions:
                raise ValueError(
                    f"{clean_type} IDs are registered externally and cannot be generated"
                )
            if clean_type not in self._types:
                raise ValueError(f"unregistered resource type: {clean_type}")
        occupied = {str(value or "").strip() for value in existing_ids}
        while True:
            candidate = f"{clean_type}_{secrets.token_hex(6)}"
            if candidate not in occupied:
                from .resource_lifecycle import record_allocation

                record_allocation(candidate)
                return candidate

_REGISTRY_LOCK = threading.Lock()
_REGISTRY: ResourceTypeRegistry | None = None


def get_resource_type_registry() -> ResourceTypeRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = ResourceTypeRegistry()
    return _REGISTRY
