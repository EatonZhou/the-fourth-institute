""":module: platform.core"""
import json
import os
from pathlib import Path
import socket
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


def test_migration_preserves_open_files_socket_and_retries(tmp_path):
    """实例目录迁移先给计划再执行，保留已打开的文件和 socket，重复执行报已迁移"""
    source = tmp_path / "old"
    destination = tmp_path / "new"
    source.mkdir()
    (source / "state").write_text("before")
    identity = source.stat().st_ino
    with (source / "state").open("a+") as stream, socket.socket(socket.AF_UNIX) as server:
        server.bind(str(source / "rpc.sock"))
        server.listen(2)
        assert storage.migrate(source, destination)["status"] == "planned"
        assert not destination.exists()
        assert storage.migrate(source, destination, apply=True)["status"] == "migrated"
        stream.write("after")
        stream.flush()
        assert destination.stat().st_ino == identity
        assert (destination / "state").read_text() == "beforeafter"
        for root in (source, destination):
            with socket.socket(socket.AF_UNIX) as client:
                client.connect(str(root / "rpc.sock"))
                accepted, address = server.accept()
                accepted.close()
        assert storage.migrate(source, destination, apply=True)["status"] == "already_migrated"


def test_migration_refuses_overwrite_and_rolls_back_failed_exchange(tmp_path, monkeypatch):
    """实例目录迁移拒绝覆盖已有目标，路径交换失败时回滚原目录"""
    source = tmp_path / "old"
    destination = tmp_path / "new"
    source.mkdir()
    destination.mkdir()
    with pytest.raises(ValueError, match="absent"):
        storage.migrate(source, destination, apply=True)
    destination.rmdir()
    def fail(*args):
        raise OSError("exchange unsupported")
    monkeypatch.setattr(storage, "exchange_paths", fail)
    with pytest.raises(OSError, match="unsupported"):
        storage.migrate(source, destination, apply=True)
    assert source.is_dir() and not source.is_symlink()
    assert not os.path.lexists(destination)


def test_runtime_files_are_ignored_by_git_snapshot_and_source_detector(tmp_path, monkeypatch):
    """portal_instances 下的运行文件被 git 忽略，也不影响源码构建 ID"""
    from scripts import portal_dev_generation
    repo = Path(__file__).resolve().parents[2]
    (tmp_path / ".gitignore").write_text((repo / ".gitignore").read_text())
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "application.py").write_text("print('source')\n")
    runtime = tmp_path / "portal_instances" / "dev"
    runtime.mkdir(parents=True)
    (runtime / "generated.py").write_text("print('runtime')\n")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    ignored = subprocess.run(["git", "-C", str(tmp_path), "check-ignore", "portal_instances/dev/generated.py"], capture_output=True, text=True, check=True)
    assert "generated.py" in ignored.stdout
    assert all(
        "portal_instances" not in item.parts
        for item in portal_dev_generation.source_paths(tmp_path)
    )
    first = portal_dev_generation.source_build_id(tmp_path)
    (runtime / "generated.py").write_text("print('changed runtime')\n")
    assert portal_dev_generation.source_build_id(tmp_path) == first


