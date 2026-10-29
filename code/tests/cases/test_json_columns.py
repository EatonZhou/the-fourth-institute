"""JSON columns keep working on both column types while they move to JSONB.

New schemas create JSONB columns.  Live schemas keep JSON text until their
online conversion, so each store is also exercised after its columns are
turned back into text.  The 0/1 integer flags that become BOOLEAN in the same
conversion are exercised the same way.
"""
import uuid

import pytest

from management_common.json_columns import json_text, json_value
from management_common.portal_database import portal_database
from management_portal.execution import (
    agent_data_store, harness_auto_policy, harness_effectiveness, harness_evolution, harness_evolution_evaluation,
    scheduler as scheduler_module,
)
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.agent_data_tables import CHAT_READ_INDEX_SQL, CHAT_STATISTICS_SQL
from management_portal.execution.agent_operations import put_operation
from management_portal.execution.contracts import (
    ExecutionInputControl, ExecutionRequest, ExecutionResult, SessionOptions,
)
from management_portal.execution.conversation_projection import CHAT_SEGMENT
from management_portal.execution.harness_evolution_offline import TurnCalls, turn_calls_from_rows
from management_portal.execution.scheduler_rows import row_payload
from management_portal.hatg.contracts import InstructionCall
from management_portal.hatg.store import ExecutionStore
from management_portal.society_service import (
    program_work_storage, relation_work_storage, resident_input_scope, trigger_path_storage,
)
from management_portal.society_service.resident_context.store import ResidentContextStore
from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository
from management_portal.society_service.service_invocation_store import ServiceInvocationStore
from management_portal.society_service.state_store import LineageDataStore, create_lineage_data_schema
from management_portal.resource_index_backend import IndexStore
from management_portal.resources.registry_store import RegistryStore
from management_portal.service_invocation_contract import ServiceInvocationConflict
from tests.cases.database_test_support import register_owned_workspace
from tests.cases.test_agent_data_store import _chat
from tests.cases.test_harness_effectiveness import _decision
from tests.cases.test_shared_search_invocation import job

COLUMN_TYPES = pytest.mark.parametrize("legacy", [False, True], ids=["jsonb", "json-text"])
RESIDENT = "resident_aaaaaaaaaaaa"
LINEAGE = "lineage_aaaaaaaaaaaa"


def column_types(connection, table, columns):
    rows = connection.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name=%s", (table,),
    ).fetchall()
    return {row["column_name"]: row["data_type"] for row in rows if row["column_name"] in columns}


def as_json_text(connection, table, columns):
    """Give the table the JSON text columns live schemas still have.

    Live schemas built the Trigger Path read indexes on their text columns. A
    JSONB index drops the no-op ``::jsonb`` cast, so these are rebuilt from their
    definitions after the change instead of being converted with the column.
    """
    indexes = {name: definition for name, definition in trigger_path_storage.READ_INDEXES.items()
               if definition.startswith((f"ON {table}(", f"ON {table} "))}
    for name in indexes:
        connection.execute(f"DROP INDEX IF EXISTS {name}")
    for column in columns:
        connection.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TEXT USING {column}::text")
    for name, definition in indexes.items():
        connection.execute(f"CREATE INDEX {name} {definition}")


def as_jsonb(connection, table, columns):
    for column in columns:
        connection.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE JSONB USING {column}::jsonb")


def schema(prefix):
    return portal_database(schema=f"{prefix}_{uuid.uuid4().hex[:8]}")


def lineage_schema():
    database = schema("json_lineage")
    database.write(create_lineage_data_schema)
    return database


def test_json_text_stores_a_nul_character_as_the_replacement_character():
    """JSON 文本将空字符替换为替代字符"""
    assert json_text({"a\x00": ["b\x00", ("c",)]}, ensure_ascii=False) == '{"a�": ["b�", ["c"]]}'
    assert json_text({"path": "C:\\u0000"}) == '{"path": "C:\\\\u0000"}'
    assert json_value('{"a": 1}') == {"a": 1}
    assert json_value({"a": 1}) == {"a": 1}
    assert json_value(None) is None


