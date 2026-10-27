""":module: platform.core"""
import json
import os
from pathlib import Path
import subprocess

import pytest

from management_common import instance_paths, portal_paths
from scripts import portal_instance_storage as storage


def configure_instance_root(root, monkeypatch):
    root.mkdir(parents=True, exist_ok=True)
    config = root / "test-portal-paths.json"
    values = portal_paths.path_values()
    values["instances_root"] = str(root)
    config.write_text(json.dumps(values))
    monkeypatch.setattr(portal_paths, "CONFIG_PATH", config)
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", str(config))


def test_worker_manager_uses_configured_instance_directory(tmp_path, monkeypatch):
    """执行 Worker 管理器的代次文件和健康文件位于配置的实例目录下"""
    from management_portal.execution.worker_manager import ExecutionWorkerManager

    configure_instance_root(tmp_path, monkeypatch)
    monkeypatch.setenv("PORTAL_INSTANCE", "dev")
    for name in (
        "PORTAL_EXECUTION_WORKER_GENERATION_ENV",
        "PORTAL_EXECUTION_WORKER_COMPONENT_GENERATIONS",
        "PORTAL_EXECUTION_WORKER_HEALTH_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    manager = ExecutionWorkerManager()

    assert manager.generation_env == tmp_path / "dev" / "source-generation.env"
    assert manager.component_generations == tmp_path / "dev" / "component-generations.json"
    assert manager.health_path == tmp_path / "dev" / "execution-worker-health.json"


def test_instance_config_override_and_source_exclusion(tmp_path, monkeypatch):
    """实例目录先读统一配置再读环境；相对值按安装根解析，实例状态与源码按真实路径区分。

    :design: decision_15aab18c0ab3
    """
    config = tmp_path / "paths.json"
    values = {key: str(tmp_path / key) for key in portal_paths.KEYS}
    values["instances_root"] = str(tmp_path / "runtime")
    config.write_text(json.dumps(values))
    monkeypatch.setattr(portal_paths, "CONFIG_PATH", config)
    monkeypatch.setattr(portal_paths, "PORTAL_ROOT", tmp_path)
    monkeypatch.delenv("PORTAL_INSTANCES_ROOT", raising=False)
    assert instance_paths.instance_root() == tmp_path / "runtime"
    assert instance_paths.is_instance_state(tmp_path / "runtime" / "state.py", tmp_path)
    assert instance_paths.is_instance_state(tmp_path / "portal_instances" / "state.py", tmp_path)
    assert not instance_paths.is_instance_state(tmp_path / "scripts" / "source.py", tmp_path)
    candidate_source = tmp_path / "runtime/release/candidates/isolated/source"
    candidate_source.mkdir(parents=True)
    assert not instance_paths.is_instance_state(candidate_source / "requirements-portal-runtime.txt", candidate_source)
    assert instance_paths.is_instance_state(candidate_source / "portal_instances/state.json", candidate_source)
    assert instance_paths.is_instance_state(tmp_path / "runtime/dev/state.json", candidate_source)
    source_alias = tmp_path / "source-alias"
    source_alias.symlink_to(candidate_source, target_is_directory=True)
    assert not instance_paths.is_instance_state(source_alias / "scripts/source.py", source_alias)
    state_alias = candidate_source / "state-alias"
    state_alias.symlink_to(tmp_path / "runtime/dev", target_is_directory=True)
    assert instance_paths.is_instance_state(state_alias / "state.json", candidate_source)
    monkeypatch.setenv("PORTAL_INSTANCES_ROOT", str(tmp_path / "alternate"))
    assert instance_paths.instance_root() == tmp_path / "runtime"  # Saved configuration wins.
    config.unlink()
    assert instance_paths.instance_root() == tmp_path / "alternate"
    monkeypatch.setenv("PORTAL_INSTANCES_ROOT", "relative/path")
    monkeypatch.chdir(tmp_path.parent)
    assert instance_paths.instance_root() == tmp_path / "relative/path"
    for value in ("", "/tmp/invalid\npath"):
        monkeypatch.setenv("PORTAL_INSTANCES_ROOT", value)
        with pytest.raises(ValueError, match="nonempty path"):
            instance_paths.instance_root()


def test_unit_install_materializes_configuration_without_editing_source(tmp_path, monkeypatch):
    """安装 systemd 单元时生成填好实例路径的实体文件，不改动模板"""
    configure_instance_root(tmp_path / "state", monkeypatch)
    source = tmp_path / "template"
    source.write_text("[Service]\nExecStart=@PORTAL_INSTANCES_ROOT@/dev/runtime-venv/bin/python\n")
    destination = tmp_path / "installed.service"
    destination.symlink_to(source)
    storage.install_unit(source, destination)
    assert not destination.is_symlink()
    assert "@PORTAL_INSTANCES_ROOT@" in source.read_text()
    assert f"Environment=PORTAL_INSTANCES_ROOT={tmp_path}/state" in destination.read_text()
    assert f"ExecStart={tmp_path}/state/dev/" in destination.read_text()


def test_runtime_files_are_ignored_by_git(tmp_path):
    """portal_instances 下的运行文件被 git 忽略"""
    repo = Path(__file__).resolve().parents[2]
    (tmp_path / ".gitignore").write_text((repo / ".gitignore").read_text())
    runtime = tmp_path / "portal_instances" / "dev"
    runtime.mkdir(parents=True)
    (runtime / "generated.py").write_text("print('runtime')\n")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    ignored = subprocess.run(["git", "-C", str(tmp_path), "check-ignore", "portal_instances/dev/generated.py"], capture_output=True, text=True, check=True)
    assert "generated.py" in ignored.stdout


def test_python_command_uses_saved_instance_configuration(tmp_path, monkeypatch):
    """实例命令行遵循安装配置，不由测试工作树名称推测目标。"""
    repo = Path(__file__).resolve().parents[2]
    configure_instance_root(tmp_path / "runtime", monkeypatch)
    monkeypatch.setenv("PORTAL_INSTANCES_ROOT", str(tmp_path / "obsolete-env"))
    python = subprocess.run(["python3", "-B", str(repo / "management_common/instance_paths.py"), "--instance", "dev"], check=True, capture_output=True, text=True)
    assert python.stdout.strip() == str(tmp_path / "runtime/dev")


def test_standalone_deployment_commands_bootstrap_imports(tmp_path):
    """部署脚本在任意目录且无 PYTHONPATH 时也能独立运行"""
    repo = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    for name in ("portal_services.py",):
        result = subprocess.run(["python3", "-B", str(repo / "scripts" / name), "--help"], cwd=tmp_path, env=environment, capture_output=True, text=True)
        assert result.returncode == 0, (name, result.stderr)
        assert "usage:" in result.stdout
