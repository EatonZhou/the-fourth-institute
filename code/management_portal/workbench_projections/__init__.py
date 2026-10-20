"""Keep storage and resource providers independent of service orchestration."""

__all__ = ["get_service"]


def __getattr__(name: str):
    # Resource warm-up and first reads can import sibling modules concurrently.
    # Loading Service here eagerly makes it wait on a partially imported Store,
    # while Store's sibling imports wait on this package's initialization lock.
    if name == "get_service":
        from .service import get_service

        return get_service
    raise AttributeError(name)
