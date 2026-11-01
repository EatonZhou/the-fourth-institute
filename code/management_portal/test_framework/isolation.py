"""Host-data and credential isolation for the local test framework."""
from __future__ import annotations

import os
from importlib import import_module
from pathlib import Path

from management_common.instance_manifest import instance_runtime_root, instance_secrets_root

REPOSITORY = Path(__file__).resolve().parents[2]


def run_root() -> Path:
    """Script artifacts live in the selected instance's runtime directory."""
    return instance_runtime_root() / 'script_runs'


def credential_paths(home: Path | None = None) -> list[Path]:
    """Only paths, never credential contents; include real targets of symlinks."""
    from management_common.host_instances import user_home

    from scripts import portal_release

    home = home or Path.home()
    paths = [home / p for p in (
        '.config/tfi', '.tfi/secrets', '.ssh', '.gnupg', '.git-credentials',
        '.netrc', '.pgpass', '.config/gh', '.config/restic', '.config/git/credentials',
        '.config/systemd/user', '.tfi/portal/authorization-principals',
    )]
    paths.append(instance_secrets_root())
    from management_portal.execution.account_registrations import registrations
    paths.extend(Path(row["config"]["account_dir"]) for row in registrations()
                 if row["kind"] == "subscription_login")
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    # Each Provider names its own credential and session stores.
    for definition in AGENT_PROVIDERS.values():
        for pattern in definition.credential_globs:
            paths.extend(home.glob(pattern))
            paths.extend(user_home().glob(pattern))
        if definition.credential_paths_provider:
            module, name = definition.credential_paths_provider.rsplit('.', 1)
            paths.extend(getattr(import_module(module), name)())
    for repo in (REPOSITORY, portal_release.RELEASE_ROOT):
        paths.extend(repo.glob('.env*'))
    # Prevent access to trusted local RPC, database and provider sockets, the
    # signing keys and the locks: all of them live in the instance runtime.
    paths.extend((Path('/run/user') / str(os.getuid()), Path('/run/postgresql'), instance_runtime_root()))
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

