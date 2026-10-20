#!/usr/bin/env python3
"""Commit selected Dev paths on main and fast-forward the release branch to that commit."""
from __future__ import annotations
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_INSTALLATION_ROOT = Path(__file__).resolve().parents[1]
if str(_INSTALLATION_ROOT) not in sys.path:
    sys.path.insert(0, str(_INSTALLATION_ROOT))
sys.dont_write_bytecode = True
from management_common.instance_paths import instance_root
from management_common.portal_paths import resolve_directory
from management_common.site_config import read_site_config
from management_common.file_lock import lock_stream
from management_common.process_runtime import run_process
from scripts import portal_release, portal_dev_commit, portal_services, portal_publication_batch as batch
from scripts.portal_source_layout import SHARED_ROOT

ROOT = SHARED_ROOT
LOG_ROOT = instance_root() / 'release/publication-logs'

# A Lineage page is one browser runtime, even though its implementation is
# split across an HTML shell and lazily loaded modules. Publishing only one
# member can leave the Release DOM and JavaScript contracts at different
# revisions, so selecting any member captures the whole compatibility unit.
LINEAGE_WORKBENCH_RELEASE_UNIT = (
    'management_portal/lineage.html',
    'management_portal/hatg-panel.html',
    'management_portal/static/lineage_workbench.css',
    'management_portal/static/lineage_workbench.js',
    'management_portal/static/lineage_workbench_watchboard.js',
    'management_portal/static/lineage_workbench_state.js',
    'management_portal/static/lineage_workbench_controller.js',
    'management_portal/static/portal_lineage_catalogs.js',
    'management_portal/static/portal_hatg_loader.js',
    'management_portal/static/portal_resident_menu.js',
    'management_portal/static/portal_resident_menu.css',
    'management_portal/static/portal_scope_menu.js',
    'management_portal/static/hatg_panel.css',
    'management_portal/static/hatg_panel.js',
    'management_portal/static/hatg_panel_features.js',
    'management_portal/static/hatg_panel_state.js',
    'management_portal/static/lineage_program.js',
    'management_portal/static/lineage_primitive_definitions.json',
    'management_portal/static/lineage_primitive_inspector.js',
    'management_portal/static/lineage_invocation_inspector.js',
    'management_portal/static/lineage_element_fields.js',
    'management_portal/static/lineage_graph.js',
    'management_portal/static/lineage_function_blocks.css',
)
RELEASE_PATH_UNITS = (LINEAGE_WORKBENCH_RELEASE_UNIT,)


@dataclass(frozen=True)
class PublishOptions:
    message: str
    paths: tuple[str, ...] = ()
    all_files: bool = False
    origin: str = 'direct'
    wall_job: str = ''
    blocks: tuple[str, ...] = ()


