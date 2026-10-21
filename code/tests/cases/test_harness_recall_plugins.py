from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal.execution.backends import (
    AgentSessionService,
    BackendContext,
    BackendInvocation,
    BackendResult,
    CodexBackend,
)
from management_portal.execution.contracts import (
    ExecutionRequest,
    SessionOptions,
)
from management_portal.execution.harness import AgentHarness, default_agent_harness
from management_portal.execution.harness_capabilities import provider_capabilities
from management_portal.execution.harness_artifact_search import (
    ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID,
    READ_HARNESS_ARTIFACT_TOOL_NAME,
    SEARCH_HARNESS_ARTIFACT_TOOL_NAME,
    ArtifactSearchAndSliceFeature,
    attach_artifact_recall_tools,
)
from management_portal.execution.harness_artifacts import HarnessArtifactStore
from management_portal.execution.harness_unified_resource_recall import (
    READ_PORTAL_RESOURCE_TOOL_NAME,
    SEARCH_PORTAL_RESOURCE_TOOL_NAME,
    UNIFIED_RESOURCE_RECALL_FEATURE_ID,
    UnifiedResourceRecallFeature,
)
from management_portal.execution.harness_tool_output_offloading import ToolOutputOffloadingFeature
from management_portal.execution.session_manager import RuntimeSession

# A test double of an Adapter declares what it can do, as a real Adapter does
# in the Provider catalog.  No real Adapter rewrites history today.



def _invocation(
    *,
    prompt: str = "continue",
    provider: str = "codex",
    is_new: bool = True,
    features: dict[str, dict] | None = None,
    runtime_options: dict | None = None,
) -> BackendInvocation:
    options = dict(runtime_options or {})
    if features is not None:
        options["agent_harness"] = {
            "mode": "manual",
            "features": features,
        }
    binding_id = "resident:recall-test"
    session_options = SessionOptions(
        mode="persistent",
        binding_id=binding_id,
        backend_route_id="zero" if provider == "codex" else "",
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt=prompt,
        service_type="agent_session",
        provider=provider,
        operation="turn",
        cwd="/tmp",
        runtime_options=options,
        session=session_options,
    )
    return BackendInvocation(
        request=request,
        session=RuntimeSession(
            mode=session_options.mode,
            binding_id=binding_id,
            backend_session_id=(
                "019f0000-0000-7000-8000-000000000001"
                if not is_new
                else ""
            ),
            backend_route_id=session_options.backend_route_id,
            is_new_backend_session=is_new,
        ),
        user_prompt=prompt,
        system_context="",
        attach_system_context=False,
        is_new_backend_session=is_new,
    )


def _tools(invocation: BackendInvocation) -> dict[str, object]:
    return {tool.name: tool for tool in invocation.harness_tools}


