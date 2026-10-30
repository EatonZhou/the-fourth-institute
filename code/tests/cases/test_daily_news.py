from datetime import datetime, timedelta

from management_portal import serve_management_portal as portal
from management_portal import http_overview


def test_daily_news_uses_latest_valid_report_until_today_exists(
    tmp_path,
    monkeypatch,
) -> None:
    """今日新闻未生成时使用最近有效报告并标记过期"""
    today = datetime.now().astimezone().date()
    previous_date = today - timedelta(days=2)
    previous_path = tmp_path / f"{previous_date.isoformat()}__daily_news.md"
    previous_path.write_text("# Previous report\n", encoding="utf-8")
    monkeypatch.setattr(http_overview, "DAILY_NEWS_ROOT", tmp_path)

    fallback = http_overview._daily_news_payload()

    assert fallback["status"] == "ok"
    assert fallback["date"] == previous_date.isoformat()
    assert fallback["requested_date"] == today.isoformat()
    assert fallback["stale"] is True
    assert fallback["content"] == "# Previous report\n"

    current_path = tmp_path / f"{today.isoformat()}__daily_news.md"
    current_path.write_text("# Current report\n", encoding="utf-8")

    current = http_overview._daily_news_payload()

    assert current["status"] == "ok"
    assert current["date"] == today.isoformat()
    assert current["stale"] is False
    assert current["content"] == "# Current report\n"
