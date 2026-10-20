from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from management_portal import resident_recommendation_backend as backend


class Discovery:
    def routing_candidates(self, **_kwargs):
        return {
            "results": [{
                "resident_id": "resident_aaaaaaaaaaaa",
                "title": "Portal Manager",
                "lineage_id": "lineage_aaaaaaaaaaaa",
                "lineage_title": "Portal",
                "goal": "Maintain Portal",
                "summary": {
                    "current_focus": "Resident protocol",
                    "primary_ownership": ["Portal backend"],
                },
            }]
        }


@pytest.fixture
def capability_binding(monkeypatch):
    binding = {"lineage_id": "lineage_111111111111", "resident_id": "resident_222222222222"}
    monkeypatch.setattr(backend, "_recommendation_resident_binding", lambda actor: dict(binding))
    monkeypatch.setattr(backend, "_available_recommendation_resident_binding", lambda actor: dict(binding))
    return binding


@pytest.mark.parametrize("legacy_model", [None, "gpt-5.4"])
def test_recommendation_runs_through_embedded_resident(monkeypatch, legacy_model, capability_binding) -> None:
    """Resident 推荐交给已绑定的推荐 Resident 按其自身配置执行，不传创建参数"""
    if legacy_model is None:
        monkeypatch.delenv("RESIDENT_CONTEXT_LLM_MODEL", raising=False)
        monkeypatch.delenv("RESIDENT_CONTEXT_LLM_REASONING_EFFORT", raising=False)
    else:
        monkeypatch.setenv("RESIDENT_CONTEXT_LLM_MODEL", legacy_model)
        monkeypatch.setenv("RESIDENT_CONTEXT_LLM_REASONING_EFFORT", "low")
    captured = {}

    def run_resident_turn(**kwargs):
        captured.update(kwargs)
        return {
            "resident_id": "resident_routing000001",
            "message_id": "message_routing000001",
            "message": json.dumps({
                "summary": "route to Portal",
                "recommendations": [{
                    "resident_id": "resident_aaaaaaaaaaaa",
                    "reason": "owns the backend",
                    "confidence": "high",
                    "action": "open",
                }],
                "fork_advice": {
                    "recommended": False,
                    "source_resident_id": "",
                    "reason": "",
                    "suggested_title": "",
                },
            }),
        }

    monkeypatch.setattr(backend, "run_resident_turn", run_resident_turn)
    result = backend.recommend_residents(
        actor="sample_admin",
        query="修复 Portal 后端",
        client=Discovery(),
    )

    assert captured["resident_scope"] == "resident-routing:sample_admin"
    assert captured["resident_binding"] == capability_binding
    assert captured["workflow_binding"] == capability_binding
    # The bound Resident runs on its own configuration; no creation values are sent.
    assert not {"provider", "lineage_scope", "title", "lineage_title"} & set(captured)
    assert "runtime_options" not in captured
    assert result["recommendations"][0]["resident_id"] == "resident_aaaaaaaaaaaa"
    assert result["recommendations"][0]["recommended_action"] == "open"
    assert "job_id" not in result
    assert "execution_id" not in result


@pytest.mark.parametrize("candidate_count,limit,preferred_minimum", [
    (8, 5, 3), (8, 8, 3), (8, 1, 1), (8, 2, 2), (2, 5, 2), (0, 5, 0),
])
def test_prompt_requests_multiple_relevant_candidates_with_independent_confidence(
    candidate_count, limit, preferred_minimum,
):
    """推荐 prompt 按候选数和上限设定推荐数量，要求不凑数并独立评估置信度"""
    candidates = [{"resident_id": f"resident_{index:012d}"} for index in range(candidate_count)]
    instructions, payload_text = backend._prompt("未读状态", candidates, limit).split("\n\n", 1)
    payload = json.loads(payload_text)
    assert payload["maximum_recommendations"] == limit
    assert payload["preferred_minimum_recommendations"] == preferred_minimum
    assert len(payload["candidates"]) == candidate_count
    assert "Honor an explicit recommendation count" in instructions
    assert "never pad with unrelated Residents" in instructions
    assert "return fewer" in instructions
    assert "Assess confidence independently" in instructions
    assert "not calibrated probabilities" in instructions


