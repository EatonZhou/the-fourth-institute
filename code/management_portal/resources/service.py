from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

from management_common.resource_identity import (
    ResourceTypeRegistry,
    get_resource_type_registry,
)
from management_portal.portal_access_control import (
    PortalAccessStore,
)

from .access import ResourceAccessPolicy
from .action import ActionProviderRegistry, action_payload
from .address import AddressParser, ResourceAddress, ResourceResolver, ResolverRegistry
from .changes import ResourceChange, ResourceChangeDispatcher
from .content_payload import resolved_media_type
from .collaboration_registry import (
    RESOURCE_TYPES as COLLABORATION_RESOURCE_TYPES,
    descriptor_resource_record,
)
from .contracts import (
    ResourceCapabilities,
    ResourceQuery,
    normalize_resolution,
)
from .errors import ResourceServiceUnavailable
from .index import ResourceIndex
from .timing import current as current_timer, lap, timed
from .mutation import (
    MutationProviderRegistry,
    ResourceMutationProvider,
    mutation_payload,
)
from .query import query_records
from .registration import ResourceRegistrationStore


LOGGER = logging.getLogger(__name__)
# A collaboration bundle that takes longer is reported with its step timings.
SLOW_COLLABORATION_SYNC_SECONDS = 0.5


def _par_permissions():
    """The PAR implementation that keeps the instances, with the permission system on or off."""
    from management_portal import permission_system
    from management_portal.permission_system.par import PARPermissions

    return permission_system.active() if permission_system.mode() == permission_system.PAR else PARPermissions()


