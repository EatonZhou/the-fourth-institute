from __future__ import annotations
import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from management_common.instance_paths import instance_root
from management_common.file_lock import lock_stream
from scripts.portal_source_layout import COMPONENTS
INSTANCE_ROOT = instance_root()
COMPONENT_BUILD_KEYS = {name: item['build_key'] for name, item in COMPONENTS.items()}
UNSUPPORTED = 'Standalone TFI: use install_tfi_release status/cleanup; source Dev/Release control is unsupported'

@contextmanager
def operation_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+', encoding='utf-8') as stream:
        with lock_stream(stream):
            stream.seek(0)
            stream.truncate()
            stream.write(json.dumps({'pid': os.getpid(), 'started_at': time.time()}, sort_keys=True) + '\n')
            stream.flush()
            yield

def _operation_lock_path(instance: str) -> Path:
    if instance not in ('dev', 'release'):
        raise ValueError(f'unknown Portal instance: {instance}')
    return INSTANCE_ROOT / instance / 'portal-services.lock'

def status(instance: str='all') -> dict[str, Any]:
    from scripts import tfi_development_runtime as paired
    root = paired.configured_root()
    if root:
        return paired.status(root, instance)
    return {'ok': False, 'supported': False, 'reason': 'Standalone TFI instances use install_tfi_release status/cleanup; source Dev/Release service management is unavailable'}

def restart(instance: str, *, components: tuple[str, ...] | list[str] | None=None, force_worker_restart: bool=False) -> dict[str, Any]:
    from scripts import tfi_development_runtime as paired
    root = paired.configured_root()
    if root:
        return paired.restart(root, instance, components, force_worker_restart=force_worker_restart)
    raise RuntimeError(UNSUPPORTED)

def request_restart(instance: str, *, components: tuple[str, ...] | list[str] | None=None) -> dict[str, Any]:
    from scripts import tfi_development_runtime as paired
    root = paired.configured_root()
    if root:
        return paired.request_restart(root, instance, components)
    raise RuntimeError(UNSUPPORTED)

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='portal_services', description='Operate Portal Dev and Release services through one safe entry point.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'health'):
        status_parser = commands.add_parser(name)
        status_scope = status_parser.add_mutually_exclusive_group()
        status_scope.add_argument('--all', action='store_true')
        status_scope.add_argument('--instance', choices=('dev', 'release'), default='')
    command = commands.add_parser('restart')
    command.add_argument('instance', choices=('dev', 'release'))
    command.add_argument('--generation-only', action='store_true', help='Deploy a generation without forcing a same-build Worker replacement.')
    command.add_argument('--component', action='append', choices=tuple(COMPONENT_BUILD_KEYS), help='Restart only this component; repeat for more. The default restarts Web and control-plane services, but not ExecutionWorker.')
    request_restart_parser = commands.add_parser('request-restart', help='Queue a Portal Dev restart that runs outside the requesting Web.')
    request_restart_parser.add_argument('instance', choices=('dev',))
    request_restart_parser.add_argument('--component', action='append', choices=tuple(COMPONENT_BUILD_KEYS), help='Request only this component; repeat for more. The default restarts Web and control-plane services, but not ExecutionWorker.')
    return parser

def main(argv: list[str] | None=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {'status', 'health'}:
            result = status(args.instance or 'all')
        elif args.command == 'request-restart':
            result = request_restart(args.instance, components=args.component)
        else:
            with operation_lock(_operation_lock_path(args.instance)):
                result = restart(args.instance, components=args.component, force_worker_restart=not args.generation_only and 'execution-worker' in (args.component or []))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get('ok') else 1
if __name__ == '__main__':
    raise SystemExit(main())
