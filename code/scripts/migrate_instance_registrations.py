"""Convert device SSH overrides and secret-file registrations, with clients stopped.

Run after offline manifest conversion and starting PostgreSQL alone. This command
never starts services or changes credentials, and never prints database endpoints.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.instance_manifest import load
from management_common.secret_environment import secret_environment
from scripts.instance_services import configuration_lock


def converted_documents(documents: dict, overrides: dict) -> dict:
    if not isinstance(overrides, dict):
        raise ValueError('Historical device SSH paths must be an object')
    updates = {}
    for device_id, paths in overrides.items():
        if device_id not in documents:
            raise ValueError('Historical SSH override refers to an unregistered device')
        if not isinstance(paths, dict) or paths.keys() - {'config_path', 'identity_file'}:
            raise ValueError('Historical SSH override contains unsupported fields')
        record = copy.deepcopy(documents[device_id])
        ssh = record.get('access_methods', {}).get('ssh')
        if not isinstance(ssh, dict) or not ssh.get('host'):
            raise ValueError('Historical SSH override requires a registered SSH host')
        for field in ('config_path', 'identity_file'):
            value = paths.get(field, '')
            if not isinstance(value, str) or (value and (
                    not Path(value).is_absolute() or '..' in Path(value).parts
                    or any(ord(c) < 32 for c in value))):
                raise ValueError('Historical SSH override requires an absolute host file path')
            # The historical override replaced both fields, including omitted fields.
            ssh[field] = value
        if record != documents[device_id]:
            updates[device_id] = record
    return updates


def relocated_secret_path(value, old_root: Path, new_root: Path):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        return value
    path = Path(value)
    if not path.is_relative_to(old_root):
        return value
    if '..' in path.parts:
        raise ValueError('Secret registration contains a noncanonical path')
    return str(new_root / path.relative_to(old_root))


def require_clients_stopped(instance):
    if not sys.platform.startswith('linux'):
        raise NotImplementedError('Verify native service ownership before running this conversion on another platform')
    result = subprocess.run(['systemctl', '--user', 'list-units', '--all', '--plain', '--output=json',
                             instance.service_prefix + '-*.service', 'portal-user-router.service'],
                            check=True, capture_output=True, text=True, timeout=30)
    units = {row['unit'] for row in json.loads(result.stdout)}
    units |= {instance.service_name(component) + '.service' for component in instance.document['services']['components']}
    units.discard(instance.service_name('postgres') + '.service')
    for unit in sorted(units):
        result = subprocess.run(['systemctl', '--user', 'show', unit,
                                 '--property=LoadState,ActiveState,MainPID,ControlPID'],
                                check=False, capture_output=True, text=True, timeout=15)
        state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if state.get('LoadState') == 'not-found':
            continue
        if (result.returncode or state.get('ActiveState') not in {'inactive', 'failed'}
                or state.get('MainPID') != '0' or state.get('ControlPID') != '0'):
            raise RuntimeError('Stop all instance clients before migrating device settings: ' + unit)


def migrate(root: Path, *, check_only=False):
    import psycopg
    instance = load(root)
    with configuration_lock(instance.root):
        old_site = json.loads((instance.root / 'config/site.json').read_text())
        target = json.loads((instance.root / 'config/target.json').read_text())
        old_secrets = Path(target['data_roots']['secrets_dir'])
        changed_apis = []
        overrides = old_site.get('deployment', {}).get('device_ssh_paths', {})
        endpoint = secret_environment(instance, 'postgres.env')['PORTAL_DATABASE_URL']
        if not check_only:
            require_clients_stopped(instance)
        with psycopg.connect(endpoint) as connection:
            connection.read_only = check_only
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout='20s'")
                if not check_only:
                    cursor.execute('SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() '
                                   "AND backend_type='client backend' AND pid<>pg_backend_pid()")
                    if cursor.fetchone()[0]:
                        raise RuntimeError('Other database clients are still connected')
                    cursor.execute('LOCK TABLE resource_registry.registry_documents IN EXCLUSIVE MODE')
                cursor.execute("SELECT document_id,document_json::jsonb FROM resource_registry.registry_documents WHERE collection='device'")
                updates = converted_documents(dict(cursor.fetchall()), overrides)
                if updates and not check_only:
                    cursor.execute("UPDATE resource_registry.registry_collections SET revision=nextval('resource_registry.registry_revision_seq') "
                                   "WHERE collection='device' RETURNING revision")
                    revision = cursor.fetchone()[0]
                    for device_id, record in updates.items():
                        cursor.execute("UPDATE resource_registry.registry_documents SET document_json=%s,revision=%s "
                                       "WHERE collection='device' AND document_id=%s",
                                       (json.dumps(record, ensure_ascii=False), revision, device_id))
                cursor.execute("SELECT to_regclass('model_apis.apis')")
                if cursor.fetchone()[0]:
                    cursor.execute('SELECT api_id,api_key_file,config_dir FROM model_apis.apis')
                    for api_id, secret_file, config_dir in cursor.fetchall():
                        changed = tuple(relocated_secret_path(value, old_secrets, instance.directory('secrets'))
                                        for value in (secret_file, config_dir))
                        if changed == (secret_file, config_dir):
                            continue
                        changed_apis.append(api_id)
                        if not check_only:
                            cursor.execute('UPDATE model_apis.apis SET api_key_file=%s,config_dir=%s,version=version+1,'
                                           "updated_at=now(),updated_by='instance-migration' WHERE api_id=%s",
                                           (*changed, api_id))
        return {'checked': check_only, 'devices': len(updates), 'model_api_paths': len(changed_apis), 'services_started': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(migrate(args.root, check_only=args.check)))