class ResourceService:
    """Public facade for resource identity, addressing, actions, and discovery."""

    Unavailable = ResourceServiceUnavailable

    def __init__(
        self,
        *,
        identity: ResourceTypeRegistry | None = None,
        resolvers: ResolverRegistry | None = None,
        mutation_providers: MutationProviderRegistry | None = None,
        action_providers: ActionProviderRegistry | None = None,
        index_provider: ResourceIndex | Callable[[], ResourceIndex] | None = None,
        resolver_loader: Callable[["ResourceService"], None] | None = None,
        mutation_provider_loader: Callable[["ResourceService"], None] | None = None,
        action_provider_loader: Callable[["ResourceService"], None] | None = None,
        access_policy: ResourceAccessPolicy | None = None,
        portal_access: PortalAccessStore | None = None,
        collaboration_store: Any = None,
        registration_store: ResourceRegistrationStore | None = None,
        type_registry: Any = None,
    ) -> None:
        self._identity = identity or get_resource_type_registry()
        from .resource_types import ResourceTypes
        self.resource_types = type_registry or ResourceTypes(identity=self._identity)
        self._addresses = AddressParser(self._identity)
        self._resolvers = resolvers or ResolverRegistry()
        self._mutation_providers = mutation_providers or MutationProviderRegistry()
        self._action_providers = action_providers or ActionProviderRegistry()
        self._index_provider = index_provider
        self._resolver_loader = resolver_loader
        self._mutation_provider_loader = mutation_provider_loader
        self._action_provider_loader = action_provider_loader
        self._portal_access = portal_access
        self._access = access_policy or ResourceAccessPolicy()
        if collaboration_store is None:
            from .collaboration_registry import CollaborationDescriptorStore

            collaboration_store = CollaborationDescriptorStore()
        self._collaboration = collaboration_store
        self._registrations = registration_store or ResourceRegistrationStore()
        self._loaded_provider_types: dict[str, set[str]] = {"resolver": set(), "mutation": set(), "action": set()}
        self._resolvers_loaded = False
        self._resolver_lock = threading.RLock()
        self._mutation_providers_loaded = False
        self._mutation_provider_lock = threading.RLock()
        self._action_providers_loaded = False
        self._action_provider_lock = threading.RLock()
        self._changes = ResourceChangeDispatcher(self._route_change, self._enqueue_change)

    def _index(self) -> ResourceIndex:
        provider = self._index_provider
        if provider is None:
            raise RuntimeError("resource index is not configured")
        if callable(provider) and not hasattr(provider, "query"):
            return provider()
        return provider

    def new_id(self, resource_type: str, existing_ids: Iterable[str] = ()) -> str:
        return self._identity.new_id(resource_type, existing_ids)

    def validate_id(
        self,
        resource_id: str,
        resource_type: str | None = None,
    ):
        return self._identity.validate(resource_id, resource_type)

    def is_valid_id(
        self,
        resource_id: object,
        resource_type: str | None = None,
    ) -> bool:
        try:
            self.validate_id(str(resource_id or "").strip(), resource_type)
        except ValueError:
            return False
        return True

    def parse_address(self, address: str) -> ResourceAddress:
        return self._addresses.parse(address)

    @staticmethod
    def portal_url_for_address(address: object, resource_type: object = "") -> str:
        from .portal import portal_url_for_address

        return portal_url_for_address(address, resource_type)

    @staticmethod
    def portal_url_for_resource(
        resource_id: object,
        resource_type: object = "",
        *,
        record: Mapping | None = None,
    ) -> str:
        from .portal import portal_url_for_resource

        return portal_url_for_resource(
            resource_id,
            resource_type,
            record=record,
        )

    def _ensure_provider(self, capability: str, resource_type: str | None) -> None:
        if resource_type is not None:
            self.resource_types.require(resource_type)
        registry, loader, lock, flag = {
            "resolver": (self._resolvers, self._resolver_loader, self._resolver_lock, "_resolvers_loaded"),
            "mutation": (self._mutation_providers, self._mutation_provider_loader, self._mutation_provider_lock, "_mutation_providers_loaded"),
            "action": (self._action_providers, self._action_provider_loader, self._action_provider_lock, "_action_providers_loaded"),
        }[capability]
        with lock:
            loaded = self._loaded_provider_types[capability]
            if getattr(self, flag) or resource_type in loaded:
                return
            selective = getattr(loader, "for_type", None)
            with registry.transaction():
                if resource_type is not None and callable(selective):
                    owned_types = selective(self, resource_type)
                else:
                    if loader is not None:
                        loader(self)
                    owned_types = None
            # Failed imports/registrations leave no partial entries or success marker.
            if owned_types is None:
                setattr(self, flag, True)
            else:
                loaded.update(owned_types)

    def _ensure_resolvers(self, resource_type: str | None = None) -> None:
        self._ensure_provider("resolver", resource_type)

    def register_resolver(
        self,
        resource_types: str | Iterable[str],
        resolve: Any,
        *,
        read_content: Any = None,
        read_content_range: Any = None,
        replace: bool = False,
    ) -> None:
        values = [resource_types] if isinstance(resource_types, str) else list(resource_types)
        for resource_type in values:
            clean_type = str(resource_type or "").strip()
            if not self._identity.is_registered(clean_type):
                raise ValueError(f"unregistered resource type: {clean_type}")
        self._resolvers.register(
            values,
            ResourceResolver(
                resolve=resolve,
                read_content=read_content,
                read_content_range=read_content_range,
            ),
            replace=replace,
        )

    def register_index_backed_resolver(
        self,
        resolve: Any,
        *,
        read_content: Any = None,
    ) -> None:
        """Bind indexed types that do not already have a domain resolver."""
        raw_types = self._index().indexed_types()
        if not isinstance(raw_types, (set, frozenset, list, tuple)):
            raise TypeError("resource index indexed_types() must return a collection")
        from .providers import resolver_domain_types

        indexed_types = frozenset(raw_types)
        resource_types = (
            indexed_types
            & self._identity.types
            - self._resolvers.registered_types()
            - (resolver_domain_types() if callable(getattr(self._resolver_loader, "for_type", None)) else frozenset())
        )
        if resource_types:
            self.register_resolver(
                sorted(resource_types),
                resolve,
                read_content=read_content,
            )

    def _ensure_mutation_providers(self, resource_type: str | None = None) -> None:
        self._ensure_provider("mutation", resource_type)

    def register_mutation_provider(
        self,
        resource_types: str | Iterable[str],
        *,
        register: Any = None,
        unregister: Any = None,
        update: Any = None,
        content_updatable: bool = False,
        service_managed_registration: bool = False,
        replace: bool = False,
    ) -> None:
        values = (
            [resource_types]
            if isinstance(resource_types, str)
            else list(resource_types)
        )
        for resource_type in values:
            clean_type = str(resource_type or "").strip()
            if not self._identity.is_registered(clean_type):
                raise ValueError(f"unregistered resource type: {clean_type}")
        self._mutation_providers.register(
            values,
            ResourceMutationProvider(
                register=register,
                unregister=unregister,
                update=update,
                content_updatable=bool(content_updatable),
                service_managed_registration=bool(service_managed_registration),
            ),
            replace=replace,
        )

    def _ensure_action_providers(self, resource_type: str | None = None) -> None:
        self._ensure_provider("action", resource_type)

    def register_action_provider(
        self,
        resource_types: str | Iterable[str],
        actions: Mapping[str, Any],
        *,
        replace: bool = False,
    ) -> None:
        values = (
            [resource_types]
            if isinstance(resource_types, str)
            else list(resource_types)
        )
        for resource_type in values:
            clean_type = str(resource_type or "").strip()
            if not self._identity.is_registered(clean_type):
                raise ValueError(f"unregistered resource type: {clean_type}")
        self._action_providers.register(
            values,
            actions,
            replace=replace,
        )

    @staticmethod
    def _mutation_record(
        result: Mapping[str, Any],
        *,
        resource_type: str,
        expected_id: str = "",
    ) -> tuple[dict[str, Any], str]:
        if not isinstance(result, Mapping):
            raise TypeError(
                f"{resource_type} mutation provider must return an object"
            )
        record = dict(result)
        resource_id = str(record.get("resource_id") or "").strip()
        if not resource_id:
            raise ValueError(
                f"{resource_type} mutation result requires resource_id"
            )
        identity_aliases = sorted(
            key
            for key, value in record.items()
            if key != "resource_id"
            and (key == "id" or key.endswith("_id"))
            and str(value or "").strip() == resource_id
        )
        if identity_aliases:
            raise ValueError(
                "resource mutation result contains identity aliases: "
                + ", ".join(identity_aliases)
            )
        if expected_id and resource_id != expected_id:
            raise ValueError(
                f"resource mutation returned {resource_id!r}, expected {expected_id!r}"
            )
        return record, resource_id

    def _complete_mutation(
        self,
        result: Mapping[str, Any],
        *,
        resource_type: str,
        expected_id: str,
        actor: str,
        kind: str,
        wait_for_index: bool,
        index_timeout: float,
        change_recording: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from management_common.operation_errors import failure_payload
        from .errors import ResourceOperationError

        resource_id = expected_id
        stage = "mutation_result"
        try:
            record, resource_id = self._mutation_record(
                result, resource_type=resource_type, expected_id=expected_id,
            )
            self._identity.validate(resource_id, resource_type)
            if change_recording:
                record["change_recording"] = change_recording
            if kind == "delete":
                stage = "registration_cleanup"
                self._registrations.forget(resource_id)
            stage = "authorization_sync"
            from management_portal import permission_system

            # Stores writing PostgreSQL records (Projects, Lineage objects, chatrooms, posts) place the object in
            # their own transaction; the file-backed ones (knowledge, papers, repos …) have no transaction to share
            # and are placed here, after their files are written.
            if kind == "delete":
                permission_system.retire_resources([resource_id])
            else:
                permission_system.sync_resource({**record, "resource_id": resource_id}, resource_type=resource_type)
            if kind == "create":
                stage = "index_projection"
                self._index().project_registered(resource_type, record)
                projection = {}
            else:
                stage = "index_notification"
                projection = self._notify_mutation(
                    resource_id=resource_id,
                    resource_type=resource_type,
                    kind={"update": "updated", "delete": "deleted"}[kind],
                    wait_for_index=wait_for_index,
                    index_timeout=index_timeout,
                )
            if projection:
                record["index_projection"] = projection
            return record
        except Exception as exc:
            raise ResourceOperationError(
                "resource.mutation_post_commit_failed", str(exc), 500,
                details={
                    "domain_committed": True,
                    "resource_id": resource_id,
                    "resource_type": resource_type,
                    "mutation": kind,
                    "stage": stage,
                    "cause": failure_payload(exc),
                },
            ) from exc

    def _notify_mutation(
        self,
        *,
        path: str = "",
        resource_id: str,
        resource_type: str,
        kind: str,
        wait_for_index: bool = False,
        index_timeout: float = 45.0,
    ) -> dict[str, Any]:
        try:
            notify_options: dict[str, Any] = {}
            if path:
                notify_options["path"] = path
            if wait_for_index:
                notify_options.update({
                    "wait": True,
                    "timeout": index_timeout,
                })
            notification = self.notify_source_changed(
                resource_id=resource_id,
                resource_type=resource_type,
                kind=kind,
                **notify_options,
            )
            if wait_for_index and (
                not notification.get("targets")
                or notification.get("finished") is not True
            ):
                LOGGER.warning(
                    "resource mutation index projection unconfirmed: %s",
                    resource_id,
                )
                return {
                    "status": "unconfirmed",
                    "reason": (
                        "refresh_incomplete"
                        if notification.get("targets")
                        else "no_targets"
                    ),
                    "wait_requested": True,
                    "targets": list(notification.get("targets") or []),
                }
        except Exception:
            # The domain registry write is authoritative. Reconciliation can
            # rebuild the derived index if this best-effort hint cannot queue.
            LOGGER.exception(
                "resource mutation committed but index notification failed: %s",
                resource_id,
            )
            return {
                "status": "unconfirmed",
                "reason": "notification_failed",
                "wait_requested": bool(wait_for_index),
                "targets": [],
            }
        return {}

    def register(
        self,
        resource_type: str,
        payload: Mapping[str, Any],
        *,
        actor: str = "",
        automatic: bool = False,
        wait_for_index: bool = False,
        index_timeout: float = 45.0,
    ) -> dict[str, Any]:
        clean_type = str(resource_type or "").strip()
        if not self._identity.is_registered(clean_type):
            raise ValueError(f"unregistered resource type: {clean_type}")
        clean_payload = mutation_payload(payload)
        self.resource_types.require(clean_type, registration=True)
        if "registration_key" in clean_payload:
            raise ValueError(
                "registration_key is internal to ResourceService; "
                "submit only resource fields"
            )
        self._ensure_mutation_providers(clean_type)
        provider = self._mutation_providers.provider_for(clean_type)
        clean_actor = str(actor or "").strip()
        if not automatic:
            self._require_mutation_actor(actor)
        if provider.register is None:
            raise ValueError(f"{clean_type} resources do not support registration")
        expected_id = ""
        provider_context: dict[str, Any] = {
            "actor": clean_actor,
            "automatic": bool(automatic),
        }
        if provider.service_managed_registration:
            expected_id, created_reservation = self._registrations.reserve(
                clean_type,
                clean_payload,
                actor=clean_actor,
                allocate=lambda: self.new_id(clean_type),
            )
            provider_context["resource_id"] = expected_id
            provider_context["reservation_created"] = created_reservation
        result = provider.register(clean_payload, **provider_context)
        return self._complete_mutation(
            result, resource_type=clean_type, expected_id=expected_id,
            actor=clean_actor, kind="create", wait_for_index=wait_for_index,
            index_timeout=index_timeout,
        )

    def _existing_identity(
        self,
        resource_id: str,
        resource_type: str = "",
        *,
        actor: str = "",
    ):
        clean_id = str(resource_id or "").strip()
        clean_type = str(resource_type or "").strip()
        if clean_type:
            return self._identity.validate(clean_id, clean_type)
        try:
            return self._identity.parse(clean_id)
        except ValueError as parse_error:
            try:
                record = self.get(clean_id, actor=actor)
            except (KeyError, RuntimeError):
                raise parse_error
            return self._identity.validate(
                clean_id,
                str(record.get("resource_type") or ""),
            )

    @staticmethod
    def _require_mutation_actor(actor: str) -> None:
        """Resource changes need an authenticated User or Resident; permissions
        belong to the permission system (decision_b820faf42218)."""
        from management_common.principal import is_user_or_resident

        if not is_user_or_resident(str(actor or "").strip()):
            raise PermissionError("Resource mutation requires an authenticated User or Resident")

    def update(
        self,
        resource_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str = "",
        resource_type: str = "",
        wait_for_index: bool = False,
        index_timeout: float = 45.0,
    ) -> dict[str, Any]:
        identity = self._existing_identity(resource_id, resource_type, actor=actor)
        self._ensure_mutation_providers(identity.resource_type)
        provider = self._mutation_providers.provider_for(identity.resource_type)
        self._require_mutation_actor(actor)
        clean_payload = mutation_payload(payload)
        if provider.update is None:
            raise ValueError(
                f"{identity.resource_type} resources do not support updates"
            )
        from .content_changes import capture_content_changes

        with capture_content_changes(actor=actor, resource_id=identity.resource_id) as captured:
            result = provider.update(
                identity.resource_id,
                clean_payload,
                actor=str(actor or ""),
            )
        change_recording = None
        if captured and (captured["errors"] or captured.get("unconfirmed_reason")):
            change_recording = {
                "status": "unconfirmed", "reason": captured.get("unconfirmed_reason") or "receipt_failed",
            }
        return self._complete_mutation(
            result, resource_type=identity.resource_type, expected_id=identity.resource_id,
            actor=actor, kind="update", wait_for_index=wait_for_index,
            index_timeout=index_timeout, change_recording=change_recording,
        )

    def execution_content_changes(self, execution_id: str, *, actor: str) -> list[dict[str, Any]]:
        from management_common.principal import is_user_or_resident
        from .content_changes import ContentChangeStore

        if not execution_id or not is_user_or_resident(actor):
            raise PermissionError("Execution content changes require their authenticated author")
        changes = ContentChangeStore().for_execution(execution_id, actor)
        # Possession of an execution id never grants access to another author's receipts.
        return changes

    def unregister(
        self,
        resource_id: str,
        *,
        actor: str = "",
        resource_type: str = "",
        wait_for_index: bool = False,
        index_timeout: float = 45.0,
    ) -> dict[str, Any]:
        identity = self._existing_identity(resource_id, resource_type, actor=actor)
        self._ensure_mutation_providers(identity.resource_type)
        provider = self._mutation_providers.provider_for(identity.resource_type)
        self._require_mutation_actor(actor)
        if provider.unregister is None:
            raise ValueError(
                f"{identity.resource_type} resources do not support unregistration"
            )
        result = provider.unregister(
            identity.resource_id,
            actor=str(actor or ""),
        )
        return self._complete_mutation(
            result, resource_type=identity.resource_type, expected_id=identity.resource_id,
            actor=actor, kind="delete", wait_for_index=wait_for_index,
            index_timeout=index_timeout,
        )

    def action(
        self,
        resource_id: str,
        action: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str = "",
        resource_type: str = "",
    ) -> dict[str, Any]:
        identity = self._existing_identity(resource_id, resource_type, actor=actor)
        clean_action = str(action or "").strip()
        self._ensure_action_providers(identity.resource_type)
        handler = self._action_providers.handler_for(identity.resource_type, clean_action)
        self._require_mutation_actor(actor)
        clean_payload = action_payload(payload)
        result = handler(
            identity.resource_id,
            clean_payload,
            actor=str(actor or ""),
        )
        if not isinstance(result, Mapping):
            raise TypeError(
                f"{identity.resource_type} action {clean_action} must return an object"
            )
        return dict(result)

    def resolve(
        self,
        address: str,
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        parsed = self.parse_address(address)
        self._ensure_resolvers(parsed.resource_type)
        resolver = self._resolvers.resolver_for(parsed.resource_type)
        resolved = normalize_resolution(
            parsed,
            resolver.resolve(
                parsed.value,
                actor=str(actor or ""),
            ),
        )
        self._access.authorize_read(resolved, actor=str(actor or ""))
        if not parsed.suffix:
            canonical_url = self.portal_url_for_resource(
                parsed.root_id,
                parsed.resource_type,
                record=resolved.get("record"),
            )
            if canonical_url:
                # Root resource navigation is owned by the resource-type URL
                # provider, so domain resolver output cannot override it.
                resolved["portal_url"] = canonical_url
        return resolved

    def read_content(
        self,
        address: str,
        *,
        actor: str = "",
        max_bytes: int = 4 * 1024 * 1024,
    ) -> tuple[bytes, str, dict[str, Any]]:
        parsed = self.parse_address(address)
        self._ensure_resolvers(parsed.resource_type)
        resolver = self._resolvers.resolver_for(parsed.resource_type)
        if resolver.read_content is None:
            raise ValueError(f"{parsed.resource_type} resources expose no byte content")
        resolved = self.resolve(
            parsed.value,
            actor=actor,
        )
        return resolver.read_content(
            resolved,
            address=parsed.value,
            actor=str(actor or ""),
            max_bytes=max_bytes,
        )

    def open_content(
        self,
        address: str,
        *,
        actor: str = "",
        max_bytes: int = 4 * 1024 * 1024,
    ) -> dict[str, Any] | tuple[bytes, str, dict[str, Any]]:
        """Resolve once; return a file plan or read bounded provider bytes.

        The result stays inside ResourceService until authorization completes.
        A caller-supplied resolution must never stand in for that check.
        """
        parsed = self.parse_address(address)
        self._ensure_resolvers(parsed.resource_type)
        resolver = self._resolvers.resolver_for(parsed.resource_type)
        resolved = self.resolve(parsed.value, actor=actor)
        resolved_path = Path(str(resolved.get("resolved_path") or ""))
        if resolved_path.is_file() or (
            resolved.get("target_kind") == "file"
            and type(resolved.get("content_size")) is int
            and type(resolved.get("content_mtime_ns")) is int
            and resolver.read_content_range is not None
        ):
            return resolved
        if resolver.read_content is None:
            raise ValueError(f"{parsed.resource_type} resources expose no byte content")
        data, content_type, content_resolution = resolver.read_content(
            resolved,
            address=parsed.value,
            actor=str(actor or ""),
            max_bytes=max_bytes,
        )
        metadata = content_resolution if isinstance(content_resolution, Mapping) else {}
        return data, content_type, {**resolved, **metadata}

    def read_content_range(
        self,
        address: str,
        *,
        start: int,
        end: int,
        actor: str = "",
    ) -> tuple[bytes, str, dict[str, Any]]:
        if type(start) is not int or type(end) is not int or start < 0 or end < start:
            raise ValueError("content range requires non-negative inclusive start and end")
        parsed = self.parse_address(address)
        self._ensure_resolvers(parsed.resource_type)
        resolver = self._resolvers.resolver_for(parsed.resource_type)
        if resolver.read_content_range is None:
            raise ValueError(f"{parsed.resource_type} resources expose no ranged byte content")
        resolved = self.resolve(
            parsed.value,
            actor=actor,
        )
        return resolver.read_content_range(
            resolved,
            address=parsed.value,
            actor=str(actor or ""),
            start=start,
            end=end,
        )

    def reference(
        self,
        address: str,
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        """Resolve an address and return a small, stable cross-surface reference."""
        parsed = self.parse_address(address)
        resolved = self.resolve(
            parsed.value,
            actor=actor,
        )
        if resolved.get("exists") is False:
            raise KeyError(parsed.value)

        try:
            indexed = self.get(parsed.root_id, actor=actor)
        except (KeyError, RuntimeError, ValueError):
            indexed = {}
        candidate = (
            dict(resolved.get("record"))
            if isinstance(resolved.get("record"), Mapping)
            else {}
        )
        if parsed.suffix:
            # A nested address names the child object, while the index still
            # describes its root.  Prefer target metadata supplied by the
            # domain resolver so every surface presents the addressed object.
            title = str(
                resolved.get("title")
                or candidate.get("title")
                or candidate.get("name")
                or candidate.get("label")
                or candidate.get("filename")
                or indexed.get("title")
                or parsed.root_id
            ).strip()
            summary = str(
                resolved.get("summary")
                or candidate.get("summary")
                or candidate.get("description")
                or indexed.get("summary")
                or ""
            ).strip()
        else:
            title = str(
                indexed.get("title")
                or candidate.get("title")
                or candidate.get("name")
                or candidate.get("label")
                or candidate.get("filename")
                or parsed.root_id
            ).strip()
            summary = str(
                indexed.get("summary")
                or candidate.get("summary")
                or candidate.get("description")
                or ""
            ).strip()
        attributes = (
            dict(indexed.get("attributes"))
            if isinstance(indexed.get("attributes"), Mapping)
            else {}
        )
        facets = [
            str(value)
            for value in indexed.get("facets") or []
            if str(value).strip()
        ]
        type_capabilities = self.capabilities(parsed.resource_type)
        content_editable = bool(type_capabilities.get("content_updatable"))
        if content_editable:
            try:
                self._identity.validate(parsed.root_id, parsed.resource_type)
                self._mutation_providers.provider_for(parsed.resource_type)
                self._require_mutation_actor(actor)
            except (KeyError, PermissionError):
                content_editable = False
        raw_version = (
            resolved.get("version")
            or candidate.get("version")
            or indexed.get("version")
            or attributes.get("version")
            or {}
        )
        version_value = (
            raw_version.get("number")
            if isinstance(raw_version, Mapping)
            else raw_version
        )
        version = str(version_value or "").strip()
        filename = str(
            resolved.get("filename")
            or candidate.get("filename")
            or ""
        ).strip()
        if not filename:
            resolved_path = str(resolved.get("resolved_path") or "").strip()
            if resolved_path:
                filename = Path(resolved_path).name
        media_type = resolved_media_type(
            resolved,
            candidate,
            {"filename": filename},
        )
        content_kind = str(
            resolved.get("content_kind")
            or candidate.get("content_kind")
            or ""
        ).strip().lower()
        raw_content_size = resolved.get("content_size")
        if type(raw_content_size) is not int:
            raw_content_size = candidate.get("content_size")
        content_size = (
            raw_content_size
            if type(raw_content_size) is int and raw_content_size >= 0
            else None
        )
        def canonical_portal_url(value: object) -> str:
            from .portal import canonical_portal_location

            return canonical_portal_location(parsed.value, value)

        generated_portal_url = self.portal_url_for_resource(
            parsed.root_id,
            parsed.resource_type,
            record=resolved.get("record") or indexed,
        )
        portal_url = (
            (
                canonical_portal_url(resolved.get("portal_url"))
                if parsed.suffix
                else ""
            )
            or generated_portal_url
            or canonical_portal_url(resolved.get("portal_url"))
            or canonical_portal_url(indexed.get("url"))
            or canonical_portal_url(candidate.get("portal_url"))
            or canonical_portal_url(candidate.get("url"))
            or f"/r/{quote(parsed.value, safe='')}"
        )
        return {
            "address": parsed.value,
            "resource_id": parsed.root_id,
            "resource_type": parsed.resource_type,
            "title": title or parsed.root_id,
            "filename": filename,
            "content_kind": content_kind,
            "media_type": media_type,
            "content_size": content_size,
            "summary": summary,
            "status": "resolved",
            "target_kind": str(resolved.get("target_kind") or "").strip(),
            "url": portal_url,
            "facets": facets,
            "attributes": attributes,
            "version": version,
            "capabilities": {
                "download": True,
                "edit": content_editable,
                "save": content_editable,
            },
        }

    def query(self, **kwargs: Any) -> dict[str, Any]:
        query = ResourceQuery.from_kwargs(kwargs)
        enabled_types = self.resource_types.enabled_types()
        if query.resource_types and not enabled_types.intersection(query.resource_types):
            return query_records((), query, portal_access=self._portal_access)
        if query.q:
            try:
                parsed = self.parse_address(query.q)
            except ValueError:
                parsed = None
            if parsed is not None and parsed.value == query.q.strip("/"):
                if parsed.resource_type not in enabled_types:
                    return query_records((), query, exact_address=True, portal_access=self._portal_access)
                try:
                    reference = self.reference(
                        parsed.value,
                        actor=query.actor,
                    )
                except (KeyError, FileNotFoundError):
                    return query_records(
                        (),
                        query,
                        exact_address=True,
                        portal_access=self._portal_access,
                    )
                return query_records(
                    (reference,),
                    query,
                    exact_address=True,
                    portal_access=self._portal_access,
                )
        return self._index().query(
            records_projector=self._collaboration_descriptors,
            records_projector_types=COLLABORATION_RESOURCE_TYPES,
            **kwargs,
        )

    def suggest(self, *, actor: str = "", q: str = "", limit: int = 30, resource_type: str = "") -> dict[str, Any]:
        return self._index().suggest(
            actor=actor,
            q=q,
            limit=limit,
            resource_type=resource_type,
            records_projector=self._collaboration_descriptors,
        )

    def get(self, resource_id: str, *, actor: str = "") -> dict[str, Any]:
        try:
            resource_types = (self._identity.parse(resource_id).resource_type,)
        except ValueError:
            resource_types = self._identity.id_exception_types
        if len(resource_types) == 1 and resource_types[0] in COLLABORATION_RESOURCE_TYPES:
            try:
                descriptor = self._collaboration.get(resource_id)
            except KeyError:
                if resource_types[0] == "workspace":
                    # A removed Workspace cannot expose file bytes through a
                    # stale discovery record. Its descriptor owns its path.
                    raise
                # Pre-descriptor index records retain their existing contract.
                # Registered objects never depend on discovery refresh progress.
                pass
            else:
                record = descriptor_resource_record(descriptor)
                # A Workspace descriptor already is one authoritative row:
                # do not mix its title/version with attributes from a later read.
                if resource_types[0] != "workspace":
                    record = self._with_collaboration_descriptor(record)
                from management_portal.society_service.lineage_access_policy import hidden_private_ids

                if not self._access.can_read(record, actor=str(actor or "")) or hidden_private_ids(actor, [record]):
                    raise KeyError(resource_id)
                record["capabilities"] = {name: True for name in ("discover", "read", "manage")}
                record.pop("search_terms", None)
                return record
        record = self._index().get_resource(
            resource_id,
            resource_types=resource_types,
            actor=actor,
        )
        self._identity.validate(resource_id, str(record.get("resource_type") or ""))
        self._access.authorize_read(record, actor=str(actor or ""))
        return self._with_collaboration_descriptor(record)

    def _with_collaboration_descriptor(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        return self._with_collaboration_descriptors((record,))[0]

    def _with_collaboration_descriptors(
        self,
        records: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        from management_portal.society_service.business_fact_views import project_resources as domain_facts
        return domain_facts(self._collaboration_descriptors(records))

    def _collaboration_descriptors(
        self,
        records: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Enrich discovery rows; the Index applies current facts before filters."""
        items = list(records)
        attributes = self._collaboration.attributes_for_records(
            record
            for record in items
            if record.get("resource_type") in COLLABORATION_RESOURCE_TYPES
        )
        return [
            {
                **record,
                "attributes": attributes[record["resource_id"]],
            }
            if record.get("resource_id") in attributes
            else record
            for record in items
        ]


    def sync_collaboration_resource(
        self,
        descriptor: Mapping[str, Any],
        *, domain_identity: str = "",
    ) -> dict[str, Any]:
        """Idempotently project one committed SocietyService domain object."""

        result = self.sync_collaboration_resources([{
            "descriptor": descriptor,
        }], domain_identity=domain_identity)
        return dict(result["resources"][0])

    @timed
    def sync_collaboration_resources(
        self,
        resources: Iterable[Mapping[str, Any]],
        *, domain_identity: str = "",
    ) -> dict[str, Any]:
        """Validate and commit one collaboration identity bundle.

        The bundle is one ResourceService request. Validation happens before
        the first authoritative write, and derived index notifications are
        emitted only after the descriptors are committed.
        Deleted objects only merge lifecycle fields into their descriptors.
        """

        if domain_identity not in {"", "society-service"}:
            raise PermissionError("Internal domain projection identity required")
        timer = current_timer()
        prepared: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in resources:
            if not isinstance(item, Mapping):
                raise ValueError("collaboration resources must be objects")
            descriptor = item.get("descriptor")
            if set(item) != {"descriptor"} or not isinstance(descriptor, Mapping):
                raise ValueError("collaboration projection requires only a descriptor object")
            clean_descriptor = self._collaboration.normalize(descriptor)
            resource_id = clean_descriptor["resource_id"]
            if resource_id in seen:
                raise ValueError(
                    f"collaboration bundle contains duplicate resource_id: {resource_id}"
                )
            seen.add(resource_id)
            prepared.append(clean_descriptor)
        if not prepared:
            raise ValueError("collaboration bundle must contain at least one resource")
        mark = timer.add("prepare", timer.started)

        results: list[dict[str, Any]] = []
        notifications: list[tuple[str, str]] = []
        from .domain_projection_evidence import (
            EVIDENCE_COLLECTION, EVIDENCE_SCHEMA, SOURCE_COLLECTION,
            projection_fingerprint, tombstone_source, same_descriptor, matches_tombstone_source,
            historical_tombstone_retry,
        )
        from management_portal import permission_system
        from management_portal.society_service.business_fact_views import projection_transaction, live_projection
        with projection_transaction(prepared, self._collaboration._registry) as connection, \
                self._collaboration.projection_scope(connection=connection) as writer:
            # Validate all deletion/Workspace lifecycle events before any live
            # domain metadata. Invalid mixed bundles never partially commit.
            if any(descriptor["resource_id"].startswith("workspace_") for descriptor in prepared):
                raise ValueError("Workspace mutations belong to the Workspace store")
            lifecycle = {
                descriptor["resource_id"]: self._collaboration.tombstone(
                    descriptor, writer, source=tombstone_source(descriptor),
                    trusted_domain=domain_identity == "society-service")
                for descriptor in prepared if descriptor["status"] == "deleted"
            }
            def project_live(descriptor):
                # The domain validates canonical facts and exact retry identity.
                # Reuse its original accepted event only when the format prefix
                # is the sole difference, without rewriting that event.
                accepted = writer.get("domain_live_sources", descriptor["resource_id"])
                event = accepted if same_descriptor(accepted, descriptor) else descriptor
                return live_projection(event, writer, self._collaboration)

            projected = [
                (lifecycle[descriptor["resource_id"]] if descriptor["resource_id"] in lifecycle else
                 project_live(descriptor),
                 descriptor["status"] != "deleted")
                for descriptor in prepared
            ]
            for descriptor, active in projected:
                resource_id = descriptor["resource_id"]
                resource_type = self._identity.parse(resource_id).resource_type
                saved, changed = self._collaboration.put_with_change(descriptor, writer=writer)
                # The object and its place in the permission trees commit together.
                permissions_changed = permission_system.sync_resource(
                    saved if active else {**saved, "status": "deleted"}, resource_type=resource_type,
                    connection=connection)
                if changed or permissions_changed:
                    notifications.append((resource_id, resource_type))
                results.append({"resource_id": resource_id, "descriptor": saved})
            for incoming in prepared:
                if incoming["status"] != "deleted":
                    accepted = writer.get("domain_live_sources", incoming["resource_id"])
                    if not same_descriptor(accepted, incoming):
                        writer.put("domain_live_sources", incoming["resource_id"], incoming)
            # The public RPC cannot supply this identity. Its authenticated
            # dispatcher supplies it after verifying the service assertion.
            # A shape/writer label alone never creates provenance evidence.
            for incoming, result in zip(prepared, results):
                if incoming["status"] != "deleted" or result["descriptor"]["status"] != "deleted":
                    continue
                rid = incoming["resource_id"]
                source = tombstone_source(incoming)
                accepted = writer.get(SOURCE_COLLECTION, rid)
                receipt = writer.get(EVIDENCE_COLLECTION, rid)
                if not (matches_tombstone_source(accepted, incoming)
                        or historical_tombstone_retry(accepted, receipt, incoming)):
                    writer.put(SOURCE_COLLECTION, rid, source)
                if domain_identity:
                    evidence = {
                        "schema": EVIDENCE_SCHEMA, "domain_identity": domain_identity,
                        "resource_id": rid, "incoming_descriptor": incoming,
                        "descriptor_version": projection_fingerprint(result["descriptor"]),
                    }
                    existing = writer.get(EVIDENCE_COLLECTION, rid)
                    # A retry must never replace the first authenticated proof
                    # with later descriptor values. Historical comparison
                    # will reject that receipt if its target/source changed.
                    same_event = (existing is not None
                                  and same_descriptor(existing.get("incoming_descriptor"), incoming))
                    if not same_event:
                        writer.put(EVIDENCE_COLLECTION, rid, evidence)
            mark = timer.add("descriptor", mark)

        for resource_id, resource_type in notifications:
            self._notify_mutation(
                path=self._collaboration.change_key(resource_id),
                resource_id=resource_id,
                resource_type=resource_type,
                kind="updated",
            )
        timer.add("notify", mark)
        timer.report_if_slow(
            "collaboration sync", SLOW_COLLABORATION_SYNC_SECONDS,
            resources=len(prepared), changed=len(notifications),
        )

        return {
            "schema": "tfi.collaboration-sync-result.v1",
            "resources": results,
        }

    def workspace_id_for_path(
        self,
        path: str | Path,
        *,
        preferred_id: str = "",
    ) -> str:
        from .workspace_store import get_workspace_store
        return get_workspace_store().for_path(str(path), preferred_id=preferred_id,
                                             new_id=lambda: self.new_id("workspace"))

    # -- the permission instance (decision_b820faf42218 §3.3) -------------------------

    def permission_facts(self) -> list:
        """Every live object as the permission system's fact adapter reads it; only this service knows them all."""
        from management_portal.permission_system.facts import collect_portal_facts

        return collect_portal_facts(self)

    def prepare_permissions(self, *, actor: str) -> dict[str, Any]:
        """Bring the active permission scheme's instance up to date with every live object (补齐).

        Turning the permission system on asks for this while checks are still
        off, so whatever was made, moved or deleted meanwhile is in the
        instance before checks resume, under the scheme activated while it was
        off.  Rows already in the instance stay as they are.
        """
        self._require_mutation_actor(actor)
        from management_portal.permission_system.par import PARPermissions

        return PARPermissions().prepare_active(self.permission_facts())

    def ensure_permission_instance(self) -> dict[str, Any] | None:
        """At startup: give each built-in scheme's instance the rules a release changed, keeping its edits (§5.11).

        Only built-in instances follow the code here; a custom scheme's
        instance is never generated again at startup
        (``complete_permission_instance`` only brings it up to date).  With
        the permission system off too.  Returns ``{scheme_id: report}`` in the
        shape of ``merge_permission_sources``, or None when nothing changed.
        """
        permissions = _par_permissions()
        stale = permissions.stale_builtin()
        if not stale:
            return None
        facts = self.permission_facts()
        reports = {}
        for scheme_id in stale:
            try:
                report = permissions.follow_release(scheme_id, facts, max_removed=ResourceService.PERMISSION_COMPLETE_MAX_REMOVED)
            except Exception as exc:  # noqa: BLE001 - reported per scheme, the others still follow
                LOGGER.exception("permission scheme %s: following the release failed", scheme_id)
                report = {"error": f"{exc.__class__.__name__}: {exc}"}
            if report is not None:
                reports[scheme_id] = report
        return reports or None

    # An unattended 补齐 that would take out more objects than this read the facts short: it writes nothing.
    PERMISSION_COMPLETE_MAX_REMOVED = 100

    def merge_permission_sources(self) -> dict[str, Any]:
        """At startup, after ``ensure_permission_instance``: merge a release's changes to the built-in rules into their copies (§5.11).

        Runs in the thread that also runs the hourly 补齐, before the first
        one; another process's 补齐 waits for the instance tables and writes
        nothing if a merge generated the instance meanwhile.
        """
        facts: list = []

        def collect() -> list:
            if not facts:
                facts.extend(self.permission_facts())
            return facts

        return _par_permissions().follow_sources(collect, max_removed=ResourceService.PERMISSION_COMPLETE_MAX_REMOVED)

    def complete_permission_instance(self) -> dict[str, Any] | None:
        """At startup and every hour: 补齐 the active scheme's instance from every live object (§5.7).

        Resources written outside a shared transaction sync after commit; this
        repairs any sync that did not run.  It keeps every row, hand-written
        ones included, and never generates.  With the permission system off too.
        """
        return _par_permissions().complete_active(self.permission_facts(),
                                                 max_removed=ResourceService.PERMISSION_COMPLETE_MAX_REMOVED)

    def capabilities(self, resource_type: str) -> dict[str, Any]:
        clean_type = str(resource_type or "").strip()
        if not self._identity.is_registered(clean_type):
            raise ValueError(f"unregistered resource type: {clean_type}")
        self._ensure_resolvers(clean_type)
        self._ensure_mutation_providers(clean_type)
        self._ensure_action_providers(clean_type)
        resolver_types = self._resolvers.registered_types()
        mutation_types = self._mutation_providers.registered_types()
        provider = (
            self._mutation_providers.provider_for(clean_type)
            if clean_type in mutation_types
            else None
        )
        resolver = (
            self._resolvers.resolver_for(clean_type)
            if clean_type in resolver_types
            else None
        )
        try:
            indexed = self._index().supports_type(clean_type)
        except RuntimeError:
            indexed = False
        capabilities = ResourceCapabilities(
            resource_type=clean_type,
            indexed=indexed,
            resolvable=resolver is not None,
            readable=bool(resolver and resolver.read_content is not None),
            registerable=bool(
                self._identity.declaration(clean_type)['registerable']
                and provider and provider.register is not None
            ),
            updatable=bool(provider and provider.update is not None),
            content_updatable=bool(
                provider
                and provider.update is not None
                and provider.content_updatable
            ),
            unregisterable=bool(provider and provider.unregister is not None),
            actions=tuple(sorted(self._action_providers.actions_for(clean_type))),
        ).as_dict()
        return capabilities

    def refresh(self, **kwargs: Any) -> dict[str, Any]:
        return self._index().refresh(**kwargs)

    def warm(self) -> None:
        """Warm the discovery index after startup."""
        try:
            index = self._index()
        except RuntimeError:
            # A service built without an index (test servers, tools) has
            # nothing more to load.
            return
        deadline = time.monotonic() + 360
        while True:
            try:
                index.query(actor="", limit=1)
                return
            except ResourceServiceUnavailable:
                if time.monotonic() >= deadline:
                    return
                time.sleep(2)

    def status(self, *, timeout: float | None = None) -> dict[str, Any]:
        del timeout
        return self._index().status()

    def projection_state(self, adapter: str) -> dict[str, Any]:
        return self._index().projection_state(str(adapter or "").strip())

    def _route_change(self, item: ResourceChange) -> list[str]:
        mark = time.perf_counter()
        try:
            return self._route_change_untimed(item)
        finally:
            lap("notify.route", mark)

    def _route_change_untimed(self, item: ResourceChange) -> list[str]:
        mark = time.perf_counter()
        index = self._index()
        detected_type = item.resource_type
        if item.resource_id and detected_type:
            self._identity.validate(item.resource_id, detected_type)
        elif item.resource_id:
            detected_type = self._identity.parse(item.resource_id).resource_type
        if detected_type:
            if not self._identity.is_registered(detected_type):
                raise ValueError(f"unregistered resource type: {detected_type}")
        lap("notify.route.identity", mark)
        return index.change_targets(
            path=item.path,
            resource_id=item.resource_id,
            resource_type=detected_type,
        )

    def _enqueue_change(
        self,
        targets: tuple[str, ...],
        item: ResourceChange,
    ) -> list[str]:
        mark = time.perf_counter()
        try:
            return self._index().queue_change(
                targets,
                path=item.path,
            )
        finally:
            lap("notify.enqueue", mark)

    def notify_source_changed(
        self,
        *,
        path: str = "",
        resource_id: str = "",
        resource_type: str = "",
        kind: str = "unknown",
        wait: bool = False,
        timeout: float = 90.0,
    ) -> dict[str, object]:
        change = ResourceChange(
            kind=kind,
            path=path,
            resource_id=resource_id,
            resource_type=resource_type,
        )
        notification = self._changes.notify(change)
        result = notification.as_dict()
        if not wait:
            return result
        finished = (
            self._index().wait_for_refresh(
                notification.targets,
                timeout=max(0.0, float(timeout)),
            )
            if notification.targets
            else True
        )
        return {
            **result,
            "waited": True,
            "finished": bool(finished),
        }