@COLUMN_TYPES
def test_instruction_records_read_and_write_both_column_types(legacy):
    """指令记录兼容 text 与 jsonb 列的读写"""
    columns = ("references_json", "error_json")
    database = schema("json_hatg")
    store = ExecutionStore(database)
    if legacy:
        database.write(lambda connection: as_json_text(connection, "instruction_executions", columns))
    for instruction_id, code in (("inst_a", "instruction.projection_pending"), ("inst_b", "instruction.failed")):
        store.begin(
            instruction_id=instruction_id,
            call=InstructionCall.from_payload({"instruction": "GetResident", "idempotency_key": instruction_id}),
            caller="resident_aaaaaaaaaaaa", entry="test", scope=("resident", "resident_aaaaaaaaaaaa"),
            now="2026-10-16T00:00:00Z",
        )
        assert store.get(instruction_id)["references"] is None
        store.finish(
            instruction_id, status="failed", now="2026-10-16T00:00:01Z", result={"status": "ok"},
            error={"code": code, "message": "bad\x00byte"},
        )

    record = store.get("inst_b")
    assert record["references"] == {"status": "ok"}
    assert record["error"]["message"] == "bad�byte"
    assert [item["instruction_id"] for item in store.pending_projections()] == ["inst_a"]
    with database.read() as connection:
        assert set(column_types(connection, "instruction_executions", columns).values()) == {
            "text" if legacy else "jsonb"
        }


RESIDENT_CONTEXT_COLUMNS = {
    "work_summaries": ("summary_json", "source_versions"),
    "snapshots": ("message_ids_json",),
    "summary_jobs": ("evidence_json",),
    "routing_eval_cases": ("ground_truth_json", "tags_json"),
    "routing_eval_submissions": ("predicted_json",),
    "service_state": ("value_json",),
}


@COLUMN_TYPES
def test_resident_context_documents_read_and_write_both_column_types(legacy):
    """Resident 上下文文档兼容 text 与 jsonb 列"""
    store = ResidentContextStore(database=schema("json_context"))
    store.ensure_schema()
    if legacy:
        with store.transaction() as connection:
            for table, columns in RESIDENT_CONTEXT_COLUMNS.items():
                as_json_text(connection, table, columns)
            # Live summary jobs still mark missing evidence with an empty string.
            connection.execute("ALTER TABLE summary_jobs ALTER COLUMN evidence_json SET DEFAULT ''")
    store.upsert_catalog([{"resident_id": RESIDENT, "lineage_id": "lineage_aaaaaaaaaaaa", "title": "A"}])

    assert store.write_work_summary(RESIDENT, RESIDENT, {"current_focus": "a\x00b"}, [], 0, {"m": "1"})["revision"] == 1
    # The same summary, or none, leaves the revision where it is.
    assert store.write_work_summary(RESIDENT, RESIDENT, {"current_focus": "a\x00b"}, [], 1, {"m": "2"})["revision"] == 1
    summary = store.write_work_summary(RESIDENT, RESIDENT, None, [], 1, {"m": "3"})
    assert (summary["revision"], summary["summary"], summary["source_versions"]) == (1, {"current_focus": "a�b"}, {"m": "3"})

    store.enqueue(RESIDENT, mode="full", reason="test")
    [job] = store.take_jobs_to_prepare(5, lease_seconds=60)
    assert json_value(job["evidence_json"] or "{}") == {}
    assert store.mark_ready(RESIDENT, {"source": {"message_ids": ["m1"], "cursor": 3}})
    [claim] = store.claim_ready_jobs("writer", limit=None, lease_seconds=60, evidence_budget=1)
    assert claim["evidence_chars"] > 0
    assert store.claimed_job(claim["claim_id"], "writer")["evidence"]["source"]["cursor"] == 3
    store.complete_claimed(
        claim["claim_id"], "writer", summary={"current_focus": "c"}, summary_text="c",
        model_version="m", account_id="a", prompt_version="p",
    )
    resident = store.resident(RESIDENT)
    assert (resident["summary"], resident["summary_revision"], resident["message_ids"]) == ({"current_focus": "c"}, 2, ["m1"])

    store.enqueue(RESIDENT, mode="full", reason="again")
    store.take_jobs_to_prepare(5, lease_seconds=60)
    store.mark_ready(RESIDENT, {"source": {"cursor": 4}})
    store.fail_job(RESIDENT, "failed", retry_at="2026-10-16T00:00:00Z")
    assert json_value(store.summary_job(RESIDENT)["evidence_json"] or "{}") == {}

    case = {"case_id": "case_a", "query": "q", "ground_truth": {"resident_id": RESIDENT}, "tags": ["t"]}
    assert store.add_routing_eval_case(case)["added"] is True
    assert store.add_routing_eval_case({**case, "case_id": "case_b"}) == {"case_id": "case_a", "added": False}
    store.record_routing_eval_submission({
        "submission_id": "sub_a", "query": "q", "predicted": [RESIDENT], "ground_truth_resident_id": RESIDENT,
        "ground_truth_action": "deliver", "submitted_by": "tester",
    })
    assert store.hold_worker_lease("one", ttl_seconds=60) is True
    assert store.hold_worker_lease("two", ttl_seconds=60) is False
    assert store.worker_lease()["holder"] == "one"
    store.release_worker_lease("one")
    assert store.worker_lease() == {}

    with store.read() as connection:
        types = {
            column_type
            for table, columns in RESIDENT_CONTEXT_COLUMNS.items()
            for column_type in column_types(connection, table, columns).values()
        }
    assert types == {"text" if legacy else "jsonb"}


