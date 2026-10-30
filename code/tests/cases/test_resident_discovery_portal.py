from __future__ import annotations

import json
from io import BytesIO
from urllib.parse import urlparse

import pytest

from management_portal.resident_discovery_client import ResidentDiscoveryClient, ResidentDiscoveryUnavailable
from management_portal import resident_recommendation_backend as recommendation
from management_portal.society_service.contracts import DOMAIN_METHODS
from management_portal.society_service.transport import SocietyServiceClient, SocietyServiceUnavailable
from management_portal import serve_management_portal as portal
from management_portal import http_auth, http_request_body, http_responses


def _lineage_client(monkeypatch) -> tuple[SocietyServiceClient, list]:
    """The real SocietyService client and RPC allowlist; only socket I/O is replaced."""

    client = SocietyServiceClient()
    calls: list = []

    def call(domain, method, *args, **kwargs):
        assert method in DOMAIN_METHODS[domain]
        calls.append((domain, method, args))
        return {"results": [{"resident_id": "resident_aaaaaaaaaaaa"}]}

    monkeypatch.setattr(client, "call", call)
    return client, calls


def test_resident_discovery_client_preserves_actor_and_candidate_scope_contracts(monkeypatch, subtests):
    """Resident 发现客户端搜索时保留调用者与候选范围，上下文一次批量读取"""
    with subtests.test(contract='test_resident_discovery_client_preserves_actor_and_candidate_scope'):
        client, calls = _lineage_client(monkeypatch)
        ResidentDiscoveryClient(client).search(
            actor='CASE-A', query='自然语言问题', limit=4, resident_ids=['resident_aaaaaaaaaaaa'],
        )
        assert calls == [("resident", "search_residents", ('CASE-A', '自然语言问题', 4, ['resident_aaaaaaaaaaaa']))]
    with subtests.test(contract='test_resident_discovery_client_requests_contexts_in_one_batch'):
        client, calls = _lineage_client(monkeypatch)
        ResidentDiscoveryClient(client).contexts(
            actor='CASE-A', resident_ids=['resident_aaaaaaaaaaaa', 'resident_bbbbbbbbbbbb'],
        )
        assert calls == [("resident", "get_resident_contexts",
                          ('CASE-A', ['resident_aaaaaaaaaaaa', 'resident_bbbbbbbbbbbb']))]


def test_resident_discovery_client_reports_an_unreachable_society_service(monkeypatch):
    """SocietyService 不可达时 Portal 报告 Resident 发现不可用

    SocietyService 连接失败映射为 ResidentDiscoveryUnavailable，页面按服务不可用处理。

    :design: decision_d19b9909a674
    """
    client = SocietyServiceClient()

    def call(*_args, **_kwargs):
        raise SocietyServiceUnavailable("socket missing")

    monkeypatch.setattr(client, "call", call)
    with pytest.raises(ResidentDiscoveryUnavailable):
        ResidentDiscoveryClient(client).search(actor='CASE-A', query='q')


class _ResponseHarness:
    def __init__(self, body: bytes):
        self.path = "/api/agent-workbench/resident-discovery/search"
        self.headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        self.rfile = BytesIO(body)
        self.wfile = BytesIO()
        self.status = 0

    def send_response(self, status):
        self.status = status

    def send_header(self, _name, _value):
        return

    def end_headers(self):
        return


def _dispatch_discovery_post(handler: _ResponseHarness):
    parsed = urlparse(handler.path)
    return portal._PORTAL_HTTP_ROUTES.dispatch(
        "POST", parsed.path, handler, parsed, "", phase="protected"
    )


def test_portal_discovery_route_uses_session_actor_and_scope(monkeypatch):
    """Resident 搜索接口按会话用户搜索，并保留请求给出的候选范围"""
    captured = {}

    class FakeClient:
        def search(self, **kwargs):
            captured.update(kwargs)
            return {"results": []}

    body = json.dumps({
        "query": "自然语言问题",
        "limit": 6,
        "resident_ids": ["resident_aaaaaaaaaaaa"],
    }).encode("utf-8")
    handler = _ResponseHarness(body)
    monkeypatch.setattr(http_auth, "_get_session_username", lambda _handler: "CASE-A")
    monkeypatch.setattr(portal, "ResidentDiscoveryClient", FakeClient)
    _dispatch_discovery_post(handler)
    assert handler.status == 200
    assert captured == {
        "actor": "CASE-A",
        "query": "自然语言问题",
        "limit": 6,
        "resident_ids": ["resident_aaaaaaaaaaaa"],
    }


