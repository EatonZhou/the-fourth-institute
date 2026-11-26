"""Lineage blackboard: graph objects, entries, retention, read positions and the entry Event (plan_786a2906c941).

:module: isa.instructions
:design: decision_ed2c1ed5c245
"""

from __future__ import annotations

import threading
import uuid

import pytest

from management_portal.society_service import blackboard_storage, project_folders
from management_portal.society_service.isa.common import store
from tests.cases.isa_test_support import ACTOR, ok, reset_kernels, run
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401 - fixture


@pytest.fixture
def lineage(future_creation_store, monkeypatch):  # noqa: F811 - pytest fixture
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    reset_kernels()
    created = ok("CreateLineage", {"title": "Team", "project_id": "project_test"}, key="lineage")
    yield created["lineage_id"]
    reset_kernels()


def resident(lineage_id: str, title: str) -> str:
    return ok("CreateResident", {"lineage_id": lineage_id, "title": title, "provider": "codex"}, key=f"r-{title}")["resident_id"]


def definition(lineage_id: str) -> dict:
    return ok("GetLineage", {"lineage_id": lineage_id})["lineage"]


def make_board(lineage_id: str, *, retention: dict | None = None, attach: dict[str, str] | None = None, key: str = "board") -> str:
    """A blackboard attached to the given Residents (resident id -> access)."""

    operations = [{"op": "create", "kind": "component", "ref": "board", "value": {
        "kind": "blackboard", "title": "Notes", "config": {"retention": retention or {}}}}]
    for resident_id, access in (attach or {}).items():
        operations.append({"op": "create", "kind": "wire", "value": {
            "kind": "attach", "source": resident_id, "target": "$ref:board", "config": {"access": access}}})
    result = ok("ApplyLineageChanges", {"lineage_id": lineage_id, "operations": operations}, key=key)
    return result["refs"]["board"]


def write(lineage_id, board_id, text, *, actor=ACTOR, key=None, **extra):
    return run("CreateBoardEntry", {"lineage_id": lineage_id, "board_id": board_id, "text": text, **extra},
               actor=actor, key=key or "w-" + uuid.uuid4().hex)


def read(lineage_id, board_id, *, actor=ACTOR, **extra):
    return run("ListBoardEntries", {"lineage_id": lineage_id, "board_id": board_id, **extra}, actor=actor)


def test_blackboard_and_attach_wires_live_in_the_lineage_definition(lineage):
    """黑板和 attach 连线通过 ApplyLineageChanges 创建，随 Lineage 定义读回，删除 Resident 时连线随之删除"""
    reader, writer = resident(lineage, "Reader"), resident(lineage, "Writer")
    board = make_board(lineage, attach={reader: "read", writer: "read_write"})
    program = definition(lineage)["program"]
    found = [c for c in program["components"] if c["kind"] == "blackboard"]
    assert [c["id"] for c in found] == [board]
    assert found[0]["config"]["retention"] == {"max_entries": 200, "max_total_bytes": 1048576}
    wires = {w["source"]: w for w in program["wires"] if w["kind"] == "attach"}
    assert {k: v["config"]["access"] for k, v in wires.items()} == {reader: "read", writer: "read_write"}
    assert all(w["target"] == board for w in wires.values())
    ok("ApplyLineageChanges", {"lineage_id": lineage, "operations": [{"op": "delete", "kind": "resident", "id": reader}]},
       key="drop-reader")
    after = definition(lineage)["program"]
    assert {w["source"] for w in after["wires"] if w["kind"] == "attach"} == {writer}
    assert any(c["id"] == board for c in after["components"])


def test_attach_wire_rules(lineage):
    """attach 只连 Resident 到黑板，access 必须是 read、write 或 read_write"""
    member = resident(lineage, "Member")
    board = make_board(lineage)
    bad = run("ApplyLineageChanges", {"lineage_id": lineage, "operations": [{"op": "create", "kind": "wire", "value": {
        "kind": "attach", "source": member, "target": board, "config": {"access": "admin"}}}]}, key="bad-access")
    assert bad.error is not None
    other = run("ApplyLineageChanges", {"lineage_id": lineage, "operations": [{"op": "create", "kind": "wire", "value": {
        "kind": "attach", "source": member, "target": member, "config": {"access": "read"}}}]}, key="bad-target")
    assert other.error is not None
    unknown = run("ApplyLineageChanges", {"lineage_id": lineage, "operations": [{"op": "create", "kind": "component", "value": {
        "kind": "blackboard", "config": {"retention": {"max_entries": 5000}}}}]}, key="bad-retention")
    assert unknown.error is not None


