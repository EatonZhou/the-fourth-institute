"""Relocate a stopped instance on this host without copying host resources.

An incomplete relocation stays registered at its current root and blocks startup.
Repeat the same target to finish reference/session migration after a failure.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode = True

from management_common import host_instances
from management_common.configuration_files import atomic_json
from management_common.instance_manifest import absolute_path, load, validate, overlaps
from management_common.secret_environment import secret_environment
from scripts import instance_services

RECORD = 'runtime/instance-relocation.json'


def _stopped(instance):
    if any(row['pid'] or row['active'] not in {'inactive', 'failed'}
           for row in instance_services.status(instance).values()):
        raise RuntimeError('Stop all instance services before relocating it')


def relocation_manifest(instance, target):
    target = absolute_path(target)
    if overlaps(instance.root, target):
        raise ValueError('The new root must not overlap the current root')
    if target.exists():
        raise ValueError('The new instance root must not already exist')
    parent = target.parent
    while not parent.exists():
        parent = parent.parent
    if parent.stat().st_dev != instance.root.stat().st_dev:
        raise ValueError('Use a database backup and restore to relocate across filesystems')
    document = deepcopy(instance.document)
    for area, value in document.get('areas', {}).items():
        path = Path(value)
        if path.is_relative_to(instance.root):
            document['areas'][area] = str(target / path.relative_to(instance.root))
    updated = validate(target, document)
    from scripts.tfi_release_install import validate_runtime_paths
    validate_runtime_paths(updated)
    registry = host_instances.read_registry()
    if registry.get(instance.name) != str(instance.root):
        raise ValueError('Only the registered instance root can be relocated')
    host_instances.validate_allocation(updated, {k: v for k, v in registry.items() if k != instance.name})
    return updated


def migrate_references(old_root, new_root):
    """Run inside the moved instance's environment with PostgreSQL alone."""
    from management_portal.project_rename import ProjectRename, Rewriter
    from management_portal.society_service.directory_relocation import rewrite_project_database
    from management_portal.directory_move_owner import reconcile_dependencies
    # The existing move semantics retain IDs, bindings, and historical messages.
    change = ProjectRename('', '', Path(old_root), Path(new_root))
    database = rewrite_project_database(change, Rewriter(change), apply=True)
    # The installer has stopped every client. Use the same native dependency
    # owner as ordinary moves, including Git's forward and reverse pointers.
    # Any failure leaves the installer's durable relocation record incomplete.
    sessions = reconcile_dependencies(Path(old_root), Path(new_root))
    return {'database': database.report(), 'provider_sessions': sessions}


def relocate(instance, target):
    from scripts import tfi_release_install as installer
    from scripts.tfi_instance_manager import control_lock
    target = absolute_path(target)
    _stopped(instance)
    existing = instance.root / RECORD
    resume = target == instance.root and existing.is_file()
    record = json.loads(existing.read_text()) if resume else None
    if resume:
        if record.get('target') != str(target) or record.get('instance_id') != instance.instance_id:
            raise ValueError('Relocation record does not match this instance')
        updated = instance
    else:
        updated = relocation_manifest(instance, target)
        record = {'source': str(instance.root), 'target': str(target), 'instance_id': instance.instance_id,
                  'status': 'pending'}
    with control_lock(instance.root), instance_services.configuration_lock(instance.root):
        _stopped(instance)
        if host_instances.find(instance.name).root != instance.root:
            raise ValueError('Instance registration changed before relocation')
        if not resume:
            updated = relocation_manifest(instance, target)
            instance_services.verify_definitions(instance, exact=False)
            atomic_json(existing, record)
            instance_services.remove_definitions(instance)
            moved = registered = False
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                instance.root.rename(target)
                moved = True
                atomic_json(target / 'instance.json', updated.document)
                host_instances.relocate_registration(instance, updated)
                registered = True
            except BaseException:
                if moved and not registered:
                    atomic_json(target / 'instance.json', instance.document)
                    target.rename(instance.root)
                instance_services.install_definitions(instance)
                existing.unlink(missing_ok=True)
                raise
        # Locks live at the root, outside the disposable runtime/cache.
        for name in ('runtime', 'cache'):
            for item in (target / name).iterdir():
                if item == target / RECORD:
                    continue
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        old_url = urlsplit(secret_environment(updated, 'postgres.env')['PORTAL_DATABASE_URL'])
        query = [(key, str(target / 'runtime/s') if key == 'host' and value == str(Path(record['source']) / 'runtime/s') else value)
                 for key, value in parse_qsl(old_url.query, keep_blank_values=True)]
        installer.write_secret(target / 'secrets/postgres.env', 'PORTAL_DATABASE_URL='
                               + urlunsplit(old_url._replace(query=urlencode(query))) + '\n')
        try:
            for name in ('s', 'tmp'):
                (target / 'runtime' / name).mkdir(mode=0o700, exist_ok=True)
            instance_services.install_definitions(updated)
            instance_services.controller(updated).start(instance_services.unit_name(updated, 'postgres'))
            instance_services.wait_database(updated)
            result = json.loads(installer.code_child(updated, 'tfi_relocate_instance.py',
                                                     record['source'], record['target']))
        except BaseException as error:
            atomic_json(target / RECORD, {**record, 'status': 'failed', 'error_type': type(error).__name__})
            raise
        finally:
            instance_services.controller(updated).stop(instance_services.unit_name(updated, 'postgres'))
        atomic_json(target / RECORD, {**record, 'status': 'completed', **result})
    return {'status': 'relocated', 'name': updated.name, 'instance_id': updated.instance_id,
            'root': str(updated.root), 'services_started': False, **result}


if __name__ == '__main__':
    import sys
    print(json.dumps(migrate_references(sys.argv[1], sys.argv[2]), ensure_ascii=False))
