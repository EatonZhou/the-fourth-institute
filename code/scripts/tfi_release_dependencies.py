"""Prepare startup dependencies; feature tools are checked when used."""
from pathlib import Path
import hashlib
import json
from scripts.tfi_instance_layout import code_root, dependencies_root

from management_common.release_dependencies import STARTUP_PROFILES, profiles
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
    if target.get('bundled_python_dependencies'):
        digest.update(b'bundled-python-lock')
        try:
            digest.update((dependencies_root(target) / 'python-wheels/requirements.lock').read_bytes())
        except FileNotFoundError as exc:
            raise RuntimeError('安装包缺少离线 Python 依赖，请重新获取完整安装包。') from exc
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
    required = [profile for profile in selected if profile in STARTUP_PROFILES]
    optional = [profile for profile in selected if profile not in STARTUP_PROFILES]
    target = state['target']
    root = Path(target['install_root'])
    script = code_root(target) / 'scripts/bootstrap_portal'

    def doctor(python):
        result = api.run_utf8_capture([python, str(script), 'doctor', *required,
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
        environment_key(target, required) if target.get('shared_code_root') else state['manifest_sha256']
    ) / 'venv'
    shared_python = environment / ('Scripts/python.exe' if api.native_tool('python').endswith('.exe') else 'bin/python')
    python = str(shared_python) if target.get('shared_code_root') and shared_python.is_file() else runtime_python(target)
    inventory = doctor(python)
    source = 'verified-existing-installation'
    if inventory is None:
        wheelhouse = dependencies_root(target) / 'python-wheels' if target.get('bundled_python_dependencies') else None
        if wheelhouse and not (wheelhouse / 'requirements.lock').is_file():
            raise RuntimeError('安装包缺少离线 Python 依赖，请重新获取完整安装包。')
        if target.get('offline_dependencies') and wheelhouse is None:
            raise RuntimeError('Bundled Python dependencies failed verification; reinstall the application package')
        report('正在从安装包准备 Portal 核心 Python 依赖，无需联网' if wheelhouse else
               '正在准备 Portal 核心 Python 依赖；可选浏览器、排版与测试工具不参与首次安装')
        environment.parent.mkdir(parents=True, exist_ok=True)
        # Never pip-install into the operator's supplied/shared interpreter.
        log = root / 'runtime/dependency-install.log'
        with log.open('a', encoding='utf-8') as stream, relay_log_progress(log):
            result = api.run_process([target['python'], str(script), 'install', *required,
                '--venv', str(environment), *(['--wheelhouse', str(wheelhouse)] if wheelhouse else [])],
                env=api.clean_environment(target), cwd=code_root(target),
                stdout=stream, stderr=stream, text=True, timeout=10800)
        if result.returncode:
            raise RuntimeError('Dependency preparation failed; inspect runtime/dependency-install.log and retry installation')
        python = str(environment / ('Scripts/python.exe' if api.native_tool('python').endswith('.exe') else 'bin/python'))
        inventory = doctor(python)
        if inventory is None:
            raise RuntimeError('Prepared core dependencies failed verification; services were not started')
        source = ('bundled-offline-wheels' if wheelhouse else
                  'shared-platform-bootstrap' if target.get('shared_code_root') else 'instance-owned-bootstrap')
    state['dependencies'] = {'status': 'verified', 'manifest_sha256': state['manifest_sha256'],
        'profiles': required, 'declared_profiles': selected,
        'optional_profiles': optional, 'optional_policy': 'user-managed; checked on use',
        'python': python, 'source': source, 'inventory': inventory}
    api.save_stage(state, 'dependencies', 'prepared')
    report('核心运行环境准备完成；可选功能的工具可在需要时自行安装' if optional else '运行环境准备完成')
