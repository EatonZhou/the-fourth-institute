"""WALL owns the lifetime of its item and project execution identities."""
from pathlib import Path

from .wall_documents import digest, index_sources, parse_points, read_source
from .workbench_projections.service import get_service


def release(record: dict, actor: str, state: dict, *, blocks: set[str] | None) -> set[str]:
    service = get_service()
    app = service.store.builtin('wall', actor)
    if app is None:
        return set()  # A project with no AI binding has no allocated slots.
    key = record['id']
    prefixes = (f'wall-work:{key}:', f'wall-discussion:{key}:', f'wall:{key}:')
    roles = {role: rid for binding in app['bindings'].values()
             for role, rid in binding.get('residents', {}).items() if role.startswith(prefixes)}
    retain = set()
    if blocks is not None:
        from .wall_backend import PROJECT_ACTIONS
        fixed = {"regression", *(digest(action) for action in PROJECT_ACTIONS)}
        retain.update(rid for role, rid in roles.items() if role.split(':')[2] in fixed)
        hashes = {digest(block) for block in blocks}
        # Read all sources without the display cache's permission/error filtering:
        # an unreadable source cannot prove a Resident is unreferenced.
        points = [p for source in index_sources(Path(record['root']))
                  for p in parse_points(source, read_source(Path(record['root']), source.address, actor))]
        live = [p for p in points if p['id'] not in blocks]
        live_ids = {p['id'] for p in live}
        retain.update(p['resident_id'] for p in live if p.get('resident_id'))
        jobs = [j for j in state['jobs'].values() if j['project'] == key]
        for job in jobs:
            if job.get('block') in live_ids or live_ids.intersection(job.get('result_block_ids') or []):
                retain.add(job.get('resident_id', ''))
        # A Dev card can reference the executor of an earlier, removed Plan.
        # Include that executor when the last card naming it is removed.
        removed_residents = {j.get('resident_id') for j in jobs
                             if j.get('block') in blocks or blocks.intersection(j.get('result_block_ids') or [])}
        removed_residents.update(rid for entry in state.get('resident_cleanup', {}).values()
                                 if entry['project'] == key and entry['block'] in blocks
                                 for rid in entry.get('resident_ids', []))
        roles = {role: rid for role, rid in roles.items()
                 if role.split(':')[2] in hashes or rid in removed_residents}
    return service.release_roles(app, actor, roles, retain=retain)
