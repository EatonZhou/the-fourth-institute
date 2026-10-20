"""A Resident chat count must not scan notices from unrelated traces."""

import json

from management_portal.execution import chat_position_index
from management_portal.execution.agent_data_store import AgentDataStore, CHAT_COUNT_SQL


def _plan_nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from _plan_nodes(child)


def test_chat_count_seeks_only_selected_trace_notices(isolated_portal_case):
    """Chat 消息计数只按索引读取所选 trace 的投影行，不读取通知本身，也不扫描其他 Resident 的消息"""
    with isolated_portal_case():
        store = AgentDataStore()
        target = "resident_aaaaaaaaaaaa"
        unrelated = "resident_bbbbbbbbbbbb"
        for resident_id in (target, unrelated):
            store.ensure_agent(resident_id, actor_ref="test-actor")
        target_sequence = store.trace_sequence_id(target)
        unrelated_sequence = store.trace_sequence_id(unrelated)
        with store.database.transaction() as connection:
            operations = []
            positions = []
            for index in range(2001):
                is_target = index == 0
                operation_id = f"operation_notice_probe_{index:04d}"
                resident_id = target if is_target else unrelated
                sequence_id = target_sequence if is_target else unrelated_sequence
                metadata = {"message": {"message_id": operation_id, "role": "system"}}
                operations.append((
                    operation_id, "notice", resident_id, "", "2026-10-12T00:00:00Z",
                    json.dumps({"segment_type": "harness", "metadata": metadata}),
                ))
                positions.append((sequence_id, operation_id, index + 1))
            connection.executemany("INSERT INTO agent_operations VALUES (%s,%s,%s,%s,%s,%s)", operations)
            connection.executemany("INSERT INTO operation_positions VALUES (%s,%s,%s)", positions)
            for sequence_id in (target_sequence, unrelated_sequence):
                chat_position_index.refresh_sequence(connection, sequence_id)
            connection.execute("ANALYZE chat_message_positions")

        with store.database.read() as connection:
            assert connection.execute(CHAT_COUNT_SQL, (target_sequence, 10)).fetchone()["count"] == 1
            plan = connection.execute(
                "EXPLAIN (ANALYZE, FORMAT JSON) " + CHAT_COUNT_SQL, (target_sequence, 10),
            ).fetchone()[0][0]["Plan"]
        relations = {node.get("Relation Name") for node in _plan_nodes(plan)} - {None}
        assert relations == {"chat_message_positions"}, relations
        position_reads = sum(
            node.get("Actual Rows", 0) * node.get("Actual Loops", 1)
            for node in _plan_nodes(plan)
            if node.get("Relation Name") == "chat_message_positions"
        )
        assert position_reads < 20, f"chat count read other traces' messages: {position_reads}"
