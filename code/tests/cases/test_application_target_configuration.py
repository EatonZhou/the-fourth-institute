"""The TAU migration fence is durable, versioned and independent of Test.

:module: operations.test-framework
:design: decision_6591e7793116, decision_c953eab654cd
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace
import secrets

import pytest

from management_common.database_time import instant
from management_common.portal_database import portal_database
from management_portal import application_targets
from management_portal.society_service import application_target_configuration as config

A = "lineage_111111111111"
B = "lineage_222222222222"


@pytest.fixture
def configuration(monkeypatch):
    database = portal_database(schema="tau_configuration_" + secrets.token_hex(6))
    monkeypatch.setattr(config, "portal_database", lambda **_kwargs: database)
    monkeypatch.setattr(application_targets, "portal_database", lambda **_kwargs: database)
    with database.transaction() as connection:
        connection.execute("CREATE TABLE lineages (lineage_id TEXT PRIMARY KEY, deleted_at TEXT)")
        connection.execute("CREATE TABLE residents (resident_id TEXT PRIMARY KEY, deleted_at TEXT)")
        connection.execute("INSERT INTO lineages VALUES (%s,NULL),(%s,NULL)", (A, B))
        connection.execute("CREATE TABLE lineage_nodes (node_id TEXT PRIMARY KEY,lineage_id TEXT,node_type TEXT,deleted_at TEXT)")
        connection.execute("INSERT INTO residents VALUES ('resident_111111111111',NULL),('resident_222222222222',NULL)")
        connection.execute("INSERT INTO lineage_nodes VALUES ('resident_111111111111',%s,'resident',NULL),('resident_222222222222',%s,'resident',NULL)", (A, B))
        application_targets.create_schema(connection)
    return database


def test_read_cas_freeze_switch_resume_without_workbench_mutation(configuration):
    """TAU 目标配置按修订号更新，暂停接收后才能切换目标，恢复需单独操作且不改动工作台

    :design-point:"""
    assert config.get("tau")["admission_state"] == "unconfigured"
    with pytest.raises(config.ConfigurationConflict):
        with config.admission_scope("tau"):
            pytest.fail("unconfigured admission")
    first = config.update("tau", {"target_id": A}, "admin", expected_revision=0)
    assert first["revision"] == 1 and first["admission_state"] == "open"
    with pytest.raises(config.ConfigurationConflict, match="revision changed"):
        config.update("tau", {"admission_paused": True}, "admin", expected_revision=0)
    with pytest.raises(config.ConfigurationConflict, match="Pause admission"):
        config.update("tau", {"target_id": B, "admission_paused": True}, "admin", expected_revision=1)
    paused = config.update("tau", {"admission_paused": True, "reason": "migration"}, "admin", expected_revision=1)
    # A new reader and the ordinary TAU target reader see the same durable row.
    assert config.get("tau") == paused
    assert application_targets.lineage("tau") == A
    with pytest.raises(config.AdmissionPaused) as rejected:
        with config.admission_scope("tau"):
            pytest.fail("paused admission")
    assert rejected.value.commit_state == "not_committed"
    assert rejected.value.details["revision"] == 2
    with pytest.raises(config.ConfigurationConflict, match="resume separately"):
        config.update("tau", {"target_id": B, "admission_paused": False}, "admin", expected_revision=2)
    switched = config.update("tau", {"target_id": B}, "admin", expected_revision=2)
    assert switched["revision"] == 3 and switched["admission_paused"]
    assert application_targets.lineage("tau") == B
    reopened = config.update("tau", {"admission_paused": False, "reason": "verified"}, "admin", expected_revision=3)
    assert reopened["admission_state"] == "open"
    assert reopened["updated_by"] == "admin" and reopened["updated_at"]
    with config.admission_scope("tau"):
        pass
    # The fixture has no Workbench table: TAU does not need or mutate it.


def test_missing_deleted_target_and_repair(configuration):
    """TAU 目标不存在时拒绝配置，目标被删除后停止接收，暂停后切换到有效目标即可修复

    :design-point:"""
    with pytest.raises(KeyError):
        config.update("tau", {"target_id": "lineage_999999999999"}, "admin", expected_revision=0)
    config.update("tau", {"target_id": A}, "admin", expected_revision=0)
    with configuration.transaction() as connection:
        connection.execute("UPDATE lineages SET deleted_at='deleted' WHERE lineage_id=%s", (A,))
    assert not config.get("tau")["target_available"]
    with pytest.raises(config.ConfigurationConflict):
        with config.admission_scope("tau"):
            pytest.fail("deleted target")
    config.update("tau", {"admission_paused": True}, "admin", expected_revision=1)
    with pytest.raises(KeyError):
        config.update("tau", {"admission_paused": False}, "admin", expected_revision=2)
    config.update("tau", {"target_id": B}, "admin", expected_revision=2)
    assert config.get("tau")["admission_paused"]


def test_configuration_stores_update_time_as_an_absolute_instant(configuration):
    """应用目标配置以 TIMESTAMPTZ 保存更新时间

    应用目标表的 updated_at 是可为空、无默认值的 TIMESTAMPTZ；从未更新的行在读取结果中返回空字符串，维护脚本更新时写入当前时刻，并以 Z 结尾的 UTC ISO 时间返回。

    :module: lineage.service
    :design: decision_86aed2f0e8c7
    :design-point:
    """
    with configuration.read() as connection:
        columns = {row["table_name"]: (row["data_type"], row["is_nullable"], row["column_default"]) for row in connection.execute(
            "SELECT table_name,data_type,is_nullable,column_default FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND column_name='updated_at'"
        ) if row["table_name"] in application_targets.COLUMNS}
    assert columns == {table: ("timestamp with time zone", "YES", None) for table in application_targets.COLUMNS}
    # A row that was never updated has no time; the interface keeps its empty string.
    with configuration.transaction() as connection:
        connection.execute("INSERT INTO application_targets (application,lineage_id) VALUES ('resource_chat:article',%s)", (A,))
    assert config.get("resource_chat:article")["updated_at"] == ""
    before = datetime.now(timezone.utc)
    updated = config.update("tau", {"target_id": A}, "admin", expected_revision=0)
    assert updated["updated_at"].endswith("Z")
    assert before <= instant(updated["updated_at"]) <= datetime.now(timezone.utc)
    assert config.get("tau")["updated_at"] == updated["updated_at"]


@pytest.mark.parametrize("changes,revision", [
    ({"admission_paused": "false"}, 0), ({"target_id": "bad"}, 0),
    ({"reason": "x" * 1001}, 0), ({"owner": "admin"}, 0), ({}, 0),
    ({"target_id": A}, True), ({"target_id": A}, -1),
])
def test_configuration_rejects_invalid_changes_before_commit(configuration, changes, revision):
    """非法的目标配置变更或修订号在提交前被拒绝，修订号保持为 0

    :design-point:"""
    with pytest.raises(ValueError):
        config.update("tau", changes, "admin", expected_revision=revision)
    assert config.get("tau")["revision"] == 0


def test_maintenance_script_shows_and_changes_a_target(configuration, capsys):
    """维护脚本读取应用目标，并按修订号暂停、切换和恢复 TAU，记录操作者

    :design-point:"""
    import json
    from scripts import application_target

    def run(*argv):
        assert application_target.main(list(argv)) == 0
        return json.loads(capsys.readouterr().out)

    assert run("show", "tau")["admission_state"] == "unconfigured"
    assert run("set", "tau", "--expected-revision", "0", "--target", A, "--actor", "sample_admin")["revision"] == 1
    assert run("set", "tau", "--expected-revision", "1", "--pause", "--reason", "move", "--actor", "sample_admin")["admission_paused"]
    assert run("set", "tau", "--expected-revision", "2", "--target", B, "--actor", "sample_admin")["target_id"] == B
    reopened = run("set", "tau", "--expected-revision", "3", "--resume", "--actor", "sample_admin")
    assert reopened["admission_state"] == "open" and reopened["updated_by"] == "sample_admin"
    with pytest.raises(config.ConfigurationConflict, match="revision changed"):
        application_target.main(["set", "tau", "--expected-revision", "3", "--pause", "--actor", "sample_admin"])
    with pytest.raises(SystemExit):
        application_target.main(["set", "tau", "--expected-revision", "4", "--actor", "sample_admin"])


def test_freeze_waits_for_durable_admission_then_rejects_new_work(configuration, tmp_path):
    """暂停 TAU 接收会等进行中的接收提交完成，之后拒绝新工作

    :design-point:"""
    from management_portal.test_framework.records import RunRecords

    config.update("tau", {"target_id": A}, "admin", expected_revision=0)
    records = RunRecords(tmp_path / "runs.json")
    started = Event()

    def freeze():
        started.set()
        return config.update("tau", {"admission_paused": True}, "admin", expected_revision=1)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with config.admission_scope("tau"):
            pending = pool.submit(freeze)
            assert started.wait(2)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.15)
            accepted = records.create({"selected": [], "execution": {"groups": []}}, "admin")
        assert pending.result(timeout=5)["admission_paused"]
    # New instances (including after restart) retain accepted work and the gate.
    assert RunRecords(tmp_path / "runs.json").get(accepted["run_id"])["run_id"] == accepted["run_id"]
    with pytest.raises(config.AdmissionPaused):
        with config.admission_scope("tau"):
            pytest.fail("new work after committed freeze")


def test_feature_applications_come_from_the_installed_features(configuration, monkeypatch):
    """功能的应用目标由功能声明：缺少该功能时它的应用名不再被接受，其行原样保留

    Notice Board 分类和 TAU 通过 ``application.targets`` 声明自己的应用和目标类型；核心应用不经过功能声明。

    :module: lineage.service
    :design: decision_6591e7793116, decision_15aab18c0ab3
    :design-point:
    """
    from management_portal import portal_features

    assert config.get("notice_classifier")["supports_admission_pause"] is False
    assert config.get("tau")["supports_admission_pause"] is True
    config.update("tau", {"target_id": A}, "admin", expected_revision=0)
    monkeypatch.setattr(portal_features, "contributions", lambda hook: ())
    for application in ("tau", "notice_classifier"):
        with pytest.raises(ValueError, match="Unknown application"):
            config.get(application)
    assert config.get("resident_summary")["revision"] == 0
    with configuration.read() as connection:
        assert connection.execute("SELECT lineage_id FROM application_targets WHERE application='tau'").fetchone()["lineage_id"] == A


OTHER_APPLICATIONS = [
    "resident_discovery", "resident_summary", "notice_classifier",
    *("semantic_search:" + value for value in application_targets.SEARCH_SURFACES),
    *("resource_chat:" + value for value in application_targets.CHAT_TYPES),
]


@pytest.mark.parametrize("application", OTHER_APPLICATIONS)
def test_other_applications_share_cas_and_reject_unsupported_admission_controls(configuration, application):
    """TAU 以外的应用目标同样按修订号更新并校验目标类型，拒绝暂停接收设置

    :module: lineage.service
    :design-point:
    """
    resident = not application.startswith("resource_chat:")
    first = "resident_111111111111" if resident else A
    second = "resident_222222222222" if resident else B
    assert config.get(application)["revision"] == 0
    with pytest.raises(ValueError, match="target type"):
        config.update(application, {"target_id": A if resident else "resident_111111111111"}, "admin", expected_revision=0)
    assert config.update(application, {"target_id": first}, "admin", expected_revision=0)["target_available"]
    switched = config.update(application, {"target_id": second, "reason": "switch"}, "admin", expected_revision=1)
    assert switched["target_id"] == second and switched["revision"] == 2
    assert not switched["supports_admission_pause"]
    for paused in (True, False):
        with pytest.raises(config.ConfigurationConflict, match="does not support"):
            config.update(application, {"admission_paused": paused}, "admin", expected_revision=2)
    assert config.get(application) == switched
    if resident:
        with configuration.transaction() as connection:
            connection.execute("UPDATE residents SET deleted_at='deleted' WHERE resident_id=%s", (second,))
        assert not config.get(application)["target_available"]
    assert config.get("tau")["revision"] == 0  # per-application rows


@pytest.mark.parametrize("application", ["unknown", "discussion_reply", "semantic_search:todo", "resource_chat:bad", "tau; DROP TABLE lineages"])
def test_unknown_application_has_no_storage_or_sql_effect(configuration, application):
    """未知应用名（包括 SQL 注入串）被拒绝，不触及存储

    :module: lineage.service
    :design-point:
    """
    with pytest.raises(ValueError, match="Unknown application"):
        config.get(application)
