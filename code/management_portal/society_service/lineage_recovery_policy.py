"""Permanent recovery failures require a new, explicitly authorized Event."""
from management_common.principal import OperationIdentity


def permanent_error(event: dict) -> str:
    identity = event.get("identity")
    if not identity:
        return "Pending Event has no verified identity; explicit reissue is required"
    try:
        principal = OperationIdentity.from_dict(identity).principal
    except (KeyError, TypeError, ValueError) as exc:
        return f"Pending Event identity is invalid; explicit reissue is required: {exc}"[:500]
    if principal.kind == "resident" and deleted_resident(principal.id):
        return f"Pending Event identity {principal.id} is a deleted Resident; explicit reissue is required"
    return ""


def deleted_resident(resident_id: str) -> bool:
    """Whether the Resident was registered and has since been deleted."""
    from .resident_catalog import get_catalog

    try:
        record = get_catalog().get(resident_id, include_deleted=True)
    except KeyError:
        return False
    return bool(str(record.get("deleted_at") or "").strip())
