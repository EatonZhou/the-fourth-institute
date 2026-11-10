import json
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

from management_portal import http_auth, http_paper, paper_bibtex
from management_portal.hatg import routing
from management_portal.hatg.catalog import declaration
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from tests.cases.test_resource_portal_routes import _server

ONE = "paper_000000000001"
TWO = "paper_000000000002"


def test_authenticated_http_hatg_and_script_body_agree(monkeypatch, capsys):
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
        result = routing.execute(InstructionCall(instruction="ExportPaperBibtex", arguments={"paper_ids": [ONE, TWO]}), context)
        assert result.status == "committed"
        assert result.result == body
        # The existing client prints JSON. Decode its result without adding a
        # newline, preserving exactly the same UTF-8 bytes as the HTTP response.
        from scripts import resident_hatg_instruction as client
        envelope = {"status": result.status, "result": result.result}
        calls = []
        def authenticated_execute(resident, call, **kwargs):
            calls.append((resident, call))
            return envelope
        monkeypatch.setattr(client, "execute", authenticated_execute)
        monkeypatch.setattr(client, "_resident_id", lambda value: value)
        capsys.readouterr()
        assert client.main(["--resident-id", "resident_000000000001", "call", "ExportPaperBibtex",
                            "--arguments-json", json.dumps({"paper_ids": [ONE, TWO]})]) == 0
        client_output = capsys.readouterr().out
        assert json.loads(client_output) == envelope
        assert calls[0][1]["arguments"] == {"paper_ids": [ONE, TWO]}
        extracted = subprocess.check_output([sys.executable, "-c",
            "import json,sys; r=json.load(sys.stdin); assert r['status']=='committed'; sys.stdout.buffer.write(r['result']['bibtex'].encode('utf-8'))"],
            input=client_output.encode())
        assert extracted == body["bibtex"].encode("utf-8")
        assert set(seen) == {"sample_admin"}
        with pytest.raises(InstructionError):
            declaration("ExportPaperBibtex").validate_arguments({"paper_ids": [ONE], "actor": "bob"})
        assert not declaration("ExportPaperBibtex").mutates
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
