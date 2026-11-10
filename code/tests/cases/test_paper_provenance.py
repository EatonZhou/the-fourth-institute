import json
import pytest
from management_portal import paper_provenance as p


@pytest.mark.parametrize("provider,account,model", [("kimi", "kimi-subscription", "kimi-old"), ("codex", "other-agent-account", "gpt-old")])
def test_original_execution_not_current_resident(monkeypatch, provider, account, model):
    calls = []
    def call(actor, instruction, args):
        calls.append(instruction)
        assert instruction == "GetMessage"
        return {"message": {"message_id": "reply", "author_id": "resident_test", "created_at": "2026-01-01T00:00:00Z",
            "generation_source": {"provider": provider, "account_id": account, "model": model, "model_evidence": "execution_request", "execution_id": "old-execution"},
            "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}}}
    monkeypatch.setattr(p.isa_client, "call", call)
    usage = {"generated_at": "2026-01-02T00:00:00Z", "model": "new-model", "model_evidence": "resident_current_settings",
        "generation_source": {"resident_id": "resident_test", "reply_message_id": "reply"}}
    resolved, status = p.resolve_usage(usage, "content", "caller")
    assert resolved["model"] == model and resolved["account_id"] == account
    assert resolved["total_tokens"] == 30 and status == "resolved"
    assert p.resolve_usage(resolved, "content", "caller")[1] == "complete"
    assert calls == ["GetMessage"]


def test_api_record_is_preserved_without_agent_or_model_call(monkeypatch):
    monkeypatch.setattr(p.isa_client, "call", lambda *a: pytest.fail("must not call a model or Resident"))
    usage = {"api_id": "kimi-api", "provider": "moonshot", "model": "kimi-k2.6", "account_id": "api-account", "total_tokens": 42}
    assert p.resolve_usage(usage, "existing summary", "caller") == (usage, "complete")


def test_missing_reply_model_uses_original_scheduler_request(monkeypatch):
    from management_portal.execution import service
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(p.isa_client, "call", lambda *a: {"message": {"message_id": "reply", "author_id": "resident_test",
        "generation_source": {"execution_id": "old-execution", "provider": "kimi", "account_id": "old-account"}}})
    def source(execution_id, *, actor):
        calls.append((execution_id, actor))
        return {"execution_id": execution_id, "resident_id": "resident_test", "model": "original-request-model", "model_evidence": "execution_request"}
    monkeypatch.setattr(service, "get_execution_service", lambda: SimpleNamespace(generation_source=source))
    result, status = p.resolve_usage({"generation_source": {"resident_id": "resident_test", "reply_message_id": "reply"}}, "existing body", "caller")
    assert result["model"] == "original-request-model" and result["account_id"] == "old-account"
    assert result["model_evidence"] == "execution_request" and calls == [("old-execution", "caller")]


def test_agent_authored_header_and_explicit_unknown_override(tmp_path, monkeypatch):
    path = tmp_path / "summary.md"
    path.write_text('---\ngenerated_by: resident_other\nreply_message_id: original\nmodel: obsolete\ngenerated_at: 123\n---\nExisting content')
    path.with_suffix(".usage.json").write_text(json.dumps({"model": "未记录"}))
    monkeypatch.setattr(p, "_paper_store_file", lambda *a: path)
    artifact = p.read_artifact({}, "summary")
    usage = artifact[4]
    assert usage["model"] == "未记录"
    assert usage["generation_source"]["resident_id"] == "resident_other"
    assert usage["generation_source"]["reply_message_id"] == "original"


def test_source_newer_than_artifact_is_rejected(monkeypatch):
    monkeypatch.setattr(p.isa_client, "call", lambda *a: {"message": {"message_id": "r", "author_id": "resident_test", "created_at": "2026-02-01T00:00:00Z"}})
    with pytest.raises(ValueError, match="newer"):
        p.resolve_usage({"generated_at": "2026-01-01T00:00:00Z", "generation_source": {"resident_id": "resident_test", "reply_message_id": "r"}}, "body", "caller")


def test_direct_agent_summary_requires_unique_text_and_time(monkeypatch):
    body = "An existing summary written by another Agent."
    message = {"message_id": "r", "author_id": "resident_test", "created_at": "2026-01-01T00:00:00Z",
        "content_parts": [{"type": "text", "text": body}], "generation_source": {"model": "old", "account_id": "account-at-time", "provider": "kimi"}}
    def call(actor, instruction, args):
        return {"results": [{"message": message}]} if instruction == "SearchMessages" else {"message": message}
    monkeypatch.setattr(p.isa_client, "call", call)
    result, _ = p.resolve_usage({"generated_at": "2026-01-02T00:00:00Z", "generation_source": {"resident_id": "resident_test"}}, body, "caller")
    assert result["model"] == "old" and result["account_id"] == "account-at-time"


@pytest.mark.parametrize("kind", ["analysis", "summary"])
def test_refresh_preserves_content_and_skips_repeat(tmp_path, monkeypatch, kind):
    path = tmp_path / (kind + ".md")
    path.write_text("Existing artifact; never regenerate me.")
    raw = path.read_bytes()
    usage = {"api_id": "kimi-api", "provider": "kimi", "model": "original", "account_id": "old-account", "total_tokens": 10}
    sidecar = path.with_suffix(".usage.json")
    sidecar.write_text(json.dumps(usage))
    before = sidecar.read_bytes()
    monkeypatch.setattr(p, "_all_papers", lambda: [{"id": "paper_test"}])
    monkeypatch.setattr(p, "_paper_by_id", lambda pid: {"id": pid})
    monkeypatch.setattr(p, "_paper_store_file", lambda *a: path)
    monkeypatch.setattr(p.permission_system, "permitted_ids", lambda *a: ["paper_test"])
    result = p.refresh({"paper_id": "paper_test", "kind": kind, "apply": True,
        "expected_sha256": p.digest(raw), "expected_usage_sha256": p.digest(before)}, "caller")
    assert result["status"] == "complete" and not result["applied"]
    assert path.read_bytes() == raw and sidecar.read_bytes() == before


def test_script_cases_and_second_run_do_not_rewrite(tmp_path, monkeypatch, capsys):
    import io
    import sys
    from scripts import backfill_paper_provenance as script, resident_hatg_instruction as hatg
    paths = {}
    for name, resident in [("api", ""), ("subscription", "resident_sub"), ("agent", "resident_other")]:
        path = tmp_path / (name + ".md")
        path.write_text("Existing summary " + name)
        paths[name] = path
        usage = {"generated_at": "2026-01-02T00:00:00Z", "generation_source": {"resident_id": resident, "reply_message_id": "reply"}}
        if name == "api":
            usage.update(api_id="kimi-api", model="kimi-api-model", provider="moonshot", account_id="api-account", total_tokens=50)
        path.with_suffix(".usage.json").write_text(json.dumps(usage))
    monkeypatch.setattr(p, "_all_papers", lambda: [{"id": name} for name in paths])
    monkeypatch.setattr(p, "_paper_by_id", lambda pid: {"id": pid})
    monkeypatch.setattr(p, "_paper_store_file", lambda paper, kind: paths[paper["id"]] if kind == "summary" else None)
    monkeypatch.setattr(p.permission_system, "permitted_ids", lambda a, i, k, ids: ids)
    monkeypatch.setattr(p.isa_client, "call", lambda a, i, args: {"message": {"message_id": "reply", "author_id": args["resident_id"], "created_at": "2026-01-01T00:00:00Z",
        "generation_source": {"provider": "kimi", "model": "then-model", "account_id": "then-account", "model_evidence": "execution_request"}, "usage": {"total_tokens": 40}}})
    monkeypatch.setattr(script, "_token", lambda _: "test")
    monkeypatch.setattr(script, "_resident_id", lambda x: x)
    monkeypatch.setattr(hatg, "execute", lambda *a: {"result": {"resident": {"lineage_id": "test"}}})
    def http(request, timeout):
        data = p.refresh(json.loads(request.data), "caller") if request.data else p.inventory("caller")
        return io.BytesIO(json.dumps(data).encode())
    monkeypatch.setattr(script, "urlopen", http)
    monkeypatch.setattr(sys, "argv", ["script", "--resident-id", "caller", "--all", "--apply"])
    originals = {k: v.read_bytes() for k, v in paths.items()}
    script.main()
    first = capsys.readouterr().out
    assert '"resolved": 2' in first and '"complete": 1' in first
    usage_bytes = {k: v.with_suffix(".usage.json").read_bytes() for k, v in paths.items()}
    script.main()
    assert '"complete": 3' in capsys.readouterr().out
    assert all(v.read_bytes() == originals[k] and v.with_suffix(".usage.json").read_bytes() == usage_bytes[k] for k, v in paths.items())
