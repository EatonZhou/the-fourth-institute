from __future__ import annotations

import asyncio
import threading

import pytest

from management_portal.notifications import (
    AsyncNotificationFanout,
    NotificationHub,
    business_projection_subject,
    notification_subject,
    resource_projection_subject,
)
from management_portal.notifications import bus


def test_notification_nats_late_pong_consumes_only_its_original_waiter(subtests, isolated_portal_case):
    """迟到的 NATS PONG 只消耗对应的原等待者，不提前完成后一次 ping"""
    for already_completed in [False, True]:
        with subtests.test(already_completed=already_completed), isolated_portal_case() as case_path:
            from management_portal.notifications.nats_client import NotificationNatsClient

            async def scenario():
                client = NotificationNatsClient()
                abandoned = asyncio.get_running_loop().create_future()
                pending = asyncio.get_running_loop().create_future()
                if already_completed:
                    abandoned.set_result(True)
                else:
                    with pytest.raises(asyncio.TimeoutError):
                        await asyncio.wait_for(abandoned, timeout=0.001)
                client._pongs = [abandoned, pending]
                client._pings_outstanding = 2

                await client._process_pong()
                assert not pending.done()
                assert client._pongs == [pending]
                assert client._pongs_received == 1
                assert client._pings_outstanding == 0

                await client._process_pong()
                assert pending.result() is True
                assert client._pongs == []
                assert client._pongs_received == 2
                await client._process_pong()
                assert client._pongs_received == 2

            asyncio.run(scenario())


