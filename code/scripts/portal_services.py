from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from scripts import portal_dev_restart_requests, portal_service_units, portal_release
from scripts import portal_dev_generation
from scripts.portal_service_control import ServiceControl
from management_portal.component_lifecycle import publish_component_state
from management_common.instance_paths import instance_root
from management_common.file_lock import lock_stream
from management_common.portal_network import DEFAULT_PORT, router_port
INSTANCE_ROOT = instance_root()
DEFAULT_RESTART_COMPONENTS = ('resource', 'execution-service', 'lineage', 'web')
DEFAULT_RESTART_NOTICE_SECONDS = 5
USER_ROUTER_SERVICE = 'portal-user-router.service'
USER_ROUTER_HEALTH_URL = f'http://127.0.0.1:{DEFAULT_PORT}/__portal_router/health'

def user_router_changes() -> list[str] | None:
    """The sources of the running router that changed since it started; ``None`` when unknown."""
    from management_portal.portal_deployment_router import changed_sources, source_manifest_path
    try:
        manifest = json.loads(source_manifest_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        manifest = None
    return changed_sources(manifest, _unit_state(USER_ROUTER_SERVICE)['pid'])

def user_router_health() -> dict[str, Any]:
    return {'ok': False, 'supported': False, 'reason': 'Standalone TFI instances use install_tfi_release status/cleanup; source Dev/Release service management is unavailable'}

def restart_user_router() -> dict[str, Any]:
    return {'ok': False, 'supported': False, 'reason': 'Standalone TFI instances use install_tfi_release status/cleanup; source Dev/Release service management is unavailable'}
from scripts.portal_source_layout import COMPONENTS, INSTANCE_LAYOUT
COMPONENT_BUILD_KEYS = {name: item['build_key'] for name, item in COMPONENTS.items()}
COMPONENT_LIFECYCLE = {'lineage': {'restarting': 'SocietyService 正在重启', 'ready': 'SocietyService 已就绪', 'unavailable': 'SocietyService 暂时不可用'}, 'resource': {'restarting': 'ResourceService 正在重启', 'ready': 'ResourceService 已就绪', 'unavailable': 'ResourceService 暂时不可用'}}
INSTANCE_CONFIG = {name: {**layout, 'restart_script': ROOT / 'scripts' / ('restart_portal_with_notice.py' if name == 'dev' else 'restart_portal_release.py'), 'restart_message': f'Portal {name.title()} 将在倒计时结束后重启。'} for name, layout in INSTANCE_LAYOUT.items()}

def _run(*args: str, check: bool=True) -> subprocess.CompletedProcess[str]:
    raise RuntimeError('Standalone TFI: use install_tfi_release status/cleanup; source Dev/Release control is unsupported')
    result = subprocess.run(list(args), check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} exited with {result.returncode}: {(result.stdout or '')[-4000:].strip()}')
    return result

def _controller() -> ServiceControl:
    raise RuntimeError('Standalone TFI: use install_tfi_release status/cleanup; source Dev/Release control is unsupported')
    return ServiceControl(runner=_run)

def _unit_state(service: str) -> dict[str, Any]:
    return _controller().status(service)

def _execution_health(instance: str) -> dict[str, Any]:
    try:
        from management_portal.execution.service import ExecutionService
        from management_portal.execution.transport import ExecutionServiceClient
        client = ExecutionServiceClient(f'{instance_root()}/{instance}/execution-service.sock', timeout=3)
        return ExecutionService(client=client).health()
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}

def _resource_service_health(instance: str) -> dict[str, Any]:
    try:
        from management_portal.resources.transport import ResourceServiceClient
        client = ResourceServiceClient(f'{instance_root()}/{instance}/resource-service.sock', timeout=3)
        payload = client.status()
        return {'ok': bool(payload.get('schema')), **payload}
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}

def _society_service_health(instance: str) -> dict[str, Any]:
    try:
        from management_portal.society_service.transport import SocietyServiceClient
        client = SocietyServiceClient(f'{instance_root()}/{instance}/society-service.sock', timeout=3)
        payload = client.health()
        return dict(payload)
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}

