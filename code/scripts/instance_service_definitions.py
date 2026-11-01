"""Render an instance's complete service inventory without changing the host."""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.instance_manifest import InstanceManifest, load, validate
from management_portal.service_runtime import MODULES

ORDER = ('postgres', 'nats', 'resource', 'execution', 'society', 'worker',
         'claude-code', 'claude', 'kimi-code', 'kimi', 'dev-web', 'web', 'gateway')


def components(instance: InstanceManifest) -> tuple[str, ...]:
    registered = instance.document['services']['components']
    return tuple(c for c in ORDER if c in registered) + tuple(sorted(set(registered) - set(ORDER)))


def dependencies(instance: InstanceManifest, component: str) -> tuple[str, ...]:
    """Stable process ordering; registering an account does not rewrite other units."""
    graph = {
        'postgres': (), 'nats': (),
        'resource': ('postgres', 'nats'),
        'execution': ('postgres', 'nats', 'resource'),
        'society': ('postgres', 'nats', 'resource', 'execution'),
        'gateway': ('web', 'dev-web'),
    }
    required = graph.get(component, ('postgres', 'nats', 'resource', 'execution', 'society'))
    return tuple(name for name in required if name in instance.document['services']['components'])


def missing_launchers(instance: InstanceManifest) -> list[str]:
    return [c for c in components(instance) if c not in {'postgres', 'nats', 'web', 'dev-web', *MODULES}
            and not c.startswith('codex-app-server@') and instance.service_program(c) is None]


def render(instance: InstanceManifest, platform: str, *, windows_mode='user', windows_account='') -> dict[str, str]:
    if platform not in {'linux', 'macos', 'windows'}:
        raise ValueError('Unsupported service platform')
    adapter = importlib.import_module('scripts.tfi_release_platform_' + platform)
    selected = components(instance)
    options = {'mode': windows_mode, 'account': windows_account} if platform == 'windows' else {}
    definitions = {}
    for component in selected:
        name = instance.service_name(component) + ('' if platform == 'windows' else '.service')
        filename = adapter.unit_path(Path('.'), name).name
        definitions[filename] = adapter.manifest_unit_text(instance, component, **options)
    return definitions


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--proposal', type=Path)
    parser.add_argument('--platform', choices=('linux', 'macos', 'windows'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    instance = validate(args.root, json.loads(args.proposal.read_text())) if args.proposal else load(args.root)
    if args.output.exists() or args.output.is_symlink():
        parser.error('Output must be a new review directory, not an installed service directory')
    definitions = render(instance, args.platform)
    args.output.mkdir(parents=True)
    for filename, content in definitions.items():
        (args.output / filename).write_text(content)
    print(json.dumps({'rendered': len(definitions), 'missing_launchers': missing_launchers(instance),
                      'activated': False, 'output': str(args.output)}))