@COLUMN_TYPES
def test_registry_documents_read_and_write_both_column_types(legacy):
    """注册文档兼容 text 与 jsonb 列的读写"""
    store = RegistryStore()
    store.database = schema("json_registry")
    store.revision("resident")
    if legacy:
        store.database.write(lambda connection: as_json_text(connection, "registry_documents", ("document_json",)))
    with store.writer("resident") as writer:
        writer.put("resident", RESIDENT, {"title": "a\x00b", "tags": ["x"]})
        assert writer.get("resident", RESIDENT) == {"title": "a�b", "tags": ["x"]}
    assert store.get("resident", RESIDENT) == {"title": "a�b", "tags": ["x"]}
    assert store.get_many("resident", [RESIDENT]) == {RESIDENT: {"title": "a�b", "tags": ["x"]}}
    assert store.changes_since("resident", 0)[1] == {RESIDENT: {"title": "a�b", "tags": ["x"]}}
    with store.database.read() as connection:
        assert column_types(connection, "registry_documents", ("document_json",)) == {
            "document_json": "text" if legacy else "jsonb"
        }


@COLUMN_TYPES
def test_resource_index_records_reload_from_both_column_types(legacy, monkeypatch):
    """资源索引记录可从 text 与 jsonb 列重新加载"""
    monkeypatch.setenv("PORTAL_RESOURCE_DATABASE_SCHEMA", f"json_index_{uuid.uuid4().hex[:8]}")
    store = IndexStore()
    if legacy:
        store.database.write(lambda connection: (
            as_json_text(connection, "resource_index_records", ("record_json",)),
            as_json_text(connection, "resource_index_adapters", ("status_json",)),
        ))
    record = {
        "resource_id": "review_abcdef012345", "resource_type": "review", "title": "a\x00b",
        "source": {"adapter": "paper_review", "path": "/tmp/review.md"}, "attributes": {},
    }
    store.publish_snapshot("paper_review", [record], {"review_count": 1})
    reloaded = IndexStore()
    assert reloaded.internal_get("review_abcdef012345")["title"] == "a�b"
    assert reloaded.status()["adapters"]["paper_review"]["source_state"]["review_count"] == 1


# The live conversion changes one table at a time.  Until it ends, a read that
# unites several tables gets JSON text from some and JSONB from others.
MIXED_PROGRAM_COLUMNS = {
    "text": {"event_source_progress": ("checkpoint_json",), "pending_event_deliveries": ("delivery_json",),
             "workspace_handoff_work": ("progress_json",)},
    "jsonb": {"operator_work": ("checkpoint_json",), "pending_expiry_notices": ("notice_json",),
              "resident_delivery_profiles": ("checkpoint_json",), "context_absorption_work": ("progress_json",)},
}


@COLUMN_TYPES
def test_program_and_relation_work_read_tables_of_both_column_types_together(legacy):
    """Lineage 程序与关系运行数据在 text、jsonb 列混用的表中读写一致，未改动时不写回"""
    database = lineage_schema()

    def prepare(connection):
        connection.execute("INSERT INTO lineages(lineage_id) VALUES (%s)", (LINEAGE,))
        if legacy:
            for table, columns in MIXED_PROGRAM_COLUMNS["text"].items():
                as_json_text(connection, table, columns)
            for table, columns in MIXED_PROGRAM_COLUMNS["jsonb"].items():
                as_jsonb(connection, table, columns)

    database.write(prepare)
    with program_work_storage.transaction(database, LINEAGE) as work:
        work["event_sources"]["source_a"] = {"cursor": "a\x00b"}
        work["event_wires"]["wire_a"] = {"pending_events": [{"claim_token": "c1", "event": {"id": "e1"}}]}
        work["operators"]["operator_a"] = {"pending_deliveries": {}}
        work["delivery_profiles"]["message_a"] = {"wire_id": "wire_a"}
        work["expired_deliveries"] = [{"wire_id": "wire_a", "cursor": "2"}]
    relations = {"edge_a": {"handoff": {"status": "sent"}, "absorption": {"target_message_id": "m"}}}
    database.write(lambda connection: relation_work_storage.put(connection, LINEAGE, {}, relations))

    with program_work_storage.transaction(database, LINEAGE) as work:
        assert work["event_sources"]["source_a"] == {"cursor": "a�b"}
        assert work["operators"]["operator_a"] == {"pending_deliveries": {}}
    assert work.changed is False
    with database.read() as connection:
        assert program_work_storage.read(connection, LINEAGE) == {
            "event_sources": {"source_a": {"cursor": "a�b"}},
            "event_wires": {"wire_a": {"pending_events": [{"claim_token": "c1", "event": {"id": "e1"}}]}},
            "operators": {"operator_a": {"pending_deliveries": {}}},
            "delivery_profiles": {"message_a": {"wire_id": "wire_a"}},
            "expired_deliveries": [{"wire_id": "wire_a", "cursor": "2"}],
        }
        assert relation_work_storage.read(connection, LINEAGE) == relations
        types = {
            table: column_types(connection, table, columns)
            for group in MIXED_PROGRAM_COLUMNS.values() for table, columns in group.items()
        }
    expected = {
        table: {column: ("text" if legacy and kind == "text" else "jsonb") for column in columns}
        for kind, group in MIXED_PROGRAM_COLUMNS.items() for table, columns in group.items()
    }
    assert types == expected


