from __future__ import annotations

from pathlib import Path

from scripts.agent_harness_benchmark import run


def test_agent_harness_effectiveness_benchmark_runs_serially() -> None:
    """AgentHarness 效果基准在本地按顺序串行运行，不调用模型，各功能均验证生效"""
    payload = run(Path(__file__).resolve().parents[2])
    results = payload["results"]

    assert payload["execution_mode"] == "serial_local_no_model_calls"
    assert payload["measurement_model_calls"] == 0
    assert payload["excluded_feature_ids"] == ["inline_media_history"]
    assert [result["order"] for result in results] == list(
        range(1, len(results) + 1)
    )
    assert [result["feature_id"] for result in results] == [
        "tool_call_granularity",
        "unified_resource_recall",
        "early_context_compaction",
        "hash_aware_read_dedup",
        "tool_output_offloading",
        "artifact_search_and_slice",
    ]
    assert all(result["status"] == "applied" for result in results)
    by_id = {result["feature_id"]: result for result in results}
    assert by_id["tool_call_granularity"]["policy_attached_once"] is True
    assert by_id["early_context_compaction"][
        "configured_limit_verified"
    ] is True
    assert by_id["hash_aware_read_dedup"][
        "first_exact_output_preserved"
    ] is True
    assert by_id["tool_output_offloading"]["exact_recall_verified"] is True
    assert by_id["artifact_search_and_slice"]["search_hit_verified"] is True
    assert by_id["artifact_search_and_slice"]["exact_slice_verified"] is True
    assert by_id["artifact_search_and_slice"][
        "net_token_reduction_percent"
    ] > 99
    assert by_id["unified_resource_recall"]["search_hit_verified"] is True
    assert by_id["unified_resource_recall"]["exact_slice_verified"] is True
    assert by_id["unified_resource_recall"]["turn_cache_verified"] is True
    assert by_id["unified_resource_recall"][
        "unreferenced_resource_denied"
    ] is True
    assert by_id["unified_resource_recall"][
        "net_token_reduction_percent"
    ] > 99
