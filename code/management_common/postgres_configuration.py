"""PostgreSQL locations come from the instance manifest; credentials are private."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from .instance_manifest import load


def postgres_env_file() -> Path:
    return load().directory('secrets') / 'postgres.env'


def host_database_url() -> str:
    from .portal_database import portal_database_url
    return portal_database_url()


def postgres_settings() -> dict:
    instance = load()
    dependency = instance.document['dependencies']['postgres']
    identity = {}
    if postgres_env_file().is_file():
        from .secret_environment import secret_environment
        connection = urlsplit(secret_environment(instance, 'postgres.env')['PORTAL_DATABASE_URL'])
        identity = {'role': connection.username, 'database': connection.path.lstrip('/')}
    return {**dependency, 'prefix': str(Path(dependency['bin']).parent),
            'base': str(instance.root), 'unit': instance.service_name('postgres') + '.service',
            'socket': str(instance.directory('runtime') / 's'),
            'data': str(instance.directory('database')), 'port': instance.document['network']['postgres_port'],
            'role': identity.get('role') or 'tfi', 'database': identity.get('database') or 'tfi_lineage'}