@COLUMN_TYPES
def test_trigger_path_accepts_a_retried_occurrence_on_both_column_types(legacy):
    """Trigger Path 在 text 与 jsonb 列上接受重试的同一事件，输入不同则拒绝"""
    database = lineage_schema()
    if legacy:
        database.write(lambda connection: (
            as_json_text(connection, "trigger_paths", ("input_json",)),
            as_json_text(connection, "trigger_path_steps", ("checkpoint_json", "result_json")),
        ))
    occurrence = {
        "lineage_id": LINEAGE, "root_kind": "event", "occurrence_id": "event_a", "root_node_id": "source_a",
        "cause_kind": "input", "cause_id": "message_a", "occurred_at": "2026-10-16T00:00:00Z",
        "input": {"id": "event_a", "payload": {"b": 1, "a": "x\x00"}},
    }

    def begin(connection, **changes):
        return trigger_path_storage.begin(connection, **{**occurrence, **changes})

    identity = database.write(begin)
    assert database.write(begin) == identity
    with pytest.raises(ValueError, match="input_json"):
        database.write(lambda connection: begin(connection, input={"id": "event_a", "payload": {"b": 2}}))

    def advance(connection):
        step = trigger_path_storage.admit_step(
            connection, trigger_path_id=identity, node_id="source_a", kind="event", activation_key="event_a",
            created_at="2026-10-16T00:00:00Z", checkpoint={"attempt": 1},
        )
        trigger_path_storage.transition_step(connection, step, status="running", at="2026-10-16T00:00:01Z",
                                             checkpoint={"attempt": 2}, result={"outputs": {"n": 1}})

    database.write(advance)
    with database.read() as connection:
        path = trigger_path_storage.read(connection, identity)
        assert path["input"] == {"id": "event_a", "payload": {"b": 1, "a": "x�"}}
        assert [(step["checkpoint"], step["result"]) for step in path["steps"]] == [({"attempt": 2}, {"outputs": {"n": 1}})]
        assert column_types(connection, "trigger_paths", ("input_json",)) == {"input_json": "text" if legacy else "jsonb"}


@COLUMN_TYPES
def test_input_scope_accepts_the_same_grant_again_on_both_column_types(legacy, monkeypatch):
    """输入范围在 text 与 jsonb 列上接受重复的相同授权，内容不同则拒绝"""
    database = lineage_schema()

    def prepare(connection):
        connection.execute("INSERT INTO residents(resident_id, deleted_at) VALUES (%s, now())", (RESIDENT,))
        if legacy:
            as_json_text(connection, "input_scopes", ("descriptor",))

    database.write(prepare)
    monkeypatch.setattr(resident_input_scope, "_database", lambda: database)
    grant = {"resident_id": RESIDENT, "message_id": "message_a", "actor": "user_a"}
    resident_input_scope.register(**grant, profile={"b": [1], "a": "x"})
    resident_input_scope.register(**grant, profile={"a": "x", "b": [1]})
    with pytest.raises(ValueError, match="immutable"):
        resident_input_scope.register(**grant, profile={"a": "y", "b": [1]})
    assert resident_input_scope.get(RESIDENT, "message_a") == {"actor": "user_a", "profile": {"a": "x", "b": [1]}}
    with database.read() as connection:
        assert column_types(connection, "input_scopes", ("descriptor",)) == {"descriptor": "text" if legacy else "jsonb"}


def test_conversation_state_written_again_unchanged_records_no_event():
    """对话状态以相同内容再次写入时不记录事件"""
    store = LineageDataStore()
    store.database.write(lambda connection: connection.execute(
        "INSERT INTO residents(resident_id,title,backend,created_by,owned_workspace_id) VALUES (%s,'','codex','',%s)",
        (RESIDENT, register_owned_workspace(connection, RESIDENT)),
    ))
    agent_data = AgentDataStore()
    agent_data.ensure_agent(RESIDENT, actor_ref="sample_admin")
    repository = ResidentConversationRepository(store, agent_data)

    assert repository.set_state(RESIDENT, {"work_state": "running", "active_message_id": "m"}) is not None
    assert repository.set_state(RESIDENT, {"active_message_id": "m", "work_state": "running"}) is None
    with store.database.read() as connection:
        assert column_types(connection, "conversation_states", ("state_json", "activity_json")) == {
            "state_json": "jsonb", "activity_json": "jsonb",
        }


