from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

from management_common.resource_identity import get_resource_type_registry

if TYPE_CHECKING:
    from ..service import ResourceService


@dataclass(frozen=True)
class ProviderPlugin:
    name: str
    ownership: dict[str, frozenset[str]]

    def install(self, capability: str, service: "ResourceService") -> None:
        module = import_module(f"{__name__}.{self.name}")
        method = {"resolver": "install_resolver", "mutation": "install_mutation_provider",
                  "action": "install_action_provider"}[capability]
        installer = getattr(module, method, None)
        if not callable(installer):
            raise TypeError(f"resource provider {self.name!r} declares {capability!r} without a callable installer")
        installer(service)


def provider_plugins(resource_type: str = "") -> tuple[ProviderPlugin, ...]:
    from ..resource_types import deployed_units

    declarations = get_resource_type_registry().declarations
    units = deployed_units()
    selected = str(declarations.get(resource_type, {}).get('provider') or '').partition(':')[0]
    groups: dict[str, dict[str, set[str]]] = {}
    for kind, declaration in declarations.items():
        if resource_type and str(declaration['provider']).partition(':')[0] != selected:
            continue
        if not declaration['provider'] or declaration['unit'] not in units:
            continue
        module_name, attribute = declaration['provider'].split(':')
        module = import_module(module_name)
        if not callable(getattr(module, attribute)):
            raise TypeError(f'{kind}: provider attribute must be callable')
        ownership = groups.setdefault(module_name.removeprefix(__name__ + '.'), {})
        for capability, installer in {'resolver': 'install_resolver', 'mutation': 'install_mutation_provider',
                                      'action': 'install_action_provider'}.items():
            if callable(getattr(module, installer, None)):
                ownership.setdefault(capability, set()).add(kind)
    return tuple(ProviderPlugin(name, {key: frozenset(values) for key, values in owners.items()})
                 for name, owners in groups.items())


def resolver_domain_types() -> frozenset[str]:
    return frozenset(value for plugin in provider_plugins() for value in plugin.ownership.get("resolver", ()))


class ProviderLoader:
    """Load only the owner of a requested type; service owns transactions/caching."""

    def __init__(self, capability: str):
        self.capability = capability

    def for_type(self, service: "ResourceService", resource_type: str) -> frozenset[str]:
        matches = [plugin for plugin in provider_plugins(resource_type)
                   if resource_type in plugin.ownership.get(self.capability, ())]
        if len(matches) > 1:
            raise ValueError(f"multiple {self.capability} providers own {resource_type}")
        if matches:
            plugin = matches[0]
            plugin.install(self.capability, service)
            return plugin.ownership[self.capability]
        if self.capability == "resolver":
            ProviderPlugin("indexed", {"resolver": frozenset()}).install(self.capability, service)
        return frozenset({resource_type})

    def __call__(self, service: "ResourceService") -> None:
        # Explicit full installation is reserved for validation/inspection.
        for plugin in provider_plugins():
            if self.capability in plugin.ownership:
                plugin.install(self.capability, service)


install_resolvers = ProviderLoader("resolver")
install_mutation_providers = ProviderLoader("mutation")
install_action_providers = ProviderLoader("action")
