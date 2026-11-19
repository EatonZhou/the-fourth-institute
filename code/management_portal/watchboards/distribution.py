"""Explicit, portable dependencies of an installed Watchboard.

Runtime state is never an application dependency. Command boards declare their
portable files; view-only applications need only their two manifests.
"""
from pathlib import Path
import json
import yaml


def dependencies(root):
    root = Path(root).resolve()
    manifests = {}
    for name in ('agent.yaml', 'watchboard.yaml'):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Installed Watchboard requires {name}')
        try:
            value = yaml.safe_load(path.read_text(encoding='utf-8'))
        except yaml.YAMLError as error:
            raise ValueError(f'Invalid Watchboard {name}: {error}') from error
        if not isinstance(value, dict):
            raise ValueError(f'{name} must be a mapping')
        manifests[name] = value
    agent = manifests['agent.yaml']
    commands = agent.get('commands')
    if not isinstance(commands, dict):
        raise ValueError('agent.yaml commands must be a mapping')
    declaration = agent.get('distribution', {})
    files = declaration.get('files', []) if isinstance(declaration, dict) else None
    if not isinstance(files, list) or any(not isinstance(f, str) for f in files):
        raise ValueError('Watchboard distribution.files must be a list of relative files')
    if commands and (not agent.get('entrypoint') or agent['entrypoint'] not in files):
        raise ValueError('Command Watchboard must explicitly declare its entrypoint in distribution.files')
    required = agent.get('configs', [agent.get('config', 'config/config.yaml')]) if commands else []
    if not isinstance(required, list) or any(f not in files for f in required):
        raise ValueError('Command Watchboard must declare portable configuration files')
    sources = {}
    for name in files:
        relative = Path(name)
        if (relative.is_absolute() or '..' in relative.parts or not relative.parts
                or relative.parts[0] in {'state', 'logs', 'secrets', 'runtime', 'cache'}
                or any(part.startswith('.') for part in relative.parts)):
            raise ValueError(f'Non-portable Watchboard dependency: {name}')
        path = root / relative
        if path.resolve() != path or not path.is_file():
            raise ValueError(f'Watchboard dependency must be a local regular file: {name}')
        if name in manifests:
            raise ValueError('Watchboard manifests are implicit dependencies')
        sources[name] = path
    # Only declarative fields are portable; arbitrary source metadata is not.
    agent = {k: v for k, v in agent.items() if k in {
        'schema', 'id', 'title', 'description', 'kind', 'entrypoint', 'commands',
        'configs', 'config', 'status_path', 'default_timeout_seconds', 'distribution'}}
    if not commands:
        agent = {k: v for k, v in agent.items() if k not in {'config', 'entrypoint', 'status_path'}}
        agent['configs'] = []
    view = {k: v for k, v in manifests['watchboard.yaml'].items()
            if k in {'schema', 'title', 'description', 'layout'}}
    return {name: json.dumps(value, ensure_ascii=False, indent=2) + '\n'
            for name, value in [('agent.yaml', agent), ('watchboard.yaml', view)]}, sources