def expand_release_unit_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Close explicitly selected paths over browser compatibility units."""

    expanded = {str(Path(path).as_posix()).rstrip('/') for path in paths}
    for unit in RELEASE_PATH_UNITS:
        if any(
            member == selected or member.startswith(selected + '/')
            for selected in expanded
            for member in unit
        ):
            expanded.update(member for member in unit if (ROOT / member).is_file())
    return tuple(sorted(expanded))


@contextmanager
def switch_lock():
    path = instance_root() / 'portal-publish.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        with lock_stream(stream):
            yield


def base_commit() -> str:
    return portal_release.released_commit() or portal_dev_commit.git(ROOT, 'rev-parse', 'HEAD').decode().strip()


def prepare_runtime():
    if sys.platform.startswith('linux'):
        subprocess.run([str(ROOT / 'scripts/prepare_portal_release_services')], check=True, stdout=sys.stderr)
    else:
        portal_services._prepare_units('release')


def contains_commit(descendant: str, ancestor: str) -> bool:
    return bool(descendant and ancestor) and subprocess.run(
        ['git', 'merge-base', '--is-ancestor', ancestor, descendant], cwd=ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0


def push_remotes(root: Path, commit: str) -> dict:
    """Push the published Dev commit to every configured remote.

    The publication commit is pushed to the Dev branch of the same name on
    each remote, without force.  A rejected or unreachable remote is reported and does not
    undo the Release update that already happened.
    """
    branch = portal_dev_commit.git(root, 'rev-parse', '--abbrev-ref', 'HEAD').decode().strip()
    remotes = [name for name in portal_dev_commit.git(root, 'remote').decode().split() if name]
    result: dict = {'branch': branch, 'commit': commit, 'remotes': {}, 'ok': True}
    if not remotes:
        result['skipped'] = 'no remote'
        return result
    if branch in ('', 'HEAD'):
        result.update(ok=False, skipped='detached HEAD')
        return result
    env = {
        **os.environ,
        'GIT_TERMINAL_PROMPT': '0',
        'GIT_SSH_COMMAND': os.environ.get('GIT_SSH_COMMAND')
        or 'ssh -o BatchMode=yes -o ConnectTimeout=20',
    }
    for remote in remotes:
        try:
            completed = subprocess.run(
                ['git', 'push', remote, f'{commit}:refs/heads/{branch}'],
                cwd=root, env=env, capture_output=True, text=True, timeout=300, check=False,
            )
            ok = completed.returncode == 0
            detail = '' if ok else (completed.stderr or completed.stdout).strip()[-600:]
        except subprocess.TimeoutExpired:
            ok, detail = False, 'push timed out'
        result['remotes'][remote] = {'ok': ok, **({'error': detail} if detail else {})}
        result['ok'] = result['ok'] and ok
    return result


def publish(options: PublishOptions) -> dict:
    if not options.message.strip():
        raise ValueError('A short description of the change is required')
    if options.origin not in ('direct', 'wall'):
        raise ValueError('Unknown publication origin')
    expanded_paths = options.paths if options.all_files else expand_release_unit_paths(options.paths)
    started_at = time.time()
    # Keep Git/index preparation short so concurrent publishers can join one
    # activation. Only explicit publication commits enter the batch.
    with batch.preparation_lock(instance_root()):
        if not portal_release.is_deployed():
            raise ValueError(f'Release checkout is missing: {portal_release.RELEASE_ROOT}')
        commit = portal_dev_commit.commit(ROOT, expanded_paths, options.message, all_files=options.all_files)
        batch.submit(instance_root(), commit)
    batch.wait(instance_root())
    with switch_lock():
        base = base_commit()
        target = batch.claim(instance_root(), commit)
        if not contains_commit(target, commit):
            raise ValueError('Publication batch does not contain the requested commit')
        covered = contains_commit(base, target)
        if covered:
            target = base
        changed = portal_dev_commit.changed_paths(ROOT, base, target)
        result = {
            'schema': 'tfi.portal-publication-result.v1',
            'commit': commit, 'dev_commit': commit, 'release_commit': target,
            'coalesced': target != commit or covered,
            'parent_commit': base,
            'message': options.message,
            'requested_paths': list(options.paths), 'paths': changed, 'changed_paths': changed,
            'expanded_paths': list(expanded_paths),
            'origin': options.origin, 'wall_job': options.wall_job, 'blocks': list(options.blocks),
            'all_files': options.all_files, 'started_at': started_at,
            'ok': False,
        }
        try:
            prepare_runtime()
            # Shared services still run from the common source; Web alone uses
            # the Release checkout. No Worker environment/version admission.
            from scripts.portal_dev_source_reloader import components_for_paths
            from scripts.portal_source_layout import SHARED_COMPONENTS
            common = set(components_for_paths(set(changed))) & set(SHARED_COMPONENTS)
            result['components'] = []
            if common:
                with portal_services.operation_lock(portal_services._operation_lock_path('dev')):
                    from scripts import portal_dev_source_reloader as reloader
                    if reloader.DEPLOYED_SOURCE_STATE.exists():
                        current = reloader.component_source_states(reloader.source_state())
                        accepted = reloader.read_deployed_source_states(current)
                        common &= reloader.changed_components(accepted, current)
                    if common:
                        portal_services.restart('dev', notice_seconds=5, components=tuple(sorted(common)), units_prepared=True)
                result['components'].extend(f'shared:{c}' for c in sorted(common))
            # Release Web stops after its countdown, the release branch
            # fast-forwards to the new commit, and Web starts again on that code.
            with portal_services.operation_lock(portal_services._operation_lock_path('release')):
                status = portal_services.instance_status('release', include_schema=True) if covered else {}
                if covered and status.get('runtime_ok'):
                    status = {**status, 'ok': True}
                else:
                    status = portal_services.restart('release', notice_seconds=5, components=('web',),
                                                     release_commit=target, units_prepared=True)
                    result['components'].append('release:web')
            # The router consumes the shared credential schema too. A healthy
            # Web alone does not mean an old router can still verify its cookies.
            # It restarts only when the code it runs changed: a restart cuts
            # every connection it carries, both instances' included.
            router_changes = portal_services.user_router_changes()
            if router_changes is None or router_changes:
                router_health = portal_services.restart_user_router()
                result['components'].append('shared:user-router')
            else:
                router_health = portal_services.user_router_health()
            result['router_health'] = router_health
            ok = bool(status.get('ok')) and bool(router_health.get('ok')) and portal_release.released_commit() == target
            result.update(ok=ok, status='completed' if ok else 'failed',
                          active_commit=portal_release.released_commit(), health=status)
        except Exception as exc:
            result.update(status='failed', error=str(exc), active_commit=portal_release.released_commit())
        finally:
            result['completed_at'] = time.time()
        # Every publication commit is also pushed. Return the push outcome
        # alongside the Release result; it does not undo the Release update.
        try:
            result['push'] = push_remotes(ROOT, target)
        except Exception as exc:
            result['push'] = {'ok': False, 'error': str(exc)}
        if result.get('ok') and 'release:web' in result.get('components', []):
            try:
                from management_common.portal_release_events import record
                result['release_event'] = {'ok': True, 'sequence': record({**result, 'commit': target, 'dev_commit': target})}
            except Exception as exc:
                # A notification failure does not undo the healthy Release.
                # The caller sees it separately and can retry the publication.
                result['release_event'] = {'ok': False, 'error': str(exc)}
    # Retire obsolete status keys with the same policy used by ordinary writes.
    # Usage snapshots have their own versioned store.
    try:
        from management_portal import status_cache_store as cache
        source = cache.default_path()
        with cache.locked(source):
            entries = cache.read(source)
            current = cache.current_entries(entries)
            cache.write(source, current)
        result['status_cache'] = {'removed_entries': len(entries) - len(current)}
    except Exception as exc:
        result['status_cache'] = {'ok': False, 'error': str(exc)}
    return result


def in_publication_unit() -> bool:
    """A new session is insufficient: the publisher needs its own service cgroup."""
    if os.environ.get('PORTAL_PUBLICATION_CHILD') == '1':
        return True
    if not sys.platform.startswith('linux'):
        return False
    return any(part.startswith('portal-publish-') and part.endswith('.service')
               for part in Path('/proc/self/cgroup').read_text().replace('\n', '/').split('/'))


def detached_publication(argv: list[str]) -> int:
    if not sys.platform.startswith('linux'):
        environment = os.environ.copy()
        environment['PORTAL_PUBLICATION_CHILD'] = '1'
        configured = os.environ.get('PORTAL_POSTGRES_ENV_FILE',
                                    read_site_config().get('deployment', {}).get('postgres_env_file', ''))
        if configured:
            path = resolve_directory(configured)
            if path.is_file():
                for line in path.read_text(encoding='utf-8').splitlines():
                    key, separator, value = line.strip().removeprefix('export ').partition('=')
                    if separator and key.strip() and not key.lstrip().startswith('#'):
                        environment.setdefault(key.strip(), (shlex.split(value) or [''])[0])
        result = run_process([sys.executable, str(ROOT / 'scripts/publish_portal_release.py'), *argv],
                             cwd=ROOT, env=environment, text=True, capture_output=True)
        if result.stdout:
            print(result.stdout, end='')
        if result.stderr:
            print(result.stderr, end='', file=sys.stderr)
        return result.returncode
    configuration_environment = [
        f'--setenv={name}={resolve_directory(os.environ[name])}'
        for name in ('PORTAL_SITE_CONFIG', 'PORTAL_PATHS_CONFIG') if name in os.environ
    ]
    postgres_env_file = resolve_directory(os.environ.get(
        'PORTAL_POSTGRES_ENV_FILE',
        read_site_config().get('deployment', {}).get('postgres_env_file', '~/.config/tfi/portal-postgres.env'),
    ))
    unit = f'portal-publish-{uuid.uuid4().hex}'
    log = LOG_ROOT / f'{unit}.log'
    error_log = log.with_suffix('.stderr.log')
    log.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        'systemd-run', '--user', '--wait', '--collect', f'--unit={unit}',
        f'--working-directory={ROOT}', '-p', f'StandardOutput=append:{log}',
        '-p', f'StandardError=append:{error_log}',
        '-p', f'EnvironmentFile=-{postgres_env_file}',
        *configuration_environment,
        sys.executable, str(ROOT / 'scripts/publish_portal_release.py'),
        *argv,
    ], check=False)
    if log.exists():
        print(log.read_text(), end='')
    if error_log.exists():
        print(error_log.read_text(), end='', file=sys.stderr)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--message', default='')
    parser.add_argument('--path', action='append', default=[], help='File or directory to synchronize; repeatable, deletions included')
    parser.add_argument('--all', dest='all_files', action='store_true', help='Intentionally synchronize all Git-visible source')
    parser.add_argument('--origin', choices=('direct', 'wall'), default='direct')
    parser.add_argument('--wall-job', default='')
    parser.add_argument('--block', action='append', default=[])
    args = parser.parse_args()
    if not in_publication_unit():
        return detached_publication(sys.argv[1:])
    try:
        result = publish(PublishOptions(
            args.message, tuple(args.path), args.all_files, args.origin, args.wall_job, tuple(args.block)))
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
