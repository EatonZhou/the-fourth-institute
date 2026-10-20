"""resident.message_received: one Event per arriving input, sent by the message's author (decision_44cf587c1721)."""

from __future__ import annotations

from management_portal.society_service import lineage_message_events as events
from management_portal.society_service import lineage_primitives
from management_portal.society_service.resident_conversation_stream import INPUT_STATE_CHANGED, QUEUE_CHANGED


def _input(cursor: int, message_id: str, state: str) -> dict:
    return {
        "event_type": INPUT_STATE_CHANGED,
        "cursor": cursor,
        "occurred_at": f"2026-10-02T00:00:{cursor:02d}Z",
        "payload": {"input_state": {"message_id": message_id, "state": state}},
    }


def test_first_arrival_state_of_each_message_becomes_one_arrival():
    """每条消息只按第一次到达状态产生一次到达

    对话日志中同一消息的 queued、running 只记一次到达，终态不算到达。

    :design: decision_44cf587c1721, decision_4adcc1ff2f2c
    """
    log = [
        _input(11, "msg_a", "queued"),
        {"event_type": QUEUE_CHANGED, "cursor": 12, "payload": {}},
        _input(13, "msg_a", "running"),
        _input(14, "msg_b", "running"),
        _input(15, "msg_a", "completed"),
        _input(16, "msg_c", "cancelled"),
    ]

    assert events.message_arrivals(log) == [
        {"message_id": "msg_a", "cursor": 11, "occurred_at": "2026-10-02T00:00:11Z"},
        {"message_id": "msg_b", "cursor": 14, "occurred_at": "2026-10-02T00:00:14Z"},
    ]


def test_relay_depth_follows_facade_business_refs():
    """转交深度沿门面转交标注逐层计数

    消息的 business_ref 指向门面消息时，relay_depth 按经过的门面数递增。

    :design: decision_44cf587c1721
    """
    refs = {
        ("resident_cccccccccccc", "msg_3"): "resident_bbbbbbbbbbbb/message/msg_2",
        ("resident_bbbbbbbbbbbb", "msg_2"): "resident_aaaaaaaaaaaa/message/msg_1",
        ("resident_aaaaaaaaaaaa", "msg_1"): "paper_123456789abc",
    }

    def lookup(resident_id: str, message_id: str) -> str:
        return refs.get((resident_id, message_id), "")

    assert events.relay_depth("resident_cccccccccccc", "msg_3", lookup) == 2
    assert events.relay_depth("resident_aaaaaaaaaaaa", "msg_1", lookup) == 0


def test_relay_depth_stops_at_cycles_and_the_lookup_limit():
    """转交深度遇到环或查找上限时停止

    转交标注形成环或超过查找上限时，relay_depth 停止增长而不是无限追溯。

    :design: decision_44cf587c1721
    """
    def cycle(resident_id: str, message_id: str) -> str:
        return "resident_aaaaaaaaaaaa/message/msg_1" if message_id == "msg_2" else "resident_bbbbbbbbbbbb/message/msg_2"

    def endless(resident_id: str, message_id: str) -> str:
        return f"resident_aaaaaaaaaaaa/message/{message_id}x"

    assert events.relay_depth("resident_bbbbbbbbbbbb", "msg_2", cycle) == 1
    assert events.relay_depth("resident_aaaaaaaaaaaa", "m", endless, limit=3) == 3


def test_cursor_values_round_trip_and_reject_foreign_values():
    """消息到达游标可往返且拒绝外来值

    对话游标写成运行基线后能原样解析，其他格式的基线值被视为无效。

    :design: decision_44cf587c1721
    """
    assert events.parse_cursor_value(events.cursor_value(42)) == 42
    assert events.parse_cursor_value("active:msg_1") is None
    assert events.cursor_signature(1) != events.cursor_signature(2)


def test_sources_watch_a_resident_and_the_event_type_is_registered():
    """resident.message_received 事件源观察指定 Resident

    事件类型已登记，事件源通过 watch 连线绑定被观察的 Resident。

    :design: decision_44cf587c1721, decision_4adcc1ff2f2c
    """
    plan = {"event_sources": {
        "es_1": {"event_type": "resident.message_received", "subject_resident_id": "resident_aaaaaaaaaaaa"},
        "es_2": {"event_type": "resident.message_received", "subject_resident_id": ""},
        "es_3": {"event_type": "resident.work_finished", "subject_resident_id": "resident_aaaaaaaaaaaa"},
    }}

    assert list(events.message_received_sources(plan)) == ["es_1"]
    assert "resident.message_received" in lineage_primitives.RUNTIME_EVENT_TYPES
    event = lineage_primitives.make_event(
        "resident.message_received",
        subject_resident_id="resident_aaaaaaaaaaaa",
        payload={"resident_id": "resident_aaaaaaaaaaaa", "message_id": "msg_a", "author_id": "CASE-A", "relay_depth": 0},
    )
    assert event.payload["relay_depth"] == 0