def test_artifact_search_finds_then_reads_exact_slice_within_scope_contracts(tmp_path, subtests, isolated_portal_case):
    """产物搜索先定位再按范围读取精确片段

    召回插件在调用者可见范围内搜索卸载产物，并读取精确的字符片段。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_artifact_search_finds_then_reads_exact_slice_within_scope'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = HarnessArtifactStore(tmp_path / "artifacts")
        content = (
            "ordinary build output\n" * 12_000
            + "UNIQUE_FAILURE_CODE=DB-9472 root cause is stale lease\n"
            + "ordinary trailing output\n" * 12_000
        )
        reference = store.put(
            scope="v2:codex:resident:recall-test",
            value=content,
        )
        feature = ArtifactSearchAndSliceFeature(store)
        invocation = _invocation(features={
            ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID: {"enabled": True},
        })

        prepared = feature.prepare_invocation(invocation)
        outcome = feature.apply(
            provider=SimpleNamespace(provider_id="codex"),
            invocation=prepared,
            context=None,
            config={"enabled": True},
        )

        assert outcome.status == "applied"
        tools = _tools(outcome.invocation)
        assert set(tools) == {
            READ_HARNESS_ARTIFACT_TOOL_NAME,
            SEARCH_HARNESS_ARTIFACT_TOOL_NAME,
        }
        found = json.loads(tools[SEARCH_HARNESS_ARTIFACT_TOOL_NAME].handler({
            "handle": reference.handle,
            "query": "DB-9472 stale lease",
            "max_hits": 2,
            "context_chars": 240,
        }))
        assert found["hit_count"] == 1
        assert "UNIQUE_FAILURE_CODE=DB-9472" in found["hits"][0]["content"]
        assert len(json.dumps(found, ensure_ascii=False)) < len(content) / 100
        exact_offset = content.index("UNIQUE_FAILURE_CODE=DB-9472")
        recalled = json.loads(tools[READ_HARNESS_ARTIFACT_TOOL_NAME].handler({
            "handle": reference.handle,
            "offset": exact_offset,
            "limit": 52,
        }))
        assert recalled["content"] == content[exact_offset:exact_offset + 52]
        assert recalled["sha256"] == reference.sha256
    with subtests.test(contract='test_artifact_search_rejects_another_session_'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = HarnessArtifactStore(tmp_path / "artifacts")
        foreign = store.put(
            scope="v2:codex:resident:other",
            value="private foreign output",
        )
        feature = ArtifactSearchAndSliceFeature(store)
        invocation = _invocation(features={
            ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID: {"enabled": True},
        })
        prepared = feature.prepare_invocation(invocation)
        search_tool = _tools(prepared)[SEARCH_HARNESS_ARTIFACT_TOOL_NAME]

        with pytest.raises(KeyError, match="not granted"):
            search_tool.handler({"handle": foreign.handle, "query": "private"})



class _FakeResourceService:
    def __init__(self, content: bytes, content_type: str = "text/plain") -> None:
        self.content = content
        self.content_type = content_type
        self.calls: list[tuple[str, str, int]] = []

    def read_content(
        self,
        address: str,
        *,
        actor: str,
        max_bytes: int,
    ) -> tuple[bytes, str, dict]:
        self.calls.append((address, actor, max_bytes))
        return (
            self.content,
            self.content_type,
            {
                "resource_id": address.partition("/")[0],
                "title": "Large decision",
            },
        )


def test_unified_resource_recall_is_lazy_cached_and_actor_authorized() -> None:
    """统一资源召回按需读取并缓存引用的资源，只允许访问提示中明确引用的地址"""
    address = "decision_123456789abc"
    content = (
        "normal architecture notes\n" * 14_000
        + "PORTAL_INVARIANT: Scheduler never edits business prompts.\n"
        + "normal appendix\n" * 14_000
    ).encode()
    service = _FakeResourceService(content)
    feature = UnifiedResourceRecallFeature(lambda: service)
    prompt = (
        "请核对这个资源。\n"
        "<resource_references>\n"
        f'<resource address="{address}" id="{address}" '
        'type="decision" status="resolved">\n'
        "title: Large decision\n"
        "</resource>\n"
        "</resource_references>"
    )
    invocation = _invocation(
        prompt=prompt,
        features={
            UNIFIED_RESOURCE_RECALL_FEATURE_ID: {
                "enabled": True,
                "max_resource_bytes": 2 * 1024 * 1024,
            },
        },
    )

    prepared = feature.prepare_invocation(invocation)
    outcome = feature.apply(
        provider=SimpleNamespace(provider_id="codex"),
        invocation=prepared,
        context=None,
        config={
            "enabled": True,
            "max_resource_bytes": 2 * 1024 * 1024,
        },
    )

    assert outcome.status == "applied"
    assert outcome.metrics["eager_resource_content_tokens"] == 0
    tools = _tools(outcome.invocation)
    found = json.loads(tools[SEARCH_PORTAL_RESOURCE_TOOL_NAME].handler({
        "address": address,
        "query": "Scheduler business prompts",
        "context_chars": 260,
    }))
    assert found["hit_count"] == 1
    assert "PORTAL_INVARIANT" in found["hits"][0]["content"]
    assert found["cache_hit"] is False
    offset = content.decode().index("PORTAL_INVARIANT")
    recalled = json.loads(tools[READ_PORTAL_RESOURCE_TOOL_NAME].handler({
        "address": address,
        "offset": offset,
        "limit": 58,
    }))
    assert recalled["content"] == content.decode()[offset:offset + 58]
    assert recalled["cache_hit"] is True
    assert service.calls == [(address, "sample_admin", 2 * 1024 * 1024)]
    assert len(json.dumps(found, ensure_ascii=False)) < len(content) / 100

    with pytest.raises(PermissionError, match="not explicitly referenced"):
        tools[SEARCH_PORTAL_RESOURCE_TOOL_NAME].handler({
            "address": "decision_aaaaaaaaaaaa",
            "query": "secret",
        })
    assert len(service.calls) == 1


def test_unified_resource_recall_keeps_handlers_for_continued_sessions_contracts(subtests, isolated_portal_case):
    """统一资源召回在续接会话中保留处理器

    会话继续时，统一资源召回工具的处理器仍然有效。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_unified_resource_recall_keeps_handlers_for_continued_sessions'), isolated_portal_case() as case_path:
        address = "decision_123456789abc"
        prompt = f"请读取 {address}"
        continued = _invocation(
            prompt=prompt,
            is_new=False,
            features={
                UNIFIED_RESOURCE_RECALL_FEATURE_ID: {"enabled": True},
            },
        )
        service = _FakeResourceService(b"binary", "image/png")
        feature = UnifiedResourceRecallFeature(lambda: service)

        prepared_continued = feature.prepare_invocation(continued)
        outcome = feature.apply(
            provider=SimpleNamespace(provider_id="codex"),
            invocation=prepared_continued,
            context=None,
            config={"enabled": True},
        )
        assert outcome.status == "applied"
        assert outcome.reason == (
            "unified_resource_recall_handler_available_"
            "existing_schema_unchanged"
        )
        assert outcome.metrics["tool_schema_exposed_this_turn"] is False
        assert set(_tools(outcome.invocation)) == {
            SEARCH_PORTAL_RESOURCE_TOOL_NAME,
            READ_PORTAL_RESOURCE_TOOL_NAME,
        }

        new_invocation = _invocation(
            prompt=prompt,
            features={
                UNIFIED_RESOURCE_RECALL_FEATURE_ID: {"enabled": True},
            },
        )
        prepared = feature.prepare_invocation(new_invocation)
        with pytest.raises(ValueError, match="textual content"):
            _tools(prepared)[READ_PORTAL_RESOURCE_TOOL_NAME].handler({
                "address": address,
            })
    with subtests.test(contract='test_default_harness_reuses_offloading_store_for_artifact_search'), isolated_portal_case() as case_path:
        harness = default_agent_harness()
        offloading = harness._features["tool_output_offloading"]
        search = harness._features[ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID]

        assert search.artifact_store is offloading.artifact_store