def maintenance_path(instance: str) -> Path:
    _config(instance)
    return INSTANCE_ROOT / instance / 'maintenance.json'

def require_available(instance: str) -> None:
    if maintenance_path(instance).exists():
        raise RuntimeError(f'{instance} is in maintenance; use portal_services start {instance} to resume')

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

def _config(instance: str) -> dict[str, Any]:
    try:
        return INSTANCE_CONFIG[instance]
    except KeyError as exc:
        raise ValueError(f'unknown Portal instance: {instance}') from exc

def _operation_lock_path(instance: str) -> Path:
    _config(instance)
    return INSTANCE_ROOT / instance / 'portal-services.lock'

def _web_health(instance: str) -> dict[str, Any]:
    url = f'http://127.0.0.1:{_config(instance)['port']}/api/health'
    try:
        with urllib.request.urlopen(url, timeout=6) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('health response is not an object')
        return {'ok': bool(payload.get('ok')), 'url': url, **payload}
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        return {'ok': False, 'url': url, 'error': str(exc)}

def _user_router_health() -> dict[str, Any]:
    url = USER_ROUTER_HEALTH_URL
    try:
        url = f'http://127.0.0.1:{router_port()}/__portal_router/health'
        with urllib.request.urlopen(url, timeout=6) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('health response is not an object')
        return {'ok': bool(payload.get('ok')), 'url': url, **payload}
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        return {'ok': False, 'url': url, 'error': str(exc)}

def _queue_counts(health: dict[str, Any]) -> dict[str, int]:
    scheduler = health.get('scheduler')
    scheduler = scheduler if isinstance(scheduler, dict) else {}
    partitions = scheduler.get('capacity_partitions')
    partitions = partitions if isinstance(partitions, dict) else {}
    queued = sum((int(item.get('queued_count') or 0) for item in partitions.values() if isinstance(item, dict)))
    if not partitions:
        status_counts = scheduler.get('status_counts')
        if isinstance(status_counts, dict):
            queued = int(status_counts.get('queued') or 0)
    return {'active': int(scheduler.get('active_count') or 0), 'queued': queued}

