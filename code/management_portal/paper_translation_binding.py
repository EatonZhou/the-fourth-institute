"""Paper translation management through the existing Workbench binding owner."""
from management_portal.workbench_projections.service import get_service

ROLE = 'paper:translation'


def inspect_binding(actor):
    app = get_service().builtin_application('paper', actor)
    main = app.get('bindings', {}).get('main') or {}
    return {'workbench_id': app['workbench_id'], 'revision': app['revision'],
            'published_revision': app.get('published_revision'),
            'lineage_id': main.get('lineage_id'),
            'resident_id': (main.get('residents') or {}).get(ROLE)}
