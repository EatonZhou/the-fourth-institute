from __future__ import annotations

import os
import time

from management_portal.execution import config, harness_retention
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.agent_operations import put_operation
from management_portal.execution.harness_artifacts import HarnessArtifactStore
from management_portal.execution.harness_effectiveness import HarnessEffectivenessStore
from management_portal.execution.harness_evolution import HarnessEvolutionStore
from management_portal.execution.harness_evolution_offline import TurnCalls, load_turn_calls
from management_portal.execution.harness_retention import prune_artifacts
from management_portal.execution.service_settings import RETENTION_DAYS, ExecutionSettings
from tests.cases.database_test_support import connect
from tests.cases.test_harness_effectiveness import _decision

KEEP = 30 * 86400


def test_retention_keeps_referenced_and_recent_artifacts(tmp_path):
    """Harness artifact 清理只删除过期且无人引用的文件，保留被引用和新近的"""
    root = tmp_path / "harness-artifacts"
    artifacts = HarnessArtifactStore(root)
    handles = {name: artifacts.put(scope="session", value=name).handle
               for name in ("segment", "record", "recent", "orphan")}
    now = time.time()
    for name, handle in handles.items():
        if name != "recent":
            old = now - KEEP - 10
            os.utime(artifacts._artifact_path(handle), (old, old))
    data = AgentDataStore()
    data.put_segment(segment_type="tool", content={"output": handles["segment"]})
    with connect(data.database) as connection:
        put_operation(connection, operation_id="recall", kind="artifact", agent_id="resident-test",
                      created_at="2026-10-12T00:00:00Z", data={"handle": handles["record"]})
    assert prune_artifacts(data, root, retention=KEEP, now=now) == 1
    for name, handle in handles.items():
        assert artifacts._artifact_path(handle).exists() is (name != "orphan")
        assert (artifacts._scope_dir("session") / handle).exists() is (name != "orphan")
    assert artifacts.read(handles["segment"])["handle"] == handles["segment"]


def test_retention_skips_database_when_no_artifact_has_expired(tmp_path):
    """没有过期 artifact 时清理不查询数据库"""
    root = tmp_path / "harness-artifacts"
    artifacts = HarnessArtifactStore(root)
    artifacts.put(scope="session", value="recent")
    assert prune_artifacts(object(), root, retention=KEEP) == 0


def test_artifact_reuse_renews_retention(tmp_path):
    """重复写入相同内容的 artifact 会刷新它的保留期"""
    store = HarnessArtifactStore(tmp_path)
    handle = store.put(scope="session", value="same output").handle
    old = time.time() - KEEP - 10
    os.utime(store._artifact_path(handle), (old, old))
    assert store.put(scope="session", value="same output").handle == handle
    assert store.prune(before=time.time() - KEEP, referenced=set()) == 0


def test_observations_are_pruned_in_bounded_batches():
    """过期的 Harness 策略观测按限定数量分批删除"""
    store = HarnessEffectivenessStore()
    try:
        for execution_id in ("old-a", "old-b", "current"):
            store.record_turn(decision=_decision(execution_id), details={"status": "completed"}, result=None)
        store.flush()
        with connect(store.database) as connection:
            connection.execute("UPDATE harness_strategy_observations SET recorded_at=to_timestamp(1) WHERE execution_id LIKE 'old-%%'")
        assert store.prune(before=time.time() - KEEP, limit=1) == 1
        assert store.prune(before=time.time() - KEEP, limit=1) == 1
        assert store.prune(before=time.time() - KEEP, limit=1) == 0
        with connect(store.database) as connection:
            assert connection.execute("SELECT execution_id FROM harness_strategy_observations").fetchone()[0] == "current"
    finally:
        store.close()


def test_retention_period_covers_every_harness_evaluation_window():
    """harness_retention_days 的下限覆盖所有 Harness 评估所需的时间窗口"""
    from management_portal.execution.harness_evolution_evaluation import EvolutionCriteria

    criteria = EvolutionCriteria()
    minimum, _maximum, default = RETENTION_DAYS["harness_retention_days"]
    assert minimum >= max(criteria.maximum_challenger_days, criteria.offline_days, criteria.traffic_days)
    assert default >= minimum


def test_cleanup_follows_the_administrator_retention_period(tmp_path, monkeypatch):
    """Harness 清理按管理员设置的保留天数删除观测、调用记录和无引用 artifact

    Evidence, recorded calls and unreferenced artifacts 35 days old outlive 40 days, not 30."""
    aged = time.time() - 35 * 86400
    observations = HarnessEffectivenessStore()
    monkeypatch.setattr(HarnessEffectivenessStore, "shared", classmethod(lambda cls: observations))
    monkeypatch.setattr(config, "execution_state_root", lambda: tmp_path)
    artifacts = HarnessArtifactStore(tmp_path / "harness-artifacts")
    handle = artifacts.put(scope="session", value="old output").handle
    os.utime(artifacts._artifact_path(handle), (aged, aged))
    calls = HarnessEvolutionStore()
    calls.write_turn_calls([TurnCalls(
        execution_id="aged-turn", agent_id="resident-test", provider="claude",
        cell="claude/repository_work", arm="champion", version=1, recorded_at=aged,
    )], keep_since=0)
    settings = ExecutionSettings()
    try:
        observations.record_turn(decision=_decision("aged-observation"), details={"status": "completed"}, result=None)
        observations.flush()
        with connect(observations.database) as connection:
            connection.execute("UPDATE harness_strategy_observations SET recorded_at=to_timestamp(%s)", (aged,))

        def kept():
            with connect(observations.database) as connection:
                observed = connection.execute("SELECT count(*) FROM harness_strategy_observations").fetchone()[0]
            return (observed, len(load_turn_calls(since=0, store=calls)), artifacts._artifact_path(handle).exists())

        settings.update_retention({"harness_retention_days": 40})
        harness_retention.maintain(AgentDataStore())
        assert kept() == (1, 1, True)
        settings.update_retention({"harness_retention_days": 30})
        harness_retention.maintain(AgentDataStore())
        assert kept() == (0, 0, False)
    finally:
        settings.update_retention({"harness_retention_days": RETENTION_DAYS["harness_retention_days"][2]})
        observations.close()
