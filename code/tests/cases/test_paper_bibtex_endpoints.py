import json
import urllib.error
import urllib.request

import pytest

from management_portal import http_auth, http_paper, paper_bibtex, application_tools
from management_portal.hatg.catalog import declaration
from management_portal.hatg.contracts import InstructionContext, InstructionError
from tests.cases.test_resource_portal_routes import _server

ONE = "paper_000000000001"
TWO = "paper_000000000002"


def test_authenticated_http_and_application_tool_body_agree(monkeypatch):
    auth = {"username": "sample_admin"}
    monkeypatch.setattr(http_auth, "_get_agent_api_auth", lambda request: auth)
    monkeypatch.setattr(http_paper, "authorize_api", lambda request, a, **kwargs: a["username"])
    seen = []
    def readable(self, ids, *, actor):
        seen.append(actor)
        return {ONE}
    monkeypatch.setattr(paper_bibtex.ResourceAccessPolicy, "readable_ids", readable)
    monkeypatch.setattr(paper_bibtex.PAPER_STORE, "manifest_for_paper_id", lambda identifier, **kwargs:
                        (None, {"paper_id": identifier, "title": "中文 & Demo", "year": "2026", "authors": ["张三"]}))
    server, thread, origin = _server()
    def send(payload):
        return urllib.request.urlopen(urllib.request.Request(origin + "/paper/api/bibtex/export",
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}))
    try:
        with send({"paper_ids": [ONE, TWO]}) as response:
            body = json.load(response)
        assert body["paper_ids"] == [ONE]
        assert {"paper_id": TWO, "code": "unavailable"} in body["warnings"]
        for payload in ({}, {"paper_ids": [], "favorites": True}, {"paper_ids": [ONE], "actor": "bob"}):
            with pytest.raises(urllib.error.HTTPError) as invalid:
                send(payload)
            assert invalid.value.code == 400
        context = InstructionContext(caller="sample_admin", entry="cli")
        result = application_tools.execute({"schema": "tfi.application-tool-call.v1",
            "tool": "application:paper.export_bibtex", "arguments": {"paper_ids": [ONE, TWO]}}, context)
        assert result["status"] == "committed", result
        assert result["result"] == body
        assert json.loads(json.dumps(result, ensure_ascii=False))["result"]["bibtex"].encode("utf-8") == body["bibtex"].encode("utf-8")
        with pytest.raises(InstructionError):
            declaration("ExportPaperBibtex")
        assert set(seen) == {"sample_admin"}
        with pytest.raises(InstructionError):
            application_tools.registry()[0]["application:paper.export_bibtex"].validate_arguments({"paper_ids": [ONE], "actor": "bob"})
        assert not application_tools.registry()[0]["application:paper.export_bibtex"].mutates
        auth.clear()
        with pytest.raises(urllib.error.HTTPError) as missing:
            send({"paper_ids": []})
        assert missing.value.code == 401
        with pytest.raises(InstructionError):
            InstructionContext(caller="", entry="cli")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