def test_connected_access_decides_who_reads_and_writes(lineage):
    """连接 read 的 Resident 不能写，未连接的 Resident 不能读写，write 连线不能读"""
    reader, writer, only_write, stranger = (resident(lineage, name) for name in ("R", "W", "O", "S"))
    board = make_board(lineage, attach={reader: "read", writer: "read_write", only_write: "write"})
    assert write(lineage, board, "hello", actor=writer).error is None
    assert write(lineage, board, "nope", actor=reader).error is not None
    assert write(lineage, board, "nope", actor=stranger).error is not None
    assert read(lineage, board, actor=stranger).error is not None
    assert read(lineage, board, actor=only_write).error is not None
    listing = read(lineage, board, actor=reader).raise_for_error()
    assert [e["text"] for e in listing["entries"]] == ["hello"]
    assert listing["entries"][0]["author_id"] == writer


def test_overflow_purges_the_oldest_and_reports_earliest_seq(lineage):
    """超限写入后最旧条目被清理，读取返回当前最早保留的序号，序号不重用"""
    board = make_board(lineage, retention={"max_entries": 3})
    for number in range(5):
        result = write(lineage, board, f"n{number}").raise_for_error()
    assert result["latest_seq"] == 5 and result["earliest_seq"] == 3
    listing = read(lineage, board).raise_for_error()
    assert [e["seq"] for e in listing["entries"]] == [3, 4, 5]
    assert listing["earliest_seq"] == 3 and listing["latest_seq"] == 5
    assert write(lineage, board, "n5").raise_for_error()["entry"]["seq"] == 6


def test_byte_limit_and_expiry_purge(lineage):
    """总字节超限从最旧开始清理；过期条目写入时清理，读取时也不返回"""
    board = make_board(lineage, retention={"max_total_bytes": blackboard_storage.MIN_TOTAL_BYTES})
    chunk = "x" * 9000
    for number in range(10):
        write(lineage, board, chunk + str(number)).raise_for_error()
    listing = read(lineage, board).raise_for_error()
    assert sum(len(e["text"]) for e in listing["entries"]) <= blackboard_storage.MIN_TOTAL_BYTES
    assert listing["earliest_seq"] > 1 and listing["latest_seq"] == 10

    expiring = make_board(lineage, key="board-ttl", retention={"max_entry_ttl": 3600})
    too_late = write(lineage, expiring, "late", expires_at="2999-01-01T00:00:00Z")
    assert too_late.error is not None
    # An entry past its expiry is not returned, whatever has been purged.
    def expire(connection):
        connection.execute("UPDATE blackboard_entries SET expires_at='2000-01-01T00:00:00Z' WHERE board_id=%s", (expiring,))
    write(lineage, expiring, "gone", expires_at=_in_a_minute()).raise_for_error()
    store().database.write(expire)
    assert read(lineage, expiring).raise_for_error()["entries"] == []


def _in_a_minute() -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()


def test_retraction_keeps_the_sequence_and_the_author_rule(lineage):
    """撤回清空正文、data 和 refs 并保留序号；Lineage 内写连线的 Resident 可撤回任何条目，其他调用者只能撤回自己的"""
    first, second = resident(lineage, "First"), resident(lineage, "Second")
    board = make_board(lineage, attach={first: "read_write", second: "read_write"})
    entry = write(lineage, board, "secret", actor=first, data={"a": 1}, refs=["article_a"]).raise_for_error()["entry"]
    own = write(lineage, board, "mine").raise_for_error()["entry"]
    retracted = run("DeleteBoardEntry", {"lineage_id": lineage, "board_id": board, "entry_id": entry["entry_id"]},
                    actor=second).raise_for_error()
    assert retracted["retracted"] is True
    body = retracted["entry"]
    assert (body["seq"], body["text"], body["data"], body["refs"], body["retracted"]) == (1, "", None, [], True)
    # A user outside the Lineage's Residents may only retract what they wrote.
    assert run("DeleteBoardEntry", {"lineage_id": lineage, "board_id": board, "entry_id": own["entry_id"]}).error is None
    other = write(lineage, board, "first's", actor=first).raise_for_error()["entry"]
    assert run("DeleteBoardEntry", {"lineage_id": lineage, "board_id": board, "entry_id": other["entry_id"]}).error is not None
    listing = read(lineage, board, actor=first).raise_for_error()
    assert [(e["seq"], e["retracted"]) for e in listing["entries"]] == [(1, True), (2, True), (3, False)]


