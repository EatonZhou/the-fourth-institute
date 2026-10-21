from __future__ import annotations

import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from management_portal import resident_sidebar_backend
from management_portal.http_agent_workbench import AgentWorkbenchHttp
from management_portal.resources.errors import ResourceServiceUnavailable


@pytest.fixture(autouse=True)
def isolated_recommendation_binding(monkeypatch):
    def missing(_actor):
        raise KeyError("capability not configured")
    monkeypatch.setattr(resident_sidebar_backend, "recommendation_chat_binding", missing)


def test_personal_ama_is_a_portal_presentation_association_contracts(monkeypatch, subtests, isolated_portal_case):
    """侧栏快捷入口显示个人 AMA 关联的 Resident，侧栏快照合并 Lineage 与 Resident 信息"""
    with subtests.test(contract='test_personal_ama_is_a_portal_presentation_association'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            resident = SimpleNamespace(get_resident=lambda resident_id, _actor: {'resident_id': resident_id, 'lineage_id': 'lineage_0123456789ab', 'title': 'Ordinary Resident'}, get_resident_state=lambda resident_id, _actor: {'resident_id': resident_id, 'state': {'work_state': 'idle'}})
            monkeypatch.setattr(resident_sidebar_backend, 'get_society_service', lambda: SimpleNamespace(resident=resident, lineage=SimpleNamespace(list_principal_hierarchy=lambda _: {'projects': [], 'user_residents': []}, list_lineages=lambda _actor: {'lineages': []})))
            monkeypatch.setattr(resident_sidebar_backend, 'get_portal_ui_state_store', lambda: SimpleNamespace(get=lambda *_args: {'value': {'resident_id': 'resident_0123456789ab'}}))
            payload = resident_sidebar_backend.shortcuts('sample_admin')
            assert payload['residents'] == [{'resident_id': 'resident_0123456789ab', 'lineage_id': 'lineage_0123456789ab', 'title': 'Ordinary Resident', 'resident_state': {'work_state': 'idle'}, 'is_lineage_leader': True}]
    with subtests.test(contract='test_sidebar_projection_joins_neutral_lineage_and_resident_facts'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            resident_id = 'resident_0123456789ab'
            lineage_id = 'lineage_0123456789ab'
            service = SimpleNamespace(resident=SimpleNamespace(), lineage=SimpleNamespace(list_principal_hierarchy=lambda _: {'projects': [], 'user_residents': []}, list_lineages=lambda _actor: {'lineages': [{'lineage_id': lineage_id, 'title': 'Lineage', 'allows': {'UpdateLineage': True}, 'leader_resident_id': resident_id, 'lineage_node_ids': [resident_id]}]}))
            monkeypatch.setattr(resident_sidebar_backend, 'get_society_service', lambda: service)
            monkeypatch.setattr(resident_sidebar_backend, 'get_portal_ui_state_store', lambda: SimpleNamespace(get=lambda *_args: None))
            payload = resident_sidebar_backend.snapshot('sample_admin')
            assert payload['lineages'][0]['created_by'] == ''
            assert payload['lineages'][0]['allows'] == {'UpdateLineage': True}
            assert payload['lineages'][0]['resident_count'] == 1
            resident = payload['residents'][0]
            assert 'lineage_owner_ids' not in resident and 'owner_ids' not in resident['lineage']
            assert resident['is_lineage_leader'] is True
            assert resident['lineage']['title'] == 'Lineage'
            assert 'lineage_node_ids' not in resident
            assert 'lineage_node_ids' not in resident['lineage']


def test_creators_and_management_credentials_are_independent():
    """侧栏中 Lineage 和 Resident 的创建者分别显示，互不推导；Lineage 的按钮权限只来自它自己的 allows"""
    lineage = resident_sidebar_backend._lineage_sidebar_items({'lineages': [{
        'lineage_id': 'lineage_112233445566', 'created_by': 'lineage-creator',
        'allows': {'UpdateLineage': False},
    }]})[0]
    assert lineage['created_by'] == 'lineage-creator'
    assert lineage['allows'] == {'UpdateLineage': False} and 'can_manage' not in lineage
    resident = resident_sidebar_backend._resident_sidebar_item({
        'resident_id': 'resident_112233445566', 'created_by': 'resident-creator',
    }, lineage)
    assert resident['created_by'] == 'resident-creator'
    assert resident['lineage']['created_by'] == 'lineage-creator'
    assert 'can_manage' not in resident


def test_recommendation_sidebar_uses_the_capability_resident_and_native_menu_identity(monkeypatch):
    """验证推荐 Resident 的真实身份、聊天卡片及请求恢复语义。

    :module: lineage.team
    :importance: P1
    :aspects: behavior, architecture-conformance
    :design: decision_3e07c6d35cac, decision_8fe87d812a4c
    """
    rid, lid = "resident_112233445566", "lineage_112233445566"
    identity = {"resident_id": rid, "lineage_id": lid, "title": "Discovery", "can_edit": True}
    monkeypatch.setattr(resident_sidebar_backend, "recommendation_chat_binding", lambda actor: {"resident_id": rid, "lineage_id": lid})
    service = SimpleNamespace(
        resident=SimpleNamespace(get_resident=lambda *_: identity),
        lineage=SimpleNamespace(list_lineages=lambda _: {"lineages": []}, list_principal_hierarchy=lambda _: {"projects": []}),
    )
    monkeypatch.setattr(resident_sidebar_backend, "get_society_service", lambda: service)
    monkeypatch.setattr(resident_sidebar_backend, "get_portal_ui_state_store", lambda: SimpleNamespace(get=lambda *_: None))
    result = resident_sidebar_backend.shortcuts("CASE-A")
    assert result["recommendation_resident"] == identity
    assert result["residents"] == [identity]
    assert result["recommendation_status"] == "available"


@pytest.mark.parametrize("error,status", [(PermissionError("denied"), "denied"), (KeyError("missing binding"), "unavailable"), (ValueError("invalid binding"), "unavailable")])
def test_recommendation_binding_failure_is_not_automatically_a_permission_lock(monkeypatch, error, status):
    """推荐 Resident 绑定读取失败时，只有权限错误显示为 denied，其他错误显示为 unavailable"""
    def unavailable(_actor):
        raise error
    monkeypatch.setattr(resident_sidebar_backend, "recommendation_chat_binding", unavailable)
    service = SimpleNamespace(resident=SimpleNamespace(),
        lineage=SimpleNamespace(list_lineages=lambda _: {"lineages": []}, list_principal_hierarchy=lambda _: {"projects": []}))
    monkeypatch.setattr(resident_sidebar_backend, "get_society_service", lambda: service)
    monkeypatch.setattr(resident_sidebar_backend, "get_portal_ui_state_store", lambda: SimpleNamespace(get=lambda *_: None))
    result = resident_sidebar_backend.shortcuts("CASE-A")
    assert result["recommendation_resident"] is None
    assert result["recommendation_status"] == status


def test_cached_sidebar_snapshot_coalesces_concurrent_cold_reads(monkeypatch):
    """缓存为空时并发读取侧栏快照只构建一次"""
    resident_sidebar_backend.invalidate_snapshot_cache()
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def build(actor):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2)
        return {"actor": actor, "calls": calls}

    monkeypatch.setattr(resident_sidebar_backend, "snapshot", build)
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [
            executor.submit(resident_sidebar_backend.cached_snapshot, "CASE-A")
            for _ in range(12)
        ]
        assert started.wait(timeout=1)
        time.sleep(0.02)
        release.set()
        results = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert results == [{"actor": "CASE-A", "calls": 1}] * 12
    resident_sidebar_backend.invalidate_snapshot_cache()


def test_sidebar_snapshot_reads_its_two_directories_side_by_side(monkeypatch):
    """侧栏快照并行读取 Lineage 列表和主体层级两个目录"""
    together = threading.Barrier(2, timeout=5)

    def read(value):
        def call(*_args, **_kwargs):
            together.wait()
            return value
        return call

    service = SimpleNamespace(
        resident=SimpleNamespace(),
        lineage=SimpleNamespace(
            list_lineages=read({"lineages": [], "schema_version": 3}),
            list_principal_hierarchy=read({"projects": ["AI员工"]}),
        ),
    )
    monkeypatch.setattr(resident_sidebar_backend, "get_society_service", lambda: service)
    monkeypatch.setattr(
        resident_sidebar_backend,
        "get_portal_ui_state_store",
        lambda: SimpleNamespace(get=lambda *_args: None),
    )

    result = resident_sidebar_backend.snapshot("CASE-A")

    assert result["schema_version"] == 3
    assert result["principal_hierarchy"] == {"projects": ["AI员工"]}
    assert result["lineages"] == [] and result["residents"] == []


@pytest.mark.parametrize("failure,code", [
    (TimeoutError("timed out"), "lineage.busy"),
    (ResourceServiceUnavailable("restarting"), "lineage.unavailable"),
])
@pytest.mark.parametrize("method", ["get_residents", "get_resident_shortcuts"])
def test_sidebar_reads_during_a_restart_answer_with_a_code_the_page_retries(failure, code, method):
    """10-12 a publication restart made the sidebar read time out (504) and the
    Lineage page showed "加载失败：HTTP 504" in the Chat; the page retries
    quietly on SocietyService codes."""

    answered = []

    def sidebar(_actor):
        raise failure

    arguments = {name: None for name in inspect.signature(AgentWorkbenchHttp).parameters}
    arguments.update(
        respond=lambda request, status, payload: answered.append(("respond", status, payload)),
        error=lambda request, exc: answered.append(("error", exc)),
        session_actor=lambda request: "CASE-A",
        lineage_unavailable=lambda request, exc: answered.append(("unavailable", exc.http_status, exc.code)),
        resident_sidebar_snapshot=sidebar,
        resident_sidebar_backend=lambda: SimpleNamespace(shortcuts=sidebar),
    )
    getattr(AgentWorkbenchHttp(**arguments), method)(object(), None, "CASE-A")
    assert answered == [("unavailable", 503, code)]