def test_notification_nats_connection_preserves_transport_options_contracts(monkeypatch, subtests, isolated_portal_case):
    """通知 NATS 连接原样传递传输参数，关闭通知总线时同时关闭监听端和发布端"""
    with subtests.test(contract='test_notification_nats_connection_preserves_transport_options'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            from unittest.mock import AsyncMock
            from management_portal.notifications.nats_client import NotificationNatsClient, connect_notifications

            connect = AsyncMock()
            monkeypatch.setattr(NotificationNatsClient, "connect", connect)
            options = {"servers": ["nats://127.0.0.1:4223"], "name": "notification-test", "connect_timeout": 1, "allow_reconnect": True, "max_reconnect_attempts": -1, "reconnect_time_wait": 1}
            client = asyncio.run(connect_notifications(**options))
            assert isinstance(client, NotificationNatsClient)
            connect.assert_awaited_once_with(**options)
    with subtests.test(contract='test_notification_bus_closes_listener_and_publisher'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            closed: list[str] = []
            monkeypatch.setattr(
                bus,
                "_LISTENER",
                type("Listener", (), {"close": lambda self: closed.append("listener")})(),
            )
            monkeypatch.setattr(
                bus,
                "_PUBLISHER",
                type("Publisher", (), {"close": lambda self: closed.append("publisher")})(),
            )

            bus.close_notifications()

            assert closed == ["listener", "publisher"]


def test_notification_subjects_have_no_instance_prefix_contracts(subtests, isolated_portal_case):
    """通知主题不带实例前缀，通知中心按主题唤醒等待者，游标越界或订阅溢出时要求快照

    :module: platform.events
    :design: decision_abaedeefca6f
    :design-point:
    """
    with subtests.test(contract='test_notification_subjects_have_no_instance_prefix'), isolated_portal_case() as case_path:
        assert notification_subject(
            "resource",
            "resident_0123456789ab",
            resource_type="resident",
        ) == "portal.resource.changed.resident.resident_0123456789ab"
        assert notification_subject(
            "execution",
            "execution-123",
        ) == "portal.execution.changed.execution-123"
        assert notification_subject(
            "execution",
            "execution-123",
            scope="activity",
        ) == "portal.execution.activity.execution-123"
        assert notification_subject(
            "resident",
            "resident_0123456789ab",
            scope="conversation",
        ) == "portal.resident.changed.resident_0123456789ab.conversation"
        assert notification_subject(
            "lineage",
            "lineage_0123456789ab",
            scope="runtime",
        ) == "portal.lineage.runtime.lineage_0123456789ab"
        assert (
            resource_projection_subject("project-knowledge")
            == "portal.resource.changed.projection.project-knowledge"
        )
        assert (
            business_projection_subject("paper", "paper_0123456789ab")
            == "portal.projection.changed.paper.paper_0123456789ab"
        )
    with subtests.test(contract='test_notification_hub_waits_for_matching_change'), isolated_portal_case() as case_path:
        hub = NotificationHub(max_items=32)
        cursor = hub.sequence
        hub.publish("portal.lineage.changed.lineage_a", {"kind": "changed"})
        hub.publish("portal.resident.changed.resident_b.activity", {"kind": "changed"})

        items, next_cursor = hub.wait(
            cursor,
            predicate=lambda item: item.subject.startswith("portal.resident."),
            timeout=0,
        )

        assert [item.subject for item in items] == [
            "portal.resident.changed.resident_b.activity"
        ]
        assert next_cursor == hub.sequence
    with subtests.test(contract='test_notification_hub_subscriber_can_unsubscribe'), isolated_portal_case() as case_path:
        hub = NotificationHub(max_items=32)
        received = []
        unsubscribe = hub.subscribe(received.append)

        hub.publish("portal.execution.changed.execution-one", {"status": "completed"})
        unsubscribe()
        hub.publish("portal.execution.changed.execution-two", {"status": "failed"})

        assert [item.subject for item in received] == [
            "portal.execution.changed.execution-one"
        ]
    with subtests.test(contract='test_notification_hub_detects_cursor_overrun'), isolated_portal_case() as case_path:
        hub = NotificationHub(max_items=32)
        for index in range(40):
            hub.publish(
                f"portal.execution.changed.execution-{index}",
                {"status": "running"},
            )

        assert hub.cursor_has_gap(0) is True
        assert hub.cursor_has_gap(8) is False
    with subtests.test(contract='test_async_notification_fanout_delivers_ordered_filtered_changes_from_thread'), isolated_portal_case() as case_path:
        async def exercise() -> None:
            hub = NotificationHub(max_items=32)
            fanout = AsyncNotificationFanout(hub)
            fanout.start()
            subscription = fanout.subscribe(
                lambda item: item.subject.startswith("portal.resource.")
            )

            def publish() -> None:
                hub.publish("portal.execution.changed.execution-one", {})
                hub.publish("portal.resource.changed.skill.skill-one", {"n": 1})
                hub.publish("portal.resource.changed.skill.skill-two", {"n": 2})

            thread = threading.Thread(target=publish)
            thread.start()
            thread.join()
            batch = await subscription.wait(1)

            assert batch is not None
            assert [item.payload["n"] for item in batch.notifications] == [1, 2]
            assert batch.snapshot_required is False
            subscription.close()
            fanout.close()

        asyncio.run(exercise())
    with subtests.test(contract='test_async_notification_fanout_uses_snapshot_on_subscriber_overflow'), isolated_portal_case() as case_path:
        async def exercise() -> None:
            hub = NotificationHub(max_items=32)
            fanout = AsyncNotificationFanout(hub, subscriber_queue_size=2)
            fanout.start()
            subscription = fanout.subscribe()

            for index in range(3):
                hub.publish(f"portal.execution.changed.execution-{index}", {})
            batch = await subscription.wait(1)

            assert batch is not None
            assert batch.snapshot_required is True
            assert batch.notifications == ()
            subscription.close()
            fanout.close()

        asyncio.run(exercise())


def test_async_notification_fanout_uses_snapshot_on_ingress_overflow_contracts(subtests, isolated_portal_case):
    """异步通知分发入口积压溢出时要求订阅者重取快照，关闭时唤醒等待中的订阅者"""
    with subtests.test(contract='test_async_notification_fanout_uses_snapshot_on_ingress_overflow'), isolated_portal_case() as case_path:
        async def exercise() -> None:
            hub = NotificationHub(max_items=32)
            fanout = AsyncNotificationFanout(hub, pending_limit=2)
            fanout.start()
            subscription = fanout.subscribe()

            for index in range(3):
                hub.publish(f"portal.execution.changed.execution-{index}", {})
            batch = await subscription.wait(1)

            assert batch is not None
            assert batch.snapshot_required is True
            subscription.close()
            fanout.close()

        asyncio.run(exercise())
    with subtests.test(contract='test_async_notification_fanout_close_wakes_waiting_subscriber'), isolated_portal_case() as case_path:
        async def exercise() -> None:
            hub = NotificationHub(max_items=32)
            fanout = AsyncNotificationFanout(hub)
            fanout.start()
            subscription = fanout.subscribe()
            waiting = asyncio.create_task(subscription.wait(10))
            await asyncio.sleep(0)

            fanout.close()
            batch = await waiting

            assert batch is not None
            assert batch.closed is True
            assert fanout.subscriber_count == 0

        asyncio.run(exercise())
