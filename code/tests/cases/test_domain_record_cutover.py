"""Domain migrations preserve records, materialize routes and reject data loss."""
import os
import uuid
from datetime import datetime

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb


def schema(connection, suffix):
    name = os.environ["PORTAL_DATABASE_NAMESPACE"] + "_" + suffix + uuid.uuid4().hex[:8]
    connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(name)))
    return name


def test_paper_cutover_preserves_more_than_a_thousand_records_and_review_content():
    """论文迁移保留千条以上记录评审正文及交接路径"""
    from management_portal import paper_state_store as store
    from scripts.migrate_paper_records import migrate
    with psycopg.connect(os.environ["PORTAL_DATABASE_URL"]) as c:
        name = schema(c, "paper")
        for kind in store.KINDS:
            c.execute(sql.SQL("CREATE TABLE {} (id TEXT PRIMARY KEY,position BIGINT,record JSONB)").format(sql.Identifier(kind)))
        c.execute("CREATE TABLE trigger_paths(lineage_id TEXT,occurrence_id TEXT,trigger_path_id TEXT)")
        c.execute("INSERT INTO trigger_paths VALUES ('lineage','event','path')")
        for number in range(1005):
            note = {"note_id": f"n{number}", "paper_id": "paper", "body": str(number), "created_at": 1700000000 + number}
            c.execute("INSERT INTO notes VALUES (%s,%s,%s)", (note["note_id"], number, Jsonb(note)))
        review = {"review_id": "review", "paper_id": "paper", "raw_response": "original"}
        c.execute("INSERT INTO review_jobs VALUES ('job',0,%s)", (Jsonb({"job_id": "job", "status": "done", "result": review}),))
        receipt = {"schema": "tfi.paper-resident-input.v1", "lineage_id": "lineage",
                   "event_id": "event", "invocation_id": "old"}
        c.execute("INSERT INTO deep_read_jobs VALUES ('deep',0,%s)",
                  (Jsonb({"job_id": "deep", "resident_invocation": receipt, "runner": "old"}),))
        result = migrate(c, schema=name, lineage_schema=name)
        assert result["records"]["notes"] == 1005
        assert c.execute("SELECT raw_response FROM reviews").fetchone()[0] == "original"
        assert c.execute("SELECT review_id FROM review_jobs").fetchone()[0] == "review"
        assert c.execute("SELECT resident_handoff FROM deep_read_jobs").fetchone()[0] == {
            "schema": receipt["schema"], "lineage_id": "lineage", "event_id": "event", "trigger_path_id": "path"}
        assert isinstance(c.execute("SELECT created_at FROM notes LIMIT 1").fetchone()[0], datetime)
        columns = {row[0] for row in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=ANY(%s)",
            (name, list(store.KINDS)))}
        assert not {"record", "position", "runner", "resident_invocation"} & columns


def test_model_api_cutover_preserves_columns_and_pins_the_effective_registration():
    """模型 API 迁移保留字段并固定当前有效路由登记"""
    from scripts.migrate_model_api_records import migrate
    with psycopg.connect(os.environ["PORTAL_DATABASE_URL"]) as c:
        name = schema(c, "model_apis")
        c.execute("""CREATE TABLE apis(api_id TEXT PRIMARY KEY,position BIGINT,entry_json JSONB,
                     version BIGINT,updated_at TEXT,updated_by TEXT)""")
        c.execute("""CREATE TABLE relay_resources(resource_id TEXT PRIMARY KEY,position BIGINT,
                     resource_json JSONB,updated_at TEXT,updated_by TEXT)""")
        for position, identifier, enabled in [(0, "off", False), (1, "current", True), (2, "spare", True)]:
            c.execute("INSERT INTO apis VALUES (%s,%s,%s,3,'2026-10-14T06:00:00Z','actor')",
                      (identifier, position, Jsonb({"kind": "openai_compatible_chat", "provider": "deepseek",
                                                   "enabled": enabled, "api_key_file": "/not-a-secret", "models": [{"id": "m"}]})))
        result = migrate(c, schema=name)
        assert result == {"apis": 3, "routes": [("dsh", "deepseek", "current")]}
        assert c.execute("SELECT models FROM apis WHERE api_id='current'").fetchone()[0] == [{"id": "m"}]
        assert c.execute("SELECT version,updated_by FROM apis WHERE api_id='current'").fetchone() == (3, "actor")
        assert isinstance(c.execute("SELECT updated_at FROM apis LIMIT 1").fetchone()[0], datetime)
        c.execute("DELETE FROM apis WHERE api_id='current'")
        assert c.execute("SELECT count(*) FROM routes").fetchone()[0] == 0


def test_chatroom_cutover_removes_order_and_enforces_room_ownership():
    """Chatroom 迁移移除顺序字段并约束成员归属房间"""
    from scripts.migrate_chatroom_relations import migrate
    with psycopg.connect(os.environ["PORTAL_DATABASE_URL"]) as c:
        name = schema(c, "chatrooms")
        c.execute("CREATE TABLE rooms(chatroom_id TEXT PRIMARY KEY,created_at TEXT,updated_at TEXT)")
        for table, key in (("members", "actor"), ("called_residents", "resident_id"), ("deliveries", "id")):
            c.execute(sql.SQL("CREATE TABLE {} (chatroom_id TEXT,{} TEXT,position BIGINT,PRIMARY KEY(chatroom_id,{}))").format(
                sql.Identifier(table), sql.Identifier(key), sql.Identifier(key)))
        c.execute("CREATE TABLE viewers(chatroom_id TEXT,actor TEXT,PRIMARY KEY(chatroom_id,actor))")
        c.execute("INSERT INTO rooms VALUES ('r','2026-10-14T06:00:00Z','')")
        c.execute("INSERT INTO members VALUES ('r','alice',4)")
        assert migrate(c, schema=name) == {"rooms": 1}
        assert c.execute("SELECT updated_at FROM rooms").fetchone() == (None,)
        c.execute("DELETE FROM rooms WHERE chatroom_id='r'")
        assert c.execute("SELECT count(*) FROM members").fetchone() == (0,)
        with pytest.raises(psycopg.errors.ForeignKeyViolation), c.transaction():
            c.execute("INSERT INTO members VALUES ('absent','alice')")
