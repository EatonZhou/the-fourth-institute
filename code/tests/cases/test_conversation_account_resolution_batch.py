"""Account directory reads through the real local service and isolated PG."""
from __future__ import annotations

from dataclasses import replace
import json
import time

import pytest

from management_portal import model_api_store
from management_portal.execution import accounts, account_registrations, provider_catalog
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import resident_catalog
from management_portal.society_service.server import LocalSocietyService
from management_portal.society_service import lineage_aggregate
from tests.cases.society_service_test_support import (
    _install_isolated_portal_access, publish_registered_lineage_members,
)
from tests.cases.test_agent_conversation_index_backend import _lineage_definition
from tests.cases.test_agent_data_store import _chat


ACTOR = "account-batch-isolated-viewer"


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    _install_isolated_portal_access(monkeypatch)
    # Change the shared mapping itself: account adapters and many Lineage
    # modules bound it by name on import, earlier cases included.
    providers = provider_catalog.AGENT_PROVIDERS
    original = dict(providers)
    providers.clear()
    providers.update(codex=original["codex"], claude=replace(original["claude"], default_route="batch_native"))
    try:
        from tests.cases.account_registration_test_support import register_codex
        register_codex(monkeypatch, tmp_path, names=("one", "two"))
        for entry in (
            {"id": "batch_native", "kind": "claude_code_subscription", "provider": "anthropic"},
            {"id": "batch_deepseek", "kind": "openai_compatible_chat", "provider": "deepseek"},
            {"id": "batch_api", "kind": "openai_compatible_chat", "provider": "openai"},
            {"id": "batch_disabled", "kind": "openai_compatible_chat", "provider": "openai", "enabled": False},
        ):
            if entry["kind"] == "claude_code_subscription":
                account_registrations.create_registration({"account_id": entry["id"], "provider": "claude", "kind": "subscription_login", "route_id": entry["id"]}, ACTOR)
            else:
                assert model_api_store.create_api(entry, ACTOR)
                account_registrations.create_registration({"account_id": entry["id"], "provider": "claude" if entry["provider"] == "deepseek" else "provider_api", "kind": "api_profile", "route_id": entry["id"] if entry["provider"] == "deepseek" else "", "config": {"api_id": entry["id"]}, "enabled": entry.get("enabled", True)}, ACTOR)
        yield
    finally:
        providers.clear()
        providers.update(original)


def populate(count, *, hidden=(), routes=None):
    ids = [f"resident_{index + 1:012x}" for index in range(count)]
    for private in (False, True):
        members = [rid for index, rid in enumerate(ids) if (index in hidden) == private]
        if not members:
            continue
        definition = _lineage_definition(f"lineage_{int(private) + 1:012x}", [])
        definition.update(created_by="account-batch-other" if private else ACTOR,
                          lineage_membership="personal" if private else "formal")
        lineage_aggregate.lineage_catalog_store().upsert(definition)
        publish_registered_lineage_members({**definition, "nodes": [{"id": rid} for rid in members]},
                                           definition["created_by"])
    store = resident_catalog.get_catalog().state_store
    data = AgentDataStore()
    for index, rid in enumerate(ids):
        if routes:
            record = store.resident_record(rid)
            backend, route = routes[index % len(routes)]
            record.update(backend=backend, backend_route_id=route)
            store.put_resident_record(rid, record, commit_configuration=True)
        data.display_chat_message(rid, _chat("account-input-" + rid, "user", "isolated fixture", author_id=ACTOR))
    return ids


def count_reads(monkeypatch):
    counts = {"builds": 0, "model_api_reads": 0}
    build, read = accounts.default_execution_accounts, model_api_store.apis
    def counted_build(**kwargs):
        counts["builds"] += 1
        return build(**kwargs)
    def counted_read():
        counts["model_api_reads"] += 1
        return read()
    monkeypatch.setattr(accounts, "default_execution_accounts", counted_build)
    monkeypatch.setattr(model_api_store, "apis", counted_read)
    return counts


def activity(**options):
    return LocalSocietyService().resident.list_resident_conversation_activity(ACTOR, **options)


