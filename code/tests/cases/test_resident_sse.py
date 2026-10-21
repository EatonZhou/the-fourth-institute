""":module: resident.conversation"""
from __future__ import annotations
from management_portal import permission_system

import unittest
from unittest import mock

from management_portal import serve_management_portal as portal
from management_portal.notifications import Notification


def _notification(
    sequence: int,
    subject: str,
    payload: dict | None = None,
) -> Notification:
    return Notification(
        sequence=sequence,
        subject=subject,
        payload=payload or {"kind": "changed"},
        received_at=0,
    )


def _readable(*readable_ids):
    """A ``permission_system.readable_ids`` answering only ``readable_ids``."""
    def read(instruction, parameter, identifiers, *, actor, **kwargs):
        return set(identifiers) & set(readable_ids)
    return read


def _unavailable(instruction, parameter, identifiers, *, actor, **kwargs):
    raise RuntimeError("permission system down")


class PortalSseTest(unittest.TestCase):
    def test_inbox_change_discards_only_the_notified_viewers_cached_index(self) -> None:
        """收件箱变更通知只清除被通知用户的会话索引缓存"""
        cache = portal._CoalescingReadCache(60)
        current = {"unread_count": 0}
        with mock.patch.object(portal, "_AGENT_CONVERSATION_READ_CACHE", cache), mock.patch.object(
            portal, "_agent_conversation_payload", side_effect=lambda *args, **kwargs: dict(current),
        ):
            self.assertEqual(portal._cached_agent_conversation_payload("CASE-A")["unread_count"], 0)
            self.assertEqual(portal._cached_agent_conversation_payload("other")["unread_count"], 0)
            current["unread_count"] = 1
            notification = _notification(
                1, "portal.projection.changed.agent-conversation-index.current",
                {"actors": ["case-a"], "fields": ["chatroom"]},
            )
            portal._portal_change_payload("other", [notification], {"projection"})
            self.assertEqual(portal._cached_agent_conversation_payload("other")["unread_count"], 0)
            payload = portal._portal_change_payload("CASE-A", [notification], {"projection"})
            self.assertIn("agent-conversation-index", payload["projection_changes"])
            self.assertEqual(portal._cached_agent_conversation_payload("CASE-A")["unread_count"], 1)
            self.assertEqual(portal._cached_agent_conversation_payload("other")["unread_count"], 0)

    def test_reconnected_gateway_discards_cached_inbox_before_snapshot(self) -> None:
        """网关重连后先清除该用户的收件箱缓存，并要求重新取快照"""
        with mock.patch.object(portal, "_forget_cached_inbox") as forget:
            payload = portal._portal_change_payload(
                "CASE-A", [_notification(1, "portal.gateway.reconnected")], {"projection"},
            )
        self.assertTrue(payload["snapshot_required"])
        forget.assert_called_once_with("CASE-A")

    def test_change_payload_preserves_only_visible_resident_change_scopes(self) -> None:
        """SSE 变更只保留可读 Resident 的变更范围并去重

        :design: decision_f560803947c6
        :design-point:
        """
        with mock.patch.object(permission_system, "readable_ids", side_effect=_readable("resident_111111111111")):
            payload = portal._portal_change_payload("CASE-A", [
                _notification(1, "portal.resident.changed.resident_111111111111.identity"),
                _notification(2, "portal.resident.changed.resident_111111111111.runtime"),
                _notification(3, "portal.resident.changed.resident_111111111111.identity"),
                _notification(4, "portal.resident.changed.resident_222222222222.identity"),
            ], {"resident"})
        self.assertEqual(payload["resident_changes"], {
            "resident_111111111111": ["identity", "runtime"],
        })

    def test_change_payload_only_invalidates_visible_resident(self) -> None:
        """可读 Resident 的对话事件随 SSE 变更原样下发"""
        with mock.patch.object(
            permission_system,
            "readable_ids",
            side_effect=_readable("resident_111111111111"),
        ):
            payload = portal._portal_change_payload(
                "CASE-A",
                [
                    _notification(
                        1,
                        "portal.resident.changed.resident_111111111111.conversation",
                        {
                            "kind": "conversation_event",
                            "event": {
                                "resident_id": "resident_111111111111",
                                "cursor": 7,
                                "event_type": "conversation.message.appended",
                                "payload": {"message": {"message_id": "message_one"}},
                            },
                        },
                    )
                ],
                {"resident"},
            )

        self.assertEqual(payload["domains"], ["resident"])
        self.assertEqual(payload["changed"], {"resident": ["resident_111111111111"]})
        self.assertEqual(
            payload["resident_events"],
            [{
                "resident_id": "resident_111111111111",
                "cursor": 7,
                "event_type": "conversation.message.appended",
                "payload": {"message": {"message_id": "message_one"}},
            }],
        )

    def test_change_payload_marks_oversized_resident_event_for_snapshot(self) -> None:
        """省略了 payload 的超大 Resident 事件下发时保留 payload_omitted 标记"""
        with mock.patch.object(
            permission_system,
            "readable_ids",
            side_effect=_readable("resident_111111111111"),
        ):
            payload = portal._portal_change_payload(
                "CASE-A",
                [
                    _notification(
                        1,
                        "portal.resident.changed.resident_111111111111.conversation",
                        {
                            "kind": "conversation_event",
                            "event": {
                                "resident_id": "resident_111111111111",
                                "cursor": 8,
                                "event_type": "conversation.message.appended",
                                "payload_omitted": True,
                            },
                        },
                    )
                ],
                {"resident"},
            )

        self.assertEqual(payload["changed"], {"resident": ["resident_111111111111"]})
        self.assertEqual(
            payload["resident_events"],
            [{
                "resident_id": "resident_111111111111",
                "cursor": 8,
                "event_type": "conversation.message.appended",
                "payload_omitted": True,
            }],
        )

    def test_change_payload_requests_snapshot_when_resident_permission_lookup_fails(self) -> None:
        """Resident 权限查询失败时 SSE 不下发事件并要求重新取快照"""
        with mock.patch.object(
            permission_system,
            "readable_ids",
            side_effect=_unavailable,
        ):
            payload = portal._portal_change_payload(
                "CASE-A",
                [
                    _notification(
                        1,
                        "portal.resident.changed.resident_111111111111.conversation",
                        {
                            "kind": "conversation_event",
                            "event": {
                                "resident_id": "resident_111111111111",
                                "cursor": 9,
                                "event_type": "conversation.message.appended",
                                "payload": {
                                    "message": {"message_id": "message_private"}
                                },
                            },
                        },
                    )
                ],
                {"resident"},
            )

        self.assertTrue(payload["snapshot_required"])
        self.assertEqual(payload["resident_events"], [])
        self.assertEqual(payload["changed"], {})

    def test_change_payload_filters_execution_changes_by_actor(self) -> None:
        """执行变更只下发给该执行的发起者"""
        payload = portal._portal_change_payload(
            "CASE-A",
            [
                _notification(
                    1,
                    "portal.execution.changed.execution-one",
                    {"kind": "changed", "actor": "CASE-A"},
                ),
                _notification(
                    2,
                    "portal.execution.changed.execution-two",
                    {"kind": "changed", "actor": "bob"},
                ),
            ],
            {"execution"},
        )

        self.assertEqual(payload["domains"], ["execution"])
        self.assertEqual(payload["changed"], {"execution": ["execution-one"]})

    def test_change_payload_filters_business_projection_changes_by_actor(self) -> None:
        """业务投影变更按 actor 过滤，并带上变更字段和详情"""
        payload = portal._portal_change_payload(
            "CASE-A",
            [
                _notification(
                    1,
                    "portal.projection.changed.paper.paper-one",
                    {
                        "kind": "changed",
                        "actor": "CASE-A",
                        "fields": ["figures", "run"],
                        "details": {"surface": "paper", "workspace_key": "paper:one"},
                    },
                ),
                _notification(
                    2,
                    "portal.projection.changed.paper.paper-two",
                    {"kind": "changed", "actor": "bob"},
                ),
                _notification(
                    3,
                    "portal.projection.changed.paper.paper-shared",
                    {"kind": "changed", "actors": ["CASE-A", "bob"]},
                ),
            ],
            {"projection"},
        )

        self.assertEqual(payload["domains"], ["projection"])
        self.assertEqual(
            payload["changed"],
            {"projection": ["paper-one", "paper-shared"]},
        )
        self.assertEqual(
            payload["projections"],
            {"paper": ["paper-one", "paper-shared"]},
        )
        self.assertEqual(
            payload["projection_changes"],
            {
                "paper": {
                    "paper-one": ["figures", "run"],
                    "paper-shared": [],
                }
            },
        )
        self.assertEqual(
            payload["projection_details"],
            {
                "paper": {
                    "paper-one": {
                        "surface": "paper",
                        "workspace_key": "paper:one",
                    }
                }
            },
        )

    def test_change_payload_filters_lineage_ids_with_read_permissions(self) -> None:
        """Lineage 变更按读权限过滤，无权读取的 Lineage 不下发"""
        notifications = [
            _notification(
                1,
                "portal.resident.changed.resident_111111111111.conversation",
            ),
            _notification(
                2,
                "portal.lineage.changed.lineage_222222222222",
            ),
        ]

        with mock.patch.object(
            permission_system,
            "readable_ids",
            side_effect=_readable("resident_111111111111"),
        ):
            payload = portal._portal_change_payload(
                "CASE-A",
                notifications,
                {"resident", "lineage"},
            )

        self.assertEqual(payload["domains"], ["resident"])
        self.assertEqual(
            payload["changed"],
            {"resident": ["resident_111111111111"]},
        )

    def test_resource_descriptor_change_invalidates_visible_resident(self) -> None:
        """Resident 资源描述变更使可读的该 Resident 失效刷新"""
        with mock.patch.object(
            permission_system,
            "readable_ids",
            side_effect=_readable("resident_111111111111"),
        ):
            payload = portal._portal_change_payload(
                "CASE-A",
                [
                    _notification(
                        1,
                        "portal.resource.changed.resident.resident_111111111111",
                    )
                ],
                {"resource", "resident"},
            )

        self.assertEqual(payload["domains"], ["resident", "resource"])
        self.assertEqual(
            payload["changed"],
            {"resident": ["resident_111111111111"]},
        )

    def test_resource_projection_preserves_visible_exact_targets(self) -> None:
        """资源投影变更只下发可读的目标资源，要求快照的分区单独列出"""
        with mock.patch.object(
            permission_system,
            "readable_ids",
            side_effect=_readable("todo_111111111111"),
        ):
            payload = portal._portal_change_payload(
                "CASE-A",
                [
                    _notification(
                        1,
                        "portal.resource.changed.projection.project_knowledge",
                        {
                            "kind": "projection_updated",
                            "resource_ids": [
                                "todo_111111111111",
                                "todo_222222222222",
                            ],
                        },
                    ),
                    _notification(
                        2,
                        "portal.resource.changed.projection.paper",
                        {
                            "kind": "projection_updated",
                            "snapshot_required": True,
                        },
                    ),
                ],
                {"resource"},
            )

        self.assertEqual(payload["domains"], ["resource"])
        self.assertEqual(
            payload["resource_projection_changes"],
            {"project_knowledge": ["todo_111111111111"]},
        )
        self.assertEqual(payload["resource_projection_snapshots"], ["paper"])

    def test_resource_projection_permission_failure_requires_partition_snapshot(self) -> None:
        """资源投影的权限查询失败时，该分区改为要求重新取快照"""
        with mock.patch.object(
            permission_system,
            "readable_ids",
            side_effect=_unavailable,
        ):
            payload = portal._portal_change_payload(
                "CASE-A",
                [
                    _notification(
                        1,
                        "portal.resource.changed.projection.project_knowledge",
                        {
                            "kind": "projection_updated",
                            "resource_ids": ["todo_111111111111"],
                        },
                    ),
                ],
                {"resource"},
            )

        self.assertEqual(payload["domains"], ["resource"])
        self.assertEqual(payload["resource_projection_changes"], {})
        self.assertEqual(
            payload["resource_projection_snapshots"],
            ["project_knowledge"],
        )

if __name__ == "__main__":
    unittest.main()
