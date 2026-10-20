from __future__ import annotations

from . import execution_client, project_domain, resource_client
from .domain_ports import bind_domain_ports
from .resident_domain_host import ResidentDomainHost
from .resident_lineage_host import ResidentLineageHost


def configure_default_domain_ports() -> None:
    bind_domain_ports(
        resource=resource_client,
        execution=execution_client,
        project=project_domain,
        resident=ResidentDomainHost(),
        resident_lineage=ResidentLineageHost(),
    )
