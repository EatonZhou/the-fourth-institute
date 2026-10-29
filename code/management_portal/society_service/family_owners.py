"""Human contacts for Family work; independent of permissions."""
from __future__ import annotations

from typing import Any, Mapping

from management_common.principal import actor_principal


def recipients(record: Mapping[str, Any], *, message: Mapping[str, Any] | None = None,
               creator_only: bool = False) -> list[str]:
    """Use this work's human initiator, otherwise its actual human familizer."""
    from .resident_work_origin import human_initiator
    from .state_store import get_lineage_data_store
    human = human_initiator(str(record["resident_id"]), message=message)
    if human and not creator_only:
        return [human]
    family_id = ((record or {}).get("family") or {}).get("family_id")
    if not family_id:
        return []
    with get_lineage_data_store().database.read() as connection:
        family = connection.execute("SELECT human_familizer_id FROM families WHERE family_id=%s AND deleted_at IS NULL", (family_id,)).fetchone()
    human = str(family['human_familizer_id']) if family else ''
    return [human] if human else []


def register_familizer(facade_id: str, actor: str) -> None:
    """Save the actual human Familize caller as the Family's contact."""
    if actor_principal(actor).kind != "user":
        return
    from .state_store import get_lineage_data_store
    def save(connection):
        row = connection.execute("""SELECT f.family_id,f.human_familizer_id FROM families f
            JOIN family_residents fr USING(family_id) WHERE fr.resident_id=%s AND fr.role='facade'
            AND f.deleted_at IS NULL FOR UPDATE OF f""", (facade_id,)).fetchone()
        if not row:
            raise ValueError('Familize contact requires a live Family facade')
        if row['human_familizer_id'] and row['human_familizer_id'] != actor:
            raise ValueError('Family already has a different human familizer')
        connection.execute('UPDATE families SET human_familizer_id=%s WHERE family_id=%s', (actor, row['family_id']))
    get_lineage_data_store().database.write(save)