def _database_schema_status(instance: str) -> dict[str, Any]:
    del instance
    try:
        try:
            import psycopg
        except ImportError:
            runtime_python = INSTANCE_ROOT / 'dev' / 'runtime-venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            probe = subprocess.run([str(runtime_python), '-c', 'import json,sys,psycopg; c=psycopg.connect(sys.stdin.read(),connect_timeout=2,autocommit=True); r=c.execute("SELECT current_database(), current_setting(\'server_version_num\')::integer").fetchone(); s=[x[0] for x in c.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT LIKE \'pg_%\' AND schema_name <> \'information_schema\' ORDER BY schema_name")]; c.close(); print(json.dumps({\'ok\':True,\'backend\':\'postgresql\',\'database\':r[0],\'server_version\':r[1],\'schemas\':s}))'], input=_postgres_database_url(), text=True, capture_output=True, timeout=15, check=True)
            return json.loads(probe.stdout)
        with psycopg.connect(_postgres_database_url(), connect_timeout=6, autocommit=True) as connection:
            row = connection.execute("SELECT current_database(), current_setting('server_version_num')::integer").fetchone()
            schemas = [str(item[0]) for item in connection.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema' ORDER BY schema_name").fetchall()]
        return {'ok': True, 'backend': 'postgresql', 'database': str(row[0]), 'server_version': int(row[1]), 'schemas': schemas}
    except Exception as exc:
        return {'ok': False, 'backend': 'postgresql', 'error': f'{type(exc).__name__}: {exc}'}

def _postgres_database_url() -> str:
    from management_portal.resident_shell_environment import portal_database_url
    return portal_database_url()

def _execution_worker_health(instance: str) -> dict[str, Any]:
    path = INSTANCE_ROOT / instance / 'execution-worker-health.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {'ok': False, 'path': str(path), 'error': str(exc)}
    if not isinstance(payload, dict):
        return {'ok': False, 'path': str(path), 'error': 'worker health is not an object'}
    return {'path': str(path), **payload}

def instance_status(instance: str, *, include_schema: bool=False) -> dict[str, Any]:
    return status(instance, include_schema=include_schema)

def status(instance: str='all', *, include_schema: bool=False) -> dict[str, Any]:
    from scripts import tfi_development_runtime as paired
    root = paired.configured_root()
    if root:
        return paired.status(root, instance)
    return {'ok': False, 'supported': False, 'reason': 'Standalone TFI instances use install_tfi_release status/cleanup; source Dev/Release service management is unavailable'}

def _prepare_units(instance: str) -> None:
    raise RuntimeError('Standalone TFI: use install_tfi_release status/cleanup; source Dev/Release control is unsupported')
    controller = _controller()
    if controller.platform == 'linux':
        portal_service_units.install_units()
        return
    portal_service_units.install_native_units(instance, controller.platform)

def _wait_for_drain(instance: str, timeout_seconds: float) -> dict[str, int]:
    started = time.monotonic()
    last: dict[str, int] | None = None
    while True:
        health = _execution_health(instance)
        if not health.get('ok'):
            raise RuntimeError(f'cannot inspect {instance} queue: {health.get('error') or health}')
        counts = _queue_counts(health)
        if counts == {'active': 0, 'queued': 0}:
            return counts
        if counts != last:
            print(f'Waiting for {instance} drain: active={counts['active']} queued={counts['queued']}', file=sys.stderr, flush=True)
            last = counts
        if timeout_seconds > 0 and time.monotonic() - started >= timeout_seconds:
            raise TimeoutError(f'{instance} did not drain within {timeout_seconds:g}s: {counts}')
        time.sleep(2)

def _require_idle(instance: str, *, drain: bool, timeout_seconds: float) -> None:
    if drain:
        _wait_for_drain(instance, timeout_seconds)
        return
    health = _execution_health(instance)
    if not health.get('ok'):
        raise RuntimeError(f'cannot inspect {instance} queue: {health}')
    counts = _queue_counts(health)
    if counts != {'active': 0, 'queued': 0}:
        raise RuntimeError(f'refusing to stop {instance} while work is active or queued: {counts}; use --drain to wait')

def _restart_web_with_notice(instance: str, seconds: int, *, release_commit: str='') -> None:
    config = _config(instance)
    command = [sys.executable, str(config['restart_script']), '--message', str(config['restart_message'])]
    if instance == 'dev':
        command.extend(['--service', str(config['web'])])
    else:
        command.extend(['--seconds', str(seconds)])
    if release_commit:
        command.extend(['--commit', release_commit])
    result = _run(*command)
    if result.stdout:
        print(result.stdout.rstrip(), file=sys.stderr)

def _assert_instance(instance: str, payload: dict[str, Any], *, components: tuple[str, ...] | list[str] | set[str] | None=None) -> None:
    selected = set(COMPONENT_BUILD_KEYS.values()) if components is None else set(components)
    if 'web' in selected and str(payload['web'].get('instance') or '') != instance:
        raise RuntimeError(f'{instance} web reported instance={payload['web'].get('instance')!r}')

def _wait_healthy(instance: str, *, pids_before_restart: dict[str, int] | None=None, verify_components: tuple[str, ...] | list[str] | set[str] | None=None, worker_id_before_restart: str='', timeout_seconds: float=270) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    last_error = 'services did not report healthy'
    while time.monotonic() < deadline:
        latest = instance_status(instance, include_schema=True)
        if latest.get('runtime_ok'):
            try:
                _assert_instance(instance, latest, components=verify_components)
                if worker_id_before_restart:
                    worker_health = latest.get('execution_worker') or {}
                    current = worker_health.get('current') or {}
                    worker_id = current.get('worker_id')
                    if not worker_health.get('ok') or not worker_id or worker_id == worker_id_before_restart:
                        raise RuntimeError('explicit Worker restart has not activated a new Worker')
                if pids_before_restart:
                    current = {item['service']: int(item['pid']) for item in [latest['web_service'], *latest['services']]}
                    unchanged = {service: pid for service, pid in pids_before_restart.items() if pid > 0 and current.get(service) == pid}
                    if unchanged:
                        raise RuntimeError(f'service PIDs did not change: {unchanged}')
                return latest
            except RuntimeError as exc:
                last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f'{instance} failed post-start verification ({last_error}): {latest}')