def test_portal_discovery_contexts_and_recommendation_routes_preserve_actor(monkeypatch):
    """Resident 发现的上下文与最近推荐接口按会话用户调用"""
    calls = []

    class FakeClient:
        def contexts(self, **kwargs):
            calls.append(("contexts", kwargs))
            return {"contexts": []}

    class FakeRecommendationBackend:
        def latest_recommendation(self, **kwargs):
            calls.append(("latest", kwargs))
            return {"status": "completed"}

    monkeypatch.setattr(http_auth, "_get_session_username", lambda _handler: "CASE-A")
    monkeypatch.setattr(portal, "ResidentDiscoveryClient", FakeClient)
    monkeypatch.setattr(portal, "resident_recommendation_backend", FakeRecommendationBackend())

    contexts = _ResponseHarness(json.dumps({"resident_ids": ["resident_aaaaaaaaaaaa"]}).encode())
    contexts.path = "/api/agent-workbench/resident-discovery/contexts"
    _dispatch_discovery_post(contexts)
    assert contexts.status == 200
    assert json.loads(contexts.wfile.getvalue()) == {"contexts": []}

    latest = _ResponseHarness(b"{}")
    latest.path = "/api/agent-workbench/resident-discovery/recommend/latest"
    _dispatch_discovery_post(latest)
    assert latest.status == 200
    assert json.loads(latest.wfile.getvalue()) == {"status": "completed"}
    assert calls == [
        ("contexts", {"actor": "CASE-A", "resident_ids": ["resident_aaaaaaaaaaaa"]}),
        ("latest", {"actor": "CASE-A"}),
    ]


def test_portal_discovery_rejects_oversized_json_before_read(monkeypatch):
    """Resident 发现接口在读取前拒绝超长请求体，返回 413"""
    class UnreadableStream:
        def read(self, *_args, **_kwargs):
            raise AssertionError("oversized body must not be read")

    handler = _ResponseHarness(b"")
    handler.path = "/api/agent-workbench/resident-discovery/search"
    handler.headers["Content-Length"] = str(http_request_body._JSON_REQUEST_MAX_BYTES + 1)
    handler.rfile = UnreadableStream()
    monkeypatch.setattr(http_auth, "_get_session_username", lambda _handler: "CASE-A")

    _dispatch_discovery_post(handler)

    assert handler.status == 413


def test_portal_discovery_unavailable_uses_service_error_contract(monkeypatch):
    """Resident 发现服务不可用时接口返回 503 与 operation.unavailable"""
    class UnavailableClient:
        def search(self, **_kwargs):
            raise http_responses.ResidentDiscoveryUnavailable("Resident Discovery service is unavailable")

    monkeypatch.setattr(portal, "ResidentDiscoveryClient", UnavailableClient)
    monkeypatch.setattr(http_auth, "_get_session_username", lambda _handler: "CASE-A")
    handler = _ResponseHarness(b"{}")

    _dispatch_discovery_post(handler)

    assert handler.status == 503
    response = json.loads(handler.wfile.getvalue())
    assert response["code"] == "operation.unavailable"


def test_portal_routing_eval_submit_uses_session_actor(monkeypatch):
    """提交路由纠正时按会话用户记录提交者"""
    captured = {}

    class FakeClient:
        def submit_routing_eval_correction(self, **kwargs):
            captured.update(kwargs)
            return {
                "submission": {"submission_id": "routing_eval_123", "dataset_case_id": "case_123"},
                "dataset_added": True,
            }

    body = json.dumps({
        "query": "preview is slow",
        "predicted": [{"resident_id": "resident_bbbbbbbbbbbb"}],
        "ground_truth_resident_id": "resident_aaaaaaaaaaaa",
        "ground_truth_action": "open",
        "rationale": "direct owner",
    }).encode("utf-8")
    handler = _ResponseHarness(body)
    handler.path = "/api/agent-workbench/resident-discovery/routing-evals/submit"
    monkeypatch.setattr(http_auth, "_get_session_username", lambda _handler: "sample_peer")
    monkeypatch.setattr(portal, "ResidentDiscoveryClient", FakeClient)

    _dispatch_discovery_post(handler)

    assert handler.status == 200
    assert captured["actor"] == "sample_peer"
    assert captured["ground_truth_resident_id"] == "resident_aaaaaaaaaaaa"
    assert captured["ground_truth_action"] == "open"


