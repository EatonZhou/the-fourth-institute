"""Host-data and credential isolation for the local test framework."""
from __future__ import annotations

import os
from importlib import import_module
from pathlib import Path

from management_common.portal_paths import path as portal_path

REPOSITORY = Path(__file__).resolve().parents[2]


def run_root() -> Path:
    """Script artifacts share the staging root owned by the installation config."""
    return portal_path('staging_root') / 'shared' / 'script_runs'


def credential_paths(home: Path | None = None) -> list[Path]:
    """Only paths, never credential contents; include real targets of symlinks."""
    from management_common.instance_paths import accounts_root, secrets_dir

    from scripts import portal_release

    home = home or Path.home()
    staging = portal_path("staging_root")
    paths = [home / p for p in (
        '.config/tfi', '.tfi/secrets', '.ssh', '.gnupg', '.git-credentials',
        '.netrc', '.pgpass', '.config/gh', '.config/restic', '.config/git/credentials',
        '.config/systemd/user', '.tfi/portal/authorization-principals',
    )]
    from management_common.site_compatibility import compatibility_value
    configured = compatibility_value("test_framework.credential_paths", [])
    if not isinstance(configured, list) or any(not isinstance(p, str) for p in configured):
        raise ValueError("Configured credential paths must be a string list")
    paths.extend((home / p).expanduser() for p in configured)
    paths.extend((accounts_root(), secrets_dir()))
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    # Each Provider names its own credential and session stores.
    for definition in AGENT_PROVIDERS.values():
        for pattern in definition.credential_globs:
            paths.extend(home.glob(pattern))
        if definition.credential_paths_provider:
            module, name = definition.credential_paths_provider.rsplit('.', 1)
            paths.extend(getattr(import_module(module), name)())
    for repo in (REPOSITORY, portal_release.RELEASE_ROOT):
        instances = repo / 'portal_instances'
        if instances.exists():
            paths.extend(instances.glob('*/*.env'))
            paths.extend(instances.glob('*/*/*.env'))
        paths.extend(repo.glob('.env*'))
    # Prevent access to trusted local RPC, database and provider sockets.
    paths.extend((Path('/run/user') / str(os.getuid()), Path('/run/postgresql'),
                  staging / 'portal_locks', staging / 'shared' / 'portal_runtime'))
    from management_common.instance_paths import instance_root
    from management_portal.resources.config import resource_rpc_signing_key_file, resource_service_socket
    from management_portal.society_service.config import society_service_socket
    from management_portal.execution.config import execution_service_socket
    for instance in ('dev', 'release'):
        paths.extend((resource_rpc_signing_key_file(instance), resource_service_socket(instance),
                      society_service_socket(instance), execution_service_socket(instance)))
    for root in {instance_root(), staging / 'portal_instances', REPOSITORY / 'portal_instances'}:
        for path in root.glob('*/*'):
            if path.is_socket() or path.suffix in {'.env', '.key'}:
                paths.append(path)
    paths.append(staging / 'shared' / 'portal_locks')
    # Mount canonical targets once; every symlink alias then sees that mask.
    existing = {p.resolve() for p in paths if p.exists()}
    return sorted((p for p in existing if not any(p != parent and p.is_relative_to(parent) for parent in existing)), key=str)


def isolation_args(*, network: bool) -> list[str]:
    """The bwrap prefix every sandboxed process uses: own PID/IPC/UTS namespaces, a private /tmp."""
    args = ['bwrap', '--die-with-parent', '--unshare-pid', '--unshare-ipc', '--unshare-uts',
            '--new-session', '--cap-drop', 'ALL', '--bind', '/', '/',
            '--dev', '/dev', '--proc', '/proc', '--ro-bind', '/sys', '/sys', '--tmpfs', '/tmp']
    return args if network else [*args, '--unshare-net']


def mask_args(paths) -> list[str]:
    """Hide directories behind an empty tmpfs and files behind /dev/null, parents first."""
    args: list[str] = []
    for path in sorted({Path(p) for p in paths}, key=lambda p: len(str(p))):
        if path.exists():
            args += ['--tmpfs', str(path)] if path.is_dir() else ['--ro-bind', '/dev/null', str(path)]
    return args