def _wait_resource_service_healthy(instance: str, *, timeout_seconds: float=90) -> None:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = _resource_service_health(instance)
        if latest.get('ok'):
            return
        time.sleep(0.2)
    raise RuntimeError(f'{instance} ResourceService did not become ready: {latest}')

def _capture_pids(instance: str, components: tuple[str, ...] | list[str] | None=None) -> dict[str, int]:
    config = _config(instance)
    services = (config['web'], *config['services']) if components is None else tuple((config['component_units'][component] for component in components if component != 'execution-worker'))
    return {state['service']: int(state['pid']) for state in (_unit_state(service) for service in services)}

def start(instance: str) -> dict[str, Any]:
    raise RuntimeError('Standalone TFI: use install_tfi_release status/cleanup; source Dev/Release control is unsupported')
    config = _config(instance)
    _prepare_units(instance)
    controller = _controller()
    if instance == 'dev':
        portal_dev_generation.activate()
    controller.start(config['target'], config['web'], USER_ROUTER_SERVICE)
    result = _wait_healthy(instance)
    maintenance_path(instance).unlink(missing_ok=True)
    if instance == 'dev':
        controller.start('portal-dev-source-reloader.service')
    return result

def stop(instance: str, *, drain: bool, drain_timeout: float, force: bool) -> dict[str, Any]:
    raise RuntimeError('Standalone TFI: use install_tfi_release status/cleanup; source Dev/Release control is unsupported')
    config = _config(instance)
    controller = _controller()
    if not force:
        _require_idle(instance, drain=drain, timeout_seconds=drain_timeout)
    from management_portal.shared_state import atomic_replace_json
    atomic_replace_json(maintenance_path(instance), {'stopped_at': time.time()})
    if instance == 'dev':
        controller.stop('portal-dev-source-reloader.service')
    controller.stop(config['web'], config['target'])
    if controller.platform == 'linux':
        controller.stop(*reversed(config['services']))
    states = [_unit_state(service) for service in (config['web'], *config['services'])]
    return {'ok': all((item['active'] == 'inactive' for item in states)), 'instance': instance, 'services': states}