AGENT_COLUMNS = {
    "segments": ("metadata_json",),
    "segment_relations": ("metadata_json",),
    "agent_operations": ("data_json",),
    "provider_bindings": ("harness_tool_names_json",),
    "executions": ("usage_json",),
}
SENDER, NOTIFIER = "resident_bbbbbbbbbbbb", "resident_cccccccccccc"


def agent_store(monkeypatch, legacy):
    database = schema("json_agent")
    monkeypatch.setattr(agent_data_store, "portal_database", lambda schema: database)
    store = AgentDataStore()
    if legacy:
        def prepare(connection):
            # The indexes are built again over the text, as the live store has them.
            for name in CHAT_READ_INDEX_SQL:
                connection.execute(f"DROP INDEX {name}")
            for name in CHAT_STATISTICS_SQL:
                connection.execute(f"DROP STATISTICS {name}")
            for table, columns in AGENT_COLUMNS.items():
                as_json_text(connection, table, columns)
            for sql in (*CHAT_READ_INDEX_SQL.values(), *CHAT_STATISTICS_SQL.values()):
                connection.execute(sql)
        database.write(prepare)
    return store


def plan(connection, query, parameters=()):
    connection.execute("SET LOCAL enable_seqscan=off")
    return "\n".join(str(row[0]) for row in connection.execute("EXPLAIN (COSTS OFF) " + query, parameters))


@COLUMN_TYPES
def test_agent_records_read_and_write_both_column_types(legacy, monkeypatch):
    """Agent 的 Segment、操作、会话绑定与执行用量兼容 text 与 jsonb 列的读写"""
    store = agent_store(monkeypatch, legacy)
    to_et = [{"kind": "user", "id": "CASE-A"}]
    store.display_chat_message(SENDER, {**_chat("msg_0000000000000b01", "agent", "周报已写好",
                                                created_at="2026-10-06T01:00:00Z"), "recipients": to_et})
    # A message without text stays an operational notice.
    store.display_chat_message(NOTIFIER, {**_chat("msg_0000000000000b02", "agent", "",
                                                  created_at="2026-10-06T02:00:00Z"), "recipients": to_et})
    for _ in range(2):
        store.put_segment(segment_id="segment_note", segment_type="harness", content="note",
                          metadata={"note": "a\x00b", "b": [1]}, created_by_agent_id=SENDER)
        store.add_relations([{"relation_type": "derived_from", "source_segment_id": "segment_note",
                              "target_segment_id": "segment_note", "operation_id": "op", "metadata": {"a": "\x00"}}])
        store.database.write(lambda connection: put_operation(
            connection, operation_id="operation_a", kind="receipt", agent_id=SENDER,
            created_at="2026-10-06T03:00:00Z", data={"z": "\x00", "a": 1}))
    session = store.ensure_provider_session(provider="codex", backend_route_id="zero", backend_session_id="thread",
                                            agent_id=SENDER, actor_ref="sample_admin")
    store.bind_provider_session("binding_a", session["provider_session_ref"], harness_tool_names=["Write", "Read"])
    store.bind_provider_session("binding_a", session["provider_session_ref"], context_fingerprint="f")
    store.begin_execution(execution_id="execution_a", agent_id=SENDER, actor_ref="sample_admin", provider="codex",
                          started_at="2026-10-06T03:00:00Z")
    for _ in range(2):
        store.finish_execution("execution_a", status="completed", finished_at="2026-10-06T03:00:01Z",
                               usage={"input_tokens": 3})
        store.record_accounted_usage("execution_a", {"input_tokens": 5})

    assert store.segment("segment_note")["metadata"] == {"note": "a�b", "b": [1]}
    assert store.provider_binding("binding_a")["harness_tool_names"] == ("Read", "Write")
    assert store.execution("execution_a")["usage"] == {"input_tokens": 3, "accounted": {"input_tokens": 5}}
    assert store.chat_actor_activity("CASE-A") == {NOTIFIER: "2026-10-06T02:00:00Z", SENDER: "2026-10-06T01:00:00Z"}
    summaries = store.chat_conversation_summaries([SENDER, NOTIFIER], "CASE-A")
    assert summaries[SENDER]["actor_addressed_message_id"] == "msg_0000000000000b01"
    assert summaries[NOTIFIER]["actor_addressed_message_id"] == "msg_0000000000000b02"
    assert [message["message_id"] for message in store.read_chat_messages(NOTIFIER)] == ["msg_0000000000000b02"]
    with store.database.read() as connection:
        assert {table: column_types(connection, table, columns) for table, columns in AGENT_COLUMNS.items()} == {
            table: {column: "text" if legacy else "jsonb" for column in columns} for table, columns in AGENT_COLUMNS.items()
        }


