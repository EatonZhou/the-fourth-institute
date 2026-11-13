"""Application-declared direct-chat policy over existing workflow registrations.

No Resident setting or message metadata can grant a workflow exemption.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from management_portal import portal_features
from management_portal.workflow_bindings_store import get_workflow_binding_store

_bindings = ContextVar('workflow_delivery_bindings', default=None)


def _read(resident_ids):
    ids = list(resident_ids)
    rows = get_workflow_binding_store().delivery_bindings(ids)
    for _, targets in portal_features.contributions('workflow.targets'):
        rows.extend(targets(ids))
    return rows


@contextmanager
def projection_scope(resident_ids):
    token = _bindings.set(_read(resident_ids))
    try:
        yield
    finally:
        _bindings.reset(token)


def policies(resident_id):
    if not resident_id:
        return []
    rows = _bindings.get()
    if rows is None:
        rows = _read([resident_id])
    result = []
    for row in rows:
        if row['resident_id'] != resident_id:
            continue
        name = row['name']
        definition = portal_features.contribution('workbench.workflows.' + row.get('application', name.split(':', 1)[0]))
        for declared in definition(name) if definition else []:
            if declared.get('name') == name and declared.get('direct_chat_disabled_reason'):
                result.append((row, declared['direct_chat_disabled_reason']))
    return result


def disabled_reason(resident_id):
    return next((reason for _, reason in policies(resident_id)), '')


def require_delivery(resident_id, actor, context=None):
    from management_common.principal import actor_principal
    if actor_principal(actor).kind != 'user':
        return
    declared = policies(resident_id)
    if not declared:
        return
    if context and context.entry == 'lineage' and context.recovery_key and context.trigger_path_id:
        from management_portal.society_service import lineage_effect_script_journal
        for binding, _ in declared:
            if not binding.get('effect_id'):
                continue
            journal = lineage_effect_script_journal.load(binding['lineage_id'], context.recovery_key)
            if (journal.get('effect_id') == binding['effect_id']
                    and journal.get('trigger_path_id') == context.trigger_path_id
                    and journal.get('status') == 'running'):
                return
    raise PermissionError(declared[0][1])