def restart(instance: str, *, notice_seconds: int, components: tuple[str, ...] | list[str] | None=None, release_commit: str='', force_worker_restart: bool=False, units_prepared: bool=False) -> dict[str, Any]:
    from scripts import tfi_development_runtime as paired
    root = paired.configured_root()
    if root:
        if release_commit:
            raise ValueError('Publish the commit with tfi_dev before restarting')
        return paired.restart(root, instance, components)
    raise RuntimeError('Standalone TFI: use install_tfi_release status/cleanup; source Dev/Release control is unsupported')
    if int(notice_seconds) != DEFAULT_RESTART_NOTICE_SECONDS:
        raise ValueError('Portal restart countdown is fixed at 5 seconds')
    config = _config(instance)
    controller = _controller()
    requested = tuple(dict.fromkeys(components or DEFAULT_RESTART_COMPONENTS))
    unknown = sorted(set(requested) - set(COMPONENT_BUILD_KEYS))
    if unknown:
        raise ValueError(f'unknown Portal restart components: {unknown}')
    selected = tuple((component for component in (*DEFAULT_RESTART_COMPONENTS, 'execution-worker') if component in requested))
    if release_commit and (instance != 'release' or 'web' not in selected):
        raise ValueError('Only a Release Web restart can advance the release branch')
    require_available(instance)
    source_before_restart = None
    if instance == 'dev':
        from scripts import portal_dev_source_reloader as reloader
        source_before_restart = reloader.component_source_states(reloader.source_state())
        from scripts.portal_source_layout import SHARED_COMPONENTS
        if set(selected) & set(SHARED_COMPONENTS) and portal_release.is_deployed():
            require_available('release')
    if not units_prepared:
        _prepare_units(instance)
    if instance == 'dev':
        portal_dev_generation.activate(components=[COMPONENT_BUILD_KEYS[item] for item in selected])
    controller.start(config['target'], USER_ROUTER_SERVICE)
    pids_before_restart = _capture_pids(instance, selected)
    lifecycle_operations = {component: f'{component}-restart-{uuid.uuid4().hex}' for component in selected if component in COMPONENT_LIFECYCLE}
    try:
        if 'web' in selected:
            _restart_web_with_notice(instance, notice_seconds, release_commit=release_commit)
        lineage_paused_for_resource = 'resource' in selected and 'lineage' in selected
        announced_components: set[str] = set()
        if lineage_paused_for_resource:
            lineage = COMPONENT_LIFECYCLE['lineage']
            publish_component_state(instance, 'lineage', 'restarting', message=lineage['restarting'], operation_id=lifecycle_operations['lineage'])
            announced_components.add('lineage')
            controller.stop(config['component_units']['lineage'])
        for component in selected:
            if component in {'web', 'execution-worker'}:
                continue
            lifecycle = COMPONENT_LIFECYCLE.get(component)
            if lifecycle and component not in announced_components:
                publish_component_state(instance, component, 'restarting', message=lifecycle['restarting'], operation_id=lifecycle_operations[component])
            if component == 'lineage' and 'resource' not in selected:
                _wait_resource_service_healthy(instance)
            action = 'start' if component == 'lineage' and lineage_paused_for_resource else 'restart'
            if component == 'execution-service':
                state = _unit_state(config['component_units'][component])
                action = 'reload' if state['active'] == 'active' else 'start'
            service = config['component_units'][component]
            if action == 'reload':
                if controller.platform == 'linux':
                    controller.reload(service)
                else:
                    _require_idle(instance, drain=True, timeout_seconds=270)
                    controller.restart(service)
            elif action == 'start':
                controller.start(service)
            else:
                controller.restart(service)
            if component == 'resource':
                _wait_resource_service_healthy(instance)
        worker_id_before_restart = ''
        if 'execution-worker' in selected:
            worker_unit = config['component_units']['execution-worker']
            worker_state = _unit_state(worker_unit)
            if force_worker_restart and worker_state['active'] == 'active':
                health = _execution_worker_health(instance)
                current = health.get('current') or {}
                worker_id_before_restart = str(current.get('worker_id') or '')
                if health.get('explicit_restart_supported') is not True or health.get('manager_pid') != worker_state.get('pid') or (not worker_id_before_restart):
                    raise RuntimeError('ExecutionWorkerManager must be deployed and report explicit restart support with a current Worker before an explicit restart')
                if controller.platform == 'linux':
                    controller.signal_main(worker_unit, 'SIGUSR1')
                else:
                    _require_idle(instance, drain=True, timeout_seconds=270)
                    controller.restart(worker_unit)
            else:
                action = 'reload' if worker_state['active'] == 'active' else 'start'
                if action == 'reload':
                    if controller.platform == 'linux':
                        controller.reload(worker_unit)
                    else:
                        _require_idle(instance, drain=True, timeout_seconds=270)
                        controller.restart(worker_unit)
                else:
                    controller.start(worker_unit)
        result = _wait_healthy(instance, pids_before_restart=pids_before_restart, verify_components=[COMPONENT_BUILD_KEYS[item] for item in selected], **{'worker_id_before_restart': worker_id_before_restart} if worker_id_before_restart else {})
        if worker_id_before_restart:
            health = result['execution_worker']
            current = health['current']
            result['worker_restart'] = {'previous_worker_id': worker_id_before_restart, 'worker_id': current['worker_id'], 'build_id': current['build_id'], 'manager_pid': health['manager_pid']}
    except Exception:
        for component, operation_id in lifecycle_operations.items():
            lifecycle = COMPONENT_LIFECYCLE[component]
            publish_component_state(instance, component, 'unavailable', message=lifecycle['unavailable'], operation_id=operation_id)
        raise
    for component, operation_id in lifecycle_operations.items():
        lifecycle = COMPONENT_LIFECYCLE[component]
        publish_component_state(instance, component, 'ready', message=lifecycle['ready'], operation_id=operation_id)
    if instance == 'dev':
        from scripts.portal_source_layout import SHARED_COMPONENTS
        common = tuple((c for c in selected if c in SHARED_COMPONENTS))
        if common and portal_release.is_deployed():
            with operation_lock(_operation_lock_path('release')):
                restart('release', notice_seconds=notice_seconds, components=common, units_prepared=True)
        accepted = reloader.read_deployed_source_states(source_before_restart)
        reloader.accept_components(accepted, source_before_restart, set(selected))
    return result

