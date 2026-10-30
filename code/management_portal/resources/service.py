from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Iterable

from management_common.resource_identity import (
    ResourceTypeRegistry,
    get_resource_type_registry,
)
from management_portal.portal_access_control import (
    PortalAccessStore,
)

from .access import ResourceAccessPolicy
from .action import ActionProviderRegistry
from .address import AddressParser, ResourceAddress, ResolverRegistry
from .changes import ResourceChange, ResourceChangeDispatcher
from .collaboration_registry import RESOURCE_TYPES as COLLABORATION_RESOURCE_TYPES
from .errors import ResourceServiceUnavailable
from .index import ResourceIndex
from .mutation import MutationProviderRegistry
from .registration import ResourceRegistrationStore
from . import (
    service_collaboration, service_index, service_mutations, service_permissions, service_providers,
    service_reads,
)


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
        service_providers.ensure_provider(self, capability, resource_type)

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
        service_providers.register_resolver(
            self, resource_types, resolve, read_content=read_content, read_content_range=read_content_range,
            replace=replace,
        )

    def register_index_backed_resolver(
        self,
        resolve: Any,
        *,
        read_content: Any = None,
    ) -> None:
        """Bind indexed types that do not already have a domain resolver."""
        service_providers.register_index_backed_resolver(self, resolve, read_content=read_content)

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
        service_providers.register_mutation_provider(
            self, resource_types, register=register, unregister=unregister, update=update,
            content_updatable=content_updatable, service_managed_registration=service_managed_registration,
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
        service_providers.register_action_provider(self, resource_types, actions, replace=replace)

    @staticmethod
    def _mutation_record(
        result: Mapping[str, Any],
        *,
        resource_type: str,
        expected_id: str = "",
    ) -> tuple[dict[str, Any], str]:
        return service_mutations.mutation_record(
            result, resource_type=resource_type, expected_id=expected_id,
        )

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
        return service_mutations.complete_mutation(
            self, result, resource_type=resource_type, expected_id=expected_id, actor=actor, kind=kind,
            wait_for_index=wait_for_index, index_timeout=index_timeout, change_recording=change_recording,
        )

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
        return service_mutations.notify_mutation(
            self, path=path, resource_id=resource_id, resource_type=resource_type, kind=kind,
            wait_for_index=wait_for_index, index_timeout=index_timeout,
        )

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
        return service_mutations.register(
            self, resource_type, payload, actor=actor, automatic=automatic, wait_for_index=wait_for_index,
            index_timeout=index_timeout,
        )

    def _existing_identity(
        self,
        resource_id: str,
        resource_type: str = "",
        *,
        actor: str = "",
    ):
        return service_mutations.existing_identity(self, resource_id, resource_type, actor=actor)

    @staticmethod
    def _require_mutation_actor(actor: str) -> None:
        """Resource changes need an authenticated User or Resident; permissions
        belong to the permission system (decision_b820faf42218)."""
        service_mutations.require_mutation_actor(actor)

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
        return service_mutations.update(
            self, resource_id, payload, actor=actor, resource_type=resource_type,
            wait_for_index=wait_for_index, index_timeout=index_timeout,
        )

    def execution_content_changes(self, execution_id: str, *, actor: str) -> list[dict[str, Any]]:
        return service_mutations.execution_content_changes(self, execution_id, actor=actor)

    def unregister(
        self,
        resource_id: str,
        *,
        actor: str = "",
        resource_type: str = "",
        wait_for_index: bool = False,
        index_timeout: float = 45.0,
    ) -> dict[str, Any]:
        return service_mutations.unregister(
            self, resource_id, actor=actor, resource_type=resource_type, wait_for_index=wait_for_index,
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
        return service_mutations.action(
            self, resource_id, action, payload, actor=actor, resource_type=resource_type,
        )

    def resolve(
        self,
        address: str,
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        return service_reads.resolve(self, address, actor=actor)

    def read_content(
        self,
        address: str,
        *,
        actor: str = "",
        max_bytes: int = 4 * 1024 * 1024,
    ) -> tuple[bytes, str, dict[str, Any]]:
        return service_reads.read_content(self, address, actor=actor, max_bytes=max_bytes)

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
        return service_reads.open_content(self, address, actor=actor, max_bytes=max_bytes)

    def read_content_range(
        self,
        address: str,
        *,
        start: int,
        end: int,
        actor: str = "",
    ) -> tuple[bytes, str, dict[str, Any]]:
        return service_reads.read_content_range(self, address, start=start, end=end, actor=actor)

    def reference(
        self,
        address: str,
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        """Resolve an address and return a small, stable cross-surface reference."""
        return service_reads.reference(self, address, actor=actor)

    def query(self, **kwargs: Any) -> dict[str, Any]:
        return service_index.query(self, **kwargs)

    def project_changes(self, project_ids: Iterable[str], *, actor: str = "") -> dict[str, str]:
        """Each Project's latest change among the resources the actor may read (ListProjects)."""
        return self._index().project_changes(
            project_ids,
            actor=actor,
            records_projector=self._collaboration_descriptors,
            records_projector_types=COLLABORATION_RESOURCE_TYPES,
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
        return service_index.get(self, resource_id, actor=actor)

    def _with_collaboration_descriptor(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        return self._with_collaboration_descriptors((record,))[0]

    def _with_collaboration_descriptors(
        self,
        records: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return service_index.with_collaboration_descriptors(self, records)

    def _collaboration_descriptors(
        self,
        records: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Enrich discovery rows; the Index applies current facts before filters."""
        return service_index.collaboration_descriptors(self, records)


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
        return service_collaboration.sync_collaboration_resources(
            self, resources, domain_identity=domain_identity,
        )

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
        return service_permissions.permission_facts(self)

    def prepare_permissions(self, *, actor: str) -> dict[str, Any]:
        """Bring the active permission scheme's instance up to date with every live object (补齐).

        Turning the permission system on asks for this while checks are still
        off, so whatever was made, moved or deleted meanwhile is in the
        instance before checks resume, under the scheme activated while it was
        off.  Rows already in the instance stay as they are.
        """
        return service_permissions.prepare_permissions(self, actor=actor)

    def ensure_permission_instance(self) -> dict[str, Any] | None:
        """At startup: give each built-in scheme's instance the rules a release changed, keeping its edits (§5.11).

        Only built-in instances follow the code here; a custom scheme's
        instance is never generated again at startup
        (``complete_permission_instance`` only brings it up to date).  With
        the permission system off too.  Returns ``{scheme_id: report}`` in the
        shape of ``merge_permission_sources``, or None when nothing changed.
        """
        return service_permissions.ensure_permission_instance(
            self, max_removed=ResourceService.PERMISSION_COMPLETE_MAX_REMOVED,
        )

    # An unattended 补齐 that would take out more objects than this read the facts short: it writes nothing.
    PERMISSION_COMPLETE_MAX_REMOVED = 100

    def merge_permission_sources(self) -> dict[str, Any]:
        """At startup, after ``ensure_permission_instance``: merge a release's changes to the built-in rules into their copies (§5.11).

        Runs in the thread that also runs the hourly 补齐, before the first
        one; another process's 补齐 waits for the instance tables and writes
        nothing if a merge generated the instance meanwhile.
        """
        return service_permissions.merge_permission_sources(
            self, max_removed=ResourceService.PERMISSION_COMPLETE_MAX_REMOVED,
        )

    def complete_permission_instance(self) -> dict[str, Any] | None:
        """At startup and every hour: 补齐 the active scheme's instance from every live object (§5.7).

        Resources written outside a shared transaction sync after commit; this
        repairs any sync that did not run.  It keeps every row, hand-written
        ones included, and never generates.  With the permission system off too.
        """
        return service_permissions.complete_permission_instance(
            self, max_removed=ResourceService.PERMISSION_COMPLETE_MAX_REMOVED,
        )

    def capabilities(self, resource_type: str) -> dict[str, Any]:
        return service_providers.capabilities(self, resource_type)

    def refresh(self, **kwargs: Any) -> dict[str, Any]:
        return self._index().refresh(**kwargs)

    def warm(self) -> None:
        """Warm the discovery index after startup."""
        service_index.warm(self)

    def status(self, *, timeout: float | None = None) -> dict[str, Any]:
        del timeout
        return self._index().status()

    def projection_state(self, adapter: str) -> dict[str, Any]:
        return self._index().projection_state(str(adapter or "").strip())

    def _route_change(self, item: ResourceChange) -> list[str]:
        return service_index.route_change(self, item)

    def _route_change_untimed(self, item: ResourceChange) -> list[str]:
        return service_index.route_change_untimed(self, item)

    def _enqueue_change(
        self,
        targets: tuple[str, ...],
        item: ResourceChange,
    ) -> list[str]:
        return service_index.enqueue_change(self, targets, item)

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
        return service_index.notify_source_changed(
            self, path=path, resource_id=resource_id, resource_type=resource_type, kind=kind, wait=wait,
            timeout=timeout,
        )