def test_custom_nested_runtime_is_excluded_even_when_git_is_unavailable(tmp_path, monkeypatch):
    """没有 git 时，嵌在源码树内的自定义实例目录仍被排除在源码之外"""
    from scripts import portal_dev_generation
    root = tmp_path / "scripts" / "runtime"
    root.mkdir(parents=True)
    (root / "worker.py").write_text("runtime\n")
    (tmp_path / "scripts" / "application.py").write_text("source\n")
    configure_instance_root(root, monkeypatch)
    monkeypatch.setattr(portal_dev_generation.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no git")))
    assert portal_dev_generation.source_paths(tmp_path) == [tmp_path / "scripts" / "application.py"]


def test_python_command_uses_saved_instance_configuration(tmp_path, monkeypatch):
    """实例命令行遵循安装配置，不由测试工作树名称推测目标。"""
    repo = Path(__file__).resolve().parents[2]
    configure_instance_root(tmp_path / "runtime", monkeypatch)
    monkeypatch.setenv("PORTAL_INSTANCES_ROOT", str(tmp_path / "obsolete-env"))
    python = subprocess.run(["python3", "-B", str(repo / "management_common/instance_paths.py"), "--instance", "dev"], check=True, capture_output=True, text=True)
    assert python.stdout.strip() == str(tmp_path / "runtime/dev")


def test_deployment_coordinator_installs_rendered_units_on_every_entrypoint(tmp_path, monkeypatch):
    """每个部署入口都安装填好实例路径的 systemd 单元文件，并只重载一次"""
    from scripts import portal_service_units as deployment
    source = tmp_path / "source"
    (source / "systemd").mkdir(parents=True)
    units = tmp_path / "units"
    configure_instance_root(tmp_path / "state", monkeypatch)
    monkeypatch.setattr(deployment, "DEV_ROOT", source)
    monkeypatch.setattr(deployment, "USER_UNIT_ROOT", units)
    monkeypatch.setattr(deployment, "DEV_WEB_UNIT_SOURCE", source / "web.service")
    calls = []
    monkeypatch.setattr(deployment, "_run", lambda *args, **kwargs: calls.append(args))
    names = set(deployment.RELEASE_UNITS + deployment.DEV_UNITS + deployment.DEV_AUX_UNITS)
    names.update(deployment.DEV_WEB_DROPINS.values())
    names.update(deployment.provider_units())
    template = "[Service]\nExecStart=@PORTAL_INSTANCES_ROOT@/dev/runtime-venv/bin/python\n"
    for name in names:
        (source / "systemd" / name).write_text(template)
    (source / "web.service").write_text(template)
    (source / "config/systemd").mkdir(parents=True)
    (source / "config/systemd" / f"{deployment.CODEX_TEMPLATE}.in").write_text(template)
    for install in (deployment.install_dev_units, deployment.install_units, deployment.install_units):
        calls.clear()
        result = install()
        assert result["ok"]
        assert calls == [('systemctl', '--user', 'daemon-reload')]
        assert len({entry['unit'] for entry in result['installed']}) == len(result['installed'])
        for entry in result["installed"]:
            installed = units / entry["unit"]
            assert not installed.is_symlink()
            assert "@PORTAL_INSTANCES_ROOT@" not in installed.read_text()
            assert str(tmp_path / "state") in installed.read_text()


def test_every_deployed_portal_process_routes_instructions_over_owner_sockets() -> None:
    """部署的 Portal 进程（含 Release Web）按拥有服务的 socket 路由指令，Release Web 单元随发布安装

    Release Web 单元与各服务单元都设置 PORTAL_ISA_ROUTING=socket；发布流程安装 Release Web 单元，使其环境随发布的源码更新。

    :module: operations.deployment
    :design: decision_08bb2d9141f9, decision_898b8911a2ff
    """
    from scripts import portal_service_units as deployment

    repo = Path(__file__).resolve().parents[2]
    assert deployment.RELEASE_WEB_SERVICE in deployment.RELEASE_UNITS
    for name in (
        deployment.RELEASE_WEB_SERVICE, *deployment.RELEASE_SERVICES[1:],
        *[unit for unit in deployment.DEV_UNITS if unit != "portal-dev-nats.service" and unit.endswith(".service")],
        *deployment.DEV_WEB_DROPINS.values(),
    ):
        assert "Environment=PORTAL_ISA_ROUTING=socket" in (repo / "systemd" / name).read_text(), name


def test_user_router_reads_the_postgres_cutover_environment() -> None:
    """用户 Router 单元读取 PostgreSQL 环境文件并由 Dev 辅助单元管理

    :module: operations.deployment
    :importance: P1
    """
    repo = Path(__file__).resolve().parents[2]
    unit = (repo / "systemd" / "portal-user-router.service").read_text()

    assert (
        "EnvironmentFile=-@PORTAL_POSTGRES_ENV_FILE@"
        in unit
    )
    assert (
        "ExecStart=@PORTAL_INSTANCES_ROOT@/dev/runtime-venv/bin/python"
        in unit
    )
    from scripts.portal_source_layout import DEV_AUX_UNITS
    assert "portal-user-router.service" in DEV_AUX_UNITS


def test_standalone_deployment_commands_bootstrap_imports(tmp_path):
    """部署脚本在任意目录且无 PYTHONPATH 时也能独立运行"""
    repo = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    for name in ("restart_portal_release.py", "portal_services.py", "portal_instance_storage.py"):
        result = subprocess.run(["python3", "-B", str(repo / "scripts" / name), "--help"], cwd=tmp_path, env=environment, capture_output=True, text=True)
        assert result.returncode == 0, (name, result.stderr)
        assert "usage:" in result.stdout
