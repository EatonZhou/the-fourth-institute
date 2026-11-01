"""Reconcile legacy Resident folders with their authoritative Lineage membership.

Used by migrate_instance_lineages after its area/Lineage prefix moves. Identity
and bindings are unchanged; RenameProject's transactional reference relocation
owns updates to registrations, nested repositories and current provider state.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path

from management_portal.project_rename import ProjectRename, Rewriter, rewrite_database, _announce, _like_escape
from management_portal.society_service.lineage_storage import lineage_folder_for_project
from scripts.instance_storage_migration import Move, PrefixMap, apply_moves, check_move, remove_empty_directories


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


@dataclass(frozen=True)
class DirectoryChanges(ProjectRename):
    pairs: tuple[tuple[str, str], ...] = ()

    def path_pairs(self):
        return list(self.pairs)


class DirectoryRewriter(Rewriter):
    def needles(self):
        # This is only a conservative SQL candidate filter; Rewriter still
        # checks every exact path boundary. Hundreds of LIKE patterns per
        # column make a bulk migration quadratic over large current tables.
        groups = {}
        for old, _new in self.change.path_pairs():
            groups.setdefault('/' if old.startswith('/') else old.partition(':')[0], []).append(old)
        prefixes = [os.path.commonprefix(values) for values in groups.values()]
        prefixes += [json.dumps(prefix)[1:-1] for prefix in list(prefixes)]
        return sorted({'%' + _like_escape(prefix) + '%' for prefix in prefixes})


def directory_change(instance, moves):
    pairs = []
    for move in moves:
        pairs.append((move.source, move.target))
        # Stored area references must move too; a path does not have to exist.
        pairs.append((instance.reference(Path(move.source)), instance.reference(Path(move.target))))
    pairs = tuple(sorted(set(pairs), key=lambda pair: (-len(pair[0]), pair[0])))
    return DirectoryChanges('', '', instance.root, instance.root, pairs=pairs)


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
                identity = json.loads(metadata.read_text()).get('lineage_id')
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

    No overwrite/merge, no re-registration, no change of Workspace IDs. A failed
    database transaction reverses this operation's renames. Later file/provider
    failures retain a resumable alias plan and are explicitly reported.
    """
    if report['conflicts']:
        raise RuntimeError('Resident folder conflicts; inspect migration report')
    moves = [Move(**m) for m in report['moves']]
    mappings = reference_moves([*moves, *(Move(**m) for m in report['aliases'])])
    if not mappings:
        return {'database': {'rows': 0}, 'files': {}, 'sessions': []}
    change = directory_change(instance, mappings)
    rewriter = DirectoryRewriter(change)
    created_parents = set()
    for move in moves:
        parent = Path(move.target).parent
        while not parent.exists():
            created_parents.add(parent)
            parent = parent.parent
    apply_moves(moves)
    try:
        database = rewrite_database(change, rewriter, apply=True)
    except BaseException:
        apply_moves(Move(m.target, m.source, m.kind) for m in reversed(moves))
        remove_empty_directories(created_parents)
        raise
    _announce(database)
    from scripts.migrate_instance_lineages import text_candidates
    from scripts.instance_storage_migration import rewrite_text_files
    from management_portal.provider_session_relocation import relocate_provider_sessions
    mapping = PrefixMap.from_moves(mappings)
    candidates = text_candidates([Move(m.source, m.target, 'project-lineage') for m in mappings], mapping)
    files = rewrite_text_files(candidates, mapping, apply=True)
    sessions = [relocate_provider_sessions(m.source, m.target) for m in mappings]
    removed = remove_empty_directories(Path(p) for p in report.get('empty', []))
    return {'database': database.report(), 'files': files, 'sessions': sessions, 'removed_empty': removed}
