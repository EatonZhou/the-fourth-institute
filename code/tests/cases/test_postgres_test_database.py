import os
import subprocess
import sys
import time
import importlib.util
import json
from pathlib import Path

import pytest

from tests.cases import postgres_test_database as database


PORTAL_URL = "postgresql://tfi@/tfi_lineage?host=%2Fsrv%2Fportal-pg%2Fsocket&port=55432"


def test_deployed_environment_file_is_resolved_before_refusing_production(tmp_path, monkeypatch):
    env_file = tmp_path / "existing-deployment.env"
    env_file.write_text(f"PORTAL_DATABASE_URL={PORTAL_URL}\n")
    site = tmp_path / "site.json"
    site.write_text(json.dumps({"deployment": {"postgres_env_file": str(env_file)}}))
    monkeypatch.setenv("PORTAL_SITE_CONFIG", str(site))
    monkeypatch.delenv("PORTAL_POSTGRES_ENV_FILE", raising=False)
    spec = importlib.util.spec_from_file_location("test_database_configuration_probe", database.__file__)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    assert probe.PRODUCTION_ENVIRONMENT_FILE == env_file
    with pytest.raises(RuntimeError, match="Portal database server"):
        probe.refuse_production(PORTAL_URL, probe.production_addresses({}))
    assert probe.POSTGRES_ROOT == Path(os.environ.get("PORTAL_TEST_POSTGRES_ROOT", Path.home() / ".local/postgresql-16"))


def test_this_session_does_not_use_the_portal_database_server():
    """当前测试会话使用的数据库不在 Portal 生产服务器上"""
    production = database.production_addresses({})
    assert database.server_address(os.environ["PORTAL_DATABASE_URL"]) not in production
    assert os.environ["PORTAL_POSTGRES_TEST_DSN"] == os.environ["PORTAL_DATABASE_URL"]


def test_explicit_dsn_on_the_portal_server_is_refused(tmp_path, monkeypatch):
    """测试数据库 DSN 指向 Portal 数据库服务器时被拒绝，不论库名和写法"""
    env_file = tmp_path / "portal-postgres.env"
    env_file.write_text(f"PORTAL_DATABASE_URL={PORTAL_URL}\nPORTAL_POSTGRES_POOL_SIZE=8\n")
    monkeypatch.setattr(database, "PRODUCTION_ENVIRONMENT_FILE", env_file)
    production = database.production_addresses({})
    # Another database name or spelling on the same server is still that server.
    for dsn in (
        "postgresql://postgres@/scratch?host=/srv/portal-pg/socket&port=55432",
        PORTAL_URL,
    ):
        with pytest.raises(RuntimeError, match="Portal database server"):
            database.choose_test_database({"PORTAL_POSTGRES_TEST_DSN": dsn})
    separate = "postgresql://postgres@/postgres?host=/srv/test-pg/socket&port=55432"
    assert database.choose_test_database({"PORTAL_POSTGRES_TEST_DSN": separate}) == separate
    # An inherited Portal URL counts as production too, whatever the file says.
    inherited = {"PORTAL_DATABASE_URL": "postgresql://u@127.0.0.1:6000/portal"}
    with pytest.raises(RuntimeError):
        database.choose_test_database({**inherited, "PORTAL_POSTGRES_TEST_DSN": "postgresql://u@localhost:6000/other"})
    assert ("/srv/portal-pg/socket", "55432") in production
    # A child process inherits both variables from its pytest session.
    chosen = "postgresql://postgres@/postgres?host=/tmp/pytest-pg/socket&port=5432"
    child = {"PORTAL_DATABASE_URL": chosen, "PORTAL_POSTGRES_TEST_DSN": chosen}
    assert database.choose_test_database(child) == chosen
    on_portal = "postgresql://postgres@/postgres?host=/srv/portal-pg/socket&port=55432"
    with pytest.raises(RuntimeError):
        database.choose_test_database({"PORTAL_DATABASE_URL": on_portal, "PORTAL_POSTGRES_TEST_DSN": on_portal})


def test_directories_left_by_finished_sessions_are_removed(tmp_path):
    """pytest 启动时回收已退出会话留下的临时目录和测试数据库

    所属 pytest 进程已退出的 tfi-portal-pytest-* 目录和测试 PostgreSQL 被删除；仍在运行的会话、新建未写属主的目录和无关目录保留；没有属主文件且超过一天未动的旧目录被删除。
    """
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        abandoned = tmp_path / f"{database.CLUSTER_PREFIX}abandoned"
        abandoned_files = tmp_path / f"{database.TEMP_PREFIX}abandoned"
        active = tmp_path / f"{database.CLUSTER_PREFIX}active"
        new_unowned = tmp_path / f"{database.TEMP_PREFIX}unowned"
        old_unowned = tmp_path / f"{database.CLUSTER_PREFIX}old-unowned"
        unrelated = tmp_path / "pytest-of-someone"
        for root, owner in (
            (abandoned, finished.pid), (abandoned_files, finished.pid), (active, running.pid),
            (new_unowned, None), (old_unowned, None), (unrelated, None),
        ):
            (root / "data").mkdir(parents=True)
            if owner is not None:
                (root / database.OWNER_FILE).write_text(str(owner))
        # Directories from before sessions wrote an owner file go once a day old.
        day_old = time.time() - database.UNOWNED_AGE_SECONDS - 60
        os.utime(old_unowned, (day_old, day_old))
        assert database.sweep_abandoned_roots(tmp_path) == sorted([abandoned, abandoned_files, old_unowned])
        assert active.exists() and new_unowned.exists() and unrelated.exists()
    finally:
        running.kill()
        running.wait()
