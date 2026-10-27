"""Deployment settings shared by PostgreSQL preparation and unit rendering."""
from __future__ import annotations

import os
from pathlib import Path
import re

from management_common.portal_paths import path, resolve_directory
from management_common.site_config import read_site_config
from management_common.site_compatibility import compatibility_value


def postgres_env_file() -> Path:
    deployment = read_site_config().get('deployment', {})
    return resolve_directory(os.environ.get('PORTAL_POSTGRES_ENV_FILE',
        deployment.get('postgres_env_file', '~/.config/tfi/portal-postgres.env')))


def host_database_url() -> str:
    """Site database URL for a host script, read from the PostgreSQL env file when unset."""
    from management_common.portal_database import portal_database_url
    if not os.getenv('PORTAL_DATABASE_URL'):
        for line in postgres_env_file().read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() == 'PORTAL_DATABASE_URL':
                os.environ[key.strip()] = value.strip().strip('"\'')
    return portal_database_url()


def postgres_settings() -> dict:
    settings = read_site_config().get('deployment', {}).get('postgres', {})
    if not isinstance(settings, dict):
        raise ValueError('deployment.postgres must be an object')
    prefix = resolve_directory(settings.get('prefix', str(Path.home() / '.local/postgresql-16')))
    base = resolve_directory(settings.get('base', str(path('staging_root') / 'shared/postgresql-16')))
    unit = settings.get('unit', compatibility_value('postgres_unit', 'portal-postgresql.service'))
    if not isinstance(unit, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service', unit):
        raise ValueError('Invalid PostgreSQL service unit')
    result = {
        'unit': unit, 'prefix': str(prefix), 'base': str(base),
        'bin': str(resolve_directory(settings.get('bin', str(prefix / 'usr/lib/postgresql/16/bin')))),
        'lib': str(resolve_directory(settings.get('lib', str(prefix / 'usr/lib/x86_64-linux-gnu')))),
        'share': str(resolve_directory(settings.get('share', str(prefix / 'usr/share/postgresql/16')))),
        'socket': str(resolve_directory(settings.get('socket', str(base / 'socket')))),
        'data': str(resolve_directory(settings.get('data', str(base / 'data')))),
        'port': settings.get('port', 55432),
        'role': settings.get('role', 'tfi'), 'database': settings.get('database', 'tfi_lineage'),
    }
    if type(result['port']) is not int or not 1 <= result['port'] <= 65535:
        raise ValueError('Invalid PostgreSQL port')
    for name in ('role', 'database'):
        if not isinstance(result[name], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', result[name]):
            raise ValueError('Invalid PostgreSQL ' + name)
    return result
