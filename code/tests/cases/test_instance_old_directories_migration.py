"""Old-layout directories go only when emptied; the backup configuration names the new layout."""
import json
from pathlib import Path

import pytest
import yaml

from scripts import migrate_instance_old_directories as old_directories
from tests.cases.instance_test_support import write_instance


def _instance(tmp_path: Path) -> Path:
    root = tmp_path / "instance"
    fake = tmp_path / "pg"
    write_instance(root, postgres={"bin": fake / "bin", "lib": fake / "lib", "share": fake / "share"})
    return root


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(old_directories, "require_offline", lambda instance, running=(): {})
    monkeypatch.setattr(old_directories, "require_protected", lambda pids: {})


def test_data_roots_with_leftovers_are_kept_and_listed(tmp_path, offline):
    root = _instance(tmp_path)
    _write(root / "data-roots/instances/deployments.json")
    _write(root / "data-roots/staging/fork.log")
    (root / "data-roots/instances/sockets").mkdir(parents=True)
    report = tmp_path / "report.json"
    assert old_directories.main(["--root", str(root), "--apply", "--report", str(report)]) == 0
    result = json.loads(report.read_text())
    assert result["data_roots"]["state"] == "blocked"
    assert result["data_roots"]["remaining"] == [str(root / "data-roots/staging/fork.log")]
    assert (root / "data-roots/staging/fork.log").is_file()
    # Old-layout files nothing reads are removed even while the owner decides on the rest.
    assert not (root / "data-roots/instances").exists()


def test_emptied_data_roots_and_old_provider_state_are_removed(tmp_path, offline):
    root = _instance(tmp_path)
    _write(root / "data-roots/instances/user-router-source.json")
    _write(root / "data-roots/instances/systemd/portal-claude-code.env")
    (root / "data-roots/instances/dev").mkdir(parents=True)
    (root / "data-roots/instances/dev/uploads").symlink_to("../uploads")
    _write(root / "data-roots/instances/dev/resource-rpc-signing.key")
    _write(root / "runtime/s/resource-rpc-signing.key")
    (root / "data-roots/staging").mkdir()
    (root / "data-roots/staging/portal_instances").symlink_to("../instances")
    (root / "provider-state").mkdir()
    _write(root / "maintenance/old.log")
    report = tmp_path / "report.json"
    assert old_directories.main(["--root", str(root), "--apply", "--report", str(report)]) == 0
    result = json.loads(report.read_text())
    assert not (root / "data-roots").exists()
    assert not (root / "provider-state").exists()
    assert (root / "maintenance/old.log").is_file()
    assert result["owner_directories"]["maintenance"]["entries"] == 1


def test_signing_key_stays_until_runtime_has_its_replacement(tmp_path):
    root = _instance(tmp_path)
    _write(root / "data-roots/instances/release/resource-rpc-signing.key")
    plan = old_directories.plan_data_roots(root)
    assert plan["state"] == "blocked"
    assert plan["remaining"] == [str(root / "data-roots/instances/release/resource-rpc-signing.key")]


def test_backup_configuration_names_new_locations(tmp_path, offline, monkeypatch):
    root = _instance(tmp_path)
    staging = root / "data-roots/staging"
    _write(root / "data/uploads/upload_1/f")
    home = Path(old_directories.pwd.getpwuid(old_directories.os.getuid()).pw_dir)
    deployment = tmp_path / "portal_paths.json"
    deployment.write_text(json.dumps({"install_root": str(root), "pg_bin": "/old/pg/bin", "data_roots": {
        "share_root": str(tmp_path / "share"), "staging_root": str(staging),
        "instances_root": str(root / "data-roots/instances"), "secrets_dir": str(root / "data-roots/secrets")}}))
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", str(deployment))
    program = _write(tmp_path / "backup/src/main.py")
    config = tmp_path / "backup/config/config.yaml"
    _write(config, yaml.safe_dump({
        "restic": {"binary": "${user_home}/.local/bin/restic"},
        "profiles": {"content": {
            "sources": [
                {"path": "${deployment.data_roots.staging_root}/shared/portal_uploads", "required": True},
                {"path": "${deployment.data_roots.staging_root}/shared/portal_cache", "required": True},
                {"path": "${deployment.data_roots.staging_root}/shared/gone", "required": False},
                {"path": "${deployment.install_root}/config", "required": True},
                {"path": "${deployment.data_roots.secrets_dir}", "required": True},
                {"path": "${user_home}/.ssh", "required": True},
            ],
            "excludes": ["**/node_modules/**", "${deployment.install_root}/data/database/**",
                         "${user_home}/.config/restic/password"]}},
        "live_postgresql": [{"id": "portal", "environment_file": "${deployment.install_root}/config/postgres.env",
                             "dump_binary": "${deployment.pg_bin}/pg_dump"}],
    }, sort_keys=False))
    config.chmod(0o640)
    document = json.loads((root / "instance.json").read_text())
    document["dependencies"]["backup"] = str(program)
    (root / "instance.json").write_text(json.dumps(document))
    moves = tmp_path / "moves.json"
    moves.write_text(json.dumps({"moves": [
        {"source": str(staging / "shared/portal_uploads"), "target": str(root / "data/uploads"), "kind": "staging-split"},
        {"source": str(staging / "shared/portal_cache/devices"), "target": str(root / "cache/devices"), "kind": "staging-split"}]}))
    report = tmp_path / "report.json"
    assert old_directories.main(["--root", str(root), "--apply", "--moves", str(moves), "--report", str(report)]) == 0
    written = config.read_text()
    assert "${" not in written
    updated = yaml.safe_load(written)
    sources = [item["path"] for item in updated["profiles"]["content"]["sources"]]
    assert str(root / "data/uploads") in sources
    assert str(root / "instance.json") in sources and str(root / "site.json") in sources
    assert str(root / "secrets") in sources and str(home / ".ssh") in sources
    assert str(staging / "shared/gone") not in sources
    assert not any(path.startswith(str(root / "cache")) for path in sources)
    assert updated["profiles"]["content"]["excludes"] == ["**/node_modules/**", str(home / ".config/restic/password")]
    assert updated["live_postgresql"][0]["environment_file"] == str(root / "secrets/postgres.env")
    assert updated["live_postgresql"][0]["dump_binary"] == str(tmp_path / "pg/bin/pg_dump")
    assert updated["restic"]["binary"] == str(home / ".local/bin/restic")
    assert config.stat().st_mode & 0o777 == 0o640
    assert json.loads(report.read_text())["apply"]["backup_config"] == "rewritten"
