import copy
import json
import os

import psycopg
import pytest

from management_common.principal import message_attribution
from scripts.migrate_message_origins import migrate, rewrite_message


SOURCES = {"archive.message-origin.v1": "tfi.message-origin.v1",
           "archive.message-origin.v2": "tfi.message-origin.v2"}


def message(mailbox=False):
    return {"message_id": "msg_preserved", "author_id": "mailbox" if mailbox else "user_example",
            "origin": {"schema": "archive.message-origin.v2" if mailbox else "archive.message-origin.v1",
                       "sender": {"kind": "mailbox" if mailbox else "user",
                                  "id": "mailbox" if mailbox else "user_example"},
                       "producer": {"kind": "service", "id": "portal"},
                       "initiated_by": {"kind": "user", "id": "user_example"}},
            "content_parts": [{"type": "text", "text": 'Keep archive.message-origin.v1 and "schema" verbatim.'}]}


@pytest.mark.parametrize("mailbox", [False, True])
def test_same_version_preserves_message_and_all_other_bytes(mailbox):
    value = {"message": message(mailbox), "untouched": {"schema": "archive.message-origin.v1"}}
    raw = json.dumps(value, indent=3)
    with pytest.raises(ValueError, match="origin schema"):
        message_attribution(value["message"])
    after = rewrite_message(raw, ("message",), SOURCES)
    expected = copy.deepcopy(value)
    expected["message"]["origin"]["schema"] = SOURCES[value["message"]["origin"]["schema"]]
    assert json.loads(after) == expected
    assert after == raw.replace(json.dumps(value["message"]["origin"]["schema"]),
                                json.dumps(expected["message"]["origin"]["schema"]), 1)
    assert message_attribution(expected["message"])["author_id"] == value["message"]["author_id"]
    assert rewrite_message(after, ("message",), SOURCES) == after


@pytest.mark.parametrize("field", ["author", "producer", "version"])
def test_invalid_identity_or_version_fails_closed(field):
    value = message()
    if field == "author":
        value["author_id"] = "different_user"
    elif field == "producer":
        value["origin"]["producer"] = {"kind": "user", "id": "user_example"}
    else:
        value["origin"]["schema"] = "archive.message-origin.v3"
    with pytest.raises(ValueError):
        rewrite_message(json.dumps(value), (), SOURCES)


@pytest.fixture
def database():
    with psycopg.connect(os.environ["PORTAL_DATABASE_URL"]) as c:
        # Temporary tables shadow the real names on the disposable test server.
        c.execute("CREATE TEMP TABLE segments (segment_id text primary key, metadata_json text, body bytea)")
        c.execute("CREATE TEMP TABLE agent_operations (operation_id text primary key, kind text, data_json text)")
        c.execute("CREATE TEMP TABLE resident_mailbox (resident_id text, message_id text, message_json text, primary key(resident_id,message_id))")
        c.commit()
        yield c


def populate(c, invalid=False):
    m = message()
    c.execute("INSERT INTO segments VALUES ('segment_a',%s,%s)", (json.dumps({"message": m}), b"immutable payload"))
    c.execute("INSERT INTO agent_operations VALUES ('artifact','message_artifact',%s)", (json.dumps({"origin": m["origin"]}),))
    c.execute("INSERT INTO agent_operations VALUES ('notice','notice',%s)", (json.dumps({"metadata": {"message": m}}),))
    box = message(True)
    if invalid:
        box["author_id"] = "different_user"
    c.execute("INSERT INTO resident_mailbox VALUES ('resident_example','msg_preserved',%s)", (json.dumps(box),))
    c.commit()


def run(c, tmp_path, **options):
    return migrate(c, agent_schema="pg_temp", lineage_schema="pg_temp", sources=SOURCES,
                   backup_path=tmp_path / "backup.private.jsonl", **options)


def test_transaction_dry_run_apply_retry_and_backup(database, tmp_path):
    populate(database)
    before = database.execute("SELECT metadata_json,body FROM segments").fetchone()
    database.commit()
    plan = run(database, tmp_path)
    assert plan["selected"] == 4 and not (tmp_path / "backup.private.jsonl").exists()
    assert database.execute("SELECT metadata_json,body FROM segments").fetchone() == before
    database.commit()
    result = run(database, tmp_path, apply=True, expected_plan=plan["plan_sha256"])
    assert result["updated"] == 4
    backup = (tmp_path / "backup.private.jsonl").read_text()
    assert "archive.message-origin.v1" in backup
    after = database.execute("SELECT metadata_json,body FROM segments").fetchone()
    assert after[1] == before[1]
    message_attribution(json.loads(after[0])["message"])
    database.commit()
    assert run(database, tmp_path)["selected"] == 0
    with pytest.raises(FileExistsError):
        run(database, tmp_path, apply=True)


def test_invalid_row_or_stale_plan_never_partially_updates(database, tmp_path):
    populate(database, invalid=True)
    with pytest.raises(ValueError, match="author"):
        run(database, tmp_path, apply=True)
    assert "archive.message-origin.v1" in database.execute("SELECT metadata_json FROM segments").fetchone()[0]


def test_failure_after_first_table_update_rolls_back_and_retains_backup(database, tmp_path):
    populate(database)
    database.execute("CREATE FUNCTION pg_temp.reject_origin_update() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'isolated update failure'; END $$")
    database.execute("CREATE TRIGGER reject_origin BEFORE UPDATE ON agent_operations FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_origin_update()")
    database.commit()
    with pytest.raises(psycopg.errors.RaiseException, match="isolated update failure"):
        run(database, tmp_path, apply=True)
    assert (tmp_path / "backup.private.jsonl").exists()
    assert "archive.message-origin.v1" in database.execute("SELECT metadata_json FROM segments").fetchone()[0]
    database.execute("UPDATE resident_mailbox SET message_json=%s", (json.dumps(message(True)),))
    database.commit()
    with pytest.raises(ValueError, match="plan"):
        run(database, tmp_path, apply=True, expected_plan="0" * 64)
    assert "archive.message-origin.v1" in database.execute("SELECT metadata_json FROM segments").fetchone()[0]