def test_idempotent_retries_do_not_duplicate_entries(lineage):
    """同一个幂等键重试不产生重复条目，返回同一个条目"""
    board = make_board(lineage)
    first = write(lineage, board, "once", key="same").raise_for_error()
    second = write(lineage, board, "once", key="same")
    assert second.replayed is True or second.raise_for_error()["entry"]["entry_id"] == first["entry"]["entry_id"]
    assert [e["text"] for e in read(lineage, board).raise_for_error()["entries"]] == ["once"]


def test_read_position_persists_and_never_moves_back(lineage):
    """已读位置按读者保存，只前进；不给 cursor 的读取从已读位置之后开始"""
    reader = resident(lineage, "Reader")
    board = make_board(lineage, attach={reader: "read"})
    writer_board = board
    for number in range(4):
        write(lineage, writer_board, f"m{number}").raise_for_error()
    assert [e["seq"] for e in read(lineage, board, actor=reader).raise_for_error()["entries"]] == [1, 2, 3, 4]
    moved = run("SetBoardReadPosition", {"lineage_id": lineage, "board_id": board, "seq": 3}, actor=reader).raise_for_error()
    assert moved["read_seq"] == 3 and moved["advanced"] is True
    back = run("SetBoardReadPosition", {"lineage_id": lineage, "board_id": board, "seq": 1}, actor=reader).raise_for_error()
    assert back["read_seq"] == 3 and back["advanced"] is False
    assert [e["seq"] for e in read(lineage, board, actor=reader).raise_for_error()["entries"]] == [4]
    assert run("SetBoardReadPosition", {"lineage_id": lineage, "board_id": board, "seq": 99}, actor=reader).error is not None
    # The creator of the entries has a position of their own.
    assert [e["seq"] for e in read(lineage, board).raise_for_error()["entries"]] == [1, 2, 3, 4]


def test_paging_filters_and_supersedes(lineage):
    """分页游标、作者与 kind 筛选，supersedes 让较早条目带 superseded_by"""
    board = make_board(lineage)
    write(lineage, board, "claim a", kind="claim").raise_for_error()
    write(lineage, board, "note", kind="note").raise_for_error()
    write(lineage, board, "claim a v2", kind="claim", supersedes=1).raise_for_error()
    page = read(lineage, board, limit=2).raise_for_error()
    assert [e["seq"] for e in page["entries"]] == [1, 2] and page["next_cursor"] == "2"
    assert page["entries"][0]["superseded_by"] == 3
    rest = read(lineage, board, cursor=page["next_cursor"]).raise_for_error()
    assert [e["seq"] for e in rest["entries"]] == [3] and rest["next_cursor"] == ""
    assert [e["seq"] for e in read(lineage, board, kind="claim").raise_for_error()["entries"]] == [1, 3]
    assert [e["seq"] for e in read(lineage, board, author_id=ACTOR).raise_for_error()["entries"]] == [1, 2, 3]
    assert write(lineage, board, "bad", supersedes=9).error is not None
    assert write(lineage, board, "x" * 20000).error is not None


def test_concurrent_writes_get_distinct_increasing_seq(lineage):
    """并发写入的 seq 不重复、不乱序"""
    board = make_board(lineage)
    results, errors = [], []

    def worker(index: int) -> None:
        outcome = write(lineage, board, f"c{index}", key=f"c-{index}")
        (errors if outcome.error else results).append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    listing = read(lineage, board, limit=200).raise_for_error()
    seqs = [e["seq"] for e in listing["entries"]]
    assert seqs == list(range(1, 9))


