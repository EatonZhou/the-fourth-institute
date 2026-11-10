"""Paper translation management through the existing Workbench binding owner."""
from management_portal.workbench_projections import native
from management_portal.workbench_projections.service import get_service
from management_portal.workbench_projections.store import ProjectionError

ROLE = 'paper:translation'
SETTINGS = {'model': 'kimi-code/kimi-for-coding', 'account_selection': 'fixed',
            'auto_switch_on_quota': False, 'allow_subagents': False}


def inspect_binding(actor):
    app = get_service().builtin_application('paper', actor)
    main = app.get('bindings', {}).get('main') or {}
    return {'workbench_id': app['workbench_id'], 'revision': app['revision'],
            'published_revision': app.get('published_revision'),
            'lineage_id': main.get('lineage_id'),
            'resident_id': (main.get('residents') or {}).get(ROLE)}


def ensure_binding(actor):
    before = inspect_binding(actor)
    if not before['lineage_id'] or before['revision'] != before['published_revision']:
        raise ProjectionError('projection.binding_draft', 'Paper requires a published main Lineage binding', 409)
    # require_role owns locking, durable creation receipts, membership and
    # publication. No direct SQL or writes to the binding JSON occur here.
    identity = native.binding('paper', {'actor': actor, 'resident_scope': ROLE,
        'title': 'Paper选中文本翻译', 'backend_route_id': 'kimi-code',
        'runtime_settings': dict(SETTINGS)}, 'kimi', create_role=True)
    from management_portal.society_service.resident_execution_domain import text_only_input_snapshot
    current = text_only_input_snapshot(identity['resident_id'], actor, text='configuration check')
    account = current['account']
    route = account.get('backend_route_id') or account.get('route_id') or account.get('account_id')
    if current['backend'] != 'kimi' or route != 'kimi-code' or any(current['settings'].get(k) != v for k, v in SETTINGS.items()):
        raise ProjectionError('projection.translation_configuration', 'Existing translation binding requires explicit Kimi subscription configuration; no fallback or replacement was performed', 409)
    return {**inspect_binding(actor), 'settings_revision': current['settings_revision'],
            'provider': 'kimi', 'account_id': 'kimi-code', 'model': SETTINGS['model']}