@COLUMN_TYPES
def test_agent_chat_reads_use_their_indexes_on_both_column_types(legacy, monkeypatch):
    """Chat 按消息 ID 读取时在 text 与 jsonb 列上都使用索引"""
    store = agent_store(monkeypatch, legacy)
    by_message = ("SELECT segment.segment_id FROM segments segment "
                  f"WHERE {CHAT_SEGMENT} AND segment.metadata_json::jsonb #>> '{{message,message_id}}' = %s")
    # Chat finds a message, Segment or notice, in the projection.
    by_identity = ("SELECT position.source_id FROM chat_message_positions position "
                   "WHERE position.message_id = %s AND position.sequence_id = %s AND position.source_kind <> 'sync'")
    with store.database.read_transaction() as connection:
        message_plan = plan(connection, by_message, ("msg_0000000000000b01",))
        identity_plan = plan(connection, by_identity, ("msg_0000000000000b01", "sequence"))
    assert "segments_message_idx" in message_plan
    assert "chat_message_positions_identity_idx" in identity_plan


RUNTIME_COLUMNS = {
    "executions": ("request_json", "checkpoint_json", "result_json", "usage_accounted_json"),
    "execution_workers": ("capabilities_json",),
    "execution_token_usage_events": ("usage_json",),
    "execution_input_controls": ("input_json",),
}
RUNTIME_FLAGS = ("cancel_requested", "usage_accounted")
WORKER = "json-worker"


def as_integer_flags(connection, table, columns):
    """Give the table the BIGINT 0/1 flags live schemas still have."""
    for column in columns:
        connection.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT, "
                           f"ALTER COLUMN {column} TYPE BIGINT USING {column}::int, "
                           f"ALTER COLUMN {column} SET DEFAULT 0")


def runtime_scheduler(monkeypatch, legacy):
    database = schema("json_runtime")
    monkeypatch.setattr(scheduler_module, "portal_database", lambda schema: database)
    scheduler = scheduler_module.Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    if legacy:
        def prepare(connection):
            for table, columns in RUNTIME_COLUMNS.items():
                as_json_text(connection, table, columns)
            # Live stores leave a checkpoint or result that was never written empty.
            connection.execute("ALTER TABLE executions ALTER COLUMN checkpoint_json SET DEFAULT '', "
                               "ALTER COLUMN result_json SET DEFAULT ''")
            as_integer_flags(connection, "executions", RUNTIME_FLAGS)
        database.write(prepare)
    scheduler.register_worker(worker_id=WORKER, build_id="test", capabilities=["agent_session:codex"])
    return scheduler