def test_deleting_the_board_removes_its_entries(lineage):
    """删除黑板同时删除条目和已读位置，被删除的 ID 不能重用"""
    board = make_board(lineage)
    write(lineage, board, "bye").raise_for_error()
    ok("ApplyLineageChanges", {"lineage_id": lineage, "operations": [{"op": "delete", "kind": "component", "id": board}]},
       key="drop-board")
    assert read(lineage, board).error is not None
    reuse = run("ApplyLineageChanges", {"lineage_id": lineage, "operations": [{"op": "create", "kind": "component", "value": {
        "id": board, "kind": "blackboard", "config": {}}}]}, key="reuse")
    assert reuse.error is not None


def test_entry_event_source_fires_once_per_entry_only_with_subscribers(lineage):
    """blackboard.entry_created 每个条目一次，不含正文；没有订阅者时基线前进但不产生 Event"""
    from management_portal.society_service import lineage_blackboard_events as events
    from management_portal.society_service.lineage_program_compilation import compile_program

    reader = resident(lineage, "Listener")
    board = make_board(lineage, attach={reader: "read_write"})
    created = ok("ApplyLineageChanges", {"lineage_id": lineage, "operations": [
        {"op": "create", "kind": "component", "ref": "src", "value": {
            "kind": "event_source", "config": {"event_type": "blackboard.entry_created", "entry_kind": "claim"}}},
        {"op": "create", "kind": "wire", "value": {
            "kind": "watch", "source": "$ref:src", "target": board, "source_port": "watch"}},
    ]}, key="source")
    source_id = created["refs"]["src"]
    lineage_value = definition(lineage)
    plan = compile_program(lineage_value["program"], lineage_value["nodes"])
    source = plan["event_sources"][source_id]
    assert source["subject_blackboard_id"] == board and source["entry_kind"] == "claim"
    assert list(events.entry_sources(plan)) == [source_id]
    write(lineage, board, "before", kind="claim").raise_for_error()
    facts = events.prefetch(lineage, plan)
    assert facts[source_id]["base_signature"] is None and facts[source_id]["cursor"] == 1
    entry = {"entry_id": "bbe_x", "seq": 2, "author_id": reader, "kind": "claim", "created_at": "2026-11-27T00:00:00Z"}
    event = events.entry_event(source_id, board, entry)
    assert event.type == "blackboard.entry_created"
    assert event.payload == {"board_id": board, "entry_id": "bbe_x", "seq": 2, "author_id": reader, "kind": "claim"}


class _Scan:
    """What the observer needs of a pass: the prefetched facts, the baseline it recognized and what it reports."""

    def __init__(self, facts, runtime_sources=None):
        from management_portal.society_service.lineage_event_observers import Facts

        self.facts = Facts(blackboard_entries=facts)
        self.runtime_sources = runtime_sources or {}
        self.baseline_count = 0
        self.observed, self.committed, self.emitted = [], [], []

    def observe(self, source, locator, signature, value):
        self.observed.append(value)
        return {"status": "baseline"}

    def commit(self, source, locator, previous, signature, value):
        self.committed.append(value)

    def emit(self, source, event, cursor, **commit):
        self.emitted.append((event, cursor, commit))


def _source(config=None, routes=True):
    from management_portal.society_service.lineage_event_observers import Source

    return Source("event_source_one", {"event_type": "blackboard.entry_created", "subscriber_routes": [{"wire_id": "w"}] if routes else [],
                                       **(config or {})}, "", False)


def _facts(base, entries, cursor=None):
    from management_portal.society_service import lineage_blackboard_events as events

    return {"event_source_one": {"board_id": "blackboard_one", "locator": events.locator("blackboard_one"),
                                 "base_signature": events.cursor_signature(base) if base is not None else None,
                                 "base_cursor": base, "cursor": cursor if cursor is not None else (entries[-1]["seq"] if entries else base),
                                 "entries": entries}}


def _stored(base):
    from management_portal.society_service import lineage_blackboard_events as events

    return {"event_source_one": {"observations": {"": {"state_locator": events.locator("blackboard_one"),
                                                       "state_signature": events.cursor_signature(base),
                                                       "state_value": events.cursor_value(base)}}}}