def test_sender_becomes_the_initiator_of_the_event():
    """消息发送者成为到达 Event 的发起者

    到达 Event 的身份以 Lineage 自动运行身份执行，发起者记为原发送者。

    :design: decision_44cf587c1721
    """
    identity = events._sender_identity("resident_aaaaaaaaaaaa", "CASE-A")

    assert identity["initiated_by"]["id"] == "CASE-A"
    assert events._sender_identity("resident_aaaaaaaaaaaa", "") is None


# -- through the reconciliation pass --------------------------------------------------

import pytest

from management_portal.society_service import lineage_domain
from tests.cases.test_lineage_generic_primitives import primitive_store, ACTOR, WORK_A, WORK_B, _worker  # noqa: F401
from tests.cases import society_service_test_support as support


class _Conversation:
    def __init__(self, state_repository) -> None:
        self.state_repository = state_repository
        self.log: list[dict] = []
        self.messages = {
            "msg_a": {"message_id": "msg_a", "author_id": "CASE-A"},
            "msg_b": {"message_id": "msg_b", "author_id": "sample_admin"},
        }
        self.refs = {"msg_b": "resident_cccccccccccc/message/msg_outer"}

    def state_snapshots(self, resident_ids):
        return self.state_repository.state_snapshots(resident_ids)

    def events_after(self, resident_id, after_cursor, *, limit=500):
        after = int(after_cursor)
        events = [event for event in self.log if event["cursor"] > after][:limit]
        return {"resident_id": resident_id, "cursor": len(self.log), "events": events}

    def message(self, resident_id, message_id):
        return self.messages.get(message_id)

    def message_business_ref(self, resident_id, message_id):
        return self.refs.get(message_id, "")

    def input_state(self, resident_id, message_id):
        if not any(event["payload"]["input_state"]["message_id"] == message_id for event in self.log):
            raise KeyError(message_id)
        return {"message_id": message_id, "state": "queued", "updated_at": "2026-10-03T00:00:00Z"}


@pytest.fixture
def conversation(monkeypatch):
    support.install_registered_lineage_members(monkeypatch)
    fake = _Conversation(events.resident_conversation_stream.get_repository())
    fake.log.append(_input(1, "msg_old", "queued"))
    monkeypatch.setattr(events.resident_conversation_stream, "get_repository", lambda: fake)
    return fake


def test_reconciliation_emits_each_new_message_once(primitive_store, conversation):
    """对账为每条新消息只产生一次到达 Event

    首次观察只建立基线，之后每条新消息产生一次 Event，作者、转交深度和发起者正确。

    :design: decision_44cf587c1721, decision_4adcc1ff2f2c
    """
    current = support.create_lineage_with_resident_members({
        "run_as_principal": {"kind": "user", "id": ACTOR},
        "title": "message received",
        "nodes": [_worker(WORK_A), _worker(WORK_B)],
        "program": {
            "components": [{"id": "event_source_inbox", "kind": "event_source",
                            "config": {"event_type": "resident.message_received"}}],
            "wires": [{"id": "wire_watch", "kind": "watch", "source": "event_source_inbox", "source_port": "watch",
                       "target": WORK_A}],
        },
    }, ACTOR)
    lineage_id = current["lineage_id"]

    def reconcile():
        return lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)["events"]

    assert reconcile() == []  # baseline: the message already queued does not count
    conversation.log.extend([
        _input(2, "msg_a", "queued"),
        _input(3, "msg_a", "running"),
        _input(4, "msg_b", "queued"),
    ])
    emitted = [item["event"] for item in reconcile()]

    assert [event["type"] for event in emitted] == ["resident.message_received"] * 2
    assert [event["payload"]["message_id"] for event in emitted] == ["msg_a", "msg_b"]
    assert emitted[0]["payload"]["author_id"] == "CASE-A"
    assert emitted[0]["payload"]["relay_depth"] == 0
    assert emitted[1]["payload"]["relay_depth"] == 1
    assert emitted[0]["identity"]["initiated_by"]["id"] == "CASE-A"
    assert all(item["event"]["payload"]["message_id"] not in {"msg_a", "msg_b"} for item in reconcile())


