"""Service launch policy for one validated instance, independent of installers."""
from __future__ import annotations

import os
from pathlib import Path
import sys

from management_common import code_environment
from management_common.instance_manifest import InstanceManifest, load

from management_common.secret_environment import secret_environment

MODULES = {
    'resource': 'management_portal.resources.server',
    'execution': 'management_portal.execution.service',
    'society': 'management_portal.society_service.server',
    'worker': 'management_portal.execution.worker_manager',
    'kimi': 'management_portal.kimi_code_server',
    'kimi-code': 'management_portal.kimi_code_server',
    'claude': 'management_portal.claude_code_daemon',
    'claude-code': 'management_portal.claude_code_daemon',
    'gateway': 'management_portal.portal_deployment_router',
    'ai-socket-relay': 'management_portal.ai_socket_relay',
}



def clean_environment(instance: InstanceManifest, *, component: str = '', postgres: bool = False) -> dict[str, str]:
    """Keep the OS user environment and select exactly one instance.

    Transport/process roles are launch policy. Paths and dependency locations are
    obtained from the manifest and code, never inherited from another instance.
    """
    allowed = {'HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'TMPDIR', 'TEMP', 'TMP',
               'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT',
               'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROCESSOR_ARCHITECTURE',
               'PROCESSOR_ARCHITEW6432', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'}
    env = {k: v for k, v in os.environ.items() if k in allowed or k.startswith('LC_')}
    temporary = str(instance.directory('runtime') / 'tmp')
    env.update(TMPDIR=temporary, TEMP=temporary, TMP=temporary)
    env.update(PORTAL_INSTANCE_ROOT=str(instance.root), PYTHONNOUSERSITE='1',
               PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    if component:
        env.update(instance.service_environment(component))
    if postgres:
        library = instance.document['dependencies']['postgres']['lib']
        if os.name == 'nt':
            env['PATH'] = library + os.pathsep + env.get('PATH', '')
        else:
            env['DYLD_LIBRARY_PATH' if sys.platform == 'darwin' else 'LD_LIBRARY_PATH'] = library
        return env
    if component != 'nats':
        node = code_environment.native_binary(instance.service_code(component) if component else instance.code_root(), 'node')
        env['PATH'] = str(node.parent) + os.pathsep + env.get('PATH', '')
    # Private environment values are read at process startup, not written into
    # user units or public configuration. They are never returned by status APIs.
    database = instance.directory('secrets') / 'postgres.env'
    if database.is_file():
        env.update(secret_environment(instance, 'postgres.env'))
    from management_common.portal_network import inherited_proxy_environment, saved_proxy_environment
    proxies = saved_proxy_environment(instance)
    env.update(inherited_proxy_environment(instance) if proxies is None else proxies)
    env.update(PORTAL_RESOURCE_SERVICE_MODE='client', PORTAL_SOCIETY_SERVICE_MODE='client',
               PORTAL_ISA_ROUTING='socket',
               PORTAL_RESOURCE_DATABASE_SCHEMA='resource_release',
               PORTAL_NATS_URL=f"nats://127.0.0.1:{instance.document['network']['nats_port']}",
               PORTAL_URL=instance.internal_web_origin())
    if component in {'resource', 'society'}:
        env[f'PORTAL_{component.upper()}_SERVICE_MODE'] = 'local'
        env[f'PORTAL_{component.upper()}_SERVICE_PROCESS'] = '1'
    return env


def component_command(instance: InstanceManifest, component: str) -> tuple[list[str], dict[str, str]]:
    code = instance.service_code(component)
    env = clean_environment(instance, component=component, postgres=component == 'postgres')
    if component == 'postgres':
        binary = Path(instance.document['dependencies']['postgres']['bin']) / ('postgres.exe' if os.name == 'nt' else 'postgres')
        command = [str(binary), '-D', str(instance.directory('database')),
                   '-p', str(instance.document['network']['postgres_port']), '-c', 'max_connections=200']
        command += ['-c', 'listen_addresses=127.0.0.1'] if os.name == 'nt' else ['-k', str(instance.directory('runtime') / 's'), '-c', 'listen_addresses=']
        return command, env
    if component == 'nats':
        return [str(code_environment.native_binary(code, 'nats')), '-a', '127.0.0.1', '-p', str(instance.document['network']['nats_port'])], env
    if component in {'claude', 'claude-code'}:
        runtime = code_environment.provider_entry('claude_runtime_current', instance)
        executable = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        package = code_environment.provider_entry('claude_app_server_current', instance)
        if not executable.is_file() or not (package / 'management_portal/claude_code_daemon.py').is_file():
            raise FileNotFoundError('Prepare the Claude SDK and app-server package before startup')
        return [str(executable), '-m', 'management_portal.claude_code_daemon'], env
    python = str(code_environment.runtime_python(code))
    program = instance.service_program(component)
    if program is not None:
        entrypoint = instance.resolve(program['entrypoint'])
        if not entrypoint.is_file() or not working_directory(instance, component).is_dir():
            raise FileNotFoundError('Registered service program or working directory is unavailable')
        env['PYTHONPATH'] = os.pathsep.join((str(code), str(entrypoint.parent)))
        return [python, str(entrypoint), *program['arguments']], env
    if component.startswith('codex-app-server@'):
        from management_common.provider_account_paths import validate_identity
        account = validate_identity(component.partition('@')[2])
        binary = code_environment.provider_entry('codex_binary', instance)
        if os.name == 'nt':
            binary /= 'codex.cmd'
        if not binary.is_file():
            raise FileNotFoundError('Prepare the Codex runtime before startup')
        return [python, '-m', 'management_portal.codex_app_server_host',
                '--socket', str(instance.directory('runtime') / 's' / f'codex-{account}.sock'),
                '--binary', str(binary), '--account-id', account], env
    if component in {'web', 'dev-web'}:
        deployment = 'dev' if component == 'dev-web' else 'release'
        return [python, '-m', 'management_portal.web_server', '--host', ('127.0.0.1' if len(instance.deployments) > 1 else instance.document['network']['bind']),
                '--port', str(instance.deployments[deployment]['web_port'])], env
    try:
        module = MODULES[component]
    except KeyError:
        raise ValueError(f'No launcher registered for instance component: {component}') from None
    from management_common import loaded_modules
    env[loaded_modules.DIRECTORY_ENV] = str(loaded_modules.directory(instance.root))
    env[loaded_modules.COMPONENT_ENV] = component
    return [python, '-m', module], env


def prepare_runtime(instance: InstanceManifest) -> None:
    for name in ('s', 'tmp'):
        path = instance.directory('runtime') / name
        if path.is_symlink():
            raise ValueError('Instance runtime subdirectory cannot be redirected')
        path.mkdir(parents=True, exist_ok=True, mode=0o700)


def working_directory(instance: InstanceManifest, component: str) -> Path:
    program = instance.service_program(component)
    if program is not None:
        return instance.resolve(program['working_directory'])
    return (code_environment.provider_entry('claude_app_server_current', instance)
            if component in {'claude', 'claude-code'} else instance.service_code(component))


def launch(root: Path, component: str) -> None:
    instance = load(root)
    prepare_runtime(instance)
    command, environment = component_command(instance, component)
    os.chdir(working_directory(instance, component))
    os.execve(command[0], command, environment)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', required=True)
    args = parser.parse_args()
    launch(load().root, args.component)


if __name__ == '__main__':
    main()
