"""Prepare selected dependencies inside an owned installation, before services."""
from pathlib import Path
import hashlib
import json
from scripts.tfi_instance_layout import code_root, dependencies_root

from management_common.release_dependencies import profiles
from management_common.installation_progress import report, relay_log_progress


def runtime_python(target):
    import json
    root = Path(target['install_root'])
    state_path = root / 'installation.json'
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding='utf-8'))
        record = state.get('dependencies', {})
        if (state.get('target') == target and record.get('status') == 'verified'
                and record.get('manifest_sha256') == state.get('manifest_sha256')):
            value = record['python']
            if value != target['python'] and not Path(value).absolute().is_relative_to(dependencies_root(target)):
                raise ValueError('Dependency interpreter escapes the owned installation')
            return value
    return target['python']


def environment_key(target, selected):
    """Only dependency inputs, not business seed data, identify shared packages."""
    source = code_root(target)
    files = sorted(source.glob("requirements*.txt")) + [source / "scripts/bootstrap_portal"]
    files += [path for path in (source / "package.json",
              source / "management_portal/test_framework/tau_runtime/package.json",
              source / "management_portal/dsh_runtime/package.json") if path.is_file()]
    digest = hashlib.sha256(json.dumps(sorted(selected)).encode())
    for path in files:
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare(api, state, manifest):
    target = state['target']
    if target.get('shared_code_root'):
        with api.installation_lock(dependencies_root(target) / '.locks/python'):
            return _prepare(api, state, manifest)
    return _prepare(api, state, manifest)


def _prepare(api, state, manifest):
    selected = profiles(manifest)
    if not selected:
        return
    target = state['target']
    root = Path(target['install_root'])
    script = code_root(target) / 'scripts/bootstrap_portal'

    def doctor(python):
        result = api.run_utf8_capture([python, str(script), 'doctor', *selected,
            '--venv', str(Path(python).parent.parent), '--tools-only', '--json'],
            env=api.clean_environment(target), cwd=code_root(target), timeout=540)
        if result.returncode == 0:
            import json
            inventory = json.loads(result.stdout)
            if not inventory or not all(row.get('ok') is True for row in inventory):
                raise ValueError('Dependency doctor returned an invalid inventory')
            return inventory
        return None

    environment = dependencies_root(target) / (
        environment_key(target, selected) if target.get('shared_code_root') else state['manifest_sha256']
    ) / 'venv'
    shared_python = environment / ('Scripts/python.exe' if api.native_tool('python').endswith('.exe') else 'bin/python')
    python = str(shared_python) if target.get('shared_code_root') and shared_python.is_file() else runtime_python(target)
    inventory = doctor(python)
    source = 'verified-existing-installation'
    if inventory is None:
        report('正在准备首次使用的 Python、浏览器与排版工具，需要联网；详细过程写入安装日志')
        environment.parent.mkdir(parents=True, exist_ok=True)
        # Never pip-install into the operator's supplied/shared interpreter.
        log = root / 'runtime/dependency-install.log'
        with log.open('a', encoding='utf-8') as stream, relay_log_progress(log):
            result = api.run_process([target['python'], str(script), 'install', *selected,
                '--venv', str(environment)], env=api.clean_environment(target), cwd=code_root(target),
                stdout=stream, stderr=stream, text=True, timeout=10800)
        if result.returncode:
            raise RuntimeError('Dependency preparation failed; inspect runtime/dependency-install.log and retry installation')
        python = str(environment / ('Scripts/python.exe' if api.native_tool('python').endswith('.exe') else 'bin/python'))
        inventory = doctor(python)
        if inventory is None:
            raise RuntimeError('Prepared dependencies failed selected-profile verification; services were not started')
        source = 'shared-platform-bootstrap' if target.get('shared_code_root') else 'instance-owned-bootstrap'
    state['dependencies'] = {'status': 'verified', 'manifest_sha256': state['manifest_sha256'],
        'profiles': selected, 'python': python, 'source': source, 'inventory': inventory}
    api.save_stage(state, 'dependencies', 'prepared')
    report('运行环境准备完成')