def test_schema_tools_install_without_references_and_hide_when_disabled(
    tmp_path: Path,
) -> None:
    """召回工具在没有资源引用时也安装，功能关闭时隐藏 schema 并拒绝调用"""
    resource_feature = UnifiedResourceRecallFeature(
        lambda: _FakeResourceService(b"unused")
    )
    enabled_new = _invocation(
        prompt="no resource on the first turn",
        features={
            UNIFIED_RESOURCE_RECALL_FEATURE_ID: {"enabled": True},
        },
    )
    installed = resource_feature.prepare_invocation(enabled_new)
    assert {
        item["name"]
        for item in CodexBackend._harness_tool_schemas(installed.harness_tools)
    } == {
        SEARCH_PORTAL_RESOURCE_TOOL_NAME,
        READ_PORTAL_RESOURCE_TOOL_NAME,
    }

    disabled_continued = _invocation(
        is_new=False,
        features={
            UNIFIED_RESOURCE_RECALL_FEATURE_ID: {"enabled": False},
        },
    )
    hidden = resource_feature.prepare_invocation(disabled_continued)
    assert len(hidden.harness_tools) == 2
    assert all(tool.expose_schema is False for tool in hidden.harness_tools)
    assert CodexBackend._harness_tool_schemas(hidden.harness_tools) == []
    with pytest.raises(PermissionError, match="disabled"):
        hidden.harness_tools[0].handler({})

    artifact_feature = ArtifactSearchAndSliceFeature(
        HarnessArtifactStore(tmp_path / "artifacts")
    )
    artifact_hidden = artifact_feature.prepare_invocation(
        _invocation(
            is_new=False,
            features={
                ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID: {"enabled": False},
            },
        )
    )
    assert len(artifact_hidden.harness_tools) == 2
    assert all(
        tool.expose_schema is False
        for tool in artifact_hidden.harness_tools
    )
    assert CodexBackend._harness_tool_schemas(
        artifact_hidden.harness_tools
    ) == []
    upgraded, changed, _reason = attach_artifact_recall_tools(
        artifact_hidden,
        artifact_feature.artifact_store,
        include_search=False,
    )
    assert changed == 1
    assert [
        item["name"]
        for item in CodexBackend._harness_tool_schemas(upgraded.harness_tools)
    ] == [READ_HARNESS_ARTIFACT_TOOL_NAME]
