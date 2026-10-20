"""Embedding management must use the serving instance's Python environment."""
from pathlib import Path
from types import SimpleNamespace

from management_portal import embedding_setup
from scripts import install_portal_embeddings


def test_runtime_python_preserves_venv_symlink(monkeypatch, tmp_path):
    base = tmp_path / 'base-python'
    base.touch()
    executable = tmp_path / 'instance-env/bin/python'
    executable.parent.mkdir(parents=True)
    executable.symlink_to(base)
    monkeypatch.setattr(embedding_setup.sys, 'executable', str(executable))
    monkeypatch.setattr(embedding_setup, 'portal_path', lambda _: tmp_path / 'unrelated-dev')
    assert embedding_setup.runtime_python() == executable
    assert embedding_setup.runtime_python() != base


def test_readiness_probes_current_interpreter(monkeypatch, tmp_path):
    executable = tmp_path / 'active-environment/bin/python'
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(embedding_setup.sys, 'executable', str(executable))
    monkeypatch.setattr(embedding_setup, 'model_directory', lambda: tmp_path)
    for name in embedding_setup.MODEL_FILES:
        (tmp_path / name).touch()
    monkeypatch.setattr(embedding_setup, 'enabled', lambda: False)
    monkeypatch.setattr(embedding_setup, 'platform_issue', lambda: None)
    monkeypatch.setattr(embedding_setup, 'job_status', lambda: {})
    calls = []
    monkeypatch.setattr(embedding_setup.process_runtime, 'run_process',
                        lambda argv, **kw: calls.append(argv) or SimpleNamespace(returncode=0))
    assert embedding_setup.readiness()['ready'] is True
    assert calls[0][0] == str(executable)


def test_detached_installer_inherits_current_interpreter_and_site(monkeypatch, tmp_path):
    monkeypatch.setattr(embedding_setup.sys, 'executable', str(tmp_path / 'instance-env/bin/python'))
    monkeypatch.setenv('PORTAL_PATHS_CONFIG', str(tmp_path / 'instance-paths.json'))
    monkeypatch.setattr(embedding_setup, 'job_file', lambda: tmp_path / 'job.json')
    monkeypatch.setattr(embedding_setup, 'platform_issue', lambda: None)
    monkeypatch.setattr(embedding_setup.threading, 'Thread', lambda **kw: SimpleNamespace(start=lambda: None))
    calls = []
    monkeypatch.setattr(embedding_setup.process_runtime, 'spawn_process',
                        lambda argv, **kw: calls.append((argv, kw)) or SimpleNamespace(pid=123))
    assert embedding_setup.start_install()['state'] == 'running'
    argv, options = calls[0]
    assert argv[0] == str(tmp_path / 'instance-env/bin/python')
    assert options['env']['PORTAL_PATHS_CONFIG'] == str(tmp_path / 'instance-paths.json')


def test_bootstrap_uses_running_environment_prefix(monkeypatch, tmp_path):
    environment = tmp_path / 'release-env'
    monkeypatch.setattr(install_portal_embeddings.sys, 'prefix', str(environment))
    monkeypatch.setattr(install_portal_embeddings.sys, 'base_prefix', str(tmp_path / 'python-base'))
    monkeypatch.setattr(install_portal_embeddings.sys, 'executable', str(environment / 'bin/python'))
    monkeypatch.setattr(install_portal_embeddings, 'job_status', lambda: {})
    states, calls = [], []
    monkeypatch.setattr(install_portal_embeddings, '_write_job', states.append)
    monkeypatch.setattr(install_portal_embeddings, 'run_process', lambda argv, **kw: calls.append(argv))
    assert install_portal_embeddings.main() == 0
    assert calls[0][0] == str(environment / 'bin/python')
    assert calls[0][-2:] == ['--venv', str(environment)]
    assert states[-1]['state'] == 'complete'


def test_system_python_is_not_modified_or_replaced_by_dev_environment(monkeypatch):
    monkeypatch.setattr(install_portal_embeddings.sys, 'prefix', install_portal_embeddings.sys.base_prefix)
    monkeypatch.setattr(install_portal_embeddings, 'job_status', lambda: {})
    states, calls = [], []
    monkeypatch.setattr(install_portal_embeddings, '_write_job', states.append)
    monkeypatch.setattr(install_portal_embeddings, 'run_process', lambda *a, **kw: calls.append(a))
    assert install_portal_embeddings.main() == 1
    assert calls == []
    assert states[-1]['state'] == 'failed'
    assert 'virtual environment' in states[-1]['error']