def test_a_message_to_a_watched_resident_wakes_its_lineage(monkeypatch):
    """被观察的 Resident 收到消息后立即派发或唤醒对账

    有到达处理器时立即派发这条消息，没有时立即唤醒所属 Lineage 的对账。

    :design: decision_44cf587c1721
    """
    from management_portal.society_service import changes, lineage_message_events as events
    from management_portal.society_service import state_store

    resident = "resident_aaaaaaaaaaaa"
    definition = {"program": {
        "components": [{"id": "event_source_inbox", "kind": "event_source",
                        "config": {"event_type": "resident.message_received"}}],
        "wires": [{"id": "wire_watch", "kind": "watch", "source": "event_source_inbox", "target": resident}],
    }}
    assert events.watched_by_message_sources(definition, resident)
    assert not events.watched_by_message_sources(definition, "resident_bbbbbbbbbbbb")
    assert not events.watched_by_message_sources({"program": {"components": [], "wires": []}}, resident)

    woken = []
    monkeypatch.setattr(changes, "lineage_changed", lambda lineage_id, runtime=False: woken.append((lineage_id, runtime)))

    class Store:
        def lineage_definition(self, lineage_id):
            return definition

    monkeypatch.setattr(state_store, "get_lineage_data_store", lambda: Store())
    record = {"resident_id": resident, "lineage_membership": {"lineage_id": "lineage_000000000001"}}
    monkeypatch.setattr(events, "_facade_record", lambda resident_id: None)
    from management_portal.society_service import resident_lineage
    monkeypatch.setattr(resident_lineage, "lineage_id", lambda value: "lineage_000000000001")
    monkeypatch.setattr(resident_lineage, "resident_id", lambda value: value["resident_id"])

    assert events.wake_watchers(record) is True
    assert woken == [("lineage_000000000001", True)]
    assert events.wake_watchers({**record, "resident_id": "resident_bbbbbbbbbbbb"}) is False

    # With an arrival handler (SocietyService), the one message is dispatched at once instead.
    pushed = []
    monkeypatch.setattr(events, "_ARRIVAL_HANDLER", lambda *arrival: pushed.append(arrival))
    assert events.wake_watchers(record, "msg_a") is True
    assert pushed == [("lineage_000000000001", resident, "msg_a")]
    assert woken == [("lineage_000000000001", True)]


def test_a_facade_message_waits_until_it_is_relayed_or_finished(monkeypatch):
    """门面消息在转交或结束前一直等待 Router

    已转交或已结束的门面消息不再产生到达 Event，仍在排队的消息继续等待。

    :design: decision_44cf587c1721
    """
    from management_portal.society_service import family_facade, lineage_message_events as events

    states = {"msg_waiting": "queued", "msg_done": "completed", "msg_relayed": "completed"}

    class Repository:
        def input_state(self, resident_id, message_id):
            if message_id not in states:
                raise KeyError(message_id)
            return {"state": states[message_id]}

    monkeypatch.setattr(family_facade, "relay_links", lambda facade: {"msg_relayed": {"resident_id": "resident_x"}})
    facade = {"resident_id": "resident_ffffffffffff"}

    assert events._facade_message_waiting(Repository(), facade, "msg_waiting")
    assert not events._facade_message_waiting(Repository(), facade, "msg_done")
    assert not events._facade_message_waiting(Repository(), facade, "msg_relayed")
    assert events._facade_message_waiting(Repository(), facade, "msg_unknown")