def test_portal_routing_eval_directory_search_uses_session_actor(monkeypatch):
    """路由评测的 Resident 目录搜索按会话用户执行"""
    captured = {}

    class FakeClient:
        def search_directory(self, **kwargs):
            captured.update(kwargs)
            return {"results": [{"resident_id": "resident_aaaaaaaaaaaa"}]}

    body = json.dumps({
        "query": "Lineage Preview",
        "limit": 30,
    }).encode("utf-8")
    handler = _ResponseHarness(body)
    handler.path = "/api/agent-workbench/resident-discovery/routing-evals/residents/search"
    monkeypatch.setattr(http_auth, "_get_session_username", lambda _handler: "sample_peer")
    monkeypatch.setattr(portal, "ResidentDiscoveryClient", FakeClient)

    _dispatch_discovery_post(handler)

    assert handler.status == 200
    assert captured == {
        "actor": "sample_peer",
        "query": "Lineage Preview",
        "limit": 30,
    }


@pytest.mark.parametrize("recommendations,expected_status", [
    ([], "completed"),
    ([{"resident_id": "resident_invented0000"}], "failed"),
])
def test_recommendation_result_route_preserves_no_match_contract(
    monkeypatch, recommendations, expected_status,
):
    """推荐结果为空时按完成返回无匹配，推荐了候选外的 Resident 时判为无效结果

    Real route, result normalizer and RPC allowlist; replace only socket I/O."""
    resident_id = "resident_aaaaaaaaaaaa"
    message_id = "message_routing000001"
    client = SocietyServiceClient()
    calls = []

    def reply(domain, method, requested_resident, actor, requested_message):
        calls.append((domain, method, actor))
        assert (domain, requested_resident, actor) == ("resident", resident_id, "test-actor")
        if method == "get_resident_input_state":
            return {"state": "completed", "reply_message_ids": ["message_reply000001"]}
        assert method == "get_resident_message"
        if requested_message == message_id:
            return {"content_parts": [{"type": "context_reference", "context": {
                "resident_routing": {"query": "修复telepus", "limit": 5, "candidates": [
                    {"resident_id": resident_id, "title": "Test candidate"},
                ]},
            }}]}
        return {"content_parts": [{"type": "text", "text": json.dumps({
            "summary": "候选中没有明确负责 Telepus 的 Resident。",
            "recommendations": recommendations,
            "fork_advice": {"recommended": False},
        })}]}

    monkeypatch.setattr(client, "call", reply)
    monkeypatch.setattr(recommendation, "get_society_service", lambda: client)
    monkeypatch.setattr(http_auth, "_get_session_username", lambda _handler: "test-actor")
    handler = _ResponseHarness(json.dumps({
        "resident_id": resident_id, "message_id": message_id,
    }).encode("utf-8"))
    handler.path = "/api/agent-workbench/resident-discovery/recommend/result"

    _dispatch_discovery_post(handler)

    assert handler.status == 200
    assert len(calls) == 3
    response = json.loads(handler.wfile.getvalue())
    assert response["status"] == expected_status
    if expected_status == "completed":
        assert response["result"]["recommendations"] == []
        assert response["result"]["fallback"] is False
    else:
        assert response["error_code"] == "recommendation.invalid_result"
        assert response["retryable"] is False
        assert "no valid candidate" in response["error"]


def test_routing_candidate_client_uses_routing_contract_and_preserves_scope(monkeypatch):
    """路由候选客户端调用 resident_routing_candidates 并保留调用者与候选范围"""
    client, calls = _lineage_client(monkeypatch)
    ResidentDiscoveryClient(client).routing_candidates(
        actor="test-actor", query="未读状态", limit=40, resident_ids=["resident_aaaaaaaaaaaa"],
    )
    assert calls == [("resident", "resident_routing_candidates",
                      ("test-actor", "未读状态", 40, ["resident_aaaaaaaaaaaa"]))]
