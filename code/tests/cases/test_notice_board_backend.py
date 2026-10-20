from __future__ import annotations

import base64
from management_portal.notice_store import NoticeState

from datetime import datetime, timezone
from pathlib import Path

import pytest

from management_portal import notice_board_backend
from management_portal.notice_board_backend import NoticeBoardBackend
from tests.cases.registry_support import put_registry_document


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture(autouse=True)
def permission_projection(monkeypatch):
    monkeypatch.setattr(NoticeBoardBackend, "_sync_permissions", staticmethod(lambda *_args, **_kwargs: None))


def test_posts_are_summarized_and_sorted_by_latest_activity_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """公告板按最新活动排序帖子摘要，提及跟随最新帖子或回复，作者编辑不丢讨论状态"""
    with subtests.test(contract='test_posts_are_summarized_and_sorted_by_latest_activity'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            timestamps = iter(
                [
                    "2026-08-02T10:00:00+08:00",
                    "2026-08-02T11:00:00+08:00",
                    "2026-08-02T12:00:00+08:00",
                ]
            )
            monkeypatch.setattr(notice_board_backend, "_now_iso", lambda: next(timestamps))
            backend = NoticeBoardBackend()

            first = backend.create_post("alice", "First post", "First body")
            backend.create_post("bob", "Second post", "Second body")
            backend.add_reply(
                "charlie",
                first["post"]["post_id"],
                "This makes the first post current.",
            )

            listed = backend.list_posts("reader")
            assert [post["title"] for post in listed["posts"]] == [
                "First post",
                "Second post",
            ]
            assert "content" not in listed["posts"][0]
            assert listed["posts"][0]["reply_count"] == 1

            detail = backend.get_post("reader", first["post"]["post_id"])
            assert detail["post"]["content"] == "First body"
            assert detail["post"]["replies"][0]["floor"] == 1
            assert detail["post"]["replies"][0]["author"] == "charlie"

            backend.delete_reply(
                "charlie",
                first["post"]["post_id"],
                detail["post"]["replies"][0]["reply_id"],
            )
            assert [post["title"] for post in backend.list_posts("reader")["posts"]] == [
                "Second post",
                "First post",
            ]
    with subtests.test(contract='test_notice_board_mentions_follow_latest_post_or_reply_event'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            published: list[tuple[str, str, dict]] = []
            monkeypatch.setattr(
                notice_board_backend,
                "publish_local_projection_change",
                lambda projection, target_id, **kwargs: published.append(
                    (projection, target_id, kwargs)
                ),
            )
            backend = NoticeBoardBackend()
            created = backend.create_post("alice", "Status", "Please review this, @bob.")
            post_id = created["post"]["post_id"]

            bob_events = backend.mention_events("BOB")
            assert len(bob_events) == 1
            assert bob_events[0]["post_id"] == post_id
            assert bob_events[0]["context"] == "post"
            assert backend.mention_events("alice") == []
            assert published[-1][0] == "agent-conversation-index"
            assert published[-1][1] == post_id

            initial_event_id = bob_events[0]["event_id"]
            backend.update_post("alice", post_id, "Renamed status", "Please review this, @bob.")
            assert backend.mention_events("bob")[0]["event_id"] == initial_event_id

            backend.add_reply("charlie", post_id, "@bob I added the requested details.")
            bob_events = backend.mention_events("bob")
            assert len(bob_events) == 1
            assert bob_events[0]["context"] == "reply"
            assert bob_events[0]["author"] == "charlie"

            reply_id = backend.get_post("charlie", post_id)["post"]["replies"][0]["reply_id"]
            backend.delete_reply("charlie", post_id, reply_id)
            assert backend.mention_events("bob")[0]["context"] == "post"

            backend.update_post("alice", post_id, "Status", "No mention now.")
            assert backend.mention_events("bob") == []
    with subtests.test(contract='test_authors_can_edit_post_title_and_content_without_losing_thread_state'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            timestamps = iter([
                "2026-08-16T10:00:00+08:00",
                "2026-08-16T10:30:00+08:00",
                "2026-08-16T11:00:00+08:00",
                "2026-08-16T11:30:00+08:00",
            ])
            monkeypatch.setattr(notice_board_backend, "_now_iso", lambda: next(timestamps))
            backend = NoticeBoardBackend()
            created = backend.create_post("alice", "Original title", "Original body")
            post_id = created["post"]["post_id"]
            reply = backend.add_reply("bob", post_id, "Keep this reply")
            reply_id = reply["post"]["replies"][0]["reply_id"]
            backend.set_pinned("CASE-A", post_id, True)

            updated = backend.update_post(
                "bob",
                post_id,
                "  Revised\n title  ",
                "Revised body",
            )["post"]

            assert updated["title"] == "Revised title"
            assert updated["content"] == "Revised body"
            assert updated["edited_at"] == "2026-08-16T11:30:00+08:00"
            assert updated["updated_at"] == updated["edited_at"]
            assert updated["pinned"] is True
            assert updated["can_edit"] is True
            assert [item["reply_id"] for item in updated["replies"]] == [reply_id]
            assert backend.get_post("reader", post_id)["post"]["can_edit"] is True

            after_reply_delete = backend.delete_reply("bob", post_id, reply_id)["post"]
            assert backend._parse_datetime(after_reply_delete["updated_at"]) == backend._parse_datetime(updated["edited_at"])
            reloaded = NoticeBoardBackend().get_post("alice", post_id)["post"]
            assert reloaded["title"] == "Revised title"
            assert reloaded["content"] == "Revised body"
            assert backend._parse_datetime(reloaded["edited_at"]) == backend._parse_datetime(updated["edited_at"])


def test_notice_board_pinning_content_and_read_state_contracts(tmp_path, subtests, isolated_portal_case):
    """公告板置顶不限定管理员名单，帖子内容与分类经校验后持久化，未读状态按用户跟踪"""
    with subtests.test(contract='test_a_admin_actions_do_not_require_fixed_names'), isolated_portal_case() as case_path:
        tmp_path = case_path
        backend = NoticeBoardBackend()
        older = backend.create_post("alice", "Older", "Body")
        backend.create_post("bob", "Newer", "Body")

        assert backend.set_pinned("alice", older["post"]["post_id"], True)["post"]["pinned"]

        pinned = backend.set_pinned("CASE-A", older["post"]["post_id"], True)
        assert pinned["post"]["pinned"] is True
        assert pinned["post"]["pinned_by"] == "CASE-A"
        assert backend.list_posts("reader")["posts"][0]["title"] == "Older"

        unpinned = backend.set_pinned("tfi", older["post"]["post_id"], False)
        assert unpinned["post"]["pinned"] is False
        assert unpinned["post"]["pinned_by"] == ""
    with subtests.test(contract='test_notice_board_validates_content_and_persists'), isolated_portal_case() as case_path:
        tmp_path = case_path
        backend = NoticeBoardBackend()

        with pytest.raises(ValueError, match="post title required"):
            backend.create_post("alice", " ", "Body")
        with pytest.raises(ValueError, match="reply content required"):
            created = backend.create_post("alice", "Post", "Body")
            backend.add_reply("bob", created["post"]["post_id"], " ")

        reloaded = NoticeBoardBackend().list_posts("reader")
        assert reloaded["post_count"] == 1
        assert reloaded["posts"][0]["title"] == "Post"
        assert reloaded["content_signature"]
    with subtests.test(contract='test_notice_board_categories_are_validated_and_legacy_posts_use_other'), isolated_portal_case() as case_path:
        tmp_path = case_path
        backend = NoticeBoardBackend()

        created = backend.create_post("alice", "Broken filter", "Details", "bug")
        assert created["post"]["category"] == "bug"
        assert created["post"]["category_label"] == "Bug"
        with pytest.raises(ValueError, match="invalid notice board category"):
            backend.create_post("alice", "Unknown", "Details", "unsupported")

        updated = backend.update_post(
            "alice",
            created["post"]["post_id"],
            "Requested filter",
            "Updated details",
            "request",
        )
        assert updated["post"]["category"] == "request"
        listed = backend.list_posts("reader")
        assert listed["categories"] == list(notice_board_backend.NOTICE_BOARD_CATEGORIES)
        assert listed["posts"][0]["category_label"] == "需求"

        record = NoticeState("posts").read({})
        record["posts"][0].pop("category")
        NoticeState("posts").replace(record)
        legacy = NoticeBoardBackend().list_posts("reader")["posts"][0]
        assert legacy["category"] == "other"
        assert legacy["category_label"] == "其他"
    with subtests.test(contract='test_notice_board_unread_state_tracks_new_posts_and_replies_per_user'), isolated_portal_case() as case_path:
        tmp_path = case_path
        backend = NoticeBoardBackend()
        created = backend.create_post("alice", "Status", "Initial post")
        post_id = created["post"]["post_id"]

        assert backend.list_posts("alice")["unread_count"] == 0
        bob_list = backend.list_posts("bob")
        assert bob_list["unread_count"] == 1
        assert bob_list["posts"][0]["unread"] is True
        assert bob_list["posts"][0]["unread_kind"] == "post"

        revision_one = bob_list["posts"][0]["notification_revision"]
        assert backend.mark_post_read("bob", post_id, revision_one)["unread"] is False
        backend.update_post("alice", post_id, "Edited status", "Edited body")
        assert backend.list_posts("BOB")["unread_count"] == 0

        first_reply = backend.add_reply("charlie", post_id, "First reply")
        revision_two = first_reply["post"]["notification_revision"]
        assert backend.list_posts("charlie")["unread_count"] == 0
        bob_after_reply = backend.list_posts("bob")
        assert bob_after_reply["unread_count"] == 1
        assert bob_after_reply["posts"][0]["unread_kind"] == "reply"

        backend.add_reply("alice", post_id, "Second reply")
        stale_mark = backend.mark_post_read("bob", post_id, revision_two)
        assert stale_mark["unread"] is True
        current_revision = stale_mark["notification_revision"]
        backend.mark_post_read("bob", post_id, current_revision)
        assert NoticeBoardBackend().list_posts("bob")["unread_count"] == 0
    with subtests.test(contract='test_notice_board_existing_history_becomes_the_unread_baseline'), isolated_portal_case() as case_path:
        tmp_path = case_path
        post_id = "notice_0123456789abcdef"
        NoticeState("posts").replace({'schema': notice_board_backend.NOTICE_BOARD_SCHEMA, 'posts': [{'post_id': post_id, 'title': 'Existing post', 'content': 'Existing content', 'author': 'alice', 'created_at': '2026-08-01T10:00:00+08:00', 'updated_at': '2026-08-01T10:00:00+08:00', 'replies': []}]})
        backend = NoticeBoardBackend()

        assert backend.list_posts("bob")["unread_count"] == 0
        backend.add_reply("alice", post_id, "A new reply")
        bob_list = backend.list_posts("bob")
        assert bob_list["unread_count"] == 1
        assert bob_list["posts"][0]["unread_kind"] == "reply"
    with subtests.test(contract='test_existing_notice_board_images_stay_served_after_post_changes'), isolated_portal_case() as case_path:
        tmp_path = case_path
        backend = NoticeBoardBackend()
        # New post images are uploads (CreateUpload purpose post_image); images
        # written by the earlier board keep their address.
        image_id = "notice_image_0123456789abcdef.png"
        put_registry_document(
            notice_board_backend.NOTICE_BOARD_IMAGE_COLLECTION,
            image_id,
            {"data_base64": base64.b64encode(PNG_1X1).decode("ascii")},
        )
        uploaded = {"image_id": image_id, "url": f"/api/notice-board/image?image_id={image_id}"}
        data, content_type = backend.resolve_image("reader", image_id)
        assert data == PNG_1X1
        assert content_type == "image/png"
        with pytest.raises(PermissionError):
            backend.resolve_image("", image_id)

        created = backend.create_post(
            "alice",
            "Image post",
            f"Screenshot\n\n![clipboard]({uploaded['url']})",
        )
        backend.update_post(
            "alice",
            created["post"]["post_id"],
            "Image removed",
            "The image is no longer part of this post.",
        )
        assert backend.resolve_image("reader", uploaded["image_id"])[0] == PNG_1X1

        backend.delete_post("alice", created["post"]["post_id"])
        assert backend.resolve_image("reader", uploaded["image_id"])[0] == PNG_1X1
        with pytest.raises(FileNotFoundError):
            backend.resolve_image("reader", "notice_image_fedcba9876543210.png")


def test_post_changes_are_left_to_the_permission_system_and_only_reply_authors_delete_replies(tmp_path: Path) -> None:
    """帖子修改与删除交由权限系统判定，回复只允许原作者删除

    公告板后端不另设帖子作者门槛；其他用户及帖子作者均不能删除他人的回复，原回复作者可以删除。
    """
    backend = NoticeBoardBackend()
    created = backend.create_post("alice", "Owned post", "Body")
    post_id = created["post"]["post_id"]
    bob_reply = backend.add_reply("bob", post_id, "Bob reply")
    bob_reply_id = bob_reply["post"]["replies"][0]["reply_id"]
    alice_reply = backend.add_reply("alice", post_id, "Alice reply")
    alice_reply_id = alice_reply["post"]["replies"][1]["reply_id"]

    alice_detail = backend.get_post("alice", post_id)["post"]
    assert alice_detail["can_delete"] is True
    assert [reply["can_delete"] for reply in alice_detail["replies"]] == [
        False,
        True,
    ]
    assert backend.get_post("reader", post_id)["post"]["can_delete"] is True

    remaining = backend.delete_reply("alice", post_id, alice_reply_id)["post"]
    assert [reply["reply_id"] for reply in remaining["replies"]] == [bob_reply_id]
    for caller in ("alice", "admin", "resident_111111111111"):
        with pytest.raises(PermissionError, match="回复作者"):
            backend.delete_reply(caller, post_id, bob_reply_id)
        assert backend.get_post(caller, post_id)["post"]["replies"][0]["reply_id"] == bob_reply_id
    backend.delete_reply("bob", post_id, bob_reply_id)
    deleted = backend.delete_post("bob", post_id)
    assert deleted == {"deleted": True, "post_id": post_id}
    assert backend.list_posts("reader")["post_count"] == 0


def test_stale_posts_fold_while_pinned_and_featured_posts_remain_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """长期无活动的帖子被折叠，置顶和精选帖子保持展开，有新回复后取消折叠"""
    current = {"value": "2026-06-01T08:00:00+00:00"}
    monkeypatch.setattr(notice_board_backend, "_now_iso", lambda: current["value"])
    monkeypatch.setattr(
        notice_board_backend,
        "_now_datetime",
        lambda: datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc),
    )
    backend = NoticeBoardBackend(stale_after_days=30)

    old = backend.create_post("alice", "Old regular", "Body")
    current["value"] = "2026-06-02T08:00:00+00:00"
    featured = backend.create_post("bob", "Old featured", "Body")
    current["value"] = "2026-06-03T08:00:00+00:00"
    pinned = backend.create_post("charlie", "Old pinned", "Body")
    current["value"] = "2026-08-05T08:00:00+00:00"
    backend.create_post("dana", "Recent regular", "Body")
    current["value"] = "2026-08-11T08:00:00+00:00"

    assert backend.set_featured("alice", featured["post"]["post_id"], True)["post"]["featured"]
    backend.set_featured("CASE-A", featured["post"]["post_id"], True)
    backend.set_pinned("tfi", pinned["post"]["post_id"], True)

    listed = backend.list_posts("reader")
    by_title = {post["title"]: post for post in listed["posts"]}
    assert [post["title"] for post in listed["posts"]] == [
        "Old pinned",
        "Old featured",
        "Recent regular",
        "Old regular",
    ]
    assert by_title["Old regular"]["folded"] is True
    assert by_title["Old featured"]["folded"] is False
    assert by_title["Old pinned"]["folded"] is False
    assert by_title["Recent regular"]["folded"] is False
    assert listed["folded_count"] == 1
    assert listed["featured_count"] == 1
    assert listed["stale_after_days"] == 30

    stored_post = next(
        post
        for post in NoticeState("posts").read({})["posts"]
        if post["post_id"] == featured["post"]["post_id"]
    )
    assert "featured" not in stored_post
    reloaded = NoticeBoardBackend(stale_after_days=30)
    assert reloaded.get_post("reader", featured["post"]["post_id"])["post"][
        "featured"
    ] is True

    backend.set_featured("CASE-A", featured["post"]["post_id"], False)
    assert {
        post["title"]: post["folded"]
        for post in backend.list_posts("reader")["posts"]
    }["Old featured"] is True

    backend.add_reply("reader", old["post"]["post_id"], "Fresh reply")
    assert {
        post["title"]: post["folded"]
        for post in backend.list_posts("reader")["posts"]
    }["Old regular"] is False


def test_mark_post_unread_moves_the_read_position_back_one_revision(tmp_path: Path) -> None:
    """标记帖子未读时已读位置回退一个版本，再次标记已读后恢复"""
    backend = NoticeBoardBackend()
    post_id = backend.create_post("alice", "Status", "Body")["post"]["post_id"]
    backend.add_reply("alice", post_id, "Update")
    current = int(backend.get_post("bob", post_id)["post"]["notification_revision"])
    assert backend.mark_post_read("bob", post_id, current)["unread"] is False
    unread = backend.mark_post_unread("bob", post_id)
    assert unread == {"post_id": post_id, "read_revision": current - 1, "notification_revision": current, "unread": True}
    assert backend.mark_post_read("bob", post_id, current)["unread"] is False


def test_a_mention_alerts_mailbox_once_and_reading_the_post_on_home_reads_it(isolated_portal_case) -> None:
    """公告板 @ 在提及时写入被提及者的 Mailbox 行，在 Home 读完帖子即读到这条 @

    发帖、编辑新增和回复中的 @ 各提醒被提及者一次，作者自己和未被提及的人没有提醒；
    在 Home 只读到较早版本时 @ 保持未读，读完整个帖子后 Mailbox 中的 @ 也变为已读。

    :design: decision_15f7df737ed5
    """
    from management_portal import conversation_read_state as reads

    def unread(actor, post_id):
        return reads.row(reads.load(actor), notice_board_backend.mailbox_key(post_id))["unread"]

    with isolated_portal_case():
        backend = NoticeBoardBackend()
        post_id = backend.create_post("alice", "Status", "Body @bob @alice")["post"]["post_id"]
        assert unread("bob", post_id) and not unread("alice", post_id) and not unread("carol", post_id)
        first = int(backend.get_post("bob", post_id)["post"]["notification_revision"])
        backend.add_reply("alice", post_id, "@bob 再看一下")
        # Reading the earlier revision on Home leaves the newer mention unread.
        backend.mark_post_read("bob", post_id, first)
        assert unread("bob", post_id)
        current = int(backend.get_post("bob", post_id)["post"]["notification_revision"])
        backend.mark_post_read("bob", post_id, current)
        assert not unread("bob", post_id)
        backend.update_post("alice", post_id, "Status", "Body @bob @carol")
        assert unread("carol", post_id) and not unread("bob", post_id)


def test_get_post_instruction_reports_edit_and_delete_permissions(tmp_path: Path, monkeypatch) -> None:
    """GetPost 返回帖子的编辑和删除权限，作者与其他读者都可以编辑和删除"""
    from types import SimpleNamespace

    from management_portal import notice_board_web_isa

    backend = NoticeBoardBackend()
    post_id = backend.create_post("alice", "Plan", "body")["post"]["post_id"]
    monkeypatch.setattr(notice_board_web_isa, "_board", lambda: backend)

    def permissions(caller):
        return notice_board_web_isa.get_post(SimpleNamespace(caller=caller, argument={"post_id": post_id}.get))["permissions"]

    assert permissions("alice")["can_edit"] is True and permissions("alice")["can_delete"] is True
    assert permissions("reader")["can_edit"] is True and permissions("reader")["can_delete"] is True
    assert set(permissions("alice")) == {"can_edit", "can_delete"}