@pytest.mark.parametrize("query,expected", [
    ("只推荐一位最相关的 Resident", 1),
    ("推荐2个候选 Resident", 2),
    ("修复最近 5 轮对话", 5),
])
def test_candidate_search_honors_requested_recommendation_count(query, expected):
    """查询中写明的推荐人数决定候选检索的推荐上限"""
    class Discovery:
        def routing_candidates(self, **kwargs):
            return {"results": [{"resident_id": "resident_aaaaaaaaaaaa"}]}

    limit, _ = backend._candidate_search(
        actor="sample_admin", query=query, limit=5, resident_ids=None, client=Discovery(),
    )
    assert limit == expected


@pytest.mark.parametrize("limit", [1, 3, 5])
def test_multiple_recommendations_keep_confidence_reason_and_candidate_boundaries(limit):
    """多条推荐按上限截取并保留置信度和理由，候选之外与重复的 Resident 被剔除"""
    candidates = [{"resident_id": f"resident_{index:012d}"} for index in range(5)]
    entries = [{
        "resident_id": item["resident_id"], "confidence": confidence,
        "reason": f"职责匹配理由 {index}", "action": "open",
    } for index, (item, confidence) in enumerate(zip(candidates, ["high", "high", "medium", "medium", "low"]))]
    raw = {"recommendations": [
        {"resident_id": "resident_outside_candidates", "confidence": "high"},
        entries[0], *entries,
    ]}
    result = backend._normalize("未读状态", candidates, raw, limit)
    assert [{key: item[key] for key in ("resident_id", "confidence", "reason")}
            for item in result["recommendations"]] == [
        {key: item[key] for key in ("resident_id", "confidence", "reason")}
        for item in entries[:limit]
    ]
    # A genuine single match is kept, not filled up with retrieval-only results.
    assert len(backend._normalize("未读状态", candidates, {"recommendations": entries[:1]}, 5)["recommendations"]) == 1


