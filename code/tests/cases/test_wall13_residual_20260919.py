import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest


from management_portal import notice_board_backend, portal_pulse_backend
from management_portal import serve_management_portal as portal
from management_portal import http_overview


def test_daily_news_unreadable_today_does_not_fallback(tmp_path, monkeypatch):
    """当天日报文件不可读时返回错误，不回退到前一天的日报"""
    today = datetime.now().astimezone().date()
    current = tmp_path / f"{today.isoformat()}__daily_news.md"
    current.write_text("unreadable fixture")
    previous = tmp_path / f"{(today - timedelta(days=1)).isoformat()}__daily_news.md"
    previous.write_text("must not return this")
    monkeypatch.setattr(http_overview, "DAILY_NEWS_ROOT", tmp_path)
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == current:
            raise PermissionError("offline unreadable fixture")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    result = http_overview._daily_news_payload()
    assert result["status"] == "error"
    assert result["content"] == ""
    assert result["path"] == str(current)
    assert "unreadable fixture" in result["error"]


def test_pulse_publication_owner_identity_and_idempotency(tmp_path, monkeypatch):
    """Pulse 发布以调用者为所有者且重复发布幂等，非法来源或他人条目被拒，只有所有者能撤回"""
    pulse = portal_pulse_backend
    summary = tmp_path / "daily.json"
    summary.write_text(json.dumps({
        "period": "daily", "key": "fixture",
        "content": {"user_private_updates": {
            "alice": {"items": [{"id": "one", "text": "Alice private item"}]},
            "bob": {"items": [{"id": "two", "text": "Bob private item"}]},
        }},
    }))
    monkeypatch.setattr(pulse, "_summary_path", lambda period, key: summary if key == "fixture" else tmp_path / "missing.json")
    monkeypatch.setattr(pulse, "get_home_payload", lambda actor: pulse._read_publications())
    payload = {"source_period": "daily", "source_key": "fixture", "item_id": "one", "owner": "bob"}
    first = pulse.update_publication("alice", payload)
    second = pulse.update_publication("alice", payload)
    assert len(second["items"]) == 1
    assert second["items"][0]["owner"] == "alice"
    assert second["items"][0]["publication_id"] == first["items"][0]["publication_id"]
    for patch in [{"source_period": "invalid"}, {"source_key": ""}, {"source_key": "missing"}, {"item_id": "two"}]:
        with pytest.raises((ValueError, PermissionError)):
            pulse.update_publication("alice", {**payload, **patch})
    # Retracting names the item; only its owner's publication is derived from it.
    item = {"action": "unpublish", "source_period": "daily", "source_key": "fixture", "item_id": "one"}
    with pytest.raises(PermissionError):
        pulse.update_publication("bob", item)
    assert len(pulse._read_publications()["items"]) == 1
    assert pulse.update_publication("alice", item)["items"] == []


def test_notice_classifier_changes_only_category(tmp_path, monkeypatch):
    """自动分类帖子只修改 category，其余字段不变"""
    monkeypatch.setattr(notice_board_backend, "publish_local_projection_change", lambda *args, **kwargs: None)
    board = notice_board_backend.NoticeBoardBackend()
    created = board.create_post("alice", "Fixture", "Body", "chat")["post"]
    assert created["category"] == "chat"
    source = board.classification_source(created["post_id"])
    before = board._load_record()["posts"][0]
    result = board.apply_automatic_category(created["post_id"], expected_fingerprint=source["fingerprint"], category="request")
    after = board._load_record()["posts"][0]
    assert result["changed"] is True
    assert {key: value for key, value in before.items() if key != "category"} == {key: value for key, value in after.items() if key != "category"}


def test_notice_mention_read_cursor_and_failed_delivery_are_independent(tmp_path, monkeypatch):
    """@ 提及事件不因已读而消失，新回复产生新的提及事件，通知投递失败时帖子仍保存"""
    monkeypatch.setattr(notice_board_backend, "publish_local_projection_change", lambda *args, **kwargs: None)
    board = notice_board_backend.NoticeBoardBackend()
    post = board.create_post("alice", "Fixture", "Please inspect @bob")["post"]
    event = board.mention_events("bob")[0]
    board.get_post("bob", post["post_id"])
    board.mark_post_read("bob", post["post_id"], post["notification_revision"])
    assert board.mention_events("bob")[0]["event_id"] == event["event_id"]
    board.add_reply("charlie", post["post_id"], "Follow up @bob")
    assert board.mention_events("bob")[0]["context"] == "reply"
    assert board.mention_events("bob")[0]["event_id"] != event["event_id"]

    def reject(*args, **kwargs):
        raise RuntimeError("offline delivery failure")

    monkeypatch.setattr(notice_board_backend, "publish_local_projection_change", reject)
    with pytest.raises(RuntimeError, match="offline delivery failure"):
        board.create_post("alice", "Persisted despite notification failure", "@bob")
    assert any(item["title"] == "Persisted despite notification failure" for item in board._load_record()["posts"])
def test_notice_backend_has_no_eight_image_post_limit(tmp_path, monkeypatch):
    """公告帖子可引用 9 张以上图片，内容原样保存"""
    monkeypatch.setattr(notice_board_backend, "publish_local_projection_change", lambda *args, **kwargs: None)
    board = notice_board_backend.NoticeBoardBackend()
    content = "\n".join(f"![fixture](/api/notice-board/image?image_id=notice_image_{index:016x}.png)" for index in range(9))
    post = board.create_post("alice", "Nine references", content)["post"]
    assert post["content"] == content
