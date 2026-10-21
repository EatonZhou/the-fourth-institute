"""Effect cleanup is transactional, bounded and independent of graph lifetime.

:module: lineage.hatg
:design: decision_4637ca4a69e2, decision_d918ebebc062
"""
import pytest

from management_portal.society_service.state_store import get_lineage_data_store


def test_cleanup_outbox_merges_retries_and_requires_current_acknowledgement():
    """Effect cleanup 合并重试并以当前任务 CAS 确认

    幂等合并调用者和ISA IDs；旧ACK不能删除并发新增依赖，created_at保持，匹配当前任务才删除。

    :design-point:
    """
    store = get_lineage_data_store()
    store.enqueue_effect_cleanup("lineage_deleted", "activation", {"alice": ["inst_lin_one"]})
    old = store.effect_cleanup_task("lineage_deleted", "activation")
    store.enqueue_effect_cleanup("lineage_deleted", "activation", {"alice": ["inst_lin_one", "inst_lin_two"], "bob": ["inst_res_three"]})
    assert store.complete_effect_cleanup("lineage_deleted", "activation", expected_instructions=old["instructions"]) is False
    task = store.effect_cleanup_task("lineage_deleted", "activation")
    assert task["instructions"] == {"alice": ["inst_lin_one", "inst_lin_two"], "bob": ["inst_res_three"]}
    assert task["created_at"] == old["created_at"]
    assert store.complete_effect_cleanup("lineage_deleted", "activation", expected_instructions=task["instructions"])
    assert store.effect_cleanup_task("lineage_deleted", "activation") is None


def test_cleanup_outbox_survives_missing_lineage_and_rolls_back_with_business_transaction():
    """Effect cleanup 独立于 Lineage 存续且随业务事务回滚

    Lineage不存在仍保存任务；业务事务失败不遗留任务，列表尊重限量。

    :design-point:
    """
    store = get_lineage_data_store()
    with pytest.raises(RuntimeError):
        with store.database.transaction():
            store.enqueue_effect_cleanup("lineage_missing", "rollback", {"alice": ["inst_lin_one"]})
            raise RuntimeError("business commit failed")
    assert store.effect_cleanup_task("lineage_missing", "rollback") is None
    for n in range(4):
        store.enqueue_effect_cleanup("lineage_missing", str(n), {})
    first = store.effect_cleanup_tasks(lineage_id="lineage_missing", limit=2)
    assert len(first) == 2
    cursor = tuple(first[-1][key] for key in ("created_at", "lineage_id", "activation_key"))
    second = store.effect_cleanup_tasks(lineage_id="lineage_missing", limit=2, after_key=cursor)
    assert len(second) == 2
    assert {r["activation_key"] for r in first}.isdisjoint(r["activation_key"] for r in second)
    assert len(store.effect_cleanup_tasks()) == 4
