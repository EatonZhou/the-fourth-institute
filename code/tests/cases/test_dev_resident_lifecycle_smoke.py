from __future__ import annotations

from scripts import dev_resident_lifecycle_smoke


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class _Connection:
    def __init__(self, schema: str) -> None:
        self.schema = schema

    def execute(self, statement, parameters=()):
        del parameters
        if self.schema == "lineage":
            assert "conversation_input_states" in statement
            return _Rows([{"reply_message_ids_json": '["reply-one"]'}])
        if self.schema == "agent":
            # The reply's execution is read through the trace's message projection.
            assert "FROM chat_message_positions reply" in statement and "reply.reply_to = %s" in statement
            return _Rows([{"execution_id": "execution-one"}])
        assert self.schema == "execution_runtime"
        assert "FROM executions" in statement
        return _Rows([{
            "execution_id": "execution-one",
            "capability": "agent_session:codex",
            "actor": "sample_admin",
            "correlation_id": "job-one",
            "request_json": "{}",
            "checkpoint_json": "",
            # Acknowledgement drops request and checkpoint; the result stays until retention.
            "result_json": '{"status": "completed"}',
            "status": "completed",
            "attempts": 1,
            "usage_accounted": 1,
            "acknowledged_at": "2026-08-26T14:00:00Z",
        }])

    def close(self):
        return None


def test_execution_proof_uses_canonical_database_schemas(monkeypatch) -> None:
    """执行证明从规范数据库读取回复与已确认的压缩记录"""
    opened: list[str] = []

    def open_schema(schema: str):
        opened.append(schema)
        return _Connection(schema)

    monkeypatch.setattr(dev_resident_lifecycle_smoke, "_read_connection", open_schema)

    assert dev_resident_lifecycle_smoke.execution_proof(["message-one"]) == [{
        "execution_id": "execution-one",
        "capability": "agent_session:codex",
        "actor": "sample_admin",
        "correlation_id": "job-one",
        "status": "completed",
        "attempts": 1,
        "usage_accounted": 1,
        "acknowledged_at": "2026-08-26T14:00:00Z",
        "storage_state": "acknowledged_compact",
    }]
    assert opened == ["execution_runtime", "agent", "lineage"]