def request_restart(instance: str, *, components: tuple[str, ...] | list[str] | None=None) -> dict[str, Any]:
    from scripts import tfi_development_runtime as paired
    root = paired.configured_root()
    if root:
        return paired.request_restart(root, instance, components)
    raise RuntimeError('Standalone TFI: use install_tfi_release status/cleanup; source Dev/Release control is unsupported')
    if instance != 'dev':
        raise ValueError('deferred restart requests are only available for Portal Dev')
    require_available(instance)
    selected = tuple(dict.fromkeys(components or ()))
    unknown = sorted(set(selected) - set(COMPONENT_BUILD_KEYS))
    if unknown:
        raise ValueError(f'unknown Portal restart components: {unknown}')
    request = portal_dev_restart_requests.submit_restart_request(selected)
    controller = _controller()
    controller.start('portal-dev-source-reloader.service')
    if controller.platform == 'linux':
        controller.signal_main('portal-dev-source-reloader.service', 'SIGUSR1')
    return {'ok': True, 'queued': True, 'instance': 'dev', 'request': request}

def _add_safety_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--drain', action=argparse.BooleanOptionalAction, default=True, help='Wait for active and queued work to reach zero (default: enabled).')
    parser.add_argument('--drain-timeout', type=float, default=0, help='Maximum drain wait in seconds; 0 waits indefinitely.')
    parser.add_argument('--force', action='store_true', help='Bypass the active/queued work guard.')

def _add_notice_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--notice', type=int, default=DEFAULT_RESTART_NOTICE_SECONDS, choices=(DEFAULT_RESTART_NOTICE_SECONDS,), help='Browser restart countdown; fixed at 5 seconds.')

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='portal_services', description='Operate Portal Dev and Release services through one safe entry point.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'health'):
        status_parser = commands.add_parser(name)
        status_scope = status_parser.add_mutually_exclusive_group()
        status_scope.add_argument('--all', action='store_true')
        status_scope.add_argument('--instance', choices=('dev', 'release'), default='')
        status_parser.add_argument('--schema', action='store_true')
    for name in ('start', 'stop', 'restart'):
        command = commands.add_parser(name)
        command.add_argument('instance', choices=('dev', 'release'))
        if name == 'stop':
            _add_safety_arguments(command)
        if name == 'restart':
            _add_notice_argument(command)
            command.add_argument('--generation-only', action='store_true', help='Deploy a generation without forcing a same-build Worker replacement.')
            command.add_argument('--component', action='append', choices=tuple(COMPONENT_BUILD_KEYS), help='Restart only this component; repeat for more. The default restarts Web and control-plane services, but not ExecutionWorker.')
    request_restart_parser = commands.add_parser('request-restart', help='Queue an immediate rolling Portal Dev component deployment.')
    request_restart_parser.add_argument('instance', choices=('dev',))
    request_restart_parser.add_argument('--component', action='append', choices=tuple(COMPONENT_BUILD_KEYS), help='Request only this component; repeat for more. Without this option, deploy components whose source changed since their last successful deployment.')
    return parser

def main(argv: list[str] | None=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {'status', 'health'}:
            selected = args.instance or 'all'
            result = status(selected, include_schema=args.schema)
        elif args.command == 'request-restart':
            result = request_restart(args.instance, components=args.component)
        else:
            with operation_lock(_operation_lock_path(args.instance)):
                if args.command == 'start':
                    result = start(args.instance)
                elif args.command == 'stop':
                    result = stop(args.instance, drain=args.drain, drain_timeout=args.drain_timeout, force=args.force)
                elif args.command == 'restart':
                    result = restart(args.instance, notice_seconds=args.notice, components=args.component, force_worker_restart=not args.generation_only and 'execution-worker' in (args.component or []))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get('ok') else 1
if __name__ == '__main__':
    raise SystemExit(main())