@COLUMN_TYPES
def test_execution_runtime_reads_and_writes_both_column_types(legacy, monkeypatch):
    """执行记录的请求、检查点、结果与用量兼容 text、jsonb 列和 0/1 整数标志"""
    scheduler = runtime_scheduler(monkeypatch, legacy)
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="a\x00b", service_type="agent_session", provider="codex", operation="turn",
        session=SessionOptions(mode="persistent", binding_id="binding_json", backend_route_id="one"),
    )
    for _ in range(2):
        # The retry matches the digest of the request as first submitted.
        scheduler.submit(request)
    queued = scheduler.states({request.execution_id: "sample_admin"})[request.execution_id]
    assert (queued["status"], queued["scheduler"]["cancel_requested"]) == ("queued", False)
    claim = scheduler.claim(worker_id=WORKER, capabilities=["agent_session:codex"])
    owner = {"worker_id": WORKER, "claim_token": claim["claim_token"]}
    scheduler.start(request.execution_id, **owner)
    scheduler.checkpoint(request.execution_id, checkpoint={"resumable": True, "turn_id": "turn-1"}, **owner)
    control = ExecutionInputControl.new(execution_id=request.execution_id, actor="sample_admin",
                                        input=[{"type": "text", "text": "more"}], expected_turn_id="turn-1")
    for _ in range(2):
        scheduler.submit_input_control(control)
    last = {"inputTokens": 7, "cachedInputTokens": 0, "outputTokens": 2, "totalTokens": 9}
    scheduler.record_token_usage_delta(request.execution_id, event_id="request-0", usage={"last": last}, **owner)
    scheduler.active_requests._scheduling_memo.clear()
    with scheduler.changes.connect() as connection:
        [(_, scheduling)] = scheduler.active_requests.scheduling_requests(connection)
    assert scheduling["session"]["binding_id"] == "binding_json"
    scheduler.finish(ExecutionResult(execution_id=request.execution_id, status="completed",
                                     output={"message": "done"}, usage={"last": last}), **owner)

    def stored():
        with scheduler.database.read() as connection:
            return row_payload(connection.execute("SELECT * FROM executions").fetchone())

    finished = stored()
    assert finished["request"]["prompt"] == "a�b"
    assert finished["checkpoint"]["turn_id"] == "turn-1"
    assert finished["result"]["output"] == {"message": "done"}
    assert finished["result"]["accounted_usage"]["total_tokens"] == 9
    assert scheduler.input_control(control.control_id)["input"] == [{"type": "text", "text": "more"}]
    assert [worker["capabilities"] for worker in scheduler.health()["workers"]] == [["agent_session:codex"]]
    scheduler.acknowledge(request.execution_id, actor="sample_admin")
    acknowledged = stored()
    assert (acknowledged["request"], acknowledged["checkpoint"]) == ({}, {})
    assert acknowledged["result"]["output"] == {"message": "done"}
    with scheduler.database.read() as connection:
        assert connection.execute("SELECT usage_accounted FROM executions WHERE execution_id = %s",
                                  (request.execution_id,)).fetchone()["usage_accounted"] == 1

    # A queued execution is cancelled at once; a claimed one is asked to stop and
    # leaves the executions that count against capacity.
    queued, running = (ExecutionRequest.new(
        actor="sample_admin", prompt=name, service_type="agent_session", provider="codex", operation="turn",
        session=SessionOptions(mode="persistent", binding_id=name, backend_route_id="one"),
    ) for name in ("binding_queued", "binding_running"))
    scheduler.submit(queued)
    assert scheduler.cancel(queued.execution_id, actor="sample_admin")["status"] == "cancelled"
    scheduler.submit(running)
    assert scheduler.claim(worker_id=WORKER, capabilities=["agent_session:codex"])["claimed"]
    with scheduler.changes.connect() as connection:
        assert [item for item, _ in scheduler.active_requests.scheduling_requests(connection, uncancelled=True)] == [
            running.execution_id]
    scheduler.cancel(running.execution_id, actor="sample_admin")
    states = scheduler.states({queued.execution_id: "sample_admin", running.execution_id: "sample_admin"})
    assert [states[item.execution_id]["scheduler"]["cancel_requested"] for item in (queued, running)] == [True, True]
    with scheduler.changes.connect() as connection:
        assert scheduler.active_requests.scheduling_requests(connection, uncancelled=True) == []
    with scheduler.database.read() as connection:
        assert {table: column_types(connection, table, columns) for table, columns in RUNTIME_COLUMNS.items()} == {
            table: {column: "text" if legacy else "jsonb" for column in columns} for table, columns in RUNTIME_COLUMNS.items()
        }
        assert column_types(connection, "executions", RUNTIME_FLAGS) == {
            column: "bigint" if legacy else "boolean" for column in RUNTIME_FLAGS}


HARNESS_COLUMNS = {
    "harness_policy_cells": ("champion_json", "challenger_json", "evaluation_json"),
    "harness_policy_events": ("champion_json", "challenger_json", "evidence_json"),
    "harness_turn_calls": ("compaction_json", "calls_json", "compactions_json"),
}
OBSERVATION_COLUMNS = (
    "production_plan_json", "shadow_plan_json", "shadow_diff_json", "actual_actions_json",
    "action_outcomes_json", "signals_json",
)
OBSERVATION_FLAGS = ("provider_latency_observed", "queue_wait_observed")


def harness_stores(monkeypatch, legacy):
    evolution_database, policy_database, effectiveness_database = (
        schema("json_evolution"), schema("json_policy"), schema("json_effectiveness"))
    monkeypatch.setattr(harness_evolution, "portal_database", lambda schema: evolution_database)
    monkeypatch.setattr(harness_auto_policy, "portal_database", lambda schema: policy_database)
    for module in (harness_effectiveness, harness_evolution_evaluation):
        monkeypatch.setattr(module, "portal_database", lambda schema: effectiveness_database)
    evolution = harness_evolution.HarnessEvolutionStore()
    policy = harness_auto_policy.HarnessAutoPolicyStateStore()
    effectiveness = harness_effectiveness.HarnessEffectivenessStore()
    evolution.ensure_schema()
    with policy._connect(), effectiveness._connect():
        pass
    if legacy:
        def prepare_evolution(connection):
            for table, columns in HARNESS_COLUMNS.items():
                as_json_text(connection, table, columns)
            # A cell without a challenger, as live stores still have it.
            connection.execute("INSERT INTO harness_policy_cells VALUES "
                               "('claude','repository_work',1,'stable','{}','',0,NULL,'{}',now())")

        def prepare_effectiveness(connection):
            connection.execute("DROP VIEW harness_observations")
            as_json_text(connection, "harness_strategy_observations", OBSERVATION_COLUMNS)
            as_integer_flags(connection, "harness_strategy_observations", OBSERVATION_FLAGS)
            harness_effectiveness.create_observation_view(connection)

        evolution_database.write(prepare_evolution)
        policy_database.write(lambda connection: as_json_text(connection, "harness_auto_policy_state", ("state_json",)))
        effectiveness_database.write(prepare_effectiveness)
    return evolution, policy, effectiveness