def test_observer_first_look_sets_the_baseline_then_fires_once_per_entry(monkeypatch):
    """观察器首次只记基线；之后每个条目一个 Event，按 kind 过滤，没有订阅者时只前进基线"""
    from management_portal.society_service import lineage_event_observers as observers
    from management_portal.society_service import lineage_program_observations as observations

    entries = [{"entry_id": f"bbe_{n}", "seq": n, "author_id": "a", "kind": "claim" if n != 2 else "note",
                "created_at": "2026-11-27T00:00:00Z"} for n in (3, 4, 5)]
    entries[1]["kind"] = "note"
    first = _Scan(_facts(None, [], cursor=2))
    observers._blackboard_entry_created(first, _source())
    assert first.observed == ["blackboard_seq:2"] and first.baseline_count == 1 and not first.emitted

    monkeypatch.setattr(observations, "observation_state", lambda sources, source_id, subject: sources[source_id]["observations"][subject])
    scan = _Scan(_facts(2, entries), _stored(2))
    observers._blackboard_entry_created(scan, _source({"entry_kind": "claim"}))
    assert [event.payload["seq"] for event, _cursor, _commit in scan.emitted] == [3, 5]
    assert all("text" not in event.payload for event, _c, _m in scan.emitted)
    assert [commit["state_value"] for _e, _c, commit in scan.emitted] == ["blackboard_seq:3", "blackboard_seq:5"]
    assert scan.emitted[1][2]["previous_state_signature"] == scan.emitted[0][2]["state_signature"]

    quiet = _Scan(_facts(2, entries), _stored(2))
    observers._blackboard_entry_created(quiet, _source(routes=False))
    assert not quiet.emitted and quiet.committed == ["blackboard_seq:5"]


def test_purging_a_lineage_removes_boards_entries_and_positions(lineage):
    """删除 Lineage 时黑板、条目、已读位置和连线一并清除"""
    reader = resident(lineage, "Reader")
    board = make_board(lineage, attach={reader: "read_write"})
    write(lineage, board, "kept until the Lineage goes").raise_for_error()
    run("SetBoardReadPosition", {"lineage_id": lineage, "board_id": board, "seq": 1}, actor=reader).raise_for_error()

    def purge(connection):
        blackboard_storage.purge_lineage(connection, lineage)
        return {table: connection.execute(f"SELECT count(*) AS n FROM {table} WHERE board_id=%s", (board,)).fetchone()["n"]
                for table in ("blackboard_entries", "blackboard_read_positions", "blackboard_heads")}

    assert store().database.write(purge) == {"blackboard_entries": 0, "blackboard_read_positions": 0, "blackboard_heads": 0}
    with store().database.read() as connection:
        assert blackboard_storage.active_objects(connection, lineage) == {}


class _Rollback(Exception):
    pass


def test_schema_without_blackboard_tables_reads_as_no_persisted_blackboards(lineage):
    """升级冷读：已有 schema 缺黑板表时按无持久黑板处理，真实 SQL 错误仍然传播"""
    outcome: dict = {}

    def cold(connection):
        connection.execute("ALTER TABLE lineage_blackboards RENAME TO lineage_blackboards_hidden")
        connection.execute("ALTER TABLE lineage_blackboard_bindings RENAME TO lineage_blackboard_bindings_hidden")
        outcome["read_many"] = blackboard_storage.read_many(connection, [lineage])
        outcome["active"] = blackboard_storage.active_objects(connection, lineage)
        outcome["deleted"] = blackboard_storage.deleted_ids(connection, ["component_x"])
        outcome["owner"] = blackboard_storage.lineage_for_object(connection, "component_x")
        blackboard_storage.delete_missing(connection, lineage, ["component_x"], None)
        blackboard_storage.purge_lineage(connection, lineage)
        blackboard_storage.transfer(connection, lineage, "lineage_other", ["component_x"])
        raise _Rollback

    with pytest.raises(_Rollback):
        store().database.write(cold)
    assert outcome == {"read_many": {lineage: {"components": [], "wires": []}}, "active": {},
                       "deleted": [], "owner": ""}

    def broken(connection):
        connection.execute("ALTER TABLE lineage_blackboards RENAME COLUMN deleted_at TO deleted_when")
        with pytest.raises(Exception) as caught:
            blackboard_storage.read_many(connection, [lineage])
        outcome["error"] = type(caught.value).__name__
        raise _Rollback

    with pytest.raises(_Rollback):
        store().database.write(broken)
    assert outcome["error"] == "UndefinedColumn"