def test_recommendation_rejects_invented_resident_contracts(monkeypatch, subtests, isolated_portal_case, capability_binding):
    """推荐结果只含候选之外的 Resident 时报错，明确无匹配时返回空推荐"""
    with subtests.test(contract='test_recommendation_rejects_invented_resident'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(backend, 'run_resident_turn', lambda **_kwargs: {'message': json.dumps({'summary': 'bad', 'recommendations': [{'resident_id': 'resident_invented0000', 'reason': 'invented', 'confidence': 'high', 'action': 'open'}], 'fork_advice': {}})})
            try:
                backend.recommend_residents(actor='sample_admin', query='test', client=Discovery())
            except ValueError as error:
                assert 'no valid candidate' in str(error)
            else:
                raise AssertionError('invented Resident was accepted')
    with subtests.test(contract='test_recommendation_accepts_explicit_no_match'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            summary = '当前候选未包含明确负责 Telepus 的 Resident，暂无法可靠路由。'
            monkeypatch.setattr(backend, 'run_resident_turn', lambda **_kwargs: {'message': json.dumps({'summary': summary, 'recommendations': [], 'fork_advice': {'recommended': False, 'source_resident_id': None}})})
            result = backend.recommend_residents(actor='test-actor', query='修复telepus', client=Discovery())
            assert result['summary'] == summary
            assert result['recommendations'] == []
            assert result['fallback'] is False
            assert result['fork_advice']['recommended'] is False


def test_recommendation_does_not_treat_invalid_shape_as_no_match(subtests, isolated_portal_case):
    """recommendations 缺失或类型错误时报错，不当作无匹配"""
    for raw in [{}, {"recommendations": None},
                                    {"recommendations": {}}, {"recommendations": ""}]:
        with subtests.test(raw=raw), isolated_portal_case() as case_path:
            with pytest.raises(ValueError):
                backend._normalize("test", Discovery().routing_candidates()["results"], raw, 5)


@pytest.mark.parametrize("legacy_model", [None, "gpt-5.4"])
def test_start_recommendation_returns_resident_message_identity(monkeypatch, legacy_model, capability_binding) -> None:
    """发起推荐返回推荐 Resident 与输入消息 ID，并按用户保存最近一次请求"""
    if legacy_model is None:
        monkeypatch.delenv("RESIDENT_CONTEXT_LLM_MODEL", raising=False)
        monkeypatch.delenv("RESIDENT_CONTEXT_LLM_REASONING_EFFORT", raising=False)
    else:
        monkeypatch.setenv("RESIDENT_CONTEXT_LLM_MODEL", legacy_model)
        monkeypatch.setenv("RESIDENT_CONTEXT_LLM_REASONING_EFFORT", "low")
    captured = {}
    saved = {}

    class UIStateStore:
        def put(self, actor, namespace, key, value):
            saved.update(actor=actor, namespace=namespace, key=key, value=value)
            return {"key": key, "value": value}

        def get(self, actor, namespace, key):
            return None

    def submit_invocation(**kwargs):
        captured.update(kwargs)
        return {
            "resident_id": "resident_routing000001",
            "lineage_id": "lineage_routing000001",
            "message_id": "message_routing000001",
        }

    monkeypatch.setattr(backend, "submit_invocation", submit_invocation)
    monkeypatch.setattr(backend, "get_portal_ui_state_store", lambda: UIStateStore())
    result = backend.start_recommendation(
        actor="sample_admin",
        query="修复 Portal 后端",
        client=Discovery(),
    )

    assert result == {
        "status": "accepted",
        "request_id": result["request_id"],
        "resident_id": "resident_routing000001",
        "lineage_id": "lineage_routing000001",
        "message_id": "message_routing000001",
    }
    assert result["request_id"].startswith("resident-recommendation-")
    assert captured["context_refs"]["resident_routing"]["query"] == "修复 Portal 后端"
    assert captured["context_refs"]["resident_routing"]["request_id"] == result["request_id"]
    assert captured["resident_binding"] == capability_binding
    assert "runtime_settings" not in captured
    assert "job_id" not in result
    assert "execution_id" not in result
    assert saved["actor"] == "sample_admin"
    assert saved["namespace"] == backend.RECOMMENDATION_UI_NAMESPACE
    assert saved["value"]["message_id"] == "message_routing000001"


def test_start_recommendation_handles_expired_image_reference(monkeypatch, tmp_path, capability_binding) -> None:
    """查询引用的图片已失效时，发起推荐以 [image unavailable] 替换图片路径"""
    captured = {}
    missing = tmp_path / "expired.png"
    query = f"Find a Resident for this screenshot [image: {missing}] and Portal UI"
    monkeypatch.setattr(backend, "submit_invocation", lambda **kwargs: captured.update(kwargs) or {
        "resident_id": capability_binding["resident_id"],
        "lineage_id": capability_binding["lineage_id"],
        "message_id": "message_routing000001",
    })
    monkeypatch.setattr(backend, "_store_latest_recommendation", lambda **_kwargs: None)

    result = backend.start_recommendation(actor="sample_admin", query=query, client=Discovery())

    assert result["status"] == "accepted"
    assert str(missing) not in captured["message"]
    assert "[image unavailable]" in captured["message"]
    assert captured["context_refs"]["resident_routing"]["query"] == (
        "Find a Resident for this screenshot [image unavailable] and Portal UI"
    )


def test_latest_recommendation_restores_actor_scoped_handle(monkeypatch) -> None:
    """最近一次推荐按用户从界面状态中恢复请求句柄"""
    class UIStateStore:
        def get(self, actor, namespace, key):
            assert actor == "sample_admin"
            assert namespace == backend.RECOMMENDATION_UI_NAMESPACE
            assert key == backend.RECOMMENDATION_UI_KEY
            return {
                "key": key,
                "value": {
                    "query": "修复刷新恢复",
                    "request_id": "resident-recommendation-restored",
                    "resident_id": "resident_routing000001",
                    "lineage_id": "lineage_routing000001",
                    "message_id": "message_routing000001",
                },
            }

    monkeypatch.setattr(backend, "get_portal_ui_state_store", lambda: UIStateStore())
    assert backend.latest_recommendation(actor="sample_admin") == {
        "status": "accepted",
        "query": "修复刷新恢复",
        "request_id": "resident-recommendation-restored",
        "resident_id": "resident_routing000001",
        "lineage_id": "lineage_routing000001",
        "message_id": "message_routing000001",
    }


def test_latest_recommendation_does_not_scan_legacy_routing_input(monkeypatch) -> None:
    """没有保存的最近推荐时返回 none，不扫描旧对话"""
    class UIStateStore:
        def get(self, actor, namespace, key):
            assert (actor, namespace, key) == (
                "sample_admin", backend.RECOMMENDATION_UI_NAMESPACE, backend.RECOMMENDATION_UI_KEY,
            )
            return None

    monkeypatch.setattr(backend, "get_portal_ui_state_store", lambda: UIStateStore())
    monkeypatch.setattr(backend, "get_society_service", lambda: pytest.fail("must not scan old conversations"))
    assert backend.latest_recommendation(actor="sample_admin") == {"status": "none"}


def test_recommendation_resolves_the_configured_resident_before_dispatch(monkeypatch, subtests):
    """发起推荐前解析 resident-discovery 登记的 Resident，未登记或登记为 Lineage 时不派发"""
    from management_portal import application_targets

    for scenario in ("current", "unbound", "lineage_holder"):
        with subtests.test(scenario=scenario), monkeypatch.context() as patch:
            targets = []
            duties = []

            def resident():
                duty = "resident-discovery"
                duties.append(duty)
                if scenario == "unbound":
                    raise application_targets.ApplicationUnconfigured("resident-discovery 尚未登记承担者")
                if scenario == "lineage_holder":
                    raise application_targets.ApplicationUnconfigured("resident-discovery 登记的是 Lineage，不是 Resident")
                return {"duty": duty, "resident_id": "resident_222222222222", "lineage_id": "lineage_111111111111"}

            def submit(**kwargs):
                targets.append(kwargs["resident_binding"])
                return {**kwargs["resident_binding"], "message_id": "msg_test"}

            patch.setattr(application_targets, "discovery_resident", resident)
            patch.setattr(backend, "get_society_service", lambda: SimpleNamespace(
                resident=SimpleNamespace(
                    get_resident_conversation_snapshot=lambda *_args: {"state": {"intake_state": "open"}}
                ),
            ))
            patch.setattr(backend, "submit_invocation", submit)
            patch.setattr(backend.system_application_flows, "binding", lambda application, *, actor, target: target)
            patch.setattr(backend, "get_portal_ui_state_store", lambda: SimpleNamespace(put=lambda *args: None, get=lambda *args: None))
            if scenario == "current":
                for query in ("first", "second"):
                    result = backend.start_recommendation(actor="sample_admin", query=query, client=Discovery())
                    assert result["resident_id"] == "resident_222222222222"
                assert duties == ["resident-discovery", "resident-discovery"]
                assert targets == [{"lineage_id": "lineage_111111111111", "resident_id": "resident_222222222222"}] * 2
            else:
                with pytest.raises(backend.ResidentDiscoveryUnavailable):
                    backend.start_recommendation(actor="sample_admin", query="test", client=Discovery())
                assert targets == []


@pytest.mark.parametrize("has_match", [True, False], ids=["match", "no-match"])
def test_recommendation_result_reads_protocol_messages(monkeypatch, has_match) -> None:
    """推荐结果从输入消息的上下文引用和回复消息中解析，有无匹配都返回 completed"""
    response = json.dumps({
        "summary": "route to Portal",
        "recommendations": [{
            "resident_id": "resident_aaaaaaaaaaaa",
            "reason": "owns the backend",
            "confidence": "high",
            "action": "open",
        }] if has_match else [],
        "fork_advice": {},
    })

    class ResidentService:
        def get_resident_input_state(self, *_args):
            return {
                "message_id": "message_routing000001",
                "state": "completed",
                "reply_message_ids": ["message_reply000001"],
            }

        def get_resident_message(self, _resident_id, _actor, message_id):
            if message_id == "message_routing000001":
                return {
                    "content_parts": [{
                        "type": "context_reference",
                        "context": {
                            "resident_routing": {
                                "query": "修复 Portal 后端",
                                "limit": 5,
                                "candidates": [
                                    backend._candidate_snapshot(Discovery().routing_candidates()["results"][0])
                                ],
                            }
                        },
                    }]
                }
            return {"content_parts": [{"type": "text", "text": response}]}

    class Service:
        resident = ResidentService()

    monkeypatch.setattr(backend, "get_society_service", lambda: Service())
    result = backend.recommendation_result(
        actor="sample_admin",
        resident_id="resident_routing000001",
        message_id="message_routing000001",
    )

    assert result["status"] == "completed"
    assert result["result"]["summary"] == "route to Portal"
    if has_match:
        assert result["result"]["recommendations"][0]["resident_id"] == "resident_aaaaaaaaaaaa"
    else:
        assert result["result"]["recommendations"] == []
        assert result["result"]["fallback"] is False


@pytest.mark.parametrize("reply", [
    "not JSON",
    json.dumps({"recommendations": "not an array"}),
    json.dumps({"recommendations": [{"resident_id": "resident_previous000"}]}),
])
def test_completed_invalid_recommendation_is_terminal(monkeypatch, reply):
    """回复不是合法推荐或只含非候选 Resident 时，推荐结果以不可重试的失败结束"""
    context = {
        "query": "未读状态", "request_id": "request-invalid", "limit": 5,
        "candidates": Discovery().routing_candidates()["results"],
    }

    def get_message(_resident, _actor, message_id):
        if message_id == "input":
            return {"content_parts": [{
                "type": "context_reference", "context": {"resident_routing": context},
            }]}
        return {"content_parts": [{"type": "text", "text": reply}]}

    service = SimpleNamespace(
        get_resident_input_state=lambda *_: {
            "state": "completed", "reply_message_ids": ["reply"],
        },
        get_resident_message=get_message,
    )
    monkeypatch.setattr(backend, "get_society_service", lambda: SimpleNamespace(resident=service))
    result = backend.recommendation_result(actor="sample_admin", resident_id="routing", message_id="input")
    assert result["status"] == "failed"
    assert result["request_id"] == "request-invalid"
    assert result["error_code"] == "recommendation.invalid_result"
    assert result["retryable"] is False
    assert result["input_state"]["state"] == "completed"
    assert "重新提交" in result["error"]
    assert "result" not in result


def test_completed_recommendation_without_context_is_terminal(monkeypatch):
    """已完成的推荐缺少路由上下文时以 context_missing 失败结束且不可重试"""
    service = SimpleNamespace(
        get_resident_input_state=lambda *_: {"state": "completed"},
        get_resident_message=lambda *_: {"content_parts": []},
    )
    monkeypatch.setattr(backend, "get_society_service", lambda: SimpleNamespace(resident=service))
    result = backend.recommendation_result(actor="sample_admin", resident_id="routing", message_id="input")
    assert result["status"] == "failed"
    assert result["error_code"] == "recommendation.context_missing"
    assert result["retryable"] is False


def test_recommendation_result_preserves_transient_read_errors(monkeypatch):
    """读取推荐结果时的临时读取错误原样抛出"""
    def unreadable(*_):
        raise ResidentDiscoveryUnavailable("temporary read failure")

    from management_portal.resident_discovery_client import ResidentDiscoveryUnavailable
    service = SimpleNamespace(
        get_resident_input_state=lambda *_: {"state": "completed"},
        get_resident_message=unreadable,
    )
    monkeypatch.setattr(backend, "get_society_service", lambda: SimpleNamespace(resident=service))
    with pytest.raises(ResidentDiscoveryUnavailable, match="temporary read failure"):
        backend.recommendation_result(actor="sample_admin", resident_id="routing", message_id="input")


def test_recommendation_result_waits_for_completed_reply_projection(monkeypatch) -> None:
    """输入已完成但回复尚无内容时推荐结果仍为 running"""
    class ResidentService:
        def get_resident_input_state(self, *_args):
            return {
                "message_id": "message_routing000001",
                "state": "completed",
                "reply_message_ids": ["message_reply000001"],
            }

        def get_resident_message(self, _resident_id, _actor, message_id):
            if message_id == "message_routing000001":
                return {
                    "content_parts": [{
                        "type": "context_reference",
                        "context": {
                            "resident_routing": {
                                "query": "修复 Portal 后端",
                                "limit": 5,
                                "candidates": [
                                    backend._candidate_snapshot(
                                        Discovery().routing_candidates()["results"][0]
                                    )
                                ],
                            }
                        },
                    }]
                }
            return {"content_parts": []}

    class Service:
        resident = ResidentService()

    monkeypatch.setattr(backend, "get_society_service", lambda: Service())
    result = backend.recommendation_result(
        actor="sample_admin",
        resident_id="resident_routing000001",
        message_id="message_routing000001",
    )

    assert result["status"] == "running"
    assert result["input_state"]["state"] == "completed"


def test_recommendation_result_fails_when_capability_resident_closed_intake(monkeypatch) -> None:
    """推荐 Resident 关闭 intake 时排队中的推荐结果判为失败"""
    class ResidentService:
        def get_resident_input_state(self, *_args):
            return {"message_id": "message_routing000001", "state": "queued"}

        def get_resident_conversation_snapshot(self, *_args):
            return {"state": {"intake_state": "closed"}}

    class Service:
        resident = ResidentService()

    monkeypatch.setattr(backend, "get_society_service", lambda: Service())
    result = backend.recommendation_result(
        actor="sample_admin",
        resident_id="resident_routing000001",
        message_id="message_routing000001",
    )

    assert result["status"] == "failed"
    assert "not accepting work" in result["error"]


def test_recommendation_rejects_closed_capability_even_with_fallback_enabled(
    monkeypatch, capability_binding,
) -> None:
    """推荐 Resident 关闭 intake 时同步推荐即使允许回退也报错，不派发调用"""
    def closed(_actor):
        raise backend.RecommendationIntakeClosed("closed intake")

    monkeypatch.setattr(backend, "_available_recommendation_resident_binding", closed)
    monkeypatch.setattr(
        backend,
        "run_resident_turn",
        lambda **_kwargs: pytest.fail("closed capability must not receive an invocation"),
    )

    with pytest.raises(backend.RecommendationIntakeClosed, match="closed intake"):
        backend.recommend_residents(
            actor="sample_admin",
            query="修复 Portal 后端",
            allow_fallback=True,
            client=Discovery(),
        )


def test_start_recommendation_completes_with_fallback_when_capability_closed(
    monkeypatch, capability_binding,
) -> None:
    """推荐 Resident 关闭 intake 时发起推荐改用目录检索结果完成，并保存该结果"""
    saved = {}

    class UIStateStore:
        def put(self, actor, namespace, key, value):
            saved.update(actor=actor, namespace=namespace, key=key, value=value)

        def get(self, actor, namespace, key):
            return None

    def closed(_actor):
        raise backend.RecommendationIntakeClosed("closed intake")

    monkeypatch.setattr(backend, "get_portal_ui_state_store", lambda: UIStateStore())
    monkeypatch.setattr(backend, "_available_recommendation_resident_binding", closed)
    monkeypatch.setattr(
        backend,
        "submit_invocation",
        lambda **_kwargs: pytest.fail("closed capability must not receive an invocation"),
    )

    result = backend.start_recommendation(
        actor="sample_admin",
        query="修复 Portal 后端",
        client=Discovery(),
    )

    assert result["status"] == "completed"
    assert result["result"]["fallback"] is True
    assert result["request_id"].startswith("resident-recommendation-")
    assert result["result"]["fallback_reason"] == "recommendation-capability-intake-closed"
    assert result["result"]["recommendation_source"] == "directory-search"
    assert result["result"]["recommendations"][0]["resident_id"] == "resident_aaaaaaaaaaaa"
    assert saved["value"] == {
        "status": "completed",
        "query": "修复 Portal 后端",
        "request_id": result["request_id"],
        "result": result["result"],
    }


def test_latest_recommendation_restores_synchronous_fallback(monkeypatch) -> None:
    """最近一次推荐可恢复同步完成的目录检索回退结果"""
    fallback = {
        "schema": "tfi.resident-discovery.recommendation.v2",
        "query": "继续已有工作",
        "summary": "推荐现有 Resident",
        "fallback": True,
        "recommendations": [{"resident_id": "resident_aaaaaaaaaaaa"}],
        "fork_advice": {"recommended": False},
    }

    class UIStateStore:
        def get(self, actor, namespace, key):
            return {
                "value": {
                    "status": "completed",
                    "query": "继续已有工作",
                    "request_id": "resident-recommendation-fallback",
                    "result": fallback,
                },
            }

    monkeypatch.setattr(backend, "get_portal_ui_state_store", lambda: UIStateStore())

    assert backend.latest_recommendation(actor="sample_admin") == {
        "status": "completed",
        "query": "继续已有工作",
        "request_id": "resident-recommendation-fallback",
        "result": fallback,
    }


@pytest.mark.parametrize(
    "limit,query", [(5, "Portal的codex账号管理"), (8, "任务" * 2000)],
    ids=["reported-query", "maximum-query-and-limit"],
)
def test_large_directory_fallback_persists_complete_cards(monkeypatch, tmp_path, limit, query):
    """超长查询和大量候选的目录检索回退结果完整保存，且只对当前用户可见"""
    from management_portal.portal_ui_state import PortalUIStateStore

    candidates = [{
        "resident_id": f"resident_{index:012x}",
        "lineage_id": f"lineage_{index:012x}",
        "title": "名" * 160,
        "lineage_title": "组" * 160,
        "summary": {
            "current_focus": "职责" * 250,
            "primary_ownership": ["维护账号" * 45] * 5,
        },
        "summary_text": "重复的检索摘要" * 4000,
        "retrieval_sources": ["lexical", "vector"],
    } for index in range(limit)]
    original = json.dumps(candidates, ensure_ascii=False)
    store = PortalUIStateStore()
    monkeypatch.setattr(backend, "get_portal_ui_state_store", lambda: store)

    def closed(_actor):
        raise backend.RecommendationIntakeClosed("closed intake")

    monkeypatch.setattr(backend, "_available_recommendation_resident_binding", closed)
    monkeypatch.setattr(backend, "submit_invocation", lambda **_: pytest.fail("must not dispatch"))
    response = backend.start_recommendation(
        actor="test-actor", query=query, limit=limit,
        client=SimpleNamespace(routing_candidates=lambda **_: {"results": candidates}),
    )

    assert response["status"] == "completed"
    result = response["result"]
    assert result["fallback"] is True
    assert result["recommendation_source"] == "directory-search"
    assert result["fallback_reason"] == "recommendation-capability-intake-closed"
    assert result["recommendations"] == [{
        **{key: candidate[key] for key in ("resident_id", "lineage_id", "title", "lineage_title")},
        "reason": backend._fallback_reason(candidate),
        "confidence": "low", "recommended_action": "open",
    } for candidate in candidates]
    # Reopen the real store: neither result truncation nor a process cache can hide a failure.
    store = PortalUIStateStore()
    assert backend.latest_recommendation(actor="test-actor") == {**response, "query": query}
    assert backend.latest_recommendation(actor="another-actor") == {"status": "none"}
    assert json.dumps(candidates, ensure_ascii=False) == original


def test_model_recommendation_exposes_card_fields_not_discovery_internals():
    """模型推荐结果只输出卡片字段，理由为空时用候选的 current_focus 补上"""
    candidate = Discovery().routing_candidates()["results"][0]
    candidate["summary_text"] = "internal retrieval text" * 5000
    candidate["retrieval_sources"] = ["lexical", "vector"]
    result = backend._normalize("账号管理", [candidate], {
        "summary": "Use the existing owner",
        "recommendations": [{
            "resident_id": candidate["resident_id"], "reason": "",
            "confidence": "high", "action": "fork",
        }],
    }, 5)
    assert result["recommendations"] == [{
        **{key: candidate[key] for key in ("resident_id", "lineage_id", "title", "lineage_title")},
        "reason": candidate["summary"]["current_focus"],
        "confidence": "high", "recommended_action": "fork",
    }]
    assert candidate["summary_text"]


@pytest.mark.parametrize("count,query", [
    (40, '我想讨论 My Lineage、个人 AMA 和 Which Resident 的路由职责。'),
    (48, '中文🧩"\\\n\t\x01' * 500),
], ids=["forty-candidates", "maximum-candidates-and-escaped-query"])
def test_large_recommendation_passes_real_invocation_budget_and_recovers(
    monkeypatch, capability_binding, count, query,
):
    """数十个大候选的推荐请求压缩到参数预算内派发，完成后能恢复推荐与 fork 建议"""
    from copy import deepcopy
    from management_portal import resident_handoff
    from management_portal.society_service.lineage_primitives import normalize_event_payload

    capability_binding.update(event_source_id="event_111111111111", effect_id="effect_111111111111")
    candidates = [{
        "resident_id": f"resident_{index:012x}", "lineage_id": f"lineage_{index:012x}",
        "title": '职责🧩"\\' * 50, "lineage_title": '项目组🧩"\\' * 50,
        "summary": {
            "current_focus": "小批量发布及完成验证；" * 80,
            "branch_delta": "保留分支职责证据；" * 80,
            **{field: ["负责 Portal 中文🧩\"\\\n\x01 的设计和实现；" * 40] * 5 for field in (
                "primary_ownership", "supporting_expertise", "durable_expertise", "route_when", "avoid_when",
            )},
        },
    } for index in range(count)]
    original = deepcopy(candidates)
    captured = {}

    def trigger(_self, binding, *, actor, prompt, request_id, content_parts):
        # Exercise the actual handoff's context assembly and the same validator
        # that rejected the reported request, without dispatching a live Event.
        normalize_event_payload({"prompt": prompt, "content_parts": content_parts})
        assert backend._json_size({"content_parts": content_parts}) <= backend.RECOMMENDATION_ARGUMENT_BUDGET
        assert len(prompt.encode("utf-8")) <= backend.RECOMMENDATION_PROMPT_BUDGET
        captured.update(prompt=prompt, content_parts=content_parts)
        return {**binding, "message_id": request_id}

    monkeypatch.setattr(resident_handoff, "ensure_invocation_resident", lambda **_: capability_binding)
    monkeypatch.setattr(backend.WorkflowBindings, "trigger", trigger)
    response = backend.start_recommendation(
        actor="test-actor", query=query, limit=8 if count == 48 else 5,
        client=SimpleNamespace(routing_candidates=lambda **_: {"results": candidates}),
    )
    assert response["status"] == "accepted"
    assert backend.latest_recommendation(actor="test-actor")["message_id"] == response["message_id"]
    payload = json.loads(captured["prompt"].split("\n\n", 1)[1])
    snapshots = captured["content_parts"][0]["context"]["resident_routing"]["candidates"]
    ids = [item["resident_id"] for item in candidates]
    assert [item["resident_id"] for item in snapshots] == ids
    assert [item["resident_id"] for item in payload["candidates"]] == ids
    assert payload["request"] == query
    assert all(set(item["summary"]) == {"current_focus"} for item in snapshots)
    assert all(item["primary_ownership"] for item in payload["candidates"])
    assert candidates == original

    reply = json.dumps({
        "recommendations": [{"resident_id": ids[index], "confidence": confidence, "action": "open"}
                            for index, confidence in [(20, "high"), (count - 1, "medium")]],
        "fork_advice": {"recommended": True, "source_resident_id": ids[-1]},
    })
    messages = {
        response["message_id"]: {"content_parts": captured["content_parts"]},
        "reply": {"content_parts": [{"type": "text", "text": reply}]},
    }
    monkeypatch.setattr(backend, "get_society_service", lambda: SimpleNamespace(resident=SimpleNamespace(
        get_resident_input_state=lambda *_: {"state": "completed", "reply_message_ids": ["reply"]},
        get_resident_message=lambda _resident, _actor, message_id: messages[message_id],
    )))
    recovered = backend.recommendation_result(
        actor="test-actor", resident_id=response["resident_id"], message_id=response["message_id"],
    )
    assert recovered["status"] == "completed"
    assert [item["resident_id"] for item in recovered["result"]["recommendations"]] == [ids[20], ids[-1]]
    assert [item["confidence"] for item in recovered["result"]["recommendations"]] == ["high", "medium"]
    assert recovered["result"]["fork_advice"]["source_lineage_id"] == candidates[-1]["lineage_id"]


@pytest.mark.parametrize("remaining", [0, -1])
def test_recommendation_structured_budget_boundary(monkeypatch, remaining):
    """推荐上下文在预算恰好够用或差一字节时都能压缩到预算内"""
    from management_portal.society_service.lineage_primitives import normalize_invocation_arguments

    candidates = Discovery().routing_candidates()["results"]
    query = '查找中文🧩"\\\n的职责'
    context = backend._routing_context(query, candidates, 5, "request")
    arguments = {"content_parts": [{"type": "context_reference", "context": context}]}
    budget = backend._json_size(arguments) + remaining
    monkeypatch.setattr(backend, "RECOMMENDATION_ARGUMENT_BUDGET", budget)
    bounded = {"content_parts": [{"type": "context_reference", "context": backend._routing_context(query, candidates, 5, "request")}]}
    assert backend._json_size(bounded) <= budget
    normalize_invocation_arguments(bounded)


def test_unfittable_payload_is_terminal_and_restored_without_dispatch(monkeypatch):
    """推荐参数无法压进预算时直接以 input_too_large 失败，不派发且可恢复该失败"""
    monkeypatch.setattr(backend, "RECOMMENDATION_ARGUMENT_BUDGET", 100)
    monkeypatch.setattr(backend, "_available_recommendation_resident_binding", lambda *_: pytest.fail("must not prepare a workflow"))
    monkeypatch.setattr(backend, "submit_invocation", lambda **_: pytest.fail("must not dispatch"))
    response = backend.start_recommendation(actor="test-actor", query="查询", client=Discovery())
    assert response["status"] == "failed"
    assert response["error_code"] == "recommendation.input_too_large"
    assert response["retryable"] is False
    assert "字节" in response["error"]
    assert backend.latest_recommendation(actor="test-actor") == {**response, "query": "查询"}


@pytest.mark.parametrize("entrypoint", ["start_recommendation", "recommend_residents"])
def test_overlong_query_is_rejected_without_truncation_or_search(entrypoint):
    """超过 4000 字的查询直接被拒绝，不截断也不检索"""
    client = SimpleNamespace(routing_candidates=lambda **_: pytest.fail("must not search a truncated query"))
    with pytest.raises(ValueError, match="4000"):
        getattr(backend, entrypoint)(actor="test-actor", query="问" * 4001, client=client)
