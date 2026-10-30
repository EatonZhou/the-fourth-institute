"""The domain service clients Portal Web calls: SocietyService and ExecutionService."""
from __future__ import annotations

from management_portal.execution import get_execution_service
from management_portal.society_service import get_society_service


_SOCIETY_SERVICE = get_society_service()
resident_api = _SOCIETY_SERVICE.resident
workspace_api = _SOCIETY_SERVICE.workspace
lineage_domain = _SOCIETY_SERVICE.lineage
execution_api = get_execution_service()