@pytest.mark.parametrize("count", [4, 50])
def test_real_service_builds_once_for_mixed_routes(catalog, monkeypatch, count):
    """混合路由会话请求仅构建一次账号目录"""
    routes = [("codex", ""), ("codex", "two"), ("claude", ""),
              ("claude", "batch_deepseek"), ("provider_api", "batch_api"),
              ("provider_api", "batch_missing"), ("provider_api", "batch_disabled")]
    expected = ["codex_one", "codex_two", "batch_native", "batch_deepseek", "batch_api", "other", "batch_disabled"]
    ids = populate(count, routes=routes)
    counts = count_reads(monkeypatch)
    start = time.perf_counter()
    result = activity(page_size=50)
    elapsed = (time.perf_counter() - start) * 1000
    assert [(r["resident_id"], r["account_id"]) for r in result["residents"]] == [
        (rid, expected[index % len(expected)]) for index, rid in enumerate(ids)]
    print(json.dumps({"scope": "isolated-PG LocalSocietyService", "count": count,
                      "elapsed_ms": elapsed, **counts}))
    assert counts == {"builds": 1, "model_api_reads": 1}


def test_one_request_shares_accounts_across_authorized_candidate_pages(catalog, monkeypatch):
    """同次请求跨候选分页共享账号且过滤不可见会话"""
    ids = populate(6, hidden={1, 3, 5})
    counts = count_reads(monkeypatch)
    first = activity(page_size=2)
    assert [r["resident_id"] for r in first["residents"]] == [ids[0], ids[2]]
    assert first["next_page_token"]
    assert counts == {"builds": 1, "model_api_reads": 1}
    second = activity(page_size=2, page_token=first["next_page_token"])
    assert [r["resident_id"] for r in second["residents"]] == [ids[4]]
    assert not second["next_page_token"]
    assert counts == {"builds": 2, "model_api_reads": 2}
    assert activity(resident_ids=[ids[1], ids[3], ids[5]])["residents"] == []
    assert counts["builds"] == 2
    # Engagement is actor scoped as well as subject to Lineage visibility.
    owner = LocalSocietyService().resident.list_resident_conversation_activity(
        "account-batch-other", resident_ids=ids)
    assert owner["residents"] == []  # no engagement by this actor


def test_next_request_refreshes_default_and_disabled_configuration(catalog, monkeypatch):
    """后续请求刷新默认账号与禁用状态"""
    ids = populate(2, routes=[("codex", ""), ("provider_api", "batch_api")])
    counts = count_reads(monkeypatch)
    first = activity(resident_ids=ids)["residents"]
    assert [r["account_id"] for r in first] == ["codex_one", "batch_api"]
    account_registrations.update_registration("codex_one", {"config": {"is_default": False}}, 1, ACTOR)
    account_registrations.update_registration("codex_two", {"config": {"is_default": True}}, 1, ACTOR)
    model_api_store.update_api("batch_api", {"enabled": False}, ACTOR)
    second = activity(resident_ids=ids)["residents"]
    assert [r["account_id"] for r in second] == ["codex_two", "batch_api"]
    assert counts == {"builds": 2, "model_api_reads": 2}


def test_bad_route_preserves_error_and_does_not_leave_cached_configuration(catalog, monkeypatch):
    """无效账号路由报错且修复后下次请求重新读取配置"""
    ids = populate(1, routes=[("claude", "batch_missing")])
    counts = count_reads(monkeypatch)
    with pytest.raises(ValueError, match="unknown Execution account route"):
        activity(resident_ids=ids)
    account_registrations.create_registration({"account_id": "batch_missing", "kind": "subscription_login",
                                               "provider": "claude", "route_id": "batch_missing"}, ACTOR)
    assert activity(resident_ids=ids)["residents"][0]["account_id"] == "batch_missing"
    assert counts == {"builds": 2, "model_api_reads": 2}


def test_empty_and_invalid_requests_do_not_build_accounts(catalog, monkeypatch):
    """空会话与无效分页请求不构建账号目录"""
    counts = count_reads(monkeypatch)
    assert activity(page_size=2)["residents"] == []
    assert activity(resident_ids=[])["residents"] == []
    for options in ({"page_size": 0}, {"page_size": 101},
                    {"page_size": 2, "resident_ids": []},
                    {"page_size": 2, "page_token": "invalid"}):
        with pytest.raises(ValueError):
            activity(**options)
    assert counts == {"builds": 0, "model_api_reads": 0}
