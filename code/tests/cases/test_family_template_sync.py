"""Existing Families follow the running Family template (decision_44cf587c1721).

:module: lineage.team
:design: decision_44cf587c1721
"""

from __future__ import annotations

import pytest

from management_portal.society_service import config, lineage_domain, resident_intake_domain
from management_portal.society_service.coordinator import LineageCoordinator
from management_portal.society_service.templates import familize
from tests.cases.isa_test_support import ok
from tests.cases.test_isa_lineage_lifecycle import (  # noqa: F401 - lineage is a fixture
    future_creation_store,
    graph,
    lineage,
    resident,
)


@pytest.fixture
def charters(monkeypatch):
    monkeypatch.setattr(resident_intake_domain, "deliver_resident_message_content", lambda *a, **k: {})


def _relay(lineage_id: str, family_id: str) -> dict:
    view = graph(lineage_id)
    block = next(item for item in view["function_blocks"] if item["id"] == family_id)
    wire_id = next(item["id"] for item in block["elements"] if item.get("key") == "relay")
    return next(item for item in view["program"]["wires"] if item["id"] == wire_id)


def test_a_family_built_by_an_older_template_is_regenerated_once(lineage, charters):
    """旧模板生成的 Family 结构按当前模板重新生成一次，报告改了哪些对象的哪些字段，之后不再提交"""
    member = resident(lineage, "Member")
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [member], "title": "Team"})
    relay = _relay(lineage, family["family_id"])
    config_before = {key: value for key, value in relay["config"].items() if key != "parallel"}
    # The Router relay was an ordered wire before the template made it parallel.
    ok("UpdateWire", {"lineage_id": lineage, "wire_id": relay["id"], "changes": {"config": config_before}})
    assert not _relay(lineage, family["family_id"])["config"].get("parallel")

    synced = familize.sync_generated_structure(lineage)
    assert synced["status"] == "synced"
    assert synced["family_ids"] == [family["family_id"]]
    assert synced["changes"] == {"wires": {relay["id"]: ["config.parallel"]}}
    assert synced["actor"] == lineage_domain._lineage_runtime_actor(lineage_domain._read_lineage_state(lineage))
    assert _relay(lineage, family["family_id"])["config"]["parallel"] is True

    revision = lineage_domain._read_lineage_state(lineage)["revision"]
    assert familize.sync_generated_structure(lineage)["status"] == "unchanged"
    assert lineage_domain._read_lineage_state(lineage)["revision"] == revision


def test_a_plain_function_block_beside_a_family_does_not_make_the_sync_commit(lineage, charters):
    """Family 旁边没有模板的普通 Function Block 不让同步每次都提交

    普通 Function Block 的 template_id 为空：规范化写成 null，存储读回时不带这个字段。两者是同一个值，
    同步比较结构时不算改动，结果为 unchanged，Lineage 版本不变。
    """
    member, other = resident(lineage, "Member"), resident(lineage, "Other")
    ok("Familize", {"lineage_id": lineage, "resident_ids": [member], "title": "Team"})
    ok("Functionalize", {"lineage_id": lineage, "element_ids": [other], "title": "Plain"})
    revision = lineage_domain._read_lineage_state(lineage)["revision"]
    assert familize.sync_generated_structure(lineage)["status"] == "unchanged"
    assert lineage_domain._read_lineage_state(lineage)["revision"] == revision


def test_a_lineage_without_a_family_is_left_alone(lineage):
    """没有 Family 的 Lineage 不做同步"""
    resident(lineage, "Solo")
    revision = lineage_domain._read_lineage_state(lineage)["revision"]
    assert familize.sync_generated_structure(lineage)["status"] == "none"
    assert lineage_domain._read_lineage_state(lineage)["revision"] == revision


def test_a_lineage_deleted_during_the_pass_is_not_a_failure(lineage, charters, monkeypatch):
    """同步途中被删除的 Lineage 不算同步失败

    同步先读出全部 Lineage 再逐个处理；其间被删除的 Lineage 直接跳过，报告里不记失败。
    """
    member = resident(lineage, "Member")
    ok("Familize", {"lineage_id": lineage, "resident_ids": [member], "title": "Team"})
    store = lineage_domain._lineage_catalog_store()
    listed = store.listing_records()
    assert lineage in {record["lineage_id"] for record in listed}
    ok("DeleteLineage", {"lineage_id": lineage})
    monkeypatch.setattr(store, "listing_records", lambda: listed)
    assert lineage not in familize.sync_all_generated_structure()["failed"]


def test_only_the_release_instance_syncs_template_structure(monkeypatch):
    """只有 Release 实例启动时同步模板结构；可以用环境变量改为其他实例"""
    monkeypatch.delenv("PORTAL_TEMPLATE_SYNC_INSTANCE", raising=False)
    assert config.syncs_template_structure("release")
    assert not config.syncs_template_structure("dev")
    monkeypatch.setenv("PORTAL_TEMPLATE_SYNC_INSTANCE", "dev")
    assert config.syncs_template_structure("dev")
    assert not config.syncs_template_structure("release")


def test_the_coordinator_syncs_once_when_it_starts():
    """协调器启动时同步一次模板结构并记录结果"""
    calls = []
    coordinator = LineageCoordinator(
        resume_residents=lambda: {}, resume_resident=lambda _: {}, reconcile_all=lambda: {},
        reconcile_lineage=lambda _: {},
        sync_templates=lambda: calls.append(1) or {"synced": [], "unchanged": 2, "failed": {}},
    )
    coordinator._sync_template_structure()
    assert calls == [1]
    assert coordinator.snapshot()["last_template_sync"]["unchanged"] == 2
