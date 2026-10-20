from concurrent.futures import ThreadPoolExecutor

import pytest

from management_common.domain_database import DomainDatabase
from management_portal import identity_store, mailbox_todos
from management_portal.society_service.resident_record_domain import (
    _runtime_error_payload, _runtime_failure_summary,
)


@pytest.mark.parametrize("module,schema,owner,table,column", [
    (identity_store, "portal_identity", "accounts", "user_invitations", "first_admin"),
    (mailbox_todos, "portal_ui", "mailbox_todos", "mailbox_todos", "indent"),
])
def test_startup_during_backup_does_not_acquire_exclusive_table_locks(module, schema, owner, table, column):
    """备份持有表的共享锁时服务启动不申请排他锁，可以正常读表"""
    database = module.database()
    database.ensure()

    def startup():
        fresh = DomainDatabase(schema, owner, module.DDL)
        with fresh.read() as connection:
            return connection.execute(f"SELECT {column} FROM {table} LIMIT 1").fetchall()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.database.transaction() as backup:
            backup.execute(f"LOCK TABLE {table} IN ACCESS SHARE MODE")
            pending = pool.submit(startup)
            pending.result(timeout=5)


def test_database_lock_timeout_is_reported_separately_from_execution_timeout():
    """数据库锁等待超时与执行超时分别报告"""
    raw = "canceling statement due to lock timeout"
    payload = _runtime_error_payload(raw)
    assert payload["code"] == "database_lock_timeout"
    assert _runtime_failure_summary(raw, payload) == "数据库锁等待超时"
    assert _runtime_failure_summary("provider request timed out") == "执行超时"
