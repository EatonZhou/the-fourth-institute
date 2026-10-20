"""Lineage-owned RunScript instruction adapters."""
from ..script_runs import service


def start(inv):
    return service.start(inv.arguments, inv.caller, idempotency_key=inv.call.idempotency_key or inv.instruction_id,
                         trigger_path_id=inv.context.trigger_path_id)


HANDLERS = {
    'RunScript': start,
    'GetScriptRun': lambda inv: service.get(inv.argument('script_run_id'), inv.caller, lineage_id=inv.argument('lineage_id')),
    'ListScriptRuns': lambda inv: service.list_runs(inv.arguments, inv.caller),
    'ReadScriptRunOutput': lambda inv: service.read_output(inv.arguments, inv.caller),
    'CancelScriptRun': lambda inv: service.cancel(inv.argument('script_run_id'), inv.caller, lineage_id=inv.argument('lineage_id')),
}
