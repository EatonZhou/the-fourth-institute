"""One lazy identity resolution path for embedded discussions.

Callers authorize the target resource and supply its context. Reading never
allocates or configures an identity; prepare is used only after a nonempty send.
Existing scope strings remain stable so histories keep their original Resident.
"""
from . import resident_handoff
from .application_bindings import resolve_application_lineage
from .resource_lineages import resolve_resource_lineage



def resolve(*, actor: str, surface: str, resident_scope: str,
            resource_type: str = "", create: bool = False, **options) -> dict:
    from .workbench_projections import native
    from .workbench_projections.store import builtins

    if create:
        metadata = dict(options.get('metadata') or {})
        resource_id = metadata.get('resource_id') or metadata.get(f'{surface}_id')
        if resource_id:
            metadata['resource_id'] = resource_id
            options['metadata'] = metadata
    if surface in builtins():
        if not create:
            return native.lookup(surface, actor=actor, resident_scope=resident_scope)
        return native.ensure(surface, actor=actor, resident_scope=resident_scope,
                             create_role=True, **options)
    lineage_id = (resolve_resource_lineage(actor=actor, resource_type=resource_type)
                  if surface == "resource" else
                  resolve_application_lineage(actor=actor, application="project-chat")
                  if surface == "project" else "")
    if not lineage_id:
        raise ValueError(f"Unsupported discussion surface: {surface!r}")
    if not create:
        return resident_handoff.lookup_invocation_resident(
            actor=actor, resident_scope=resident_scope, lineage_binding=lineage_id)
    return resident_handoff.ensure_invocation_resident(
        actor=actor, resident_scope=resident_scope, lineage_binding=lineage_id, **options)
