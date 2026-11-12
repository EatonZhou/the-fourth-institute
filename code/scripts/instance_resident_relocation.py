"""Reconcile legacy Resident folders with their authoritative Lineage membership.

Used by migrate_instance_lineages after its area/Lineage prefix moves. Identity
and bindings are unchanged; RenameProject's transactional reference relocation
owns updates to registrations, nested repositories and current provider state.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from management_common.directory_move_store import DirectoryMoveStore
from management_common.json_columns import json_value
from management_common.portal_database import portal_database
from management_portal.society_service.directory_relocation import relocate_nested, reconcile_dependencies
from management_portal.society_service.lineage_storage import lineage_folder_for_project
from scripts.instance_storage_migration import Move, PrefixMap, check_move


def resident_rows(connection):
    return connection.execute(
        'SELECT r.resident_id,l.lineage_id,l.project_id,w.path FROM lineage.residents r '
        'LEFT JOIN workspace.workspaces w ON w.workspace_id=r.owned_workspace_id '
        'LEFT JOIN lineage.lineage_nodes n ON n.node_id=r.resident_id '
        'LEFT JOIN lineage.lineages l ON l.lineage_id=n.lineage_id '
        'WHERE r.deleted_at IS NULL ORDER BY r.resident_id').fetchall()


def plan(instance, rows, prior_moves=()):
    """Database membership, not an old folder's basename, determines destination."""
    mapping = PrefixMap.from_moves(prior_moves)
    moves, aliases, residents, conflicts = [], [], [], []
    for rid, lid, project, stored in rows:
        if not lid or not stored:
            conflicts.append({'resident_id': rid, 'reason': 'missing Lineage or owned Workspace'})
            continue
        original = instance.resolve(stored)
        source_workspace = Path(mapping.map(str(original)) or str(original))
        target = lineage_folder_for_project(lid, project) / 'residents' / rid
        if source_workspace == target / 'workspace':
            if not original.is_dir():
                conflicts.append({'resident_id': rid, 'reason': 'owned Workspace directory missing', 'path': str(original)})
            continue
        source = source_workspace.parent
        if original.name != 'workspace' or original.parent.name != rid or original.parent.parent.name != 'residents':
            conflicts.append({'resident_id': rid, 'reason': 'unrecognised Resident folder', 'path': str(original)})
            continue
        entry = {'resident_id': rid, 'lineage_id': lid, 'source': str(source), 'target': str(target)}
        residents.append(entry)
        move = Move(str(source), str(target), 'resident-folder')
        # During a dry run of the whole upgrade the source may still be under
        # knowledge_base/agents. Check the physical source, retain the future name.
        physical = original.parent
        if physical.is_symlink() or target.is_symlink():
            conflicts.append({**entry, 'reason': 'Resident folder is a symlink'})
        elif physical.is_dir():
            if not original.is_dir():
                conflicts.append({**entry, 'reason': 'owned Workspace directory missing'})
                continue
            issue = check_move(Move(str(physical), str(target), move.kind))
            if issue:
                conflicts.append({**entry, 'reason': issue})
            else:
                moves.append(move)
        elif (target / 'workspace').is_dir():
            aliases.append(move)
        else:
            conflicts.append({**entry, 'reason': 'source and destination Workspace both missing'})
    return {'moves': [asdict(m) for m in moves], 'aliases': [asdict(m) for m in aliases],
            'residents': residents, 'conflicts': conflicts}


def reference_moves(moves):
    """A whole-directory prefix already covers children with the same mapping."""
    ordered = sorted(moves, key=lambda m: (len(Path(m.source).parts), m.source))
    kept = []
    for move in ordered:
        source, target = Path(move.source), Path(move.target)
        if any(source.is_relative_to(Path(parent.source)) and
               Path(parent.target) / source.relative_to(Path(parent.source)) == target for parent in kept):
            continue
        kept.append(move)
    return sorted(kept, key=lambda m: -len(m.source))


def plan_remainders(instance, resident_report, projects):
    """Preserve each old Lineage container inside its database owner's folder.

    Several old containers can belong to one Lineage and have separate Workspace
    registrations. Merging their roots would collapse distinct resource IDs onto
    one path. Keep each container, including conflicting files/deleted Residents,
    under legacy-layout/<old-name>; no contents or registrations are discarded.
    """
    roots = {}
    for row in resident_report['residents']:
        root = Path(row['source']).parent.parent
        if root.name.startswith('lineage_') or not root.is_dir():
            continue
        roots.setdefault(root, set()).add(row['lineage_id'])
    moves, owners, conflicts = [], [], []
    for root, memberships in sorted(roots.items()):
        metadata = root / 'lineage.json'
        if metadata.is_file():
            try:
                identity = json.loads(metadata.read_text(encoding='utf-8')).get('lineage_id')
            except (ValueError, OSError):
                identity = None
            basis = 'legacy identity verified against database'
        else:
            identity = next(iter(memberships)) if len(memberships) == 1 else None
            basis = 'unique registered Resident membership; no legacy metadata'
        if identity not in projects:
            conflicts.append({'source': str(root), 'reason': 'Lineage ownership unresolved'})
            continue
        folder = lineage_folder_for_project(identity, projects[identity])
        target = folder / 'legacy-layout' / root.name
        move = Move(str(root), str(target), 'lineage-remainder')
        issue = check_move(move)
        if issue:
            conflicts.append({'source': str(root), 'target': str(target), 'reason': issue})
        moves.append(move)
        owners.append({'source': str(root), 'target': str(target), 'lineage_id': identity, 'basis': basis})
    return {'moves': [asdict(m) for m in moves], 'aliases': [],
            'residents': [], 'owners': owners, 'conflicts': conflicts}


def apply(instance, report):
    """Caller owns the offline window or the affected Residents' mutation locks.

    Every move commits its declared owner registrations and cleanup together.
    Recovery uses the same journal as online moves, including Provider and Git.
    """
    if report['conflicts']:
        raise RuntimeError('Resident folder conflicts; inspect migration report')
    database = portal_database(schema='lineage')
    mover = DirectoryMoveStore()
    declarations = {row['source']: row for row in [*report.get('residents', []), *report.get('owners', [])]}
    operations = []
    for external, rows in ((False, report['moves']), (True, report['aliases'])):
        for move in reference_moves([Move(**row) for row in rows]):
            owner = declarations[move.source]
            key, table = ('resident_id', 'residents') if 'resident_id' in owner else ('lineage_id', 'lineages')
            with database.read() as connection:
                row = connection.execute(f'SELECT directory_json FROM {table} WHERE {key}=%s AND deleted_at IS NULL',
                                         (owner[key],)).fetchone()
            if row is None or not row['directory_json']:
                raise RuntimeError('Directory owner requires offline ownership migration: ' + owner[key])
            from management_portal.society_service.directory_owner import directory_metadata
            directory_metadata(json_value(row['directory_json']), owner[key])
            kwargs = dict(owner_id=owner[key], actor='instance-layout-migration', database=database,
                          update=relocate_nested, dependencies=reconcile_dependencies)
            if external:
                operation_id, _ = mover.register_external_move(move.source, move.target, **kwargs)
            else:
                operation_id, _ = mover.move(move.source, move.target,
                                             terminate=lambda _path: None, **kwargs)
            operations.append(operation_id)
    return {'directory_operations': operations}
