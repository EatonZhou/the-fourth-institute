"""Script artifacts and scheduled Effect projections (decision_8b510cbd9c58).

:module: spec.design-governance
:design: decision_8b510cbd9c58, decision_481dc4e6292c
"""
from __future__ import annotations

import base64
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import pytest

from management_portal import scheduled_triggered_monitor as monitor
from management_portal import http_script_runs as artifacts
from management_portal.http_errors import exception_http_error
from management_portal.hatg.contracts import InstructionError
from tests.cases.test_scheduled_triggered_monitor import Reads, LINEAGE, NOW, RESIDENT


def test_scheduled_script_effects_use_dsl_and_their_own_history():
    """定时触发的脚本 Effect 单独成行，从 DSL 解析脚本路径并读取自己的运行历史

    :design-point:"""
    reads = Reads()
    reads.lineage["program"]["components"] += [
        {"id": "effect_script", "kind": "effect", "title": "维护", "config": {"script":
            'if GetLineage lineage_id=example; then\n RunScript path="/jobs/a b.py"\nfi\n'
            'run=$(RunScript path=${event.payload.path})'}},
        {"id": "effect_other", "kind": "effect", "config": {"script": 'output text="RunScript path=fake"'}},
    ]
    reads.lineage["program"]["wires"] += [
        {"id": f"wire_{source}", "kind": "route", "source": source, "target": "effect_script", "target_port": "invoke"}
        for source in ("event_source_clock", "event_source_unused", "event_source_manual")
    ]
    reads.lineage["program"]["wires"].append({"id": "other", "kind": "route", "source": "event_source_clock", "target": "effect_other"})

    def read(name, args):
        if name == "ListScriptRuns":
            assert args == {"lineage_id": LINEAGE, "effect_id": "effect_script", "limit": 50}
            return {"runs": [{"script_run_id": "script_run_test", "status": "timed_out", "path": "/jobs/a b.py", "started_at": "2026-09-29T10:00:00Z"}]}
        return reads(name, args)

    rows = monitor._lineage_rows(reads.lineage, monitor._Reader("viewer", read), NOW)
    assert len(rows) == 2
    effect = next(row for row in rows if row["kind"] == "effect")
    assert effect["effect_id"] == "effect_script" and "resident_id" not in effect
    assert effect["script_paths"] == ["/jobs/a b.py", "动态路径（运行时确定）"]
    assert effect["last_run"]["status"] == "timed_out" and effect["health"] == "error"
    assert len(effect["sources"]) == 2 and len(effect["manual_sources"]) == 1
    assert all(args.get("resident_id", RESIDENT) == RESIDENT for _, args in reads.calls)
    effect["sources"] = [{"event_type": "file.changed", "config": {}, "runtime": {"fire_count": 1}}]
    effect["consecutive_failures"] = 0
    assert monitor._cleanup_hints(effect, NOW) == []


def test_script_paths_ignore_comments_and_support_json_arguments():
    """解析脚本路径时忽略注释和字符串中的 RunScript，支持 JSON 形式参数

    :design-point:"""
    assert monitor._script_paths({"config": {"script": '# RunScript path=no\noutput text="RunScript path=no"'}}) == []
    assert monitor._script_paths({"config": {"script": 'RunScript path:="/jobs/ok.py"'}}) == ["/jobs/ok.py"]


@pytest.fixture
def artifact_reader(monkeypatch):
    data = b"<html>" + b"x" * 70000 + b"</html>"
    calls = []

    def call(actor, name, args):
        assert actor == "viewer"
        calls.append((name, args))
        if name == "GetScriptRun":
            return {"artifacts": [{"name": "report.html", "size": len(data), "mime_type": "text/html"}]}
        assert name == "ReadScriptRunOutput" and args["name"] == "report.html"
        start = args["offset"]
        end = min(start + args["limit"], len(data))
        return {"base64": base64.b64encode(data[start:end]).decode(), "offset": start,
                "next_offset": end, "size": len(data), "eof": end == len(data)}

    monkeypatch.setattr(artifacts.isa_client, "call", call)
    return data, calls


def test_artifact_route_reads_bounded_isa_pages_and_isolates_html(artifact_reader):
    """脚本产物接口按固定大小分页经 ISA 读取，HTML 产物以沙箱和禁止联网头隔离

    :design-point:"""
    data, calls = artifact_reader
    handler = artifacts.ScriptRunHttp(respond=lambda *args: args, exception=lambda _, exc: (_ for _ in ()).throw(exc),
                                     payload=lambda *args, **kwargs: (args, kwargs))
    response, policy = handler.artifact(None, urlsplit("/api/script-runs/artifact?lineage_id=lineage_aaaaaaaaaaaa&script_run_id=run&name=report.html"), "viewer")
    assert response[1:] == (200, data, "text/html; charset=utf-8")
    assert [args["offset"] for name, args in calls if name == "ReadScriptRunOutput"] == [0, 65536]
    assert policy["cache_control"] == "no-store"
    headers = policy["extra_headers"]
    assert "sandbox allow-scripts;" in headers["Content-Security-Policy"]
    assert "allow-same-origin" not in headers["Content-Security-Policy"]
    assert "connect-src 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"


def test_artifact_route_denial_does_not_read_content(monkeypatch):
    """无权查看脚本运行时产物接口返回 403，且不读取内容

    :design-point:"""
    calls = []
    def denied(actor, name, args):
        calls.append(name)
        raise InstructionError("instruction.forbidden", "不可读取")
    monkeypatch.setattr(artifacts.isa_client, "call", denied)
    handler = artifacts.ScriptRunHttp(respond=lambda *args: args, exception=lambda _, exc: exception_http_error(exc), payload=None)
    status, _ = handler.artifact(None, urlsplit("/api/script-runs/artifact?lineage_id=lineage_aaaaaaaaaaaa&script_run_id=run&name=report.html"), "viewer")
    assert status == 403 and calls == ["GetScriptRun"]


def test_artifact_route_rejects_unlisted_path(artifact_reader):
    """产物接口对未列出的文件名或越界路径返回 404

    :design-point:"""
    _, calls = artifact_reader
    handler = artifacts.ScriptRunHttp(respond=lambda *args: args, exception=None, payload=None)
    response = handler.artifact(None, urlsplit("/api/script-runs/artifact?lineage_id=lineage_aaaaaaaaaaaa&script_run_id=run&name=../../secret"), "viewer")
    assert response[1] == 404 and len(calls) == 1


def test_script_run_dom_output_refresh_and_preview_policy():
    """脚本运行 DOM 保留跨页 UTF-8、纯文本输出、终态停止刷新及产物预览策略

    运行生产 renderer 的 Node DOM 契约；不验证浏览器对 sandbox/CSP 的执行。
    产物响应头、授权与路径边界由本文件的 HTTP owner 用例覆盖。

    :design-point:
    """
    source = Path(__file__).with_name("javascript") / "script_run_pages.cjs"
    result = subprocess.run(["node", str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
