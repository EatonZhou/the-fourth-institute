"""One-time relocation of project Lineages and non-Lineage entries.

Plan by default; apply only with PostgreSQL alone. Reports contain path mappings,
never file contents, and are also the input to the database conversion.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_common.configuration_files import atomic_json
from management_common.secret_environment import secret_environment
from scripts.instance_storage_migration import (
    Move, PrefixMap, Report, apply_moves, check_move, parse_common, require_offline,
    require_protected, require_database_alone, remove_empty_directories,
    write_report,
)
from scripts.migrate_instance_staging import read_instance

LINEAGE = re.compile(r"lineage_[0-9a-f]{12}\Z")


def apply_registered_lineage_moves(moves):
    """The offline conversion uses the same owner commit as ordinary moves."""
    from management_common.directory_move_store import DirectoryMoveStore
    from management_common.portal_database import portal_database, _database_schema
    from management_common.json_columns import json_value
    from management_portal.society_service.directory_relocation import relocate_nested, reconcile_dependencies
    database = portal_database(schema='lineage')
    mover = DirectoryMoveStore()
    receipts = []
    for move in moves:
        identity = Path(move.source).name
        if move.kind != 'project-lineage':
            # Programs and instance feature areas follow the existing instance
            # protocol; they are not registered Society object moves.
            apply_moves([move])
            continue
        if not LINEAGE.fullmatch(identity):
            descriptor = Path(move.source) / 'lineage.json'
            identity = json.loads(descriptor.read_text()).get('lineage_id') if descriptor.is_file() else None
            if not identity or not LINEAGE.fullmatch(identity):
                raise ValueError('Legacy Lineage directory needs an explicit registered owner: ' + move.source)
        with database.read() as connection:
            row = connection.execute(f'SELECT directory_json FROM "{_database_schema("lineage")}".lineages '
                                     'WHERE lineage_id=%s AND deleted_at IS NULL', (identity,)).fetchone()
        if row is None:
            raise ValueError('Lineage move requires a registered owner: ' + identity)
        metadata = json_value(row['directory_json'])
        operation_id, _ = mover.move(move.source, move.target, owner_id=identity,
            origin=metadata['origin'], actor='instance-lineage-migration', terminate=lambda _path: None,
            database=database, update=relocate_nested, dependencies=reconcile_dependencies)
        receipts.append(operation_id)
    return receipts


def plan(instance, projects, program_root=Path('/opt/tfi')):
    moves, links, empty, owners, conflicts = [], [], [], [], []
    project_root, root = instance.area('projects'), instance.area('lineages')
    aliases = []
    for project in sorted(project_root.iterdir()):
        if not project.is_dir() or project.is_symlink():
            continue
        old = project / 'knowledge_base' / 'agents'
        if old.is_dir():
            for source in sorted(old.iterdir()):
                if source.is_symlink():
                    conflicts.append({'path': str(source), 'reason': 'unexpected project folder link'})
                    continue
                if unquote(project.name) != project.name and source.is_dir() and not any(
                        p.is_file() or p.is_symlink() for p in source.rglob('*')):
                    empty.extend([*source.rglob('*'), source, old, old.parent, project])
                    continue
                # Older named Resident folders are preserved and moved as well.
                target = project / 'agents' / source.name
                if LINEAGE.fullmatch(source.name) and source.name in projects:
                    registered = projects[source.name]
                    target = (project_root / registered / 'agents' / source.name
                              if registered else root / source.name)
                moves.append(Move(str(source), str(target), 'project-lineage'))
            empty.append(old)
        if (project / 'agents').is_dir():
            owners.extend({'path': str(p), 'decision': 'keep project-owned file'}
                          for p in (project / 'agents').iterdir() if not LINEAGE.fullmatch(p.name))
    for source in sorted(root.iterdir()):
        if LINEAGE.fullmatch(source.name):
            target = project_root / projects[source.name] / 'agents' / source.name if projects.get(source.name) else source
            if source.is_symlink():
                original = str(source.resolve())
                links.append({'path': str(source), 'target': original})
                aliases.append(Move(str(source), str(target if target != source else Path(original)), 'lineage-link'))
            elif target != source:
                moves.append(Move(str(source), str(target), 'project-lineage'))
            continue
        if source.name == 'KnowledgeArchiveAgent':
            target, kind = program_root / 'KnowledgeArchiveAgent', 'program'
        elif source.name == '_shared':
            # Shared helper code belongs alongside the independently installed program.
            target, kind = program_root / 'portal-agent-shared', 'program'
        elif source.name.startswith('_') or source.name.startswith('.'):
            target, kind = instance.area('features') / 'legacy-agents' / source.name, 'feature-data'
        else:
            target, kind = root.parent / source.name, 'personal-owner'
            owners.append({'path': str(source), 'target': str(target), 'decision': 'preserve outside instance areas; owner to decide'})
        moves.append(Move(str(source), str(target), kind))
    # Links can point into a folder that is itself moving.
    mapping = PrefixMap.from_moves(moves)
    aliases = [Move(m.source, mapping.map(m.target) or m.target, m.kind) for m in aliases]
    for move in moves:
        issue = check_move(move)
        if issue:
            conflicts.append({'source': move.source, 'target': move.target, 'reason': issue})
    return dict(moves=[asdict(m) for m in moves], aliases=[asdict(m) for m in aliases],
                links=links, empty=[str(p) for p in empty], owners=owners, conflicts=conflicts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parse_common(parser)
    parser.add_argument('--program-root', type=Path, default=Path('/opt/tfi'))
    parser.add_argument('--resume', action='store_true', help='resume a report whose directory moves completed')
    parser.add_argument('--resident-contract', action='store_true',
                        help='reconcile Resident folders after an earlier Lineage migration')
    args = parser.parse_args(argv)
    instance, document, _ = read_instance(args.root)
    os.environ['PORTAL_INSTANCE_ROOT'] = str(instance.root)
    import psycopg
    with psycopg.connect(secret_environment(instance, 'postgres.env')['PORTAL_DATABASE_URL']) as conn:
        conn.read_only = True
        if args.apply:
            require_protected(args.protected_pid)
            require_offline(instance, running=('postgres',))
            require_database_alone(conn)
        projects = dict(conn.execute('SELECT lineage_id,project_id FROM lineage.lineages').fetchall())
        from scripts import instance_resident_relocation
        residents = instance_resident_relocation.resident_rows(conn)
    result = json.loads(args.report.read_text()) if args.resume else plan(instance, projects, args.program_root)
    if args.resident_contract:
        result = instance_resident_relocation.plan(instance, residents)
    elif not args.resume:
        result['resident_folders'] = instance_resident_relocation.plan(
            instance, residents, [Move(**m) for m in result['moves']])
    report = Report('migrate_instance_lineages', str(instance.root), args.apply)
    report.sections = result
    write_report(args.report, report)
    if args.resident_contract:
        if args.apply:
            result['outcome'] = instance_resident_relocation.apply(instance, result)
            write_report(args.report, report)
            remainder = instance_resident_relocation.plan_remainders(instance, result, projects)
            result['lineage_remainders'] = remainder
            write_report(args.report, report)
            result['remainder_outcome'] = instance_resident_relocation.apply(instance, remainder)
            write_report(args.report, report)
        print(json.dumps({'residents': len(result['residents']), 'conflicts': len(result['conflicts'])}))
        return 0
    if args.apply:
        if result['conflicts'] or result.get('resident_folders', {}).get('conflicts'):
            raise RuntimeError('Lineage relocation conflicts; see report')
        moves = [Move(**m) for m in result['moves']]
        if args.resume:
            if any(os.path.lexists(m.source) or not os.path.lexists(m.target) for m in moves):
                raise RuntimeError('Resume requires every reported directory move to have completed')
        else:
            result['directory_move_operations'] = apply_registered_lineage_moves(moves)
        result['renamed'] = True
        write_report(args.report, report)
        mapping = PrefixMap.from_moves([*moves, *(Move(**m) for m in result['aliases'])])
        for item in result['links']:
            link = Path(item['path'])
            if args.resume and not os.path.lexists(link):
                continue
            # The old target may now be absent, but the link text must still match.
            if not link.is_symlink() or str(link.resolve()) != item['target']:
                raise RuntimeError('Lineage entry link changed during migration')
            link.unlink()
        # User-authored paths are historical prose. Registered directories and
        # Provider/Git dependencies were reconciled by their move owners.
        result['text_rewrites'] = {'errors': []}
        write_report(args.report, report)
        from management_portal.provider_session_relocation import relocate_provider_sessions
        result['sessions'] = [relocate_provider_sessions(old, new) for old, new in mapping.pairs]
        # Sources are now at their moved names. Read the same registrations
        # through those prefix moves so already completed moves are resumable.
        relocated_rows = [(rid, lid, project, mapping.map(str(instance.resolve(path))) or path)
                          for rid, lid, project, path in residents]
        resident_plan = instance_resident_relocation.plan(instance, relocated_rows)
        result['resident_folders'] = resident_plan
        write_report(args.report, report)
        result['resident_outcome'] = instance_resident_relocation.apply(instance, resident_plan)
        remainder = instance_resident_relocation.plan_remainders(instance, resident_plan, projects)
        result['lineage_remainders'] = remainder
        write_report(args.report, report)
        result['remainder_outcome'] = instance_resident_relocation.apply(instance, remainder)
        # The database path converter reads this report's top-level aliases.
        result['aliases'].extend([*resident_plan['moves'], *resident_plan['aliases'],
                                  *remainder['moves'], *remainder['aliases']])
        result['removed_empty'] = remove_empty_directories(Path(p) for p in result['empty'])
        def relocate(value):
            if isinstance(value, dict):
                return {k: relocate(v) for k, v in value.items()}
            if isinstance(value, list):
                return [relocate(v) for v in value]
            if isinstance(value, str):
                try:
                    path = str(instance.resolve(value))
                except ValueError:
                    return value
                return instance.reference(Path(mapping.map(path) or path).resolve())
            return value
        document['services']['programs'] = relocate(document['services'].get('programs', {}))
        atomic_json(instance.root / 'instance.json', document)
        write_report(args.report, report)
        if any(r['errors'] or r['conflicts'] for r in result['sessions']):
            raise RuntimeError('Relocation completed with session issues; see report before starting')
    print(json.dumps({'moves': len(result['moves']), 'links': len(result['links']), 'conflicts': len(result['conflicts'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
