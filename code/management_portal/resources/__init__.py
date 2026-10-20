from .errors import GitCheckoutRequired, ResourceOperationError, ResourceVersionConflict


__all__ = [
    "ResourceService",
    "GitCheckoutRequired",
    "ResourceOperationError",
    "ResourceVersionConflict",
    "get_resource_service",
]


def __getattr__(name: str):
    # RPC clients and health probes do not load the service's database owners.
    if name == "get_resource_service":
        from .runtime import get_resource_service

        return get_resource_service
    if name == "ResourceService":
        from .service import ResourceService

        return ResourceService
    raise AttributeError(name)