def test_an_arrival_is_dispatched_at_once_and_the_pass_only_moves_past_it(primitive_store, conversation, monkeypatch):
    """消息到达即派发 Event，对账不再重复

    并行连线的到达 Event 在接收后立即派发；之后的对账发现同一事件键已触发，只推进基线、不再执行 Effect。

    :design: decision_44cf587c1721
    """
    from management_portal.society_service import lineage_program_polling, lineage_program_runtime

    current = support.create_lineage_with_resident_members({
        "run_as_principal": {"kind": "user", "id": ACTOR},
        "title": "message received push",
        "nodes": [_worker(WORK_A), _worker(WORK_B)],
        "program": {
            "components": [
                {"id": "event_source_inbox", "kind": "event_source", "config": {"event_type": "resident.message_received"}},
                {"id": "effect_route", "kind": "effect", "config": {"script": "output routed=yes", "endpoints": []}},
            ],
            "wires": [
                {"id": "wire_watch", "kind": "watch", "source": "event_source_inbox", "source_port": "watch",
                 "target": WORK_A},
                # A Router-like Effect: no ordering, so its Events are pushed as messages arrive.
                {'id': 'wire_route', 'kind': 'route', 'source': 'event_source_inbox', 'target': 'effect_route', 'config': {'parallel': True}},
            ],
        },
    }, ACTOR)
    lineage_id = current["lineage_id"]
    monkeypatch.setattr(lineage_program_polling, "_runtime_lineage", lambda clean_id, ports: ({"lineage_id": clean_id}, ""))
    monkeypatch.setattr(lineage_domain, "_lineage_runtime_actor", lambda lineage: ACTOR)

    def fire_count():
        source = lineage_program_runtime.read_runtime(lineage_id)["event_sources"].get("event_source_inbox") or {}
        return int(source.get("fire_count") or 0)

    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)  # baseline
    assert lineage_domain.dispatch_message_arrival(lineage_id, WORK_A, "msg_a")["status"] == "none"  # not accepted yet
    conversation.log.append(_input(2, "msg_a", "queued"))

    pushed = lineage_domain.dispatch_message_arrival(lineage_id, WORK_A, "msg_a")

    assert pushed["status"] == "dispatched"
    event = pushed["events"][0]["event"]
    assert event["payload"]["message_id"] == "msg_a" and event["identity"]["initiated_by"]["id"] == "CASE-A"
    assert fire_count() == 1
    assert lineage_domain.dispatch_message_arrival(lineage_id, WORK_B, "msg_a")["status"] == "none"  # not watched
    assert [item["status"] for item in pushed["events"][0]["results"]] == ["executed"]
    passed = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)["events"]
    # The pass finds the pushed Event already fired: it neither fires nor routes it again.
    assert [item["duplicate"] for item in passed] == [True]
    assert passed[0]["event"]["id"] == event["id"]
    assert [item.get("results", []) for item in passed] == [[]]
    assert fire_count() == 1
    assert lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)["events"] == []


@pytest.mark.parametrize("initial_members", [[], [WORK_B]])
def test_empty_family_preserves_arrivals_until_members_return(primitive_store, conversation, monkeypatch, initial_members):
    """空 Family 保留到达消息并在成员返回后依次发出"""
    from management_portal.society_service import family_facade, resident_held_infos

    members = list(initial_members)
    facade = {"resident_id": WORK_A}
    monkeypatch.setattr(events, "_facade_record", lambda _: facade)
    monkeypatch.setattr(family_facade, "family_members", lambda _: list(members))
    monkeypatch.setattr(family_facade, "relay_links", lambda _: {})
    monkeypatch.setattr(family_facade, "quoted_member", lambda *_: "")
    monkeypatch.setattr(resident_held_infos, "is_info", lambda *_: False)
    conversation.log.clear()
    current = support.create_lineage_with_resident_members({
        "run_as_principal": {"kind": "user", "id": ACTOR},
        "title": "Empty Family", "nodes": [_worker(WORK_A), _worker(WORK_B)],
        "program": {"components": [{"id": "event_source_inbox", "kind": "event_source",
                                    "config": {"event_type": "resident.message_received"}}],
                    "wires": [{"id": "wire_watch", "kind": "watch", "source": "event_source_inbox",
                               "source_port": "watch", "target": WORK_A}]},
    }, ACTOR)
    lineage_id = current["lineage_id"]
    reconcile = lambda: lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)["events"]
    assert reconcile() == []
    members.clear()
    conversation.log.extend([_input(1, "msg_a", "queued"), _input(2, "msg_b", "queued")])
    source = {"event_type": "resident.message_received", "subject_resident_id": WORK_A,
              "subscriber_routes": [{"config": {"parallel": True}}]}
    pushed = []
    assert events.dispatch_arrival({"event_sources": {"event_source_inbox": source}}, ACTOR, WORK_A,
                                   "msg_a", lambda *args: pushed.append(args)) == []
    assert reconcile() == [] and reconcile() == [] and pushed == []
    members.append(WORK_B)
    emitted = [*reconcile(), *reconcile()]
    assert [item["event"]["payload"]["message_id"] for item in emitted] == ["msg_a", "msg_b"]
    assert emitted[0]["event"]["identity"]["initiated_by"]["id"] == "CASE-A"
    assert reconcile() == []