@COLUMN_TYPES
def test_harness_records_read_and_write_both_column_types(legacy, monkeypatch):
    """Harness 策略、轮次调用与效果观察记录兼容 text、jsonb 列和 0/1 整数标志"""
    evolution, policy, effectiveness = harness_stores(monkeypatch, legacy)
    if legacy:
        assert evolution.cell("claude", "repository_work").challenger is None
    started = evolution.start_challenger("claude", "repository_work", {"compaction_max_context_tokens": 300_000},
                                         actor="t")
    assert evolution.retire("claude", "repository_work", "rolled_back", actor="t",
                            evidence={"why": "a\x00"}).challenger is None
    evolution.freeze("claude", "repository_work", actor="t")
    assert evolution.cell("claude", "repository_work").challenger is None
    frozen, retired, start = evolution.events(provider="claude")
    assert (frozen["challenger"], retired["challenger"], start["challenger"]) == (
        None, started.challenger, started.challenger)
    assert retired["evidence"] == {"why": "a�"}
    turn = TurnCalls(execution_id="execution_t", agent_id=SENDER, provider="claude", cell="claude/repository_work",
                     arm="champion", version=1, recorded_at=1.0, compaction={"limit": 5}, calls=((1.0, 10, 5),),
                     compactions=(2.0,))
    evolution.write_turn_calls([turn], keep_since=0)
    [stored] = evolution.turn_calls(since=0)
    assert (stored["compaction"], stored["calls"], stored["compactions"]) == ({"limit": 5}, [[1.0, 10, 5]], [2.0])

    assert policy.save("scope", {"profile": "a\x00"}) and policy.flush()
    assert harness_auto_policy.HarnessAutoPolicyStateStore().load("scope") == {"profile": "a�"}

    try:
        effectiveness.record_turn(decision=_decision("execution_o"),
                                  details={"status": "completed", "provider_wall_ms": 120}, result=None)
        effectiveness.flush()
    finally:
        effectiveness.close()
    [outcome] = harness_evolution_evaluation.load_outcomes(since=0)
    assert (outcome.execution_id, outcome.plan) == ("execution_o", ("inline_media_history",))
    with effectiveness.database.read() as connection:
        observation = dict(connection.execute(
            "SELECT execution_id, provider, load_family, signals_json, action_outcomes_json FROM harness_observations"
        ).fetchone())
        flags = connection.execute(f"SELECT {','.join(OBSERVATION_FLAGS)} FROM harness_observations").fetchone()
        assert set(column_types(connection, "harness_strategy_observations", OBSERVATION_COLUMNS).values()) == {
            "text" if legacy else "jsonb"
        }
        assert set(column_types(connection, "harness_strategy_observations", OBSERVATION_FLAGS).values()) == {
            "bigint" if legacy else "boolean"
        }
    assert [bool(flags[name]) for name in OBSERVATION_FLAGS] == [True, False]
    usage = {"input_tokens": 10, "cached_input_tokens": 4}
    called = turn_calls_from_rows(
        {**observation, "agent_id": SENDER, "recorded_at": 1.0, "checkpoint_json": "" if legacy else {}},
        [{"occurred_at": "2026-10-16T00:00:00Z", "usage_json": json_text(usage) if legacy else usage}], [],
    )
    assert called.calls == ((1792108800.0, 10, 4),)
    with evolution.database.read() as connection:
        assert {table: column_types(connection, table, columns) for table, columns in HARNESS_COLUMNS.items()} == {
            table: {column: "text" if legacy else "jsonb" for column in columns} for table, columns in HARNESS_COLUMNS.items()
        }


@COLUMN_TYPES
def test_a_deleted_search_stays_deleted_on_both_column_types(legacy):
    """已删除的检索在整数与布尔标志列上都保持删除并拒绝再次预留"""
    database = schema("flag_search")
    store = ServiceInvocationStore(database)
    if legacy:
        database.write(lambda connection: as_integer_flags(connection, "service_search_heads", ("deleted",)))
    assert store.cancel(job(), "knowledge", deleted=True)["fence_reason"] == "deleted"
    with pytest.raises(ServiceInvocationConflict, match="stale or deleted"):
        store.reserve(job(generation=2), "knowledge", {"plan_fingerprint": "plan"})
    with database.read() as connection:
        assert column_types(connection, "service_search_heads", ("deleted",)) == {
            "deleted": "bigint" if legacy else "boolean"}
