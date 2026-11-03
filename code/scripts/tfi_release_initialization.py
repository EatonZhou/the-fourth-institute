"""Installer-owned boundary for the exporter-owned feature defaults helper.

The installer runs these steps in a child process with the new instance's code
environment, after its database is imported and before any application service
starts. They read the instance from PORTAL_INSTANCE_ROOT.
"""
from pathlib import Path
import json
import os
import sys


def verify_frozen():
    """Read-only proof that imported tasks, event sources and sessions are frozen."""
    import psycopg
    from management_portal.distribution.history import verify_frozen as verify
    with psycopg.connect(os.environ['PORTAL_DATABASE_URL']) as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        return {**verify(connection), 'verification': 'target-database-read-only'}


def initialize(engineering):
    import psycopg
    from management_common.instance_manifest import load
    from management_portal.distribution.initialization import initialize as apply_defaults
    instance = load()
    manifest = json.loads(Path(engineering).read_text(encoding='utf-8'))
    with psycopg.connect(os.environ['PORTAL_DATABASE_URL'], autocommit=True) as connection:
        with connection.transaction():
            return apply_defaults(manifest, connection, instance, site_config_path=instance.root / 'site.json')


def restore_lineage_events(engineering):
    """Explicit offline repair, bound to this installation's original seed."""
    import psycopg
    from management_common.instance_manifest import load
    from management_portal.distribution.initialization import restore_lineage_events as restore
    instance = load()
    document = json.loads(Path(engineering).read_text(encoding='utf-8'))
    manifest = document.get('engineering', document)
    if manifest.get('run_id') != instance.document['seed_version']:
        raise ValueError('Repair manifest must match the installed seed_version')
    if manifest.get('history_freeze', {}).get('event_sources_paused') is not True:
        raise ValueError('Repair requires an explicitly frozen release seed')
    with psycopg.connect(os.environ['PORTAL_DATABASE_URL'], autocommit=True) as connection:
        return restore(manifest, connection)


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    command = sys.argv[1:2]
    if command == ['verify-frozen'] and len(sys.argv) == 2:
        result = verify_frozen()
    elif command == ['initialize'] and len(sys.argv) == 3:
        result = initialize(sys.argv[2])
    elif command == ['restore-lineage-events'] and len(sys.argv) == 3:
        result = restore_lineage_events(sys.argv[2])
    else:
        raise SystemExit('Usage: tfi_release_initialization.py verify-frozen | initialize <engineering manifest> | restore-lineage-events <original seed manifest>')
    print(json.dumps(result, ensure_ascii=False))
