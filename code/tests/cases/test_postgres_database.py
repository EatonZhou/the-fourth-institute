from __future__ import annotations

from unittest.mock import Mock

from management_common.postgres_database import (
    PostgresConnection,
    PostgresRow,
    _display_dsn,
)


def test_connection_forwards_native_postgresql_statements_unchanged() -> None:
    """PostgresConnection 原样转发 PostgreSQL 语句和参数"""
    raw = Mock()
    connection = PostgresConnection(raw)
    connection.execute("SELECT value %% 2 FROM records WHERE id=%s", (7,))
    raw.execute.assert_called_once_with(
        "SELECT value %% 2 FROM records WHERE id=%s",
        (7,),
        prepare=None,
    )


def test_portal_row_supports_positional_named_and_mapping_access() -> None:
    """PostgresRow 支持按位置、按列名和按映射访问"""
    row = PostgresRow(("id", "title"), (7, "alpha"))
    assert row[0] == 7
    assert row["title"] == "alpha"
    assert tuple(row) == (7, "alpha")
    assert dict(row) == {"id": 7, "title": "alpha"}


def test_display_dsn_redacts_password_and_query() -> None:
    """显示用的数据库 DSN 去掉密码和查询参数"""
    assert _display_dsn(
        "postgresql://portal:secret@database.example:5432/portal?sslmode=require"
    ) == "postgresql://portal@database.example:5432/portal"
