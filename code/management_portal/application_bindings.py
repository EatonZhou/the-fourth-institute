"""Application references to ordinary Lineages; never allocate a team."""
from .society_service import get_society_service


def main_lineage(app: dict) -> str:
    from .workbench_projections.store import ProjectionError
    target = app.get("bindings", {}).get("main", {}).get("lineage_id")
    if not target:
        raise ProjectionError("projection.lineage_unbound", "功能尚未绑定主 Lineage", 409)
    return target


def projection_application(name: str, actor: str) -> dict:
    from .workbench_projections.service import get_service
    return get_service().initialize(name, actor)


def resolve_application_lineage(*, actor: str, application: str) -> str:
    """The ordinary Lineage an application works in.

    Built-in workbench applications use their bound main Lineage; every other
    application, such as ``tau``, has its own application configuration.
    Resolving gives no authority over the target and never creates anything.
    """
    from . import application_targets
    from .workbench_projections.store import builtins

    if application in builtins():
        return main_lineage(projection_application(application, actor))
    if application.startswith('resource-chat:'):
        lineage_id = application_targets.resource_chat_lineage(application.split(':',1)[1])
    else:
        try:
            lineage_id = application_targets.lineage(application)
        except ValueError:
            raise ValueError('Unknown Portal application: ' + application) from None
    get_society_service().lineage.get_lineage(lineage_id, actor)
    return lineage_id
