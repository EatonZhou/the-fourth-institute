"""Paper translation management through the existing Workbench binding owner."""
from management_portal.workbench_projections.service import get_service
from management_portal.workbench_projections.store import ProjectionError

ROLE = 'paper:translation'


def inspect_binding(actor):
    app = get_service().builtin_application('paper', actor)
    main = app.get('bindings', {}).get('main') or {}
    return {'workbench_id': app['workbench_id'], 'revision': app['revision'],
            'published_revision': app.get('published_revision'),
            'lineage_id': main.get('lineage_id'),
            'resident_id': (main.get('residents') or {}).get(ROLE)}


def ensure_binding(actor):
    raise ProjectionError('projection.translation_cancelled', 'Kimi subscription translation experiment is permanently cancelled', 410)
