"""What each Adapter shows for the Harness capabilities its Provider declares.

A Provider declares capabilities (``AgentProvider.harness_capabilities``) and
Portal relies on them without looking at the Provider's name, so a
declaration alone does not verify the Adapter's behavior. Every capability
therefore names, for each
declaring Provider, the test that exercises it on that Provider's Adapter
(``ADAPTER_TESTS``), unless the catalog test checks it on the Adapter class
itself (``CLASS_CHECKED``).  A capability whose events have a common shape has
a check here, and its Adapter tests hand the events they got to it.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from management_common.provider_subagent_runs import PROGRESS_METHOD, run_item
from management_portal.execution import harness_capabilities as capabilities
from management_portal.execution.harness_native_items import NATIVE_TOOL_ITEM_TYPES

# test_catalog_declares_what_the_adapter_class_implements checks these on the class.
CLASS_CHECKED = frozenset({capabilities.AUTO_COMPACT_THRESHOLD})

_TURN_INPUT = "tests/cases/test_turn_input_during_turn.py"
_CONTINUATION = "tests/cases/test_provider_error_continuation.py"
_EXECUTION = "tests/cases/test_execution_layer.py"
_CAPABILITIES = "tests/cases/test_harness_capabilities.py"
_SUBAGENTS = "tests/cases/test_subagent_trace.py"
_NATIVE_TOOLS = "tests/cases/test_claude_harness_tools.py::test_a_native_socket_turn_offers_portal_tools_and_hands_their_results_to_the_harness"

ADAPTER_TESTS: dict[str, dict[str, str]] = {
    capabilities.CONTEXT_USAGE_EVENTS: {
        "antigravity": "tests/cases/test_antigravity.py::test_turn_serves_portal_tools_and_reports_native_events_and_usage",
        "codex": f"{_EXECUTION}::test_codex_backend_projects_current_context_usage_from_token_events",
        "claude": "tests/cases/test_claude_code_daemon.py::ClaudeCodeDaemonTest::test_context_usage_reports_current_window_and_rejects_stale_turn",
        "kimi": "tests/cases/test_live_provider_ingress.py::test_kimi_live_context_reports_latest_main_request_including_cached_input",
        "dsh": "tests/cases/test_dsh_bridge_handover.py::test_a_retiring_worker_leaves_the_turn_and_its_tool_call_to_the_next_worker",
        "opencode": "tests/cases/test_opencode_adapter.py::test_an_opencode_turn_serves_its_portal_tools_and_reports_its_own_tool_calls",
    },
    capabilities.PORTAL_TOOLS: {
        "antigravity": "tests/cases/test_antigravity.py::test_turn_serves_portal_tools_and_reports_native_events_and_usage",
        "claude": _NATIVE_TOOLS,
        "codex": "tests/cases/test_agent_harness.py::test_codex_harness_tool_result_passes_through_tool_batch_lifecycle",
        "dsh": _NATIVE_TOOLS,
        "kimi": "tests/cases/test_kimi_portal_tools.py::test_a_kimi_turn_serves_its_harness_tools_to_its_own_session",
        "opencode": "tests/cases/test_opencode_adapter.py::test_an_opencode_turn_serves_its_portal_tools_and_reports_its_own_tool_calls",
    },
    capabilities.NATIVE_TOOL_EVENTS: {
        "antigravity": "tests/cases/test_antigravity.py::test_turn_serves_portal_tools_and_reports_native_events_and_usage",
        "claude": f"{_TURN_INPUT}::test_native_text_and_reasoning_reach_the_governor_as_activity",
        "codex": f"{_EXECUTION}::test_codex_backend_bridges_native_events_and_turn_controls",
        "dsh": f"{_TURN_INPUT}::test_native_text_and_reasoning_reach_the_governor_as_activity",
        "kimi": f"{_TURN_INPUT}::test_kimi_reports_the_main_agents_tool_calls_live_in_the_common_shape",
        "opencode": "tests/cases/test_opencode_adapter.py::test_an_opencode_turn_serves_its_portal_tools_and_reports_its_own_tool_calls",
    },
    capabilities.ACTIVE_TURN_INPUT: {
        "claude": f"{_TURN_INPUT}::test_a_native_turn_takes_send_now_while_it_runs",
        "codex": f"{_EXECUTION}::test_codex_backend_maps_execution_input_control_to_native_steer",
        "dsh": f"{_TURN_INPUT}::test_a_native_turn_takes_send_now_while_it_runs",
        "kimi": f"{_TURN_INPUT}::test_kimi_backend_hands_send_now_to_the_running_prompt",
        "opencode": "tests/cases/test_opencode_adapter.py::test_an_opencode_turn_takes_send_now_while_it_runs",
    },
    capabilities.ERROR_CONTINUATION: {
        "claude": f"{_CONTINUATION}::test_a_connection_failure_while_anthropic_answers_is_an_ordinary_provider_error",
        "codex": f"{_EXECUTION}::test_codex_backend_continues_capacity_failure_once",
        "dsh": f"{_CONTINUATION}::test_a_failed_turn_is_continued_once_in_the_same_session",
        "kimi": f"{_CONTINUATION}::test_a_failed_kimi_turn_is_continued_in_its_session",
        "opencode": "tests/cases/test_opencode_adapter.py::test_a_failed_opencode_turn_is_continued_in_its_session",
    },
    capabilities.NATIVE_IMAGE_INPUT: {
        "claude": "tests/cases/test_claude_native_integration.py::test_claude_images_reach_native_client_and_common_composer",
        "codex": f"{_EXECUTION}::test_codex_backend_maps_input_media_to_native_app_server_images",
    },
    capabilities.INPUT_IMAGE_DETAIL: {
        "codex": f"{_CAPABILITIES}::test_codex_takes_a_reasoning_effort_and_an_image_detail_for_each_turn",
    },
    capabilities.TURN_REASONING_EFFORT: {
        "codex": f"{_CAPABILITIES}::test_codex_takes_a_reasoning_effort_and_an_image_detail_for_each_turn",
    },
    capabilities.SUBAGENTS: {
        "claude": f"{_SUBAGENTS}::test_providers_that_can_start_subagents_declare_it_and_follow_the_setting",
        "codex": f"{_EXECUTION}::test_codex_backend_enables_subagents_only_when_explicitly_allowed_contracts",
        "kimi": f"{_SUBAGENTS}::test_kimi_without_subagents_has_no_agent_tools",
        "dsh": "tests/cases/test_dsh_backend.py::test_dsh_subagents_follow_the_residents_setting",
        "opencode": "tests/cases/test_opencode_adapter.py::test_opencode_subagents_follow_the_residents_setting",
    },
    capabilities.SUBAGENT_EVENTS: {
        "claude": "tests/cases/test_claude_code_daemon.py::ClaudeCodeDaemonTest::test_a_subagent_runs_card_comes_when_the_model_has_its_result",
        "codex": f"{_SUBAGENTS}::test_codex_subagent_cards_follow_the_child_threads",
        "kimi": f"{_SUBAGENTS}::test_kimi_subagent_card_comes_with_the_runs_end",
        "dsh": "tests/cases/test_dsh_bridge_handover.py::test_a_subagents_steps_reach_the_worker_under_its_call_and_end_in_its_card",
        "opencode": "tests/cases/test_opencode_adapter.py::test_an_opencode_subagents_steps_reach_the_worker_under_its_call_and_end_in_its_card",
    },
    capabilities.SUBAGENT_STOP: {
        "claude": "tests/cases/test_claude_code_daemon.py::ClaudeCodeDaemonTest::test_one_subagent_stops_through_its_cli_task_while_the_turn_goes_on",
        "codex": f"{_SUBAGENTS}::test_codex_stops_one_subagent_by_interrupting_its_own_thread_while_the_turn_goes_on",
        "kimi": f"{_SUBAGENTS}::test_kimi_stops_one_subagent_by_cancelling_its_task_while_the_turn_goes_on",
        "dsh": "tests/cases/test_dsh_bridge_handover.py::test_one_subagent_stops_as_its_parent_would_stop_it_while_the_turn_goes_on",
        "opencode": "tests/cases/test_opencode_adapter.py::test_opencode_stops_one_subagent_by_aborting_its_child_session_while_the_turn_goes_on",
    },
}
# The check each Adapter test of these capabilities hands its events to.
EVENT_CHECKS = {
    capabilities.NATIVE_TOOL_EVENTS: "check_tool_events",
    capabilities.SUBAGENT_EVENTS: "check_subagent_events",
}


def declared(definition: Any) -> frozenset[str]:
    """What a Provider declares on any of its routes (image input depends on the route)."""

    found = set(definition.harness_capabilities())
    if definition.media and definition.image_input_transport == "native":
        found.add(capabilities.NATIVE_IMAGE_INPUT)
    return frozenset(found)


def check_tool_events(events: Iterable[Mapping[str, Any]]) -> None:
    """The Provider's own tool calls reach the Harness as items the loop governor counts.

    Each call starts and then completes with the same id; a shell call says
    its command and any other tool its name.  No Provider-specific tool item
    (a ``toolCall``) reaches the Harness untranslated.
    """

    started: set[str] = set()
    completed: set[str] = set()
    for event in events:
        method = event.get("method")
        params = event.get("params") or {}
        item = params.get("item") if isinstance(params.get("item"), Mapping) else {}
        if method not in {"item/started", "item/completed"}:
            continue
        assert item.get("type") != "toolCall", item
        if item.get("type") not in NATIVE_TOOL_ITEM_TYPES:
            continue
        call = str(item.get("id") or "")
        assert call, item
        if item["type"] == "commandExecution":
            assert item.get("command") or item.get("commandActions"), item
        elif item["type"] == "dynamicToolCall":
            assert item.get("tool"), item
        if method == "item/started":
            started.add(call)
        else:
            assert call in started, ("completed before it started", item)
            assert item.get("status") in {"completed", "failed", "declined"}, item
            completed.add(call)
    assert completed, "no tool call reached the Harness"


RUN_FIELDS = frozenset(run_item({})["run"])
ENDS = {"completed", "failed", "stopped"}


def _card(params: Mapping[str, Any]) -> Mapping[str, Any]:
    assert {"threadId", "turnId", "item"} <= set(params) and params["turnId"], params
    item = params["item"]
    run = item["run"]
    assert item["type"] == "subagentRun" and set(run) == RUN_FIELDS, item
    assert item["id"] == f"subagent-run:{run['tool_use_id']}:{run['end_id']}", item
    assert run["tool_use_id"] and run["turn_id"] == params["turnId"] and isinstance(run["background"], bool), run
    return run


def check_subagent_events(events: Iterable[Mapping[str, Any]]) -> None:
    """Every subagent event is in the common shape, and each of the three kinds came."""

    seen = set()
    for event in events:
        method = event.get("method")
        params = event.get("params") or {}
        if method == "subagent/event":
            assert isinstance(params.get("traceKey"), str) and params["traceKey"], params
            assert isinstance(params.get("parentToolUseId"), str) and params["parentToolUseId"], params
            assert isinstance(params.get("ancestorTraceKeys", []), list), params
            inner = params.get("event")
            assert isinstance(inner, Mapping) and isinstance(inner.get("method"), str) and isinstance(inner.get("params"), Mapping), params
        elif method == PROGRESS_METHOD:
            run = _card(params)
            assert (run["status"], run["end_id"], run["result"], run["finished_at"]) == ("running", "live", "", ""), run
        elif method == "item/completed" and (params.get("item") or {}).get("type") == "subagentRun":
            run = _card(params)
            assert run["status"] in ENDS and run["end_id"] not in {"", "live"} and run["finished_at"], run
            method = "subagentRun"
        else:
            continue
        seen.add(method)
    assert seen == {"subagent/event", PROGRESS_METHOD, "subagentRun"}, seen
