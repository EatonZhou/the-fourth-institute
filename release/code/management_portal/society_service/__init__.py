from __future__ import annotations

from typing import Any

from .config import society_service_mode, society_service_socket
from .transport import (
    LineageConflictError,
    SocietyServiceBusy,
    SocietyServiceClient,
    SocietyServiceUnavailable,
)


_CLIENT: SocietyServiceClient | None = None


def get_society_service() -> Any:
    global _CLIENT
    if society_service_mode() == "local":
        from .server import LocalSocietyService

        return LocalSocietyService()
    if _CLIENT is None:
        _CLIENT = SocietyServiceClient(society_service_socket())
    return _CLIENT


__all__ = [
    "LineageConflictError",
    "SocietyServiceBusy",
    "SocietyServiceClient",
    "SocietyServiceUnavailable",
    "get_society_service",
]
