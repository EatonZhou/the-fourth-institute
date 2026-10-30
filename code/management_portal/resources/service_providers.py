"""ResourceService providers: registering resolvers and mutation and action providers,
loading them on first use, and the capabilities they give a resource type."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Iterable

from .address import ResourceResolver
from .contracts import ResourceCapabilities
from .mutation import ResourceMutationProvider

if TYPE_CHECKING:
    from .service import ResourceService


def ensure_provider(service: "ResourceService", capability: str, resource_type: str | None) -> None:
    if resource_type is not None:
        service.resource_types.require(resource_type)
    registry, loader, lock, flag = {
        "resolver": (service._resolvers, service._resolver_loader, service._resolver_lock, "_resolvers_loaded"),
        "mutation": (service._mutation_providers, service._mutation_provider_loader, service._mutation_provider_lock, "_mutation_providers_loaded"),
        "action": (service._action_providers, service._action_provider_loader, service._action_provider_lock, "_action_providers_loaded"),
    }[capability]
    with lock:
        loaded = service._loaded_provider_types[capability]
        if getattr(service, flag) or resource_type in loaded:
            return
        selective = getattr(loader, "for_type", None)
        with registry.transaction():
            if resource_type is not None and callable(selective):
                owned_types = selective(service, resource_type)
            else:
                if loader is not None:
                    loader(service)
                owned_types = None
        # Failed imports/registrations leave no partial entries or success marker.
        if owned_types is None:
            setattr(service, flag, True)
        else:
            loaded.update(owned_types)


def register_resolver(
    service: "ResourceService",
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
        if not service._identity.is_registered(clean_type):
            raise ValueError(f"unregistered resource type: {clean_type}")
    service._resolvers.register(
        values,
        ResourceResolver(
            resolve=resolve,
            read_content=read_content,
            read_content_range=read_content_range,
        ),
        replace=replace,
    )


def register_index_backed_resolver(
    service: "ResourceService",
    resolve: Any,
    *,
    read_content: Any = None,
) -> None:
    raw_types = service._index().indexed_types()
    if not isinstance(raw_types, (set, frozenset, list, tuple)):
        raise TypeError("resource index indexed_types() must return a collection")
    from .providers import resolver_domain_types

    indexed_types = frozenset(raw_types)
    resource_types = (
        indexed_types
        & service._identity.types
        - service._resolvers.registered_types()
        - (resolver_domain_types() if callable(getattr(service._resolver_loader, "for_type", None)) else frozenset())
    )
    if resource_types:
        service.register_resolver(
            sorted(resource_types),
            resolve,
            read_content=read_content,
        )


def register_mutation_provider(
    service: "ResourceService",
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
        if not service._identity.is_registered(clean_type):
            raise ValueError(f"unregistered resource type: {clean_type}")
    service._mutation_providers.register(
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


def register_action_provider(
    service: "ResourceService",
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
        if not service._identity.is_registered(clean_type):
            raise ValueError(f"unregistered resource type: {clean_type}")
    service._action_providers.register(
        values,
        actions,
        replace=replace,
    )


def capabilities(service: "ResourceService", resource_type: str) -> dict[str, Any]:
    clean_type = str(resource_type or "").strip()
    if not service._identity.is_registered(clean_type):
        raise ValueError(f"unregistered resource type: {clean_type}")
    service._ensure_resolvers(clean_type)
    service._ensure_mutation_providers(clean_type)
    service._ensure_action_providers(clean_type)
    resolver_types = service._resolvers.registered_types()
    mutation_types = service._mutation_providers.registered_types()
    provider = (
        service._mutation_providers.provider_for(clean_type)
        if clean_type in mutation_types
        else None
    )
    resolver = (
        service._resolvers.resolver_for(clean_type)
        if clean_type in resolver_types
        else None
    )
    try:
        indexed = service._index().supports_type(clean_type)
    except RuntimeError:
        indexed = False
    capabilities = ResourceCapabilities(
        resource_type=clean_type,
        indexed=indexed,
        resolvable=resolver is not None,
        readable=bool(resolver and resolver.read_content is not None),
        registerable=bool(
            service._identity.declaration(clean_type)['registerable']
            and provider and provider.register is not None
        ),
        updatable=bool(provider and provider.update is not None),
        content_updatable=bool(
            provider
            and provider.update is not None
            and provider.content_updatable
        ),
        unregisterable=bool(provider and provider.unregister is not None),
        actions=tuple(sorted(service._action_providers.actions_for(clean_type))),
    ).as_dict()
    return capabilities
