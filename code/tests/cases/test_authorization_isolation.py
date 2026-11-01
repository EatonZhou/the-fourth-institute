"""Permission tests never touch the Portal's own database.

:module: spec.design-governance
"""
from __future__ import annotations

import os

from tests.cases.postgres_test_database import server_address


def test_database_is_a_disposable_namespace_off_the_portal_server(production_database_addresses):
    """权限测试使用一次性的 pytest_ 命名空间，不连接 Portal 生产数据库服务器"""
    assert os.environ["PORTAL_DATABASE_NAMESPACE"].startswith("pytest_")
    assert server_address(os.environ["PORTAL_DATABASE_URL"]) not in production_database_addresses
