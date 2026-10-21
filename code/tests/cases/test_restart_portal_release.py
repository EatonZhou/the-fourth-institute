from __future__ import annotations

import json
import sys

import pytest

from scripts import portal_restart_notice_client
from scripts import restart_portal_release


class _DrainResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_realtime_drain_requires_canonical_confirmation(monkeypatch) -> None:
    """实时连接排空接口没有明确确认已排空时，重启客户端报错"""
    payload = {"ok": True, "closed_realtime_connections": 2}
    monkeypatch.setattr(
        portal_restart_notice_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _DrainResponse(payload),
    )

    with pytest.raises(RuntimeError, match="were not drained"):
        portal_restart_notice_client.drain_realtime("http://portal.test/drain")


def test_release_restart_uses_restart_notice_and_drain_endpoints(monkeypatch) -> None:
    """Release 重启先发布 5 秒重启通知，倒计时后排空实时连接再重启服务"""
    calls: list[tuple] = []
    monkeypatch.setattr(
        restart_portal_release,
        "publish_restart_notice",
        lambda url, **payload: calls.append(("publish", url, payload)) or {"active": True},
    )
    monkeypatch.setattr(restart_portal_release.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))
    monkeypatch.setattr(restart_portal_release, "_drain_realtime", lambda: calls.append(("drain",)))
    monkeypatch.setattr(restart_portal_release.ServiceControl, "restart",
                        lambda _self, service: calls.append(("restart", service)))
    monkeypatch.setattr(sys, "argv", ["restart_portal_release.py"])

    assert restart_portal_release.main() == 0
    assert calls == [
        (
            "publish",
            restart_portal_release.RESTART_NOTICE_URL,
            {
                "seconds": 5,
                "message": "Portal Release将在倒计时结束后重启。",
                "services": [restart_portal_release.SERVICE],
            },
        ),
        ("sleep", 5),
        ("drain",),
        ("restart", restart_portal_release.SERVICE),
    ]


def test_release_restart_advances_release_branch_while_web_is_stopped(monkeypatch) -> None:
    """指定提交的 Release 重启在 Web 停止期间推进发布分支，然后再启动"""
    from scripts import portal_release

    calls: list[tuple] = []
    monkeypatch.setattr(restart_portal_release, "publish_restart_notice", lambda url, **payload: {"active": True})
    monkeypatch.setattr(restart_portal_release.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(restart_portal_release, "_drain_realtime", lambda: calls.append(("drain",)))
    monkeypatch.setattr(restart_portal_release.ServiceControl, "stop",
                        lambda _self, service: calls.append(("service", "stop", service)))
    monkeypatch.setattr(restart_portal_release.ServiceControl, "start",
                        lambda _self, service: calls.append(("service", "start", service)))
    monkeypatch.setattr(portal_release, "advance", lambda root, commit: calls.append(("advance", root, commit)))
    monkeypatch.setattr(sys, "argv", ["restart_portal_release.py", "--commit", "abc123"])

    assert restart_portal_release.main() == 0
    assert calls == [
        ("drain",),
        ("service", "stop", restart_portal_release.SERVICE),
        ("advance", portal_release.RELEASE_ROOT, "abc123"),
        ("service", "start", restart_portal_release.SERVICE),
    ]


def test_release_web_starts_again_when_release_branch_cannot_advance(monkeypatch) -> None:
    """发布分支无法推进到指定提交时，Release Web 仍会重新启动"""
    from scripts import portal_release

    calls: list[str] = []
    monkeypatch.setattr(restart_portal_release, "publish_restart_notice", lambda url, **payload: {"active": True})
    monkeypatch.setattr(restart_portal_release.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(restart_portal_release, "_drain_realtime", lambda: None)
    monkeypatch.setattr(restart_portal_release.ServiceControl, "stop",
                        lambda _self, _service: calls.append("stop"))
    monkeypatch.setattr(restart_portal_release.ServiceControl, "start",
                        lambda _self, _service: calls.append("start"))

    def fail(root, commit):
        raise ValueError("unknown commit")

    monkeypatch.setattr(portal_release, "advance", fail)
    monkeypatch.setattr(sys, "argv", ["restart_portal_release.py", "--commit", "missing"])

    with pytest.raises(ValueError, match="unknown commit"):
        restart_portal_release.main()
    assert calls == ["stop", "start"]


def test_release_realtime_drain_requires_shared_confirmation(monkeypatch, capsys) -> None:
    """Release 重启经共享客户端排空实时连接，并输出关闭数量和耗时"""
    calls: list[tuple] = []
    monkeypatch.setattr(
        restart_portal_release,
        "drain_realtime",
        lambda url, **options: calls.append(("drain", url, options)) or {
            "closed_realtime_connections": 3,
            "elapsed_seconds": 0.125,
        },
    )

    restart_portal_release._drain_realtime()

    assert calls == [("drain", restart_portal_release.REALTIME_DRAIN_URL, {})]
    assert "3 connection(s) closed in 0.125s" in capsys.readouterr().out
