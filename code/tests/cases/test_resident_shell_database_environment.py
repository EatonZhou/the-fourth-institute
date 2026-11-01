from types import SimpleNamespace

import pytest

from tests.cases.instance_test_support import select_instance
from management_portal.resident_shell_environment import (
    may_inherit_portal_database,
    portal_database_environment,
)


def test_service_database_environment_is_loaded_only_for_authorized_resident(monkeypatch, tmp_path):
    """只有 owner 权限、A 模式且身份一致的 Resident shell 继承 Portal 数据库环境变量"""
    instance = select_instance(monkeypatch, tmp_path / "instance")
    env_file = instance.directory("secrets") / "postgres.env"
    env_file.parent.mkdir()
    env_file.write_text('# service settings\nPORTAL_DATABASE_URL="postgresql://test-database"\n')
    monkeypatch.delenv("PORTAL_DATABASE_URL", raising=False)

    request = SimpleNamespace(
        agent_id="resident_123456789abc",
        permission_profile="owner",
        metadata={
            "business_type": "resident",
            "resident_id": "resident_123456789abc",
            "filesystem_scope": {"mode": "A"},
        },
    )
    assert may_inherit_portal_database(request)
    assert portal_database_environment(True) == {
        "PORTAL_DATABASE_URL": "postgresql://test-database"
    }
    assert portal_database_environment(False) == {}
    request.permission_profile = "restricted"
    assert not may_inherit_portal_database(request)
    request.permission_profile = "owner"
    request.metadata["filesystem_scope"] = {"mode": "B"}
    assert not may_inherit_portal_database(request)
    request.metadata["filesystem_scope"] = {"mode": "A"}
    request.metadata["resident_id"] = "resident_aaaaaaaaaaaa"
    assert not may_inherit_portal_database(request)


def test_missing_service_database_configuration_fails_without_secret_in_error(monkeypatch, tmp_path):
    """缺少数据库环境文件时报错，错误信息不含密钥"""
    select_instance(monkeypatch, tmp_path / "instance")
    monkeypatch.delenv("PORTAL_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="secrets/postgres.env") as error:
        portal_database_environment(True)
    assert "postgresql://" not in str(error.value)
