"""Prepare selected dependencies inside an owned installation, before services."""
from pathlib import Path

from management_common.release_dependencies import profiles


def runtime_python(target):
    import json
    root = Path(target['install_root'])
    state_path = root / 'installation.json'
    if state_path.is_file():
        state = json.loads(state_path.read_text())
        record = state.get('dependencies', {})
        if (state.get('target') == target and record.get('status') == 'verified'
                and record.get('manifest_sha256') == state.get('manifest_sha256')):
            value = record['python']
            if value != target['python'] and not Path(value).absolute().is_relative_to(root / 'runtime/dependencies'):
                raise ValueError('Dependency interpreter escapes the owned installation')
            return value
    return target['python']


def prepare(api, state, manifest):
    selected = profiles(manifest)
    if not selected:
        return
    target = state['target']
    root = Path(target['install_root'])
    script = root / 'code/scripts/bootstrap_portal'

    def doctor(python):
        result = api.run_process([python, str(script), 'doctor', *selected,
            '--venv', str(Path(python).parent.parent), '--tools-only', '--json'],
            env=api.clean_environment(target), cwd=root / 'code', capture_output=True,
            text=True, timeout=180)
        if result.returncode == 0:
            import json
            inventory = json.loads(result.stdout)
            if not inventory or not all(row.get('ok') is True for row in inventory):
                raise ValueError('Dependency doctor returned an invalid inventory')
            return inventory
        return None

    python = runtime_python(target)
    inventory = doctor(python)
    source = 'verified-existing-installation'
    if inventory is None:
        environment = root / 'runtime/dependencies' / state['manifest_sha256'] / 'venv'
        environment.parent.mkdir(parents=True, exist_ok=True)
        # Never pip-install into the operator's supplied/shared interpreter.
        log = root / 'runtime/dependency-install.log'
        with log.open('a', encoding='utf-8') as stream:
            result = api.run_process([target['python'], str(script), 'install', *selected,
                '--venv', str(environment)], env=api.clean_environment(target), cwd=root / 'code',
                stdout=stream, stderr=stream, text=True, timeout=3600)
        if result.returncode:
            raise RuntimeError('Dependency preparation failed; inspect runtime/dependency-install.log and retry installation')
        python = str(environment / ('Scripts/python.exe' if api.native_tool('python').endswith('.exe') else 'bin/python'))
        inventory = doctor(python)
        if inventory is None:
            raise RuntimeError('Prepared dependencies failed selected-profile verification; services were not started')
        source = 'instance-owned-bootstrap'
    state['dependencies'] = {'status': 'verified', 'manifest_sha256': state['manifest_sha256'],
        'profiles': selected, 'python': python, 'source': source, 'inventory': inventory}
    api.save_stage(state, 'dependencies', 'prepared')
