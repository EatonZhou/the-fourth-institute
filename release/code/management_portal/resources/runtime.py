from __future__ import annotations

import threading

from management_portal.portal_access_control import get_portal_access_store

from .config import resource_service_mode
from .providers import (
    install_action_providers,
    install_mutation_providers,
    install_resolvers,
)
from .registration import ResourceRegistrationStore
from .service import ResourceService


_SERVICE_LOCK = threading.Lock()
_SERVICE: object | None = None


def create_local_resource_service() -> ResourceService:
    from .providers import provider_plugins
    provider_plugins()  # Deployed declarations must be importable before accepting work.
    return ResourceService(
        index_provider=_portal_index,
        resolver_loader=install_resolvers,
        mutation_provider_loader=install_mutation_providers,
        action_provider_loader=install_action_providers,
        portal_access=get_portal_access_store(),
        registration_store=ResourceRegistrationStore(),
    )


def get_resource_service():
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                mode = resource_service_mode()
                if mode == "client":
                    from .transport import ResourceServiceClient

                    _SERVICE = ResourceServiceClient()
                elif mode in {"local", "server"}:
                    _SERVICE = create_local_resource_service()
                else:
                    raise RuntimeError(
                        f"unsupported ResourceService mode: {mode}"
                    )
    return _SERVICE


def _portal_index():
    from management_portal import resource_index_backend

    return resource_index_backend.get_service()
